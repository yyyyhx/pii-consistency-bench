"""The scored examples in docs/WALKTHROUGH.md: t09 cleaned correctly, then with one mistake at a time.

    python scripts/walkthrough_t09.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from piibench.cleaners import oracle  # noqa: E402
from piibench.gold import Gold, read_jsonl, write_jsonl  # noqa: E402
from piibench.grader import grade  # noqa: E402

TASK = ROOT / "tasks" / "t09-storm-linked-accounts"


def main() -> int:
    g = Gold.load(TASK / "gold.json")
    personas = json.loads((TASK / "input" / "pseudonyms.json").read_text())
    m = oracle.assignment(g, personas)
    spare = personas[len(g.entities)]
    occ = {o.occ_id: o for o in g.occurrences}
    names = {e.entity_id: e.aliases[0].value if e.aliases[0].form == "full_name" else
             next(a.value for a in e.aliases if a.form == "full_name") for e in g.entities}

    handle = [o.occ_id for o in g.occurrences if o.entity_id == "E06" and o.value == "silentpuffin80"]
    bot_notice = next(o.occ_id for o in g.occurrences if o.entity_id == "E06" and o.file == "data/comments.jsonl")
    thanks = [o.occ_id for o in g.occurrences if o.entity_id == "E18" and o.form == "first_name"]
    signature = next(o.occ_id for o in g.occurrences if o.entity_id == "E05" and o.form == "email_obfuscated")
    local = m["E05"]["email"].split("@")[0]
    domain = occ[signature].value.split(" at ", 1)[1]

    def repoint_all(d: Path) -> None:
        for rel, spec in g.files.items():
            for path, target in spec.get("fk", {}).items():
                if "*" in path:
                    continue
                keys = [r[g.files[target]["key"]] for r in read_jsonl(d / target)]
                rows = read_jsonl(d / rel)
                for r in rows:
                    if r.get(path) is not None and len(keys) > 1:
                        r[path] = next(k for k in keys if k != r[path])
                write_jsonl(d / rel, rows)

    def with_real_names(d: Path) -> None:
        oracle.apply(d, g, m)
        (d / "mapping.json").write_text(json.dumps([dict(m[e], original=names[e]) for e in m]))

    scenarios = [
        ("correct", lambda d: oracle.apply(d, g, m)),
        ("`silentpuffin80` treated as a separate person (its own persona)",
         lambda d: oracle.apply(d, g, m, override={i: spare["username"] for i in handle})),
        ("both Seraphinas given Seraphina Ostrowski's persona",
         lambda d: oracle.apply(d, g, m, override={i: m["E06"]["first_name"] for i in thanks})),
        ("handle left in the bot notice", lambda d: oracle.apply(d, g, m, skip={bot_notice})),
        ("obfuscated address: persona's name, original mail domain",
         lambda d: oracle.apply(d, g, m, override={signature: f"{local} at {domain}"})),
        ("correct files, but `mapping.json` also lists the real names", with_real_names),
        ("every foreign key repointed to another real record", lambda d: (oracle.apply(d, g, m), repoint_all(d))),
    ]
    print(f"t09: {len(g.entities)} people, {len(g.occurrences)} occurrences; E06 -> {m['E06']['full_name']}, "
          f"E18 -> {m['E18']['full_name']}, E05 -> {m['E05']['full_name']}\n")
    print("| cleaning | score | strict (min) | ER | CC | separation | people fully right | solved |")
    print("|---|---|---|---|---|---|---|---|")
    for name, fn in scenarios:
        d = Path(tempfile.mkdtemp()) / "ws"
        shutil.copytree(TASK / "input", d)
        fn(d)
        r = grade(TASK / "input", d, g)
        c, x = r.components, r.diagnostics
        why = [k.split("_")[1] for k in ("gate_schema", "gate_links", "gate_text") if not x[k]]
        solved = "yes" if x["solved"] else "no" + (f" ({', '.join(why)} gate)" if why else "")
        print(f"| {name} | {r.score:.3f} | {x['min_component']:.3f} | {c['entity_recall']:.2f} | "
              f"{c['continuity']:.2f} | {x['separation']:.2f} | {x['entity_success']:.2f} | {solved} |")
        shutil.rmtree(d.parent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
