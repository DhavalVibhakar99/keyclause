#!/usr/bin/env python3
"""Capture the few link-only sources the change tests depend on (one page each, no crawling).

Guide section 6 allows reading code-publisher pages "freely; respect terms, no bulk
scraping". This fetches only the listed pages, once, and saves plain text with the
same SOURCE/RETRIEVED header as the starter pack. Every rule extracted from these
pages is labelled team-captured and given lower confidence.

    python3 scripts/capture_links.py            # fetch the default set
    python3 scripts/capture_links.py D059 D060  # fetch specific doc ids

If a page comes back empty (some sites render text with JavaScript), open it in a
browser, select all text, and paste it into data/extra_corpus/<DOC_ID>.manual.txt;
re-running this script will pick it up and add the header.
"""
from __future__ import annotations
import csv
import html
import re
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "pack" / "corpus" / "corpus_manifest.csv"
OUT = ROOT / "data" / "extra_corpus"
EXTRA_MANIFEST = OUT / "extra_manifest.csv"

# Organizers confirmed (Discord, Oct 3) teams may capture link-only pages and use secondary sources.
# All link-only sources except CA statute mirrors already supplied as official text (D017-D021)
# and D056 (the official site refused capture). One request per page.
DEFAULT = ['D002', 'D015', 'D028', 'D030', 'D032', 'D033', 'D034', 'D035', 'D037', 'D038', 'D044', 'D054', 'D055', 'D059', 'D060', 'D061', 'D062', 'D063', 'D064', 'D070', 'D071', 'D072', 'D074', 'D075', 'D077', 'D086', 'D087']
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"


class Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "tr", "section", "article"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def to_text(raw):
    p = Text()
    p.feed(raw)
    t = html.unescape("".join(p.out))
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


def main(ids, force=False):
    OUT.mkdir(parents=True, exist_ok=True)
    rows = {r["doc_id"]: r for r in csv.DictReader(open(MANIFEST, newline="", encoding="utf-8"))}
    existing = {}
    if EXTRA_MANIFEST.exists():
        existing = {r["doc_id"]: r for r in csv.DictReader(open(EXTRA_MANIFEST, newline="", encoding="utf-8"))}
    for did in ids:
        src = rows[did]
        url = src["url"]
        prev = existing.get(did)
        if not force and prev and prev.get("status") == "ok" and (OUT / prev["text_file"]).exists():
            print(f"{did}: already captured ({prev['capture'][:40]}); skipped (use --force to refetch)")
            continue
        manual = OUT / f"{did}.manual.txt"
        stamp = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
        if manual.exists():
            body, how = manual.read_text(encoding="utf-8").strip(), "manual copy from browser"
        else:
            r = subprocess.run(["curl", "-sSL", "--max-time", "40", "-A", UA, url], capture_output=True, text=True)
            body = to_text(r.stdout) if r.returncode == 0 else ""
            how = "curl"
        words = len(body.split())
        if words < 80:
            print(f"{did}: only {words} words from {url} -- paste the page text into {manual.name} and re-run")
            continue
        (OUT / f"{did}.txt").write_text(f"SOURCE: {url}\nRETRIEVED: {stamp.replace('T', ' ').replace('Z', ' UTC')}\n\n{body}\n", encoding="utf-8")
        existing[did] = {
            "doc_id": did, "jurisdictions": src["jurisdictions"], "url": url,
            "source_type": src["source_type"] + " (team capture)", "capture": how,
            "retrieved_at": stamp, "sha256": "", "text_file": f"{did}.txt", "status": "ok",
        }
        print(f"{did}: saved {words} words ({how})")
        time.sleep(2)
    with open(EXTRA_MANIFEST, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["doc_id", "jurisdictions", "url", "source_type", "capture",
                                           "retrieved_at", "sha256", "text_file", "status"])
        w.writeheader()
        w.writerows(existing.values())
    print(f"manifest: {EXTRA_MANIFEST} ({len(existing)} documents)")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--force"]
    main(args or DEFAULT, force="--force" in sys.argv)
