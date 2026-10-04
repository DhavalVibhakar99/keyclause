"""Self-check: our stand-in for the hidden judge scripts.

Prints PASS/FAIL/WARN per check and returns a non-zero exit code on any FAIL.
  - schema validity of rules.json, lookups.json, changes.json
  - every quoted_span re-verified verbatim against its source document
  - T1-T5 asserted from their expected_behavior
  - invariants (city rules only inside their city, all 500 addresses, no MA rent cap)
  - optional hand-labelled gold set (data/gold_set.csv)
"""
from __future__ import annotations
import csv
import json

from . import config
from .corpus import load_corpus
from .spans import SpanIndex

RESULTS = {"applies", "unknown", "superseded", "not_yet_effective", "pending"}


class Report:
    def __init__(self):
        self.rows = []

    def add(self, level, name, detail=""):
        self.rows.append((level, name, detail))

    def ok(self, cond, name, detail="", warn=False):
        self.add("PASS" if cond else ("WARN" if warn else "FAIL"), name, "" if cond else detail)

    def print(self):
        for level, name, detail in self.rows:
            print(f"[{level}] {name}" + (f" -- {detail}" if detail else ""))
        fails = sum(1 for r in self.rows if r[0] == "FAIL")
        warns = sum(1 for r in self.rows if r[0] == "WARN")
        print(f"\n{len(self.rows) - fails - warns} passed, {warns} warnings, {fails} failed")
        return fails


def _load(name):
    return json.loads((config.OUT_DIR / name).read_text(encoding="utf-8"))


