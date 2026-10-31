# Data: sources, download, provenance

Raw data stays outside the repo, in `../dataset/` next to the repo root (override with
`PIIBENCH_DATA_DIR`). Nothing from there is committed. The repo only contains the generated
tasks, which keep the real record structure but use synthetic identities.

```
<parent>/
├── dataset/                           # raw data, not in git
│   ├── MozillaApacheDataset-sample/   # 20-MAD sample (git clone)
│   ├── smartshark_2_1_small.agz       # SmartSHARK archive, MD5-checked
│   └── mongo-data/                    # MongoDB restore of the identity-linking collections
└── pii-consistency-bench/             # this repository
```

## 1. SmartSHARK 2.1 Small

| | Value | Where it comes from |
|---|---|---|
| Dataset | `doi:10.25625/PHV2VX` (GRO.data, Göttingen) | Dataverse API |
| File | `smartshark_2_1_small.agz`, datafile id 32457 (`mongodump --archive --gzip`) | Dataverse API |
| Size | 11,968,836,074 bytes (11.97 GB) | Dataverse API `filesize` |
| MD5 | `8dca79068b308eb3c80ace0fc4771c19` | Dataverse API `checksum` |
| License | CC-BY | Dataverse API `termsOfUse` |
| Endpoint A | `https://data.goettingen-research-online.de/api/access/datafile/32457?gbrecs=true` | dataset page |
| Endpoint B | `https://data.goettingen-research-online.de/download/smartshark_2_1_small.agz` | `alternativeURL` field |

Metadata query:
`https://data.goettingen-research-online.de/api/datasets/:persistentId/?persistentId=doi:10.25625/PHV2VX`.
The same server also has 1.3, 2.1 Full, 2.2 Small (about 39 GB) and 2.2 Full; 2.1 Small is the
smallest 2.x release.

### Getting the archive

`scripts/data/fetch_smartshark.sh` downloads it with `scripts/data/resumable_download.py` (standard library only):
parallel 64 MiB range requests, each chunk resumable on its own, progress saved so the job can be stopped and
restarted, every response checked to be the exact requested range, and size and MD5 verified at the end (a
`.md5ok` marker is written next to the archive).

```bash
scripts/data/fetch_smartshark.sh probe    # ~3 min, range support and throughput on both endpoints
scripts/data/fetch_smartshark.sh start    # runs in the background, reuses the partial file
scripts/data/fetch_smartshark.sh status
scripts/data/fetch_smartshark.sh stop     # saves state; start picks up again
```

### What is in the public dump

Checked from the archive headers:

* One database, `smartshark_2_1`, with 28 collections. The schema matches pycoSHARK 1.4.2 (pycoSHARK
  2.x renamed some fields).
* There is no `people` or `identity` collection in any public SmartSHARK dump (2.1 and 2.2, small and
  full). The paper (arXiv 2102.11540, section I) says identities are not shared unless requested.
  People only show up as ObjectIds. A person record is keyed on an exact name and email, so the same
  id appears on several surfaces when someone used the same identity in git, Jira and on the mailing
  lists. That gives real cross-surface links but no alias clusters. A Jira account is usually a separate id
  from the same person's git and GitHub accounts. The benchmark therefore treats every id as its own person and
  never merges two ids.
* Person-id fields: `commit.author_id/committer_id`, `tag.tagger_id`,
  `issue.creator_id/reporter_id/assignee_id`, `issue_comment.author_id`, `event.author_id`,
  `pull_request.creator_id/assignee_id/linked_user_ids/requested_reviewer_ids`,
  `pull_request_comment.author_id`, `pull_request_review.creator_id`,
  `pull_request_review_comment.creator_id`, `pull_request_commit.author_id/committer_id`,
  `message.from_id/to_ids/cc_ids`.
* Free text still has real names and handles (commit messages, mail bodies and Message-IDs, PR fork
  URLs, Travis logs). The SmartSHARK tasks therefore export no text at all; their prose is templated.
* We are not asking for the withheld identity collections. The benchmark only needs the link
  structure, and requesting real identities to build a PII benchmark would be hard to justify.

Once the download is verified, restore just the identity-related collections into a local MongoDB
(Docker, `mongo:4.4.29` pinned by digest):

```bash
scripts/data/restore_smartshark.sh probe     # lists the namespaces in the archive
scripts/data/restore_smartshark.sh restore   # 19 collections via --nsInclude, then prints counts
scripts/data/restore_smartshark.sh stop
```

### How SmartSHARK slices become tasks

One builder reads the local MongoDB through `docker exec ... mongo` and keeps the export in memory only:
`scripts/build_smartshark_linked.py`, which runs `scripts/smartshark_export.js` inside the database container. It
builds all three SmartSHARK main tasks, and with `--hard` the two hard-split tasks.

* t08 (ZooKeeper, seed mode `shared_id`): person ids that appear on git, GitHub and the mailing list, with their
  commits, pull requests, mail, and the Jira issues those commits and pull requests reference.
