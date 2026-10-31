"""Build benchmark tasks from the 20-MAD sample: real structure, synthetic identities."""

from __future__ import annotations

import glob
import json
import random
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from . import GENERATOR_VERSION
from .gold import Entity, Gold, TextBuilder, write_jsonl
from .personas import TARGET_FIRST, TARGET_LAST, IdentityFactory, SourceIdentity, obfuscate_email, pool_size
from .sanitize import (CAP_WORD_RE, LEFTOVER_ASSIGN_RE, NameLexicon, build_lexicon, reject_reason,
                       strip_commit_message)
from .sources.mad import BOT_ACCOUNTS, MadSample, detect_bots

PROJECT_EMAIL_DOMAIN = {"apache": "apache.example.org", "mozilla": "mozilla.example.com"}
NOBODY = -1
NOBODY_ACCOUNT = {"login": "nobody@mozilla.org", "display_name": "Nobody; OK to take it and work on it"}
CI_JOB = {"ZOOKEEPER": "ZooKeeper-trunk", "HDFS": "Hadoop-hdfs-trunk", "HADOOP": "Hadoop-common-trunk"}
LIST_NAME = {"ZOOKEEPER": "zookeeper", "HDFS": "hdfs", "HADOOP": "hadoop"}

FILE_DOCS = {
    "data/commits.jsonl": "git commits: author/committer (name, email), message, linked issue",
    "data/issues.jsonl": "Jira issues: reporter/assignee/creator accounts, summary, description",
    "data/bugs.jsonl": "Bugzilla bugs: creator/assigned_to accounts (login email, display name), summary, description",
    "data/comments.jsonl": "issue comments: author account, body",
    "data/people.jsonl": "developer directory: one record per developer with known names, emails and accounts",
    "data/ci_builds.jsonl": "CI builds: job, commit, trigger, console log",
}

POLICY = """\
You are cleaning personally identifiable information (PII) from an export of linked
software-engineering records before it is shared outside the organisation.

Working directory: {root}
Files to clean (JSON Lines, one JSON object per line):
{files}
Pseudonym pool: pseudonyms.json (synthetic personas to use as replacements; you will not need all of them).

Rules
1. Replace every reference to a real person, in structured fields and in free text: full names,
   partial names (first name only, surname only, initials such as "J. Doe"), display names, email
   addresses, usernames and handles (for example "[~jdoe]", "via jdoe", "@jdoe", "[:jdoe]", "r=jdoe"),
   and account names. This includes personal usernames inside URLs and file paths (for example
   "https://github.com/jdoe/repo" or "/home/jdoe/"); the rest of such a URL or path stays unchanged.
2. Map each real person to exactly one persona and use that same persona everywhere
   the person appears, in every file and field. The same person can appear under different aliases on
   different surfaces (a git author email, a tracker username, a first name in a comment); all of them
   must become the same persona. Personas may come from pseudonyms.json, or you may invent them.
   If you invent any, write mapping.json in the working directory: a JSON list of objects with
   persona_id, full_name, first_name, last_name, email, and username. A consistent invented persona
   listed there scores the same as a pool persona. An invented persona that is not listed does not count.
   mapping.json is shipped with the cleaned export, so it must list only the personas: never put an
   original name, email address, or username in it.
3. Never map two different people to the same persona. Similar or even identical names do not imply the
   same person; use the surrounding records to decide.
4. Use the persona field that fits the slot: names -> full_name (or first_name / last_name / "T. Doe"
   initial form when the original was a first name, surname, or initial form); emails -> email;
   usernames and handles -> username.
5. Change nothing else. Keep record ids, hashes, issue keys, timestamps, links (apart from personal
   usernames inside them), field names, bot and system accounts (such as "Hadoop QA"), mailing-list
   addresses, product and library names, code, and all other text exactly as it is.
6. Keep every file valid JSON Lines with the same records, keys, and value types. Edit the files in place.

When you are done, reply with a one-paragraph summary. Only the files are graded.
"""


@dataclass
class TaskSpec:
    task_id: str
    title: str
    difficulty: str
    targets: list[str]
    source: str = "apache"
    product: str = "ZOOKEEPER"
    repo: str = "zookeeper"
    seed: int = 0
    n_issues: int = 6
    max_comments_per_issue: int = 5
    max_commits_per_issue: int = 2
    max_entities: int = 8
    min_seed_keys: int = 2
    include_directory: bool = True
    include_bots: bool = True
    comment_mention_rate: float = 0.5
    description_mention_rate: float = 0.3
    commit_attribution_rate: float = 0.3
    max_text_chars: int = 700
    collisions: int = 0
    ci_surface: bool = False
    distractor_rate: float = 0.0
    hard_mention_rate: float = 0.0
    third_parties: int = 0
    notes: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def tracker(self) -> str:
        return "jira" if self.source == "apache" else "bugzilla"


def _ts(x) -> str | None:
    if x is None or pd.isna(x):
        return None
    return pd.Timestamp(x).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def _nan(x):
    return None if x is None or (isinstance(x, float) and pd.isna(x)) or x is pd.NaT else x


def _clip_paragraphs(paras: list[str], limit: int) -> str:
    out, n = [], 0
    for p in paras:
        if out and n + len(p) > limit:
            break
        out.append(p)
        n += len(p) + 2
    return "\n\n".join(out)[: max(limit, len(out[0]) if out else 0)]


@dataclass
class Selection:
    spec: TaskSpec
    issues: list[int]
    cand: dict
    comments: dict
    person: object
    seed_person: int
    collisions: list[tuple[int, int]]
    rejects: Counter


