"""
Dot article summariser.

Scheduled run: opens dot.news/posts, takes today's story, summarises it with
Gemini and saves it to summaries/.

Manual run from the hub (REQUEST_ID set): summarises a pasted link
(ARTICLE_URL) or the story from a chosen date (ARTICLE_DATE) and writes it to
summaries/pending/<REQUEST_ID>.json, so the hub can show it before saving.
"""
import os
import re
import sys
import json
import time
import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

POSTS_URL = "https://www.dot.news/posts"
ROOT = Path(__file__).parent
SUMMARY_DIR = ROOT / "summaries"
PENDING_DIR = SUMMARY_DIR / "pending"
DEBUG_DIR = ROOT / "debug"
IST = ZoneInfo("Asia/Kolkata")
NOW = datetime.datetime.now(IST)
TODAY = NOW.strftime("%Y-%m-%d")

ARTICLE_URL = (os.environ.get("ARTICLE_URL") or "").strip()
ARTICLE_DATE = (os.environ.get("ARTICLE_DATE") or "").strip()
REQUEST_ID = re.sub(r"[^\w-]", "", os.environ.get("REQUEST_ID") or "")


def fail(message):
    """Stop, and tell the hub why if it is waiting for a result."""
    print(message)
    if REQUEST_ID:
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        (PENDING_DIR / f"{REQUEST_ID}.json").write_text(json.dumps({"error": message}), encoding="utf-8")
        sys.exit(0)          # let the workflow commit the error file
    sys.exit(1)


# ---------- 1. Find and read the article ----------

def open_page(page, url, wait_for=None):
    """Open a page without waiting for every background request to stop.
    dot.news keeps some connections open, so 'networkidle' can time out."""
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    if wait_for:
        try:
            page.wait_for_selector(wait_for, timeout=30000)
        except Exception:
            pass
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass                                   # fine: content is usually there by now
    page.wait_for_timeout(2500)


def load_text(page, url):
    open_page(page, url, wait_for="p, article, h1")
    for _ in range(6):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(700)
    return page.inner_text("body")


def post_links(page):
    """All story links on the posts page, newest first, as (href, text)."""
    links = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => ({href: e.href, text: (e.innerText || '').trim()}))",
    )
    return [(l["href"], l["text"]) for l in links
            if "dot.news" in l["href"] and re.search(r"/post/[0-9a-f]{24}", l["href"])]


def date_label(date_str):
    """How dot.news labels a day on its posts page: Today, Yesterday or '22 Sep'."""
    d = datetime.date.fromisoformat(date_str)
    today = NOW.date()
    if d == today:
        return "Today"
    if d == today - datetime.timedelta(days=1):
        return "Yesterday"
    return f"{d.day} {d.strftime('%b')}"


def find_by_date(page, date_str):
    open_page(page, POSTS_URL, wait_for='a[href*="/post/"]')
    label = date_label(date_str)
    for _ in range(25):                      # scroll to load older stories
        for href, text in post_links(page):
            first = text.split("\n")[0].strip()
            if first.lower() == label.lower():
                return href
        page.mouse.wheel(0, 6000)
        page.wait_for_timeout(1200)
    return None


def get_article():
    DEBUG_DIR.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1600})

        if ARTICLE_URL:
            url = ARTICLE_URL
        elif ARTICLE_DATE:
            url = find_by_date(page, ARTICLE_DATE)
            if not url:
                browser.close()
                fail(f"No Dot story found for {ARTICLE_DATE}. Dot may not have published that day, or it is too far back.")
        else:
            links = []
            for attempt in range(3):                 # the page sometimes renders slowly
                open_page(page, POSTS_URL, wait_for='a[href*="/post/"]')
                links = post_links(page)
                if links:
                    break
                page.wait_for_timeout(5000)
            if not links:
                browser.close()
                fail("Couldn't find any stories on dot.news/posts.")
            url = links[0][0]

        text = load_text(page, url)
        page.screenshot(path=str(DEBUG_DIR / "page.png"), full_page=True)
        (DEBUG_DIR / "page.txt").write_text(f"URL: {url}\n\n{text}", encoding="utf-8")
        browser.close()
    return url, text


# ---------- 2. Summarise with Gemini ----------

def summarise(text, url):
    key = os.environ["GEMINI_API_KEY"]
    instructions = (ROOT / "prompt.txt").read_text(encoding="utf-8")
    body = {"contents": [{"parts": [{"text": f"{instructions}\n\nSOURCE URL: {url}\n\nPAGE TEXT:\n{text[:60000]}"}]}]}
    # Each model has its own free daily allowance, so try several before giving up.
    models = [m for m in [os.environ.get("GEMINI_MODEL"), "gemini-3.5-flash", "gemini-3.8-flash", "gemini-flash-latest",
                          "gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"] if m]
    for model in models:
        for attempt in range(2):
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=body, timeout=180,
            )
            if r.status_code == 200:
                parts = r.json()["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts).strip()
            print(f"{model} attempt {attempt + 1}: HTTP {r.status_code} {r.text[:300]}")
            if r.status_code in (500, 503):          # temporarily overloaded: wait a little, retry once
                time.sleep(20)
                continue
            break                                     # 429 (allowance used), 404 (retired), etc.: next model
    fail("Gemini couldn't summarise the article. Try again in a few minutes.")


# ---------- 3. Save ----------

def load_index():
    f = SUMMARY_DIR / "index.json"
    return json.loads(f.read_text()) if f.exists() else {}


def save_scheduled(title, summary, url):
    SUMMARY_DIR.mkdir(exist_ok=True)
    index = load_index()
    if title in index:
        print(f"Already summarised: {title}. Skipping.")
        return
    entry = f"# {title}\n*{TODAY}* · [Original article]({url})\n\n{summary}\n"
    (SUMMARY_DIR / f"{TODAY}.md").write_text(entry, encoding="utf-8")
    all_file = SUMMARY_DIR / "ALL_SUMMARIES.md"
    old = all_file.read_text(encoding="utf-8") if all_file.exists() else ""
    all_file.write_text(entry + "\n---\n\n" + old, encoding="utf-8")
    index[title] = TODAY
    (SUMMARY_DIR / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False))
    print(f"Saved: {title}")


def save_pending(title, summary, url):
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    data = {"title": title, "summary": summary, "url": url, "date": ARTICLE_DATE or TODAY,
            "already_saved": title in load_index()}
    (PENDING_DIR / f"{REQUEST_ID}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"Ready for review: {title}")


def main():
    url, text = get_article()
    print(f"Read {len(text)} characters from {url}")
    result = summarise(text, url)
    if result.startswith("NO_ARTICLE"):
        fail("Couldn't find an article on that page. Check the link.")
    first, _, rest = result.partition("\n")
    title = re.sub(r"^\W*(line\s*1\s*:\s*)?(title\s*:\s*)?", "", first.strip(), flags=re.I).strip(" *#") or "Untitled"
    summary = rest.strip()
    if REQUEST_ID:
        save_pending(title, summary, url)
    else:
        save_scheduled(title, summary, url)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:                     # always tell the hub what went wrong
        fail(f"Something went wrong while reading the article ({type(e).__name__}). Try again in a few minutes.")
