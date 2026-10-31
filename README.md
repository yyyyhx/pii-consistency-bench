# PII Consistency Bench

**A benchmark for identity-consistent PII pseudonymization across linked software-engineering records.**

[![License: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![Data: CC BY 4.0](https://img.shields.io/badge/data-CC%20BY%204.0-lightgrey.svg)](LICENSE-DATA)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](pyproject.toml)

Removing names from a dataset is the easy part. The hard part is that one developer appears differently on every
surface:

* a git author name and work email;
* a Jira username;
* a GitHub handle that has nothing to do with their name;
* a first name in someone else's review ("LGTM, thanks Seraphina!");
* an obfuscated address in a mailing-list signature;
* a username in a CI log.

If developer *Mike* becomes *Tom*, every one of those has to become the **same** Tom. Two different people must
never be merged into one, even when they share a name. And the links between records (which commit fixes which
issue, which pull request contains which commit) must survive.

PII Consistency Bench tests exactly that. It provides:

* **12 tasks.** Each is a small export of real, linked records from Apache projects (commits, issues, comments, pull
  requests, reviews, mailing lists, CI builds), with every person replaced by a synthetic identity.
* **A gold manifest** for every task: every person, every alias, and every occurrence, down to the character span
  inside free text.
* **A grader** that measures privacy, identity continuity, edit precision and structural integrity together, plus
  rule-based baselines.
* **A generator** that builds new tasks from the public 20-MAD and SmartSHARK datasets, with adjustable difficulty.

It is approach-agnostic: rule systems, NER pipelines, entity-resolution methods and LLM agents are all graded only
on the files they leave behind.

## An example

One person in task t09 (Apache Storm). All names are synthetic; the links are real.

```text
commits.jsonl        commit dc94bf47, author: Seraphina Ostrowski <seraphina.ostrowski@example.com>
pull_requests.jsonl  PR 299, creator: silentpuffin80 (no profile name), commits: [dc94bf47, aae64a0b]
comments.jsonl       ASF GitHub Bot: "GitHub user silentpuffin80 opened a pull request: .../pull/299"
comments.jsonl       another issue: "Thanks, Seraphina!"   (a different Seraphina)
```

Nothing in `silentpuffin80` says who it is, but the records do: pull request 299 lists commit `dc94bf47`, whose
author is Seraphina Ostrowski. A correct cleaning:

* gives her one persona everywhere, including on `silentpuffin80`;
* gives the other Seraphina a different persona;
* leaves the bot, the hashes and the issue keys untouched.

[`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md) follows this example through grading.

## Quick start

Python 3.12+, no dependencies.

```bash
git clone https://github.com/yyyyhx/pii-consistency-bench && cd pii-consistency-bench && pip install -e .

piibench init t09 ./work          # copy one task's input (never the answer key) into ./work
# ... clean ./work with your method ...
piibench score t09 ./work         # grade it

piibench run --cmd "python $PWD/my_cleaner.py" --split all --repeats 3 --out runs.jsonl   # evaluate on every task
```

`piibench run` works with any command that edits the files in its working directory (the command runs inside a fresh
copy of each task, hence the absolute path): a script, a rule system, or an LLM agent. See [`docs/USAGE.md`](docs/USAGE.md).

## The tasks

| Task | Source | People | What it tests |
|---|---|---|---|
| t01 | 20-MAD, ZooKeeper | 6 | control task with a developer directory |
| t02 | 20-MAD, HDFS | 9 | aliases that can only be connected through context |
| t03 | 20-MAD, Thunderbird | 7 | Bugzilla: email logins, `[:nick]` and `r=nick` handles |
| t04 | 20-MAD, Hadoop | 8 | two different people with the same name |
| t05 | 20-MAD, Hadoop | 8 | a long history (422 places to clean) |
| t06 | 20-MAD, ZooKeeper | 4 | CI console logs, home-directory paths, notification mails |
| t07 | 20-MAD, HDFS | 4 | bots, mailing lists and code that must be left alone |
| t08 | SmartSHARK, ZooKeeper | 36 | usernames only in fork URLs, nicknames, obfuscated addresses, accented spellings, commit trailers |
| t09 | SmartSHARK, Storm | 27 | GitHub handles unrelated to the name, first names shared by two people, person-like words ("Bloom filter") |
| t10 | SmartSHARK, Curator | 25 | two people with the same full name in one pull request, one approving the other's work |
| **h1** | SmartSHARK, Storm | 31 | **hard:** every GitHub handle anonymous, linkable only through the commits its pull request lists |
| **h2** | SmartSHARK, Curator | 29 | **hard:** three pairs of people with identical full names inside one pull request |

* **t01-t07:** most aliases still contain part of the person's name, so careful string work goes a long way.
* **t08:** adds many more forms of the same name.
* **t09-t10:** some people can only be resolved from record evidence.
* **h1-h2 (the hard split):** those people are the majority. Report it separately.

| | t01-t07 | t08 | t09 | t10 | h1 | h2 |
|---|---|---|---|---|---|---|
| Occurrences that carry a piece of the person's own name | 99% | 100% | 88% | 77% | 80% | 61% |

## How it is scored

For every place where a person appears, the grader recovers what that text became, then checks four things:

* **Entity recall (ER):** is anything left that identifies the person? This includes spellings with the accents
  stripped, or a name glued into another word.
* **Continuity (CC):** did every mention become the same pseudonym, in the right form for its slot (a name where a
  name was, an email where an email was), with nobody else sharing it?
* **Edit precision (P):** was everything else left alone?
* **Integrity (RI):** do the files parse, are all records there, and do all links still point where they did?

```text
score  = gate x harmonic_mean(ER, CC, P, RI)
gate   = files valid AND no link changed AND at least 90% of the non-personal text kept verbatim
solved = gate AND every component = 1
```

The harmonic mean is pulled down by the worst component, so finding every name cannot hide a broken identity graph.

ER and CC are shares of people, so one wrong person in a 27-person task costs only 0.01-0.02. Always report
**solved** and **people fully right** next to the mean score. The grader also reports occurrence recall (structured
fields vs free text), separation, B-cubed precision and recall, form accuracy and text preservation.
[`docs/DESIGN.md`](docs/DESIGN.md) explains every rule and why it is there.

## Baselines

| Method | Main split: mean score | Main: people fully right | Hard split: mean score | Hard: people fully right |
|---|---|---|---|---|
| no-op | 0.000 | 0% | 0.000 | 0% |
| redact everything | 0.000 | 0% | 0.000 | 0% |
| rules, structured fields only | 0.490 | 24% | 0.502 | 25% |
| rules with entity resolution | 0.829 | 64% | 0.831 | 57% |
| gold applied | 1.000 | 100% | 1.000 | 100% |

The rule system finds 97% of all mentions but solves only the control task. It cannot connect an anonymous handle to
its owner, and it merges people who share a name. Per-task results are in
[`docs/BASELINES.md`](docs/BASELINES.md).

## Is the benchmark sound?

* **The grader penalizes the right thing.** Deliberately broken cleanings lose points on the component they break:
  * one missed email lowers entity recall;
  * merging two people lowers continuity;
  * rewiring the links fails the link gate;
  * leaving real names in `mapping.json` scores 0.

  See [`docs/DESIGN.md`](docs/DESIGN.md) section 6.6. Over 100 tests pin these behaviours.
* **The answer key is checked.** Every task passes four checks at generation time (`piibench check`):
  1. the gold covers every identifier;
  2. an independent scan, which does not use the gold, finds no real names;
  3. every first-name mention can be attributed from its own discussion thread;
  4. applying the gold scores exactly 1.

  A random sample of 40 free-text mentions was also checked by hand, and all 40 were attributed correctly.
* **Everything is reproducible.** Task generation is deterministic, and all baseline numbers regenerate from the
  repository.

## Data and privacy

* **What is real.** Record structure (ids, commit hashes, timestamps, and the links between commits, issues, pull
  requests, reviews, mail threads and CI builds) comes from two public research datasets:
  [20-MAD](https://github.com/M3SOulu/MozillaApacheDataset-sample) (Claes and Mäntylä, MSR 2020) and
  [SmartSHARK](https://doi.org/10.25625/PHV2VX) (Trautsch et al.). Both are CC BY 4.0.
* **What is synthetic.** Every name, email address, username and personal mention.
* **Free text.** No SmartSHARK text is exported; prose in those tasks is generated from templates. 20-MAD prose is
  sanitized and scanned.
* **Pseudonymous, not anonymous.** The tasks keep real commit hashes and issue keys because links are part of what
  is tested. They are pseudonymous with respect to the original developers, and add no personal information beyond
  what the source datasets make public. Please do not use them to re-identify anyone.

[`docs/DATA.md`](docs/DATA.md) documents the sources and how the tasks were built.

## Repository layout

```
tasks/<id>/input/       what a method sees: TASK.md (rules), pseudonyms.json (persona pool), data/*.jsonl
tasks/<id>/gold.json    the answer key (never give it to the method under test); audit.json: statistics
tasks_hard/<id>/        the hard split, same layout
src/piibench/           grader, CLI, baselines, PII scan, solvability check, 20-MAD task generator
scripts/                SmartSHARK task builder, Hugging Face export, analysis scripts
results/                baseline reports and grader sanity tables
docs/                   USAGE, DESIGN, WALKTHROUGH, BASELINES, DATA
examples/               a minimal cleaner showing the interface
```

`python scripts/export_hf.py` also exports the tasks as a Hugging Face-style dataset (JSON Lines, one configuration
with whole tasks and one with every annotated occurrence); see [`docs/USAGE.md`](docs/USAGE.md) section 8.

## Citation

If you use PII Consistency Bench, please cite it (see [`CITATION.cff`](CITATION.cff)), together with 20-MAD and
SmartSHARK.

## License

Code: [MIT](LICENSE). Data: [CC BY 4.0](LICENSE-DATA), derived from 20-MAD and SmartSHARK (both CC BY 4.0).
