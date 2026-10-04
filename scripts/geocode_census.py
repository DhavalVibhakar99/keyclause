#!/usr/bin/env python3
"""Resolve every sample address to its Census incorporated place.

Run this on a machine that can reach geocoding.geo.census.gov (e.g. your laptop):

    python3 scripts/geocode_census.py

It is resumable (raw answers are cached in data/geocode_raw/), polite (one request
at a time with a short pause) and uses only the standard library plus the system
`curl`, so it avoids Python certificate problems on macOS.

Output: data/geocode_results.csv with columns
    address_id, match, matched_address, place_name, place_geoid, county_name, query
"""
from __future__ import annotations
import csv
import json
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDRESSES = ROOT / "pack" / "data" / "sample_addresses.csv"
RAW_DIR = ROOT / "data" / "geocode_raw"
OUT = ROOT / "data" / "geocode_results.csv"
ENDPOINT = "https://geocoding.geo.census.gov/geocoder/geographies/address"
PARAMS = {
    "benchmark": "Public_AR_Current",
    "vintage": "Current_Current",
    "layers": "Incorporated Places,Counties",
    "format": "json",
}


def fetch(street, city, state, zipc):
    q = dict(PARAMS, street=street, state=state)
    if city:
        q["city"] = city
    if zipc:
        q["zip"] = zipc
    url = ENDPOINT + "?" + urllib.parse.urlencode(q)
    for attempt in range(3):
        r = subprocess.run(["curl", "-sS", "--max-time", "30", url], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip().startswith("{"):
            return url, json.loads(r.stdout)
        time.sleep(2 * (attempt + 1))
    return url, {"error": (r.stderr or r.stdout)[:500]}


def parse(data):
    matches = (data.get("result") or {}).get("addressMatches") or []
    if not matches:
        return {"match": "No_Match", "matched_address": "", "place_name": "", "place_geoid": "", "county_name": ""}
    m = matches[0]
    geos = m.get("geographies") or {}
    places = geos.get("Incorporated Places") or []
    counties = geos.get("Counties") or []
    return {
        "match": "Match" if len(matches) == 1 else f"Match ({len(matches)} candidates; first used)",
        "matched_address": m.get("matchedAddress", ""),
        "place_name": places[0].get("NAME", "") if places else "",
        "place_geoid": places[0].get("GEOID", "") if places else "",
        "county_name": counties[0].get("NAME", "") if counties else "",
    }


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(open(ADDRESSES, newline="", encoding="utf-8")))
    out_rows = []
    for i, r in enumerate(rows, 1):
        aid = r["address_id"]
        raw_path = RAW_DIR / f"{aid}.json"
        if raw_path.exists():
            cached = json.loads(raw_path.read_text())
        else:
            # Try with the postal city first; neighborhood names ("Dorchester") can fail
            # to match, so retry with ZIP only.
            url, data = fetch(r["street_address"], r["postal_city"], r["state"], r["zip"])
            parsed = parse(data) if "error" not in data else None
            if not parsed or parsed["match"] == "No_Match":
                url2, data2 = fetch(r["street_address"], "", r["state"], r["zip"])
                if "error" not in data2 and parse(data2)["match"] != "No_Match":
                    url, data = url2, data2
            cached = {"query": url, "response": data}
            raw_path.write_text(json.dumps(cached))
            time.sleep(0.3)
        p = parse(cached["response"]) if "error" not in cached["response"] else {
            "match": "Error", "matched_address": "", "place_name": "", "place_geoid": "", "county_name": ""}
        out_rows.append({"address_id": aid, **p, "query": cached["query"]})
        if i % 25 == 0:
            print(f"{i}/{len(rows)} done", flush=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["address_id", "match", "matched_address", "place_name", "place_geoid", "county_name", "query"])
        w.writeheader()
        w.writerows(out_rows)
    from collections import Counter
    print("match status:", Counter(r["match"].split(" (")[0] for r in out_rows))
    print("places:", Counter(r["place_name"] for r in out_rows).most_common(15))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    sys.exit(main())
