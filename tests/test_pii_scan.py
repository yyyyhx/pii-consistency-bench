"""The gold-independent PII scan: committed tasks are clean, planted identifiers are caught."""

from __future__ import annotations

from pathlib import Path

import pytest

from piibench import pii_scan

ROOT = Path(__file__).resolve().parents[1]
ALL_TASKS = sorted(p for root in (ROOT / "tasks", ROOT / "tasks_hard") if root.is_dir()
                   for p in root.iterdir() if (p / "gold.json").exists())



@pytest.mark.parametrize("task", ALL_TASKS, ids=lambda p: p.name)
def test_committed_task_has_no_unregistered_person_identifiers(task):
    assert pii_scan.scan_task(task) == {}


@pytest.mark.parametrize("text,category", [
    ("Please ask zbigniew.k@gmail.com about it.", "email"),
    ("Rebased on https://github.com/jkowalski42/storm/tree/fix", "url_user"),
    ("Patch by jkowalski42, see above.", "cue_name"),
    ("Thanks Zbigniew, that fixed the build.", "cue_name"),
    ("I talked to Terrence about the release.", "proper_name"),
])
def test_planted_identifier_is_found(text, category):
    assert category in pii_scan.scan_strings([text], known=set())


def test_known_synthetic_tokens_and_code_are_not_flagged():
    text = ("Thanks gquennell, merged in HDFS-1234. See FollowerSyncRequest.java and branch-2; "
            "mail dev@storm.apache.org or https://github.com/apache/storm.")
    assert pii_scan.scan_strings([text], known={"gquennell"}) == {}


def test_cue_word_inside_a_username_is_not_a_cue():
    """"byilmaz" is an account name, not "by ilmaz"."""
    assert "cue_name" not in pii_scan.scan_strings(['{"username": "byilmaz"}'], known={"byilmaz"})
