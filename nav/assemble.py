"""Turn validated extraction candidates into the final rule set (rules.json).

- Merge the same rule found in several documents (e.g. a code section plus an
  agency page describing it). The best-sourced record becomes primary; the rest
  are kept as additional_sources. Disagreeing effective dates become a flagged
  open question instead of being silently resolved.
- Assign readable ids. Rules named in the supplied change tests get the test ids.
- Compute effective dates and status (nav/dates.py), never taking status from the model.
"""
from __future__ import annotations
import json
import re
from collections import defaultdict

from . import audit, config
from .dates import derive_effective, status_at

# (?<![.\w]): the "A" ending "N.J.S.A. 10" is a statute abbreviation, not Assembly bill A10.
BILL_RE = re.compile(r"(?<![.\w])(AB|SB|A|S|H|HB)\.?\s?(\d{2,5})\b", re.I)
SECTION_RE = re.compile(r"\d+(?:\.\d+)+[A-Za-z]?|\b\d{2,}[A-Za-z]?\b")


def _level(jur):
    return "city" if "," in jur else "state"


def _form_class(r):
    return "law" if r.get("legal_form") in ("enacted_law", "official_guidance") else r.get("legal_form")


DATE_NOISE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s*\d{4}\b"
                        r"|\b(19|20)\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b", re.I)


# N.J.S.A. title:chapter-section cites ("46:8-21.1", "2A:18-61.1") are one token each, so different
# sections of the same title don't merge just because both contain "46". Limited to N.J.S.A. cites:
# municipal codes with the same shape (Newark "19:2-3.1") keep the older chapter-level matching.
NJSA_RE = re.compile(r"N\.\s?J\.\s?S\.\s?A\.?")
COLON_CITE_RE = re.compile(r"\b\d+[A-Z]?:\d+[A-Z]?-\d+(?:\.\d+)*[A-Za-z]?")


def _cite_tokens(r):
    raw = f"{r.get('citation', '')}"
    colon = set()
    if NJSA_RE.search(raw):
        colon = set(COLON_CITE_RE.findall(raw))
        raw = COLON_CITE_RE.sub(" ", raw)
    text = DATE_NOISE.sub(" ", raw)
    bills = {f"{a.upper().rstrip('B')}{b}" for a, b in BILL_RE.findall(text)}
    nums = set(SECTION_RE.findall(text)) | colon
    return bills, nums


def _same_instrument(a, b):
    if _form_class(a) != "law":
        # bills/ballot measures: same only if they share a bill number
        ba, _ = _cite_tokens(a)
        bb, _ = _cite_tokens(b)
        return bool(ba & bb) or (not ba and not bb)
    _, na = _cite_tokens(a)
    _, nb = _cite_tokens(b)
    if not na or not nb:
        return True          # a page describing "the Rent Ordinance" without a cite merges with the law itself
    if any(x == y or x.startswith(y + ".") or y.startswith(x + ".") for x in na for y in nb):
        return True          # "13.63" and "13.63.020" are the same chapter
    ka, kb = (a.get("key_value") or "").strip().lower(), (b.get("key_value") or "").strip().lower()
    return bool(ka) and ka == kb   # two pages stating the same limit describe the same rule


SOURCE_RANK = {"enacted_law": 3, "official_guidance": 2, "bill": 3, "ballot_measure": 3}


def _is_supporting(r):
    """Relocation-assistance pages describe payment amounts under a just-cause law. They support
    the main rule but never supply its effective date or key value (amounts change every year)."""
    return r.get("category") == "just_cause_eviction" and "relocation" in (r.get("title") or "").lower()


def _primary_key(r):
    official = 1 if r.get("source_type", "").startswith("official") else 0
    return (not _is_supporting(r), official, SOURCE_RANK.get(r.get("legal_form"), 0), r.get("confidence", 0),
            1 if r.get("effective_date") or r.get("enacted_date") else 0)


WORD_RE = re.compile(r"[a-z]{3,}")


