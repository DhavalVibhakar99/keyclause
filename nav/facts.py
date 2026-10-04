"""Building facts for each address, with provenance.

Unit counts come from the `units` column when present. When it is blank, a
lower bound (and sometimes an upper bound) is read from the assessor's own use
description, e.g. NJ MOD-IV "4S-B-A-21U-H" (21 units) or Boston "APT 7-30 UNITS".
Every inferred value records where it came from so explanations can say so.
Nothing is guessed: descriptions we cannot read with confidence stay unknown.
"""
from __future__ import annotations
import csv
import re
from dataclasses import dataclass, field

from . import config


@dataclass
class Facts:
    address_id: str
    street_address: str
    postal_city: str
    state: str
    zip: str
    year_built: int | None
    units_min: int | None        # lower bound on unit count (exact when == units_max)
    units_max: int | None
    units_source: str            # "units column", "assessor use description '...'", "none"
    use_code: str
    use_description: str
    subsidized: bool | None      # True only when the record says so; otherwise unknown
    source_dataset: str
    retrieved_at: str
    notes: list = field(default_factory=list)

    @property
    def units_exact(self):
        if self.units_min is not None and self.units_min == self.units_max:
            return self.units_min
        return None

    def units_label(self):
        if self.units_exact is not None:
            return f"{self.units_exact} units"
        if self.units_min is not None and self.units_max is not None:
            return f"{self.units_min}-{self.units_max} units"
        if self.units_min is not None:
            return f"at least {self.units_min} units"
        return "unit count unknown"


# NJ MOD-IV building descriptions: "21U" = 21 units. Only J.C. and Hoboken use this
# convention consistently in the sample; Newark's "2UG"-style tokens are ambiguous
# (they may describe garages), so Newark is deliberately not parsed.
NJ_UNIT_RE = re.compile(r"(?<![A-Z0-9])(\d{1,4})U(?![A-Z])")
RANGE_RE = re.compile(r"(\d+)\s*-\s*(\d+)\s*UNIT", re.I)
PLUS_RE = re.compile(r"\(?(\d+)\+\s*UNITS?\)?", re.I)
OR_MORE_RE = re.compile(r"(\d+)\s*UNITS?\s*OR\s*MORE", re.I)
LE_RE = re.compile(r"(\d+)\s*UNITS?\s*OR\s*LESS", re.I)
WORD_NUM = {"five": 5, "four": 4, "three": 3, "two": 2, "six": 6}


def _units_from_description(state, postal_city, desc):
    d = desc.strip()
    if not d:
        return None, None
    if state == "NJ" and postal_city in ("Jersey City", "Hoboken"):
        hits = [int(x) for x in NJ_UNIT_RE.findall(d.upper())]
        if hits:
            # Several buildings on one parcel ("3B-7U/4B-24U-G") -> the parcel has at
            # least the largest stated count; we do not sum, to stay conservative.
            return max(hits), (hits[0] if len(hits) == 1 else None)
        return None, None
    m = RANGE_RE.search(d)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = PLUS_RE.search(d) or OR_MORE_RE.search(d)
    if m:
        return int(m.group(1)), None
    m = LE_RE.search(d)
    if m:
        return None, int(m.group(1))
    m = re.search(r"(\w+) or more apartments", d, re.I)
    if m and m.group(1).lower() in WORD_NUM:
        return WORD_NUM[m.group(1).lower()], None
    m = re.match(r">\s*(\d+)-UNIT", d.upper().replace("MXD ", ""))
    if m:
        return int(m.group(1)) + 1, None
    m = re.match(r"(\d+)-(\d+)-UNIT", d.upper())
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _int_or_none(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def load_addresses():
    out = {}
    with open(config.ADDRESSES, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            units = _int_or_none(r["units"])
            desc = r["use_description"]
            notes = []
            if units is not None:
                umin = umax = units
                src = "units column"
                dmin, dmax = _units_from_description(r["state"], r["postal_city"], desc)
                if dmin is not None and dmin > units:
                    # Two public sources disagree: keep both ends, never pick one.
                    notes.append(f"units column says {units} but use description '{desc}' says {dmin}; treated as a range")
                    umin, umax = units, dmin
                    src = f"units column ({units}) and use description '{desc}' disagree"
            else:
                umin, umax = _units_from_description(r["state"], r["postal_city"], desc)
                src = f"assessor use description '{desc}'" if (umin or umax) else "none"
            if umin is not None and umax is not None and umin > umax:
                umax = None
            subsidized = None
            if re.search(r"SUBSD|SECTION\s*8|S-\s*8|AFFORDAB", desc, re.I):
                subsidized = True
            out[r["address_id"]] = Facts(
                address_id=r["address_id"],
                street_address=r["street_address"],
                postal_city=r["postal_city"],
                state=r["state"],
                zip=r["zip"],
                year_built=_int_or_none(r["year_built"]),
                units_min=umin,
                units_max=umax,
                units_source=src,
                use_code=r["use_code"],
                use_description=desc,
                subsidized=subsidized,
                source_dataset=r["source_dataset"],
                retrieved_at=r["retrieved_at"],
                notes=notes,
            )
    return out


if __name__ == "__main__":
    a = load_addresses()
    from collections import Counter
    print(len(a), "addresses")
    print("unit source:", Counter(f.units_source.split(" '")[0] for f in a.values()))
    print("no unit info at all:", Counter(f.postal_city for f in a.values() if f.units_min is None and f.units_max is None))
    print("year missing:", sum(f.year_built is None for f in a.values()))
    for f in a.values():
        if f.notes:
            print(f.address_id, f.notes)
