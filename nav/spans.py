"""Verbatim quote verification: the guard that makes invented citations impossible.

A quoted span is accepted only if it occurs in the source document. Matching
ignores differences in whitespace (line wraps in the text copies) and in quote
or dash glyphs (curly vs straight), but nothing else. When a match is found we
return the document's OWN text for that location, so the stored span is always
copied from the source, never from the model.
"""
from __future__ import annotations
import re

_GLYPHS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", "﻿": "",
}


def _norm_char(c):
    return _GLYPHS.get(c, c)


def _normalize_with_map(text):
    """Collapse whitespace and unify glyphs; return (normalized, index_map)."""
    out, idx = [], []
    prev_space = True
    for i, ch in enumerate(text):
        ch = _norm_char(ch)
        if ch == "":
            continue
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
        else:
            out.append(ch)
            idx.append(i)
            prev_space = False
    return "".join(out), idx


def normalize(text):
    return _normalize_with_map(text)[0].strip()


class SpanIndex:
    """Pre-normalized document text for fast repeated lookups."""

    def __init__(self, text):
        self.text = text
        self.norm, self.map = _normalize_with_map(text)
        self.norm_lower = self.norm.lower()

    def locate(self, span):
        """Return (start, end) offsets in the original text, or None."""
        q = normalize(span)
        if len(q) < 1:
            return None
        pos = self.norm.find(q)
        if pos < 0:
            return None
        start = self.map[pos]
        end = self.map[pos + len(q) - 1] + 1
        return start, end

    def verify(self, span):
        """Return the source's own wording for the span (whitespace collapsed), or None."""
        loc = self.locate(span)
        if loc is None:
            return None
        original = self.text[loc[0]:loc[1]]
        return re.sub(r"\s+", " ", original).strip()

    def contains_number(self, token):
        return token.lower() in self.norm_lower


NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def numbers_supported(claim, index, extra_texts=()):
    """Every number in `claim` must appear somewhere in the source document.

    Used to catch invented figures in model-written fields such as key_value or
    requirement. Returns the list of unsupported numbers (empty = OK).
    """
    missing = []
    for n in NUM_RE.findall(claim or ""):
        variants = {n, n.replace(",", "")}
        if any(index.contains_number(v) for v in variants):
            continue
        if any(v in t for t in extra_texts for v in variants):
            continue
        missing.append(n)
    return missing
