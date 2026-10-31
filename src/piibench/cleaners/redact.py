"""Privacy-only baseline: redact instead of pseudonymize (destroys identity continuity)."""

from __future__ import annotations

from pathlib import Path

from . import rules


def clean(workdir: Path) -> dict:
    """Run the rule-based detector, then overwrite every replacement with a redaction token."""
    workdir = Path(workdir)
    pool = workdir / "pseudonyms.json"
    original = pool.read_text(encoding="utf-8")
    import json

    redacted = [{"persona_id": p["persona_id"], "full_name": "[REDACTED]", "first_name": "[REDACTED]",
                 "last_name": "[REDACTED]", "email": "[REDACTED_EMAIL]", "username": "[REDACTED_USER]"}
                for p in json.loads(original)]
    pool.write_text(json.dumps(redacted), encoding="utf-8")
    try:
        return rules.clean(workdir)
    finally:
        pool.write_text(original, encoding="utf-8")
