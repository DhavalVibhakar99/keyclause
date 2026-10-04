"""JSON exports for the renter app (app/simple.json and app/by_city.json, copied to docs/).

Pure code, no model. Uses the unchanged engine and the same assembled rules as `run.py build`,
and never writes to out/ (audit logging is silenced while the export runs).

- simple.json: the 500 sample addresses, six topics each, one templated sentence per topic.
- by_city.json: answers for a building that is not in the sample, per place (10 cities we read
  local law for, plus state-only CA / NJ / MA), for every year-built range x unit-count range.
  The ranges come from the place's own rules (unit thresholds, exemption caps, date cutoffs,
  rolling new-construction windows), and each range is checked to give the same answer at both ends.
"""
from __future__ import annotations
import csv
import json
import re
import shutil
from datetime import date

from . import audit, config
from .facts import Facts

AS_OF = config.DEFAULT_AS_OF
TOPICS = [
    ("rent_increase_limits", "Rent increases"),
    ("just_cause_eviction", "Eviction"),
    ("security_deposits", "Security deposit"),
    ("application_screening_fees", "Screening fee"),
    ("screening_restrictions", "Screening rules"),
    ("algorithmic_rent_setting", "Algorithmic pricing"),
]
CITIES = ["Los Angeles, CA", "San Francisco, CA", "Berkeley, CA", "San Diego, CA", "Santa Ana, CA",
          "Boston, MA", "Cambridge, MA", "Hoboken, NJ", "Jersey City, NJ", "Newark, NJ"]
STATES = {"CA": "California", "NJ": "New Jersey", "MA": "Massachusetts"}
YEAR_FLOOR, UNIT_CEIL = 1800, 1000          # open-ended ranges are checked at these values
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


# ---------- sentences (fixed templates over existing fields) ----------

def _because(code, f):
    """Same reasons as the web app's summary (app/index.html, becauseText)."""
    units = f.units_label()
    return {
        "year_built": "the construction year isn't public",
        "year_exact": f"it was built in {f.year_built}, the cutoff year, and the exact date isn't public",
        "units": (f'the unit count is only known as "{units}"' if units != "unit count unknown"
                  else "the unit count isn't public"),
        "owner": "owner details aren't public",
        "exemption_use": f'an exemption may cover this property type ("{f.use_description}")',
        "subsidized": "it's recorded as subsidized housing, which may be exempt",
        "scope": "a rule covers only certain programs or events the records don't show",
        "date_imprecise": "a law's start date isn't precise enough for this date",
        "filing": "the building may be newly built and exempt, which depends on a filing that isn't public",
        "local_rule": "it depends on whether a local rule covers the building",
    }.get(code, "a needed fact isn't public")


def _join(parts):
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " or " + parts[-1]


def _first_sentence(text):
    return re.split(r"(?<=[.!?])\s+", (text or "").strip())[0]


def _period(s):
    s = s.strip()
    return s if s.endswith((".", "!", "?")) else s + "."


def _nice_date(d):
    if not d:
        return "a later date"
    p = d.split("-")
    if len(p) == 3:
        return f"{MONTHS[int(p[1]) - 1]} {int(p[2])}, {p[0]}"
    if len(p) == 2:
        return f"{MONTHS[int(p[1]) - 1]} {p[0]}"
    return p[0]


def topic_rows(rows, S, f):
    """Six topic rows from the engine's rows for one building. S = schema rules by id."""
    out = []
    for cat, name in TOPICS:
        rs = [x for x in rows if S[x["team_rule_id"]]["category"] == cat]
        results = {x["result"] for x in rs}
        ids = [x["team_rule_id"] for x in rs]
        flag = any(x["conflict_flag"] for x in rs)
        if results & {"applies", "superseded"}:
            status = "applies"
            gov = sorted((x for x in rs if x["result"] == "applies"),
                         key=lambda x: (S[x["team_rule_id"]]["level"] != "city", x["team_rule_id"]))
            r = S[gov[0]["team_rule_id"]] if gov else S[rs[0]["team_rule_id"]]
            detail = r.get("key_value") or _first_sentence(r.get("requirement"))
            sentence = "A rule applies here." + (" " + _period(detail[0].upper() + detail[1:]) if detail else "")
        elif "unknown" in results:
            status = "unknown"
            codes = []
            for x in rs:
                for c in x.get("unknown_codes") or []:
                    if c not in codes:
                        codes.append(c)
            reason = _join([_because(c, f) for c in codes] or [_because("", f)])
            sentence = "Can't tell from public records. " + _period(reason[0].upper() + reason[1:])
        elif "not_yet_effective" in results:
            status = "not_yet_effective"
            dates = sorted(S[x["team_rule_id"]].get("effective_date") or "" for x in rs
                           if x["result"] == "not_yet_effective")
            sentence = f"A law is passed but starts on {_nice_date(dates[0] if dates else None)}."
        elif "pending" in results:
            status = "pending"
            sentence = "A bill is proposed. It is not law."
        else:
            status = "none"
            sentence = "No rule found in the laws this tool has read for this address."
        out.append({"topic": name, "status": status, "sentence": sentence, "rule_ids": ids, "flag": flag})
    return out


