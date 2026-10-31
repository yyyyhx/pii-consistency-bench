# Benchmark design

Grader 0.3; ten main tasks plus a two-task hard split. This document describes the code as it is and gives the
reasoning behind each decision. Numbers are reproducible with the commands named next to them.

## 1. What is being evaluated

PII Consistency Bench asks whether a system can clean PII from linked repository and development-operations data
*consistently*: if developer Mike becomes Tom, every Mike identity and alias (name, email, username, author
record, comment, CI reference) must become the same coherent Tom identity on every surface. That combines four
capabilities, and the benchmark scores all four on the same output:

| Capability | What the agent has to do | Where it is scored |
|---|---|---|
| Detection | find every identifier, in structured fields and inside prose, URLs, logs, mail signatures | entity recall (ER), occurrence recall |
| Cross-surface entity resolution | decide which aliases on which surfaces are the same person, and which look-alikes are not | continuity (CC), separation, B-cubed precision/recall |
| Identity-consistent pseudonymization | give each person one persona, rendered in the form each slot needs | continuity, form accuracy |
| Preservation | leave schemas, links and the non-PII text intact | schema, link and text gates; edit precision (P); integrity (RI) |

The system under test is the whole thing: model, agent harness, tools and strategy. Rule-based, schema-aware,
entity-resolution, LLM-agent and hybrid systems all take the same task and are graded only on the files they
leave behind; the final chat message is ignored.

Each task is a small export of linked records in the agent's working directory:

```
/workspace/
├── TASK.md            # cleaning policy and file list (same text as the prompt)
├── pseudonyms.json    # pool of synthetic personas (1.5x the number of people + 2)
└── data/*.jsonl       # commits, issues/bugs, comments, pull requests, reviews, PR comments,
                       # mail, CI builds, a developer directory (which files depends on the task)
```

### The contract given to the agent (`TASK.md`)

1. Replace every reference to a real person, in structured fields and in free text, including usernames
   inside URLs and paths.
2. Map each person to exactly one persona and use it everywhere. Personas come from `pseudonyms.json`, or the
   agent may invent them and list them in `mapping.json` (persona_id, full_name, first_name, last_name, email,
   username). `mapping.json` ships with the cleaned export, so it must list only personas, never an original
   name, email or username.
3. Never map two people to one persona; identical names do not imply the same person.
4. Render the persona in the slot's form (name, email or username).
5. Change nothing else; keep every file valid JSONL with the same records, keys and value types.

### Why a pseudonym pool, and why `mapping.json` exists

The grader has to decide whether `Tom Baker`, `tbaker` and `tom.baker@x` are one coherent identity. With a
pool, each persona is a known tuple (name, email, username), so that check is an exact lookup and needs no
fuzzy matching that could itself be wrong. The pool gives the agent a vocabulary, not an assignment: which
person becomes which persona, and which aliases belong together, is still entirely the agent's decision. Any
one-to-one assignment scores the same (`tests/test_grader.py::test_alternative_valid_assignment_gets_full_credit`).
A rule-based baseline can use the pool too, which keeps the benchmark approach-agnostic.

An agent that invents its own consistent names should not fail for that reason, so invented personas are
accepted when the agent declares them in `mapping.json`. A declared persona is treated exactly like a pool
persona (section 6.3). An invented persona that is not declared is a string the grader cannot attribute, so
continuity is 0 for that person; `TASK.md` says this explicitly.

Open-world scoring, where the grader infers personas from undeclared replacements, was considered and not built
(section 9).

## 2. Data sources and what is real

| Element | t01-t07 (20-MAD) | t08 (SmartSHARK) | t09-t10, h1-h2 (SmartSHARK, linked accounts) |
|---|---|---|---|
| Which records exist, ids, hashes, timestamps, statuses | real | real | real |
| Commit-issue links | real | real | real |
| Which commits a pull request contains | - | not matchable in ZooKeeper | real (`pull_request_commit`) |
| Review and PR-comment authors and states | - | real | real |
| Jira issues linked to the commits and pull requests, their reporters, assignees and comment authors | - | real | real |
| Mail dates, thread references | - | real dates | real |
| CI builds | synthetic, linked to real commits (t06) | - | real Travis build numbers, events, states |
| Which raw identities are one person | real (`idmerging.merged_id`) | one SmartSHARK person id | one SmartSHARK person id |
| Prose | real, sanitized (section 4) | templated | templated |
| Every name, email, username, handle, mention | synthetic | synthetic | synthetic |

