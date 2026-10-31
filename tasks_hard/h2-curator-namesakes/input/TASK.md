You are cleaning personally identifiable information (PII) from an export of linked
software-engineering records before it is shared outside the organisation.

Working directory: the current working directory
Files to clean (JSON Lines, one JSON object per line):
- data/commits.jsonl  (git commits: author/committer (name, email), message, linked issue)
- data/issues.jsonl  (Jira issues: reporter/assignee accounts, summary, description)
- data/comments.jsonl  (Jira comments: author account, body (includes ASF GitHub Bot notices))
- data/pull_requests.jsonl  (GitHub pull requests: creator account, source repository, commits, linked issue)
- data/reviews.jsonl  (GitHub pull-request reviews: reviewer account, state, body)
- data/pr_comments.jsonl  (GitHub pull-request comments: author account, body)
- data/mail.jsonl  (developer mailing-list messages: sender name and address, subject, body, reply link)
- data/ci_builds.jsonl  (Travis CI builds: commit, state, console log)
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