# ---------- engine access ----------

def _load():
    """Assemble rules exactly as `run.py build` does, without writing anything to out/."""
    from .assemble import assemble, to_schema
    from .facts import load_addresses
    from .geo import resolve_all
    audit.log = lambda *a, **k: None          # the export must not touch out/audit_log.jsonl
    extracted = json.loads(config.EXTRACTED.read_text(encoding="utf-8"))
    rules = assemble(extracted, AS_OF)
    S = {r["team_rule_id"]: to_schema(r) for r in rules}
    facts = load_addresses()
    return rules, S, facts, resolve_all(facts)


def _synthetic(state, city, year, units):
    return Facts(address_id="SYNTH", street_address="", postal_city=(city or "").split(",")[0], state=state,
                 zip="", year_built=year, units_min=units, units_max=units,
                 units_source="user input" if units is not None else "none", use_code="",
                 use_description="APARTMENT BUILDING", subsidized=False, source_dataset="synthetic",
                 retrieved_at=AS_OF)


def _res(state, city):
    return {"state": state, "city": city, "resolved_label": city or state, "method": "synthetic",
            "flags": [], "note": None}


# ---------- ranges ----------

def _breakpoints(rules, res):
    """Range start values implied by the place's own rules."""
    from .engine import _applicable
    from .dates import parse
    ys, us = set(), {2}                       # 2: the engine treats 2+ units as a multifamily building
    q = date.fromisoformat(AS_OF)
    for r in _applicable(rules, res):
        cov = r.get("coverage") or {}
        if cov.get("min_units"):
            us.add(cov["min_units"])
        if cov.get("max_units"):
            us.add(cov["max_units"] + 1)
        dc = cov.get("date_cutoff") or {}
        if dc.get("date") and parse(dc["date"]):
            y = parse(dc["date"])[0].year
            ys |= {y, y + 1}                  # the cutoff year gets its own range
        roll = cov.get("rolling_new_construction_exempt_years")
        if roll:
            ys |= {q.year - roll, q.year - roll + 1}
        for e in cov.get("exemptions") or []:
            if e.get("applies_only_if_units_at_most") is not None:
                us.add(e["applies_only_if_units_at_most"] + 1)
            if e.get("exempt_years"):
                ys.add(q.year - e["exempt_years"])
            if e.get("constructed_after"):
                ys.add(int(e["constructed_after"][:4]))
    return sorted(y for y in ys if YEAR_FLOOR < y <= q.year), sorted(u for u in us if u > 1)


def _ranges(starts, lo, hi):
    """[(from, to)] covering lo..open with no gaps; first `from` and last `to` are open (None)."""
    out, prev = [], None
    for s in starts:
        out.append((prev, s - 1))
        prev = s
    out.append((prev, None))
    return [(a if a is not None and a > lo else None, b) for a, b in out]


def _ends(rng, lo, hi):
    a, b = rng
    return (a if a is not None else lo), (b if b is not None else hi)


def _rep(rng, lo, hi):
    a, b = _ends(rng, lo, hi)
    return (a + b) // 2


def _year_label(r):
    a, b = r
    if a is None and b is None:
        return "Any year"
    if a is None:
        return f"{b} or earlier"
    if b is None:
        return f"{a} or later"
    return str(a) if a == b else f"{a} to {b}"


def _unit_label(r):
    a, b = r
    a = a if a is not None else 1
    if b is None:
        return f"{a} or more units"
    if a == b:
        return f"{a} unit" if a == 1 else f"{a} units"
    return f"{a} to {b} units"


def _signature(rules, res, state, city, year, units):
    from .engine import lookup_address
    rows = lookup_address(rules, _synthetic(state, city, year, units), res, AS_OF)
    return rows, tuple((x["team_rule_id"], x["result"], x["conflict_flag"], tuple(x["unknown_codes"])) for x in rows)


