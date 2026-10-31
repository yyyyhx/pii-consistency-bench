"""Gold-independent scan of agent-visible task text for leftover real-person identifiers.

The generator's completeness check and the grader both look for the tokens registered in the gold
manifest, so a real name the sanitizer missed is invisible to both. This scan looks for person-shaped
strings without consulting the gold, and only uses the gold (plus the pool and distractors) to skip
strings already known to be synthetic. It is a heuristic second opinion: a real name that is also an
ordinary English word ("Will", "pat") can still pass.

    python -m piibench.pii_scan tasks/<id> [...]     # prints masked findings, exit 1 if any
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from .gold import leaf_paths
from .grader.leaks import fold
from .sanitize import TECH_NAME_ALLOW, is_word


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
LIST_LOCAL_RE = re.compile(r"^(dev|user|users|commits|issues|notifications|private|general|announce|builds|reviews)$", re.I)
RESERVED_DOMAIN_RE = re.compile(r"(^|\.)(example|invalid|test|localhost)(\.|$)|pseudonym", re.I)
URL_USER_RE = re.compile(r"(?:github\.com/|people\.apache\.org/~|/home/|/Users/|[\s(]~)([A-Za-z][\w.-]{1,38})")
# a cue word must end at a word boundary ("byilmaz" is a username, not "by ilmaz"); "r=" and trailers need none
CUE_RE = re.compile(r"\b(?:(?:by|thanks|thank you|cc|ping|hi|hey|dear|via)\b|r=|sr=|reviewed-by:|signed-off-by:)\s*:?\s*"
                    r"([A-Za-z][\w.-]{1,30})", re.I)
CAP_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")
SYSTEM_USERS = {"apache", "jenkins", "jenkins-agent", "travis", "runner", "root", "ubuntu"}
# Hits reviewed by hand in the committed tasks and confirmed not to be people (function names, tools).
REVIEWED_NON_NAMES = {"getlogin"}


# BSD /usr/share/dict word lists, bundled so the scan is identical on every platform (see data/README.md)
DATA = Path(__file__).with_name("data")


def _load(path: Path) -> list[str]:
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return raw.decode("utf-8", errors="ignore").split()


# Only words the dictionary lists in lowercase count as ordinary English; capitalized entries are proper nouns.
_WORDS = {w for w in _load(DATA / "web2.txt.gz") if w.islower()}
_PROPER = set(_load(DATA / "propernames.txt")) - {w.capitalize() for w in _WORDS}


def available() -> bool:
    return bool(_WORDS) and bool(_PROPER)


def _codeish(tok: str) -> bool:
    """Issue keys, file names, CamelCase identifiers and numbers are not names (handles like jdoe42 are)."""
    return (bool(re.search(r"[._/]|[a-z][A-Z]", tok)) or tok.isupper() or tok.isdigit()
            or bool(re.fullmatch(r"[A-Za-z]+-\d+", tok)) or bool(re.fullmatch(r"[0-9a-f]{7,40}", tok)))


def mask(tok: str) -> str:
    return f"{tok[:1]}{'*' * (len(tok) - 1)}#{hashlib.sha1(tok.encode()).hexdigest()[:6]}"


def scan_strings(strings: list[str], known: set[str]) -> dict[str, Counter]:
    """Findings by category. ``known`` holds casefolded strings that are synthetic or allowed."""
    found: dict[str, Counter] = {"email": Counter(), "url_user": Counter(), "cue_name": Counter(),
                                 "proper_name": Counter()}
    for v in strings:
        for m in EMAIL_RE.finditer(v):
            addr = m.group(0)
            local, domain = addr.split("@", 1)
            if RESERVED_DOMAIN_RE.search(domain) or fold(addr) in known or LIST_LOCAL_RE.match(local):
                continue
            found["email"][addr] += 1
        for m in URL_USER_RE.finditer(v):
            u = m.group(1).split("/")[0].rstrip(".")
            if fold(u) not in known and u.casefold() not in SYSTEM_USERS and not is_word(u, _WORDS):
                found["url_user"][u] += 1
        for m in CUE_RE.finditer(v):
            w = m.group(1).rstrip(".")
            if len(w) < 3 or fold(w) in known or _codeish(w) or w.casefold() in REVIEWED_NON_NAMES:
                continue
            if all(is_word(part, _WORDS) for part in w.split("-") if part and not part.isdigit()):
                continue
            found["cue_name"][w] += 1
        for m in CAP_RE.finditer(v):
            w = m.group(0)
            if w in _PROPER and fold(w) not in known and w not in TECH_NAME_ALLOW:
                found["proper_name"][w] += 1
    return {k: c for k, c in found.items() if c}


def known_tokens(task_dir: Path) -> set[str]:
    task_dir = Path(task_dir)
    gold = json.loads((task_dir / "gold.json").read_text(encoding="utf-8"))
    known = {fold(x) for e in gold["entities"] for vals in e["tokens"].values() for x in vals}
    known |= {fold(d["value"]) for d in gold.get("distractors", [])}
    for p in json.loads((task_dir / "input" / "pseudonyms.json").read_text(encoding="utf-8")):
        known |= {fold(str(p[f])) for f in ("first_name", "last_name", "username", "email")}
    return known


def scan_task(task_dir: Path) -> dict[str, Counter]:
    task_dir = Path(task_dir)
    gold = json.loads((task_dir / "gold.json").read_text(encoding="utf-8"))
    strings = []
    for rel in gold["files"]:
        for line in (task_dir / "input" / rel).read_text(encoding="utf-8").splitlines():
            if line.strip():
                strings += [v for v in leaf_paths(json.loads(line)).values() if isinstance(v, str)]
    return scan_strings(strings, known_tokens(task_dir))


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    bad = 0
    for d in argv:
        f = scan_task(Path(d))
        bad += sum(sum(c.values()) for c in f.values())
        print(Path(d).name, {k: {mask(t): n for t, n in c.items()} for k, c in f.items()} or "clean")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