class Generator:
    def __init__(self, mad: MadSample):
        self.mad = mad
        self._lex: dict[str, NameLexicon] = {}

    # -------------------------------------------------------------- lexicon
    def lexicon(self, source: str) -> NameLexicon:
        if source not in self._lex:
            c = self.mad.commits
            msgs = c[c.source == source].message.fillna("").tolist()
            tracker = "jira" if source == "apache" else "bugzilla"
            files = glob.glob(str(self.mad.root / "nlp" / tracker / source / "*" / "*_nlcomments.parquet"))
            texts = pd.concat([pd.read_parquet(f, columns=["text"]) for f in files]).text.tolist()
            iss = self.mad.issues
            summaries = iss[iss.source == source].summary.fillna("").tolist()
            subjects = [strip_commit_message(m, source)[0].split("\n")[0] for m in msgs]
            self._lex[source] = build_lexicon(msgs, texts, summaries + subjects)
        return self._lex[source]

    # -------------------------------------------------------------- selection
    def select(self, spec: TaskSpec, rng: random.Random) -> Selection:
        lex = self.lexicon(spec.source)
        mad = self.mad
        k2p = mad.key_to_person(spec.source)
        ambiguous = mad.ambiguous_keys(spec.source)
        idm = mad.idmerging
        flagged_bots = set(idm[(idm.source == spec.source) & idm.is_bot].key_hash)

        issues = mad.issues[(mad.issues.source == spec.source) & (mad.issues["product"] == spec.product)]
        issues = issues.set_index("issue_id", drop=False)
        comments = mad.comments[(mad.comments.source == spec.source) & (mad.comments["product"] == spec.product)]
        paras = mad.comment_paragraphs(spec.source, spec.product)
        bots = detect_bots(comments, paras) if spec.source == "apache" else {}
        nobody_key = None
        if spec.tracker == "bugzilla":
            vc = issues.assignee_key.value_counts()
            if len(vc) and vc.iloc[0] / len(issues) > 0.15:
                nobody_key = vc.index[0]  # Bugzilla's default assignee (nobody@mozilla.org)
        commits = mad.commits[(mad.commits.source == spec.source) & (mad.commits.repo == spec.repo)
                              & mad.commits.issue_id.isin(issues.issue_id)]
        para_by_comment = paras.groupby("comment_id").text.apply(list)
        rejects: Counter = Counter()

        def person(kind: str, key) -> int | None:
            key = _nan(key)
            if key is None or key in ambiguous or key in bots or key in flagged_bots:
                return None
            if key == nobody_key:
                return NOBODY
            return k2p.get((kind, key))

        def human(p) -> bool:
            return p is not None and p != NOBODY

        def usable_paragraphs(texts: list[str]) -> list[str]:
            ok = []
            for t in texts:
                t = t.strip()
                r = reject_reason(t, lex)
                rejects[(r or "accept").split(":")[0]] += 1
                if r is None:
                    ok.append(t)
            return ok

        def subject_ok(msg: str) -> bool:
            subject = strip_commit_message(str(msg), spec.source)[0].split("\n")[0]
            if LEFTOVER_ASSIGN_RE.search(subject):  # a reviewer slot we failed to strip
                return False
            return reject_reason(subject, lex, min_chars=10) is None

        cand: dict[int, dict] = {}
        for iid, cgrp in commits.groupby("issue_id"):
            iid = int(iid)
            if iid not in issues.index:
                continue
            row = issues.loc[iid]
            if reject_reason(str(row.summary or ""), lex, min_chars=10):
                continue
            cg = cgrp[cgrp.author.map(lambda k: human(person("commits", k)))
                      & cgrp.committer.map(lambda k: human(person("commits", k)))
                      & cgrp.message.map(subject_ok)]
            if cg.empty:
                continue
            icom = comments[comments.issue_id == iid].sort_values("created")
            hum, bot_rows = [], []
            for _, cr in icom.iterrows():
                body = usable_paragraphs(para_by_comment.get(cr.comment_id, []))
                if not body:
                    continue
                if cr.author_key in bots:
                    bot_rows.append((cr, body))
                elif human(person("issues", cr.author_key)):
                    hum.append((cr, body))
            if len(hum) < 2:
                continue
            cand[iid] = {"row": row, "commits": cg.sort_values("commit_time"), "human": hum, "bots": bot_rows}

        def mandatory(iid: int) -> set[int]:
            c = cand[iid]
            ppl = {person("issues", c["row"].reporter_key), person("issues", c["row"].assignee_key),
                   person("issues", c["row"].creator_key)}
            for _, cm in c["commits"].head(spec.max_commits_per_issue).iterrows():
                ppl |= {person("commits", cm.author), person("commits", cm.committer)}
            return {p for p in ppl if human(p)}

        keys_of: dict[int, set] = defaultdict(set)
        issues_of: dict[int, set[int]] = defaultdict(set)
        commit_people: set[int] = set()
        for iid, c in cand.items():
            for p in mandatory(iid) | {person("issues", cr.author_key) for cr, _ in c["human"]}:
                issues_of[p].add(iid)
            for _, cm in c["commits"].iterrows():
                for k in (cm.author, cm.committer):
                    p = person("commits", k)
                    if human(p):
                        keys_of[p].add(("c", k))
                        commit_people.add(p)
            for k in (c["row"].reporter_key, c["row"].assignee_key, c["row"].creator_key,
                      *[cr.author_key for cr, _ in c["human"]]):
                p = person("issues", k)
                if human(p):
                    keys_of[p].add(("i", k))
        seeds = sorted((p for p in commit_people if len(keys_of[p]) >= spec.min_seed_keys and len(issues_of[p]) >= 3),
                       key=lambda p: (-len(issues_of[p]), p))
        if not seeds:
            raise RuntimeError(f"{spec.task_id}: no seed person satisfies the spec")
        seed_person = rng.choice(seeds[:20])

        selected: list[int] = []
        ents: set[int] = set()
        pool = sorted(issues_of[seed_person])
        rng.shuffle(pool)
        while len(selected) < spec.n_issues:
            best, best_cost = None, None
            for iid in pool:
                if iid in selected:
                    continue
                if len(ents | mandatory(iid)) > spec.max_entities:
                    continue
                cost = len(mandatory(iid) - ents)
                if best_cost is None or cost < best_cost:
                    best, best_cost = iid, cost
            if best is None:
                extra = sorted({i for p in ents for i in issues_of[p]} - set(selected) - set(pool))
                if not extra:
                    break
                rng.shuffle(extra)
                pool += extra
                continue
            selected.append(best)
            ents |= mandatory(best)
        selected.sort(key=lambda i: cand[i]["row"].created)

        chosen: dict[int, list] = {}
        for iid in selected:
            picked = []
            for cr, body in cand[iid]["human"]:
                p = person("issues", cr.author_key)
                if p in ents or len(ents) < spec.max_entities:
                    ents.add(p)
                    picked.append((cr, body, None))
                if len(picked) >= spec.max_comments_per_issue:
                    break
            if spec.include_bots and cand[iid]["bots"]:
                cr, body = cand[iid]["bots"][0]
                picked.append((cr, body, bots[cr.author_key]))
            picked.sort(key=lambda t: t[0].created)
            chosen[iid] = picked

        collisions: list[tuple[int, int]] = []
        if spec.collisions:
            part: dict[int, set[int]] = defaultdict(set)
            for iid in selected:
                for p in mandatory(iid) | {person("issues", cr.author_key) for cr, _, b in chosen[iid] if not b}:
                    if human(p):
                        part[p].add(iid)
            people = sorted(p for p in part if p != seed_person)
            pairs = [(a, b) for i, a in enumerate(people) for b in people[i + 1:] if not (part[a] & part[b])]
            rng.shuffle(pairs)
            used: set[int] = set()
            for a, b in pairs:
                if a in used or b in used:
                    continue
                collisions.append((a, b))
                used |= {a, b}
                if len(collisions) == spec.collisions:
                    break
            if len(collisions) < spec.collisions:
                raise RuntimeError(f"{spec.task_id}: only {len(collisions)} disjoint collision pairs available")
        return Selection(spec, selected, cand, chosen, person, seed_person, collisions, rejects)

    def build(self, spec: TaskSpec, out_root: Path) -> dict:
        rng = random.Random(spec.seed)
        sel = self.select(spec, rng)
        return _Renderer(self, sel, rng).render(Path(out_root))


