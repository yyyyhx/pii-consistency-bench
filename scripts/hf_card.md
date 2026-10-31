---
license: cc-by-4.0
language:
  - en
pretty_name: "PII Consistency Bench: identity-consistent PII pseudonymization across linked records"
size_categories:
  - 1K<n<10K
task_categories:
  - token-classification
  - other
tags:
  - pii
  - pseudonymization
  - anonymization
  - de-identification
  - entity-resolution
  - privacy
  - benchmark
  - llm-agents
  - software-engineering
  - mining-software-repositories
configs:
  - config_name: tasks
    data_files:
      - split: main
        path: data/tasks/main.jsonl
      - split: hard
        path: data/tasks/hard.jsonl
  - config_name: occurrences
    data_files:
      - split: main
        path: data/occurrences/main.jsonl
      - split: hard
        path: data/occurrences/hard.jsonl
---

# PII Consistency Bench

PII Consistency Bench is a benchmark for **identity-consistent PII pseudonymization** across linked software-engineering
records: git commits, issue trackers (Jira, Bugzilla), GitHub pull requests and reviews, mailing lists and CI logs.

Removing names is not the hard part. The hard part is that one developer appears differently on every surface, and
all of those appearances must become **the same** pseudonym:

* a git author name and email;
* a tracker username;
* a GitHub handle unrelated to the name;
* a first name in a review;
* an obfuscated address in a mail signature.

Two different people must never be merged, even when they share a name, and the links between records must
survive.

Code, grader, baselines and documentation: **https://github.com/yyyyhx/pii-consistency-bench**

## What is in the dataset

| Split | Tasks | People | Records | Occurrences of a person |
|---|---|---|---|---|
| `main` | 10 | 134 | 685 | 2,487 |
| `hard` | 2 | 60 | 261 | 866 |

Each task is a small export of linked records (JSON Lines files) with the record structure taken from a real Apache
project. Every person in it has a synthetic identity. Each task comes with:

* `instructions`: the cleaning rules given to the system under test;
* `personas`: a pool of synthetic personas to use as replacements;
* `files`: the data files to clean;
* `gold`: the answer key. It lists every person, every alias, and every occurrence, with file, record, field path,
  character span (for free text), surface, and the replacement forms the slot accepts.

### Configurations

* **`tasks`**: one row per task. A row contains everything needed to run and grade the task. Write `files` to
  `data/<path>`, `instructions` to `TASK.md` and `personas` to `pseudonyms.json`. Do not give the `gold` column to
  the system under test.
* **`occurrences`**: one row per occurrence of a person, flattened from the gold. It is useful for browsing the
  annotations or for detection-style evaluation (`free_text`, `span_start` and `span_end` locate mentions inside
  prose).

### The tasks

| Task | Source | What it tests |
|---|---|---|
| t01-t07 | 20-MAD (ZooKeeper, HDFS, Hadoop, Thunderbird) | a directory-backed control task, aliases joined only by context, Bugzilla handles, two people with one name, long histories, CI logs, bots and mailing lists that must be left alone |
| t08 | SmartSHARK (ZooKeeper) | one identity across git, GitHub and mail; fork-URL usernames, nicknames, obfuscated addresses, accented spellings, commit trailers |
| t09 | SmartSHARK (Storm) | GitHub handles unrelated to the name (resolvable through the commits a pull request lists), first names shared by two people |
| t10 | SmartSHARK (Curator) | two people with the same full name in one pull request |
| h1 | SmartSHARK (Storm) | every handle anonymous and linkable only through pull-request commits |
| h2 | SmartSHARK (Curator) | three same-full-name pairs inside one pull request |

## How to use it

Grading uses the scorer from the GitHub repository, which needs the standard library only. The repository also
contains the tasks:

```bash
git clone https://github.com/yyyyhx/pii-consistency-bench && cd pii-consistency-bench && pip install -e .
piibench run --cmd "python $PWD/my_cleaner.py"     # runs your cleaner on fresh copies of every task and grades them
```

To work from this dataset instead, rebuild the task folders from the `tasks` configuration. The rebuilt folders are
byte-identical to the repository's:

```bash
# download this dataset into piibench-data/ (the "Files and versions" tab, or huggingface-cli download)
piibench materialize piibench-data/data/tasks/main.jsonl piibench-data/data/tasks/hard.jsonl --out piibench-tasks
export PIIBENCH_TASKS_ROOT=$PWD/piibench-tasks
```

The score is `gate x harmonic_mean(entity recall, continuity, edit precision, integrity)`. The gate requires valid
files, unchanged references between records, and at least 90% of the non-personal text kept. Results should be
reported together with the share of tasks solved (every person correct, every gate passed) and the share of people
fully correct, since one wrong person moves the mean score only slightly. See the repository for details.

## How the data was made

**Record structure.** Record ids, commit hashes, timestamps and the links between records (which commit belongs to
which issue, which pull request contains which commit, who reviewed what, who replied to whom) come from two public
research datasets:

* **20-MAD** (Claes and Mäntylä, MSR 2020; CC BY 4.0);
* **SmartSHARK** 2.1 Small (Trautsch et al.; CC BY 4.0).

**Identities.** Every name, email address, username and personal mention was replaced by a synthetic identity drawn
from name lists that are disjoint from the persona pool. All domains are reserved example domains.

**Free text.** 20-MAD prose is sanitized: attribution slots are removed, and paragraphs that still resemble a
reference to a person are dropped. No SmartSHARK text is used; prose in those tasks is generated from templates.

**Checks.** Every task passes four checks at generation time:

1. the gold covers every identifier in the data;
2. an independent scan, which does not use the gold, finds no real names;
3. every first-name mention can be attributed from its own discussion thread;
4. applying the gold yields a perfect score.

In addition, a random sample of 40 free-text mentions was checked by hand, and all 40 were attributed correctly.

## Privacy note

The tasks keep real commit hashes and issue keys, because the links between records are part of what is evaluated.
These are public identifiers: anyone can look up the original author of a public commit. The data is therefore
pseudonymous with respect to the original developers rather than anonymous. It adds no personal information beyond
what the source datasets already make public. Please do not use it to re-identify individuals.

## License and citation

The data is released under CC BY 4.0, and is derived from 20-MAD and SmartSHARK (both CC BY 4.0); please credit them
as well. The code in the GitHub repository is MIT-licensed. Citation information is in the repository's
`CITATION.cff`.
