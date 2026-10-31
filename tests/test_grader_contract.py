"""Grader 0.2 contract: declared personas, leak detection edge cases, shared names, and per-task invariants."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from piibench.cleaners import oracle
from piibench.gold import Gold, read_jsonl, write_jsonl
from piibench.grader import grade
from piibench.grader.leaks import entity_substring_tokens

ROOT = Path(__file__).resolve().parents[1]
TASK_DIRS = {p.name: p for root in (ROOT / "tasks", ROOT / "tasks_hard") if root.is_dir()
             for p in root.iterdir() if (p / "gold.json").exists()}
ALL_TASKS = sorted(TASK_DIRS)


def setup(task: str, tmp_path: Path) -> tuple[Gold, list[dict], Path]:
    gold = Gold.load(TASK_DIRS[task] / "gold.json")
    personas = json.loads((TASK_DIRS[task] / "input" / "pseudonyms.json").read_text())
    ws = tmp_path / task
    shutil.copytree(TASK_DIRS[task] / "input", ws)
    return gold, personas, ws


def run(task: str, ws: Path, gold: Gold):
    return grade(TASK_DIRS[task] / "input", ws, gold)


def invented(i: int, style: str = "distinct") -> dict:
    """Invented personas in naming conventions an agent might choose."""
    base = ["Arden", "Bryn", "Corin", "Dace", "Elric", "Fallon", "Gale", "Hollis", "Idris", "Jorah", "Kestrel",
            "Lark", "Marlo", "Nell", "Orrin"]
    first = base[(i - 1) % len(base)] + "abcdefgh"[(i - 1) // len(base)] * ((i - 1) // len(base) > 0)
    last = f"Vantrel{i}"
    if style == "distinct":
        return {"persona_id": f"X{i}", "full_name": f"{first} {last}", "first_name": first, "last_name": last,
                "email": f"{first.lower()}.{last.lower()}@example.invalid", "username": f"{first[0].lower()}{last.lower()}"}
    if style == "username_is_first_name":  # "Arden" / "arden": one string, two forms of one persona
        return {"persona_id": f"X{i}", "full_name": f"{first} {last}", "first_name": first, "last_name": last,
                "email": f"{first.lower()}@example.invalid", "username": first.lower()}
    raise ValueError(style)


def declare(ws: Path, gold: Gold, style: str = "distinct") -> dict:
    mapping = {e.entity_id: invented(i, style) for i, e in enumerate(gold.entities, start=1)}
    oracle.apply(ws, gold, mapping)
    (ws / "mapping.json").write_text(json.dumps(list(mapping.values())))
    return mapping


# ------------------------------------------------------------------ declared personas (mapping.json)

@pytest.mark.parametrize("task", ["t01-zookeeper-directory", "t04-hadoop-name-collision", "t08-zookeeper-pulls-mail"])
@pytest.mark.parametrize("style", ["distinct", "username_is_first_name"])
def test_consistent_declared_personas_score_full(task, style, tmp_path):
    gold, _, ws = setup(task, tmp_path)
    declare(ws, gold, style)
    r = run(task, ws, gold)
    assert r.score == pytest.approx(1.0), r.failures
    assert r.diagnostics["mapping_errors"] == []


def test_mapping_not_a_list_is_reported(tmp_path):
    task = "t01-zookeeper-directory"
    gold, _, ws = setup(task, tmp_path)
    mapping = declare(ws, gold)
    (ws / "mapping.json").write_text(json.dumps({"personas": list(mapping.values())}))
    r = run(task, ws, gold)
    assert r.score == 0.0
    assert any("JSON list" in e for e in r.diagnostics["mapping_errors"])


def test_mapping_invalid_json_is_reported(tmp_path):
    task = "t01-zookeeper-directory"
    gold, _, ws = setup(task, tmp_path)
    declare(ws, gold)
    (ws / "mapping.json").write_text("[{not json")
    r = run(task, ws, gold)
    assert r.score == 0.0
    assert any("not valid JSON" in e for e in r.diagnostics["mapping_errors"])


def test_mapping_entry_missing_field_drops_only_that_persona(tmp_path):
    task = "t01-zookeeper-directory"
    gold, _, ws = setup(task, tmp_path)
    mapping = declare(ws, gold)
    rows = list(mapping.values())
    del rows[0]["username"]
    (ws / "mapping.json").write_text(json.dumps(rows))
    r = run(task, ws, gold)
    assert any("missing username" in e for e in r.diagnostics["mapping_errors"])
    assert r.diagnostics["n_declared_personas"] == len(rows) - 1
    assert r.components["continuity"] == pytest.approx((len(rows) - 1) / len(rows))


def test_two_people_declared_as_one_identity_is_a_merge(tmp_path):
    """Two persona ids with identical strings are one identity, so both people fail continuity."""
    task = "t01-zookeeper-directory"
    gold, _, ws = setup(task, tmp_path)
    mapping = {e.entity_id: invented(i) for i, e in enumerate(gold.entities, start=1)}
    a, b = gold.entities[0].entity_id, gold.entities[1].entity_id
    mapping[b] = dict(mapping[a], persona_id="X-dup")
    oracle.apply(ws, gold, mapping)
    (ws / "mapping.json").write_text(json.dumps(list(mapping.values())))
    r = run(task, ws, gold)
    bad = {f["entity"] for f in r.failures["continuity"]}
    assert {a, b} <= bad
    assert r.diagnostics["separation"] < 1.0


def test_mapping_listing_original_names_is_a_leak(tmp_path):
    """A mapping file that records the real identities is a re-identification key shipped with the export."""
    task = "t01-zookeeper-directory"
    gold, _, ws = setup(task, tmp_path)
    mapping = {e.entity_id: invented(i) for i, e in enumerate(gold.entities, start=1)}
    oracle.apply(ws, gold, mapping)
    rows = [dict(v, original=" ".join(e.tokens["names"])) for e, v in zip(gold.entities, mapping.values())]
    (ws / "mapping.json").write_text(json.dumps(rows))
    r = run(task, ws, gold)
    assert r.diagnostics["mapping_leaks"] > 0
    assert r.components["entity_recall"] == 0.0
    assert r.score == 0.0


def test_pool_answer_with_unrelated_mapping_file_is_unaffected(tmp_path):
    task = "t01-zookeeper-directory"
    gold, _, ws = setup(task, tmp_path)
    oracle.clean(ws, gold)
    (ws / "mapping.json").write_text(json.dumps([invented(1)]))
    assert run(task, ws, gold).score == pytest.approx(1.0)


# ------------------------------------------------------------------ leak detection

def test_concatenated_real_name_is_a_leak(tmp_path):
    """'QuentinWojcik' has no word boundary around either name, so a boundary-only match would count it as clean."""
    task = "t01-zookeeper-directory"
    gold, personas, ws = setup(task, tmp_path)
    o = next(o for o in gold.occurrences if o.form == "full_name")
    oracle.apply(ws, gold, oracle.assignment(gold, personas), override={o.occ_id: o.value.replace(" ", "")})
    r = run(task, ws, gold)
    assert r.diagnostics["n_leaked"] == 1
    assert r.components["entity_recall"] < 1.0


def test_original_username_embedded_in_new_handle_is_a_leak(tmp_path):
    task = "t01-zookeeper-directory"
    gold, personas, ws = setup(task, tmp_path)
    occs = [o for o in gold.occurrences if o.form == "username" and len(o.value) >= 4]
    oracle.apply(ws, gold, oracle.assignment(gold, personas), override={o.occ_id: "x" + o.value for o in occs})
    r = run(task, ws, gold)
    assert r.diagnostics["n_leaked"] == len(occs)


@pytest.mark.parametrize("task", ALL_TASKS)
def test_no_pool_persona_embeds_an_original_identifier(task):
    """Guards the substring leak rule against false positives on correct pool answers."""
    gold = Gold.load(TASK_DIRS[task] / "gold.json")
    personas = json.loads((TASK_DIRS[task] / "input" / "pseudonyms.json").read_text())
    rendered = [v.casefold() for p in personas for v in (p["full_name"], p["email"], p["username"])]
    for e in gold.entities:
        for t in entity_substring_tokens(e):
            assert not any(t in v for v in rendered), (e.entity_id, t)


# ------------------------------------------------------------------ shared names (t04)

def test_t04_leftover_shared_first_name_is_charged_to_its_owner_only(tmp_path):
    task = "t04-hadoop-name-collision"
    gold, personas, ws = setup(task, tmp_path)
    a, b = gold.provenance["collisions"][0]
    o = next(o for o in gold.occurrences if o.entity_id == a and o.form == "first_name")
    oracle.apply(ws, gold, oracle.assignment(gold, personas), skip={o.occ_id})
    r = run(task, ws, gold)
    n = len(gold.entities)
    assert r.components["entity_recall"] == pytest.approx((n - 1) / n)  # only `a` fails
    assert r.components["continuity"] == pytest.approx(1.0)


def test_t04_stray_shared_surname_is_charged_to_both_sharers(tmp_path):
    """An unattributable shared surname could be either person, so both lose entity recall (conservative)."""
    task = "t04-hadoop-name-collision"
    gold, personas, ws = setup(task, tmp_path)
    oracle.apply(ws, gold, oracle.assignment(gold, personas))
    a, _ = gold.provenance["collisions"][0]
    surname = gold.entity(a).tokens["names"][1]
    rows = read_jsonl(ws / "data/issues.jsonl")
    rows[0]["summary"] += f" ({surname})"
    write_jsonl(ws / "data/issues.jsonl", rows)
    r = run(task, ws, gold)
    n = len(gold.entities)
    assert r.components["entity_recall"] == pytest.approx((n - 2) / n)


def test_t04_merging_the_namesakes_fails_both(tmp_path):
    task = "t04-hadoop-name-collision"
    gold, personas, ws = setup(task, tmp_path)
    mapping = oracle.assignment(gold, personas)
    a, b = gold.provenance["collisions"][0]
    mapping[b] = mapping[a]
    oracle.apply(ws, gold, mapping)
    r = run(task, ws, gold)
    assert {a, b} <= {f["entity"] for f in r.failures["continuity"]}
    assert r.diagnostics["bcubed_precision"] < 1.0
    assert r.diagnostics["bcubed_recall"] == pytest.approx(1.0)


def test_split_identity_lowers_bcubed_recall(tmp_path):
    task = "t01-zookeeper-directory"
    gold, personas, ws = setup(task, tmp_path)
    mapping = oracle.assignment(gold, personas)
    spare = personas[len(gold.entities)]
    e = gold.entities[0]
    occs = [o for o in gold.occurrences if o.entity_id == e.entity_id]
    half = {o.occ_id: oracle.render(spare, o.form) for o in occs[: len(occs) // 2]}
    oracle.apply(ws, gold, mapping, override=half)
    r = run(task, ws, gold)
    assert r.diagnostics["bcubed_recall"] < 1.0
    assert r.diagnostics["bcubed_precision"] == pytest.approx(1.0)


# ------------------------------------------------------------------ robustness and per-task invariants

def test_huge_value_is_not_aligned(tmp_path):
    task = "t01-zookeeper-directory"
    gold, _, ws = setup(task, tmp_path)
    oracle.clean(ws, gold)
    o = next(o for o in gold.occurrences if o.span is not None)
    rel = o.file
    rows = read_jsonl(ws / rel)
    key = gold.files[rel]["key"]
    rec = next(r for r in rows if str(r[key]) == o.key)
    rec[o.path] = rec[o.path] + "x" * 300_000
    write_jsonl(ws / rel, rows)
    r = run(task, ws, gold)
    assert r.components["precision"] < 1.0
    assert r.diagnostics["n_overscrub_edits"] >= 1


@pytest.mark.parametrize("task", ALL_TASKS)
def test_oracle_and_noop_on_every_task(task, tmp_path):
    gold, _, ws = setup(task, tmp_path)
    noop = run(task, ws, gold)
    assert noop.score == 0.0 and noop.gate
    oracle.clean(ws, gold, seed=7)
    r = run(task, ws, gold)
    assert r.score == pytest.approx(1.0), r.failures
    assert r.diagnostics["solved"]
    assert r.diagnostics["bcubed_precision"] == pytest.approx(1.0)
    assert r.diagnostics["bcubed_recall"] == pytest.approx(1.0)


def test_mapping_restating_pool_personas_under_other_ids_is_harmless(tmp_path):
    """An agent may write the pool personas it used into mapping.json. Restating them, even under
    new ids, must not create a second candidate persona for the same strings."""
    task = "t04-hadoop-name-collision"
    gold, personas, ws = setup(task, tmp_path)
    mapping = oracle.assignment(gold, personas)
    oracle.apply(ws, gold, mapping)
    restated = [dict(p, persona_id=f"agent-{i}") for i, p in enumerate(mapping.values())]
    (ws / "mapping.json").write_text(json.dumps(restated + restated[:2]))
    r = run(task, ws, gold)
    assert r.score == pytest.approx(1.0), r.failures
    assert r.diagnostics["mapping_errors"] == []
    assert r.diagnostics["n_declared_personas"] == 0


def test_declared_personas_sharing_a_username_are_reported(tmp_path):
    task = "t01-zookeeper-directory"
    gold, _, ws = setup(task, tmp_path)
    mapping = {e.entity_id: invented(i) for i, e in enumerate(gold.entities, start=1)}
    a, b = gold.entities[0].entity_id, gold.entities[1].entity_id
    mapping[b] = dict(mapping[b], username=mapping[a]["username"])
    oracle.apply(ws, gold, mapping)
    (ws / "mapping.json").write_text(json.dumps(list(mapping.values())))
    r = run(task, ws, gold)
    assert any("username" in e and "also persona" in e for e in r.diagnostics["mapping_errors"])
    assert r.components["continuity"] < 1.0


def test_entity_success_counts_people_both_clean_and_consistent(tmp_path):
    task = "t01-zookeeper-directory"
    gold, personas, ws = setup(task, tmp_path)
    mapping = oracle.assignment(gold, personas)
    a = gold.entities[0]
    leak = next(o for o in gold.occurrences if o.entity_id == a.entity_id)
    oracle.apply(ws, gold, mapping, skip={leak.occ_id})
    r = run(task, ws, gold)
    n = len(gold.entities)
    assert r.diagnostics["entity_success"] == pytest.approx((n - 1) / n)
    assert r.diagnostics["entities_ok"] == sorted(e.entity_id for e in gold.entities if e.entity_id != a.entity_id)


# ------------------------------------------------------------------ spelling variants

def test_fold_removes_accents_and_case():
    from piibench.grader.leaks import fold
    assert fold("Søren Jørgensen") == fold("SOREN jorgensen") == "soren jorgensen"
    assert fold("Zieliński") == "zielinski" and fold("Þorvaldsen") == "thorvaldsen" and fold("Yılmaz") == "yilmaz"


@pytest.mark.parametrize("task", ["t08-zookeeper-pulls-mail", "t09-storm-linked-accounts", "t10-curator-namesakes"])
def test_ascii_spelling_of_an_accented_name_is_a_leak(task, tmp_path):
    """Writing "Zielinski" where the record said "Zieliński" leaves the person identifiable."""
    from piibench.grader.leaks import fold
    gold, personas, ws = setup(task, tmp_path)
    accented = [o for o in gold.occurrences if o.form in ("full_name", "last_first") and fold(o.value) != o.value.casefold()]
    assert accented, "task should contain accented spellings"
    o = accented[0]
    plain = "".join(c for c in fold(o.value).title())
    oracle.apply(ws, gold, oracle.assignment(gold, personas), override={o.occ_id: plain})
    r = run(task, ws, gold)
    assert r.diagnostics["n_leaked"] == 1
    assert r.components["entity_recall"] < 1.0
