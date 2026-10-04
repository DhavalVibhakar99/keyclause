"""Central configuration. Every path and constant lives here so runs are reproducible."""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "pack"
CORPUS_DIR = PACK / "corpus"
MANIFEST = CORPUS_DIR / "corpus_manifest.csv"
EXTRA_CORPUS_DIR = ROOT / "data" / "extra_corpus"   # team-captured sources (link-only pages), if allowed
EXTRA_MANIFEST = EXTRA_CORPUS_DIR / "extra_manifest.csv"
ADDRESSES = PACK / "data" / "sample_addresses.csv"
SCHEMA = PACK / "schema" / "rule_record.schema.json"
CHANGE_TESTS = PACK / "dev" / "change_tests.json"
GEOCODE_RESULTS = ROOT / "data" / "geocode_results.csv"   # produced by scripts/geocode_census.py
GOLD_SET = ROOT / "data" / "gold_set.csv"                  # hand-labelled by the team
COVERAGE_EVIDENCE = ROOT / "data" / "coverage_evidence.json"  # team-chosen coverage, each part backed by a verified starter-pack quote
RULE_OVERRIDES = ROOT / "data" / "rule_overrides.json"      # team editorial fixes (title/citation/requirement/key value only)
APP_DATA = ROOT / "app" / "data.js"                         # bundle read by app/index.html

CACHE_DIR = ROOT / "cache"
OUT_DIR = ROOT / "out"
AUDIT_LOG = OUT_DIR / "audit_log.jsonl"
EXTRACTED = OUT_DIR / "extracted_raw.json"    # validated candidate records per document

DEFAULT_AS_OF = "2026-10-01"

# LLM settings (only Module A uses the model)
MODEL = os.environ.get("NAV_MODEL", "claude-sonnet-5-5")
API_BASE = os.environ.get("NAV_API_BASE", "https://api.anthropic.com")
API_KEY_ENV = "ANTHROPIC_API_KEY"
PROMPT_VERSION = "extract-v4"
MAX_OUTPUT_TOKENS = 32000   # includes adaptive thinking

CATEGORIES = [
    "rent_increase_limits",
    "just_cause_eviction",
    "security_deposits",
    "application_screening_fees",
    "screening_restrictions",
    "algorithmic_rent_setting",
]

# Short codes used to build readable rule ids, e.g. CA-ALG-01.
CATEGORY_CODE = {
    "rent_increase_limits": "RENT",
    "just_cause_eviction": "EVIC",
    "security_deposits": "DEP",
    "application_screening_fees": "FEE",
    "screening_restrictions": "SCR",
    "algorithmic_rent_setting": "ALG",
}
JURISDICTION_CODE = {
    "CA": "CA", "NJ": "NJ", "MA": "MA",
    "Los Angeles, CA": "LA", "San Francisco, CA": "SF", "San Diego, CA": "SD",
    "Berkeley, CA": "BRK", "Santa Ana, CA": "SNA",
    "Jersey City, NJ": "JC", "Hoboken, NJ": "HOB", "Newark, NJ": "NWK",
    "Boston, MA": "BOS", "Cambridge, MA": "CAM",
}
CITIES = [j for j in JURISDICTION_CODE if "," in j]
