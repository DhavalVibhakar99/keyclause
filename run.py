#!/usr/bin/env python3
"""Rental Housing Law Navigator: one command per pipeline step.

  python3 run.py extract [--docs D024,D069]   Module A (uses the model; cached)
  python3 run.py build                        assemble rules + lookups + changes (no model)
  python3 run.py check                        self-check (schemas, spans, T1-T5, invariants, gold set)
  python3 run.py all                          extract + build + check
  python3 run.py explain A0001 [--as-of DATE] show one address's answer
  python3 run.py export                       app/simple.json + app/by_city.json (copied to docs/; no model)
Not legal advice.
"""
from __future__ import annotations
import argparse
import json
import sys

from nav import audit, config
from nav.assemble import assemble, write_rules
from nav.changes import run_changes
from nav.engine import run_lookups
from nav.facts import load_addresses
from nav.geo import resolve_all

EXTRA_DATES = ["2025-12-31", "2026-01-02", "2027-07-02"]


def cmd_extract(args):
    from nav.extract import run
    from nav.llm import backend
    print(f"LLM backend: {backend()} (api = .anthropic_key credits; claude_cli = Claude Code on your subscription)")
    docs = set(args.docs.split(",")) if args.docs else None
    results, failures = run(docs, workers=args.workers)
    kept = sum(len(r["rules"]) for r in results.values())
    print(f"\n{len(results)} documents processed, {kept} rule candidates kept, {len(failures)} failures")
    return 1 if failures else 0


def cmd_classify(args):
    from nav.classify import classify_records
    extracted = json.loads(config.EXTRACTED.read_text(encoding="utf-8"))
    recs = [r for d in extracted.values() for r in d.get("rules", [])]
    if args.force:
        for r in recs:
            r.pop("rule_effect", None)
    classify_records(recs)
    config.EXTRACTED.write_text(json.dumps(extracted, indent=1, ensure_ascii=False), encoding="utf-8")
    from collections import Counter
    print(Counter(r["rule_effect"] for r in recs))
    for r in recs:
        if r["rule_effect"] != "regulates_landlords":
            print(f"  {r['rule_effect']}: {r['source_doc_id']} {r['jurisdiction']} {r['title'][:70]} -- {r.get('rule_effect_reason')}")
    return 0


def _build(as_of=config.DEFAULT_AS_OF):
    extracted = json.loads(config.EXTRACTED.read_text(encoding="utf-8"))
    rules = assemble(extracted, as_of)
    write_rules(rules)
    facts = load_addresses()
    resolution = resolve_all(facts)
    cache = {d: run_lookups(rules, facts, resolution, d) for d in [as_of] + EXTRA_DATES}
    lookups = {"as_of": as_of, "lookups": {aid: [{k: v for k, v in row.items() if k != "unknown_codes"} for row in rows]
                                             for aid, rows in cache[as_of].items()}}
    (config.OUT_DIR / "lookups.json").write_text(json.dumps(lookups, indent=1, ensure_ascii=False), encoding="utf-8")
    changes = run_changes(rules, facts, resolution, cache=cache)
    (config.OUT_DIR / "changes.json").write_text(json.dumps(changes, indent=1, ensure_ascii=False), encoding="utf-8")
    (config.OUT_DIR / "resolution.json").write_text(json.dumps(resolution, indent=1), encoding="utf-8")
    from nav.bundle import write_bundle
    write_bundle(rules, facts, resolution, cache, changes)
    audit.log("built", as_of=as_of, rules=len(rules), addresses=len(facts))
    return rules, facts, resolution, cache


def cmd_build(args):
    rules, facts, resolution, cache = _build()
    from collections import Counter
    c = Counter(row["result"] for rows in cache[config.DEFAULT_AS_OF].values() for row in rows)
    print(f"{len(rules)} rules; lookups for {len(facts)} addresses; results: {dict(c)}")
    print("wrote out/rules.json, out/lookups.json, out/changes.json")
    return 0


def cmd_check(args):
    from nav.selfcheck import check
    rules, facts, resolution, cache = _build()
    return 1 if check(facts, resolution, cache).print() else 0


def cmd_all(args):
    rc = cmd_extract(args)
    if rc:
        print("extraction had failures; building with what succeeded")
    return cmd_check(args)