Two rules hold everywhere:

* **No real identity is inferred or merged.** 20-MAD person keys are SHA-1 pseudonyms; only the dataset's own
  `merged_id` clustering decides which aliases belong together. SmartSHARK's public dump has no `people` or
  `identity` collection, so one SmartSHARK person id is one entity and two ids are never merged. A Jira account
  and a git/GitHub account are usually different ids in SmartSHARK, and are then different entities. Where
  SmartSHARK keys one person with a single id on both, they are one entity. t08's seeds were chosen that way, and
  t09, h1 and h2 each have one such person.
* **Synthetic identities cannot belong to anyone.** Source names and pool names come from disjoint lists, and
  every domain is reserved (`example.com/.org/.net`, `.example`, `.invalid`).

The inputs keep real commit hashes, issue keys and timestamps, because links between records are part of what is
evaluated. Those are public quasi-identifiers: anyone can look up the real author of a commit hash. The task
files are therefore pseudonymous relative to the real developers, not anonymous; they add no personal data that
is not already public.

## 3. Identity model

```
Entity (one person: a 20-MAD merged_id, or one SmartSHARK person id)
  └── Alias (one rendered identifier: full name, first name, "F. Last", "Last, First", nickname,
  │          email, obfuscated email, username, GitHub handle, ...)
        └── Occurrence (an alias at a location: file, record key, field path, optional character span)
Records are linked by foreign keys (issue_key, bug_id, commit hash, pull_request_id, in_reply_to, ...)
```

The same graph drives sampling, gold generation (an occurrence is recorded every time an alias is rendered),
grading (results are aggregated per entity) and failure analysis (which surface or link broke).

## 4. Task families

### t01-t07: 20-MAD slices (Hadoop, HDFS, ZooKeeper, Thunderbird)

`python -m piibench.generate` (specs in `configs/tasks.json`) picks a seed developer with several raw identities,
selects connected issues, commits and comments around them, gives each person a synthetic identity, and renders
each raw key of that person as a different alias variant (`Alice Chen <alice.chen@...>`, `achen
<achen@users.noreply...>`, a second tracker account shown as `A. Chen`). The tasks differ in tracker (Jira or
Bugzilla), surfaces (a developer directory, synthetic CI logs), and planted difficulty: a name collision (t04),
long history (t05), distractors such as bots, mailing lists and `@Override` (t07), and hard free-text mentions
(nicknames, mail-quote headers, URL usernames, people who only appear in prose).

20-MAD's prose is not pseudonymized, so a sanitizer strips attribution slots (`Contributed by`, `via`, `r=`,
trailers) and drops any paragraph that still contains a name or handle seen anywhere in the sample's
attribution slots, a capitalized non-dictionary word, or a mention pattern. Mentions are then injected where the
real data had the same pattern. Generation is deterministic: regenerating t01 and t04 from the raw sample gives
byte-identical files.

### t08: SmartSHARK ZooKeeper, one identity on every surface

Built by `scripts/build_smartshark_linked.py` (seed mode `shared_id`). The seeds are person ids that appear on
git, GitHub and the mailing list: people who used one name and address everywhere, so SmartSHARK keyed them
once. Each seed brings up to two of their commits (linked Jira issues first), up to two pull requests (those
naming an issue first), their mail, and the linked issues with their reporters, assignees and one comment. Every
commit points at a real Jira issue, half of the pull requests name one, and some mails reply to each other. The
free-text aliases are the point of this task: a fork URL that uses the person's second username (the account is
`emeroy`, the fork is `github.com/emeric.oyelaran/zookeeper`), nicknames in Jira comments, and mail signatures with
an obfuscated address (`a.b at lab dot example dot org`). ZooKeeper's pull-request commits do not match its git
history by author, so t08 cannot have the opaque-handle evidence of t09-t10.

