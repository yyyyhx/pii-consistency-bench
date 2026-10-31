"""Schema-aware rule-based pseudonymizer (non-LLM baseline).

1. Find person objects by shape: ``{name, email}`` (git identity), ``{username, display_name}``
   (tracker account), directory records ``{display_name, emails, accounts}``.
2. Union-find over the identifiers that co-occur in one person object, identical strings across
   surfaces, and email local part == username.
3. Assign one pool persona per cluster (order of first appearance).
4. Replace structured values by lookup; optionally replace every known alias string in free text
   (full names, first names, surnames, "F. Last", emails, usernames) with word boundaries.

It never sees the gold manifest. Its blind spots are by design those of real rule systems:
aliases that only appear in free text, nicknames, and different people with identical names.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..gold import read_jsonl, write_jsonl

BOT_RE = re.compile(r"\b(QA|Bot|Hudson|Jenkins|CI|Automation)\b|^(hudson|jenkins|hadoopqa|githubbot)$|"
                    r"^nobody@|^Nobody;", re.I)
INITIAL_RE = re.compile(r"^[A-Z]\.\s+[A-Z][\w'-]+$")
EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
BZ_DISPLAY_RE = re.compile(r"^(?P<name>.*?)\s*\[:(?P<nick>[\w.-]+)\]$")


class _UF:
    def __init__(self) -> None:
        self.parent: dict = {}
        self.order: dict = {}

    def add(self, x) -> None:
        if x not in self.parent:
            self.parent[x] = x
            self.order[x] = len(self.order)

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b) -> None:
        self.add(a)
        self.add(b)
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if self.order[ra] > self.order[rb]:
                ra, rb = rb, ra
            self.parent[rb] = ra


def _node(kind: str, value: str):
    return (kind, value.lower() if kind in ("email", "username") else value)


def _name_kind(value: str) -> str:
    if EMAIL_RE.match(value):
        return "email"
    if " " not in value.strip() and value == value.lower():
        return "username"
    return "name"


def _login_kind(value: str) -> str:
    return "email" if EMAIL_RE.match(value) else "username"


def _is_bot(obj: dict) -> bool:
    return any(isinstance(v, str) and BOT_RE.search(v) for v in obj.values())


PERSON_FIELDS = ("name", "email", "username", "display_name", "login")


def _is_person_obj(value: dict) -> bool:
    keys = set(value)
    return ({"name", "email"} <= keys or {"username", "display_name"} <= keys or {"display_name", "emails"} <= keys
            or {"login", "display_name"} <= keys)


def _display_nodes(display: str) -> list:
    m = BZ_DISPLAY_RE.match(display)
    if m:
        return [_node(_name_kind(m["name"]), m["name"]), _node("username", m["nick"])]
    return [_node(_name_kind(display), display)]


def _person_objects(value, out: list) -> None:
    if isinstance(value, dict):
        if _is_person_obj(value):
            out.append(value)
        for v in value.values():
            _person_objects(v, out)
    elif isinstance(value, list):
        for v in value:
            _person_objects(v, out)


def clean(workdir: Path, free_text: bool = True) -> dict:
    workdir = Path(workdir)
    personas = json.loads((workdir / "pseudonyms.json").read_text(encoding="utf-8"))
    files = sorted((workdir / "data").glob("*.jsonl"))
    tables = {f: read_jsonl(f) for f in files}

    uf = _UF()
    for rows in tables.values():
        for row in rows:
            objs: list = []
            _person_objects(row, objs)
            for obj in objs:
                if _is_bot(obj):
                    continue
                nodes = []
                if "name" in obj and isinstance(obj["name"], str):
                    nodes.append(_node(_name_kind(obj["name"]), obj["name"]))
                if "email" in obj and isinstance(obj["email"], str):
                    nodes.append(_node("email", obj["email"]))
                if "username" in obj and isinstance(obj["username"], str):
                    nodes.append(_node("username", obj["username"]))
                if "login" in obj and isinstance(obj["login"], str):  # Bugzilla login email or GitHub handle
                    nodes.append(_node(_login_kind(obj["login"]), obj["login"]))
                if "display_name" in obj and isinstance(obj["display_name"], str):
                    nodes += _display_nodes(obj["display_name"])
                for em in obj.get("emails", []) or []:
                    nodes.append(_node("email", em))
                for n in nodes:
                    uf.add(n)
                for n in nodes[1:]:
                    uf.union(nodes[0], n)
    # email local part == username
    usernames = {v for (k, v) in uf.parent if k == "username"}
    for (k, v) in list(uf.parent):
        if k == "email" and v.split("@")[0] in usernames:
            uf.union((k, v), ("username", v.split("@")[0]))

    clusters: dict = {}
    for n in sorted(uf.parent, key=lambda n: uf.order[n]):
        clusters.setdefault(uf.find(n), []).append(n)
    assign = {}
    for i, root in enumerate(clusters):
        assign[root] = personas[i % len(personas)]

    structured: dict = {}  # (kind, normalized) -> replacement
    text_map: dict[str, tuple[str, bool]] = {}  # alias string -> (replacement, case_insensitive)
    for root, nodes in clusters.items():
        p = assign[root]
        for kind, value in nodes:
            if kind == "email":
                rep = p["email"]
            elif kind == "username":
                rep = p["username"]
            elif INITIAL_RE.match(value):
                rep = f"{p['first_name'][0]}. {p['last_name']}"
            else:
                rep = p["full_name"]
            structured[(kind, value)] = rep
            text_map[value] = (rep, kind != "name")
            if kind == "name" and not INITIAL_RE.match(value):
                parts = value.split()
                if len(parts) >= 2:
                    text_map.setdefault(parts[0], (p["first_name"], False))
                    text_map.setdefault(parts[-1], (p["last_name"], False))
            if kind == "name" and INITIAL_RE.match(value):
                text_map.setdefault(value.split()[-1], (p["last_name"], False))

    def sub_structured(obj: dict) -> None:
        if _is_bot(obj):
            return
        if isinstance(obj.get("name"), str):
            obj["name"] = structured.get(_node(_name_kind(obj["name"]), obj["name"]), obj["name"])
        if isinstance(obj.get("display_name"), str):
            disp = obj["display_name"]
            m = BZ_DISPLAY_RE.match(disp)
            if m:
                name = structured.get(_node(_name_kind(m["name"]), m["name"]), m["name"])
                nick = structured.get(_node("username", m["nick"]), m["nick"])
                obj["display_name"] = f"{name} [:{nick}]"
            else:
                obj["display_name"] = structured.get(_node(_name_kind(disp), disp), disp)
        if isinstance(obj.get("email"), str):
            obj["email"] = structured.get(_node("email", obj["email"]), obj["email"])
        if isinstance(obj.get("login"), str):
            obj["login"] = structured.get(_node(_login_kind(obj["login"]), obj["login"]), obj["login"])
        if isinstance(obj.get("username"), str):
            obj["username"] = structured.get(_node("username", obj["username"]), obj["username"])
        if isinstance(obj.get("emails"), list):
            obj["emails"] = [structured.get(_node("email", e), e) for e in obj["emails"]]

    keys = sorted(text_map, key=len, reverse=True)
    rx = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(k) for k in keys) + r")(?![A-Za-z0-9])", re.I) if keys else None
    lower_map = {k.lower(): k for k in keys}

    def sub_text(s: str) -> str:
        if rx is None:
            return s

        def rep(m: re.Match) -> str:
            hit = m.group(0)
            key = hit if hit in text_map else lower_map.get(hit.lower())
            if key is None:
                return hit
            new, ci = text_map[key]
            if not ci and hit != key:
                return hit  # names are case-sensitive
            return new
        return rx.sub(rep, s)

    def walk(value) -> None:
        if isinstance(value, dict):
            person = _is_person_obj(value)
            if person:
                sub_structured(value)
            for k, v in list(value.items()):
                if isinstance(v, str):
                    if free_text and not (person and k in PERSON_FIELDS):
                        value[k] = sub_text(v)
                else:
                    walk(v)
        elif isinstance(value, list):
            for i, v in enumerate(value):
                if isinstance(v, str):
                    if free_text:
                        value[i] = sub_text(v)
                else:
                    walk(v)

    for f, rows in tables.items():
        for row in rows:
            walk(row)
        write_jsonl(f, rows)
    return {"clusters": len(clusters), "personas_used": min(len(clusters), len(personas))}
