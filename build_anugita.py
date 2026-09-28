"""
One-time builder: downloads K. T. Telang's public-domain English translation of
the Anugita (Sacred Books of the East, vol. 8, 1882) from sacred-texts.com and
saves it as data/anugita.json, split into chapters and readable passages.

sacred-texts.com now embeds each chapter's text as an escaped HTML string
(contentHtml:"...") inside the page, so we read that string directly.
"""
import re
import json
import html
import time
from pathlib import Path

import requests

BASE = "https://sacred-texts.com/hin/sbe08/sbe08{:02d}.htm"
FIRST_PAGE, CHAPTERS = 28, 36          # Anugita chapters I-XXXVI are pages 28-63
OUT = Path(__file__).parent / "data" / "anugita.json"
TARGET = 700                            # aim for passages of roughly this many characters
MAX = 1300


def chapter_html(page):
    m = re.search(r'contentHtml:"((?:[^"\\]|\\.)*)"', page)
    if not m:
        raise RuntimeError("Couldn't find the chapter text on the page (site layout may have changed).")
    return json.loads('"' + m.group(1) + '"')


def clean(fragment):
    fragment = re.sub(r'<a[^>]*href="[^"]*#fn_\d+"[^>]*>.*?</a>', "", fragment, flags=re.S)   # footnote numbers
    fragment = re.sub(r"<a>\s*</a>", "", fragment)
    text = re.sub(r"<[^>]+>", "", fragment)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(\S) \d{1,4}(?=[\s,;:.!?'\"’)]|$)", r"\1", text)   # leftover footnote numbers
    return re.sub(r"\s+([,.;:!?])", r"\1", text)


def paragraphs(body):
    body = re.split(r"<hr", body, maxsplit=1)[0]                 # footnotes come after the rule
    out = []
    for raw in re.findall(r"<p[^>]*>(.*?)</p>", body, flags=re.S):
        if re.fullmatch(r"\s*<a>\s*p\.\s*\d+\s*</a>\s*", raw):   # page-number markers
            continue
        t = clean(raw)
        if not t or re.fullmatch(r"p\.\s*\d+", t):
            continue
        if out and not re.search(r"[.!?:;\"'’”)\]]$", out[-1]) and t[0].islower():
            out[-1] += " " + t                                    # paragraph split by a page break
        elif out and len(out[-1]) < 60 and re.search(r"(:|\bsaid|\bspoke)$", out[-1]):
            out[-1] += " " + t                                    # "X said:" joins what follows
        else:
            out.append(t)
    return out


def passages(paras):
    out = []
    for p in paras:
        if out and len(out[-1]) + len(p) < TARGET:
            out[-1] += "\n\n" + p
        elif len(p) > MAX:                                        # split very long paragraphs at sentences
            buf = ""
            for s in re.split(r"(?<=[.!?])\s+", p):
                if buf and len(buf) + len(s) > TARGET:
                    out.append(buf.strip()); buf = ""
                buf += " " + s
            if buf.strip():
                out.append(buf.strip())
        else:
            out.append(p)
    return out


def main():
    chapters = []
    for i in range(CHAPTERS):
        url = BASE.format(FIRST_PAGE + i)
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (personal study app)"}, timeout=60)
        r.raise_for_status()
        page = r.content.decode("utf-8", errors="replace")      # the site doesn't declare its encoding
        ps = passages(paragraphs(chapter_html(page)))
        print(f"Chapter {i + 1}: {len(ps)} passages")
        if not ps:
            raise RuntimeError(f"No text found for chapter {i + 1}")
        chapters.append({"n": i + 1, "adhyaya": 16 + i, "passages": ps})
        time.sleep(1)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({
        "title": "Anu Gita",
        "source": "Translated by Kashinath Trimbak Telang, Sacred Books of the East vol. 8 (1882). Public domain; text via sacred-texts.com.",
        "chapters": chapters,
    }, ensure_ascii=False), encoding="utf-8")
    print("Saved", OUT, sum(len(c["passages"]) for c in chapters), "passages")


if __name__ == "__main__":
    main()
