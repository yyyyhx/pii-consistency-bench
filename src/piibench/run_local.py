"""Run the non-LLM cleaners (baselines) on the tasks and grade them."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
from pathlib import Path

from .cleaners import oracle, redact, rules
from .gold import Gold
from .grader import grade

CLEANERS = {
    "noop": lambda ws, gold: None,
    "redact": lambda ws, gold: redact.clean(ws),
    "rules-structured": lambda ws, gold: rules.clean(ws, free_text=False),
    "rules": lambda ws, gold: rules.clean(ws, free_text=True),
    "oracle": lambda ws, gold: oracle.clean(ws, gold),
}


def run_one(task_dir: Path, cleaner: str) -> dict:
    gold = Gold.load(task_dir / "gold.json")
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "workspace"
        shutil.copytree(task_dir / "input", ws)
        t0 = time.perf_counter()
        CLEANERS[cleaner](ws, gold)
        elapsed = time.perf_counter() - t0
        rep = grade(task_dir / "input", ws, gold)
    return {"task_id": gold.task_id, "cleaner": cleaner, "elapsed_s": round(elapsed, 4), **rep.to_json()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", default="tasks")
    ap.add_argument("--cleaners", nargs="+", default=list(CLEANERS))
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--out", default=None, help="write per-run JSON reports into this directory")
    args = ap.parse_args(argv)
    task_dirs = sorted(p for p in Path(args.tasks).iterdir() if (p / "gold.json").exists())
    if args.only:
        task_dirs = [p for p in task_dirs if p.name in args.only]
    rows = []
    for td in task_dirs:
        for c in args.cleaners:
            r = run_one(td, c)
            rows.append(r)
            if args.out:
                out = Path(args.out)
                out.mkdir(parents=True, exist_ok=True)
                (out / f"{td.name}__{c}.json").write_text(json.dumps(r, indent=1))
    hdr = f"{'task':28s} {'cleaner':17s} {'score':>6s} {'ER':>5s} {'CC':>5s} {'P':>6s} {'RI':>6s} {'occR':>6s} {'txtR':>5s} {'sep':>5s} {'time':>7s}"
    print(hdr)
    for r in rows:
        c, d = r["components"], r["diagnostics"]
        txt = d["occurrence_recall_free_text"]
        print(f"{r['task_id'][:28]:28s} {r['cleaner']:17s} {r['score']:6.3f} {c['entity_recall']:5.2f} "
              f"{c['continuity']:5.2f} {c['precision']:6.3f} {c['integrity']:6.3f} {d['occurrence_recall']:6.3f} "
              f"{(txt if txt is not None else float('nan')):5.2f} {d['separation']:5.2f} {r['elapsed_s']:7.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
