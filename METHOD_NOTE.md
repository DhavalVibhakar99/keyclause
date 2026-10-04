# Method note: Rental Housing Law Navigator

**Not legal advice.** Spec: the v5 participant guide. The original Hack-Nation brief is superseded.

## The AI reads the law; code decides every answer

1. **Extraction (Claude, the only model step).**
   - Each corpus document is read once, through a fixed JSON schema that uses the brief's six category definitions.
   - Every quote must appear **word for word** in its document: the rule's quote, plus any date or coverage quote. If not, the record is dropped after one repair attempt.
   - The jurisdiction must match the manifest, and every number must appear in the source.
   - Every call is cached and logged (`out/audit_log.jsonl`).
2. **Assembly (code).**
   - Records merge by citation section. N.J.S.A. cites count as whole sections, and pages with no section attach to one law only.
   - Relocation-assistance and "scope of the act" pages are supporting detail only. They never supply a date or key value.
   - Status comes from dates and legal form, never from the model.
   - Conflicting dates become flagged open questions.
3. **Coverage (code, three-valued).**
   - **Placement:** the Census geocoder places each address, with a postal-place fallback.
   - **Facts:** units come from the assessor record or a parsed use code.
   - **Results:** each condition is yes, no or unknown. A missing fact gives "unknown", never a guess.
   - **Unit-capped exemptions:** they cannot apply above the cap; at or under it, or with units missing, the result is unknown.
   - **Tenancy-level carve-outs:** an owner sharing a kitchen or bath doesn't change the building result. It's shown as a caveat on the card.
   - **Summaries:** explanations and the plain-language summary come from fixed templates. No model is involved.
4. **Changes and self-check (code).**
   - **T1–T5 all pass:** T1 250 CA addresses; T2 40 Hoboken + 50 Jersey City; T3 140 NJ addresses, 90 flagged; T4 110 MA addresses pending; T5 empty.
   - **`python3 run.py check`:** 29 passed, 2 warnings, 0 failed.

## Sources and the citation metric

- **Corpus:** 93 documents, 77 with text.
- **Team captures:** link-only pages we captured with the organizers' approval are research sources outside the distributed corpus. Per the organizers' ruling, they are **excluded from the citation metric**.
- **Preference for the starter pack:** where a rule's group also has a starter-pack record, that record supplies the main quote and citation. The capture stays as a supporting source, and status, dates and coverage are unchanged.
- **Result:** 54 rules. 9 still rest on a team capture (MA-RENT-P1, HOB-ALG-01, HOB-RENT-01, JC-ALG-01, LA-DEP-01, NWK-RENT-01, NWK-SCR-01, SD-SCR-01, SNA-ALG-01), and their confidence is lowered by 0.1. **3,087 of 3,229 "applies" answers (95.6%) cite a starter-pack quote.**

## Team decisions, all verified and logged

- **`data/coverage_evidence.json`**
  - **What it adds:** coverage conditions, exemptions and unit caps that extraction missed.
  - **Evidence:** each entry rests on a quote that must appear word for word in a starter-pack document, or in the rule's own main source. If not, the build stops.
  - **In use:**
    - Building-age tests: SF (certificate of occupancy by June 13, 1979), Berkeley (June 1980), Hoboken and Newark (30-year new-construction exemption that depends on a filing)
    - Exemptions: the LAD and NJ deposit owner-occupied exemptions
    - The two-unit cap on California's owner-occupied duplex exemption
    - Jersey City's "exemptions exist but aren't listed" caveat (confidence 0.5)
- **`data/rule_overrides.json`**
  - **What it changes:** wording only (title, citation, plain-language text, key value). It can't touch dates, coverage, status or quotes.
  - **In use:** MA-EVIC-01 ("no just-cause law; notice rules only") and LA-EVIC-01's key value.

Both files are logged in the audit log and recorded in each rule's `validation_flags`.

## Gold set validation

We hand-labelled 30 rows from the law text, across the 9 cities. Each row is one rule at one address (`data/gold_set.csv`).
- **First run:** 21 rows matched.
- **Fixes:** each one encoded a coverage condition from quoted text. San Francisco and Berkeley age tests; Hoboken and Newark new-construction tests; shared-kitchen carve-outs treated as caveats.
- **Now: 28/30 match.** The two remaining differences are known:

| Address | Rule | Label | System | Why |
|---|---|---|---|---|
| A0008, A0108 | JC-RENT-01 | unknown | applies, with caveat | Source D036 says exemptions exist but doesn't list them. We report "applies" at confidence 0.5 with that caveat; the labeller chose "unknown". |

## Known limits

- **Many unknowns come from facts that aren't public:** year built is missing for every Berkeley and San Diego address and for most in Hoboken and Newark. Owner facts are never available, and Newark has no unit data.
- **Rules with no stated effective date** are treated as in force.
- **Four rules rest on secondary pages:** MA-RENT-P1, JC-ALG-01, LA-DEP-01, SNA-ALG-01.
