"""Command-line interface.

    piibench list                                    # the tasks
    piibench init t09 ./work                         # copy a task's agent-visible input into ./work
    piibench score t09 ./work                        # grade the cleaned files in ./work
    piibench run --cmd "python $PWD/my_cleaner.py"   # run any command on every task and grade it
    piibench baselines                               # the built-in rule baselines
    piibench check                                   # generation checks on every task
    piibench materialize tasks/main.jsonl --out DIR  # rebuild task folders from the Hugging Face export

Tasks are looked up in ``tasks/`` (main split) and ``tasks_hard/`` (hard split) of the repository, or under the
directory named by ``PIIBENCH_TASKS_ROOT``. A task can be named by its full id or by its short prefix (``t09``, ``h2``).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .gold import Gold
from .grader import grade

SPLITS = {"main": "tasks", "hard": "tasks_hard"}


def tasks_root() -> Path:
    env = os.environ.get("PIIBENCH_TASKS_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "tasks").is_dir():
            return parent
    return Path.cwd()


def task_dirs(split: str = "all") -> list[Path]:
    root = tasks_root()
    names = SPLITS.values() if split == "all" else [SPLITS[split]]
    return [p for n in names if (root / n).is_dir()
            for p in sorted((root / n).iterdir()) if (p / "gold.json").exists()]


def find_task(name: str) -> Path:
    hits = [p for p in task_dirs() if p.name == name or p.name.split("-")[0] == name]
    if len(hits) != 1:
        known = ", ".join(p.name for p in task_dirs())
        raise SystemExit(f"unknown task {name!r}; known tasks: {known}")
    return hits[0]


def split_of(task_dir: Path) -> str:
    return next((k for k, v in SPLITS.items() if task_dir.parent.name == v), "?")


def summarize(task_id: str, rep) -> str:
    c, d = rep.components, rep.diagnostics
    gates = [g.split("_")[1] for g in ("gate_schema", "gate_links", "gate_text") if not d[g]]
    lines = [
        f"{task_id}: score {rep.score:.3f}  solved {'yes' if d['solved'] else 'no'}"
        + (f"  (failed gate: {', '.join(gates)})" if gates else ""),
        f"  entity recall {c['entity_recall']:.3f}  continuity {c['continuity']:.3f}  "
        f"precision {c['precision']:.3f}  integrity {c['integrity']:.3f}",
        f"  people fully right {d['entity_success']:.3f} ({len(d['entities_ok'])}/{d['n_entities']})  "
        f"occurrence recall {d['occurrence_recall']:.3f}  leaked occurrences {d['n_leaked']}",
    ]
    if d.get("mapping_errors"):
        lines.append(f"  mapping.json problems: {d['mapping_errors'][:3]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------- commands

def cmd_list(args) -> int:
    print(f"{'task':30s} {'split':5s} {'people':>6s} {'occurrences':>11s} {'free text':>9s}")
    for td in task_dirs(args.split):
        a = json.loads((td / "audit.json").read_text())
        print(f"{td.name:30s} {split_of(td):5s} {a['entities']:6d} {a['occurrences']:11d} "
              f"{a['occurrences_free_text']:9d}")
    return 0


def cmd_init(args) -> int:
    td = find_task(args.task)
    dest = Path(args.workspace)
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest} exists and is not empty")
    shutil.copytree(td / "input", dest, dirs_exist_ok=True)
    print(f"copied {td.name} into {dest}: TASK.md, pseudonyms.json, data/")
    print(f"clean the files in place, then run:  piibench score {td.name.split('-')[0]} {dest}")
    return 0


def cmd_score(args) -> int:
    td = find_task(args.task)
    rep = grade(td / "input", Path(args.workspace), Gold.load(td / "gold.json"))
    print(summarize(td.name, rep))
    if args.json:
        Path(args.json).write_text(json.dumps({"task_id": td.name, **rep.to_json()}, indent=1) + "\n")
    return 0


def cmd_run(args) -> int:
    tds = [find_task(t) for t in args.tasks] if args.tasks else task_dirs(args.split)
    out = open(args.out, "a", encoding="utf-8") if args.out else None
    rows = []
    for td in tds:
        for rep_i in range(args.repeats):
            with tempfile.TemporaryDirectory() as tmp:
                ws = Path(tmp) / "workspace"
                shutil.copytree(td / "input", ws)
                env = dict(os.environ, PIIBENCH_WORKSPACE=str(ws), PIIBENCH_TASK=td.name)
                cmd = args.cmd.replace("{workspace}", shlex.quote(str(ws))).replace("{task}", td.name)
                t0 = time.perf_counter()
                stderr = ""
                try:
                    proc = subprocess.run(cmd, shell=True, cwd=ws, env=env, timeout=args.timeout,
                                          capture_output=True, text=True)
                    status = "ok" if proc.returncode == 0 else f"exit {proc.returncode}"
                    stderr = proc.stderr
                except subprocess.TimeoutExpired:
                    status = "timeout"
                elapsed = time.perf_counter() - t0
                rep = grade(td / "input", ws, Gold.load(td / "gold.json"))
                if args.keep:
                    shutil.copytree(ws, Path(args.keep) / f"{td.name}__{rep_i}", dirs_exist_ok=True)
            row = {"task_id": td.name, "split": split_of(td), "repeat": rep_i, "status": status,
                   "elapsed_s": round(elapsed, 2), **rep.to_json()}
            if status != "ok":
                row["stderr_tail"] = stderr[-2000:]
            rows.append(row)
            if out:
                out.write(json.dumps(row, sort_keys=True) + "\n")
                out.flush()
            print(f"{td.name:30s} #{rep_i} {status:8s} score {rep.score:.3f}  "
                  f"solved {'yes' if rep.diagnostics['solved'] else 'no '}  "
                  f"people fully right {rep.diagnostics['entity_success']:.3f}  {elapsed:7.1f} s")
            if status != "ok" and stderr.strip():
                print("    " + "\n    ".join(stderr.strip().splitlines()[-5:]))
    if out:
        out.close()
    if rows:
        by_task: dict[str, list[float]] = {}
        for r in rows:
            by_task.setdefault(r["task_id"], []).append(r["score"])
        mean = statistics.fmean(statistics.fmean(v) for v in by_task.values())
        solved = sum(r["diagnostics"]["solved"] for r in rows)
        people = statistics.fmean(r["diagnostics"]["entity_success"] for r in rows)
        print(f"\nmean score {mean:.3f} over {len(by_task)} tasks; solved {solved}/{len(rows)} runs; "
              f"people fully right {people:.3f}")
        failed = sum(r["status"] != "ok" for r in rows)
        if failed:
            print(f"warning: {failed}/{len(rows)} runs did not exit cleanly (see above); the command runs inside "
                  f"the workspace, so give script paths as absolute paths, e.g. \"python $PWD/my_cleaner.py\"")
            return 1
    return 0


def cmd_baselines(args) -> int:
    from . import run_local
    code = 0
    for split in (["main", "hard"] if args.split == "all" else [args.split]):
        root = tasks_root() / SPLITS[split]
        if root.is_dir():
            print(f"== {split} ({root.name}/)")
            code |= run_local.main(["--tasks", str(root), "--cleaners", *args.cleaners])
    return code


def cmd_check(args) -> int:
    from . import pii_scan, solvability
    from .cleaners import oracle
    bad = 0
    for td in task_dirs(args.split):
        gold = Gold.load(td / "gold.json")
        problems = []
        scan = pii_scan.scan_task(td) if pii_scan.available() else None
        if scan:
            problems.append(f"PII scan: {({k: sum(v.values()) for k, v in scan.items()})}")
        amb = solvability.ambiguous_mentions(td)
        if amb:
            problems.append(f"{len(amb)} ambiguous first-name mentions")
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(td / "input", ws)
            noop = grade(td / "input", ws, gold).score
            oracle.clean(ws, gold)
            best = grade(td / "input", ws, gold).score
        if abs(best - 1.0) > 1e-9 or noop != 0.0:
            problems.append(f"gold applied scores {best:.3f}, no-op scores {noop:.3f}")
        bad += bool(problems)
        print(f"{td.name:30s} " + ("ok" if not problems else "; ".join(problems))
              + ("" if scan is not None else "  (PII scan skipped: no system word list)"))
    return 1 if bad else 0


def cmd_materialize(args) -> int:
    out = Path(args.out)
    n = 0
    for src in args.files:
        for line in Path(src).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            td = out / SPLITS.get(row.get("split", "main"), "tasks") / row["task_id"]
            (td / "input" / "data").mkdir(parents=True, exist_ok=True)
            (td / "input" / "TASK.md").write_text(row["instructions"], encoding="utf-8")
            (td / "input" / "pseudonyms.json").write_text(
                json.dumps(row["personas"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            for f in row["files"]:
                (td / "input" / "data" / f["path"]).write_text(f["content"], encoding="utf-8")
            (td / "gold.json").write_text(row["gold"], encoding="utf-8")
            (td / "audit.json").write_text(json.dumps({
                "task_id": row["task_id"], "entities": row["num_people"], "occurrences": row["num_occurrences"],
                "occurrences_free_text": row["num_occurrences_free_text"]}, indent=1) + "\n", encoding="utf-8")
            n += 1
    print(f"wrote {n} tasks under {out}; use them with:  export PIIBENCH_TASKS_ROOT={out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="piibench", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="list the tasks")
    p.add_argument("--split", choices=["main", "hard", "all"], default="all")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("init", help="copy a task's agent-visible input into a workspace")
    p.add_argument("task")
    p.add_argument("workspace")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("score", help="grade a cleaned workspace")
    p.add_argument("task")
    p.add_argument("workspace")
    p.add_argument("--json", help="also write the full report to this file")
    p.set_defaults(fn=cmd_score)

    p = sub.add_parser("run", help="run a command on fresh copies of the tasks and grade each result")
    p.add_argument("--cmd", required=True,
                   help="shell command, run inside the workspace; {workspace} and {task} are substituted, and "
                        "PIIBENCH_WORKSPACE / PIIBENCH_TASK are set")
    p.add_argument("--split", choices=["main", "hard", "all"], default="main")
    p.add_argument("--tasks", nargs="*", help="task ids or prefixes (default: every task in --split)")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--timeout", type=float, default=None, help="seconds per run")
    p.add_argument("--out", help="append one JSON line per run to this file")
    p.add_argument("--keep", help="copy every cleaned workspace into this directory")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("baselines", help="run the built-in baselines")
    p.add_argument("--split", choices=["main", "hard", "all"], default="all")
    p.add_argument("--cleaners", nargs="+", default=["noop", "redact", "rules-structured", "rules", "oracle"])
    p.set_defaults(fn=cmd_baselines)

    p = sub.add_parser("check", help="generation checks: PII scan, solvability, gold = 1, no-op = 0")
    p.add_argument("--split", choices=["main", "hard", "all"], default="all")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("materialize", help="rebuild task folders from the Hugging Face export (config 'tasks')")
    p.add_argument("files", nargs="+", help="JSON Lines files of the 'tasks' configuration")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_materialize)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