### t09-t10: SmartSHARK linked accounts (Storm, Curator)

`scripts/build_smartshark_linked.py` with `scripts/smartshark_export.js`. These two tasks remove the shortcuts
that let careful string matching solve most of t01-t08 (section 8). They have eight files (commits, Jira issues
and comments, pull requests, reviews, PR comments, mail, Travis CI builds), 108-122 records and 25-27 people, and
no developer directory.

* **Seeds** are developers who authored a git commit that also appears, under the same person id, in a pull
  request they opened. That `pull_request_commit` row is real record-level evidence connecting the developer's
  GitHub account and git identity.
* **Opaque GitHub handles.** For 60% (t09) or 40% (t10) of the seeds, the GitHub account is a handle that shares
  nothing with the name (`silentpuffin80`) and shows no profile name. The handle also appears in an ASF GitHub Bot
  notice on the linked Jira issue ("GitHub user silentpuffin80 opened a pull request") and, for some people, in a
  `TODO(handle)` note. What ties it to the person is the commits the pull request lists, and for some people a mail
  signature that names both (`Farhan Rautio (GitHub: quietwren83)`). The generator asserts that every opaque handle
  has at least one listed commit authored by its owner.
* **Shared first names (two pairs in t09, one in t10).** A developer and a Jira-only person share a first name but never
  take part in the same thread. Each first-name mention ("Thanks, Seraphina!") is therefore unique where it appears,
  while the name as a string belongs to two people, so a global first-name-to-persona rule gets one of them wrong.
  `piibench.solvability` enforces this (section 5).
* **Namesakes in one thread (t10).** The creator of a pull request and the person who approved it share a full
  name. GitHub does not let the account that opened a pull request approve it, and the approval thanks the
  author by name, so the records show two people. This is the opposite of t04, where the namesakes never meet and
  a directory separates them.
* **The ASF GitHub Bot** is a distractor (keep the bot account), but the handle inside its text is PII.
* **Person-like non-PII.** Half of the issue descriptions, PR descriptions and mails carry a sentence with an
  eponymous technical term (Bloom filter, Levenshtein distance, Dijkstra's algorithm, Fenwick tree, Kahan
  summation) or a product or language named like a person (Cassandra, Ruby, Julia). They are listed as distractors;
  changing them is over-scrubbing.

All three SmartSHARK tasks (t08-t10) also carry identity forms that real exports contain and plain string matching
handles badly:

* **Spelling variants.** A developer's git identity is ASCII, while their GitHub profile and mail show the native
  spelling (`Wójcik`, `Zieliński`, `Bergström`, `Oyèláràn`), a middle initial (`Quentin J. Wojcik`) or an all-lowercase
  name. The grader compares identities accent-insensitively (section 6.2), so leaving `Zielinski` where the record
  said `Zieliński` is a leak.
* **Commit and code metadata.** `Signed-off-by:` and `Co-authored-by:` trailers in commit messages, a javadoc
  `@author` line in pull-request descriptions (only for developers whose handle is derived from their name, so it
  never gives away an opaque handle), and `TODO(handle)` in pull-request comments.
* **Profile links and quoted signatures.** Jira comments that link the assignee's profile page
  (`ViewProfile.jspa?name=<username>`), and mail replies that quote the previous message's signature, so one
  message carries two people.
* All prose is templated; no SmartSHARK text is exported. The export prints structure only (ids, hashes, dates,
  numbers, states, issue keys parsed from PR titles) and is never written to disk.