def cmd_explain(args):
    rules = json.loads((config.OUT_DIR / "rules_internal.json").read_text(encoding="utf-8"))
    facts = load_addresses()
    res = resolve_all(facts)
    from nav.engine import lookup_address
    f = facts[args.address_id]
    print(f"{f.address_id}: {f.street_address}, {f.postal_city}, {f.state} {f.zip}")
    print(f"  built {f.year_built or 'unknown'}, {f.units_label()} ({f.units_source})")
    print(f"  jurisdiction: {res[f.address_id]['resolved_label']} via {res[f.address_id]['method']}")
    print(f"  as of {args.as_of}. Not legal advice.\n")
    by_id = {r["team_rule_id"]: r for r in rules}
    for row in lookup_address(rules, f, res[f.address_id], args.as_of):
        r = by_id[row["team_rule_id"]]
        print(f"[{row['result'].upper()}] {row['team_rule_id']} {r['title']} -- {r['citation']}")
        print(f"   {row['explanation']}")
        print(f"   source: {r['source_url']} (retrieved {r.get('retrieved_at')})")
        print(f"   quote: \"{r['quoted_span'][:200]}\"\n")
    return 0


def cmd_export(args):
    from nav.exports import export, find_range
    simple, by_city, report, facts, resolution, paths = export()
    for p in paths:
        print(f"wrote {p.relative_to(config.ROOT)} ({p.stat().st_size:,} bytes) and docs/{p.name}")
    print("\nPlaces (ranges from each place's rules; splits found by the both-ends check; census name seen in geocoder):")
    places = {p["key"]: p for p in by_city["places"]}
    for k, p in places.items():
        splits, seen = report[k]
        print(f"  {k:18} {p['kind']:10} {len(p['year_ranges']) - 1} year ranges x {len(p['unit_ranges']) - 1} unit ranges"
              f" | splits: {splits or 'none'} | census_names {p['census_names']}"
              + (f" (seen {seen}x)" if seen is not None else ""))

    # self-test: sample addresses with an exact year and exact unit count, looked up in by_city.json
    addrs = {a["id"]: a for a in simple["addresses"]}
    total, mism = 0, []
    for aid, f in sorted(facts.items()):
        if f.year_built is None or f.units_exact is None:
            continue
        total += 1
        city = resolution[aid].get("city")
        p = places.get(city) or places[f.state]
        key = f"{find_range(p['year_ranges'], f.year_built)}|{find_range(p['unit_ranges'], f.units_exact)}"
        want = [t["status"] for t in addrs[aid]["topics"]]
        got = [t["status"] for t in p["results"][key]]
        if want != got:
            diff = [f"{t['topic']}: sample {w}, table {g}" for t, w, g in zip(addrs[aid]["topics"], want, got) if w != g]
            cause = []
            d = f.use_description.upper()
            if f.subsidized:
                cause.append("subsidized housing in the assessor record")
            if any(k in d for k in ("CONDO", "TIC")):
                cause.append(f"use description '{f.use_description}' (condo/TIC)")
            if resolution[aid].get("flags"):
                cause.append("geocoder flag: " + "; ".join(resolution[aid]["flags"]))
            mism.append(f"  {aid} {city} built {f.year_built}, {f.units_exact} units -> {key}: " + "; ".join(diff)
                        + f"\n      cause: {'; '.join(cause) or 'not explained by use description, subsidy or geocoder flags'}")
    print(f"\nSelf-test: {total - len(mism)}/{total} sample addresses with exact year and units match by_city.json")
    print("\n".join(mism) if mism else "  no mismatches")

    def show(title, topics):
        print(f"\n{title}")
        for t in topics:
            print(f"  {t['topic']:20} [{t['status']}{', flagged' if t['flag'] else ''}] {t['sentence']}")
    for aid in ("A0001", "A0002"):
        a = addrs[aid]
        show(f"{aid}: {a['street']}, {a['postal_city']} (built {a['year_built']}, {a['units_label']})", a["topics"])
    for title, key, y, u in (("Jersey City, built 1965, 12 units", "Jersey City, NJ", 1965, 12),
                             ("Jersey City, year unknown, 12 units", "Jersey City, NJ", None, 12),
                             ("Hoboken, built 2001, 20 units", "Hoboken, NJ", 2001, 20),
                             ("New Jersey outside the three cities (state only), built 1990, 30 units", "NJ", 1990, 30)):
        p = places[key]
        k = f"{find_range(p['year_ranges'], y)}|{find_range(p['unit_ranges'], u)}"
        show(f"{title}  [{k}]", p["results"][k])
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract"); e.add_argument("--docs"); e.add_argument("--workers", type=int, default=2)
    c = sub.add_parser("classify"); c.add_argument("--force", action="store_true")
    sub.add_parser("build")
    sub.add_parser("check")
    a = sub.add_parser("all"); a.add_argument("--docs"); a.add_argument("--workers", type=int, default=2)
    sub.add_parser("export")
    x = sub.add_parser("explain"); x.add_argument("address_id"); x.add_argument("--as-of", default=config.DEFAULT_AS_OF)
    args = p.parse_args()
    return {"classify": cmd_classify, "extract": cmd_extract, "build": cmd_build, "check": cmd_check, "all": cmd_all, "explain": cmd_explain, "export": cmd_export}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
