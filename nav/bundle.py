"""Write app/data.js: everything the static demo page needs, precomputed by the pipeline.

The page never decides anything itself; it only displays the engine's results for a
set of query dates (the default date plus the change-test dates).
"""
from __future__ import annotations
import json
import shutil

from . import config
from .corpus import load_corpus

OPEN_QUESTIONS = [  # guide section 9, surfaced verbatim as open questions
    "Berkeley's algorithmic ban (ch. 13.63) has two published effective dates: March 1, 2026 in the ordinance text, January 2026 per an August 2026 law-firm alert.",
    "New Jersey's FAIR Act may preempt the Jersey City and Hoboken ordinances once it takes effect.",
    "Los Angeles's new RSO formula has two published effective dates: 2026-02-02 per LAHD, 2026-01-24 per a landlord association.",
    "California's screening-fee cap has no single official 2026 dollar figure.",
]


def write_bundle(rules, facts, resolution, cache, changes):
    corpus = load_corpus()
    from .assemble import to_schema
    data = {
        "default_as_of": config.DEFAULT_AS_OF,
        "dates": sorted(cache),
        "rules": {r["team_rule_id"]: to_schema(r) for r in rules},
        "addresses": {
            aid: {
                "street": f.street_address, "postal_city": f.postal_city, "state": f.state, "zip": f.zip,
                "year_built": f.year_built, "units": f.units_label(), "units_source": f.units_source,
                "use": f.use_description, "dataset": f.source_dataset,
                "city": resolution[aid].get("city"), "resolved": resolution[aid].get("resolved_label"),
                "method": resolution[aid].get("method"), "geo_flags": resolution[aid].get("flags", []),
                "geo_note": resolution[aid].get("note"),
            } for aid, f in facts.items()
        },
        "lookups": {d: {aid: rows for aid, rows in table.items()} for d, table in cache.items()},
        "changes": changes,
        "open_questions": OPEN_QUESTIONS,
        "source_notes": {r["team_rule_id"]: r["source_note"] for r in rules if r.get("source_note")},
        "sources": {d.doc_id: {"url": d.url, "retrieved": d.retrieved_at, "type": d.source_type,
                               "origin": d.origin} for d in corpus.values() if d.has_text},
    }
    path = config.APP_DATA
    path.parent.mkdir(exist_ok=True)
    path.write_text("window.NAV_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n",
                    encoding="utf-8")
    # GitHub Pages serves /docs: keep an identical copy of the static app there
    docs = config.ROOT / "docs"
    docs.mkdir(exist_ok=True)
    shutil.copyfile(path, docs / "data.js")
    shutil.copyfile(path.parent / "index.html", docs / "index.html")
    (docs / ".nojekyll").write_text("", encoding="utf-8")
    return path
