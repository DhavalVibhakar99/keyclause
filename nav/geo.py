"""Module B, step 1: resolve each address to the jurisdiction that makes the law.

Primary source: the Census Geocoder (incorporated place), produced by
scripts/geocode_census.py and saved to data/geocode_results.csv.
Fallback: a table of postal place names that sit inside a city's limits
(e.g. Dorchester -> Boston, San Ysidro -> San Diego).

When both are available and disagree, the address is flagged for review and the
Census answer wins. When neither resolves to a covered city, only state law is
applied and the lookup says so.
"""
from __future__ import annotations
import csv
import re

from . import config

# Postal names that are neighborhoods inside the legal city (not separate municipalities).
POSTAL_TO_CITY = {
    ("MA", "Boston"): "Boston, MA",
    ("MA", "Dorchester"): "Boston, MA",
    ("MA", "Roxbury"): "Boston, MA",
    ("MA", "East Boston"): "Boston, MA",
    ("MA", "South Boston"): "Boston, MA",
    ("MA", "Allston"): "Boston, MA",
    ("MA", "Brighton"): "Boston, MA",
    ("MA", "Jamaica Plain"): "Boston, MA",
    ("MA", "Mattapan"): "Boston, MA",
    ("MA", "Hyde Park"): "Boston, MA",
    ("MA", "Cambridge"): "Cambridge, MA",
    ("CA", "Los Angeles"): "Los Angeles, CA",
    ("CA", "San Francisco"): "San Francisco, CA",
    ("CA", "San Diego"): "San Diego, CA",
    ("CA", "San Ysidro"): "San Diego, CA",
    ("CA", "Berkeley"): "Berkeley, CA",
    ("NJ", "Jersey City"): "Jersey City, NJ",
    ("NJ", "Hoboken"): "Hoboken, NJ",
    ("NJ", "Newark"): "Newark, NJ",
}


def _place_to_city(place_name, state):
    """Map a Census place name ('Jersey City city', 'Boston city') to our jurisdiction key."""
    if not place_name:
        return None
    base = re.sub(r"\s+(city|town|township|CDP|city and county|borough)$", "", place_name.strip(), flags=re.I)
    base = re.sub(r"^City and County of\s+", "", base, flags=re.I)
    key = f"{base}, {state}"
    return key if key in config.JURISDICTION_CODE else f"{base}, {state} (not in corpus)"


def _house_numbers(s):
    m = re.match(r"\s*(\d+)", s or "")
    return m.group(1) if m else None


def _reliable_match(street, g):
    """A Census match is trusted only if it is unambiguous and the house number agrees."""
    if "candidates" in g.get("match", ""):
        return False, "several candidate addresses"
    inp, got = _house_numbers(street), _house_numbers(g.get("matched_address", ""))
    if inp and got and inp != got:
        # ranges like "1031-1035 CLINTON ST" may match any number inside the range
        rng = re.match(r"\s*(\d+)\s*-\s*(\d+)", street)
        if not (rng and int(rng.group(1)) <= int(got) <= int(rng.group(2))):
            return False, f"house number {inp} matched as {got}"
    return True, ""


def load_geocodes():
    """{address_id: {'place': ..., 'match': ..., 'matched_address': ...}} if the file exists."""
    out = {}
    if not config.GEOCODE_RESULTS.exists():
        return out
    with open(config.GEOCODE_RESULTS, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["address_id"]] = r
    return out


def resolve_all(facts):
    geocodes = load_geocodes()
    results = {}
    for aid, f in facts.items():
        fallback = POSTAL_TO_CITY.get((f.state, f.postal_city))
        g = geocodes.get(aid)
        census_city, method, flags, untrusted = None, None, [], False
        if g and g.get("match", "").startswith("Match") and g.get("place_name"):
            reliable, why = _reliable_match(f.street_address, g)
            if reliable:
                census_city = _place_to_city(g["place_name"], f.state)
            else:
                flags.append(f"Census match not trusted ({why}: matched '{g.get('matched_address')}'); "
                             "used postal-place table; needs human review.")
                untrusted = True
        if census_city:
            city, method = census_city, "Census Geocoder (incorporated place)"
            if fallback and census_city != fallback:
                flags.append(f"Census places this address in {census_city}; postal city '{f.postal_city}' suggests {fallback}. Census used; needs human review.")
        elif fallback:
            city = fallback
            method = ("postal-place table (Census match not trusted)" if untrusted else
                      "postal-place table (no Census match)" if g else
                      "postal-place table (Census results not loaded)")
            if g and not untrusted:
                flags.append(f"Census Geocoder returned no incorporated place ({g.get('match', 'no result')}); used postal-place table.")
        else:
            city, method = None, "unresolved"
            flags.append("Could not resolve a city; only state law applied.")
        if city and city.endswith("(not in corpus)"):
            flags.append(f"Resolved to {city.replace(' (not in corpus)', '')}, which has no local law in the corpus; only state law applied.")
        note = None
        if f.postal_city and city and not city.startswith(f.postal_city + ","):
            note = f"Mailing city '{f.postal_city}' is inside the City of {city.split(',')[0]}."
        results[aid] = {
            "state": f.state,
            "city": city if city and city in config.JURISDICTION_CODE else None,
            "resolved_label": city,
            "method": method,
            "flags": flags,
            "note": note,
        }
    return results
