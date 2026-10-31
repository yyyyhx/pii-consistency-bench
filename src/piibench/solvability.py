"""Check that every first-name mention can be attributed from the records the agent sees.

A first name (or nickname) alone identifies a person only within its thread. The benchmark deliberately gives
the same first name to different people in one task (t04, t09, t10), so a mention like "Thanks, Seraphina!"
must be unique among the people of its own thread, not counting the speaker: otherwise the gold would decide a
coin flip, and an agent that picks the other person would be marked wrong for a reason it cannot see.

A thread is an issue (its record, comments, the commits that reference it, and the pull requests that name it),
a pull request (its record, reviews, comments, the commits it lists, and its linked issue), or a mail thread.

    python -m piibench.solvability tasks/<id> [...]     # prints ambiguous mentions, exit 1 if any
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

SPEAKER_PREFIXES = ("author.", "creator.", "from.")


def ambiguous_mentions(task_dir: Path) -> list[dict]:
    task_dir = Path(task_dir)
    gold = json.loads((task_dir / "gold.json").read_text(encoding="utf-8"))
    first = {e["entity_id"]: e["tokens"]["names"][0].casefold() for e in gold["entities"]}
    rows = {rel: [json.loads(ln) for ln in (task_dir / "input" / rel).read_text(encoding="utf-8").splitlines() if ln.strip()]
            for rel in gold["files"]}
    in_record: dict[tuple[str, str], set[str]] = defaultdict(set)
    speakers: dict[tuple[str, str], set[str]] = defaultdict(set)
    for o in gold["occurrences"]:
        rk = (o["file"], o["key"])
        in_record[rk].add(o["entity_id"])
        if o["span"] is None and o["path"].startswith(SPEAKER_PREFIXES):
            speakers[rk].add(o["entity_id"])

    thread_of: dict[tuple[str, str], tuple[str, str]] = {}
    pr_issue: dict[str, str] = {}
    pr_commits: dict[str, list[str]] = {}
    for rel, rs in rows.items():
        key = gold["files"][rel]["key"]
        for r in rs:
            k = str(r[key])
            if rel.endswith(("issues.jsonl", "bugs.jsonl")):
                thread_of[(rel, k)] = ("issue", k)
            elif rel.endswith("pull_requests.jsonl"):
                thread_of[(rel, k)] = ("pr", k)
                pr_issue[k] = r.get("issue_key")
                pr_commits[k] = list(r.get("commits") or [])
            elif "pull_request_id" in r:
                thread_of[(rel, k)] = ("pr", str(r["pull_request_id"]))
            elif rel.endswith("mail.jsonl"):
                thread_of[(rel, k)] = ("mail", str(r.get("in_reply_to") or k))
            elif r.get("issue_key") or r.get("bug_id"):
                thread_of[(rel, k)] = ("issue", str(r.get("issue_key") or r.get("bug_id")))
    members: dict[tuple[str, str], set[str]] = defaultdict(set)
    for rk, th in thread_of.items():
        members[th] |= in_record[rk]
    commit_file = next((rel for rel in rows if rel.endswith("commits.jsonl")), None)
    for pr, shas in pr_commits.items():
        for h in shas:
            members[("pr", pr)] |= in_record.get((commit_file, h), set())
    for pr, ik in pr_issue.items():
        if ik:
            both = members[("pr", pr)] | members[("issue", str(ik))]
            members[("pr", pr)] = members[("issue", str(ik))] = both

    out = []
    for o in gold["occurrences"]:
        if o["form"] not in ("first_name", "nickname") or o["span"] is None:
            continue
        rk = (o["file"], o["key"])
        th = thread_of.get(rk)
        if th is None:
            continue
        others = members[th] - speakers[rk] - {o["entity_id"]}
        clash = sorted(e for e in others if first[e] == first[o["entity_id"]])
        if clash:
            out.append({"occ_id": o["occ_id"], "file": o["file"], "key": o["key"], "value": o["value"],
                        "entity": o["entity_id"], "same_name_in_thread": clash})
    return out


def main(argv: list[str] | None = None) -> int:
    bad = 0
    for d in argv if argv is not None else sys.argv[1:]:
        amb = ambiguous_mentions(Path(d))
        bad += len(amb)
        print(Path(d).name, "ok" if not amb else amb)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
