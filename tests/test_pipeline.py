"""End-to-end pipeline test on the fixture (no API key needed).

    python3 -m tests.test_pipeline

Outputs go to tests/out/ so real results in out/ are never touched.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

from nav import config

TEST_OUT = Path(__file__).resolve().parent / "out"
config.OUT_DIR = TEST_OUT
config.EXTRACTED = TEST_OUT / "extracted_raw.json"
config.AUDIT_LOG = TEST_OUT / "audit_log.jsonl"
config.GOLD_SET = TEST_OUT / "no_gold.csv"
config.APP_DATA = TEST_OUT / "data.js"

from nav import extract                     # noqa: E402
from nav.corpus import load_corpus          # noqa: E402
from nav.spans import SpanIndex             # noqa: E402
from tests.fixture_candidates import CANDIDATES  # noqa: E402


def main():
    TEST_OUT.mkdir(exist_ok=True)
    corpus = load_corpus()
    extracted = {}
    for did, recs in CANDIDATES.items():
        doc = corpus[did]
        idx = SpanIndex(doc.body)
        problems, kept = [], []
        for rec in recs:
            r = extract.validate_record(doc, idx, dict(rec), problems)
            if r:
                kept.append(r)
        assert not [p for p in problems if "dropped" in p], problems
        extracted[did] = {"doc_id": did, "rules": kept, "problems": problems}
    config.EXTRACTED.write_text(json.dumps(extracted, indent=1))

    import run
    rules, facts, resolution, cache = run._build()
    by_id = {r["team_rule_id"]: r for r in rules}
    print("rule ids:", sorted(by_id))
    print("NJ-ALG-01 effective:", by_id["NJ-ALG-01"]["effective_date_resolved"], "|", by_id["NJ-ALG-01"]["effective_date_basis"])
    print("CA-ALG-01 effective:", by_id["CA-ALG-01"]["effective_date_resolved"], "|", by_id["CA-ALG-01"]["effective_date_basis"])
    assert by_id["NJ-ALG-01"]["effective_date_resolved"] == "2027-07-01"
    assert by_id["CA-ALG-01"]["effective_date_resolved"] == "2026-01-01"

    from nav.selfcheck import check
    fails = check(facts, resolution, cache).print()
    # sample explanations
    L = cache[config.DEFAULT_AS_OF]
    for aid in ("A0001", "A0004"):
        print(f"\n{aid}:", json.dumps(L[aid], indent=1)[:1500])
    sf = [a for a in facts if resolution[a]["city"] == "San Francisco, CA"]
    print("\nSF sample:", json.dumps(L[sf[0]], indent=1)[:1800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