class _Renderer:
    def __init__(self, gen: Generator, sel: Selection, rng: random.Random):
        self.gen, self.sel, self.spec, self.rng = gen, sel, sel.spec, rng
        self.person = sel.person
        self.lex = gen.lexicon(self.spec.source)
        self.factory = IdentityFactory(self.spec.seed * 7919 + 17)
        self.gold = Gold(task_id=self.spec.task_id, seed=self.spec.seed, generator_version=GENERATOR_VERSION,
                         provenance={}, files={})
        self.ents: dict[int, Entity] = {}
        self.idents: dict[int, SourceIdentity] = {}
        self.git_variant: dict[str, tuple[str, str, str]] = {}
        self.trk_variant: dict[str, tuple] = {}
        self.git_count: Counter = Counter()
        self.trk_count: Counter = Counter()
        self.collide_with = {b: a for a, b in sel.collisions}
        self.proj_domain = PROJECT_EMAIL_DOMAIN[self.spec.source]
        self.distractors: dict[str, str] = {}
        self.third_seen: set[str] = set()
        self.nick_used: set[int] = set()
        bz = self.spec.tracker == "bugzilla"
        self.f_com = "data/commits.jsonl"
        self.f_iss = "data/bugs.jsonl" if bz else "data/issues.jsonl"
        self.f_cmt = "data/comments.jsonl"
        self.f_ppl = "data/people.jsonl"
        self.f_ci = "data/ci_builds.jsonl"

    # ---------------------------------------------------------------- identities
    def entity(self, p: int) -> Entity:
        if p not in self.ents:
            if p in self.collide_with:
                src = self.idents.get(self.collide_with[p]) or self.ident(self.collide_with[p])
                d = self.factory.source_identity(n_usernames=2, first=src.first, last=src.last,
                                                 exclude_domains={src.org_domain, src.personal_domain})
            else:
                d = self.factory.source_identity(n_usernames=2)
            self.idents[p] = d
            e = Entity(entity_id=f"E{len(self.ents) + 1:02d}", source_ref=f"mad:{self.spec.source}:{p}", tokens={})
            self.ents[p] = e
            self.gold.entities.append(e)
        return self.ents[p]

    def ident(self, p: int) -> SourceIdentity:
        self.entity(p)
        return self.idents[p]

    def p_of(self, e: Entity) -> int:
        return next(p for p, x in self.ents.items() if x is e)

    def git_identity(self, key: str) -> tuple[Entity, tuple[str, str, str]]:
        p = self.person("commits", key)
        e = self.entity(p)
        if key not in self.git_variant:
            i = self.git_count[p]
            self.git_count[p] += 1
            d = self.idents[p]
            variants = [
                ("full_name", d.full_name, d.work_email),
                ("full_name", d.full_name, d.project_email(self.proj_domain)),
                ("username", d.usernames[0], d.noreply_email()),
                ("initial_last", d.initial_last, d.personal_email),
            ]
            self.git_variant[key] = variants[i % len(variants)]
        return e, self.git_variant[key]

    def tracker_identity(self, key: str) -> tuple[Entity, tuple]:
        """Jira: (username, display_form, display). Bugzilla: (login email, display_form, name, nick)."""
        p = self.person("issues", key)
        e = self.entity(p)
        if key not in self.trk_variant:
            i = self.trk_count[p]
            self.trk_count[p] += 1
            d = self.idents[p]
            if self.spec.tracker == "jira":
                if i == 0:
                    v = (d.usernames[0], "full_name", d.full_name)
                elif i == 1:
                    v = (d.usernames[1], "initial_last", d.initial_last)
                else:
                    v = (self.factory.reserve_username(f"{d.usernames[0]}{i}"), "full_name", d.full_name)
            else:
                v = [(d.work_email, "full_name", d.full_name, d.usernames[0]),
                     (d.personal_email, "full_name", d.full_name, d.usernames[1]),
                     (d.project_email(self.proj_domain), "initial_last", d.initial_last, d.usernames[0])][i % 3]
            self.trk_variant[key] = v
        return e, self.trk_variant[key]

    def mention_username(self, e: Entity) -> str:
        return self.idents[self.p_of(e)].usernames[0]

    def bz_display(self, e: Entity) -> tuple[str, str]:
        """Display name and login of the entity's first Bugzilla account (falls back to a fresh rendering)."""
        p = self.p_of(e)
        for key, v in self.trk_variant.items():
            if self.person("issues", key) == p:
                return v[2], v[3]
        d = self.idents[p]
        return d.full_name, d.usernames[0]

    def login_email(self, e: Entity) -> str:
        p = self.p_of(e)
        for key, v in self.trk_variant.items():
            if self.person("issues", key) == p and self.spec.tracker == "bugzilla":
                return v[0]
        return self.idents[p].work_email

    # ---------------------------------------------------------------- accounts
    def account(self, key, file: str, rec_key, path: str, surface: str) -> dict | None:
        key = _nan(key)
        if key is None:
            return None
        p = self.person("issues", key)
        if p is None:
            return None
        if p == NOBODY:
            for v in NOBODY_ACCOUNT.values():
                self.distractors[v] = "system_account"
            return dict(NOBODY_ACCOUNT)
        e, v = self.tracker_identity(key)
        g = self.gold
        if self.spec.tracker == "jira":
            uname, dform, dname = v
            g.add(e, "username", uname, file, rec_key, f"{path}.username", None, f"{surface}.username", False)
            g.add(e, dform, dname, file, rec_key, f"{path}.display_name", None, f"{surface}.display_name", False)
            return {"username": uname, "display_name": dname}
        email, dform, dname, nick = v
        g.add(e, "email", email, file, rec_key, f"{path}.login", None, f"{surface}.login", False)
        tb = TextBuilder().mention(e, dform, dname, synthetic=False).lit(" [:")
        tb.mention(e, "username", nick, synthetic=False, accept=["username"]).lit("]")
        return {"login": email, "display_name": tb.register(g, file, rec_key, f"{path}.display_name",
                                                            f"{surface}.display_name")}

    def human_entity(self, key) -> Entity | None:
        p = self.person("issues", key)
        return self.ents.get(p) if p not in (None, NOBODY) else None

    # ---------------------------------------------------------------- text helpers
    def distractor(self, tb: TextBuilder) -> None:
        prod = self.spec.product
        ml, ml2 = f"dev@{LIST_NAME[prod]}.apache.org", f"user@{LIST_NAME[prod]}.apache.org"
        job = CI_JOB[prod]
        options = [
            (f"Please move the design discussion to {ml}.", {ml: "mailing_list"}),
            ("{code}\n@Override\npublic void process(WatchedEvent event) {\n    // intentionally empty\n}\n{code}",
             {"@Override": "code"}),
            ("The javadoc still needs @param and @since tags for the new methods.", {"@param": "code", "@since": "code"}),
            (f"Hadoop QA flagged it, but the Jenkins job {job} was already failing before this patch.",
             {"Hadoop QA": "bot", "Jenkins": "product", job: "ci_job"}),
            ("We get Jackson and Guava transitively through Apache Curator, so please check the dependency tree.",
             {"Jackson": "library", "Guava": "library", "Apache Curator": "library"}),
            (f"I asked on {ml2} whether anyone still relies on the old behaviour.", {ml2: "mailing_list"}),
        ]
        text, ds = self.rng.choice(options)
        tb.lit("\n\n" + text)
        self.distractors.update(ds)

    def comment_body(self, text: str, me: Entity, others: list[Entity], count: int) -> TextBuilder:
        tb = TextBuilder()
        spec, rng = self.spec, self.rng
        if not others or rng.random() >= spec.comment_mention_rate:
            return tb.lit(text)
        tgt = rng.choice(others)
        idt = self.idents[self.p_of(tgt)]
        if spec.tracker == "jira":
            style = rng.randrange(6)
            if style == 0:
                tb.lit("Hi ").mention(tgt, "first_name", idt.first).lit(", ").lit(text)
            elif style == 1:
                tb.lit("[~").mention(tgt, "username", idt.usernames[0], accept=["username"]).lit("] ").lit(text)
            elif style == 2:
                tb.lit(text).lit(" Thanks, ").mention(tgt, "first_name", idt.first).lit("!")
            elif style == 3:
                tb.lit(text).lit("\n\nThanks [~").mention(tgt, "username", idt.usernames[0],
                                                         accept=["username"]).lit("] for the review.")
            elif style == 4:
                tb.lit(text).lit("\n\nI've committed this. Thanks ").mention(tgt, "full_name", idt.full_name).lit("!")
            else:
                tb.mention(tgt, "first_name", idt.first).lit(": ").lit(text)
            return tb
        style = rng.randrange(4)
        name, nick = self.bz_display(tgt)
        if style == 0 and count > 0:
            dform = "initial_last" if name == idt.initial_last else "full_name"
            tb.lit("(In reply to ").mention(tgt, dform, name).lit(" [:").mention(tgt, "username", nick, accept=["username"])
            tb.lit(f"] from comment #{rng.randint(1, count)})\n").lit(text)
        elif style == 1:
            tb.lit(text).lit("\n\nThanks ").mention(tgt, "first_name", idt.first).lit("!")
        elif style == 2:
            tb.lit(":").mention(tgt, "username", nick, accept=["username"]).lit(", ").lit(text)
        else:
            tb.lit(text).lit("\n\nFlags: needinfo?(").mention(tgt, "email", self.login_email(tgt)).lit(")")
        return tb

    def third_party(self) -> Entity:
        """Someone mentioned only in prose: no account or record of their own."""
        keys = [p for p in self.ents if p <= -100]
        if len(keys) < self.spec.third_parties and (not keys or self.rng.random() < 0.5):
            p = -100 - len(keys)
            self.idents[p] = self.factory.source_identity(n_usernames=1)
            e = Entity(entity_id=f"E{len(self.ents) + 1:02d}", source_ref=f"synthetic:third_party:{len(keys) + 1}",
                       tokens={})
            self.ents[p] = e
            self.gold.entities.append(e)
            return e
        return self.ents[self.rng.choice(keys)]

    def hard_mention(self, tb: TextBuilder, ikey, me: Entity, others: list[Entity]) -> None:
        """Mentions that exact-string replacement of structured aliases cannot catch."""
        rng, bz = self.rng, self.spec.tracker == "bugzilla"
        kinds = ["third_party", "nickname"] + ([] if bz else ["mail_quote", "fork_url", "people_url"])
        kind = rng.choice(kinds)
        if kind in ("nickname", "mail_quote") and not others:
            kind = "third_party"
        if kind == "third_party" and not self.spec.third_parties:
            kind = "nickname" if others else "fork_url" if not bz else None
        if kind is None:
            return
        if kind == "third_party":
            e = self.third_party()
            d = self.idents[self.p_of(e)]
            if e.entity_id not in self.third_seen:
                self.third_seen.add(e.entity_id)
                style = rng.randrange(3)
                if style == 0:
                    tb.lit("\n\nI ran into the same problem last week; ").mention(e, "full_name", d.full_name)
                    tb.lit(" from our storage team has a workaround.")
                elif style == 1:
                    tb.lit("\n\n").mention(e, "full_name", d.full_name).lit(" (").mention(e, "email", d.work_email)
                    tb.lit(") reported the same failure on our internal cluster.")
                else:
                    tb.lit("\n\nLooping in ").mention(e, "full_name", d.full_name).lit(", who wrote the original client code.")
            else:
                tb.lit("\n\n").mention(e, "first_name", d.first).lit(" confirmed that the workaround helps.")
        elif kind == "nickname":
            tgt = rng.choice(others)
            p = self.p_of(tgt)
            d = self.idents[p]
            nick = d.nickname
            if nick and not self.lex.is_word(nick) and nick not in {x.first for x in self.idents.values()}:
                self.nick_used.add(p)
                tb.lit("\n\n").mention(tgt, "nickname", nick).lit(", could you rebase this on trunk?")
            else:
                tb.lit("\n\n").mention(tgt, "first_name", d.first).lit(", could you rebase this on trunk?")
        elif kind == "mail_quote":
            tgt = rng.choice(others)
            d = self.idents[self.p_of(tgt)]
            date = f"{rng.choice(['Mon', 'Tue', 'Wed', 'Thu', 'Fri'])}, {rng.randint(1, 28)} " \
                   f"{rng.choice(['Mar', 'Jun', 'Sep', 'Nov'])} {rng.randint(2010, 2019)}"
            tb.lit(f"\n\nOn {date}, ").mention(tgt, "last_first", d.last_first).lit(" <")
            tb.mention(tgt, "email_obfuscated", obfuscate_email(d.work_email)).lit("> wrote:\n")
            tb.lit("> Is there a simpler way to reproduce this?")
        else:
            d = self.idents[self.p_of(me)]
            if kind == "fork_url":
                tb.lit("\n\nRebased version on my fork: https://github.com/")
                tb.mention(me, "username", d.usernames[0], accept=["username"]).lit(f"/{self.spec.repo}/tree/{ikey}")
            else:
                tb.lit("\n\nThe patch is also at https://people.apache.org/~")
                tb.mention(me, "username", d.usernames[0], accept=["username"]).lit(f"/{ikey}.patch")

    # ---------------------------------------------------------------- render
    def render(self, out_root: Path) -> dict:
        spec, rng, gold, sel = self.spec, self.rng, self.gold, self.sel
        bz = spec.tracker == "bugzilla"
        issue_rows, commit_rows, comment_rows, ci_rows = [], [], [], []
        participants: dict[str, list[Entity]] = defaultdict(list)
        assignee_of: dict[str, Entity | None] = {}
        key_of: dict[int, object] = {}

        for iid in sel.issues:
            row = sel.cand[iid]["row"]
            ikey = int(row.issue_id) if bz else row.issue_key
            key_of[iid] = ikey
            summary = strip_commit_message(str(row.summary), spec.source)[0]
            if bz:
                rec = {"id": ikey, "product": row["product"], "component": _nan(row.component),
                       "status": _nan(row.status), "resolution": _nan(row.resolution), "severity": _nan(row.severity),
                       "priority": _nan(row.priority), "version": _nan(row.version),
                       "creation_time": _ts(row.created), "last_change_time": _ts(row.updated), "summary": summary}
                rec["creator"] = self.account(row.creator_key, self.f_iss, ikey, "creator", "bugs.creator")
                rec["assigned_to"] = self.account(row.assignee_key, self.f_iss, ikey, "assigned_to", "bugs.assigned_to")
                roles = [("creator", row.creator_key), ("assigned_to", row.assignee_key)]
            else:
                rec = {"issue_key": ikey, "project": row["product"], "type": _nan(row.issuetype),
                       "status": _nan(row.status), "priority": _nan(row.priority), "resolution": _nan(row.resolution),
                       "component": _nan(row.component), "created": _ts(row.created), "updated": _ts(row.updated),
                       "summary": summary}
                rec["reporter"] = self.account(row.reporter_key, self.f_iss, ikey, "reporter", "issues.reporter")
                rec["assignee"] = self.account(row.assignee_key, self.f_iss, ikey, "assignee", "issues.assignee")
                rec["creator"] = self.account(row.creator_key, self.f_iss, ikey, "creator", "issues.creator")
                roles = [("reporter", row.reporter_key), ("assignee", row.assignee_key), ("creator", row.creator_key)]
            for _, k in roles:
                e = self.human_entity(k)
                if e is not None and e not in participants[ikey]:
                    participants[ikey].append(e)
            assignee_of[ikey] = self.human_entity(row.assignee_key)
            desc_paras = [p.strip() for p in re.split(r"\n\s*\n", str(_nan(row.description) or "")) if p.strip()]
            desc = _clip_paragraphs([p for p in desc_paras if reject_reason(p, self.lex) is None], spec.max_text_chars)
            tb = TextBuilder().lit(desc)
            target = assignee_of[ikey] or (participants[ikey][0] if participants[ikey] else None)
            if target is not None and rng.random() < spec.description_mention_rate:
                if bz:
                    tb.lit("\n\nCC'ing :" if desc else "CC'ing :").mention(target, "username", self.bz_display(target)[1],
                                                                          accept=["username"]).lit(" who knows this code.")
                else:
                    tb.lit("\n\n" if desc else "").lit("[~").mention(target, "username", self.mention_username(target),
                                                                     accept=["username"]).lit("] can you take a look?")
            if spec.distractor_rate and not bz and rng.random() < spec.distractor_rate / 2:
                self.distractor(tb)
            rec["description"] = tb.register(gold, self.f_iss, ikey, "description", f"{Path(self.f_iss).stem}.description")
            issue_rows.append(rec)

        build_no = 1000 + rng.randrange(4000)
        for iid in sel.issues:
            ikey = key_of[iid]
            for _, cm in sel.cand[iid]["commits"].head(spec.max_commits_per_issue).iterrows():
                rec, ea, ec, subject = self.commit(cm, ikey, assignee_of, participants)
                commit_rows.append(rec)
                if spec.ci_surface and not bz:
                    build_no += rng.randint(1, 9)
                    ci_rows.append(self.ci_build(rec, ea, ec, subject, build_no))

        for iid in sel.issues:
            ikey = key_of[iid]
            for n, (cr, body, bot) in enumerate(sel.comments[iid]):
                cid = int(cr.comment_id)
                text = _clip_paragraphs(body, spec.max_text_chars)
                if bot:
                    acct = dict(BOT_ACCOUNTS[bot])
                    for v in acct.values():
                        self.distractors[v] = "bot"
                    tb = TextBuilder().lit(text)
                else:
                    acct = self.account(cr.author_key, self.f_cmt, cid, "creator" if bz else "author",
                                        "comments.creator" if bz else "comments.author")
                    me = self.human_entity(cr.author_key)
                    if me not in participants[ikey]:
                        participants[ikey].append(me)
                    others = [e for e in participants[ikey] if e is not me]
                    tb = self.comment_body(text, me, others, n)
                    if spec.hard_mention_rate and rng.random() < spec.hard_mention_rate:
                        self.hard_mention(tb, ikey, me, others)
                if spec.distractor_rate and not bz and rng.random() < spec.distractor_rate:
                    self.distractor(tb)
                if bz:
                    rec = {"id": cid, "bug_id": ikey, "count": n, "creator": acct, "creation_time": _ts(cr.created),
                           "text": None}
                    rec["text"] = tb.register(gold, self.f_cmt, cid, "text", "comments.text")
                else:
                    rec = {"comment_id": cid, "issue_key": ikey, "author": acct, "created": _ts(cr.created), "body": None}
                    rec["body"] = tb.register(gold, self.f_cmt, cid, "body", "comments.body")
                comment_rows.append(rec)

        people_rows = self.directory() if spec.include_directory and not bz else []
        return self.write(out_root, issue_rows, commit_rows, comment_rows, people_rows, ci_rows)

    def commit(self, cm, ikey, assignee_of, participants):
        spec, rng, gold = self.spec, self.rng, self.gold
        h = cm.hash
        ea, (af, aname, aemail) = self.git_identity(cm.author)
        ec, (cf, cname, cemail) = self.git_identity(cm.committer)
        for e in (ea, ec):
            if e not in participants[ikey]:
                participants[ikey].append(e)
        f = self.f_com
        acc_a = ["username", "name"] if af == "username" else None
        acc_c = ["username", "name"] if cf == "username" else None
        gold.add(ea, af, aname, f, h, "author.name", None, "commits.author.name", False, acc_a)
        gold.add(ea, "email", aemail, f, h, "author.email", None, "commits.author.email", False)
        gold.add(ec, cf, cname, f, h, "committer.name", None, "commits.committer.name", False, acc_c)
        gold.add(ec, "email", cemail, f, h, "committer.email", None, "commits.committer.email", False)
        text, slots = strip_commit_message(str(cm.message), spec.source)
        lines = text.split("\n")
        subject = lines[0]
        body_paras = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(lines[1:])) if p.strip()]
        body = [p for p in body_paras if p.startswith("git-svn-id:") or p.startswith("(cherry picked")
                or reject_reason(p, self.lex) is None]
        tb = TextBuilder().lit(subject)
        if spec.tracker == "bugzilla":
            others = [e for e in participants[ikey] if e is not ea]
            if others and (slots.moz_review or rng.random() < spec.commit_attribution_rate):
                rev = assignee_of.get(ikey) if assignee_of.get(ikey) not in (None, ea) else rng.choice(others)
                tb.lit(", r=" if not subject.rstrip().endswith(".") else " r=")
                tb.mention(rev, "username", self.bz_display(rev)[1], accept=["username"])
                rest = [e for e in others if e is not rev]
                if rest and rng.random() < 0.3:
                    sr = rng.choice(rest)
                    tb.lit(" sr=").mention(sr, "username", self.bz_display(sr)[1], accept=["username"])
        else:
            contributor = assignee_of.get(ikey) or ea
            pattern = ("via" if slots.via else "contributed" if slots.contributed_by
                       else "paren" if slots.trailing_paren else None)
            if pattern is None and rng.random() < spec.commit_attribution_rate:
                pattern = "contributed"
            if pattern == "via" and contributor is ec:
                pattern = "contributed"
            cident = self.idents[self.p_of(contributor)]
            if pattern == "contributed":
                tb.lit(" " if subject.rstrip().endswith((".", "!", "?")) else ". ")
                tb.lit("Contributed by ").mention(contributor, "full_name", cident.full_name).lit(".")
            elif pattern == "via":
                tb.lit(" (").mention(contributor, "full_name", cident.full_name).lit(" via ")
                tb.mention(ec, "username", self.mention_username(ec), accept=["username", "name"]).lit(")")
            elif pattern == "paren":
                tb.lit(" (").mention(ec, "username", self.mention_username(ec), accept=["username", "name"]).lit(")")
        if body:
            tb.lit("\n\n" + "\n\n".join(body))
        if spec.third_parties and spec.tracker == "jira" and rng.random() < spec.hard_mention_rate / 2:
            co = self.third_party()
            d = self.idents[self.p_of(co)]
            self.third_seen.add(co.entity_id)
            tb.lit("\n\nCo-authored-by: ").mention(co, "full_name", d.full_name).lit(" <")
            tb.mention(co, "email", d.work_email).lit(">")
        rec = {"hash": h, "repo": cm.repo, "parents": [p for p in str(_nan(cm.parents) or "").split() if p],
               "author": {"name": aname, "email": aemail}, "authored_at": _ts(cm.author_time),
               "committer": {"name": cname, "email": cemail}, "committed_at": _ts(cm.commit_time),
               "message": None, ("bug_id" if spec.tracker == "bugzilla" else "issue_key"): ikey,
               "stats": {"added": int(_nan(cm.added) or 0), "removed": int(_nan(cm.removed) or 0)}}
        rec["message"] = tb.register(gold, f, h, "message", "commits.message")
        return rec, ea, ec, subject

    def ci_build(self, commit: dict, ea: Entity, ec: Entity, subject: str, number: int) -> dict:
        rng, gold, f = self.rng, self.gold, self.f_ci
        job = CI_JOB[self.spec.product]
        build_id = f"{job}#{number}"
        status = rng.choice(["SUCCESS", "SUCCESS", "SUCCESS", "FAILURE", "UNSTABLE"])
        user_triggered = rng.random() < 0.4
        ia, ic = self.idents[self.p_of(ea)], self.idents[self.p_of(ec)]
        triggered_by = None
        if user_triggered:
            triggered_by = {"username": ic.usernames[0], "display_name": ic.full_name}
            gold.add(ec, "username", ic.usernames[0], f, build_id, "triggered_by.username", None,
                     "ci_builds.triggered_by.username", True)
            gold.add(ec, "full_name", ic.full_name, f, build_id, "triggered_by.display_name", None,
                     "ci_builds.triggered_by.display_name", True)
        ws = f"/home/jenkins/jenkins-agent/workspace/{job}"
        tb = TextBuilder()
        if user_triggered:
            tb.lit("Started by user ").mention(ec, "full_name", ic.full_name).lit("\n")
        else:
            tb.lit("Started by an SCM change\n")
        tb.lit(f"Running as SYSTEM\nBuilding remotely on H{rng.randint(10, 49)} (ubuntu) in workspace {ws}\n")
        tb.lit(f" > git rev-parse --resolve-git-dir {ws}/.git # timeout=10\n")
        tb.lit(f"Checking out Revision {commit['hash']} (refs/remotes/origin/trunk)\n")
        tb.lit(f"Commit message: \"{subject}\"\nChanges:\n[")
        tb.mention(ea, "username", ia.usernames[0], accept=["username"]).lit(f"] {subject}\n")
        if user_triggered and rng.random() < 0.6:
            tb.lit("[INFO] Using local repository at /home/").mention(ec, "username", ic.usernames[0],
                                                                       accept=["username"]).lit("/.m2/repository\n")
        tb.lit("[INFO] Scanning for projects...\n")
        if status == "SUCCESS":
            tb.lit("[INFO] BUILD SUCCESS\n")
        else:
            tb.lit(f"[ERROR] Tests run: {rng.randint(200, 900)}, Failures: {rng.randint(1, 3)}, Errors: 0\n")
            tb.lit("[INFO] BUILD FAILURE\n" if status == "FAILURE" else "[WARNING] Build UNSTABLE\n")
        tb.lit("Recording test results\nSending e-mails to: ").mention(ec, "email", commit["committer"]["email"])
        if ea is not ec:
            tb.lit(" ").mention(ea, "email", commit["author"]["email"])
        tb.lit(f"\nFinished: {status}")
        self.distractors[job] = "ci_job"
        self.distractors["/home/jenkins"] = "system_account"
        rec = {"build_id": build_id, "job": job, "commit": commit["hash"], "status": status,
               "started_at": commit["committed_at"], "duration_s": rng.randint(300, 5400),
               "triggered_by": triggered_by, "log": None}
        rec["log"] = tb.register(gold, f, build_id, "log", "ci_builds.log")
        return rec

    def directory(self) -> list[dict]:
        gold, f = self.gold, self.f_ppl
        git_by_p: dict[int, list] = defaultdict(list)
        trk_by_p: dict[int, list] = defaultdict(list)
        for key, v in self.git_variant.items():
            git_by_p[self.person("commits", key)].append(v)
        for key, v in self.trk_variant.items():
            trk_by_p[self.person("issues", key)].append(v)
        rows = []
        for i, (p, e) in enumerate((p, e) for p, e in self.ents.items() if p > -100):
            pid = f"dev-{i + 1:03d}"
            d = self.idents[p]
            accounts, emails = [], []
            for (_nf, nm, em) in git_by_p[p]:
                accounts.append({"system": "git", "name": nm, "email": em})
                if em not in emails:
                    emails.append(em)
            for (un, _df, dn) in trk_by_p[p]:
                accounts.append({"system": "jira", "username": un, "display_name": dn})
            rows.append({"person_id": pid, "display_name": d.full_name, "emails": emails, "accounts": accounts})
            gold.add(e, "full_name", d.full_name, f, pid, "display_name", None, "people.display_name", False)
            for j, em in enumerate(emails):
                gold.add(e, "email", em, f, pid, f"emails.{j}", None, "people.emails", False)
            for j, acc in enumerate(accounts):
                if acc["system"] == "git":
                    nf = next(v[0] for v in git_by_p[p] if v[1] == acc["name"] and v[2] == acc["email"])
                    gold.add(e, nf, acc["name"], f, pid, f"accounts.{j}.name", None, "people.accounts.name",
                             False, ["username", "name"] if nf == "username" else None)
                    gold.add(e, "email", acc["email"], f, pid, f"accounts.{j}.email", None, "people.accounts.email", False)
                else:
                    gold.add(e, "username", acc["username"], f, pid, f"accounts.{j}.username", None,
                             "people.accounts.username", False)
                    df = next(v[1] for v in trk_by_p[p] if v[0] == acc["username"])
                    gold.add(e, df, acc["display_name"], f, pid, f"accounts.{j}.display_name", None,
                             "people.accounts.display_name", False)
        return rows

    # ---------------------------------------------------------------- output
    def write(self, out_root, issue_rows, commit_rows, comment_rows, people_rows, ci_rows) -> dict:
        spec, gold, sel = self.spec, self.gold, self.sel
        bz = spec.tracker == "bugzilla"
        for p, e in self.ents.items():
            d = self.idents[p]
            emails = {a.value for a in e.aliases if a.form == "email"}
            emails |= {a.value.replace(" at ", "@", 1).replace(" dot ", ".") for a in e.aliases
                       if a.form == "email_obfuscated"}
            usernames = sorted({a.value for a in e.aliases if a.form == "username"} | set(d.usernames))
            names = [d.first, d.last] + ([d.nickname] if p in self.nick_used else [])
            e.tokens = {"names": names, "usernames": usernames, "emails": sorted(emails),
                        "email_locals": sorted({m.split("@")[0] for m in emails})}
        personas = self.factory.persona_pool(pool_size(len(self.ents)))

        out = out_root / spec.task_id
        if out.exists():
            shutil.rmtree(out)
        inp = out / "input"
        files = {self.f_com: commit_rows, self.f_iss: issue_rows, self.f_cmt: comment_rows}
        if people_rows:
            files[self.f_ppl] = people_rows
        if ci_rows:
            files[self.f_ci] = ci_rows
        for rel, rows in files.items():
            write_jsonl(inp / rel, rows)
        (inp / "pseudonyms.json").write_text(json.dumps([p.to_json() for p in personas], indent=2) + "\n")
        listing = "\n".join(f"- {rel}  ({FILE_DOCS[rel]})" for rel in files)
        (inp / "TASK.md").write_text(POLICY.format(root="the current working directory", files=listing))

        fk_issue = "bug_id" if bz else "issue_key"
        gold.files = {
            self.f_com: {"key": "hash", "fk": {fk_issue: self.f_iss}, "text_fields": ["message"]},
            self.f_iss: {"key": "id" if bz else "issue_key", "fk": {}, "text_fields": ["summary", "description"]},
            self.f_cmt: {"key": "id" if bz else "comment_id", "fk": {fk_issue: self.f_iss},
                         "text_fields": ["text" if bz else "body"]},
        }
        if people_rows:
            gold.files[self.f_ppl] = {"key": "person_id", "fk": {}, "text_fields": []}
        if ci_rows:
            gold.files[self.f_ci] = {"key": "build_id", "fk": {"commit": self.f_com}, "text_fields": ["log"]}
        gold.distractors = [{"value": v, "kind": k} for v, k in sorted(self.distractors.items())]
        real = ["commits", "bugs" if bz else "issues", "comments"]
        synthetic = ["all identities", "injected mentions (synthetic=true)"]
        if people_rows:
            synthetic.append("people directory (rendering of real merge clusters)")
        if ci_rows:
            synthetic.append("CI builds and logs (linked to real commit hashes)")
        if sel.collisions:
            synthetic.append(f"{len(sel.collisions)} deliberate name collision(s) between distinct people")
        if self.distractors:
            synthetic.append("injected non-PII distractor sentences")
        gold.provenance = {
            "source": "20-MAD sample (M3SOulu/MozillaApacheDataset-sample, CC-BY 4.0)",
            "project": f"{spec.source}/{spec.product} + repo {spec.repo}",
            "real_structure": real,
            "synthetic": synthetic,
            "seed_person": f"mad:{spec.source}:{sel.seed_person}",
            "collisions": [[self.ents[a].entity_id, self.ents[b].entity_id] for a, b in sel.collisions],
            "issue_keys": [r.get("issue_key", r.get("id")) for r in issue_rows],
            "spec": asdict(spec),
        }
        (out / "gold.json").write_text(json.dumps(gold.to_json(), indent=1, ensure_ascii=False) + "\n")
        audit = self.audit(inp, personas)
        (out / "audit.json").write_text(json.dumps(audit, indent=1, ensure_ascii=False) + "\n")
        return audit

    def audit(self, inp: Path, personas) -> dict:
        from .grader.leaks import combined_pattern
        from .grader.score import decoded_text

        gold, spec = self.gold, self.spec
        problems = []
        raw = {rel: decoded_text(inp / rel) for rel in gold.files}
        rx = combined_pattern(gold.entities)
        for rel, text in raw.items():
            found = len(rx.findall(text))
            expected = sum(len(rx.findall(o.value)) for o in gold.occurrences if o.file == rel)
            if found != expected:
                problems.append(f"sensitive tokens in {rel}: found {found}, annotated {expected}")
        tgt_rx = re.compile(r"\b(" + "|".join(sorted(set(TARGET_FIRST) | set(TARGET_LAST))) + r")\b")
        for rel, text in raw.items():
            if m := tgt_rx.search(text):
                problems.append(f"pseudonym-pool name in input {rel}: {m.group(0)}")
        if problems:
            raise RuntimeError(f"{spec.task_id}: gold validation failed: {problems}")
        carrier_caps: Counter = Counter()
        for text in raw.values():
            for w in CAP_WORD_RE.findall(text):
                if not self.lex.is_word(w):
                    carrier_caps[w] += 1
        names = {t for e in gold.entities for t in e.tokens["names"]}
        counts = {rel: sum(1 for line in (inp / rel).read_text(encoding="utf-8").splitlines() if line.strip())
                  for rel in gold.files}
        return {
            "task_id": spec.task_id,
            "records": counts,
            "entities": len(gold.entities),
            "pool_size": len(personas),
            "occurrences": len(gold.occurrences),
            "occurrences_synthetic": sum(o.synthetic for o in gold.occurrences),
            "occurrences_free_text": sum(o.span is not None for o in gold.occurrences),
            "occurrences_by_surface": dict(sorted(Counter(o.surface for o in gold.occurrences).items())),
            "aliases_per_entity": {e.entity_id: len(e.aliases) for e in gold.entities},
            "collisions": gold.provenance["collisions"],
            "distractors": len(gold.distractors),
            "sanitizer_paragraph_decisions": dict(self.sel.rejects.most_common()),
            "non_dictionary_capitalized_tokens": {w: c for w, c in carrier_caps.most_common() if w not in names},
            "validation": "ok",
        }


def load_specs(path: Path) -> list[TaskSpec]:
    return [TaskSpec(**d) for d in json.loads(path.read_text(encoding="utf-8"))]


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .sources.mad import default_data_dir

    ap = argparse.ArgumentParser(description="Generate benchmark tasks from the 20-MAD sample")
    ap.add_argument("--specs", default="configs/tasks.json")
    ap.add_argument("--out", default="tasks")
    ap.add_argument("--data", default=None, help="path to MozillaApacheDataset-sample")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args(argv)
    mad = MadSample(Path(args.data) if args.data else default_data_dir())
    gen = Generator(mad)
    for spec in load_specs(Path(args.specs)):
        if args.only and spec.task_id not in args.only:
            continue
        audit = gen.build(spec, Path(args.out))
        print(json.dumps({k: audit[k] for k in ("task_id", "records", "entities", "occurrences",
                                                 "occurrences_free_text", "collisions", "distractors")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
