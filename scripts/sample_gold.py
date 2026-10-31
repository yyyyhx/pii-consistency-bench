"""Print a random sample of gold occurrences in free text, with their context, for a manual audit.

    python scripts/sample_gold.py [--n 40] [--seed 0] [--tasks t08 t09 t10]

Structured fields and exact token coverage are checked automatically at generation time. What a script cannot
check is attribution: whether "Thanks, Seraphina!" really refers to the person the gold says. This prints each
sampled mention with the text around it, the record it sits in, the person the gold assigns it to, and every other
person in the task who carries the same token, so a reviewer can confirm the attribution from the records.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from piibench.gold import Gold, read_jsonl  # noqa: E402
from piibench.grader.leaks import fold  # noqa: E402


def get(rec: dict, path: str):
    for part in path.split("."):
        rec = rec[int(part)] if isinstance(rec, list) else rec[part]
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tasks", nargs="*", default=["t08", "t09", "t10"])
    args = ap.parse_args()
    pool = []
    for td in sorted((ROOT / "tasks").iterdir()):
        if td.name[:3] in args.tasks and (td / "gold.json").exists():
            g = Gold.load(td / "gold.json")
            pool += [(td, g, o) for o in g.occurrences if o.span]
    rnd = random.Random(args.seed)
    for td, g, o in rnd.sample(pool, min(args.n, len(pool))):
        spec = g.files[o.file]
        rec = next(r for r in read_jsonl(td / "input" / o.file) if str(r[spec["key"]]) == o.key)
        text = get(rec, o.path)
        s, e = o.span
        ent = next(x for x in g.entities if x.entity_id == o.entity_id)
        full = next((a.value for a in ent.aliases if a.form == "full_name"), ent.aliases[0].value)
        others = sorted({next((a.value for a in x.aliases if a.form == "full_name"), x.entity_id) for x in g.entities
                         if x.entity_id != o.entity_id and any(fold(o.value) == fold(a.value) or fold(o.value) in
                                                               [fold(n) for n in x.tokens.get("names", [])]
                                                               for a in x.aliases)})
        ctx = {k: rec[k] for k in ("issue_key", "pull_request_id", "in_reply_to", "list") if k in rec}
        print(f"--- {td.name[:3]} {o.file}:{o.key}:{o.path} {ctx}")
        print(f"    ...{text[max(0, s - 70):s]}[[{text[s:e]}]]{text[e:e + 50]}...".replace("\n", " / "))
        print(f"    gold: {o.entity_id} {full} ({o.form})" + (f"   others with this token: {others}" if others else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
