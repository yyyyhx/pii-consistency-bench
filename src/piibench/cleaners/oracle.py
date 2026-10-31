"""Reference cleaner that applies the gold manifest (upper bound and grader test fixture)."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path

from ..gold import Gold, get_path, read_jsonl, set_path, write_jsonl
from ..personas import render_persona as render


def assignment(gold: Gold, personas: list[dict], seed: int | None = None) -> dict[str, dict]:
    """entity_id -> persona; ``seed`` picks a different (equally valid) bijection."""
    order = list(personas)
    if seed is not None:
        random.Random(seed).shuffle(order)
    return {e.entity_id: order[i] for i, e in enumerate(gold.entities)}


def apply(workdir: Path, gold: Gold, mapping: dict[str, dict], skip: set[str] | None = None,
          override: dict[str, str] | None = None) -> None:
    """Rewrite ``workdir/data`` in place. ``skip`` leaves occurrences untouched; ``override`` forces a value."""
    skip = skip or set()
    override = override or {}
    by_file: dict[str, dict[tuple[str, str], list]] = defaultdict(lambda: defaultdict(list))
    for o in gold.occurrences:
        by_file[o.file][(o.key, o.path)].append(o)
    for rel, fields in by_file.items():
        spec = gold.files[rel]
        rows = read_jsonl(workdir / rel)
        idx = {str(r[spec["key"]]): r for r in rows}
        for (key, path), occs in fields.items():
            rec = idx[key]
            spanned = sorted((o for o in occs if o.span), key=lambda o: o.span[0], reverse=True)
            for o in occs:
                if o.span is None and o.occ_id not in skip:
                    set_path(rec, path, override.get(o.occ_id, render(mapping[o.entity_id], o.form)))
            if spanned:
                text = get_path(rec, path)
                for o in spanned:
                    if o.occ_id in skip:
                        continue
                    s, e = o.span
                    text = text[:s] + override.get(o.occ_id, render(mapping[o.entity_id], o.form)) + text[e:]
                set_path(rec, path, text)
        write_jsonl(workdir / rel, rows)


def clean(workdir: Path, gold: Gold, seed: int | None = None) -> None:
    personas = json.loads((Path(workdir) / "pseudonyms.json").read_text(encoding="utf-8"))
    apply(Path(workdir), gold, assignment(gold, personas, seed))
