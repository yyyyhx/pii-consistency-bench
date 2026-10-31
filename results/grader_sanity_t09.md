Task t09-storm-linked-accounts: 27 entities, 416 occurrences

| Cleaning | score | score with schema gate only | ER | CC | P | RI | text kept | occurrence recall |
|---|---|---|---|---|---|---|---|---|
| no-op | 0.000 | 0.000 | 0.00 | 0.00 | 1.000 | 1.000 | 1.00 | 0.000 |
| gold applied | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| gold applied, other persona bijection | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| one missed email | 0.990 | 0.990 | 0.96 | 1.00 | 1.000 | 1.000 | 1.00 | 0.998 |
| one missed free-text alias | 0.990 | 0.990 | 0.96 | 1.00 | 1.000 | 1.000 | 1.00 | 0.998 |
| one occurrence -> other persona (other file) | 0.990 | 0.990 | 1.00 | 0.96 | 1.000 | 1.000 | 1.00 | 1.000 |
| two entities merged into one persona | 0.980 | 0.980 | 1.00 | 0.93 | 1.000 | 1.000 | 1.00 | 1.000 |
| redact everything | 0.000 | 0.000 | 1.00 | 0.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| random persona per occurrence | 0.000 | 0.000 | 1.00 | 0.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| structured fields only | 0.733 | 0.733 | 0.41 | 1.00 | 1.000 | 1.000 | 1.00 | 0.692 |
| gold + one summary redacted | 0.999 | 0.999 | 1.00 | 1.00 | 0.998 | 1.000 | 1.00 | 1.000 |
| gold + one broken foreign key | 0.000 (links gate) | 0.998 | 1.00 | 1.00 | 0.998 | 0.995 | 1.00 | 1.000 |
| gold + truncated JSONL | 0.000 (schema gate) | 0.000 | 1.00 | 0.33 | 1.000 | 0.859 | 1.00 | 1.000 |
| gold + every foreign key repointed to another real record | 0.000 (links gate) | 0.890 | 1.00 | 1.00 | 0.832 | 0.774 | 1.00 | 1.000 |
| pseudonyms kept, all other prose deleted | 0.000 (text gate) | 0.596 | 1.00 | 0.41 | 0.443 | 1.000 | 0.06 | 1.000 |
| invented personas, declared in mapping.json | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| invented personas, username = first name, declared | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| invented personas, not declared | 0.000 | 0.000 | 1.00 | 0.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| invented personas, mapping.json also lists real names | 0.000 | 0.000 | 0.00 | 1.00 | 1.000 | 1.000 | 1.00 | 1.000 |
| one full name written without its space (real name kept) | 0.990 | 0.990 | 0.96 | 1.00 | 1.000 | 1.000 | 1.00 | 0.998 |
