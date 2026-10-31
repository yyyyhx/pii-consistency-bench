"""Grader correctness on controlled, intentionally flawed cleanings of a committed task."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from piibench.cleaners import oracle
from piibench.gold import Gold, read_jsonl, write_jsonl
from piibench.grader import grade
from piibench.personas import render_persona

TASKS = Path(__file__).resolve().parents[1] / "tasks"
TASK = TASKS / "t01-zookeeper-directory"


@pytest.fixture()
def gold() -> Gold:
    return Gold.load(TASK / "gold.json")


@pytest.fixture()
def personas() -> list[dict]:
    return json.loads((TASK / "input" / "pseudonyms.json").read_text())


@pytest.fixture()
def work(tmp_path: Path) -> Path:
    dst = tmp_path / "ws"
    shutil.copytree(TASK / "input", dst)
    return dst


def run(work: Path, gold: Gold):
    return grade(TASK / "input", work, gold)


def first_occ(gold: Gold, pred):
    return next(o for o in gold.occurrences if pred(o))


def test_noop_scores_zero(work, gold):
    r = run(work, gold)
    assert r.score == 0.0
    assert r.components["entity_recall"] == 0.0
    assert r.diagnostics["occurrence_recall"] == 0.0
    assert r.gate


def test_oracle_is_perfect(work, gold):
    oracle.clean(work, gold)
    r = run(work, gold)
    assert r.score == pytest.approx(1.0), r.failures
    assert all(v == pytest.approx(1.0) for v in r.components.values())
    assert r.diagnostics["text_preservation"] == pytest.approx(1.0)


def test_alternative_valid_assignment_gets_full_credit(work, gold):
    oracle.clean(work, gold, seed=12345)
    r = run(work, gold)
    assert r.score == pytest.approx(1.0), r.failures


def _invented(i: int) -> dict:
    return {
        "persona_id": f"invented-{i}",
        "first_name": f"Nova{i}",
        "last_name": f"Quill{i}",
        "full_name": f"Nova{i} Quill{i}",
        "email": f"nova{i}.quill{i}@example.invalid",
        "username": f"nquill{i}",
    }


def test_consistent_invented_personas_score_full_when_declared(work, gold):
    mapping = {e.entity_id: _invented(i) for i, e in enumerate(gold.entities, start=1)}
    oracle.apply(work, gold, mapping)
    (work / "mapping.json").write_text(json.dumps(list(mapping.values())))
    r = run(work, gold)
    assert r.score == pytest.approx(1.0), r.failures
    assert r.diagnostics["n_declared_personas"] == len(gold.entities)


def test_invented_personas_without_mapping_file_score_zero(work, gold):
    mapping = {e.entity_id: _invented(i) for i, e in enumerate(gold.entities, start=1)}
    oracle.apply(work, gold, mapping)
    r = run(work, gold)
    assert r.components["entity_recall"] == pytest.approx(1.0)
    assert r.components["continuity"] == 0.0
    assert r.score == 0.0


def test_missed_email(work, gold, personas):
    o = first_occ(gold, lambda o: o.form == "email")
    oracle.apply(work, gold, oracle.assignment(gold, personas), skip={o.occ_id})
    r = run(work, gold)
    assert r.diagnostics["n_leaked"] == 1
    assert r.components["entity_recall"] < 1.0
    assert r.components["continuity"] == pytest.approx(1.0)
    assert 0.0 < r.score < 1.0


def test_missed_free_text_alias(work, gold, personas):
    o = first_occ(gold, lambda o: o.span is not None)
    oracle.apply(work, gold, oracle.assignment(gold, personas), skip={o.occ_id})
    r = run(work, gold)
    assert r.diagnostics["occurrence_recall_free_text"] < 1.0
    assert r.diagnostics["occurrence_recall_structured"] == pytest.approx(1.0)
    assert r.components["entity_recall"] < 1.0


def test_inconsistent_replacement_across_files(work, gold, personas):
    mapping = oracle.assignment(gold, personas)
    spare = personas[len(gold.entities)]  # a pool persona no entity uses
    e = gold.entities[0]
    o = first_occ(gold, lambda o: o.entity_id == e.entity_id and o.file == "data/comments.jsonl")
    oracle.apply(work, gold, mapping, override={o.occ_id: oracle.render(spare, o.form)})
    r = run(work, gold)
    assert r.components["entity_recall"] == pytest.approx(1.0)
    assert r.components["continuity"] < 1.0
    assert any(f["entity"] == e.entity_id for f in r.failures["continuity"])


def test_two_entities_merged(work, gold, personas):
    mapping = oracle.assignment(gold, personas)
    a, b = gold.entities[0], gold.entities[1]
    mapping[b.entity_id] = mapping[a.entity_id]
    oracle.apply(work, gold, mapping)
    r = run(work, gold)
    assert r.components["entity_recall"] == pytest.approx(1.0)
    assert r.diagnostics["separation"] < 1.0
    bad = {f["entity"] for f in r.failures["continuity"]}
    assert {a.entity_id, b.entity_id} <= bad


def test_redact_everything_scores_zero(work, gold, personas):
    override = {o.occ_id: "[REDACTED]" for o in gold.occurrences}
    oracle.apply(work, gold, oracle.assignment(gold, personas), override=override)
    r = run(work, gold)
    assert r.components["entity_recall"] == pytest.approx(1.0)
    assert r.components["continuity"] == 0.0
    assert r.score == 0.0


def test_partial_name_replacement_is_a_leak(work, gold, personas):
    mapping = oracle.assignment(gold, personas)
    o = first_occ(gold, lambda o: o.form == "full_name" and o.span is None)
    e = gold.entity(o.entity_id)
    partial = f"{mapping[e.entity_id]['first_name']} {e.tokens['names'][1]}"  # keeps the real surname
    oracle.apply(work, gold, mapping, override={o.occ_id: partial})
    r = run(work, gold)
    assert r.diagnostics["n_leaked"] == 1
    assert r.components["entity_recall"] < 1.0


def test_wrong_form_breaks_continuity(work, gold, personas):
    mapping = oracle.assignment(gold, personas)
    o = first_occ(gold, lambda o: o.form == "email")
    oracle.apply(work, gold, mapping, override={o.occ_id: mapping[o.entity_id]["full_name"]})
    r = run(work, gold)
    assert r.components["entity_recall"] == pytest.approx(1.0)
    assert r.diagnostics["form_accuracy"] < 1.0
    assert r.components["continuity"] < 1.0


def test_overscrubbing_non_pii_text(work, gold):
    oracle.clean(work, gold)
    rows = read_jsonl(work / "data/issues.jsonl")
    rows[0]["summary"] = "[REDACTED]"
    write_jsonl(work / "data/issues.jsonl", rows)
    r = run(work, gold)
    assert r.components["precision"] < 1.0
    assert r.components["integrity"] == pytest.approx(1.0)


def test_overscrubbing_free_text_around_pii(work, gold):
    oracle.clean(work, gold)
    rows = read_jsonl(work / "data/commits.jsonl")
    o = first_occ(gold, lambda o: o.file == "data/commits.jsonl" and o.path == "message")
    rec = next(r for r in rows if r["hash"] == o.key)
    rec["message"] = rec["message"].replace("ZOOKEEPER", "PROJECT", 1)
    write_jsonl(work / "data/commits.jsonl", rows)
    r = run(work, gold)
    assert r.components["precision"] < 1.0
    assert r.components["entity_recall"] == pytest.approx(1.0)
    assert r.components["continuity"] == pytest.approx(1.0)


def test_bot_account_rewritten_is_overscrub(work, gold, personas):
    if not gold.distractors:
        pytest.skip("task has no bot distractors")
    oracle.clean(work, gold)
    rows = read_jsonl(work / "data/comments.jsonl")
    bot = next(r for r in rows if r["author"]["display_name"] == gold.distractors[0]["value"])
    bot["author"]["display_name"] = personas[-1]["full_name"]
    write_jsonl(work / "data/comments.jsonl", rows)
    r = run(work, gold)
    assert r.components["precision"] < 1.0
    assert r.diagnostics["distractor_preservation"] < 1.0


def test_broken_json_fails_gate(work, gold):
    oracle.clean(work, gold)
    p = work / "data/comments.jsonl"
    p.write_text(p.read_text()[:-40] + "\n")
    r = run(work, gold)
    assert not r.gate
    assert r.score == 0.0


def test_deleted_record_fails_gate(work, gold):
    oracle.clean(work, gold)
    rows = read_jsonl(work / "data/comments.jsonl")
    write_jsonl(work / "data/comments.jsonl", rows[1:])
    r = run(work, gold)
    assert not r.gate
    assert r.components["integrity"] < 1.0
    assert r.score == 0.0


def test_broken_reference_fails_link_gate(work, gold):
    """A changed or dangling foreign key fails the gate; it does not merely lower integrity."""
    oracle.clean(work, gold)
    rows = read_jsonl(work / "data/comments.jsonl")
    rows[0]["issue_key"] = "ZOOKEEPER-0"
    write_jsonl(work / "data/comments.jsonl", rows)
    r = run(work, gold)
    assert r.diagnostics["gate_schema"] and not r.diagnostics["gate_links"]
    assert r.components["integrity"] < 1.0
    assert r.score == 0.0
    assert 0.0 < r.diagnostics["score_v1"] < 1.0


def test_repointed_reference_to_existing_record_fails_link_gate(work, gold):
    """Pointing a comment at another real issue keeps the key resolvable but still breaks the link."""
    oracle.clean(work, gold)
    issues = [r["issue_key"] for r in read_jsonl(work / "data/issues.jsonl")]
    rows = read_jsonl(work / "data/comments.jsonl")
    rows[0]["issue_key"] = next(k for k in issues if k != rows[0]["issue_key"])
    write_jsonl(work / "data/comments.jsonl", rows)
    r = run(work, gold)
    assert not r.diagnostics["gate_links"]
    assert r.score == 0.0


def test_destroyed_prose_fails_text_gate(work, gold, personas):
    """Keeping only the pseudonyms and deleting the surrounding text is not a usable export."""
    mapping = oracle.assignment(gold, personas)
    oracle.apply(work, gold, mapping)
    spans: dict = {}
    for o in gold.occurrences:
        if o.span:
            spans.setdefault((o.file, o.key, o.path), []).append(o)
    for rel, spec in gold.files.items():
        rows = read_jsonl(work / rel)
        for row in rows:
            for tf in spec.get("text_fields", []):
                if isinstance(row.get(tf), str):
                    occs = sorted(spans.get((rel, str(row[spec["key"]]), tf), []), key=lambda o: o.span[0])
                    row[tf] = " ".join(render_persona(mapping[o.entity_id], o.form) for o in occs)
        write_jsonl(work / rel, rows)
    r = run(work, gold)
    assert r.diagnostics["text_preservation"] < 0.2
    assert not r.diagnostics["gate_text"]
    assert r.score == 0.0


@pytest.mark.parametrize("sep", [" (originally ", "\n", "\n\n"])
def test_name_copied_into_other_field_is_residual_leak(work, gold, sep):
    """Also after an escaped newline, which raw-JSON scanning would miss (``\\nName``)."""
    oracle.clean(work, gold)
    e = gold.entities[0]
    rows = read_jsonl(work / "data/issues.jsonl")
    rows[0]["description"] += f"{sep}{e.tokens['names'][0]}"
    write_jsonl(work / "data/issues.jsonl", rows)
    r = run(work, gold)
    assert r.components["entity_recall"] < 1.0
    assert r.diagnostics["n_residual_token_hits"] >= 1
