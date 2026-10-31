# Walkthrough: one person through one task

This page follows one synthetic developer through task t09 (Apache Storm, built from SmartSHARK). It shows:

* what the agent sees;
* what the gold records;
* what a correct cleaning looks like;
* how the grader scores common mistakes;
* what the rule baseline does with it.

All names below are synthetic. Every number comes from the task files and the grader, and the score table in
section 5 is printed by `python scripts/walkthrough_t09.py`.

## 1. What the agent sees

The workspace holds three things:

* `TASK.md`, the rules;
* `pseudonyms.json`, 43 synthetic personas;
* eight JSONL files with 122 linked records about 27 people: commits, Jira issues and comments, pull requests,
  reviews, PR comments, mailing-list messages and Travis CI builds.

One of the 27 people is Seraphina Ostrowski. She appears in several places.

Two git commits (`data/commits.jsonl`). Her name and work email are in the author and committer fields, and again in
a `Signed-off-by` trailer:

```json
{"hash": "dc94bf47886c...", "author": {"name": "Seraphina Ostrowski", "email": "seraphina.ostrowski@example.com"},
 "committer": {"name": "Seraphina Ostrowski", "email": "seraphina.ostrowski@example.com"},
 "message": "STORM-538: Simplify the retry loop\n\nThis closes #299\n\nSigned-off-by: Seraphina Ostrowski <seraphina.ostrowski@example.com>",
 "issue_key": "STORM-538"}
```

A pull request (`data/pull_requests.jsonl`). It was opened from a GitHub account whose handle has nothing to do with
her name and which shows no profile name:

```json
{"id": "299", "title": "STORM-538: Simplify the retry loop",
 "creator": {"login": "silentpuffin80", "display_name": "silentpuffin80"},
 "commits": ["dc94bf47886c...", "aae64a0b71ca..."], "issue_key": "STORM-538"}
```

A Jira comment by the ASF GitHub Bot (`data/comments.jsonl`). The bot account is not a person, but the handle inside
the text is:

```text
GitHub user silentpuffin80 opened a pull request:
    https://github.com/apache/storm/pull/299
```

A review comment on that pull request (`data/pr_comments.jsonl`), from another developer:

```text
Can you squash the commits, Seraphina?
```

A mailing-list message (`data/mail.jsonl`). It was sent from a personal address, with the name in lower case and a
signature:

```text
from: seraphina ostrowski <seraphinao14@post.example.org>
...
-- 
Seraphina Ostrowski
```

Elsewhere, on a different issue (STORM-609), two Jira comments say "Thanks, Seraphina!" and link to the Jira profile
`ViewProfile.jspa?name=scastellanos`. That Seraphina is someone else: Seraphina Castellanos, a Jira user who
reported STORM-609. She never takes part in any thread with Seraphina Ostrowski.

## 2. The reasoning the task requires

* **`silentpuffin80` is Seraphina Ostrowski.** The evidence is in the records: pull request 299 lists commit
  `dc94bf47`, and that commit was authored by Seraphina Ostrowski. The link is real SmartSHARK data (a
  `pull_request_commit` row with the same person id); only the names are synthetic. Nothing else in the export
  connects the handle to her.
* **"Seraphina?" on pull request 299 is Seraphina Ostrowski.** It is her pull request.
* **"Thanks, Seraphina!" on STORM-609 is Seraphina Castellanos.** She is the only Seraphina in that thread.
  Replacing every "Seraphina" with one persona gets one of the two people wrong.
* **The two spellings and the two addresses are one person.** `seraphina ostrowski` (lower case) and the personal
  address `seraphinao14@post.example.org` both belong to her.
* **Some things look like data but are not personal.** `ASF GitHub Bot`, `dev@storm.apache.org` and `Travis CI` are
  not people. Neither are the person-like technical words in the prose: a Fenwick tree, Kahan summation, a Bloom
  filter, the Ruby and Julia bindings. All of them must stay as they are.

## 3. What the gold records

`gold.json` is never in the workspace. It lists Seraphina Ostrowski as entity `E06`, with every alias and every place
it occurs:

```text
E06  Seraphina Ostrowski
     aliases: full name (two spellings), first name, two emails, the GitHub handle silentpuffin80
     occurrences (17): commits.author.name/email x2 each, commits.committer.name/email x2 each,
                       commits.message x2 (the Signed-off-by trailer: name, then email),
                       pull_requests.creator.login, pull_requests.creator.display_name,
                       comments.body (the handle in the bot notice, characters 12-26),
                       pr_comments.body ("Seraphina", characters 28-37),
                       mail.from.name, mail.from.email, mail.body (the signature)
E18  Seraphina Castellanos
     occurrences (8): issue reporter and assignee (username, display name),
                      "Seraphina" in two STORM-609 comments, the profile URL name in the same two comments
```

