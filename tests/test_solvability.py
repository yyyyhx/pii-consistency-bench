"""Every first-name mention must be attributable from its own thread (see piibench.solvability)."""

from __future__ import annotations

from pathlib import Path

import pytest

from piibench.solvability import ambiguous_mentions

ROOT = Path(__file__).resolve().parents[1]
ALL_TASKS = sorted(p for root in (ROOT / "tasks", ROOT / "tasks_hard") if root.is_dir()
                   for p in root.iterdir() if (p / "gold.json").exists())


@pytest.mark.parametrize("task", ALL_TASKS, ids=lambda p: p.name)
def test_first_name_mentions_are_unique_in_their_thread(task):
    assert ambiguous_mentions(task) == []
