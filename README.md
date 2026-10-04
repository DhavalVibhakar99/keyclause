# Rental Housing Law Navigator

**Not legal advice.** This is a transparency prototype built from public sources. Every answer shows its source text, citation, retrieval date and as-of date.

Hack-Nation 7th Global AI Hackathon, RealPage Challenge 2. **Live demo:** https://dhavalvibhakar99.github.io/rental-housing-law-navigator/ (served from `docs/`). **Code:** https://github.com/DhavalVibhakar99/rental-housing-law-navigator

## What it does

For each of 500 apartment buildings in California, New Jersey and Massachusetts, the Navigator answers: **which state and city housing rules apply here, as of a given date, and why?** It covers six categories:
- rent increase limits
- just-cause eviction
- security deposits
- application and screening fees
- screening restrictions
- algorithmic rent setting

The design rule is **the AI reads the law; code decides every address answer.**

1. **Extract (the only AI step).**
   - Claude reads each corpus document and proposes rule records as structured JSON.
   - Every quote must appear word for word in its source. Records that fail are dropped after one repair attempt.
   - Jurisdictions must match the manifest, and every number must appear in the source.
2. **Assemble (code).**
   - Records about the same law are merged and given readable IDs.
   - Status (in force, not yet effective, pending, failed) is computed from dates and legal form, never taken from the model.
3. **Resolve and apply (code).**
   - Addresses are placed by Census geocoding.
   - Coverage uses three-valued logic (yes / no / unknown) over assessor facts such as year built, unit count and use description.
   - Explanations and the plain-language summary come from fixed templates.
4. **Changes (code).** The engine runs at two dates and compares the results for change tests T1–T5.
5. **Self-check (code).** `python3 run.py check` validates the schemas, re-verifies every quote, runs T1–T5 as assertions, tests invariants, and scores the gold set.

## Results

| | |
|---|---|
| Rules | **54** (50 in force, 1 not yet effective, 2 pending bills, 1 failed ballot question) |
| Self-check | **29 passed, 2 warnings, 0 failed** |
| Change tests | **T1–T5 all pass** |
| Citation share | **3,087 of 3,229 "applies" answers (95.6%) cite a quote from the distributed starter-pack corpus** |
| Gold set | **28/30** hand-labelled rows match (see `METHOD_NOTE.md`) |

| Test | Expected | Result |
|---|---|---|
| T1 California AB 325 / SB 763 | CA-ALG-01 not yet effective on 2025-12-31, applies on 2026-01-02, for every CA address | Pass. 250 CA addresses |
| T2 Hoboken vs Jersey City bans | HOB-ALG-01 only in Hoboken, JC-ALG-01 only in Jersey City | Pass. 40 + 50 addresses |
| T3 NJ FAIR Act | NJ-ALG-01 not yet effective on 2026-10-01, applies on 2027-07-02; Jersey City and Hoboken flagged | Pass. 140 addresses, 90 flagged |
| T4 MA bills S.2983 / H.5222 | Pending for every MA address | Pass. 110 addresses |
| T5 MA rent-control ballot question struck | No rent cap in Boston or Cambridge; empty affected set | Pass. 0 addresses |

## Output files

| File | Contents |
|---|---|
| `out/rules.json` | 54 rule records in the challenge schema, each with citation, source URL, quoted span, retrieval date and confidence |
| `out/lookups.json` | All 500 addresses as of 2026-10-01: each applicable rule's result and templated explanation |
| `out/changes.json` | Change tests T1–T5: affected and conflict-flagged addresses, with notes |

Also committed:
- `out/extracted_raw.json`: validated extraction records, so `build` runs without the API
- `app/` and `docs/`: the static demo, as identical copies
- `data/gold_set.csv`: the hand-labelled gold set
- `data/coverage_evidence.json` and `data/rule_overrides.json`: team decisions, each verified and logged

## How to run

You need Python 3 (standard library only; `jsonschema` is used if installed). The law texts are **not in this repository** (see below), so first put them in place:

