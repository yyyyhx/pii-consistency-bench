"""Gold manifest structures and record/path utilities shared by the generator and the grader."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .personas import FORM_CLASS


def get_path(record: Any, path: str) -> Any:
    cur = record
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur


def set_path(record: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = record
    for part in parts[:-1]:
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        cur[last] = value


def leaf_paths(record: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a JSON value into dotted leaf paths (lists indexed)."""
    out: dict[str, Any] = {}
    if isinstance(record, dict):
        if not record and prefix:
            out[prefix] = record
        for k, v in record.items():
            out.update(leaf_paths(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(record, list):
        if not record and prefix:
            out[prefix] = record
        for i, v in enumerate(record):
            out.update(leaf_paths(v, f"{prefix}.{i}" if prefix else str(i)))
    else:
        out[prefix] = record
    return out


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError(f"{path.name}:{i + 1}: not a JSON object")
        rows.append(obj)
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


@dataclass
class Alias:
    alias_id: str
    form: str
    value: str


@dataclass
class Entity:
    entity_id: str
    source_ref: str
    tokens: dict[str, list[str]]
    aliases: list[Alias] = field(default_factory=list)

    def alias(self, form: str, value: str) -> Alias:
        for a in self.aliases:
            if a.form == form and a.value == value:
                return a
        a = Alias(alias_id=f"{self.entity_id}.a{len(self.aliases) + 1}", form=form, value=value)
        self.aliases.append(a)
        return a


@dataclass
class Occurrence:
    occ_id: str
    entity_id: str
    alias_id: str
    form: str
    value: str
    file: str
    key: str
    path: str
    span: list[int] | None
    surface: str
    synthetic: bool
    accept: list[str]


@dataclass
class Gold:
    task_id: str
    seed: int
    generator_version: str
    provenance: dict
    files: dict[str, dict]
    entities: list[Entity] = field(default_factory=list)
    occurrences: list[Occurrence] = field(default_factory=list)
    distractors: list[dict] = field(default_factory=list)

    def entity(self, entity_id: str) -> Entity:
        for e in self.entities:
            if e.entity_id == entity_id:
                return e
        raise KeyError(entity_id)

    def add(self, entity: Entity, form: str, value: str, file: str, key: str, path: str,
            span: tuple[int, int] | None, surface: str, synthetic: bool, accept: list[str] | None = None) -> Occurrence:
        alias = entity.alias(form, value)
        occ = Occurrence(
            occ_id=f"O{len(self.occurrences) + 1:04d}", entity_id=entity.entity_id, alias_id=alias.alias_id,
            form=form, value=value, file=file, key=str(key), path=path,
            span=list(span) if span else None, surface=surface, synthetic=synthetic,
            accept=accept or [FORM_CLASS[form]],
        )
        self.occurrences.append(occ)
        return occ

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> Gold:
        ents = [Entity(entity_id=e["entity_id"], source_ref=e["source_ref"], tokens=e["tokens"],
                       aliases=[Alias(**a) for a in e["aliases"]]) for e in d["entities"]]
        occs = [Occurrence(**o) for o in d["occurrences"]]
        return cls(task_id=d["task_id"], seed=d["seed"], generator_version=d["generator_version"],
                   provenance=d["provenance"], files=d["files"], entities=ents, occurrences=occs,
                   distractors=d.get("distractors", []))

    @classmethod
    def load(cls, path: Path) -> Gold:
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))


class TextBuilder:
    """Builds a free-text value while recording the character spans of injected mentions."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._len = 0
        self.mentions: list[tuple[int, int, Entity, str, str, bool, list[str] | None]] = []

    def lit(self, s: str) -> TextBuilder:
        self._parts.append(s)
        self._len += len(s)
        return self

    def mention(self, entity: Entity, form: str, value: str, synthetic: bool = True,
                accept: list[str] | None = None) -> TextBuilder:
        self.mentions.append((self._len, self._len + len(value), entity, form, value, synthetic, accept))
        return self.lit(value)

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def register(self, gold: Gold, file: str, key: str, path: str, surface: str) -> str:
        for start, end, ent, form, value, synthetic, accept in self.mentions:
            gold.add(ent, form, value, file, key, path, (start, end), surface, synthetic, accept)
        return self.text
