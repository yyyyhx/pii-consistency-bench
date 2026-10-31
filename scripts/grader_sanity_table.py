"""Score a task under controlled, intentionally flawed cleanings (the table in docs/DESIGN.md §6).

    python scripts/grader_sanity_table.py [--task t01-zookeeper-directory]
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from piibench.cleaners import oracle  # noqa: E402
from piibench.gold import Gold, read_jsonl, write_jsonl  # noqa: E402
from piibench.grader import grade  # noqa: E402
from piibench.personas import render_persona  # noqa: E402


def invented(i: int, username_is_first: bool = False) -> dict:
    base = ["Arden", "Bryn", "Corin", "Dace", "Elric", "Fallon", "Gale", "Hollis", "Idris", "Jorah", "Kestrel",
            "Lark", "Marlo", "Nell", "Orrin"]
    first, last = base[(i - 1) % len(base)] + "abcdefgh"[(i - 1) // len(base)] * ((i - 1) // len(base) > 0), f"Vantrel{i}"
    user = first.lower() if username_is_first else f"{first[0].lower()}{last.lower()}"
    return {"persona_id": f"X{i}", "full_name": f"{first} {last}", "first_name": first, "last_name": last,
            "email": f"{first.lower()}.{last.lower()}@example.invalid", "username": user}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="t01-zookeeper-directory")
    a = ap.parse_args()
    td = ROOT / "tasks" / a.task
    g = Gold.load(td / "gold.json")
    personas = json.loads((td / "input" / "pseudonyms.json").read_text())
    m = oracle.assignment(g, personas)
    spare = personas[len(g.entities)]

    def ws() -> Path:
        d = Path(tempfile.mkdtemp()) / "ws"
        shutil.copytree(td / "input", d)
        return d

    def edit(d: Path, rel: str, fn) -> None:
        rows = read_jsonl(d / rel)
        fn(rows)
        write_jsonl(d / rel, rows)

    first_email = next(o for o in g.occurrences if o.form == "email")
    first_span = next(o for o in g.occurrences if o.span)
    other_file = next(o for o in g.occurrences if o.entity_id == g.entities[0].entity_id and "comments" in o.file)
    iss = next(f for f in g.files if "issues" in f or "bugs" in f)
    cmt = next(f for f in g.files if "comments" in f)
    fk = next(iter(g.files[cmt]["fk"]))
    merged = dict(m)
    merged[g.entities[1].entity_id] = m[g.entities[0].entity_id]
    rnd = random.Random(0)
    inv = {e.entity_id: invented(i) for i, e in enumerate(g.entities, 1)}
    inv_u = {e.entity_id: invented(i, True) for i, e in enumerate(g.entities, 1)}
    spans: dict = {}
    for o in g.occurrences:
        if o.span:
            spans.setdefault((o.file, o.key, o.path), []).append(o)

    def declare(d: Path, mapping: dict, extra=None) -> None:
        oracle.apply(d, g, mapping)
        rows = list(mapping.values()) if extra is None else extra
        (d / "mapping.json").write_text(json.dumps(rows))

    def repoint_all(d: Path) -> None:
        for rel, spec in g.files.items():
            for path, target in spec.get("fk", {}).items():
                if "*" in path:
                    continue
                keys = [r[g.files[target]["key"]] for r in read_jsonl(d / target)]
                edit(d, rel, lambda rows, path=path, keys=keys: [r.__setitem__(path, next(k for k in keys if k != r[path]))
                                                                 for r in rows if r.get(path) is not None and len(keys) > 1])

    def keep_only_pseudonyms(d: Path) -> None:
        oracle.apply(d, g, m)
        for rel, spec in g.files.items():
            def f(rows, rel=rel, spec=spec):
                for r in rows:
                    for tf in spec.get("text_fields", []):
                        if isinstance(r.get(tf), str):
                            occs = sorted(spans.get((rel, str(r[spec["key"]]), tf), []), key=lambda o: o.span[0])
                            r[tf] = " ".join(render_persona(m[o.entity_id], o.form) for o in occs)
            edit(d, rel, f)

    full = next(o for o in g.occurrences if o.form == "full_name")

    scenarios = [
        ("no-op", lambda d: None),
        ("gold applied", lambda d: oracle.clean(d, g)),
        ("gold applied, other persona bijection", lambda d: oracle.clean(d, g, seed=9)),
        ("one missed email", lambda d: oracle.apply(d, g, m, skip={first_email.occ_id})),
        ("one missed free-text alias", lambda d: oracle.apply(d, g, m, skip={first_span.occ_id})),
        ("one occurrence -> other persona (other file)",
         lambda d: oracle.apply(d, g, m, override={other_file.occ_id: oracle.render(spare, other_file.form)})),
        ("two entities merged into one persona", lambda d: oracle.apply(d, g, merged)),
        ("redact everything", lambda d: oracle.apply(d, g, m, override={o.occ_id: "[REDACTED]" for o in g.occurrences})),
        ("random persona per occurrence", lambda d: oracle.apply(
            d, g, m, override={o.occ_id: oracle.render(rnd.choice(personas), o.form) for o in g.occurrences})),
        ("structured fields only", lambda d: oracle.apply(d, g, m, skip={o.occ_id for o in g.occurrences if o.span})),
        ("gold + one summary redacted", lambda d: (oracle.clean(d, g),
                                                   edit(d, iss, lambda r: r[0].__setitem__("summary", "[REDACTED]")))),
        ("gold + one broken foreign key", lambda d: (oracle.clean(d, g),
                                                     edit(d, cmt, lambda r: r[0].__setitem__(fk, "BROKEN-0")))),
        ("gold + truncated JSONL", lambda d: (oracle.clean(d, g), (d / cmt).write_text((d / cmt).read_text()[:-40] + "\n"))),
        ("gold + every foreign key repointed to another real record", lambda d: (oracle.clean(d, g), repoint_all(d))),
        ("pseudonyms kept, all other prose deleted", keep_only_pseudonyms),
        ("invented personas, declared in mapping.json", lambda d: declare(d, inv)),
        ("invented personas, username = first name, declared", lambda d: declare(d, inv_u)),
        ("invented personas, not declared", lambda d: oracle.apply(d, g, inv)),
        ("invented personas, mapping.json also lists real names", lambda d: declare(
            d, inv, [dict(v, original=" ".join(e.tokens["names"])) for e, v in zip(g.entities, inv.values())])),
        ("one full name written without its space (real name kept)",
         lambda d: oracle.apply(d, g, m, override={full.occ_id: full.value.replace(" ", "")})),
    ]
    print(f"Task {a.task}: {len(g.entities)} entities, {len(g.occurrences)} occurrences\n")
    print("| Cleaning | score | score with schema gate only | ER | CC | P | RI | text kept | occurrence recall |")
    print("|---|---|---|---|---|---|---|---|---|")
    for name, fn in scenarios:
        d = ws()
        fn(d)
        r = grade(td / "input", d, g)
        c, x = r.components, r.diagnostics
        why = [k.split("_")[1] for k in ("gate_schema", "gate_links", "gate_text") if not x[k]]
        gate = f" ({', '.join(why)} gate)" if why else ""
        print(f"| {name} | {r.score:.3f}{gate} | {x['score_v1']:.3f} | {c['entity_recall']:.2f} | {c['continuity']:.2f} "
              f"| {c['precision']:.3f} | {c['integrity']:.3f} | {x['text_preservation']:.2f} | {x['occurrence_recall']:.3f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