1. **Starter pack:** unzip the challenge's participant pack into `pack/`, so that `pack/corpus/corpus_manifest.csv`, `pack/corpus/text/`, `pack/data/sample_addresses.csv`, `pack/schema/` and `pack/dev/change_tests.json` exist.
2. **Team-captured pages (optional, needed for 9 rules):** download each URL listed in `data/extra_corpus/extra_manifest.csv` and save its text as `data/extra_corpus/<text_file>`, starting with the `SOURCE:` / `RETRIEVED:` header described in `nav/corpus.py`. `scripts/capture_links.py` fetches the HTML pages. The PDF captures (marked "PDF downloaded by team") must be saved by hand.

Then:

```bash
python3 run.py build             # rules, lookups, changes and app/data.js from out/extracted_raw.json (no model)
python3 run.py check             # self-check
python3 run.py explain A0001     # one address in the terminal (add --as-of 2025-12-31)
open docs/index.html             # the demo page, offline
```

Re-extracting a document calls the Claude API and spends credit. Do it only for named documents: `python3 run.py extract --docs D052`. This needs a key in `.anthropic_key`, which is gitignored.

## Research sources outside the distributed corpus

Some starter-pack entries were link-only. The organizers approved capturing those pages, and we fetched each one once. **These team-captured texts are research sources outside the distributed corpus.** **Per the organizers' ruling, they are excluded from the citation metric**: only quoted spans found in the distributed starter-pack corpus count.

We keep every rule but prefer starter-pack quotes:
- When a rule's group has both a starter-pack record and a team capture, the starter-pack record supplies the main quote and citation. The capture stays as a supporting source, and status, dates and coverage are unchanged.
- **9 rules still have their main quote outside the distributed corpus:** MA-RENT-P1, HOB-ALG-01, HOB-RENT-01, JC-ALG-01, LA-DEP-01, NWK-RENT-01, NWK-SCR-01, SD-SCR-01 and SNA-ALG-01.
- Those rules carry a 0.1 confidence penalty per address. The self-check lists them on every run.

## Known limits

- **Missing building facts lead to "unknown"; we never guess.**
  - Year built is missing for all Berkeley and San Diego addresses and for most in Hoboken and Newark.
  - Newark has no unit data.
  - Owner facts (owner-occupied, natural person) are never public.
- **Hoboken and Newark rent control:** "unknown" at 40 of 40 and 48 of 50 addresses. Both ordinances exempt new multiple dwellings for up to 30 years, depending on a filing that isn't in the data, and most of those buildings have no year built.
- **NJ owner-occupied exemptions:** NJ-SCR-02 (LAD) and NJ-DEP-01/02 (Security Deposit Law) are unknown at 52 of 140 NJ addresses, mostly in Newark, where unit counts aren't public.
- **Jersey City rent control (JC-RENT-01):** its source page says exemptions exist but doesn't list them. We report "applies" at confidence 0.5 with that caveat. The gold-set labeller chose "unknown", and this is the only remaining gold-set difference.
- **Tenancy-level carve-outs** (an owner sharing a kitchen or bath with the tenant) appear as caveats and don't change the building result.
- **Rules with no stated effective date** are treated as in force.
- **Massachusetts has no just-cause eviction law.** MA-EVIC-01 covers notice-to-quit rules only, and its card says so.
- **Four rules rest on secondary pages** (law firm, news): MA-RENT-P1, JC-ALG-01, LA-DEP-01, SNA-ALG-01.
- **Dates in the app:** the date picker offers only the precomputed dates (2025-12-31, 2026-01-02, 2026-10-01 and 2027-07-02).

## Specification

The current spec is the **v5 participant guide**. **The original Hack-Nation challenge brief is superseded by the v5 guide**, including its scoring weights. See `METHOD_NOTE.md` for the one-page method note.

## License

The code and the team's outputs are released under the MIT License (see `LICENSE`). The law texts, the starter pack and the captured pages are not included in this repository and are not covered by that license. Their sources are listed in the manifests.
