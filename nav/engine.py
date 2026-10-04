"""Module B, step 2: decide which rules cover each address. Pure code, no model.

Three-valued logic: every condition is True, False or None (unknown). A rule
applies only if every condition is True and no exemption can apply; it is
"unknown" when a needed fact is missing. Explanations are built from the actual
facts and rule fields, so they cannot contain claims the data does not support.
"""
from __future__ import annotations
import re
from datetime import date

from . import config
from .dates import status_at


class Unknown(str):
    """An unknown-coverage message that also carries a reason code (for the app's plain summary)."""
    code = "other"


SHARED_FACILITY_RE = re.compile(r"\bshar(?:e|es|ing)\b.{0,60}\b(?:kitchen|bath)", re.I)
BUILDING_TYPE_RE = re.compile(r"duplex|single[- ]family|two[- ]family|three[- ]family|\bunits?\b|bedroom|\bADU", re.I)


def is_tenancy_carveout(e):
    """An exemption that turns on one tenancy, not the building: the owner shares a kitchen or bath with
    the tenant. It never changes the building-level result; the card shows it as a caveat. Composite
    exemptions that also name a building type (duplex, single-family, unit counts) are left alone."""
    if e.get("fact") == "tenancy_specific":
        return True
    d = e.get("description") or ""
    return (e.get("fact") == "owner_occupied" and e.get("applies_only_if_units_at_most") is None
            and bool(SHARED_FACILITY_RE.search(d)) and not BUILDING_TYPE_RE.search(d))


def U(code, msg):
    u = Unknown(msg)
    u.code = code
    return u


def AND(values):
    if any(v is False for v in values):
        return False
    if any(v is None for v in values):
        return None
    return True


def _cutoff_interval(cov_date, mode):
    from .dates import parse
    p = parse(cov_date)
    if not p:
        return None
    start, end = p
    # partial dates: pick the boundary that keeps the comparison conservative
    return end if mode in ("on_or_before", "after") else start


def _date_test(year_built, cutoff, mode):
    """Building completed sometime in `year_built`; compare with cutoff. True/False/None."""
    if year_built is None:
        return None
    lo, hi = date(year_built, 1, 1), date(year_built, 12, 31)
    if mode == "on_or_before":
        return True if hi <= cutoff else (False if lo > cutoff else None)
    if mode == "before":
        return True if hi < cutoff else (False if lo >= cutoff else None)
    if mode == "after":
        return True if lo > cutoff else (False if hi <= cutoff else None)
    if mode == "on_or_after":
        return True if lo >= cutoff else (False if hi < cutoff else None)
    return None


BASIS_LABEL = {
    "certificate_of_occupancy": "certificate of occupancy",
    "year_built": "year built",
    "construction_completed": "construction completion",
    "first_occupied": "first occupancy",
    "other": "date",
}


