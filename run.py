#!/usr/bin/env python3
"""Rental Housing Law Navigator: one command per pipeline step.

  python3 run.py extract [--docs D024,D069]   Module A (uses the model; cached)
  python3 run.py build                        assemble rules + lookups + changes (no model)
  python3 run.py check                        self-check (schemas, spans, T1-T5, invariants, gold set)
  python3 run.py all                          extract + build + check
  python3 run.py explain A0001 [--as-of DATE] show one address's answer
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


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract"); e.add_argument("--docs"); e.add_argument("--workers", type=int, default=2)
    c = sub.add_parser("classify"); c.add_argument("--force", action="store_true")
    sub.add_parser("build")
    sub.add_parser("check")
    a = sub.add_parser("all"); a.add_argument("--docs"); a.add_argument("--workers", type=int, default=2)
    x = sub.add_parser("explain"); x.add_argument("address_id"); x.add_argument("--as-of", default=config.DEFAULT_AS_OF)
    args = p.parse_args()
    return {"classify": cmd_classify, "extract": cmd_extract, "build": cmd_build, "check": cmd_check, "all": cmd_all, "explain": cmd_explain}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
