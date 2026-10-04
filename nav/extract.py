"""Module A: read each corpus document and extract structured rule candidates.

The model only READS. Everything it returns is checked by code before it is kept:
  1. quoted_span must occur verbatim in the document (else one repair attempt, then drop);
  2. the date and coverage quotes, when given, must also occur verbatim;
  3. jurisdiction must be one the manifest assigns to the document;
  4. numbers in key_value / requirement must appear in the document (else confidence is lowered);
  5. status is NOT taken from the model: it is computed later from dates and legal form.
"""
from __future__ import annotations
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import audit, config
from .corpus import load_corpus, retrieval_date
from .llm import LLMError, call_tool
from .spans import SpanIndex, numbers_supported

FACT_ENUM_SCOPE = ["subsidized_or_affordable_housing", "owner_type", "other"]
FACT_ENUM_EXEMPT = [
    "owner_occupied", "owner_is_natural_person", "single_family_or_condo",
    "subsidized_or_affordable", "new_construction", "tenancy_specific",
    "special_housing_type", "other",
]
DATE_RE = "^\\d{4}(-\\d{2}(-\\d{2})?)?$"

RULE_ITEM = {
    "type": "object",
    "required": ["category", "jurisdiction", "title", "requirement", "citation", "quoted_span",
                 "legal_form", "coverage", "confidence"],
    "properties": {
        "category": {"type": "string", "enum": config.CATEGORIES},
        "jurisdiction": {"type": "string", "description": "Exactly one of the jurisdictions listed for this document."},
        "title": {"type": "string", "description": "Short name of the law or provision."},
        "requirement": {"type": "string", "description": "One or two plain-language sentences stating what the rule requires. Use only facts stated in the document."},
        "key_value": {"type": ["string", "null"], "description": "Headline number or formula exactly as the document gives it, e.g. 'one month's rent', 'lesser of 5% plus CPI or 10%'. Null if none."},
        "citation": {"type": "string", "description": "Official citation as written in the document (code section, ordinance number, bill number). If the document gives none, describe the instrument plainly, e.g. 'Jersey City Municipal Code ch. 260 (as described on city page)'."},
        "quoted_span": {"type": "string", "description": "20-400 characters copied EXACTLY, character for character, from the document, that supports the requirement. Do not paraphrase, fix typos, or join separate passages."},
        "legal_form": {"type": "string", "enum": ["enacted_law", "bill", "ballot_measure", "official_guidance"],
                       "description": "enacted_law: statute/ordinance text or official notice of an adopted law. bill: a legislative bill not yet law. ballot_measure: an initiative/ballot question. official_guidance: an agency page summarizing a law already in force."},
        "bill_outcome": {"type": ["string", "null"], "enum": ["pending", "failed", "enacted", None],
                         "description": "For bills and ballot measures only: pending, failed (died, vetoed, struck), or enacted. Null for enacted_law/official_guidance."},
        "enacted_date": {"type": ["string", "null"], "pattern": DATE_RE},
        "effective_date": {"type": ["string", "null"], "pattern": DATE_RE,
                           "description": "Date the requirement takes (or took) effect, ONLY if the document states it as a calendar date. Null if not stated or only stated relative to enactment."},
        "effective_relative": {"type": ["object", "null"],
                               "description": "If the document states the effective date RELATIVE to enactment (e.g. 'first day of the twelfth month next following the date of enactment' or 'the 90th day after enactment'), describe it here instead of computing it.",
                               "properties": {
                                   "kind": {"type": "string", "enum": ["first_day_of_nth_month_after_enactment", "nth_day_after_enactment", "other"]},
                                   "n": {"type": ["integer", "null"]},
                                   "clause": {"type": "string", "description": "Exact quote of the relative clause."}},
                               "required": ["kind", "clause"]},
        "date_span": {"type": ["string", "null"], "description": "Exact quote from the document stating the effective or enactment date. Null if no date given."},
        "coverage": {
            "type": "object",
            "description": "Which buildings the rule covers, as stated in the document. Use null for anything the document does not state. Do not import outside knowledge.",
            "properties": {
                "summary": {"type": ["string", "null"], "description": "Plain-language description of coverage."},
                "min_units": {"type": ["integer", "null"], "description": "Covers only buildings with at least this many units."},
                "max_units": {"type": ["integer", "null"], "description": "Covers only buildings with at most this many units."},
                "date_cutoff": {
                    "type": ["object", "null"],
                    "properties": {
                        "basis": {"type": "string", "enum": ["certificate_of_occupancy", "year_built", "construction_completed", "first_occupied", "other"]},
                        "covered_if": {"type": "string", "enum": ["on_or_before", "before", "after", "on_or_after"]},
                        "date": {"type": "string", "pattern": DATE_RE},
                    },
                    "required": ["basis", "covered_if", "date"],
                },
                "rolling_new_construction_exempt_years": {"type": ["integer", "null"], "description": "Exempts units whose certificate of occupancy was issued within the last N years (rolling)."},
                "scope_limited_to": {
                    "type": "array",
                    "description": "Facts the rule REQUIRES for coverage at all (e.g. 'applies only to affordable housing').",
                    "items": {"type": "object", "required": ["fact", "description"],
                              "properties": {"fact": {"type": "string", "enum": FACT_ENUM_SCOPE},
                                             "description": {"type": "string"}}},
                },
                "exemptions": {
                    "type": "array",
                    "items": {"type": "object", "required": ["fact", "description"],
                              "properties": {
                                  "fact": {"type": "string", "enum": FACT_ENUM_EXEMPT},
                                  "description": {"type": "string"},
                                  "applies_only_if_units_at_most": {"type": ["integer", "null"], "description": "If the exemption is limited to buildings with at most N units, give N."}}},
                },
                "coverage_span": {"type": ["string", "null"], "description": "Exact quote supporting the coverage conditions, if any are stated."},
            },
        },
        "yields_to_local_rule": {"type": "boolean", "description": "True if this (state) rule says it does not apply where a local ordinance with a stricter limit applies."},
        "preempts_local": {"type": "string", "enum": ["yes", "possible", "no"], "description": "Whether this rule states it preempts or supersedes local ordinances on the same subject."},
        "interaction": {"type": ["string", "null"], "description": "How this rule relates to other levels of law, only as stated in the document."},
        "open_question": {"type": ["string", "null"], "description": "Any ambiguity or conflicting information in the document (e.g. two different effective dates)."},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

EXTRACT_TOOL = {
    "name": "record_rules",
    "description": "Record every housing rule the document establishes or describes, in the six categories.",
    "input_schema": {
        "type": "object",
        "required": ["rules"],
        "properties": {
            "rules": {"type": "array", "items": RULE_ITEM},
            "document_notes": {"type": ["string", "null"], "description": "Anything notable about the document (e.g. it contains no rules in the six categories)."},
        },
    },
}

SYSTEM = """You extract rental-housing rules from official legal documents into structured records for a transparency tool. Accuracy matters more than coverage: a missing rule is better than an invented one.

Categories (use exactly these):
- rent_increase_limits: caps or formulas limiting rent increases (rent control, rent stabilization, statewide caps).
- just_cause_eviction: allowed causes, notice requirements, relocation assistance, coverage.
- security_deposits: caps or rules on security deposit amounts.
- application_screening_fees: fee caps, allowed upfront charges, receipts and refunds, broker fees.
- screening_restrictions: limits on criminal-history and income-source screening, including source-of-income discrimination.
- algorithmic_rent_setting: bans or limits on algorithmic or coordinated rent-pricing tools.

Hard rules:
1. Use ONLY the document text. Never add facts, dates, numbers or citations from memory.
2. quoted_span, date_span and coverage_span must be copied character-for-character from the document. They are checked automatically and rejected if not found.
3. One record per distinct legal requirement and category. Do not split one rule into many records; do not merge two different laws (e.g. two different bills) into one.
4. Skip topics outside the six categories (registration, habitability, etc.). Notices and relocation assistance count only when tied to an eviction (just_cause_eviction).
5. Bills that are not law are legal_form "bill" with bill_outcome "pending" or "failed" as the document shows. Never present a bill as law. If the page is only a bill status/history page without the bill text, still record ONE record for the bill in the category its title indicates: quoted_span = the bill title copied exactly, requirement = what the title says the bill would do, coverage left empty, confidence 0.4 or lower.
6. If the document states no effective date, leave effective_date null rather than guessing. If it states the effective date relative to enactment, fill effective_relative (do not compute the date yourself) and give the enactment date in enacted_date with its quote in date_span. Always give enacted_date when the document states an approval/adoption/enactment date.
7. Record ambiguities or conflicting statements in open_question.
8. If the document contains no rule in the six categories, return an empty list and explain in document_notes.
9. coverage.exemptions are only categories of BUILDINGS, UNITS or TENANCIES that the rule does not cover. Definitions that limit which PERSONS or ACTORS count (e.g. "a government entity is not a coordinator") are not exemptions; mention them in coverage.summary instead.
10. preempts_local is "yes" only if the text expressly says it preempts or supersedes local ordinances; "possible" if the text is silent but a local ordinance on the same subject could conflict; otherwise "no".
11. If a city or agency page merely restates a STATE law (e.g. explains a Civil Code section), do not record it as a city rule: skip it. Record only rules the listed jurisdiction itself enacted or enforces as its own law.
12. If a rule applies only when a specific event occurs (e.g. a condominium conversion, a sale, a substantial rehabilitation, a specific program), add that event to coverage.scope_limited_to with fact "other".
13. A provision that limits what a landlord may charge before or at the start of a tenancy (e.g. first month's rent, last month's rent, security deposit, key and lock) is an application_screening_fees rule (allowed upfront charges). Record it in that category, in addition to any security_deposits record for the deposit itself."""


def _user_prompt(doc):
    juris = "; ".join(doc.jurisdictions)
    return (
        f"Document ID: {doc.doc_id}\n"
        f"Jurisdiction(s) assigned by the manifest: {juris}\n"
        f"Source URL: {doc.url}\n"
        f"Source type: {doc.source_type}\n"
        f"Retrieved: {doc.retrieved_at}\n\n"
        f"<document>\n{doc.body}\n</document>\n\n"
        f"Extract the rules. For jurisdiction use exactly: {juris}."
    )


REPAIR_TOOL = {
    "name": "verbatim_quote",
    "description": "Return an exact quote from the document.",
    "input_schema": {
        "type": "object",
        "required": ["quote"],
        "properties": {"quote": {"type": ["string", "null"], "description": "20-400 characters copied exactly from the document, or null if the document does not support the statement."}},
    },
}


def _repair_span(doc, statement, field):
    user = (f"<document>\n{doc.body}\n</document>\n\n"
            f"Copy, character for character, a passage of 20-400 characters from the document that supports this "
            f"{field}: \"{statement}\". If the document does not support it, return null.")
    out = call_tool("You copy exact quotes from documents. Never paraphrase.", user, REPAIR_TOOL,
                    label=f"repair:{doc.doc_id}:{field}", max_tokens=8000)
    return out.get("quote")


def validate_record(doc, idx, rec, problems):
    """Return a cleaned record or None. Appends human-readable problems."""
    rid = f"{doc.doc_id}:{rec.get('category')}:{rec.get('title', '')[:40]}"
    if rec.get("jurisdiction") not in doc.jurisdictions:
        problems.append(f"{rid}: jurisdiction '{rec.get('jurisdiction')}' not assigned to document; dropped")
        return None

    # 1. main quote
    span = idx.verify(rec.get("quoted_span", ""))
    if span is None or len(span) < 20:
        try:
            fixed = _repair_span(doc, rec.get("requirement", ""), "requirement")
        except LLMError as e:
            fixed = None
            problems.append(f"{rid}: repair failed: {e}")
        span = idx.verify(fixed) if fixed else None
        if span is None or len(span) < 20:
            problems.append(f"{rid}: quoted_span not found verbatim (even after repair); dropped")
            audit.log("span_rejected", doc_id=doc.doc_id, record=rid, span=rec.get("quoted_span", "")[:300])
            return None
        problems.append(f"{rid}: quoted_span repaired")
    rec["quoted_span"] = span

    conf = float(rec.get("confidence") or 0.5)
    flags = []

    # 2. date quote supports the date
    if rec.get("effective_date") or rec.get("enacted_date"):
        ds = rec.get("date_span")
        verified = idx.verify(ds) if ds else None
        if verified is None:
            flags.append("date not supported by a verbatim quote; date removed")
            rec["effective_date"] = None
            rec["enacted_date"] = None
            rec["date_span"] = None
        else:
            rec["date_span"] = verified

    rel = rec.get("effective_relative")
    if rel:
        v = idx.verify(rel.get("clause", ""))
        if v is None:
            flags.append("relative effective-date clause not found verbatim; removed")
            rec["effective_relative"] = None
        else:
            rel["clause"] = v

    # coverage quote
    cov = rec.get("coverage") or {}
    has_conditions = any(cov.get(k) for k in ("min_units", "max_units", "date_cutoff",
                                               "rolling_new_construction_exempt_years", "scope_limited_to"))
    if cov.get("coverage_span"):
        v = idx.verify(cov["coverage_span"])
        cov["coverage_span"] = v
        if v is None:
            flags.append("coverage quote not found verbatim")
            conf -= 0.15
    elif has_conditions:
        flags.append("coverage conditions have no supporting quote")
        conf -= 0.1

    # 4. numbers in model-written fields
    missing = numbers_supported((rec.get("key_value") or "") + " " + (rec.get("requirement") or ""), idx)
    if missing:
        flags.append(f"numbers not found in source text: {', '.join(missing)}")
        conf -= 0.2

    rec["coverage"] = cov
    rec["confidence"] = round(max(0.05, min(1.0, conf)), 2)
    rec["validation_flags"] = flags
    rec["source_doc_id"] = doc.doc_id
    rec["source_url"] = doc.url
    rec["retrieved_at"] = retrieval_date(doc)
    rec["source_type"] = doc.source_type
    rec["source_origin"] = doc.origin
    return rec


def extract_doc(doc):
    idx = SpanIndex(doc.body)
    out = call_tool(SYSTEM, _user_prompt(doc), EXTRACT_TOOL, label=f"extract:{doc.doc_id}")
    problems, kept = [], []
    for rec in out.get("rules", []):
        r = validate_record(doc, idx, dict(rec), problems)
        if r:
            kept.append(r)
    audit.log("extracted", doc_id=doc.doc_id, proposed=len(out.get("rules", [])), kept=len(kept),
              problems=problems, notes=out.get("document_notes"))
    return {"doc_id": doc.doc_id, "rules": kept, "problems": problems, "notes": out.get("document_notes")}


def run(doc_ids=None, workers=4):
    corpus = load_corpus()
    targets = [d for d in corpus.values() if d.has_text and (not doc_ids or d.doc_id in doc_ids)]
    results = {}
    if config.EXTRACTED.exists() and doc_ids:
        results = json.loads(config.EXTRACTED.read_text(encoding="utf-8"))
    failures = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(extract_doc, d): d.doc_id for d in targets}
        for f in as_completed(futs):
            did = futs[f]
            try:
                results[did] = f.result()
                r = results[did]
                print(f"{did}: kept {len(r['rules'])} rule(s)" + (f", {len(r['problems'])} note(s)" if r["problems"] else ""), flush=True)
            except Exception as e:   # one bad document must not lose the whole run
                failures[did] = f"{type(e).__name__}: {e}"
                print(f"{did}: FAILED {failures[did]}", flush=True)
    from .classify import classify_records
    classify_records([r for d in results.values() for r in d.get("rules", [])])
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    config.EXTRACTED.write_text(json.dumps(dict(sorted(results.items())), indent=1, ensure_ascii=False), encoding="utf-8")
    if failures:
        audit.log("extract_failures", failures=failures)
    return results, failures