def evaluate_coverage(rule, f, as_of):
    """Return (value, reasons, unknowns, exemption_notes)."""
    cov = rule.get("coverage") or {}
    vals, reasons, unknowns, notes = [], [], [], []
    ulabel = f.units_label()
    usrc = "" if f.units_source in ("units column", "none") else f" (from {f.units_source})"

    mn = cov.get("min_units")
    if mn:
        if f.units_min is not None and f.units_min >= mn:
            vals.append(True); reasons.append(f"{ulabel}{usrc}, meets the {mn}+ unit threshold")
        elif f.units_max is not None and f.units_max < mn:
            vals.append(False); reasons.append(f"{ulabel}, below the {mn}-unit threshold")
        else:
            vals.append(None); unknowns.append(U("units", f"coverage needs at least {mn} units; {ulabel}"))
    mx = cov.get("max_units")
    if mx:
        if f.units_max is not None and f.units_max <= mx:
            vals.append(True); reasons.append(f"{ulabel}, within the {mx}-unit limit")
        elif f.units_min is not None and f.units_min > mx:
            vals.append(False); reasons.append(f"{ulabel}, above the {mx}-unit limit")
        else:
            vals.append(None); unknowns.append(U("units", f"coverage limited to {mx} units or fewer; {ulabel}"))

    dc = cov.get("date_cutoff")
    if dc and dc.get("date"):
        mode = dc.get("covered_if", "on_or_before")
        cutoff = _cutoff_interval(dc["date"], mode)
        basis = BASIS_LABEL.get(dc.get("basis"), "date")
        words = mode.replace("_", " ")
        if cutoff:
            v = _date_test(f.year_built, cutoff, mode)
            vals.append(v)
            if f.year_built is None:
                unknowns.append(U("year_built", f"coverage depends on {basis} {words} {cutoff.isoformat()}; year built is not in the data"))
            elif v is None:
                unknowns.append(U("year_exact", f"built {f.year_built}, the same year as the {basis} cutoff ({words} {cutoff.isoformat()}); "
                                  "the exact date is not in the data"))
            elif v:
                reasons.append(f"built {f.year_built}, {words} the {basis} cutoff of {cutoff.isoformat()}")
            else:
                reasons.append(f"built {f.year_built}, outside the {basis} cutoff ({words} {cutoff.isoformat()})")

    roll = cov.get("rolling_new_construction_exempt_years")
    if roll:
        q = date.fromisoformat(as_of)
        try:
            cutoff = q.replace(year=q.year - roll)
        except ValueError:       # Feb 29
            cutoff = q.replace(year=q.year - roll, day=28)
        v = _date_test(f.year_built, cutoff, "on_or_before")
        vals.append(v)
        if f.year_built is None:
            unknowns.append(U("year_built", f"buildings newer than {roll} years are exempt; year built is not in the data"))
        elif v is None:
            unknowns.append(U("year_exact", f"built {f.year_built}; may fall within the {roll}-year new-construction exemption"))
        elif v:
            reasons.append(f"built {f.year_built}, older than the {roll}-year new-construction exemption")
        else:
            reasons.append(f"built {f.year_built}, within the {roll}-year new-construction exemption")

    for s in cov.get("scope_limited_to") or []:
        if s.get("fact") == "subsidized_or_affordable_housing" and f.subsidized:
            vals.append(True); reasons.append(f"assessor record indicates subsidized/affordable housing ('{f.use_description}')")
        else:
            vals.append(None); unknowns.append(U("scope", f"rule covers only: {s.get('description')}; not determinable from the data"))

    base = AND(vals) if vals else True
    if not vals:
        reasons.append("no building-level conditions in the rule")

    # exemptions
    ex_vals = []
    desc_upper = f.use_description.upper()
    multi = (f.units_min or 0) >= 2 or any(k in desc_upper for k in ("APT", "APARTMENT", "UNIT", "FLATS")) \
        or f.state == "NJ"   # NJ class 4C = apartment property
    for e in cov.get("exemptions") or []:
        fact, cap, d = e.get("fact"), e.get("applies_only_if_units_at_most"), e.get("description", "")
        if is_tenancy_carveout(e):
            notes.append(f"caveat, tenancy-level carve-out (does not change the building result): {d}")
            continue
        if fact == "single_family_or_condo":
            if "TIC" in desc_upper or "CONDO" in desc_upper:
                ex_vals.append(None); unknowns.append(U("exemption_use", f"exemption may apply ({d}); record is '{f.use_description}'"))
            elif multi:
                ex_vals.append(False); notes.append(f"exemption for {d} cannot apply to a multifamily apartment property")
            else:
                ex_vals.append(None); unknowns.append(U("exemption_use", f"exemption may apply ({d})"))
        elif fact in ("owner_occupied", "owner_is_natural_person", "owner_type"):
            if cap is not None and f.units_min is not None and f.units_min > cap:
                ex_vals.append(False); notes.append(f"exemption for {d} is limited to {cap} units or fewer; building has {ulabel}")
            else:
                ex_vals.append(None)
                unknowns.append(U("owner", f"exemption depends on owner facts not in the data ({d})"))
        elif fact == "new_construction" and e.get("exempt_years"):
            # time-limited new-construction exemption that also depends on a filing not in the data
            yrs, after = e["exempt_years"], e.get("constructed_after")
            first_year = date.fromisoformat(as_of).year - yrs
            if f.year_built is None:
                ex_vals.append(None)
                unknowns.append(U("year_built", f"new buildings are exempt for up to {yrs} years ({d}); year built is not in the data"))
            elif f.year_built < first_year or (after and f.year_built < int(after[:4])):
                ex_vals.append(False)
                notes.append(f"built {f.year_built}, outside the {yrs}-year new-construction exemption")
            else:
                ex_vals.append(None)
                unknowns.append(U("filing", f"built {f.year_built}, within the {yrs}-year new-construction exemption, "
                                            "which depends on a filing that is not in the data"))
        elif fact == "subsidized_or_affordable":
            if f.subsidized:
                ex_vals.append(None); unknowns.append(U("subsidized", f"record indicates subsidized housing; exemption may apply ({d})"))
            else:
                notes.append(f"not evaluated: {d}")
        else:
            notes.append(f"not evaluated (tenancy- or program-specific): {d}")

    if any(v is True for v in ex_vals):
        value = False
    elif base is True and any(v is None for v in ex_vals):
        value = None
    else:
        value = base
    return value, reasons, unknowns, notes


def _applicable(rules, res):
    out = []
    for r in rules:
        if r.get("rule_effect") == "restricts_local_government":
            continue      # e.g. a state ban on local rent control is context, not a rule at the address
        if r["level"] == "state" and r["jurisdiction"] == res["state"]:
            out.append(r)
        elif r["level"] == "city" and res.get("city") and r["jurisdiction"] == res["city"]:
            out.append(r)
    return out