def _title_overlap(r, cluster):
    words = lambda x: set(WORD_RE.findall(f"{x.get('title', '')} {x.get('citation', '')}".lower()))
    w = words(r)
    return max(len(w & words(o)) / (len(w | words(o)) or 1) for o in cluster)


def _group(records):
    """Union-find within (jurisdiction, category, form class)."""
    buckets = defaultdict(list)
    for r in records:
        buckets[(r["jurisdiction"], r["category"], _form_class(r), r.get("rule_effect") == "restricts_local_government")].append(r)
    groups = []
    for key, recs in buckets.items():
        parent = list(range(len(recs)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        # Records with no section cite (e.g. an agency page about "the LAD") and relocation-assistance
        # pages (supporting detail) attach to ONE main cluster afterwards, so they can never bridge
        # two different laws into one rule or split off as a rule of their own.
        loose = [i for i, r in enumerate(recs)
                 if _form_class(r) == "law" and (not _cite_tokens(r)[1] or _is_supporting(r))]
        cited = [i for i in range(len(recs)) if i not in loose]
        for a in range(len(cited)):
            for b in range(a + 1, len(cited)):
                i, j = cited[a], cited[b]
                if _same_instrument(recs[i], recs[j]):
                    parent[find(i)] = find(j)
        for n, i in enumerate(loose):
            roots = sorted({find(j) for j in cited})
            if roots:
                best = max(roots, key=lambda root: (_title_overlap(recs[i], [recs[j] for j in cited if find(j) == root]), -root))
                parent[find(i)] = best
            else:   # no main record at all: group the loose ones among themselves
                for j in loose[:n]:
                    if _same_instrument(recs[i], recs[j]):
                        parent[find(i)] = find(j)
        clusters = defaultdict(list)
        for i, r in enumerate(recs):
            clusters[find(i)].append(r)
        groups.extend(clusters.values())
    return groups


def _merge(cluster):
    cluster = sorted(cluster, key=_primary_key, reverse=True)
    p = dict(cluster[0])
    p["level"] = _level(p["jurisdiction"])
    notes = []
    # A borrowed effective date is the earliest one stated: later dates in the same cluster are
    # amendments or annual adjustments (e.g. relocation amounts), not when the law took effect.
    if not p.get("effective_date"):
        dated = sorted((o for o in cluster[1:] if o.get("effective_date")
                         and not (_is_supporting(o) and not _is_supporting(p))), key=lambda o: o["effective_date"])
        if dated:
            p["effective_date"] = dated[0]["effective_date"]
            notes.append(f"effective_date taken from {dated[0]['source_doc_id']} (earliest stated)")
    # fill missing structured fields from other sources
    for other in cluster[1:]:
        if _is_supporting(other) and not _is_supporting(p):
            continue
        for f in ("effective_date", "enacted_date", "effective_relative", "key_value"):
            if not p.get(f) and other.get(f):
                p[f] = other[f]
                notes.append(f"{f} taken from {other['source_doc_id']}")
        cov, ocov = p.get("coverage") or {}, other.get("coverage") or {}
        for f in ("min_units", "max_units", "date_cutoff", "rolling_new_construction_exempt_years"):
            if cov.get(f) is None and ocov.get(f) is not None:
                cov[f] = ocov[f]
                notes.append(f"coverage.{f} taken from {other['source_doc_id']}")
        p["coverage"] = cov
    # conflicting effective dates across sources -> open question, flagged
    eff = {r["effective_date"] for r in cluster if r.get("effective_date") and (r is cluster[0] or not _is_supporting(r))}
    if len(eff) > 1:
        oq = f"Sources state different effective dates: {', '.join(sorted(eff))}."
        p["open_question"] = (p.get("open_question") + " " if p.get("open_question") else "") + oq
    p["additional_sources"] = [
        {"doc_id": o["source_doc_id"], "url": o["source_url"], "retrieved_at": o["retrieved_at"],
         "quoted_span": o["quoted_span"], "citation": o.get("citation"),
         **({"role": "supporting detail: relocation assistance", "title": o.get("title"),
             "key_value": o.get("key_value"), "stated_date": o.get("effective_date")}
            if _is_supporting(o) and not _is_supporting(p) else {})}
        for o in cluster[1:]
    ]
    p["merge_notes"] = notes
    effects = [r.get("rule_effect") for r in cluster if r.get("rule_effect")]
    # Only 'restricts_local_government' changes behaviour; 'other' (bills, scope pages) is handled by status logic.
    p["rule_effect"] = ("restricts_local_government"
                        if effects and all(e == "restricts_local_government" for e in effects) else "regulates_landlords")
    p["yields_to_local_rule"] = any(r.get("yields_to_local_rule") for r in cluster)
    pre = [r.get("preempts_local", "no") for r in cluster]
    p["preempts_local"] = "yes" if "yes" in pre else ("possible" if "possible" in pre else "no")
    return _prefer_pack_source(p, cluster)


SOURCE_FIELDS = ("source_doc_id", "source_url", "quoted_span", "citation", "retrieved_at", "source_type", "source_origin")


def _prefer_pack_source(p, cluster):
    """Organizer ruling: only quotes from the distributed starter-pack corpus count as citations.
    If the main record is a team capture and the group also has a starter-pack record, that record
    becomes the main source (quote, citation, URL). Only the source fields move: title, dates,
    coverage, status inputs and confidence stay exactly as merged. The team capture is kept as a
    supporting source."""
    if p.get("source_origin") != "team_capture":
        return p
    pack = [o for o in cluster if o.get("source_origin") == "starter_pack"
            and not (_is_supporting(o) and not _is_supporting(p))]
    if not pack:
        return p
    # the pack record must describe the same provision as the card: most shared section numbers
    # with the team capture first, then closest title, then the usual source ranking
    tn = _cite_tokens(p)[1]
    best = max(pack, key=lambda o: (len(tn & _cite_tokens(o)[1]), _title_overlap(o, [p]), _primary_key(o)))
    team = {f: p.get(f) for f in SOURCE_FIELDS}
    for f in SOURCE_FIELDS:
        p[f] = best.get(f)
    extra = [s for s in p["additional_sources"]
             if not (s["doc_id"] == best["source_doc_id"] and s["quoted_span"] == best["quoted_span"])]
    p["additional_sources"] = [{"doc_id": team["source_doc_id"], "url": team["source_url"],
                                "retrieved_at": team["retrieved_at"], "quoted_span": team["quoted_span"],
                                "citation": team["citation"],
                                "role": "team-captured research source (outside the distributed corpus)"}] + extra
    flags = p.setdefault("validation_flags", [])
    flags.append(f"main quote taken from starter-pack {best['source_doc_id']}; team capture {team['source_doc_id']} "
                 "kept as a supporting source (organizer ruling: only distributed-corpus quotes count)")
    if p.get("effective_date") and p.get("effective_date") != best.get("effective_date"):
        src = next((o["source_doc_id"] for o in cluster if o.get("effective_date") == p["effective_date"]), None)
        p["effective_date_source"] = src
        flags.append(f"effective date {p['effective_date']} comes from {src}, not from the main quote's document")
    return p


def _absorb_local_proposals(rules):
    """A city's pending proposal record becomes history of the enacted ordinance on the same subject.

    Our corpus holds staff reports and agenda copies of local ordinances that were later adopted
    (e.g. a proposal record next to the enacted ban). Reporting both would show one law twice, once
    as 'pending'. State bills are never absorbed: they are separate instruments (e.g. MA S.2983)."""
    laws = {(r["jurisdiction"], r["category"]): r for r in rules
            if r["level"] == "city" and _form_class(r) == "law" and r.get("rule_effect") != "restricts_local_government"}
    out = []
    for r in rules:
        law = laws.get((r["jurisdiction"], r["category"]))
        if r["level"] == "city" and _form_class(r) == "bill" and r.get("bill_outcome") != "failed" and law:
            law.setdefault("additional_sources", []).append(
                {"doc_id": r["source_doc_id"], "url": r["source_url"], "retrieved_at": r["retrieved_at"],
                 "quoted_span": r["quoted_span"], "citation": r.get("citation"), "note": "earlier proposal of this ordinance"})
            law.setdefault("merge_notes", []).append(f"absorbed proposal record from {r['source_doc_id']}")
            if not law.get("effective_date") and r.get("effective_date"):
                law["effective_date"] = r["effective_date"]
            _prefer_pack_proposal_quote(law, r)
            continue
        out.append(r)
    return out


def _prefer_pack_proposal_quote(law, prop):
    """Organizer ruling (only distributed-corpus quotes count): if the enacted ordinance's main quote is
    a team capture and the starter-pack proposal's quote appears word for word in the enacted text,
    the proposal's quote becomes the main quote. Status, dates and coverage stay with the enacted law."""
    if law.get("source_origin") != "team_capture" or prop.get("source_origin") != "starter_pack":
        return
    from .corpus import load_corpus
    from .spans import SpanIndex
    enacted = load_corpus().get(law["source_doc_id"])
    if not enacted or SpanIndex(enacted.body).verify(prop["quoted_span"]) is None:
        return
    team = {f: law.get(f) for f in SOURCE_FIELDS}
    for f in SOURCE_FIELDS:
        law[f] = prop.get(f)
    law["additional_sources"] = [{"doc_id": team["source_doc_id"], "url": team["source_url"],
                                  "retrieved_at": team["retrieved_at"], "quoted_span": team["quoted_span"],
                                  "citation": team["citation"],
                                  "role": "team-captured research source (outside the distributed corpus): enacted text"}] + [
        s for s in law["additional_sources"] if not (s["doc_id"] == prop["source_doc_id"] and s["quoted_span"] == prop["quoted_span"])]
    law.setdefault("validation_flags", []).append(
        f"main quote taken from starter-pack proposal {prop['source_doc_id']}; the same words appear verbatim in the enacted "
        f"text {team['source_doc_id']}, which supplies status and effective date")


# Change tests reference these ids; match them to our rules by what they describe.
def _test_id_for(r):
    j, c, form = r["jurisdiction"], r["category"], _form_class(r)
    bills, _ = _cite_tokens(r)
    text = (r.get("citation", "") + " " + r.get("title", "")).upper()
    if c == "algorithmic_rent_setting":
        if form == "law" and j == "CA":
            return "CA-ALG-01"
        if form == "law" and j == "Jersey City, NJ":
            return "JC-ALG-01"
        if form == "law" and j == "Hoboken, NJ":
            return "HOB-ALG-01"
        if form == "law" and j == "NJ":
            return "NJ-ALG-01"
        if form == "bill" and j == "MA" and ("S2983" in bills or "2983" in text):
            return "MA-ALG-P1"
        if form == "bill" and j == "MA" and ("H5222" in bills or "5222" in text):
            return "MA-ALG-P2"
    if c == "rent_increase_limits" and j == "MA" and form == "ballot_measure":
        return "MA-RENT-P1"
    # stable ids for the two MA fee rules: broker fees (c. 112, § 87DDD 1/2) and upfront charges (c. 186, § 15B)
    if c == "application_screening_fees" and j == "MA" and form == "law":
        cite = r.get("citation", "")
        if "87DDD" in cite:
            return "MA-FEE-01"
        if "15B" in cite:
            return "MA-FEE-02"
    # team decision: the NJ deposit cap (N.J.S.A. 46:8-21.2) is NJ-DEP-01; the return rule numbers after it
    if c == "security_deposits" and j == "NJ" and form == "law" and "46:8-21.2" in _cite_tokens(r)[1]:
        return "NJ-DEP-01"
    return None


SCOPE_RE = re.compile(r"\b(application|applicability|scope) of (the )?act\b", re.I)


def _is_scope(r):
    """A record that only says which buildings an act covers (e.g. N.J.S.A. 46:8-26, application of the
    Rent Security Deposit Act). It is supporting detail for that act's rules, never a rule of its own,
    and never supplies a date or key value."""
    return bool(SCOPE_RE.search(r.get("title") or ""))


def _attach_scope(rules, scope):
    for s in scope:
        for r in rules:
            if (r["jurisdiction"], r["category"]) == (s["jurisdiction"], s["category"]):
                r.setdefault("additional_sources", []).append(
                    {"doc_id": s["source_doc_id"], "url": s["source_url"], "retrieved_at": s["retrieved_at"],
                     "quoted_span": s["quoted_span"], "citation": s.get("citation"),
                     "role": "scope of the act (supporting detail; supplies no date)"})


def assemble(extracted, as_of=config.DEFAULT_AS_OF):
    records = [r for d in extracted.values() for r in d.get("rules", [])]
    scope = [r for r in records if _is_scope(r)]
    clusters = _group([r for r in records if not _is_scope(r)])
    rules = [_merge(c) for c in clusters]
    _attach_scope(rules, scope)
    rules = _absorb_local_proposals(rules)

    # effective dates and status
    for r in rules:
        r["effective_date_resolved"], r["effective_date_basis"] = derive_effective(r)
        r["status"] = status_at(r, as_of)
        if r["status"] == "ambiguous_date":
            r["status"] = "in_force"
            r.setdefault("validation_flags", []).append("query date falls inside a partial effective date")

    # ids: test ids first (one per id, best-sourced wins), then sequential
    rules.sort(key=lambda r: (r["level"] != "state", r["jurisdiction"], r["category"], -r.get("confidence", 0)))
    used = set()
    for r in rules:
        tid = _test_id_for(r)
        if tid and tid not in used:
            r["team_rule_id"] = tid
            used.add(tid)
    counters = defaultdict(int)
    for r in rules:
        if r.get("team_rule_id"):
            continue
        jc = config.JURISDICTION_CODE.get(r["jurisdiction"], "X")
        cc = config.CATEGORY_CODE[r["category"]]
        p = "P" if _form_class(r) != "law" else ""
        while True:
            counters[(jc, cc, p)] += 1
            rid = f"{jc}-{cc}-{p}{counters[(jc, cc, p)]:02d}"
            if rid not in used:
                break
        r["team_rule_id"] = rid
        used.add(rid)

    # interactions: state rules that yield to local rules of the same category
    by_cat_city = defaultdict(list)
    for r in rules:
        if r["level"] == "city":
            by_cat_city[(r["category"], r["jurisdiction"].split(", ")[1])].append(r["team_rule_id"])
    for r in rules:
        r["overrides"] = []
        state = r["jurisdiction"] if r["level"] == "state" else r["jurisdiction"].split(", ")[1]
        locals_ = by_cat_city.get((r["category"], state), [])
        if r["level"] == "state" and r.get("yields_to_local_rule"):
            r["overrides"] = locals_
            r["interaction"] = r.get("interaction") or "Yields where a stricter local rule of the same category applies."
        r["conflict_flag"] = bool(r.get("open_question")) or (
            r["level"] == "state" and r.get("preempts_local") in ("yes", "possible") and bool(locals_))
        notes = [x for x in [r.get("open_question")] if x]
        if r["conflict_flag"] and r.get("preempts_local") in ("yes", "possible") and locals_:
            notes.append(f"May preempt local rules {', '.join(locals_)}; needs human review.")
        r["conflict_note"] = " ".join(notes) or None
    _apply_overrides(rules)
    _apply_coverage_evidence(rules)
    audit.log("assembled", candidates=len(records), rules=len(rules), test_ids=sorted(used & {
        "CA-ALG-01", "JC-ALG-01", "HOB-ALG-01", "NJ-ALG-01", "MA-ALG-P1", "MA-ALG-P2", "MA-RENT-P1"}))
    return rules


def _apply_overrides(rules):
    """Team editorial fixes from data/rule_overrides.json: title, citation and plain-language
    requirement and key value only. Never dates, coverage, status or quotes. Each override must match exactly
    one rule (by jurisdiction, category and primary source document) or the build stops."""
    if not config.RULE_OVERRIDES.exists():
        return
    for ov in json.loads(config.RULE_OVERRIDES.read_text(encoding="utf-8")):
        m = ov["match"]
        hits = [r for r in rules if all(r.get(k) == v for k, v in m.items())]
        if len(hits) != 1:
            raise ValueError(f"rule override {m} matched {len(hits)} rules (expected 1); update data/rule_overrides.json")
        r = hits[0]
        changed = {k: v for k, v in ov["set"].items() if k in ("title", "citation", "requirement", "key_value")}
        r.update(changed)
        r.setdefault("validation_flags", []).append(
            f"team editorial override of {', '.join(changed)}: {ov['reason']}")
        audit.log("rule_override", rule=r["team_rule_id"], fields=sorted(changed), reason=ov["reason"])


def _apply_coverage_evidence(rules):
    """Coverage conditions and exemptions the team took from a starter-pack document, each backed by a
    quote that must appear word for word in that document (data/coverage_evidence.json). Unit-capped
    exemptions are testable: above the cap the exemption cannot apply; at or under it, or with units
    missing, the answer is unknown (engine policy). Anything unverifiable stops the build."""
    if not config.COVERAGE_EVIDENCE.exists():
        return
    from .corpus import load_corpus
    from .spans import SpanIndex
    corpus = load_corpus()
    for ev in json.loads(config.COVERAGE_EVIDENCE.read_text(encoding="utf-8")):
        m = ev["match"]
        hits = [r for r in rules if all(r.get(k) == v for k, v in m.items())]
        if len(hits) != 1:
            raise ValueError(f"coverage evidence {m} matched {len(hits)} rules (expected 1)")
        r = hits[0]
        doc = corpus.get(ev["evidence_doc_id"])
        # starter-pack documents, or the rule's own main source when that is a team capture
        if not doc or not doc.has_text or (doc.origin != "starter_pack" and doc.doc_id != r["source_doc_id"]):
            raise ValueError(f"coverage evidence for {r['team_rule_id']}: {ev['evidence_doc_id']} is neither a starter-pack "
                             "document with text nor the rule's own main source")
        idx = SpanIndex(doc.body)

        def verified(q):
            v = idx.verify(q)
            if v is None:
                raise ValueError(f"coverage evidence for {r['team_rule_id']}: quote not found verbatim in {doc.doc_id}: {q[:80]}")
            r.setdefault("coverage_evidence", []).append({"doc_id": doc.doc_id, "url": doc.url, "quote": v, "origin": doc.origin})
            return v
        cov = r.setdefault("coverage", {})
        changes = []
        if "date_cutoff" in ev:
            dc = dict(ev["date_cutoff"])
            verified(dc.pop("quote"))
            changes.append(f"date cutoff {cov.get('date_cutoff')} -> {dc}")
            cov["date_cutoff"] = dc
        if "no_date_conditions" in ev:
            verified(ev["no_date_conditions"]["quote"])
            if cov.get("date_cutoff") or cov.get("rolling_new_construction_exempt_years"):
                changes.append("removed building-age conditions")
            cov["date_cutoff"], cov["rolling_new_construction_exempt_years"] = None, None
        for uc in ev.get("unit_caps", []):   # a cap the text states but extraction left out
            hit = [e for e in cov.get("exemptions") or [] if e.get("description") == uc["description"]]
            if len(hit) != 1:
                raise ValueError(f"coverage evidence for {r['team_rule_id']}: unit cap target not found: {uc['description'][:60]}")
            verified(uc["quote"])
            changes.append(f"unit cap {hit[0].get('applies_only_if_units_at_most')} -> {uc['cap']} on: {uc['description'][:60]}")
            hit[0]["applies_only_if_units_at_most"] = uc["cap"]
        have = {e.get("description") for e in cov.get("exemptions") or []}
        for e in ev.get("exemptions", []):
            e = dict(e)
            verified(e.pop("quote"))
            if e["description"] not in have:
                cov.setdefault("exemptions", []).append(e)
                changes.append(f"exemption added: {e['description']}")
        if "caveat" in ev:   # coverage that can't be fully tested: keep the result, warn, lower confidence
            verified(ev["caveat"]["quote"])
            r.setdefault("team_caveats", []).append(ev["caveat"]["text"])
            cap = ev["caveat"].get("confidence_max")
            if cap is not None and (r.get("confidence") or 0) > cap:
                changes.append(f"confidence {r.get('confidence')} -> {cap}")
                r["confidence"] = cap
        r.setdefault("validation_flags", []).append(
            f"coverage from verified quotes in {doc.doc_id} ({ev['reason']})")
        audit.log("coverage_evidence", rule=r["team_rule_id"], doc=doc.doc_id, changes=changes)


def _caveats(r):
    """Plain caveats for the rule card: tenancy-level carve-outs, plus any team caveat."""
    from .engine import is_tenancy_carveout
    out = [f"Tenancy-level carve-out: {e['description'].rstrip('.')}. Public records don't show individual tenancies, so the building-level result is not changed."
           for e in (r.get("coverage") or {}).get("exemptions") or [] if is_tenancy_carveout(e)]
    return out + r.get("team_caveats", [])


SCHEMA_FIELDS = ["team_rule_id", "jurisdiction", "level", "category", "status", "title", "requirement",
                 "key_value", "coverage_conditions", "exemptions", "overrides", "interaction",
                 "effective_date", "citation", "source_doc_id", "source_url", "quoted_span",
                 "confidence", "conflict_flag", "conflict_note"]


def to_schema(r):
    cov = r.get("coverage") or {}
    ex = cov.get("exemptions") or []
    out = {
        "team_rule_id": r["team_rule_id"],
        "jurisdiction": r["jurisdiction"],
        "level": r["level"],
        "category": r["category"],
        "status": r["status"],
        "title": r["title"],
        "requirement": r["requirement"],
        "key_value": r.get("key_value"),
        "coverage_conditions": cov or None,
        "exemptions": "; ".join(e["description"] for e in ex) or None,
        "overrides": r.get("overrides", []),
        "interaction": r.get("interaction"),
        "effective_date": r.get("effective_date_resolved"),
        "citation": r["citation"],
        "source_doc_id": r.get("source_doc_id"),
        "source_url": r["source_url"],
        "quoted_span": r["quoted_span"],
        "confidence": r.get("confidence"),
        "conflict_flag": bool(r.get("conflict_flag")),
        "conflict_note": r.get("conflict_note"),
        # extra, non-schema fields for transparency (schema allows additional properties)
        "retrieved_at": r.get("retrieved_at"),
        "legal_form": r.get("legal_form"),
        "bill_outcome": r.get("bill_outcome"),
        "enacted_date": r.get("enacted_date"),
        "effective_date_basis": r.get("effective_date_basis"),
        "source_origin": r.get("source_origin"),
        "rule_effect": r.get("rule_effect", "regulates_landlords"),
        "rule_effect_reason": r.get("rule_effect_reason"),
        "additional_sources": r.get("additional_sources", []),
        "coverage_evidence": r.get("coverage_evidence", []),
        "caveats": _caveats(r),
        "validation_flags": r.get("validation_flags", []),
    }
    return out


def write_rules(rules):
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    (config.OUT_DIR / "rules_internal.json").write_text(json.dumps(rules, indent=1, ensure_ascii=False), encoding="utf-8")
    path = config.OUT_DIR / "rules.json"
    path.write_text(json.dumps({"rules": [to_schema(r) for r in rules]}, indent=1, ensure_ascii=False), encoding="utf-8")
    return path
