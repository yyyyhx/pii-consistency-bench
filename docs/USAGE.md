# Using PII Consistency Bench

How to evaluate your own method on the benchmark, read the scores, report results, and generate more tasks.

## 1. Install

Python 3.12 or newer. The grader, the checks and the baselines use the standard library only.

```bash
git clone https://github.com/yyyyhx/pii-consistency-bench
cd pii-consistency-bench
pip install -e .            # add '.[dev]' to run the tests, '.[build]' to regenerate the 20-MAD tasks
piibench list               # the 12 tasks
```

## 2. One task by hand

```bash
piibench init t09 ./work        # copies TASK.md, pseudonyms.json and data/ into ./work (never the gold)
# ... clean the files in ./work in place, with any method ...
piibench score t09 ./work       # grades them
```

`piibench score` prints the score, whether the task is solved, the four components, and the share of people fully
right. Add `--json report.json` for the full report, which lists every leaked occurrence and every person whose
continuity failed.

Tasks can be named by their full id (`t09-storm-linked-accounts`) or by the prefix (`t09`, `h2`).

## 3. A method on every task

`piibench run` evaluates any command:

1. it copies each task's input into a fresh temporary workspace;
2. it runs your command there;
3. it grades what the command left behind.

```bash
piibench run --cmd "python $PWD/my_cleaner.py"                         # the ten main tasks, once each
piibench run --cmd "python $PWD/my_cleaner.py" --split hard            # the hard split
piibench run --cmd "python $PWD/my_cleaner.py" --split all --repeats 3 --out runs.jsonl --timeout 1800
```

* **Where the command runs.** It runs in the workspace (the current directory), so give your own script by its
  absolute path, as `$PWD/...` above does. The workspace path is also available as `$PIIBENCH_WORKSPACE` or
  `{workspace}`, and the task id as `$PIIBENCH_TASK` or `{task}`.
* **Failed runs.** A run whose command exits with an error or times out is still graded, but `run` prints the end
  of its error output and exits with status 1. The `--out` line records the status and the error output.
* **What gets saved.** `--out` appends one JSON line per run: the full grader report, the elapsed time and the
  command's exit status. `--keep DIR` keeps every cleaned workspace.
* **The summary.** At the end, `run` prints the mean score over tasks, the number of solved runs and the share of
  people fully right.

[`examples/rules_cleaner.py`](../examples/rules_cleaner.py) is a minimal cleaner that shows the interface.

## 4. What a method receives and what it must do

The workspace contains:

| File | Contents |
|---|---|
| `TASK.md` | The cleaning rules. Give this text to an LLM-based method as its instructions. |
| `pseudonyms.json` | The persona pool: synthetic identities with `persona_id`, `full_name`, `first_name`, `last_name`, `email`, `username`. |
| `data/*.jsonl` | The records to clean: commits, issues, comments, pull requests, reviews, mail, CI builds, a developer directory (it depends on the task). |

The method edits the files in place. The rules in `TASK.md`, in short:

1. **Replace every reference to a real person,** in structured fields and in free text, including usernames inside
   URLs and paths.
2. **Map each person to exactly one persona** and use it everywhere. Personas can come from the pool, or the method
   may invent its own and list them in `mapping.json`: a JSON list of objects with the same six fields as the pool.
   `mapping.json` ships with the cleaned export, so it must never contain an original name, email or username.
3. **Never map two different people to the same persona.** Identical names do not imply the same person.
4. **Use the persona field that fits the slot:** a name for a name, an email for an email, a username for a
   username or handle.
5. **Change nothing else:** ids, hashes, issue keys, timestamps, links, bot accounts, mailing lists and code stay as
   they are.
6. **Keep every file valid JSON Lines,** with the same records, keys and value types.

## 5. Evaluating an LLM agent

Any agent that can read and write files in a directory can be evaluated:

```bash
piibench run --cmd "my-agent --instructions TASK.md --workdir ." --split all --repeats 3 --out agent.jsonl
```

* **Prompt.** Give the agent `TASK.md` as its prompt or system instructions. It needs file access to the workspace;
  a shell is optional but helps, since most strong strategies write a small script.
* **Isolation.** Keep the agent away from the repository, because each task's `gold.json` is the answer key. Run it
  in a container or sandbox that only sees the workspace. `piibench run` only copies the input, but an agent with
  a shell could still walk up the directory tree.
* **Output only.** Only the files count. The agent's final message is ignored.
* **Per-turn output limits.** If your harness caps the tokens a model may produce in one turn, check that no run
  was cut off. A truncated run measures the harness, not the model.