def _explain(rule, f, res, value, reasons, unknowns, notes, status, as_of):
    where = (f"State rule ({rule['jurisdiction']})." if rule["level"] == "state"
             else f"{rule['jurisdiction']} rule; address resolved to {res['city']} via {res['method']}.")
    if rule["level"] == "city" and res.get("note"):
        where += " " + res["note"]
    parts = [where]
    if status == "pending":
        parts.append(f"Pending {'ballot measure' if rule.get('legal_form') == 'ballot_measure' else 'bill'}, not law as of {as_of}; "
                     "listed because it would cover this address if enacted.")
    elif status == "not_yet_effective":
        parts.append(f"Enacted but takes effect {rule.get('effective_date_resolved')} ({rule.get('effective_date_basis')}); not in force on {as_of}.")
    if reasons:
        parts.append("Covered: " + "; ".join(reasons) + ".")
    if unknowns:
        parts.append("Unknown: " + "; ".join(unknowns) + ".")
    if notes:
        parts.append("Notes: " + "; ".join(notes) + ".")
    return " ".join(parts)


def lookup_address(rules, f, res, as_of):
    rows = []
    for r in _applicable(rules, res):
        status = status_at(r, as_of)
        if status == "failed":
            continue
        value, reasons, unknowns, notes = evaluate_coverage(r, f, as_of)
        if value is False:
            continue
        if status == "ambiguous_date":
            result = "unknown"
            unknowns = unknowns + [U("date_imprecise", f"the published effective date ({r.get('effective_date_resolved')}) is not precise enough to decide {as_of}")]
        elif status == "pending":
            result = "pending"
        elif status == "not_yet_effective":
            result = "not_yet_effective"
        else:
            result = "applies" if value is True else "unknown"
        rows.append({"rule": r, "result": result, "reasons": reasons, "unknowns": unknowns,
                     "notes": notes, "status": status})

    # supersession: a state rule that yields to an applicable local rule of the same category
    by_id = {x["rule"]["team_rule_id"]: x for x in rows}
    for x in rows:
        r = x["rule"]
        if r["level"] != "state" or not r.get("overrides") or x["result"] not in ("applies", "unknown"):
            continue
        local = [by_id[i] for i in r["overrides"] if i in by_id and by_id[i]["status"] == "in_force"]
        if any(l["result"] == "applies" for l in local):
            ids = ", ".join(l["rule"]["team_rule_id"] for l in local if l["result"] == "applies")
            x["result"] = "superseded"
            x["notes"] = x["notes"] + [f"the stricter local rule {ids} governs this address"]
        elif any(l["result"] == "unknown" for l in local) and x["result"] == "applies":
            x["result"] = "unknown"
            ids = ", ".join(l["rule"]["team_rule_id"] for l in local)
            x["unknowns"] = x["unknowns"] + [U("local_rule", f"if local rule {ids} covers this building, it governs instead")]

    # conflict flags: rule-level open questions, and state/local preemption questions
    cats_here = {(x["rule"]["category"], x["rule"]["level"]) for x in rows}
    out = []
    for x in rows:
        r = x["rule"]
        # conflict = sources disagree on the effective date, or state/local preemption at this address
        reasons_flag = [x for x in [r.get("date_conflict_note")] if x]
        flag = bool(reasons_flag)
        if r["level"] == "state" and r.get("preempts_local") in ("yes", "possible") and (r["category"], "city") in cats_here:
            flag = True
            reasons_flag.append("may preempt the local rule here; needs human review")
        if r["level"] == "city":
            preempting = [y["rule"]["team_rule_id"] for y in rows if y["rule"]["level"] == "state"
                          and y["rule"]["category"] == r["category"] and y["rule"].get("preempts_local") in ("yes", "possible")]
            if preempting:
                flag = True
                reasons_flag.append(f"state rule {', '.join(preempting)} may preempt this local rule; needs human review")
        expl = _explain(r, f, res, None, x["reasons"], x["unknowns"], x["notes"], x["status"], as_of)
        if res.get("flags"):
            expl += " Jurisdiction note: " + " ".join(res["flags"])
        if r.get("source_note"):
            expl += " Source note: " + r["source_note"]
        if reasons_flag:
            expl += " Review flag: " + " ".join(reasons_flag)
        out.append({
            "team_rule_id": r["team_rule_id"],
            "result": x["result"],
            "explanation": expl,
            "conflict_flag": flag,
            "confidence": _confidence(r, x),
            # app-only (stripped from lookups.json): why the answer is unknown, as reason codes
            "unknown_codes": sorted({getattr(u, "code", "other") for u in x["unknowns"]}) if x["result"] == "unknown" else [],
        })
    out.sort(key=lambda o: o["team_rule_id"])
    return out


def _confidence(rule, x):
    """Simple, explainable indicator: extraction confidence, reduced when facts are inferred or unknown."""
    c = float(rule.get("confidence") or 0.5)
    if x["unknowns"]:
        c = min(c, 0.5)
    if rule.get("source_origin") == "team_capture":
        c -= 0.1
    return round(max(0.05, min(1.0, c)), 2)


def run_lookups(rules, facts, resolution, as_of=config.DEFAULT_AS_OF):
    return {aid: lookup_address(rules, facts[aid], resolution[aid], as_of) for aid in sorted(facts)}
