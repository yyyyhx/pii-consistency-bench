"""Sensitive-token matching for leak detection.

Matching runs on folded text: accents removed and case folded, so "Søren", "Soren" and "SOREN" are one token.
Real aliases differ exactly this way (a Jira profile with the accented spelling, a git config without it), and a
cleaner that leaves "Soren" behind has leaked "Søren".
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from ..gold import Entity

# letters that Unicode decomposition does not reduce to a base letter plus a combining mark
_SPECIAL = str.maketrans({"ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ı": "i", "þ": "th",
                          "Þ": "Th", "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ß": "ss"})


def fold(s: str) -> str:
    """Accent- and case-insensitive form used for every identity comparison."""
    s = unicodedata.normalize("NFKD", s.translate(_SPECIAL))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


class FoldedPattern:
    """A compiled pattern over folded tokens that folds the text it is applied to."""

    def __init__(self, rx: re.Pattern[str]):
        self.rx = rx

    def search(self, text: str):
        return self.rx.search(fold(text))

    def finditer(self, text: str):
        return self.rx.finditer(fold(text))

    def findall(self, text: str) -> list[str]:
        return self.rx.findall(fold(text))


def entity_tokens(entity: Entity) -> set[str]:
    toks = set(entity.tokens.get("emails", [])) | set(entity.tokens.get("email_locals", []))
    toks |= set(entity.tokens.get("usernames", [])) | set(entity.tokens.get("names", []))
    return {t for t in toks if t}


def _pattern(tokens: Iterable[str]) -> FoldedPattern:
    """Boundary-delimited alternation of folded tokens; longer tokens first so an email wins over its parts."""
    alts = sorted({re.escape(fold(t)) for t in tokens}, key=len, reverse=True)
    return FoldedPattern(re.compile(r"(?<![a-z0-9])(?:" + "|".join(alts) + r")(?![a-z0-9])"))


def entity_patterns(entity: Entity) -> FoldedPattern:
    return _pattern(entity_tokens(entity))


def combined_pattern(entities: Iterable[Entity]) -> FoldedPattern:
    toks: set[str] = set()
    for e in entities:
        toks |= entity_tokens(e)
    return _pattern(toks)


SUBSTRING_MIN_LEN = 4


def entity_substring_tokens(entity: Entity) -> set[str]:
    """Folded identifiers long enough to count as a leak when embedded inside a replacement.

    Only applied to the replacement of one of the entity's own occurrences, never to the whole output:
    a short nickname like "Vale" is a substring of ordinary words ("evaluate").
    """
    return {fold(t) for t in entity_tokens(entity) if len(t) >= SUBSTRING_MIN_LEN}