| ID | Source | Records | People | Occurrences (free text) | What it targets |
|---|---|---|---|---|---|
| t01 | 20-MAD ZooKeeper + directory | 36 | 6 | 138 (13) | easy control |
| t02 | 20-MAD HDFS | 54 | 9 | 199 (49) | aliases linked only through context |
| t03 | 20-MAD Thunderbird (Bugzilla) | 32 | 7 | 135 (76) | email logins, `[:nick]`, `r=` handles |
| t04 | 20-MAD Hadoop + directory | 50 | 8 | 192 (34) | two people with one name, separated by the directory |
| t05 | 20-MAD Hadoop, long history | 103 | 8 | 422 (96) | recall over many records |
| t06 | 20-MAD ZooKeeper + synthetic CI | 41 | 4 | 140 (36) | logs, `/home/<user>`, notification mails |
| t07 | 20-MAD HDFS + distractors | 45 | 4 | 165 (28) | over-scrubbing bots, lists, code |
| t08 | SmartSHARK ZooKeeper | 94 | 36 | 336 (96) | real issue links; fork-URL usernames, nicknames, obfuscated addresses, spelling variants, trailers |
| t09 | SmartSHARK Storm | 122 | 27 | 416 (128) | opaque GitHub handles, shared first names, person-like terms, CI, spelling variants |
| t10 | SmartSHARK Curator | 108 | 25 | 344 (96) | namesakes in one pull request, personal mail addresses, spelling variants |

### Why these tasks, and not more of them

Each task is a different project, surface combination, identity pattern or difficulty slice. The aim is coverage
of identity patterns, not of the source datasets. Size alone does not add difficulty: t05 has the most occurrences
(103 records, 422 occurrences), but almost all of its aliases are derivable from the names (section 8). What adds
difficulty is the identity pattern, so t09, t10 and the hard split each introduce patterns no other task has,
rather than more records of the same kind. SmartSHARK provides five tasks, including every pull-request, review,
PR-comment, mail-thread and real-CI surface in the benchmark.

### The hard split: h1-h2

In t09 and t10, the people who can only be resolved from record evidence (anonymous handles, shared names) are a
minority of each task, so a system can score well while barely being tested on them. The hard split makes those
people the majority. It is built by the same generator (`scripts/build_smartshark_linked.py --hard`, into
`tasks_hard/`) and keeps the same records, rules and grader. It is reported separately from the ten main tasks.

What changes, compared with t09 and t10:

* **Every developer's GitHub handle is opaque, and the commits are the only link.** No mail signature names the
  handle, and reviewers and commenters address an opaque developer as `@handle` rather than by first name. The
  only way to connect a handle to a person is the commits its pull request lists. A check confirms that no record
  in either task contains both an opaque handle and its owner's name.
* **More people share names.** h1 has three pairs who share a first name. h2 has three pairs who share a full
  name inside one pull request, and two pairs who share a first name.

The evidence that each namesake pair is two people:

* In two of the h2 pairs, the second person *approved* the first person's pull request ("LGTM, thanks Bartosz!").
  GitHub does not let an author approve their own pull request.