Each occurrence also records which forms are acceptable in its slot: a name, an email or a username.

## 4. A correct cleaning

Take `E06 -> Nolwenn Lindgren` and `E18 -> Lysander Strandberg`. Any one-to-one choice from the pool scores the
same, and so do invented personas declared in `mapping.json`. The records then read:

```text
commit author     Nolwenn Lindgren <nolwenn.lindgren@pseudonym.example>
commit trailer    Signed-off-by: Nolwenn Lindgren <nolwenn.lindgren@pseudonym.example>
PR creator        {"login": "nlindgren", "display_name": "nlindgren"}
bot notice        GitHub user nlindgren opened a pull request: https://github.com/apache/storm/pull/299
PR comment        Can you squash the commits, Nolwenn?
mail              from: Nolwenn Lindgren <nolwenn.lindgren@pseudonym.example> ... -- Nolwenn Lindgren
STORM-609         I can reproduce this on master. Thanks, Lysander!
                  Assigning to https://issues.apache.org/jira/secure/ViewProfile.jspa?name=lstrandberg
```

Everything else stays unchanged: the hashes, issue keys, URLs (apart from personal usernames in them), the bot, the
mailing list and the Fenwick tree.

## 5. How the grader scores it, and common mistakes

For every gold occurrence, the grader does three things:

* finds what the occurrence became in the agent's output;
* checks that nothing of the original remains (entity recall, ER);
* checks that all of the person's occurrences became one persona that nobody else uses, in the right form for each
  slot (continuity, CC).

Section 6 of [`DESIGN.md`](DESIGN.md) has the full definitions.

Scores on t09 for the correct cleaning, and for the correct cleaning with one mistake added:

| cleaning | score | strict (min) | ER | CC | separation | people fully right | solved |
|---|---|---|---|---|---|---|---|
| correct | 1.000 | 1.000 | 1.00 | 1.00 | 1.00 | 1.00 | yes |
| `silentpuffin80` treated as a separate person (its own persona) | 0.990 | 0.963 | 1.00 | 0.96 | 1.00 | 0.96 | no |
| both Seraphinas given Seraphina Ostrowski's persona | 0.980 | 0.926 | 1.00 | 0.93 | 0.96 | 0.93 | no |
| handle left in the bot notice | 0.990 | 0.963 | 0.96 | 1.00 | 1.00 | 0.96 | no |
| obfuscated address: persona's name, original mail domain | 0.990 | 0.963 | 1.00 | 0.96 | 1.00 | 0.96 | no |
| correct files, but `mapping.json` also lists the real names | 0.000 | 0.000 | 0.00 | 1.00 | 1.00 | 0.00 | no |
| every foreign key repointed to another real record | 0.000 | 0.000 | 1.00 | 1.00 | 1.00 | 1.00 | no (links gate) |

(The obfuscated-address row uses another person in t09, Nikolai Wainwright, whose mail signature reads
`nikolai.wainwright at lab dot example dot org`. Rewriting it as `<persona>.<name> at lab dot example dot org`
keeps the original domain, so a reader of the export still sees a real-looking work domain next to a pseudonym.)

Two things to take from this table:

* **One wrong person barely moves the score.** One wrong person out of 27 changes the averaged score by only
  0.01-0.02, because entity recall and continuity are shares of people. That is why results are always reported
  with `solved` (every person right, every gate passed) and with the share of people fully right, not only as a
  mean score.
* **Gates are absolute.** Two kinds of export are not anonymized at any level of name replacement: one that ships
  the real names next to the pseudonyms, and one whose links are rewired.

## 6. What the rule baseline does on t09

The rule baseline (`rules`) never connects `silentpuffin80` to Seraphina Ostrowski. It links identifiers only when
they share a JSON object or are identical strings. It finds every identifier on t09 (entity recall 1.00), but it
builds the wrong identity graph: people fully right 0.67, score 0.889.

To see how your own method does on this task:

```bash
piibench init t09 ./work        # copy the task's input
# ... run your method on ./work ...
piibench score t09 ./work
```

[`USAGE.md`](USAGE.md) explains how to evaluate a method on every task.
