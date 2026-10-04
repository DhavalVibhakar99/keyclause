"""Load the supplied corpus: manifest rows plus the plain-text copies.

Each document keeps its source URL and retrieval date so every rule can cite them.
Link-only documents are kept in the registry (they are legitimate sources the
law references) but have no text, so nothing can be extracted or quoted from them.
"""
from __future__ import annotations
import csv
import hashlib
import re
from dataclasses import dataclass, field

from . import config


@dataclass
class Doc:
    doc_id: str
    jurisdictions: list
    url: str
    source_type: str
    capture: str
    status: str
    retrieved_at: str = ""
    text: str = ""            # full text including the SOURCE/RETRIEVED header
    body: str = ""            # text after the header
    text_sha256: str = ""
    origin: str = "starter_pack"   # or "team_capture"
    notes: list = field(default_factory=list)

    @property
    def has_text(self) -> bool:
        return bool(self.body.strip())


HEADER_RE = re.compile(r"^SOURCE:\s*(?P<url>\S+)\s*\nRETRIEVED:\s*(?P<ret>[^\n]+)\n", re.M)


def _parse_jurisdictions(raw: str) -> list:
    # "Berkeley, CA" is one jurisdiction; a bare "CA" is a state.
    raw = raw.strip()
    return [raw] if raw else []


def _read_manifest(path, origin):
    docs = []
    if not path.exists():
        return docs
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            d = Doc(
                doc_id=row["doc_id"].strip(),
                jurisdictions=_parse_jurisdictions(row.get("jurisdictions", "")),
                url=row.get("url", "").strip(),
                source_type=row.get("source_type", "").strip(),
                capture=row.get("capture", "").strip(),
                status=row.get("status", "").strip(),
                retrieved_at=row.get("retrieved_at", "").strip(),
                origin=origin,
            )
            tf = row.get("text_file", "").strip()
            base = path.parent
            if tf and (base / tf).exists():
                d.text = (base / tf).read_text(encoding="utf-8")
                d.text_sha256 = hashlib.sha256(d.text.encode("utf-8")).hexdigest()
                m = HEADER_RE.match(d.text)
                if m:
                    d.body = d.text[m.end():]
                    if m.group("url") != d.url:
                        d.notes.append(f"header URL differs from manifest: {m.group('url')}")
                    if not d.retrieved_at:
                        d.retrieved_at = m.group("ret").strip()
                else:
                    d.body = d.text
                    d.notes.append("no SOURCE/RETRIEVED header")
            docs.append(d)
    return docs


def load_corpus():
    """Return {doc_id: Doc} for the starter pack plus any team captures."""
    docs = _read_manifest(config.MANIFEST, "starter_pack")
    docs += _read_manifest(config.EXTRA_MANIFEST, "team_capture")
    out = {}
    for d in docs:
        if d.doc_id in out:
            prev = out[d.doc_id]
            # A team capture may fill in a starter-pack entry that had no text (link-only),
            # either from the listed URL or from the official source of the same law.
            if d.origin == "team_capture" and not prev.has_text:
                if d.url != prev.url:
                    d.notes.append(f"replaces starter-pack link {prev.url} with official source")
                d.jurisdictions = prev.jurisdictions
                out[d.doc_id] = d
                continue
            raise ValueError(f"duplicate doc_id {d.doc_id}")
        out[d.doc_id] = d
    return out


def retrieval_date(doc: Doc) -> str:
    """ISO date (YYYY-MM-DD) of retrieval, for display and citation."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", doc.retrieved_at or "")
    return m.group(1) if m else ""


if __name__ == "__main__":
    corpus = load_corpus()
    with_text = [d for d in corpus.values() if d.has_text]
    print(f"{len(corpus)} documents, {len(with_text)} with text, "
          f"{sum(len(d.body) for d in with_text):,} characters")
    for d in corpus.values():
        if d.notes:
            print(d.doc_id, d.notes)
