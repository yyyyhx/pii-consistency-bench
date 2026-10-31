"""Export the tasks as a Hugging Face dataset (JSON Lines plus a dataset card).

    python scripts/export_hf.py [--out hf]

Writes two configurations, each with a ``main`` and a ``hard`` split:

* ``tasks``: one row per task. It holds everything needed to run the task (the instructions, the persona pool and
  every data file) and its gold manifest, so a task can be rebuilt on disk from a single row.
* ``occurrences``: one row per occurrence of a person in the data, with its location (file, record, field and
  character span), the alias form, and the replacement forms the slot accepts.

The dataset card is copied from ``hf_card.md`` next to this script.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPLITS = {"main": "tasks", "hard": "tasks_hard"}


def task_row(td: Path, split: str) -> dict:
    gold = json.loads((td / "gold.json").read_text(encoding="utf-8"))
    audit = json.loads((td / "audit.json").read_text(encoding="utf-8"))
    pv = gold["provenance"]
    files = [{"path": str(p.relative_to(td / "input" / "data")), "content": p.read_text(encoding="utf-8")}
             for p in sorted((td / "input" / "data").glob("*.jsonl"))]
    return {
        "task_id": td.name,
        "split": split,
        "source": "20-MAD" if pv["source"].startswith("20-MAD") else "SmartSHARK",
        "project": pv.get("project", ""),
        "num_people": audit["entities"],
        "num_records": sum(audit["records"].values()),
        "num_occurrences": audit["occurrences"],
        "num_occurrences_free_text": audit["occurrences_free_text"],
        "instructions": (td / "input" / "TASK.md").read_text(encoding="utf-8"),
        "personas": json.loads((td / "input" / "pseudonyms.json").read_text(encoding="utf-8")),
        "files": files,
        "gold": (td / "gold.json").read_text(encoding="utf-8"),  # verbatim, so a materialized task is identical
    }


def occurrence_rows(td: Path, split: str) -> list[dict]:
    gold = json.loads((td / "gold.json").read_text(encoding="utf-8"))
    rows = []
    for o in gold["occurrences"]:
        span = o.get("span")
        rows.append({
            "task_id": td.name, "split": split, "occ_id": o["occ_id"], "entity_id": o["entity_id"],
            "alias_id": o["alias_id"], "form": o["form"], "value": o["value"], "file": o["file"],
            "record_key": str(o["key"]), "field_path": o["path"], "surface": o["surface"],
            "span_start": span[0] if span else None, "span_end": span[1] if span else None,
            "free_text": span is not None, "accept": o["accept"], "synthetic_mention": bool(o.get("synthetic")),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "hf"))
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    counts = {}
    for split, folder in SPLITS.items():
        tds = sorted(p for p in (ROOT / folder).iterdir() if (p / "gold.json").exists())
        for config, rows in (("tasks", [task_row(td, split) for td in tds]),
                             ("occurrences", [r for td in tds for r in occurrence_rows(td, split)])):
            path = out / "data" / config / f"{split}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
            counts[f"{config}/{split}"] = len(rows)
    shutil.copy(Path(__file__).with_name("hf_card.md"), out / "README.md")
    for k, v in counts.items():
        print(f"{k:22s} {v:6d} rows")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
