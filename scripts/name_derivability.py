"""How much of each task can be resolved by string matching alone (docs/DESIGN.md section 8).

    python scripts/name_derivability.py

An alias or occurrence counts as name-derivable when it contains the first four letters (accents and case folded) of
one of the person's names, and no other person in the task has a name starting with those four letters.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from piibench.grader.leaks import fold  # noqa: E402


def main() -> int:
    total = {"t01-t07": [0, 0, 0, 0]}
    print("| task | aliases name-derivable | occurrences name-derivable |\n|---|---|---|")
    for td in [p for root in ("tasks", "tasks_hard") if (ROOT / root).is_dir()
               for p in sorted((ROOT / root).iterdir()) if (p / "gold.json").exists()]:
        g = json.loads((td / "gold.json").read_text())
        owners: dict[str, set[str]] = {}
        for e in g["entities"]:
            for n in e["tokens"]["names"]:
                if len(n) >= 4:
                    owners.setdefault(fold(n)[:4], set()).add(e["entity_id"])
        own = {e["entity_id"]: {fold(n)[:4] for n in e["tokens"]["names"]
                                if len(n) >= 4 and len(owners[fold(n)[:4]]) == 1} for e in g["entities"]}
        aliases = [(e["entity_id"], a["value"]) for e in g["entities"] for a in e["aliases"]]
        a_ok = sum(any(p in fold(v) for p in own[eid]) for eid, v in aliases)
        o_ok = sum(any(p in fold(o["value"]) for p in own[o["entity_id"]]) for o in g["occurrences"])
        n_occ = len(g["occurrences"])
        print(f"| {td.name} | {a_ok}/{len(aliases)} ({a_ok / len(aliases):.0%}) | {o_ok}/{n_occ} ({o_ok / n_occ:.0%}) |")
        if td.parent.name == "tasks" and td.name[:3] <= "t07":
            for i, v in enumerate((a_ok, len(aliases), o_ok, n_occ)):
                total["t01-t07"][i] += v
    a, na, o, no = total["t01-t07"]
    print(f"| t01-t07 | {a}/{na} ({a / na:.0%}) | {o}/{no} ({o / no:.0%}) |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