* In the third pair, the second person thanks the author by handle and gives a +1 ("Thanks for the patch
  @wquiroga. +1"). That is weaker than an approval, but still separates them.
* In each of the two approval pairs, one commit also carries a `Co-authored-by:` trailer naming the reviewer. The
  reviewer appears under an address that occurs nowhere else. The gold assigns that trailer to the reviewer: a
  commit's co-author is by definition someone other than its author, and the reviewer is the only other person
  with that name. This is the finest-grained judgement in the benchmark. A system that separates the pair everywhere
  except on this line still has that person's continuity marked wrong.

The objection a reader could raise is that one developer might approve their own work from a second account. The
records argue against it: the reviewer thanks the author by name, and `TASK.md` rule 3 says that identical names
do not imply the same person.

| ID | Source | Records | People | Occurrences (free text) | Opaque handles | Name-derivable occurrences | Rule baseline (people fully right) |
|---|---|---|---|---|---|---|---|
| h1 | SmartSHARK Storm | 142 | 31 | 481 (149) | 10 | 80% | 0.851 (58%) |
| h2 | SmartSHARK Curator | 119 | 29 | 385 (111) | 7 | 61% | 0.812 (55%) |

Both tasks pass the same generation checks as the main ten (PII scan, solvability, oracle = 1, noop = 0) and
rebuild byte for byte.

## 5. Gold manifest

`tasks/<task_id>/gold.json` is never copied into the agent's workspace:

```jsonc
{
  "task_id": "...", "seed": 0, "generator_version": "...",
  "provenance": {"source": "...", "real_structure": [...], "synthetic": [...], "collisions": [...],
                 "opaque_handles": [...], "shared_first_names": [...]},
  "entities": [{"entity_id": "E01", "source_ref": "smartshark:<id>",
                "tokens": {"names": [...], "emails": [...], "email_locals": [...], "usernames": [...]},
                "aliases": [{"alias_id": "E01.a3", "form": "email", "value": "..."}]}],
  "occurrences": [{"occ_id": "O0042", "entity_id": "E01", "alias_id": "E01.a3", "form": "email",
                   "file": "data/commits.jsonl", "key": "3f2a...", "path": "author.email", "span": null,
                   "surface": "commits.author.email", "synthetic": false, "accept": ["email"]}],
  "distractors": [{"value": "ASF GitHub Bot", "kind": "bot"}],
  "files": {"data/pull_requests.jsonl": {"key": "id", "fk": {"commits.*": "data/commits.jsonl"},
                                         "text_fields": ["title", "description"]}}
}
```

`span` is a half-open character range in the original field value; `accept` lists the persona form classes
allowed in the slot (`name`, `email`, `username`); `fk` paths may use `*` for list elements.

Every task passes four checks at generation time (and as tests):

1. Completeness: every gold token found in the decoded input lies inside a labelled occurrence.
2. A gold-independent PII scan (`python -m piibench.pii_scan tasks/<id>`): emails on non-reserved domains,
   usernames in URL and path positions, non-dictionary tokens after attribution cues ("by", "thanks", "r="),
   and capitalized given names that are not known synthetic tokens. Check 1 can only find tokens the gold already
   knows, and the grader uses the same tokens, so a real name the sanitizer missed would be invisible to both and
   an agent that removed it would be charged with over-scrubbing. Check 2 does not share that blind spot. All twelve
   tasks (main and hard split) are clean (`tests/test_pii_scan.py`); it still cannot recognise a real name that is
   also an ordinary English word.
3. Solvability (`python -m piibench.solvability tasks/<id>`, `tests/test_solvability.py`): every first-name or
   nickname mention is unique among the people of its own thread, not counting the speaker. A thread is an issue
   with its comments, referencing commits and naming pull requests; a pull request with its reviews, comments, listed
   commits and linked issue; or a mail thread. Without this, a gold label could depend on a coin flip the agent
   cannot see.
4. The gold applied by the oracle scores 1 and doing nothing scores 0 (`tests/test_grader_contract.py`).

On top of the automatic checks, the attribution of free-text mentions was audited by hand. Mentions are the part
no script can verify ("Thanks, Seraphina!": which Seraphina?). A random sample of 40 free-text occurrences in
t08-t10 was printed with its record and thread (`python scripts/sample_gold.py --n 40 --seed 0`). Every one of the
40 attributions is supported by the records: the person addressed is the reporter or creator of that thread, or
the handle belongs to the pull request whose commits that person authored.

## 6. Grading (grader 0.3)

The grader reads the pristine input (kept beside the gold, outside the workspace), the agent's output and the
manifest. It is pure standard library and deterministic.

### 6.1 Recovering each replacement

For a structured occurrence the replacement is the output value at `path`. For free text, the original value is
cut into the non-PII pieces around the gold spans; if those pieces are found in order in the output, whatever sits
between them is the replacement for each span.

Otherwise the agent also changed some non-PII text, and a character-level `difflib` alignment is used:

* **The spans are masked first.** A name and its replacement often share characters (` at `, ` dot `, a letter).
  If those coincidences were allowed to match, they would decide where a replacement starts and ends. For
  example, an agent that rewrote `Rosalind Zamora [:rosalind-zam]` as `Henrik Lindgren [hlindgren]` (dropping only
  the `:`) would have its replacement cut in the middle of a name. So only the non-PII text can anchor the
  alignment.
* **Each span's replacement** is whatever lies between the anchor before it and the anchor after it.
* **Every gap between anchors that loses or changes non-PII text counts as one over-scrub edit.**
  Whitespace-only insertions and deletions are not counted.

A value more than 20 times longer than the original (and over 200,000 characters) is not aligned: its spans count
as unresolved and it is one over-scrub.

### 6.2 Leaks

All identity comparisons run on folded text: accents removed (including letters such as `ø`, `ł`, `ß`, `þ` that
Unicode does not decompose) and case folded, so `Søren`, `Soren` and `SOREN` are the same token.

An occurrence is leaked when its replacement still identifies the person:

* any of the person's tokens (first name, surname, email, email local part, username, nickname) appears with no
  letter or digit on either side, which catches half-done replacements such as `Alice Chen` -> `Tom Chen`; or
* any token of four or more characters appears anywhere inside the replacement, which catches the real name glued
  into a longer string (`AliceChen`, `xachen`), which a boundary-only match would count as clean. This substring
  rule is applied only to the replacement of the person's own occurrence, never to the whole output, where a short
  nickname (`Vale`) is part of ordinary words (`evaluate`). A test checks that no pool persona of any task contains an
  original identifier as a substring.

On top of that, every decoded string value in the output files and in `mapping.json` is scanned for every
person's tokens (residual hits), which catches a name copied into another field. A token shared by two people
(t04, t09, t10, h1, h2) is charged to a person only for hits not already explained by a leaked occurrence of the other.
A stray shared surname is charged to both, because it cannot be attributed.

### 6.3 Persona lookup and declared personas

A replacement is matched, ignoring case and extra whitespace, against every rendering of every pool and declared
persona: full name, first name, last name, `F. Last`, `Last, First`, email (also `a.b at x dot org`) and username.
One string can render several forms of one persona (a username equal to the lowercased first name) or forms of
several personas (two personas sharing a surname), so each string keeps all its candidates:

* the occurrence resolves to a persona only if every candidate names the same persona (a string shared by two
  personas proves nothing about continuity);
* the form is accepted if any of that persona's candidate forms is in the slot's `accept` list.

Keeping only the first candidate would be wrong: a consistent, leak-free set of invented personas whose usernames
equal their lowercased first names would score 0.000 on t01 and 0.36-0.70 on other tasks. With all candidates kept
it scores 1.000.

`mapping.json` handling:

* entries must be objects with five non-empty fields; invalid JSON, a non-list, or a missing field is reported in
  `diagnostics.mapping_errors` instead of being dropped silently;
* an entry identical to a pool persona or to an earlier entry is a restatement and is ignored (an agent may list the
  pool personas it used);
* an entry whose full name, email or username is also another persona's is reported, and kept, so the ambiguity
  fails continuity for the people involved, like any merge;
* the file's text is scanned for leaks. A mapping that also lists the real names is a re-identification key
  shipped with the export: entity recall is 0 for every person it names, even when the data files are clean.

### 6.4 Metrics

`E` is the set of people with at least one gold occurrence, `O` the set of occurrences. `changed` means the
replacement differs from the original. Occurrences in a missing record count as changed and not leaked.

| Metric | Formula | In the score |
|---|---|---|
| Entity recall (ER) | `\|{e : no leaked occurrence and no residual hit}\| / \|E\|` | yes |
| Continuity (CC) | `\|{e : at least one changed, non-leaked occurrence; all of them resolve to one persona in an accepted form; no other person uses that persona}\| / \|E\|` | yes |
| Edit precision (P) | `tp / (tp + fp)`, 1 if both are 0. `tp` = changed occurrences, right or wrong; `fp` = edit regions outside gold spans (whitespace-only inserts and deletes excluded) + changed non-PII leaves | yes |
| Integrity (RI) | `1 - failures / checks` over file parses, record presence, foreign-key equality and resolution | yes |
| Occurrence recall | not-leaked occurrences / all, also split into structured and free text and per surface | no |
| Separation | `1 - \|{e : its persona is used by another person}\| / \|E\|` | no |
| People fully right | `\|{e : ER and CC both hold for e}\| / \|E\|`: nothing leaked and one persona of their own | no |
| Strict score | `min(ER, CC, P, RI)` behind the same gates (an alternative conservative composite) | no |
| B-cubed precision / recall | over occurrences, predicted cluster = resolved persona (unresolved or leaked = singleton). Precision falls with merges, recall with splits and misses | no |
| Form accuracy | replaced occurrences whose form class is accepted / replaced occurrences | no |
| Text preservation | non-PII characters kept verbatim / non-PII characters in the original | gate |
| Distractor preservation | bot, list and system strings still present / present in the input | no |

ER and CC measure different things: ER asks whether anything about the person is left, CC whether what was
replaced is one consistent identity that belongs to that person alone. Redacting everything gives ER 1 and CC 0;
a random persona per occurrence gives ER 1 and CC near 0; pseudonymizing only structured fields gives a high CC
and a low ER.

### 6.5 Score

```
gate  = schema valid  AND  no foreign key changed or dangling  AND  text preservation >= 0.90
HM    = 4 / (1/ER + 1/CC + 1/P + 1/RI)        (0 if any component is 0)
score = HM if gate else 0
solved = gate AND ER = CC = P = RI = 1
```

`score_v1` (the same score with the schema gate only) is reported next to the score, to show what the link and
text gates add.

Why each part:

* **Schema gate.** A truncated or retyped export is useless, whatever it does for privacy.
* **Link gate.** Foreign keys are never PII in these tasks, so there is no legitimate reason to change one, and a
  changed key silently corrupts the dataset. Without the gate, repointing every foreign key to a different real
  record still scores 0.853-0.890, because integrity averages failed checks with many passing presence checks.
  A cleaned export whose links are rewired is not a usable dataset.
* **Text gate.** The output must stay readable and useful, and P counts a whole rewritten field as one edit.
  Without the gate, keeping the pseudonyms and deleting all other prose (text preservation 0.01-0.06) still scores
  0.38-0.64 on eight of the ten tasks. The floor of 0.90 sits far below anything a careful cleaner produces and
  far above destruction; small edits keep costing precision.
* **Harmonic mean.** A conservative composite is needed so that high token recall cannot hide a broken identity
  graph. The harmonic mean is pulled down hardest by the weakest component, while partial progress still shows.
  Two caveats, both handled in reporting rather than by changing the formula:
  * ER and CC are shares of people, so one wrong person moves a 27-person task by only about 0.01-0.02 (see
    `WALKTHROUGH.md`). For privacy one exposed person is a total failure for that person. Results therefore lead
    with `solved` (every person right, every gate passed) and report the share of people fully right.
  * The minimum of the four components is the other natural conservative composite. It is reported as the strict
    score next to the harmonic mean.

### 6.6 What each controlled failure scores

`python scripts/grader_sanity_table.py --task t01-zookeeper-directory` (saved in `results/grader_sanity_t01.md`;
`results/grader_sanity_t09.md` for t09). Selected rows:

| Cleaning (t01) | score | schema gate only | ER | CC | P | RI | text kept |
|---|---|---|---|---|---|---|---|
| gold applied (any bijection) | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 1.00 |
| one missed email | 0.952 | 0.952 | 0.83 | 1.00 | 1.000 | 1.000 | 1.00 |
| two people merged into one persona | 0.889 | 0.889 | 1.00 | 0.67 | 1.000 | 1.000 | 1.00 |
| redact everything | 0.000 | 0.000 | 1.00 | 0.00 | 1.000 | 1.000 | 1.00 |
| structured fields only | 0.667 | 0.667 | 0.33 | 1.00 | 1.000 | 1.000 | 1.00 |
| every foreign key repointed to another real record | 0.000 (links) | 0.879 | 1.00 | 1.00 | 0.852 | 0.727 | 1.00 |
| pseudonyms kept, all other prose deleted | 0.000 (text) | 0.609 | 1.00 | 0.33 | 0.636 | 1.000 | 0.01 |
| invented personas, username = first name, declared | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 1.00 |
| invented personas, not declared | 0.000 | 0.000 | 1.00 | 0.00 | 1.000 | 1.000 | 1.00 |
| invented personas, mapping.json lists the real names | 0.000 | 0.000 | 0.00 | 1.00 | 1.000 | 1.000 | 1.00 |
| one full name written without its space | 0.952 | 0.952 | 0.83 | 1.00 | 1.000 | 1.000 | 1.00 |

### 6.7 Choices that are deliberate and can be argued

* Replacing a handle slot (`[~qwojcik]`) with a full name fails that person's continuity: the slot form is part of
  a coherent identity, and `[~Full Name]` breaks Jira mention syntax.
* Generalizing a person away ("a colleague") instead of pseudonymizing fails continuity for that person. This is a
  pseudonymization benchmark; suppression is a different, also legitimate, strategy that it does not reward.
* A stray shared surname is charged to both namesakes (6.2).

## 7. Running a fair evaluation

The gold sits next to each task's input in this repository, so a fair evaluation has to keep it away from the
system under test:

* **Give the system only `input/`.** `piibench init` and `piibench run` copy only `TASK.md`, `pseudonyms.json` and
  `data/` into a fresh workspace. The gold and the pristine input stay in the task folder.
* **For agents with a shell,** run them in a sandbox or container that cannot read the repository. An agent that can
  read `gold.json` can copy the answer.
* **Grade only the files.** The grader reads the workspace after the system has finished. Whatever the system says
  about its own work is ignored.
* **Use a fresh copy for every run.** For repeated runs, `piibench run --repeats N` starts each run from a fresh copy.
* **Report the statistics that matter.** Report the share of tasks solved and of people fully right next to the
  mean score, and report variance when you run a task more than once.

## 8. What string matching can and cannot resolve

Most tasks can be largely solved by careful string matching, and the numbers show why
(`python scripts/name_derivability.py`):

* In t01-t07, 96% of gold aliases and 99% of occurrences contain a four-letter prefix of a first or last name that
  belongs to no other person in the task: every username and email is derived from the name. t08 is the same (99%
  and 100%); its difficulty is the variety of forms (fork URLs, nicknames, obfuscated addresses, accented spellings,
  trailers), not the reasoning.
* Jira accounts and git identities put the username or email in the same JSON object as the display name.
* In t01-t08, first names are unique within a task, so "Thanks, Quentin!" is never ambiguous.
* t04's one collision is answered by `people.jsonl`, which lists the two namesakes as separate records.

t09 and t10 remove those shortcuts for part of each task:

* opaque handles that share nothing with the name;
* first names shared across people;
* namesakes inside one thread;
* no directory;
* about three times as many people.

In t09, 88% of occurrences carry an entity-unique name fragment; in t10, 77%. The rest can only be resolved from
record evidence.

The rule baseline shows the effect. It finds nearly every identifier on t09 and t10 (ER 1.00 and 0.96). But it
splits the opaque handles from their owners and gives one first name to two people: CC 0.67 and 0.76, people fully
right 0.67 and 0.76. Because each of these tasks holds only a handful of such people, they move the mean score
little.

The hard split raises the share of such people while keeping everything else the same. In h1 and h2, 80% and 61% of
occurrences carry a name fragment. The rule baseline gets 58% and 55% of people fully right there, against 67% and
76% on t09 and t10.

## 9. Alternatives considered

* **Open-world scoring without declaration.** The grader would infer each person's persona from the replacements
  (one canonical name, email and username per person, distinct across people, shape-checked). It is closer to
  "can the agent anonymize", but needs name canonicalization (initials, nicknames, obfuscated addresses, casing),
  makes weak leak detection critical, and would change the meaning of every result. The declared-persona path gets
  most of the benefit with an exact check.
* **A pseudonym tool** (`pseudonym(person_key)`) would make the task pure clustering plus tool use and add an
  interface every approach must adopt.
* **Bigger tasks.** Not useful on their own (section 8).
* **Production-ops surfaces** (incidents, traces). Neither source dataset has them; they could be added as a
  synthetic surface linked to the same personas.
