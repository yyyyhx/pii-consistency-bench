"""The command-line interface: init, score, run, materialize."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from piibench import cli

ROOT = Path(__file__).resolve().parents[1]


def test_find_task_by_prefix_and_full_id():
    assert cli.find_task("t09").name == "t09-storm-linked-accounts"
    assert cli.find_task("h2-curator-namesakes").name == "h2-curator-namesakes"


def test_init_then_score_untouched_is_zero(tmp_path, capsys):
    ws = tmp_path / "ws"
    assert cli.main(["init", "t01", str(ws)]) == 0
    assert (ws / "TASK.md").exists() and (ws / "pseudonyms.json").exists() and not (ws / "gold.json").exists()
    report = tmp_path / "r.json"
    assert cli.main(["score", "t01", str(ws), "--json", str(report)]) == 0
    assert json.loads(report.read_text())["score"] == 0.0


def test_run_grades_any_command(tmp_path):
    cleaner = tmp_path / "clean.py"
    cleaner.write_text("from pathlib import Path\nfrom piibench.cleaners import rules\nrules.clean(Path.cwd())\n")
    out = tmp_path / "runs.jsonl"
    cmd = f"{sys.executable} {cleaner}"
    assert cli.main(["run", "--cmd", cmd, "--tasks", "t01", "t09", "--out", str(out)]) == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["task_id"] for r in rows] == ["t01-zookeeper-directory", "t09-storm-linked-accounts"]
    assert rows[0]["score"] == 1.0 and rows[0]["status"] == "ok"


def test_run_reports_a_failing_command(tmp_path, capsys):
    out = tmp_path / "runs.jsonl"
    cmd = f"{sys.executable} does_not_exist.py"  # relative to the workspace, so it is not found
    assert cli.main(["run", "--cmd", cmd, "--tasks", "t01", "--out", str(out)]) == 1
    row = json.loads(out.read_text())
    assert row["status"].startswith("exit") and "does_not_exist.py" in row["stderr_tail"]
    assert "did not exit cleanly" in capsys.readouterr().out


def test_hugging_face_export_round_trips(tmp_path):
    subprocess.run([sys.executable, str(ROOT / "scripts" / "export_hf.py"), "--out", str(tmp_path / "hf")],
                   check=True, capture_output=True)
    rows = tmp_path / "hf" / "data" / "tasks"
    assert cli.main(["materialize", str(rows / "main.jsonl"), str(rows / "hard.jsonl"),
                     "--out", str(tmp_path / "mat")]) == 0
    for split in ("tasks", "tasks_hard"):
        for td in (ROOT / split).iterdir():
            if (td / "gold.json").exists():
                copy = tmp_path / "mat" / split / td.name
                assert (copy / "gold.json").read_bytes() == (td / "gold.json").read_bytes()
                for f in (td / "input").rglob("*"):
                    if f.is_file():
                        assert (copy / "input" / f.relative_to(td / "input")).read_bytes() == f.read_bytes()
