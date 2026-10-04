"""Module C: which addresses each supplied change case affects.

Generic mechanics, driven by dev/change_tests.json:
  as_of     -> run the engine at both dates; affected = addresses whose result for the rule changes
  boundary  -> affected = addresses where the rule reaches (any result) on the date
  pending   -> affected = addresses the bill would cover if enacted (result 'pending')
  negative  -> affected = addresses with any result for the rule (expected: none)
Conflict flags come from the engine's lookups plus the test's own `conflict_with`
list (state law that may preempt local rules present at the address).
"""
from __future__ import annotations
import json

from . import config
from .engine import run_lookups


def _results_for(lookups, rule_ids):
    out = {}
    for aid, rows in lookups.items():
        for row in rows:
            if row["team_rule_id"] in rule_ids:
                out.setdefault(aid, {})[row["team_rule_id"]] = row
    return out


def run_changes(rules, facts, resolution, tests=None, cache=None):
    tests = tests or json.loads(config.CHANGE_TESTS.read_text(encoding="utf-8"))
    cache = cache if cache is not None else {}
    known = {r["team_rule_id"] for r in rules}

    def lk(as_of):
        if as_of not in cache:
            cache[as_of] = run_lookups(rules, facts, resolution, as_of)
        return cache[as_of]

    out = {}
    for t in tests:
        tid, ids = t["test_id"], t["rule_ids"]
        missing = [i for i in ids if i not in known]
        notes = [t.get("title", "")]
        if missing:
            notes.append(f"Not extracted from the corpus, so no address can be reported: {', '.join(missing)}.")
        affected, conflicts, detail = set(), set(), {}
        if t["type"] == "as_of":
            b, a = _results_for(lk(t["as_of_before"]), ids), _results_for(lk(t["as_of_after"]), ids)
            for aid in set(b) | set(a):
                for rid in ids:
                    rb = b.get(aid, {}).get(rid, {}).get("result")
                    ra = a.get(aid, {}).get(rid, {}).get("result")
                    if rb != ra:
                        affected.add(aid)
                    if a.get(aid, {}).get(rid, {}).get("conflict_flag") or b.get(aid, {}).get(rid, {}).get("conflict_flag"):
                        conflicts.add(aid)
            detail = {"before": t["as_of_before"], "after": t["as_of_after"],
                      "before_results": _count(b), "after_results": _count(a)}
            cw = t.get("conflict_with") or []
            if cw:
                here = _results_for(lk(t["as_of_after"]), cw)
                here_b = _results_for(lk(t["as_of_before"]), cw)
                for aid in set(here) | set(here_b):
                    if aid in affected:
                        conflicts.add(aid)
                notes.append(f"Conflict flag: addresses where {', '.join(ids)} would overlap local rule(s) {', '.join(cw)} "
                             "(possible preemption once in force); needs human review.")
        elif t["type"] in ("boundary", "pending", "negative"):
            r = _results_for(lk(t["as_of"]), ids)
            for aid, rows in r.items():
                if t["type"] == "pending" and not any(x["result"] == "pending" for x in rows.values()):
                    continue
                affected.add(aid)
                if any(x["conflict_flag"] for x in rows.values()):
                    conflicts.add(aid)
            detail = {"as_of": t["as_of"], "results": _count(r)}
            if t["type"] == "negative":
                status = {x["team_rule_id"]: x["status"] for x in rules if x["team_rule_id"] in ids}
                notes.append(f"Recorded status: {status or 'measure text not in corpus'}. Affected set is empty when no address receives this rule.")
                rent = _ma_rent_caps(lk(t["as_of"]), rules)
                notes.append("No rent cap reported for any Massachusetts address." if not rent
                             else f"WARNING: rent caps reported in MA at {sorted(rent)[:10]}")
        out[tid] = {
            "affected_address_ids": sorted(affected),
            "conflict_flag_address_ids": sorted(conflicts),
            "notes": " ".join(n for n in notes if n),
            "detail": detail,
        }
    return out


def _count(res):
    c = {}
    for rows in res.values():
        for rid, row in rows.items():
            c.setdefault(rid, {}).setdefault(row["result"], 0)
            c[rid][row["result"]] += 1
    return c


def _ma_rent_caps(lookups, rules):
    rent_ids = {r["team_rule_id"] for r in rules if r["category"] == "rent_increase_limits"
                and (r["jurisdiction"] == "MA" or r["jurisdiction"].endswith(", MA"))}
    hits = set()
    for aid, rows in lookups.items():
        for row in rows:
            if row["team_rule_id"] in rent_ids and row["result"] in ("applies", "unknown", "superseded", "not_yet_effective"):
                hits.add(aid)
    return hits