def _place(rules, S, state, city, census_names):
    res = _res(state, city)
    q_year = date.fromisoformat(AS_OF).year
    y_starts, u_starts = _breakpoints(rules, res)
    sig = lambda y, u: _signature(rules, res, state, city, y, u)[1]
    splits = []
    # verify both ends of every range; a difference means a breakpoint is missing, so split there
    changed = True
    while changed:
        changed = False
        yr = _ranges(y_starts, YEAR_FLOOR, q_year)
        ur = _ranges([u for u in u_starts], 1, UNIT_CEIL)
        u_probe = [None] + [_rep(r, 1, UNIT_CEIL) for r in ur]
        y_probe = [None] + [_rep(r, YEAR_FLOOR, q_year) for r in yr]
        for r in yr:
            a, b = _ends(r, YEAR_FLOOR, q_year)
            for u in u_probe:
                if sig(a, u) != sig(b, u):
                    first = next(y for y in range(a + 1, b + 1) if sig(y, u) != sig(a, u))
                    y_starts = sorted(set(y_starts) | {first}); splits.append(f"year {first}"); changed = True
                    break
            if changed:
                break
        if changed:
            continue
        for r in ur:
            a, b = _ends(r, 1, UNIT_CEIL)
            for y in y_probe:
                if sig(y, a) != sig(y, b):
                    first = next(u for u in range(a + 1, b + 1) if sig(y, u) != sig(y, a))
                    u_starts = sorted(set(u_starts) | {first}); splits.append(f"units {first}"); changed = True
                    break
            if changed:
                break
    yr, ur = _ranges(y_starts, YEAR_FLOOR, q_year), _ranges(u_starts, 1, UNIT_CEIL)
    year_ranges = [{"id": "y_unknown", "label": "I don't know"}] + [
        {"id": f"y{i}", "label": _year_label(r), "from": r[0], "to": r[1]} for i, r in enumerate(yr, 1)]
    unit_ranges = [{"id": "u_unknown", "label": "I don't know"}] + [
        {"id": f"u{i}", "label": _unit_label(r), "from": r[0] if r[0] is not None else 1, "to": r[1]}
        for i, r in enumerate(ur, 1)]
    results = {}
    for yd, yv in [("y_unknown", None)] + [(f"y{i}", _rep(r, YEAR_FLOOR, q_year)) for i, r in enumerate(yr, 1)]:
        for ud, uv in [("u_unknown", None)] + [(f"u{i}", _rep(r, 1, UNIT_CEIL)) for i, r in enumerate(ur, 1)]:
            rows, _ = _signature(rules, res, state, city, yv, uv)
            results[f"{yd}|{ud}"] = topic_rows(rows, S, _synthetic(state, city, yv, uv))
    return {"key": city or state, "state": state, "kind": "city" if city else "state_only",
            "census_names": census_names, "year_ranges": year_ranges, "unit_ranges": unit_ranges,
            "results": results}, splits


def _census_names():
    """Census 'Incorporated Places' names, as the geocoder returned them for our addresses."""
    seen = {}
    if config.GEOCODE_RESULTS.exists():
        with open(config.GEOCODE_RESULTS, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row.get("place_name"):
                    seen.setdefault(row["place_name"], 0)
                    seen[row["place_name"]] += 1
    names = {}
    for city in CITIES:
        canonical = city.split(",")[0] + " city"        # e.g. "Jersey City city"
        names[city] = {"names": [canonical], "seen_in_geocoder": seen.get(canonical, 0)}
    return names


def find_range(ranges, value):
    if value is None:
        return ranges[0]["id"]
    for r in ranges[1:]:
        if (r.get("from") is None or value >= r["from"]) and (r.get("to") is None or value <= r["to"]):
            return r["id"]
    raise ValueError(f"no range covers {value}")


# ---------- main ----------

def export():
    from .engine import lookup_address
    rules, S, facts, resolution = _load()

    # simple.json from the same engine; must equal out/lookups.json exactly
    lookups = {aid: lookup_address(rules, facts[aid], resolution[aid], AS_OF) for aid in sorted(facts)}
    saved = json.loads((config.OUT_DIR / "lookups.json").read_text(encoding="utf-8"))["lookups"]
    strip = {a: [{k: v for k, v in x.items() if k != "unknown_codes"} for x in rows] for a, rows in lookups.items()}
    if strip != saved:
        raise SystemExit("export: engine results differ from out/lookups.json; run `python3 run.py build` first")
    simple = {
        "as_of": AS_OF,
        "rules": {i: {k: r.get(k) for k in ("title", "citation", "quoted_span", "source_url", "retrieved_at",
                                            "level", "jurisdiction")} for i, r in S.items()},
        "addresses": [],
    }
    for aid in sorted(facts):
        f, res = facts[aid], resolution[aid]
        simple["addresses"].append({
            "id": aid, "street": f.street_address, "postal_city": f.postal_city, "state": f.state, "zip": f.zip,
            "resolved_city": res.get("city"), "year_built": f.year_built,
            "units": f.units_exact, "units_label": f.units_label(),
            "topics": topic_rows(lookups[aid], S, f),
        })

    cn = _census_names()
    places, report = [], {}
    for city in CITIES:
        p, splits = _place(rules, S, city.split(", ")[1], city, cn[city]["names"])
        places.append(p); report[city] = (splits, cn[city]["seen_in_geocoder"])
    for st in STATES:
        p, splits = _place(rules, S, st, None, [])
        p["state_name"] = STATES[st]
        places.append(p); report[st] = (splits, None)
    by_city = {"as_of": AS_OF, "places": places}

    used = {i for p in places for rows in p["results"].values() for t in rows for i in t["rule_ids"]}
    missing = used - set(simple["rules"])
    if missing:
        raise SystemExit(f"export: by_city uses rule ids missing from simple.json: {sorted(missing)}")

    paths = []
    for name, obj in (("simple.json", simple), ("by_city.json", by_city)):
        p = config.ROOT / "app" / name
        p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        (config.ROOT / "docs").mkdir(exist_ok=True)
        shutil.copyfile(p, config.ROOT / "docs" / name)
        paths.append(p)
    return simple, by_city, report, facts, resolution, paths