* t09 (Storm) and t10 (Curator), seed mode `pr_commit`: seeds are
  developers who authored a git commit that also appears, under the same id, in a pull request they opened
  (`pull_request_commit`). The export prints only ids, hashes, dates, numbers, states and issue keys parsed
  from pull-request titles; it adds each seed's Travis-built commits, the linked Jira issues with their
  reporters, assignees and first comments, one review and one discussion comment per pull request, and the
  seeds' mail with thread references.
* h1 (Storm) and h2 (Curator), the hard split: the same seed mode as t09-t10, with a different sample of seeds
  (another random seed and salt, up to 10 seeds instead of 8) and the difficulty parameters raised (`HARD_SPECS` in
  the builder).

Project choice was measured, not guessed (counts over the restored collections):

| Project | commit authors who also opened a PR | ...and mailed the list | PR commits found in git with the same author id | Travis builds |
|---|---|---|---|---|
| storm | 55 | 15 | 544 of 1,575 found | 7,135 |
| curator | 19 | 6 | 1,223 of 1,396 found | 928 |
| samza | 16 | 5 | 436 of 521 found | 4,778 |
| nifi | 36 | 16 | 0 of 3,000 sampled (PR commits not in the git data) | 10,963 |
| zookeeper (t08) | used by t08 with seed mode `shared_id` | | 0 matching author ids | 0 |

Storm and Curator have the most developers whose GitHub and git activity is linked by the data itself, plus
mail and Travis CI. NiFi was rejected because its pull-request commits never match the git history.

The generated inputs keep real commit hashes, issue keys and timestamps. Those are public quasi-identifiers
(anyone can look up a commit's real author), so the tasks are pseudonymous relative to the real developers;
they contain no personal data that is not already public, and every name, address and handle in them is
synthetic.

## 2. 20-MAD sample

Cloned from `https://github.com/M3SOulu/MozillaApacheDataset-sample` (branch `master`) into
`dataset/MozillaApacheDataset-sample`. The clone used `GIT_LFS_SKIP_SMUDGE=1`, but that repo doesn't
use LFS, so every file is real data (the Parquet files load fine with pyarrow 26). The sample has
far more linked identities than a handful of tasks needs, so the full 20-MAD download isn't needed.

| File | Rows | Person fields |
|---|---|---|
| `commits.parquet` | 100,353 (Apache hadoop + zookeeper, Mozilla releases-comm-central) | `author`, `committer` (SHA-1 keys) |
| `issues.parquet` | 85,182 (Jira HADOOP/HDFS/ZOOKEEPER, Bugzilla Thunderbird) | `reporter_key`, `creator_key`, `assignee_key` |
| `comments.parquet` | 778,886 | `author_key`, `update_author_key` |
| `idmerging.parquet` | 70,873 | `key_hash` to `merged_id`, `is_bot` |
| `timestamps.parquet` | 1,190,995 | `person` (merged id) per action |
| `tzhistory.parquet` | 83,812 | per-person timezone history |
| `nlp/{jira,bugzilla}/**/*_nlcomments.parquet` | comment text by paragraph | free text |

What the identity data looks like:

* 39,966 merged persons: 14,484 with one key, 24,880 with two, 602 with three to twelve.
* 841 non-bot persons appear in both commits and issues; 533 of them have at least three keys.
* 84,729 of 100,353 commits link to an issue, so commit-issue-comment chains are real.
* 8,975 commits have a different author and committer.

The free text is not pseudonymized; only the structured person fields are SHA-1 keys. In the sample,
50.9% of Apache commit messages contain `Contributed by <real name>` (or `(<name> via <committer>)`),
83.4% of Mozilla commit messages contain reviewer handles (`r=<nick>`), and Jira comments have
greetings like `Hi Flavio,`, `[~user]` mentions (2.7%) and `@user` mentions (4.7%). Raw text never goes
into a task as is: attribution slots are stripped, synthetic mentions are injected in their place,
and paragraphs that still look like they mention someone are dropped (see `docs/DESIGN.md`).

Other things that shaped the generator:

* `is_bot` misses the Apache Jira bots. The two busiest ZooKeeper commenters are Hadoop QA and the
  ASF GitHub Bot, and neither is flagged, so bots are detected from their message templates
  (`sources/mad.py::detect_bots`).
* A few Apache keys map to two merged persons. They resolve to the busier one and are never used as
  sampling seeds.
* Mozilla `issues.reporter_key` is empty; `creator_key` and `assignee_key` are filled and map to
  persons, so Bugzilla tasks use creator and assignee.

## License and citation

20-MAD is CC-BY 4.0: Claes, M. and Mäntylä, M., "20-MAD: 20 years of issues and commits of Mozilla and
Apache development", MSR 2020 (data on OSF, `kvxr4`). SmartSHARK releases on GRO.data are CC-BY
(Trautsch, Trautsch, Herbold). The carrier text in `tasks/` comes from 20-MAD and keeps that
attribution.

The general rule: structure is real, identities are synthetic. Which records exist, which raw keys
belong to one person, which commits link to which issues, the timestamps and the technical prose come
from the datasets. Every name, email, username and mention in a task is synthetic, and every injected
mention is marked as such in the gold manifest.
