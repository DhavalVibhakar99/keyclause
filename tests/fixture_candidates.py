"""TEST FIXTURE ONLY: hand-written candidate records used to exercise the pipeline
before an API key is available. Every quoted span is real text from the corpus, and
records still pass through the normal validators. Never used for a submission:
the test redirects all outputs to tests/out/.
"""
from __future__ import annotations
CANDIDATES = {
    "D024": [{
        "category": "rent_increase_limits", "jurisdiction": "CA", "title": "Tenant Protection Act rent cap",
        "requirement": "Annual rent increases are capped at 5 percent plus the change in the cost of living, or 10 percent, whichever is lower.",
        "key_value": "lesser of 5% + CPI or 10%", "citation": "Cal. Civ. Code § 1947.12",
        "quoted_span": "an owner of residential real property shall not, over the course of any 12-month period, increase the gross rental rate for a dwelling or a unit more than 5 percent plus the percentage change in the cost of living, or 10 percent, whichever is lower",
        "legal_form": "enacted_law", "bill_outcome": None, "enacted_date": None, "effective_date": None, "date_span": None,
        "coverage": {"rolling_new_construction_exempt_years": 15,
                     "coverage_span": "Housing that has been issued a certificate of occupancy within the previous 15 years",
                     "exemptions": [{"fact": "single_family_or_condo", "description": "single-family homes or condominiums owned by a natural person, with notice"}]},
        "yields_to_local_rule": True, "preempts_local": "no", "interaction": None, "open_question": None, "confidence": 0.9}],
    "D022": [{
        "category": "algorithmic_rent_setting", "jurisdiction": "CA", "title": "AB 325 common pricing algorithm ban",
        "requirement": "It is unlawful to use or distribute a common pricing algorithm as part of a conspiracy to restrain trade.",
        "key_value": None, "citation": "AB 325 (Cal. Bus. & Prof. Code § 16729)",
        "quoted_span": "It shall be unlawful for a person to use or distribute a common pricing algorithm as part of a contract, combination in the form of a trust, or conspiracy to restrain trade or commerce in violation of this chapter.",
        "legal_form": "enacted_law", "enacted_date": "2025-10-06", "effective_date": None,
        "date_span": "Approved by Governor October 06, 2025.",
        "coverage": {}, "yields_to_local_rule": False, "preempts_local": "no", "confidence": 0.85}],
    "D069": [{
        "category": "algorithmic_rent_setting", "jurisdiction": "NJ", "title": "FAIR Act",
        "requirement": "Prohibits coordinated algorithmic rent setting.",
        "key_value": None, "citation": "P.L. 2026, c.43 (C.56:9-20 to 56:9-26)",
        "quoted_span": "This act shall take effect on the first day of the twelfth month next following the date of enactment.",
        "legal_form": "enacted_law", "enacted_date": "2026-07-20", "effective_date": None, "date_span": "approved July 20, 2026",
        "effective_relative": {"kind": "first_day_of_nth_month_after_enactment", "n": 12,
                               "clause": "This act shall take effect on the first day of the twelfth month next following the date of enactment."},
        "coverage": {}, "yields_to_local_rule": False, "preempts_local": "possible", "confidence": 0.8}],
    "D046": [{
        "category": "algorithmic_rent_setting", "jurisdiction": "MA", "title": "S.2983 algorithmic rent setting ban (pending)",
        "requirement": "Would prohibit algorithmic rent setting.", "citation": "S.2983 (194th General Court)",
        "quoted_span": "An Act prohibiting algorithmic rent setting", "legal_form": "bill", "bill_outcome": "pending",
        "coverage": {}, "preempts_local": "no", "confidence": 0.8}],
    "D045": [{
        "category": "algorithmic_rent_setting", "jurisdiction": "MA", "title": "H.5222 algorithmic rent fixing (pending)",
        "requirement": "Would prevent algorithmic rent fixing in the rental housing market.", "citation": "H.5222 (194th General Court)",
        "quoted_span": "An Act relative to preventing algorithmic rent fixing in the rental housing market",
        "legal_form": "bill", "bill_outcome": "pending", "coverage": {}, "preempts_local": "no", "confidence": 0.8}],
    "D081": [{
        "category": "algorithmic_rent_setting", "jurisdiction": "San Francisco, CA", "title": "SF algorithmic device ban",
        "requirement": "Prohibits the sale or use of algorithmic devices to set rents or manage occupancy.",
        "citation": "S.F. Admin. Code § 37.10C",
        "quoted_span": "The law prohibits the sale or use of algorithmic devices to set rents or manage occupancy levels for residential units in San Francisco.",
        "legal_form": "official_guidance", "effective_date": "2024-10-14",
        "date_span": "Legislation adding Section 37.10C to the Rent Ordinance went into effect on October 14, 2024.",
        "coverage": {}, "preempts_local": "no", "confidence": 0.85}],
    "D080": [{
        "category": "rent_increase_limits", "jurisdiction": "San Francisco, CA", "title": "SF Rent Ordinance annual increase",
        "requirement": "For rent-controlled units the annual allowable increase is 1.6% from March 1, 2026 through February 28, 2027.",
        "key_value": "1.6%", "citation": "S.F. Admin. Code ch. 37",
        "quoted_span": "For rent-controlled units, the annual allowable increase amount effective March 1, 2026 through February 28, 2027 is 1.6%.",
        "legal_form": "official_guidance",
        "coverage": {"min_units": 2, "date_cutoff": {"basis": "certificate_of_occupancy", "covered_if": "on_or_before", "date": "1979-06-13"}},
        "preempts_local": "no", "confidence": 0.7}],
}
