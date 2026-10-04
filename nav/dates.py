"""Effective dates and status, computed by code (never by the model).

Precedence for a rule's effective date:
  1. a calendar date the document states (with a verified quote);
  2. a date computed from a quoted relative clause + the quoted enactment date
     (e.g. NJ FAIR Act: "first day of the twelfth month next following the date of enactment");
  3. a state's constitutional default for statutes that state no date
     (California: January 1 after enactment, Cal. Const. art. IV, sec. 8(c)(1)),
     only for enacted state statutes whose enactment date is quoted;
  4. otherwise unknown -> treated as already in force if the law is enacted, with a flag.
Every derived date records how it was derived, so explanations can show it.
"""
from __future__ import annotations
import calendar
from datetime import date

DEFAULT_RULES = {
    "CA": {
        "citation": "Cal. Const. art. IV, sec. 8(c)(1)",
        "description": "A California statute enacted in a regular session takes effect on January 1 of the year after enactment unless it states otherwise.",
    },
}


def parse(d):
    """'2026', '2026-03', '2026-03-01' -> (start_date, end_date) of the period, or None."""
    if not d:
        return None
    parts = [int(p) for p in str(d).split("-")]
    if len(parts) == 1:
        return date(parts[0], 1, 1), date(parts[0], 12, 31)
    if len(parts) == 2:
        y, m = parts
        return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    return date(*parts), date(*parts)


def iso(d):
    return d.isoformat() if d else None


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def derive_effective(rule):
    """Return (effective_date_iso_or_None, basis_text)."""
    if rule.get("effective_date"):
        return rule["effective_date"], "stated in source"
    enacted = parse(rule.get("enacted_date"))
    rel = rule.get("effective_relative")
    if rel and enacted and enacted[0] == enacted[1]:
        n = rel.get("n")
        if rel.get("kind") == "first_day_of_nth_month_after_enactment" and n:
            eff = add_months(enacted[0], n)
            return iso(eff), f"computed: first day of month {n} after enactment ({iso(enacted[0])}), per quoted clause"
        if rel.get("kind") == "nth_day_after_enactment" and n:
            from datetime import timedelta
            eff = enacted[0] + timedelta(days=n)
            return iso(eff), f"computed: day {n} after enactment ({iso(enacted[0])}), per quoted clause"
    if (rule.get("level") == "state" and rule.get("jurisdiction") in DEFAULT_RULES
            and rule.get("legal_form") == "enacted_law" and enacted and enacted[0] == enacted[1]
            and not rel):
        dr = DEFAULT_RULES[rule["jurisdiction"]]
        eff = date(enacted[0].year + 1, 1, 1)
        return iso(eff), f"default rule: {dr['description']} ({dr['citation']}); enacted {iso(enacted[0])}"
    return None, "no effective date in source"


def status_at(rule, as_of):
    """in_force | not_yet_effective | pending | failed | ambiguous_date, as of `as_of` (ISO)."""
    form = rule.get("legal_form")
    outcome = rule.get("bill_outcome")
    if form in ("bill", "ballot_measure"):
        if outcome == "failed":
            return "failed"
        if outcome != "enacted":
            return "pending"
    q = date.fromisoformat(as_of)
    eff = parse(rule.get("effective_date_resolved"))
    if eff is None:
        return "in_force"
    start, end = eff
    if q < start:
        return "not_yet_effective"
    if q > end or start == end:
        return "in_force"
    return "ambiguous_date"  # query date falls inside a partial effective date (e.g. '2026-01')
