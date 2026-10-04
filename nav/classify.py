"""Module A, step 2: classify what each extracted rule DOES (one batched model call, cached).

Some laws in a category do not protect tenants at an address; they restrict what local
governments may enact. Example: Mass. G.L. c. 40P sec. 4 bans local rent control. Filed
under rent_increase_limits it would wrongly read as a rent cap. Such rules stay in
rules.json (cited, with their quote) but the engine never reports them as applying to an
address; the app shows them as context ("no rent cap here because ...").

The label is the model's reading of the rule's own fields and verified quote; it never
sees an address. Unlabelled records default to 'regulates_landlords' and are flagged.
"""
from __future__ import annotations

from . import audit
from .llm import LLMError, call_tool

EFFECTS = ["regulates_landlords", "restricts_local_government", "other"]

TOOL = {
    "name": "classify_rules",
    "description": "Classify what each rule does.",
    "input_schema": {
        "type": "object",
        "required": ["labels"],
        "properties": {"labels": {"type": "array", "items": {
            "type": "object", "required": ["i", "effect", "reason"],
            "properties": {
                "i": {"type": "integer"},
                "effect": {"type": "string", "enum": EFFECTS},
                "reason": {"type": "string", "description": "One short sentence."}}}}},
    },
}

SYSTEM = """You classify housing-law records by what they do. For each record choose:
- regulates_landlords: the rule imposes a limit, duty or prohibition on landlords/owners (or their agents/vendors) with respect to rental housing, e.g. a rent cap, deposit cap, just-cause requirement, screening limit, fee cap, or algorithmic-pricing ban.
- restricts_local_government: the rule only limits what cities or towns may enact or enforce (e.g. a state ban on local rent control) and imposes no limit on landlords itself.
- other: anything else (purely procedural, definitions, enforcement bodies only).
Judge only from the record's text and quote. Return one label per record index."""


def classify_records(records):
    """records: list of dicts (mutated: adds rule_effect, rule_effect_reason)."""
    todo = [r for r in records if not r.get("rule_effect")]
    if not todo:
        return records
    lines = []
    for i, r in enumerate(todo):
        lines.append(f"[{i}] {r['jurisdiction']} | {r['category']} | {r.get('title', '')}\n"
                     f"    requirement: {r.get('requirement', '')}\n    quote: \"{r.get('quoted_span', '')}\"")
    user = "Records:\n\n" + "\n\n".join(lines)
    try:
        out = call_tool(SYSTEM, user, TOOL, label="classify:effects", max_tokens=16000)
        labels = {x["i"]: x for x in out.get("labels", []) if isinstance(x.get("i"), int)}
    except LLMError as e:
        audit.log("classify_failed", detail=str(e))
        labels = {}
    for i, r in enumerate(todo):
        lab = labels.get(i)
        if lab and lab.get("effect") in EFFECTS:
            r["rule_effect"], r["rule_effect_reason"] = lab["effect"], lab.get("reason")
        else:
            r["rule_effect"], r["rule_effect_reason"] = "regulates_landlords", "not classified (default)"
            r.setdefault("validation_flags", []).append("rule effect not classified; defaulted to regulates_landlords")
    audit.log("classified", records=len(todo),
              restricts_local=[f"{r['source_doc_id']}:{r.get('title', '')[:50]}" for r in todo
                               if r["rule_effect"] == "restricts_local_government"])
    return records