## 6. Reading the score

```text
score  = gate x harmonic_mean(ER, CC, P, RI)
gate   = files valid AND no reference between records changed AND >= 90% of non-personal text kept verbatim
solved = gate AND ER = CC = P = RI = 1
```

| Component | Meaning |
|---|---|
| ER, entity recall | Share of people with nothing identifying left anywhere (accents and case are ignored; a name glued into another word still counts) |
| CC, continuity | Share of people whose every replaced mention is the same persona, in the right form for its slot, used by nobody else |
| P, edit precision | Share of edits that fall on personal data rather than on other content |
| RI, integrity | Files parse, all records are present, every reference resolves to its original target |

The report also contains, among others:

* occurrence recall, overall and split into structured fields and free text;
* separation;
* B-cubed precision and recall;
* form accuracy;
* text preservation;
* the strict score, which is the minimum of the four components.

`entities_ok` lists the people who came out fully right.

**One wrong person barely moves the mean score.** ER and CC are shares of people, so one wrong person in a 27-person
task costs about 0.01-0.02. A score of 0.99 can still mean that a real developer is identifiable. Report the share
of tasks **solved** and the share of **people fully right** alongside the mean.

[`DESIGN.md`](DESIGN.md) section 6 defines every metric and explains each choice.
[`WALKTHROUGH.md`](WALKTHROUGH.md) shows what common mistakes score on one task.

## 7. Reporting results

To make results comparable:

* **Report the splits separately:** the ten main tasks, and the two hard tasks.
* **Use several runs per task when the method is stochastic,** at least three. Report the mean, the spread between
  runs, and how many tasks were solved in every run.
* **Report the three headline measures:** mean score, tasks solved, and people fully right. Add cost and time if
  relevant.
* **Exclude incomplete runs and re-run them.** If a run fails for infrastructure reasons (an API error, a timeout),
  say so rather than counting it as a score of 0.

The rule baselines in [`BASELINES.md`](BASELINES.md) are the reference point.

## 8. Exporting the tasks as a dataset

`python scripts/export_hf.py` writes the tasks to `hf/` as a dataset in the Hugging Face layout (JSON Lines plus a
dataset card), with two configurations, each with a `main` and a `hard` split:

* `tasks`: one row per task, with the instructions, the persona pool, every data file and the gold;
* `occurrences`: one row per occurrence of a person, with its file, record, field path and character span. This is
  useful for detection-style evaluation without the grader.

The `tasks` rows can be turned back into task folders that are byte-identical to this repository's:

```bash
piibench materialize hf/data/tasks/main.jsonl hf/data/tasks/hard.jsonl --out ./piibench-tasks
export PIIBENCH_TASKS_ROOT=$PWD/piibench-tasks
piibench list
```

## 9. Checks and new tasks

`piibench check` runs the generation checks on every task:

* an independent PII scan;
* the solvability check: every first-name mention is attributable from its own thread;
* the gold scores 1;
* doing nothing scores 0.

`piibench baselines` runs the built-in baselines.

**New tasks need the raw datasets** ([`DATA.md`](DATA.md)):

* `python -m piibench.generate` builds the 20-MAD tasks from `configs/tasks.json`.
* `python scripts/build_smartshark_linked.py` builds the SmartSHARK tasks from a local MongoDB restore, and `--hard`
  builds the hard split.

Both are deterministic.

The SmartSHARK builder's `Spec` exposes the difficulty levers:

| Parameter | Effect |
|---|---|
| `opaque_share` | Share of developers whose GitHub handle is unrelated to their name |
| `opaque_by_handle` | Reviewers address anonymous developers by `@handle`, so commits are the only evidence |
| `signature_handle_share` | Share of anonymous developers who name their handle in a mail signature (an easy clue) |
| `shared_first_pairs` | Pairs of people, never in the same thread, who share a first name |
| `namesake_pairs` | Pairs of people with the same full name inside one pull request |
| `nickname_rate`, `obfuscated_signature_share`, `name_variant_share` | Nicknames, `name at domain dot org` addresses, accented, initialled or lowercase spellings |
| `trailer_rate`, `code_mention_rate`, `profile_url_rate`, `quote_signatures` | Commit trailers, `@author` and `TODO(handle)`, profile URLs, quoted signatures in replies |

Every generated task is validated before it is written: PII scan, solvability, gold = 1, and every anonymous
handle linked by at least one commit.
