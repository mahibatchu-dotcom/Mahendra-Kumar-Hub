"""
Case competition details finder (runs on GitHub, started by the hub website).

For each competition the hub sends (name, organiser, links found in the email):
  1. search the web for the competition's page (Unstop, D2C, organiser site...)
  2. open the best pages in a real browser (works for JavaScript sites)
  3. ask Gemini to pick out: deadline, team size, organiser, link, open/closed
Results go to casecomps/pending/<REQUEST_ID>.json for the hub to pick up.
Only public web pages are read. No email content is sent here.
"""
import os
import re
import json
import time
import datetime
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

REQ = re.sub(r"[^\w-]", "", os.environ.get("REQUEST_ID") or "manual")
CASES = json.loads(os.environ.get("CASES") or "[]")[:15]
KEY = os.environ["GEMINI_API_KEY"]
OUT = Path(__file__).parent / "casecomps" / "pending" / f"{REQ}.json"
TODAY = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")
BAD = re.compile(r"(webex|zoom\.us|teams\.microsoft|meet\.google|unsubscribe|linkedin\.com|instagram\.com|facebook\.com|"
                 r"twitter\.com|x\.com/|youtube\.com|youtu\.be|whatsapp|duckduckgo\.com|bing\.com|google\.com/search)", re.I)
GOOD_SITES = ("unstop.com", "dare2compete", "d2c.", "devfolio", "hackerearth", "forms.gle", "docs.google.com/forms")
MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash",
          "gemini-3.8-flash", "gemini-flash-latest"]


def open_text(page, url, limit=9000):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(2500)
        return page.inner_text("body")[:limit]
    except Exception as e:
        print("  could not open", url, type(e).__name__)
        return ""


def search(page, query):
    """Return (result urls, the results page text) from DuckDuckGo, falling back to Bing."""
    urls, text = [], ""
    try:
        page.goto("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query), wait_until="domcontentloaded", timeout=40000)
        for a in page.query_selector_all("a.result__a"):
            href = a.get_attribute("href") or ""
            q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg")
            urls.append(q[0] if q else href)
        text = page.inner_text("body")[:4000]
    except Exception as e:
        print("  duckduckgo failed", type(e).__name__)
    if not urls:
        try:
            page.goto("https://www.bing.com/search?q=" + urllib.parse.quote(query), wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(1500)
            urls = [a.get_attribute("href") or "" for a in page.query_selector_all("li.b_algo h2 a")]
            text = page.inner_text("body")[:4000]
        except Exception as e:
            print("  bing failed", type(e).__name__)
    return [u for u in urls if u.startswith("http") and not BAD.search(u)], text


def rank(urls, organiser):
    org_words = [w for w in re.findall(r"[a-z]{4,}", (organiser or "").lower()) if w not in ("limited", "india", "industries")]
    def score(u):
        s = 0
        if any(g in u for g in GOOD_SITES): s += 3
        if any(w in u.lower() for w in org_words): s += 2
        if re.search(r"case|competition|challenge|contest", u, re.I): s += 1
        return -s
    seen, out = set(), []
    for u in sorted(urls, key=score):
        if u not in seen:
            seen.add(u); out.append(u)
    return out


def ask_gemini(prompt):
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}}
    for model in MODELS:
        for _ in range(2):
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                              headers={"x-goog-api-key": KEY, "Content-Type": "application/json"}, json=body, timeout=120)
            if r.status_code == 200:
                parts = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
                text = re.sub(r"^```(json)?|```$", "", text.strip()).strip()
                return json.loads(text[text.find("{"): text.rfind("}") + 1])
            if r.status_code in (500, 503):
                time.sleep(15); continue
            break
    raise RuntimeError("Gemini unavailable")


def main():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1600},
                                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36")
        for c in CASES:
            name, org = c.get("name", ""), c.get("organiser", "")
            print("Looking up:", name, "|", org)
            try:
                links = [u for u in c.get("links", []) if not BAD.search(u)]
                found, serp = search(page, f"{name} {org} case competition registration")
                found2, serp2 = search(page, f"{name} {org} unstop")
                urls = rank(links + found + found2, org)[:3]
                pages = []
                for u in urls:
                    t = open_text(page, u)
                    if t.strip():
                        pages.append(f"=== PAGE: {u} ===\n{t}")
                prompt = f"""Today is {TODAY}. Find the details of this student case competition, using ONLY the text below.
Competition: "{name}" organised by "{org or 'unknown'}".
Return ONLY JSON: {{"name": "official name or null", "organiser": "organiser or null", "registration_deadline": "YYYY-MM-DD or null",
"deadline_text": "deadline as written or null", "team_size": "e.g. '2-4' or '3', or null", "link": "best registration page URL or null",
"status": "open / closed / unknown"}}.
Mark status "closed" if a page says registrations are closed or the deadline has passed. Only use details that clearly belong to THIS competition and its current edition.

=== SEARCH RESULTS ===
{serp[:3000]}
{serp2[:2000]}

{chr(10).join(pages)[:24000]}"""
                r = ask_gemini(prompt)
                r["id"] = c["id"]
                results.append(r)
                print("  ->", {k: r.get(k) for k in ("registration_deadline", "team_size", "status")})
            except Exception as e:
                print("  failed:", type(e).__name__, e)
                results.append({"id": c.get("id"), "error": str(e)[:200]})
        browser.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"results": results}, ensure_ascii=False), encoding="utf-8")
    print("Saved", OUT)


if __name__ == "__main__":
    main()
