Task t01-zookeeper-directory: 6 entities, 138 occurrences

| Cleaning | score | score with schema gate only | ER | CC | P | RI | text kept | occurrence recall |
|---|---|---|---|---|---|---|---|---|
| no-op | 0.000 | 0.000 | 0.00 | 0.00 | 1.000 | 1.000 | 1.00 | 0.000 |
| gold applied | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| gold applied, other persona bijection | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| one missed email | 0.952 | 0.952 | 0.83 | 1.00 | 1.000 | 1.000 | 1.00 | 0.993 |
| one missed free-text alias | 0.952 | 0.952 | 0.83 | 1.00 | 1.000 | 1.000 | 1.00 | 0.993 |
| one occurrence -> other persona (other file) | 0.952 | 0.952 | 1.00 | 0.83 | 1.000 | 1.000 | 1.00 | 1.000 |
| two entities merged into one persona | 0.889 | 0.889 | 1.00 | 0.67 | 1.000 | 1.000 | 1.00 | 1.000 |
| redact everything | 0.000 | 0.000 | 1.00 | 0.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| random persona per occurrence | 0.000 | 0.000 | 1.00 | 0.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| structured fields only | 0.667 | 0.667 | 0.33 | 1.00 | 1.000 | 1.000 | 1.00 | 0.906 |
| gold + one summary redacted | 0.998 | 0.998 | 1.00 | 1.00 | 0.993 | 1.000 | 0.99 | 1.000 |
| gold + one broken foreign key | 0.000 (links gate) | 0.992 | 1.00 | 1.00 | 0.993 | 0.977 | 1.00 | 1.000 |
| gold + truncated JSONL | 0.000 (schema gate) | 0.000 | 1.00 | 0.00 | 1.000 | 0.724 | 1.00 | 1.000 |
| gold + every foreign key repointed to another real record | 0.000 (links gate) | 0.879 | 1.00 | 1.00 | 0.852 | 0.727 | 1.00 | 1.000 |
| pseudonyms kept, all other prose deleted | 0.000 (text gate) | 0.609 | 1.00 | 0.33 | 0.636 | 1.000 | 0.01 | 1.000 |
| invented personas, declared in mapping.json | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| invented personas, username = first name, declared | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| invented personas, not declared | 0.000 | 0.000 | 1.00 | 0.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| invented personas, mapping.json also lists real names | 0.000 | 0.000 | 0.00 | 1.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| one full name written without its space (real name kept) | 0.952 | 0.952 | 0.83 | 1.00 | 1.000 | 1.000 | 1.00 | 0.993 |
