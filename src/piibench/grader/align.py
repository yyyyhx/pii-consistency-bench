"""Recover what each gold span became in the cleaned text."""

from __future__ import annotations

import difflib
from dataclasses import dataclass


@dataclass
class Alignment:
    replacements: list[str]  # one per span, in span order
    exact_literals: bool  # all non-PII text preserved verbatim
    overscrub_edits: int  # edit regions outside gold spans
    preserved_chars: int  # non-PII characters preserved
    literal_chars: int  # non-PII characters in the original


def _literals(original: str, spans: list[tuple[int, int]]) -> list[str]:
    lits, cur = [], 0
    for s, e in spans:
        lits.append(original[cur:s])
        cur = e
    lits.append(original[cur:])
    return lits


def align(original: str, cleaned: str, spans: list[tuple[int, int]]) -> Alignment:
    """``spans`` must be sorted and non-overlapping half-open ranges into ``original``."""
    lits = _literals(original, spans)
    lit_chars = sum(len(x) for x in lits)
    fast = _sequential(cleaned, lits)
    if fast is not None:
        return Alignment(fast, True, 0, lit_chars, lit_chars)
    return _diff(original, cleaned, spans, lit_chars)


def _sequential(cleaned: str, lits: list[str]) -> list[str] | None:
    if not cleaned.startswith(lits[0]):
        return None
    pos = len(lits[0])
    reps = []
    last = len(lits) - 1
    for i in range(1, len(lits)):
        lit = lits[i]
        if i == last:
            end = len(cleaned) - len(lit)
            if end < pos or not cleaned.endswith(lit):
                return None
            reps.append(cleaned[pos:end])
            pos = len(cleaned)
        else:
            if not lit:
                return None
            j = cleaned.find(lit, pos)
            if j < 0:
                return None
            reps.append(cleaned[pos:j])
            pos = j + len(lit)
    return reps


def _diff(original: str, cleaned: str, spans: list[tuple[int, int]], lit_chars: int) -> Alignment:
    """Character diff, anchored only on non-PII text.

    Characters a name shares with its replacement (" at ", " dot ", a letter) are coincidences and must not
    decide where a replacement starts or ends, nor pull the literal text out of place. So the spans are masked
    with a character that occurs nowhere in the cleaned text before diffing: only literal text can match. Each
    span's replacement is whatever lies between the anchor that ends before it and the anchor that starts after
    it. Every gap between anchors that loses or changes literal text is one over-scrub edit; whitespace-only
    insertions and deletions are not counted.
    """
    in_span = bytearray(len(original))
    for s, e in spans:
        in_span[s:e] = b"\x01" * (e - s)
    mask = next(chr(c) for c in range(0xE000, 0xF900) if chr(c) not in cleaned)
    masked = "".join(mask if in_span[p] else c for p, c in enumerate(original))
    anchors = [(i, j, n) for i, j, n in
               difflib.SequenceMatcher(None, masked, cleaned, autojunk=False).get_matching_blocks() if n]

    reps = []
    for s, e in spans:
        js = max((j + n for i, j, n in anchors if i + n <= s), default=0)
        je = min((j for i, j, n in anchors if i >= e), default=len(cleaned))
        reps.append(cleaned[js:max(js, je)] if s < e else cleaned[js:js])

    edits = 0
    bounds = [(0, 0, 0)] + anchors + [(len(original), len(cleaned), 0)]
    for (i1, j1, n1), (i2, j2, _n2) in zip(bounds, bounds[1:]):
        go, gc = original[i1 + n1:i2], cleaned[j1 + n1:j2]
        lost = [c for p, c in enumerate(go, i1 + n1) if not in_span[p]]
        if len(lost) == len(go):  # a gap in literal text only
            if go and gc:
                edits += 1
            elif (go + gc).strip():
                edits += 1
        elif "".join(lost).strip():  # literal characters lost next to a replacement
            edits += 1
    preserved = sum(n for _i, _j, n in anchors)
    return Alignment(reps, False, edits, preserved, lit_chars)
