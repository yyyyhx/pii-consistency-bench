"""Build the SmartSHARK tasks (t08-t10, and the hard split h1-h2) from the local MongoDB.

    python scripts/build_smartshark_linked.py                 # t08, t09, t10 into tasks/
    python scripts/build_smartshark_linked.py --only t09-storm-linked-accounts
    python scripts/build_smartshark_linked.py --hard          # h1, h2 into tasks_hard/

What is real and what is synthetic
----------------------------------
`scripts/smartshark_export.js` exports structure only: ObjectIds, commit hashes and parents, dates, issue keys,
statuses, pull-request numbers and states, which commits a pull request contains, review and comment authors,
mail thread references, and Travis build numbers and states. No prose, name or address leaves the database, and
the export is never written to disk. Every name, email, username, handle and sentence in the task is synthetic.

One SmartSHARK person id is one entity. Ids are never merged: the public dump has no identity table, and a Jira
account is usually a separate person id from the same person's git/GitHub account, so it is a separate entity
here as well.

What makes t09-t10 (and h1-h2) harder than t01-t08
---------------------------------------------------
* Opaque GitHub handles. For part of the developers, the GitHub account is a handle that shares nothing with
  the person's name (``quietfox17``) and shows no display name. The only record-level evidence linking it to
  the person's git identity is real: the pull request lists commit hashes that appear in ``commits.jsonl``
  authored by that person (SmartSHARK ``pull_request_commit`` rows with the same author id). Some handles are
  also mentioned by the ASF GitHub Bot on the linked Jira issue, or in a mail signature.
* Shared first names (t09): a developer and a Jira-only person share a first name, and first-name mentions
  ("Thanks, Chiara!") are resolvable only from the thread they sit in.
* Namesakes in one thread (t10): two different people share a full name, and one of them reviews the other's
  pull request. GitHub does not let the author's account approve its own pull request, so the records show two
  people; t04
  tested the opposite pattern (same name, never in the same thread, separated by a directory).
* No developer directory, more people per task (25-31), and eight files: commits, Jira issues, Jira comments,
  pull requests, reviews, pull-request comments, mail, and CI builds.
* The hard split (``HARD_SPECS``) makes these people the majority: every handle opaque and linked only through
  commits (``opaque_by_handle``), and several namesake pairs (``namesake_pairs``).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from piibench import GENERATOR_VERSION, pii_scan, solvability  # noqa: E402
from piibench.cleaners import oracle  # noqa: E402
from piibench.generate import POLICY  # noqa: E402
from piibench.gold import Entity, Gold, TextBuilder, write_jsonl  # noqa: E402
from piibench.grader import grade  # noqa: E402
from piibench.grader.leaks import combined_pattern  # noqa: E402
from piibench.grader.score import decoded_text  # noqa: E402
from piibench.personas import (  # noqa: E402
    EXTRA_TARGET_FIRST, EXTRA_TARGET_LAST, SOURCE_FIRST, TARGET_FIRST, TARGET_LAST, IdentityFactory, SourceIdentity,
    obfuscate_email, pool_size,
)

EXPORT_JS = ROOT / "scripts" / "smartshark_export.js"


@dataclass
class Spec:
    task_id: str
    project: str
    title: str
    seed: int
    salt: str
    max_seeds: int = 8
    opaque_share: float = 0.5          # developers whose GitHub handle is unrelated to their name
    shared_first_pairs: int = 0        # developer / Jira-person pairs that share a first name
    namesake_in_thread: bool = False   # PR creator and a reviewer of that PR share a full name
    namesake_pairs: int = 0            # how many such pairs (0: one pair if namesake_in_thread)
    mail_personal_share: float = 0.5   # developers who mail from a personal address instead of the work one
    signature_handle_share: float = 0.3  # opaque developers who name their handle in a mail signature
    opaque_by_handle: bool = False     # reviews and PR comments address opaque developers by @handle, not first name
    bot_comment_rate: float = 1.0      # ASF GitHub Bot comment on the Jira issue a pull request names
    builds_per_commit: int = 2
    seed_mode: str = "pr_commit"       # how seeds are chosen (scripts/smartshark_export.js)
    commits_per_seed: int = 3          # only used by seed_mode "shared_id"
    issue_comments: int = 3            # Jira comments kept per linked issue
    fork_url_alias: bool = False       # fork URLs use the person's second username, not their account name
    nickname_rate: float = 0.0         # Jira comments that address the reporter by nickname
    obfuscated_signature_share: float = 0.0  # mails signed with the sender's address in "a at b dot c" form
    name_variant_share: float = 0.0    # developers whose GitHub and mail name is spelled differently from git
    trailer_rate: float = 0.0          # commits ending in Signed-off-by (and sometimes Co-authored-by) trailers
    code_mention_rate: float = 0.0     # PR descriptions with an @author tag, PR comments with a TODO(handle)
    profile_url_rate: float = 0.0      # Jira comments linking the assignee's profile page
    quote_signatures: bool = False     # mail replies quote the parent message's signature
    difficulty: str = "hard"
    targets: list[str] = field(default_factory=list)


SPECS = [
    Spec("t08-zookeeper-pulls-mail", "zookeeper", "ZooKeeper: commits, Jira, pull requests, reviews and mail",
         seed=808, salt="z", max_seeds=8, seed_mode="shared_id", commits_per_seed=2, issue_comments=1,
         opaque_share=0.0, shared_first_pairs=0, mail_personal_share=0.3, bot_comment_rate=1.0,
         fork_url_alias=True, nickname_rate=0.5, obfuscated_signature_share=0.5, difficulty="medium",
         name_variant_share=0.4, trailer_rate=0.4, code_mention_rate=0.4, profile_url_rate=0.3, quote_signatures=True,
         targets=["one identity across git, GitHub and the mailing list, linked through real issue keys",
                  "a username that appears only in a fork URL", "nicknames in Jira comments",
                  "obfuscated addresses in mail signatures", "Jira-only people"]),
    Spec("t09-storm-linked-accounts", "storm", "Storm: opaque GitHub handles and shared first names", seed=909,
         salt="a", opaque_share=0.6, shared_first_pairs=2, mail_personal_share=0.5, signature_handle_share=0.3,
         name_variant_share=0.4, trailer_rate=0.4, code_mention_rate=0.4, profile_url_rate=0.3, quote_signatures=True,
         targets=["GitHub handles linked to git identities only through pull-request commits",
                  "first-name mentions shared by two people, resolved by thread", "Jira-only people",
                  "ASF GitHub Bot comments (bot kept, handle inside replaced)", "Travis CI logs", "mail threads"]),
    Spec("t10-curator-namesakes", "curator", "Curator: namesakes in one pull request, personal mail addresses",
         seed=1010, salt="b", opaque_share=0.4, shared_first_pairs=1, namesake_in_thread=True,
         mail_personal_share=0.75, signature_handle_share=0.5,
         name_variant_share=0.4, trailer_rate=0.4, code_mention_rate=0.4, profile_url_rate=0.3, quote_signatures=True,
         targets=["two people with one full name in the same pull request (reviewer and author)",
                  "mail from personal addresses with name variants", "opaque GitHub handles",
                  "ASF GitHub Bot comments", "Travis CI logs"]),
]

# The hard split: the same mechanisms as t09-t10, with the people who need record-level reasoning in the majority.
# Every developer's GitHub handle is opaque and nothing but the pull request's commits ties it to its owner (no
# handle in mail signatures, reviewers address opaque developers as @handle); several namesake pairs and more
# shared first names. Built into tasks_hard/ and reported as a separate split.
HARD_SPECS = [
    Spec("h1-storm-opaque", "storm", "Storm, hard: every GitHub handle opaque and linked only through commits",
         seed=919, salt="c", max_seeds=10, opaque_share=1.0, shared_first_pairs=3, mail_personal_share=0.5,
         signature_handle_share=0.0, opaque_by_handle=True,
         name_variant_share=0.4, trailer_rate=0.4, code_mention_rate=0.4, profile_url_rate=0.3, quote_signatures=True,
         targets=["opaque GitHub handles whose only evidence is the commits a pull request lists",
                  "first-name mentions shared by two people, resolved by thread"]),
    Spec("h2-curator-namesakes", "curator", "Curator, hard: several namesake pairs and opaque handles",
         seed=1020, salt="d", max_seeds=10, opaque_share=1.0, shared_first_pairs=2, namesake_pairs=3,
         mail_personal_share=0.75, signature_handle_share=0.0, opaque_by_handle=True,
         name_variant_share=0.4, trailer_rate=0.4, code_mention_rate=0.4, profile_url_rate=0.3, quote_signatures=True,
         targets=["several pairs of people with one full name in the same pull request",
                  "opaque GitHub handles whose only evidence is the commits a pull request lists"]),
]

# Spellings of synthetic names that real systems store differently (an accented Jira or GitHub profile next to
# an ASCII git config). Names without one get a middle initial or an all-lowercase display name instead.
ACCENTED = {"Soren": "Søren", "Radomir": "Radomír", "Emeric": "Émeric", "Zoltan": "Zoltán", "Jorgensen": "Jørgensen",
            "Zielinski": "Zieliński", "Szymanski": "Szymański", "Jablonski": "Jabłoński", "Kaminski": "Kamiński",
            "Wojcik": "Wójcik", "Bergstrom": "Bergström", "Vasquez": "Vásquez", "Yilmaz": "Yılmaz",
            "Thorvaldsen": "Þorvaldsen", "Ludmila": "Ludmiła", "Clementine": "Clémentine", "Eulalia": "Eulàlia",
            "Giselle": "Gisèle", "Oyelaran": "Oyèláràn", "Mireille": "Mireille", "Jovan": "Jovan"}
ACCENTED = {k: v for k, v in ACCENTED.items() if k != v}

HANDLE_A = ["quiet", "rusty", "amber", "lunar", "brisk", "dusty", "hollow", "sable", "tidal", "velvet", "copper",
            "misty", "silent", "frosty", "gentle", "mellow", "nimble", "polar", "rapid", "solar"]
HANDLE_B = ["fox", "otter", "heron", "lynx", "badger", "falcon", "marten", "kestrel", "wren", "bison", "puffin",
            "gecko", "ibis", "koala", "lemur", "newt", "osprey", "plover", "stoat", "tapir"]

PHRASES = [
    "Fix race condition in the worker heartbeat", "Handle a missing configuration value gracefully",
    "Add retry with backoff to the client connection", "Remove unused imports and deprecated calls",
    "Improve the error message when the cluster is unreachable", "Make the metrics reporting interval configurable",
    "Avoid leaking file handles on shutdown", "Document the new timeout option",
    "Add unit tests for the serializer", "Guard against negative timeouts", "Simplify the retry loop",
    "Fix flaky integration test on slow machines", "Close the session when the leader changes",
    "Upgrade the build plugin and fix compiler warnings", "Log the cause when a listener throws",
]
ISSUE_BODY = [
    "Steps to reproduce: start two nodes, stop the leader, and watch the follower logs.",
    "The exception only shows up under load; the stack trace points at the connection pool.",
    "This looks like a regression from the last minor release.",
    "We hit this in production after the upgrade; rolling back fixes it.",
    "The default value is too low for large clusters.",
]
JIRA_COMMENT = [
    "I can reproduce this on master.", "Attached a patch with a unit test.", "+1, the patch looks good to me.",
    "This also affects the 2.x branch.", "Committed to master. Thanks for the contribution.",
    "Could you add a test for the failure case?", "The fix works for us in staging.",
]
PR_BODY = ["Tested locally with the full unit test suite.", "Adds a regression test for the reported case.",
           "Small change, no API impact.", "Follow-up to the discussion on the mailing list."]
# Person-like strings that are not personal data: eponymous technical terms and products or languages named
# like people. Replacing them is over-scrubbing (they are listed as distractors in the gold).
LOOKALIKES = [
    ("The lookup uses a Bloom filter, so a few false positives are expected.", {"Bloom": "eponym"}),
    ("Fuzzy matching uses the Levenshtein distance between the two keys.", {"Levenshtein": "eponym"}),
    ("Routing between brokers follows Dijkstra's shortest-path algorithm.", {"Dijkstra": "eponym"}),
    ("Counters are kept in a Fenwick tree to answer range queries.", {"Fenwick": "eponym"}),
    ("The metric sums use Kahan summation to avoid drift.", {"Kahan": "eponym"}),
    ("The same failure shows up with the Cassandra state backend.", {"Cassandra": "product"}),
    ("The Ruby and Julia client bindings are not affected.", {"Ruby": "language", "Julia": "language"}),
]
LOOKALIKE_RATE = 0.5

MAIL_BODY = [
    "I would like to start a discussion about the release schedule for the next minor version.",
    "The nightly build has been failing on the integration tests since yesterday.",
    "Does anyone still depend on the old configuration format? I would like to deprecate it.",
    "I am planning to cut the release candidate this week unless there are objections.",
    "Thanks everyone for testing the release candidate.",
]


# ---------------------------------------------------------------------------------------------- export


def export_slice(spec: Spec) -> dict:
    prelude = (f"var PROJECT={json.dumps(spec.project)}, MAX_SEEDS={spec.max_seeds}, SEED_SALT={json.dumps(spec.salt)}, "
               f"SEED_MODE={json.dumps(spec.seed_mode)}, COMMITS_PER_SEED={spec.commits_per_seed}, "
               f"ISSUE_COMMENTS={spec.issue_comments};\n")
    proc = subprocess.run(["docker", "exec", "-i", "smartshark-mongo", "mongo", "--quiet", "smartshark_2_1"],
                          input=prelude + EXPORT_JS.read_text(encoding="utf-8"), text=True, capture_output=True,
                          check=False)
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith('{"project"')]
    if proc.returncode != 0 or not line:
        raise RuntimeError(proc.stderr[-2000:] or proc.stdout[-2000:])
    return json.loads(line[-1])


# ---------------------------------------------------------------------------------------------- people


@dataclass
class Person:
    oid: str
    kind: str  # "dev" (git/GitHub account) or "jira"
    entity: Entity
    ident: SourceIdentity
    handle: str = ""
    opaque: bool = False
    mail_email: str = ""
    mail_name_form: str = "full_name"
    signature_handle: bool = False
    variant_first: str = ""            # display spelling on GitHub and in mail ("" = same as git)
    variant_last: str = ""

    @property
    def display_full(self) -> str:
        return f"{self.variant_first or self.ident.first} {self.variant_last or self.ident.last}"

    @property
    def display_last_first(self) -> str:
        return f"{self.variant_last or self.ident.last}, {self.variant_first or self.ident.first}"


class Builder:
    def __init__(self, spec: Spec, sl: dict):
        self.spec, self.sl = spec, sl
        self.rng = random.Random(spec.seed)
        self.factory = IdentityFactory(spec.seed * 7919 + 23)
        self.gold = Gold(task_id=spec.task_id, seed=spec.seed, generator_version=GENERATOR_VERSION + "+smartshark-linked",
                         provenance={}, files={})
        self.people: dict[str, Person] = {}
        self.distractors: dict[str, str] = {}
        self.nick_used: set[str] = set()
        self.repo = spec.project
        self.list_addr = f"dev@{spec.project}.apache.org"
        self.shared_first: list[list[str]] = []
        self.namesakes: list[str] = []                     # everyone in a namesake pair
        self.namesake_pairs: list[tuple[str, str]] = []    # (PR creator, the other participant)

    # -------------------------------------------------------------- identities
    def plan_people(self) -> None:
        sl, rng, spec = self.sl, self.rng, self.spec
        dev = list(sl["seeds"])
        for c in sl["commits"]:
            dev += [c["author"], c["committer"]]
        dev += [r["creator"] for r in sl["reviews"]] + [c["author"] for c in sl["pr_comments"]]
        jira = []
        for i in sl["issues"]:
            jira += [i["reporter"], i["assignee"]] + [c["author"] for c in i["comments"]]
        dev = list(dict.fromkeys(x for x in dev if x))
        jira = list(dict.fromkeys(x for x in jira if x and x not in dev))

        # namesakes: a PR creator and someone who reviewed or commented on that PR get one full name
        forced: dict[str, tuple[str | None, str | None]] = {}
        n_pairs = spec.namesake_pairs or (1 if spec.namesake_in_thread else 0)
        if n_pairs:
            pairs = [(pr["creator"], r["creator"]) for pr in sl["prs"] for r in sl["reviews"]
                     if r["pr"] == pr["id"] and r["creator"] != pr["creator"] and r["state"] in ("APPROVED", "CHANGES_REQUESTED")]
            pairs += [(pr["creator"], c["author"]) for pr in sl["prs"] for c in sl["pr_comments"]
                      if c["pr"] == pr["id"] and c["author"] != pr["creator"]]
            for a, b in pairs:
                if len(self.namesake_pairs) < n_pairs and not {a, b} & set(self.namesakes):
                    self.namesake_pairs.append((a, b))
                    self.namesakes += [a, b]
            if len(self.namesake_pairs) < n_pairs:
                raise RuntimeError(f"{spec.task_id}: only {len(self.namesake_pairs)} pull requests with a second "
                                   f"participant for the namesake pairs")
        # Shared first names: a developer and a Jira-only person who never take part in the same thread. Each
        # first-name mention is then unique where it appears (solvable from its thread), while the name as a
        # string belongs to two people (a global first-name -> persona rule gets one of them wrong). Prefer
        # people who are addressed by first name: developers whose pull requests get a review or comment, and
        # Jira reporters whose issues get comments from someone else.
        threads = self.thread_sets()
        firsts = [f for f in SOURCE_FIRST]
        rng.shuffle(firsts)
        addressed_dev = {pr["creator"] for pr in sl["prs"]
                         if any(r["pr"] == pr["id"] for r in sl["reviews"] + sl["pr_comments"])}
        addressed_jira = {i["reporter"] for i in sl["issues"] if any(c["author"] != i["reporter"] for c in i["comments"])}
        dev_pick = sorted((x for x in sl["seeds"] if x not in self.namesakes),
                          key=lambda x: (x not in addressed_dev, x))
        jira_pick = sorted(jira, key=lambda x: (x not in addressed_jira, x))
        used: set[str] = set()
        for d in dev_pick:
            if len(self.shared_first) == spec.shared_first_pairs:
                break
            j = next((j for j in jira_pick if j not in used and not (threads[d] & threads[j])), None)
            if j is None:
                continue
            used.add(j)
            fn = firsts.pop()
            forced[d] = (fn, None)
            forced[j] = (fn, None)
            self.shared_first.append([d, j])
        if len(self.shared_first) < spec.shared_first_pairs:
            raise RuntimeError(f"{spec.task_id}: only {len(self.shared_first)} thread-disjoint first-name pairs")

        n_opaque = round(spec.opaque_share * len(sl["seeds"]))
        opaque = set(rng.sample(list(sl["seeds"]), n_opaque))
        for a, _b in self.namesake_pairs:
            opaque.discard(a)  # keep the namesake evidence about the names, not the handles
        twin_of = {b: a for a, b in self.namesake_pairs}
        handles_used: set[str] = set()

        for oid in dev + jira:
            first, last = forced.get(oid, (None, None))
            if oid in twin_of:
                if twin_of[oid] not in self.people:
                    raise RuntimeError(f"{spec.task_id}: namesake {oid} comes before the person it copies")
                src = self.people[twin_of[oid]].ident
                ident = self.factory.source_identity(first=src.first, last=src.last,
                                                     exclude_domains={src.org_domain, src.personal_domain})
            else:
                ident = self.factory.source_identity(first=first, last=last)
            e = Entity(entity_id=f"E{len(self.people) + 1:02d}", source_ref=f"smartshark:{oid}", tokens={})
            self.gold.entities.append(e)
            p = Person(oid=oid, kind="dev" if oid in dev else "jira", entity=e, ident=ident)
            if p.kind == "dev":
                p.opaque = oid in opaque
                if p.opaque:
                    while True:
                        h = f"{rng.choice(HANDLE_A)}{rng.choice(HANDLE_B)}{rng.randint(10, 99)}"
                        if h not in handles_used:
                            break
                    p.handle = h
                    handles_used.add(h)
                    p.signature_handle = rng.random() < spec.signature_handle_share
                else:
                    p.handle = ident.usernames[0]
                personal = rng.random() < spec.mail_personal_share
                p.mail_email = ident.personal_email if personal else ident.work_email
                p.mail_name_form = rng.choice(["full_name", "full_name", "last_first"]) if personal else "full_name"
                if spec.name_variant_share:
                    # a name with a native spelling always shows it on GitHub and in mail; others sometimes show a
                    # middle initial or an all-lowercase profile name
                    vf, vl = ACCENTED.get(ident.first, ident.first), ACCENTED.get(ident.last, ident.last)
                    if (vf, vl) == (ident.first, ident.last) and rng.random() < spec.name_variant_share:
                        if rng.random() < 0.5:
                            vf = f"{ident.first} {rng.choice('ABCDEFGHJKLMNPRSTW')}."
                        else:
                            vf, vl = ident.first.lower(), ident.last.lower()
                    p.variant_first, p.variant_last = ("", "") if (vf, vl) == (ident.first, ident.last) else (vf, vl)
            self.people[oid] = p

    def lookalike(self, tb: TextBuilder, trailing: str = "") -> None:
        """Sometimes add a sentence with a person-like term that must be left alone."""
        if self.rng.random() < LOOKALIKE_RATE:
            text, kinds = self.rng.choice(LOOKALIKES)
            tb.lit(("" if trailing else " ") + text + trailing)
            self.distractors.update(kinds)

    def thread_sets(self) -> dict[str, set[str]]:
        """person id -> the threads they take part in. An issue and the pull requests naming it are one thread."""
        sl = self.sl
        issue_by_oid = {i["oid"]: i for i in sl["issues"]}
        keys = {i["key"] for i in sl["issues"]}
        tid = {pr["id"]: (f"I:{pr['issue_key']}" if pr["issue_key"] in keys else f"P:{pr['id']}") for pr in sl["prs"]}
        out: dict[str, set[str]] = {}

        def add(oid, t):
            if oid:
                out.setdefault(oid, set()).add(t)
        for i in sl["issues"]:
            for oid in [i["reporter"], i["assignee"]] + [c["author"] for c in i["comments"]]:
                add(oid, f"I:{i['key']}")
        for c in sl["commits"]:
            k = next((issue_by_oid[i]["key"] for i in c["issues"] if i in issue_by_oid), None)
            for oid in (c["author"], c["committer"]):
                if k:
                    add(oid, f"I:{k}")
                for pr in sl["prs"]:
                    if c["hash"] in pr["commits"]:
                        add(oid, tid[pr["id"]])
        for pr in sl["prs"]:
            add(pr["creator"], tid[pr["id"]])
        for r in sl["reviews"] + sl["pr_comments"]:
            add(r.get("creator") or r.get("author"), tid[r["pr"]])
        return {k: v for k, v in out.items()} | {o: set() for o in sl["seeds"] if o not in out}

    def P(self, oid: str | None) -> Person | None:
        return self.people.get(oid) if oid else None

    # -------------------------------------------------------------- gold helpers
    def add(self, p: Person, form: str, value: str, file: str, key, path: str, surface: str, accept=None) -> None:
        self.gold.add(p.entity, form, value, file, str(key), path, None, surface, False, accept)

    def git_obj(self, p: Person, file: str, key, path: str, surface: str) -> dict:
        self.add(p, "full_name", p.ident.full_name, file, key, f"{path}.name", f"{surface}.name")
        self.add(p, "email", p.ident.work_email, file, key, f"{path}.email", f"{surface}.email")
        return {"name": p.ident.full_name, "email": p.ident.work_email}

    def jira_obj(self, p: Person | None, file: str, key, path: str, surface: str) -> dict | None:
        if p is None:
            return None
        u = p.ident.usernames[0]
        self.add(p, "username", u, file, key, f"{path}.username", f"{surface}.username")
        self.add(p, "full_name", p.ident.full_name, file, key, f"{path}.display_name", f"{surface}.display_name")
        return {"username": u, "display_name": p.ident.full_name}

    def gh_obj(self, p: Person, file: str, key, path: str, surface: str) -> dict:
        """GitHub account: opaque handles show the handle as the display name (no profile name)."""
        self.add(p, "username", p.handle, file, key, f"{path}.login", f"{surface}.login")
        if p.opaque:
            self.add(p, "username", p.handle, file, key, f"{path}.display_name", f"{surface}.display_name",
                     accept=["username", "name"])
            return {"login": p.handle, "display_name": p.handle}
        self.add(p, "full_name", p.display_full, file, key, f"{path}.display_name", f"{surface}.display_name")
        return {"login": p.handle, "display_name": p.display_full}

    # -------------------------------------------------------------- render
    def build(self, out_root: Path) -> dict:
        sl, rng, spec = self.sl, self.rng, self.spec
        self.plan_people()
        F = {k: f"data/{k}.jsonl" for k in ("commits", "issues", "comments", "pull_requests", "reviews",
                                             "pr_comments", "mail", "ci_builds")}
        issues_by_oid = {i["oid"]: i for i in sl["issues"]}
        issue_keys = {i["key"] for i in sl["issues"]}
        commit_by_oid = {c["oid"]: c for c in sl["commits"]}
        hashes = {c["hash"] for c in sl["commits"]}
        pr_of_commit = {h: pr for pr in sl["prs"] for h in pr["commits"]}
        phrase_of: dict[str, str] = {}

        # Who takes part in each thread. A first name alone must be unique in its thread (not counting the
        # speaker); otherwise the mention is rendered as the account handle instead (see piibench.solvability).
        def commit_issue(c: dict) -> str | None:
            return next((issues_by_oid[i]["key"] for i in c["issues"] if i in issues_by_oid), None)

        issue_people: dict[str, set[str]] = {}
        for i in sl["issues"]:
            issue_people[i["key"]] = {i["reporter"], i["assignee"]} | {c["author"] for c in i["comments"]}
        for c in sl["commits"]:
            if (k := commit_issue(c)) is not None:
                issue_people[k] |= {c["author"], c["committer"]}
        pr_people: dict[str, set[str]] = {}
        for pr in sl["prs"]:
            pr_people[pr["id"]] = ({pr["creator"]} | {r["creator"] for r in sl["reviews"] if r["pr"] == pr["id"]}
                                   | {c["author"] for c in sl["pr_comments"] if c["pr"] == pr["id"]}
                                   | {x for c in sl["commits"] if c["hash"] in pr["commits"]
                                      for x in (c["author"], c["committer"])})
        for pr in sl["prs"]:
            if pr["issue_key"] in issue_people:
                issue_people[pr["issue_key"]] |= pr_people[pr["id"]]
                pr_people[pr["id"]] |= issue_people[pr["issue_key"]]

        def name_clash(target: Person, speaker: Person | None, people: set[str]) -> bool:
            return any(o not in (target.oid, speaker.oid if speaker else None) and o in self.people
                       and self.people[o].ident.first == target.ident.first for o in people if o)

        def address(tb: TextBuilder, target: Person, speaker: Person | None, people: set[str], jira: bool) -> None:
            """The target's first name, or their account handle when the first name is ambiguous here."""
            if spec.opaque_by_handle and target.opaque and not jira:
                tb.lit("@").mention(target.entity, "username", target.handle, accept=["username"])
                return
            if not name_clash(target, speaker, people):
                nick = target.ident.nickname
                if (spec.nickname_rate and jira and nick and rng.random() < spec.nickname_rate
                        and nick.lower() not in pii_scan._WORDS
                        and all(nick != o.ident.first for o in self.people.values())):
                    tb.mention(target.entity, "nickname", nick)
                    self.nick_used.add(target.oid)
                else:
                    tb.mention(target.entity, "first_name", target.ident.first)
            elif jira:
                tb.lit("[~").mention(target.entity, "username", target.ident.usernames[0], accept=["username"]).lit("]")
            else:
                tb.lit("@").mention(target.entity, "username", target.handle, accept=["username"])

        def phrase(key: str) -> str:
            if key not in phrase_of:
                phrase_of[key] = rng.choice(PHRASES)
            return phrase_of[key]

        # commits
        commits = []
        for c in sorted(sl["commits"], key=lambda c: c["committed_at"] or ""):
            a, cm = self.P(c["author"]), self.P(c["committer"]) or self.P(c["author"])
            ikey = next((issues_by_oid[i]["key"] for i in c["issues"] if i in issues_by_oid), None)
            pr = pr_of_commit.get(c["hash"])
            subj = f"{ikey}: {phrase(ikey)}" if ikey else rng.choice(PHRASES)
            msg = TextBuilder().lit(subj + (f"\n\nThis closes #{pr['id']}" if pr else ""))
            if spec.trailer_rate and rng.random() < spec.trailer_rate:
                msg.lit("\n\nSigned-off-by: ").mention(a.entity, "full_name", a.ident.full_name).lit(" <")
                msg.mention(a.entity, "email", a.ident.work_email).lit(">")
                rv = next((r for r in sl["reviews"] if pr and r["pr"] == pr["id"] and r["creator"] != c["author"]), None)
                if rv and rng.random() < 0.5:
                    co = self.people[rv["creator"]]
                    msg.lit("\nCo-authored-by: ").mention(co.entity, "full_name", co.ident.full_name).lit(" <")
                    msg.mention(co.entity, "email", co.ident.work_email).lit(">")
            rec = {"hash": c["hash"], "repo": self.repo, "parents": c["parents"],
                   "author": self.git_obj(a, F["commits"], c["hash"], "author", "commits.author"),
                   "authored_at": c["authored_at"],
                   "committer": self.git_obj(cm, F["commits"], c["hash"], "committer", "commits.committer"),
                   "committed_at": c["committed_at"],
                   "message": msg.register(self.gold, F["commits"], c["hash"], "message", "commits.message"),
                   "issue_key": ikey}
            commits.append(rec)

        # Jira issues and comments
        issues, comments = [], []
        pr_by_issue: dict[str, list[dict]] = {}
        for pr in sl["prs"]:
            if pr["issue_key"] in issue_keys:
                pr_by_issue.setdefault(pr["issue_key"], []).append(pr)
        cid = 0
        for i in sorted(sl["issues"], key=lambda i: i["created"] or ""):
            rep, asg = self.P(i["reporter"]), self.P(i["assignee"])
            k = i["key"]
            tb = TextBuilder().lit(rng.choice(ISSUE_BODY))
            self.lookalike(tb)
            if asg is not None and asg is not rep and rng.random() < 0.5:
                tb.lit("\n\n[~").mention(asg.entity, "username", asg.ident.usernames[0], accept=["username"])
                tb.lit("] could you take a look?")
            rec = {"issue_key": k, "project": spec.project.upper(), "type": i["type"] or "Bug",
                   "status": i["status"] or "Open", "priority": i["priority"] or "Major",
                   "created": i["created"], "updated": i["updated"], "summary": phrase(k),
                   "reporter": self.jira_obj(rep, F["issues"], k, "reporter", "issues.reporter"),
                   "assignee": self.jira_obj(asg, F["issues"], k, "assignee", "issues.assignee"),
                   "description": tb.register(self.gold, F["issues"], k, "description", "issues.description")}
            issues.append(rec)
            for c in i["comments"]:
                au = self.P(c["author"])
                cid += 1
                tb = TextBuilder()
                if rep is not None and au is not rep and rng.random() < 0.6:
                    style = rng.randrange(3)
                    if style == 0:
                        tb.lit(rng.choice(JIRA_COMMENT)).lit(" Thanks, ")
                        address(tb, rep, au, issue_people[k], jira=True)
                        tb.lit("!")
                    elif style == 1:
                        tb.lit("[~").mention(rep.entity, "username", rep.ident.usernames[0], accept=["username"])
                        tb.lit("] does the patch work for you?")
                    else:
                        address(tb, rep, au, issue_people[k], jira=True)
                        tb.lit(": ").lit(rng.choice(JIRA_COMMENT))
                else:
                    tb.lit(rng.choice(JIRA_COMMENT))
                if asg is not None and spec.profile_url_rate and rng.random() < spec.profile_url_rate:
                    tb.lit("\n\nAssigning to https://issues.apache.org/jira/secure/ViewProfile.jspa?name=")
                    tb.mention(asg.entity, "username", asg.ident.usernames[0], accept=["username"])
                ckey = 100000 + cid
                comments.append({"comment_id": ckey, "issue_key": k,
                                 "author": self.jira_obj(au, F["comments"], ckey, "author", "comments.author"),
                                 "created": c["created"],
                                 "body": tb.register(self.gold, F["comments"], str(ckey), "body", "comments.body")})
            for pr in pr_by_issue.get(k, []):
                if rng.random() >= spec.bot_comment_rate:
                    continue
                cid += 1
                ckey = 100000 + cid
                cr = self.people[pr["creator"]]
                tb = TextBuilder().lit("GitHub user ").mention(cr.entity, "username", cr.handle, accept=["username"])
                tb.lit(f" opened a pull request:\n\n    https://github.com/apache/{self.repo}/pull/{pr['id']}\n\n"
                       f"    {k}: {phrase(k)}")
                comments.append({"comment_id": ckey, "issue_key": k,
                                 "author": {"username": "githubbot", "display_name": "ASF GitHub Bot"},
                                 "created": pr["created"],
                                 "body": tb.register(self.gold, F["comments"], str(ckey), "body", "comments.body")})
                self.distractors.update({"githubbot": "bot", "ASF GitHub Bot": "bot"})
        comments.sort(key=lambda r: (r["created"] or "", r["comment_id"]))

        # pull requests, reviews, pull-request comments
        prs, reviews, prc = [], [], []
        reviewer_of = {r["pr"]: r for r in sl["reviews"]}
        for pr in sorted(sl["prs"], key=lambda p: p["created"] or ""):
            cr = self.people[pr["creator"]]
            key = pr["id"]
            ikey = pr["issue_key"] if pr["issue_key"] in issue_keys else None
            tb = TextBuilder().lit(rng.choice(PR_BODY))
            self.lookalike(tb)
            if not cr.opaque and spec.code_mention_rate and rng.random() < spec.code_mention_rate:
                tb.lit("\n\nNew helper class:\n\n    /**\n     * Retries a request with exponential backoff.\n     *\n"
                       "     * @author ").mention(cr.entity, "full_name", cr.ident.full_name).lit("\n     */")
            rv = reviewer_of.get(key)
            if rv and rng.random() < 0.5:
                rp = self.people[rv["creator"]]
                tb.lit("\n\n@").mention(rp.entity, "username", rp.handle, accept=["username"]).lit(" could you review?")
            url = TextBuilder().lit("https://github.com/")
            if pr["fork"]:
                fork_user = cr.ident.usernames[1] if spec.fork_url_alias and not cr.opaque else cr.handle
                url.mention(cr.entity, "username", fork_user, accept=["username"]).lit(f"/{self.repo}")
            else:
                url.lit(f"apache/{self.repo}")
            rec = {"id": key, "title": f"{ikey}: {phrase(ikey)}" if ikey else rng.choice(PHRASES),
                   "state": pr["state"] or "closed", "created_at": pr["created"], "merged_at": pr["merged"],
                   "creator": self.gh_obj(cr, F["pull_requests"], key, "creator", "pull_requests.creator"),
                   "source_repo_url": url.register(self.gold, F["pull_requests"], key, "source_repo_url",
                                                   "pull_requests.source_repo_url"),
                   "commits": [h for h in pr["commits"] if h in hashes], "issue_key": ikey,
                   "description": tb.register(self.gold, F["pull_requests"], key, "description",
                                              "pull_requests.description")}
            prs.append(rec)
        for r in sl["reviews"]:
            cr = self.people[next(p["creator"] for p in sl["prs"] if p["id"] == r["pr"])]
            rp = self.people[r["creator"]]
            tb = TextBuilder()
            state = r["state"] or "COMMENTED"
            here = pr_people[r["pr"]]
            if state == "APPROVED":
                tb.lit("LGTM, thanks ")
                address(tb, cr, rp, here, jira=False)
                tb.lit("!")
            elif state == "CHANGES_REQUESTED":
                tb.lit("Please rebase on master and add a test, ")
                address(tb, cr, rp, here, jira=False)
                tb.lit(".")
            else:
                address(tb, cr, rp, here, jira=False)
                tb.lit(", could you explain why the timeout changed?")
            reviews.append({"id": f"rv-{r['pr']}", "pull_request_id": r["pr"], "state": state, "submitted_at": r["at"],
                            "creator": self.gh_obj(rp, F["reviews"], f"rv-{r['pr']}", "creator", "reviews.creator"),
                            "body": tb.register(self.gold, F["reviews"], f"rv-{r['pr']}", "body", "reviews.body")})
        for c in sl["pr_comments"]:
            cr = self.people[next(p["creator"] for p in sl["prs"] if p["id"] == c["pr"])]
            au = self.people[c["author"]]
            tb = TextBuilder()
            if rng.random() < 0.5:
                tb.lit("Thanks for the patch @").mention(cr.entity, "username", cr.handle, accept=["username"]).lit(". +1")
            else:
                tb.lit("Can you squash the commits, ")
                address(tb, cr, au, pr_people[c["pr"]], jira=False)
                tb.lit("?")
            if spec.code_mention_rate and rng.random() < spec.code_mention_rate:
                tb.lit("\n\nI left a TODO(").mention(cr.entity, "username", cr.handle, accept=["username"])
                tb.lit(") in the retry loop for the follow-up.")
            k = f"pc-{c['pr']}"
            prc.append({"id": k, "pull_request_id": c["pr"], "created_at": c["at"],
                        "author": self.gh_obj(au, F["pr_comments"], k, "author", "pr_comments.author"),
                        "body": tb.register(self.gold, F["pr_comments"], k, "body", "pr_comments.body")})

        # mail
        mail = []
        mail_ids = {m["oid"]: f"m{n + 1:03d}" for n, m in enumerate(sorted(sl["mail"], key=lambda m: m["at"] or ""))}
        sender_of = {m["oid"]: self.people[m["from"]] for m in sl["mail"]}
        for m in sorted(sl["mail"], key=lambda m: m["at"] or ""):
            p = self.people[m["from"]]
            mid = mail_ids[m["oid"]]
            parent = next((r for r in m["refs"] if r in mail_ids), None)
            subj_phrase = rng.choice(PHRASES)
            subject = f"Re: [DISCUSS] {subj_phrase}" if parent else f"[DISCUSS] {subj_phrase}"
            tb = TextBuilder()
            if parent:
                q = sender_of[parent]
                tb.lit("On ").lit((self.sl_mail_date(parent) or "")[:10]).lit(", ")
                tb.mention(q.entity, "full_name", q.ident.full_name).lit(" wrote:\n> ").lit(rng.choice(MAIL_BODY))
                if spec.quote_signatures:
                    tb.lit("\n> -- \n> ").mention(q.entity, "full_name", q.display_full).lit(" | ")
                    tb.mention(q.entity, "email_obfuscated", obfuscate_email(q.ident.work_email))
                tb.lit("\n\n")
            tb.lit("Hi all,\n\n").lit(rng.choice(MAIL_BODY)).lit("\n\n")
            self.lookalike(tb, trailing="\n\n")
            if p.opaque and p.signature_handle:
                tb.lit("-- \n").mention(p.entity, "full_name", p.ident.full_name).lit(" (GitHub: ")
                tb.mention(p.entity, "username", p.handle, accept=["username"]).lit(")")
            elif spec.obfuscated_signature_share and rng.random() < spec.obfuscated_signature_share:
                tb.lit("-- \n").mention(p.entity, "full_name", p.ident.full_name).lit(" | ")
                tb.mention(p.entity, "email_obfuscated", obfuscate_email(p.ident.work_email))
            elif rng.random() < 0.5:
                tb.lit("Thanks,\n").mention(p.entity, "first_name", p.ident.first)
            else:
                tb.lit("-- \n").mention(p.entity, "full_name", p.ident.full_name)
            name = p.display_full if p.mail_name_form == "full_name" else p.display_last_first
            self.add(p, p.mail_name_form, name, F["mail"], mid, "from.name", "mail.from.name")
            self.add(p, "email", p.mail_email, F["mail"], mid, "from.email", "mail.from.email")
            mail.append({"id": mid, "list": self.list_addr, "date": m["at"], "in_reply_to": mail_ids.get(parent),
                         "from": {"name": name, "email": p.mail_email}, "subject": subject,
                         "body": tb.register(self.gold, F["mail"], mid, "body", "mail.body")})
        self.distractors[self.list_addr] = "mailing_list"

        # CI builds (Travis): real build numbers, states and events, synthetic log text
        builds, per_commit = [], Counter()
        commit_rec = {c["hash"]: c for c in commits}
        for b in sorted(sl["builds"], key=lambda b: b["number"]):
            c = commit_by_oid.get(b["commit_oid"])
            if c is None or per_commit[c["hash"]] >= spec.builds_per_commit:
                continue
            per_commit[c["hash"]] += 1
            a, cm = self.P(c["author"]), self.P(c["committer"]) or self.P(c["author"])
            bid = f"travis-{b['number']}"
            rec_c = commit_rec[c["hash"]]
            tb = TextBuilder().lit(f"Build #{b['number']} ({b['event']}) on Travis CI\n")
            tb.lit(f"$ git clone --depth=50 https://github.com/apache/{self.repo}.git apache/{self.repo}\n")
            tb.lit(f"$ git checkout -qf {c['hash']}\nCommit {c['hash'][:7]} by ")
            tb.mention(a.entity, "full_name", a.ident.full_name).lit(" <").mention(a.entity, "email", a.ident.work_email).lit(">\n")
            tb.lit(f"Subject: {rec_c['message'].splitlines()[0]}\n$ mvn -B install -DskipTests=false\n")
            state = b["state"] or "passed"
            tb.lit("[INFO] BUILD SUCCESS\n" if state == "passed" else "[ERROR] BUILD FAILURE\n")
            tb.lit(f"Done. Your build exited with {0 if state == 'passed' else 1}.\nNotifying ")
            tb.mention(cm.entity, "email", cm.ident.work_email).lit(" of the result.")
            builds.append({"build_id": bid, "number": b["number"], "commit": c["hash"], "event": b["event"],
                           "state": state, "started_at": b["started"], "duration_s": b["duration"],
                           "log": tb.register(self.gold, F["ci_builds"], bid, "log", "ci_builds.log")})
        if builds:
            self.distractors["Travis CI"] = "ci_service"

        files = {F["commits"]: commits, F["issues"]: issues, F["comments"]: comments, F["pull_requests"]: prs,
                 F["reviews"]: reviews, F["pr_comments"]: prc, F["mail"]: mail, F["ci_builds"]: builds}
        files = {k: v for k, v in files.items() if v}
        return self.write(out_root, files)

    def sl_mail_date(self, oid: str) -> str | None:
        return next((m["at"] for m in self.sl["mail"] if m["oid"] == oid), None)

    # -------------------------------------------------------------- output
    def write(self, out_root: Path, files: dict[str, list[dict]]) -> dict:
        spec, gold = self.spec, self.gold
        for p in self.people.values():
            e = p.entity
            emails = sorted({a.value for a in e.aliases if a.form == "email"})
            usernames = sorted({a.value for a in e.aliases if a.form == "username"} | set(p.ident.usernames)
                               | ({p.handle} if p.handle else set()))
            emails = sorted(set(emails) | {a.value.replace(" at ", "@", 1).replace(" dot ", ".") for a in e.aliases
                                           if a.form == "email_obfuscated"})
            names = [p.ident.first, p.ident.last] + ([p.ident.nickname] if p.oid in self.nick_used else [])
            e.tokens = {"names": names, "usernames": usernames, "emails": emails,
                        "email_locals": sorted({m.split("@")[0] for m in emails})}
        # entities without any occurrence (a Jira assignee slot that was empty) are dropped
        used = {o.entity_id for o in gold.occurrences}
        gold.entities = [e for e in gold.entities if e.entity_id in used]
        pool_factory = IdentityFactory(spec.seed * 104729 + 5)
        personas = [p.to_json() for p in pool_factory.persona_pool(pool_size(len(gold.entities)))]

        spec_files = {
            "data/commits.jsonl": {"key": "hash", "fk": {"issue_key": "data/issues.jsonl"}, "text_fields": ["message"]},
            "data/issues.jsonl": {"key": "issue_key", "fk": {}, "text_fields": ["summary", "description"]},
            "data/comments.jsonl": {"key": "comment_id", "fk": {"issue_key": "data/issues.jsonl"}, "text_fields": ["body"]},
            "data/pull_requests.jsonl": {"key": "id", "fk": {"commits.*": "data/commits.jsonl",
                                                            "issue_key": "data/issues.jsonl"},
                                         "text_fields": ["title", "description"]},
            "data/reviews.jsonl": {"key": "id", "fk": {"pull_request_id": "data/pull_requests.jsonl"}, "text_fields": ["body"]},
            "data/pr_comments.jsonl": {"key": "id", "fk": {"pull_request_id": "data/pull_requests.jsonl"},
                                       "text_fields": ["body"]},
            "data/mail.jsonl": {"key": "id", "fk": {"in_reply_to": "data/mail.jsonl"}, "text_fields": ["subject", "body"]},
            "data/ci_builds.jsonl": {"key": "build_id", "fk": {"commit": "data/commits.jsonl"}, "text_fields": ["log"]},
        }
        gold.files = {rel: spec_files[rel] for rel in files}
        gold.occurrences = [o for o in gold.occurrences if o.file in gold.files]
        gold.distractors = [{"value": v, "kind": k} for v, k in sorted(self.distractors.items())]
        eid = {p.oid: p.entity.entity_id for p in self.people.values()}
        gold.provenance = {
            "source": "SmartSHARK 2.1 Small (Trautsch et al., CC-BY 4.0), local MongoDB",
            "project": f"apache/{spec.project}",
            "real_structure": ["commit hashes, parents, dates, author/committer ids", "commit-issue links",
                               "pull-request numbers, states, dates, fork flag, and the commits each contains",
                               "review and pull-request comment authors and states", "Jira issue keys, types, statuses, "
                               "reporter/assignee/comment authors", "mail dates and thread references",
                               "Travis build numbers, events, states, durations"],
            "synthetic": ["all names, emails, usernames and handles", "all prose (templated)",
                          "ASF GitHub Bot comment text (pattern of the real bot)", "CI log text"],
            "opaque_handles": sorted(eid[p.oid] for p in self.people.values() if p.opaque and eid[p.oid] in
                                     {e.entity_id for e in gold.entities}),
            "shared_first_names": [[eid[a], eid[b]] for a, b in self.shared_first],
            "collisions": [[eid[a], eid[b]] for a, b in self.namesake_pairs],
            "spec": {"task_id": spec.task_id, "difficulty": spec.difficulty, "targets": spec.targets,
                     "opaque_share": spec.opaque_share, "shared_first_pairs": spec.shared_first_pairs,
                     "namesake_in_thread": spec.namesake_in_thread},
        }
        if spec.namesake_pairs or spec.opaque_by_handle:
            gold.provenance["spec"] |= {"namesake_pairs": len(self.namesake_pairs),
                                        "opaque_by_handle": spec.opaque_by_handle}

        out = out_root / spec.task_id
        if out.exists():
            shutil.rmtree(out)
        inp = out / "input"
        for rel, rows in files.items():
            write_jsonl(inp / rel, rows)
        (inp / "pseudonyms.json").write_text(json.dumps(personas, indent=2) + "\n", encoding="utf-8")
        docs = {
            "data/commits.jsonl": "git commits: author/committer (name, email), message, linked issue",
            "data/issues.jsonl": "Jira issues: reporter/assignee accounts, summary, description",
            "data/comments.jsonl": "Jira comments: author account, body (includes ASF GitHub Bot notices)",
            "data/pull_requests.jsonl": "GitHub pull requests: creator account, source repository, commits, linked issue",
            "data/reviews.jsonl": "GitHub pull-request reviews: reviewer account, state, body",
            "data/pr_comments.jsonl": "GitHub pull-request comments: author account, body",
            "data/mail.jsonl": "developer mailing-list messages: sender name and address, subject, body, reply link",
            "data/ci_builds.jsonl": "Travis CI builds: commit, state, console log",
        }
        listing = "\n".join(f"- {rel}  ({docs[rel]})" for rel in files)
        (inp / "TASK.md").write_text(POLICY.format(root="the current working directory", files=listing), encoding="utf-8")
        (out / "gold.json").write_text(json.dumps(gold.to_json(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        audit = self.validate(out, personas)
        (out / "audit.json").write_text(json.dumps(audit, indent=1) + "\n", encoding="utf-8")
        return audit

    def validate(self, out: Path, personas: list[dict]) -> dict:
        gold, inp = self.gold, out / "input"
        problems = []
        raw = {rel: decoded_text(inp / rel) for rel in gold.files}
        rx = combined_pattern(gold.entities)
        for rel, text in raw.items():
            found = len(rx.findall(text))
            expected = sum(len(rx.findall(o.value)) for o in gold.occurrences if o.file == rel)
            if found != expected:
                problems.append(f"sensitive tokens in {rel}: found {found}, annotated {expected}")
        tgt = re.compile(r"\b(" + "|".join(sorted(set(TARGET_FIRST) | set(TARGET_LAST) | set(EXTRA_TARGET_FIRST)
                                                   | set(EXTRA_TARGET_LAST))) + r")\b")
        for rel, text in raw.items():
            if m := tgt.search(text):
                problems.append(f"pool name in input {rel}: {m.group(0)}")
        if amb := solvability.ambiguous_mentions(out):
            problems.append(f"first-name mentions not unique in their thread: {amb}")
        if pii_scan.available() and (f := pii_scan.scan_task(out)):
            problems.append(f"gold-independent PII scan: { {k: len(v) for k, v in f.items()} }")
        # solvability: every opaque handle is tied to its owner's git identity by a listed commit
        commits = {json.loads(ln)["hash"]: json.loads(ln) for ln in (inp / "data/commits.jsonl").read_text().splitlines()}
        for ln in (inp / "data/pull_requests.jsonl").read_text().splitlines():
            pr = json.loads(ln)
            p = next(p for p in self.people.values() if p.kind == "dev" and p.handle == pr["creator"]["login"])
            if p.opaque and not any(commits[h]["author"]["email"] == p.ident.work_email for h in pr["commits"]):
                problems.append(f"PR {pr['id']}: opaque handle has no linking commit")
        if problems:
            raise RuntimeError(f"{gold.task_id}: validation failed: {problems}")
        # the oracle must score 1 and doing nothing 0
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            shutil.copytree(inp, ws)
            assert grade(inp, ws, gold).score == 0.0
            oracle.clean(ws, gold)
            r = grade(inp, ws, gold)
            if r.score != 1.0:
                raise RuntimeError(f"{gold.task_id}: oracle scored {r.score}: {r.failures}")
        return {
            "task_id": gold.task_id,
            "records": {rel: sum(1 for ln in (inp / rel).read_text().splitlines() if ln.strip()) for rel in gold.files},
            "entities": len(gold.entities),
            "entities_by_kind": dict(Counter(p.kind for p in self.people.values()
                                             if p.entity.entity_id in {e.entity_id for e in gold.entities})),
            "opaque_handles": len(gold.provenance["opaque_handles"]),
            "pool_size": len(personas),
            "occurrences": len(gold.occurrences),
            "occurrences_free_text": sum(o.span is not None for o in gold.occurrences),
            "occurrences_by_surface": dict(sorted(Counter(o.surface for o in gold.occurrences).items())),
            "aliases_per_entity": {e.entity_id: len(e.aliases) for e in gold.entities},
            "shared_first_names": gold.provenance["shared_first_names"],
            "collisions": gold.provenance["collisions"],
            "distractors": len(gold.distractors),
            "validation": "ok",
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--hard", action="store_true", help="build the hard split (default output: tasks_hard/)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    args.out = args.out or str(ROOT / ("tasks_hard" if args.hard else "tasks"))
    for spec in HARD_SPECS if args.hard else SPECS:
        if args.only and spec.task_id not in args.only:
            continue
        audit = Builder(spec, export_slice(spec)).build(Path(args.out))
        print(json.dumps({k: audit[k] for k in ("task_id", "records", "entities", "entities_by_kind", "opaque_handles",
                                                 "occurrences", "occurrences_free_text", "shared_first_names",
                                                 "collisions")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
