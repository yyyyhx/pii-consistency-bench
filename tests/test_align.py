"""Recovering each span's replacement from the cleaned text."""

from __future__ import annotations

from piibench.grader.align import align

QUOTE = "On Tue, Wainwright, Tatiana <tatiana.wainwright at example dot com> wrote:\n> Is there a simpler way?"
SPANS = [(8, 27), (29, 66)]


def test_exact_literals():
    out = "On Tue, Laurentis, Ugo <ugo.laurentis at pseudonym dot example> wrote:\n> Is there a simpler way?"
    al = align(QUOTE, out, SPANS)
    assert al.replacements == ["Laurentis, Ugo", "ugo.laurentis at pseudonym dot example"]
    assert al.exact_literals and al.overscrub_edits == 0


def test_character_lost_next_to_a_span_keeps_the_replacement():
    """The agent rewrote the address and also dropped the ">" after it: one over-scrub, replacement intact."""
    out = "On Tue, Laurentis, Ugo <ugo.laurentis at pseudonym dot example wrote:\n> Is there a simpler way?"
    al = align(QUOTE, out, SPANS)
    assert al.replacements == ["Laurentis, Ugo", "ugo.laurentis at pseudonym dot example"]
    assert al.overscrub_edits == 1
    assert al.preserved_chars == al.literal_chars - 1


def test_edit_far_from_spans_does_not_move_the_replacements():
    """Characters the address shares with its replacement (" at ", " dot ") are not anchors."""
    out = "On Tue, Laurentis, Ugo <ugo.laurentis at pseudonym dot example> wrote:\n> Is there an easier way?"
    al = align(QUOTE, out, SPANS)
    assert al.replacements == ["Laurentis, Ugo", "ugo.laurentis at pseudonym dot example"]
    assert al.overscrub_edits >= 1 and not al.exact_literals