def check(facts, resolution, lookups_by_date=None):
    rep = Report()
    rules = _load("rules.json")["rules"]
    lookups = _load("lookups.json")
    changes = _load("changes.json")
    by_id = {r["team_rule_id"]: r for r in rules}

    # schema
    from .schema_check import validate
    schema = json.loads(config.SCHEMA.read_text(encoding="utf-8"))
    errs = []
    for r in rules:
        errs.extend(f"{r.get('team_rule_id')}: {e}" for e in validate(r, schema))
    rep.ok(not errs, f"rules.json: {len(rules)} records match the schema", "; ".join(errs[:5]))
    lk_shape = isinstance(lookups.get("lookups"), dict) and all(
        isinstance(rows, list) and all({"team_rule_id", "result", "explanation", "conflict_flag"} <= set(x) for x in rows)
        for rows in lookups["lookups"].values())
    rep.ok(lk_shape, "lookups.json matches the template shape")
    ch_shape = all({"affected_address_ids", "conflict_flag_address_ids", "notes"} <= set(v) for v in changes.values())
    rep.ok(ch_shape, "changes.json matches the template shape")
    rep.ok(len(by_id) == len(rules), "rule ids are unique")

    # spans
    corpus = load_corpus()
    idx_cache, bad = {}, []
    for r in rules:
        d = corpus.get(r.get("source_doc_id"))
        if not d or not d.has_text:
            bad.append(f"{r['team_rule_id']} (no source text)")
            continue
        idx = idx_cache.setdefault(d.doc_id, SpanIndex(d.body))
        if idx.verify(r["quoted_span"]) is None:
            bad.append(r["team_rule_id"])
        if d.url != r["source_url"]:
            bad.append(f"{r['team_rule_id']} (source_url mismatch)")
    for r in rules:   # team-chosen coverage must rest on verified starter-pack quotes
        for ev in r.get("coverage_evidence") or []:
            d = corpus.get(ev["doc_id"])
            if not d or (d.origin != "starter_pack" and d.doc_id != r.get("source_doc_id")) \
                    or SpanIndex(d.body).verify(ev["quote"]) is None:
                bad.append(f"{r['team_rule_id']} (coverage evidence not verified in {ev['doc_id']})")
    rep.ok(not bad, f"all {len(rules)} quoted spans found verbatim in their source documents", ", ".join(bad[:10]))
    rep.ok(all(r.get("retrieved_at") for r in rules), "every rule carries a retrieval date")
    # Organizer ruling: only quotes found in the distributed starter-pack corpus count as citations.
    in_pack = {r["team_rule_id"] for r in rules
               if corpus.get(r.get("source_doc_id")) and corpus[r["source_doc_id"]].origin == "starter_pack"}
    outside = [r["team_rule_id"] for r in rules if r["team_rule_id"] not in in_pack]
    applies = [x for rows in lookups.get("lookups", {}).values() for x in rows if x["result"] == "applies"]
    backed = sum(1 for x in applies if x["team_rule_id"] in in_pack)
    share = f"{backed}/{len(applies)} 'applies' answers ({100 * backed / max(1, len(applies)):.1f}%) cite a starter-pack quote"
    if outside:
        rep.add("WARN", f"{len(outside)} rules' main quote is not in the distributed corpus (excluded from the citation metric); {share}",
                ", ".join(outside))
    else:
        rep.add("PASS", f"every rule's main quote is in the distributed corpus; {share}")

    # lookups shape
    L = lookups.get("lookups", {})
    rep.ok(lookups.get("as_of") == config.DEFAULT_AS_OF, f"lookups as_of = {config.DEFAULT_AS_OF}")
    rep.ok(set(L) == set(facts), f"lookups cover all {len(facts)} addresses", f"{len(set(facts) - set(L))} missing")
    unknown_ids = {row["team_rule_id"] for rows in L.values() for row in rows} - set(by_id)
    rep.ok(not unknown_ids, "every lookup references an existing rule", ", ".join(sorted(unknown_ids))[:200])
    bad_res = [row["result"] for rows in L.values() for row in rows if row["result"] not in RESULTS]
    rep.ok(not bad_res, "every result uses an allowed value", str(set(bad_res)))

    # invariant: city rules only inside their city
    leaks = []
    for aid, rows in L.items():
        city = resolution[aid].get("city")
        for row in rows:
            r = by_id.get(row["team_rule_id"])
            if r and r["level"] == "city" and r["jurisdiction"] != city:
                leaks.append(f"{aid}:{r['team_rule_id']}")
            if r and r["level"] == "state" and r["jurisdiction"] != facts[aid].state:
                leaks.append(f"{aid}:{r['team_rule_id']}")
    rep.ok(not leaks, "no rule applied outside its jurisdiction", ", ".join(leaks[:10]))

    # change tests
    tests = json.loads(config.CHANGE_TESTS.read_text(encoding="utf-8"))
    rep.ok(all(t["test_id"] in changes for t in tests), "changes.json covers T1-T5")
    by_state = {s: {a for a, f in facts.items() if f.state == s} for s in ("CA", "NJ", "MA")}
    by_city = {}
    for a, res in resolution.items():
        by_city.setdefault(res.get("city"), set()).add(a)
    lk = lookups_by_date or {}

    def res_for(as_of, rid):
        table = lk.get(as_of, {})
        return {a: next((x["result"] for x in rows if x["team_rule_id"] == rid), None) for a, rows in table.items()}

    for rid in ("CA-ALG-01", "JC-ALG-01", "HOB-ALG-01", "NJ-ALG-01", "MA-ALG-P1", "MA-ALG-P2"):
        rep.ok(rid in by_id, f"rule {rid} extracted (needed by change tests)", "missing")

    if "CA-ALG-01" in by_id and lk:
        b, a = res_for("2025-12-31", "CA-ALG-01"), res_for("2026-01-02", "CA-ALG-01")
        rep.ok(all(b.get(x) == "not_yet_effective" for x in by_state["CA"]), "T1: CA-ALG-01 not_yet_effective on 2025-12-31 for every CA address",
               f"{sum(b.get(x) != 'not_yet_effective' for x in by_state['CA'])} CA addresses differ")
        rep.ok(all(a.get(x) == "applies" for x in by_state["CA"]), "T1: CA-ALG-01 applies on 2026-01-02 for every CA address",
               f"{sum(a.get(x) != 'applies' for x in by_state['CA'])} CA addresses differ")
    for rid, city in (("HOB-ALG-01", "Hoboken, NJ"), ("JC-ALG-01", "Jersey City, NJ")):
        if rid in by_id:
            got = {a for a, rows in L.items() if any(x["team_rule_id"] == rid for x in rows)}
            rep.ok(got == by_city.get(city, set()), f"T2: {rid} exactly on {city} addresses",
                   f"{len(got - by_city.get(city, set()))} outside, {len(by_city.get(city, set()) - got)} missing")
    if "NJ-ALG-01" in by_id and lk:
        b, a = res_for("2026-10-01", "NJ-ALG-01"), res_for("2027-07-02", "NJ-ALG-01")
        rep.ok(all(b.get(x) == "not_yet_effective" for x in by_state["NJ"]), "T3: NJ-ALG-01 not_yet_effective on 2026-10-01 for every NJ address",
               f"{sum(b.get(x) != 'not_yet_effective' for x in by_state['NJ'])} differ")
        rep.ok(all(a.get(x) == "applies" for x in by_state["NJ"]), "T3: NJ-ALG-01 applies on 2027-07-02 for every NJ address",
               f"{sum(a.get(x) != 'applies' for x in by_state['NJ'])} differ")
        jc_hob = by_city.get("Jersey City, NJ", set()) | by_city.get("Hoboken, NJ", set())
        flagged = set(changes.get("T3", {}).get("conflict_flag_address_ids", []))
        rep.ok(jc_hob <= flagged, "T3: Jersey City and Hoboken addresses carry a conflict flag",
               f"{len(jc_hob - flagged)} not flagged")
    for rid in ("MA-ALG-P1", "MA-ALG-P2"):
        if rid in by_id:
            got = {a for a, rows in L.items() if any(x["team_rule_id"] == rid and x["result"] == "pending" for x in rows)}
            rep.ok(got == by_state["MA"], f"T4: {rid} pending for every MA address", f"{len(by_state['MA'] - got)} missing")
    rep.ok(changes.get("T5", {}).get("affected_address_ids") == [], "T5: affected set is empty")
    rent_ma = [a for a, rows in L.items() if facts[a].state == "MA" and any(
        by_id[x["team_rule_id"]]["category"] == "rent_increase_limits" and x["result"] != "pending" for x in rows)]
    rep.ok(not rent_ma, "T5: no rent cap reported for any Boston or Cambridge address", ", ".join(rent_ma[:10]))

    # gold set
    if config.GOLD_SET.exists():
        total = correct = blank = 0
        misses = []
        with open(config.GOLD_SET, newline="", encoding="utf-8") as fh:
            for g in csv.DictReader(fh):
                if not (g.get("expected_result") or "").strip():
                    blank += 1          # template row not labelled yet
                    continue
                total += 1
                rows = L.get(g["address_id"], [])
                rid = (g.get("team_rule_id") or "").strip()
                if rid:                 # a named rule; needed where a jurisdiction has two rules in one category
                    got = next((x["result"] for x in rows if x["team_rule_id"] == rid), "not_listed")
                else:
                    got = next((x["result"] for x in rows if by_id[x["team_rule_id"]]["category"] == g["category"]
                                and by_id[x["team_rule_id"]]["jurisdiction"] == g["jurisdiction"]), "not_listed")
                g["expected_result"] = g["expected_result"].strip()
                if got == g["expected_result"]:
                    correct += 1
                else:
                    misses.append(f"{g['address_id']} {g['jurisdiction']} {g['category']}: expected {g['expected_result']}, got {got}")
        if total:
            rep.ok(correct == total, f"gold set: {correct}/{total} match", "\n    " + "\n    ".join(misses), warn=True)
        if blank:
            rep.add("WARN", f"gold set: {blank} row(s) not labelled yet")
    else:
        rep.add("WARN", "no gold set yet (data/gold_set.csv)")
    return rep
