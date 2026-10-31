"""Remove real-person references from 20-MAD free text before it is used as carrier prose.

The pipeline is conservative: anything that looks like a person reference is either stripped (known
attribution slots) or causes the paragraph to be dropped. See docs/DESIGN.md §4.
"""

from __future__ import annotations

import gzip
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .personas import SOURCE_FIRST, SOURCE_LAST, TARGET_FIRST, TARGET_LAST

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
JIRA_MENTION_RE = re.compile(r"\[~([^\]\s]+)\]")
AT_MENTION_RE = re.compile(r"(?<![\w.@/])@([A-Za-z][\w.-]{1,38})")
GITHUB_USER_RE = re.compile(r"\bgit[Hh]ub user (\S+)", re.I)
CAP_WORD_RE = re.compile(r"\b[A-Z][a-z]+\b")
LOWER_WORD_RE = re.compile(r"\b[a-z][a-z0-9_.-]{2,}\b")

# Attribution slots in commit messages (Apache).
CONTRIB_RE = re.compile(r"[\s.;,]*\(?\s*(?:Contributed|Patch(?:ed)?|Submitted)\s+by:?\s+([^\n()]+?)\s*\)?\s*(?:\.\s*)?(?=\n|$)", re.I)
VIA_RE = re.compile(r"\s*\(([^()\n]{2,80}?)\s+via\s+([^()\n]{2,60}?)\)")
TRAILER_RE = re.compile(r"^\s*(?:Signed-off-by|Reviewed-by|Co-authored-by|Acked-by|Tested-by|Reported-by|Author|Reviewed by|Reviewers?)\s*:?.*$", re.I | re.M)
TRAILING_PAREN_RE = re.compile(r"\s*\(([A-Za-z][A-Za-z .,'&-]{1,60})\)\s*$")
NON_NAME_PARENS = {"cherry", "backport", "addendum", "branch", "trunk", "wip", "partial", "followup", "follow-up",
                   "test", "tests", "docs", "doc", "revert", "reverted", "amended", "part", "take", "again"}
# Attribution slots in commit messages (Mozilla).
# "r=a,b" lists handles; "ui-r=x, r=y" is two separate pairs, so a list item must not itself be "key=".
MOZ_REVIEW_RE = re.compile(r"[,;]?\s*\b(?:r|sr|a|f|p|rs|ui-r|ui-review|feedback|review)\s*[=+]\s*[\w.:|/+-]+"
                           r"(?:\s*,\s*(?![\w-]+\s*[=+])[\w.:|/+-]+)*", re.I)
LEFTOVER_ASSIGN_RE = re.compile(r"(?<![\w-])=[A-Za-z]")

# Greeting / thanks / quoting patterns in comments (used for harvesting and rejection).
ADDRESS_PATTERNS = [
    re.compile(r"\b(?:Hi|Hey|Hello|Dear|Thanks|Thank you|Thx|cc|CC|ping)[,:]?\s+([A-Z][a-z]+)"),
    re.compile(r"\b(?:Thanks|Thank you)\b[^.!?\n]{0,40},\s*([A-Z][a-z]+)\s*[!.,]"),
    re.compile(r"^([A-Z][a-z]+)\s*[,:]\s", re.M),
    re.compile(r"\b([A-Z][a-z]+)\s+(?:wrote|said|mentioned|suggested|pointed out):?"),
    re.compile(r"^--\s*([A-Z][a-z]+)", re.M),
]
LOWER_ADDRESS_RE = re.compile(r"^([a-z]{2,15})\s*,\s", re.M)
INITIALS_RE = re.compile(r"\b(?:like|per|as|cc|ping|thanks)\s+[A-Z]{2,3}\b|"
                         r"\b[A-Z]{2,3}\s+(?:notes|noted|said|says|suggested|mentioned|points out|pointed out|wrote|asked)\b")
QUOTE_HEADER_RE = re.compile(r"\(In reply to .*?\)|\bwrote:", re.I)
IN_REPLY_RE = re.compile(r"\(In reply to (.+?) from comment #\d+\)")
BZ_NICK_RE = re.compile(r"[\[(]:([\w.-]+)[\])]")
NEEDINFO_RE = re.compile(r"needinfo\?\(", re.I)
PUSHED_BY_RE = re.compile(r"^Pushed by \S+", re.M)

# Technical words that are also personal names; allowed as product/library terms.
TECH_NAME_ALLOW = {"Hudson", "Jenkins", "Ivy", "Maven", "Avro", "Jackson", "Mockito", "Curator", "Jute", "Thrift",
                   "Kerby", "Ant", "Mozilla", "Thunderbird", "Firefox", "Apache", "Hadoop", "Lightning", "Netty",
                   "Guava", "Junit", "Jetty", "Kerberos", "Linux", "Windows", "Ubuntu", "Debian", "Python", "Perl",
                   "Java", "Scala", "Ruby", "Docker", "Travis", "Github", "Jira", "Bugzilla", "Gecko", "Yetus",
                   "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec",
                   "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
NOT_NAMES = {"The", "A", "An", "And", "Or", "But", "So", "Not", "No", "Yes", "Ok", "Okay"}
CONTRACTION_STEMS = {"didn", "doesn", "wouldn", "couldn", "shouldn", "isn", "wasn", "aren", "weren", "hasn",
                     "haven", "hadn", "won", "ain", "mustn", "needn", "don", "can"}
GITHUB_MERGE_RE = re.compile(r"\b(Closes|closes|This closes|Fixes) #(\d+) from ([\w.-]+)/(\S+)")
SENTENCE_START_RE = re.compile(r"(?:^|[.!?:;]\s+|\n\s*|[\"'(\[]\s*)$")


def is_word(token: str, english_lower: set[str]) -> bool:
    """Dictionary lookup tolerant to common English inflections (web2 lists base forms only)."""
    w = token.lower().strip("'")
    if w in english_lower or w in CONTRACTION_STEMS:
        return True
    cands = []
    if w.endswith("'s"):
        cands.append(w[:-2])
    if w.endswith("ies") or w.endswith("ied"):
        cands.append(w[:-3] + "y")
    if w.endswith("es"):
        cands.append(w[:-2])
    if w.endswith("s"):
        cands.append(w[:-1])
    for suf in ("ed", "ing", "er", "ers", "est", "ly", "ness", "ment", "able", "ally"):
        if w.endswith(suf) and len(w) > len(suf) + 2:
            stem = w[: -len(suf)]
            cands += [stem, stem + "e"]
            if len(stem) > 2 and stem[-1] == stem[-2]:
                cands.append(stem[:-1])
            if stem.endswith("i"):
                cands.append(stem[:-1] + "y")
    return any(c in english_lower for c in cands)


def _load_words() -> tuple[set[str], set[str]]:
    """(common lowercase English words, given names)."""
    # the BSD word lists bundled with the package, so sanitization is identical on every platform
    data = Path(__file__).with_name("data")
    words = gzip.decompress((data / "web2.txt.gz").read_bytes()).decode("utf-8", errors="ignore").split()
    lower = {w for w in words if w.islower()}
    given = set((data / "propernames.txt").read_text(encoding="utf-8", errors="ignore").split())
    return lower, given


@dataclass
class NameLexicon:
    english_lower: set[str]
    names: set[str]  # personal-name tokens rejected wherever they appear capitalized
    word_names: set[str]  # given names that are also English words: rejected only mid-sentence
    handles: set[str]  # lowercase handles harvested from mentions
    tech: set[str] = field(default_factory=set)

    def is_word(self, token: str) -> bool:
        return is_word(token, self.english_lower)

    def stats(self) -> dict[str, int]:
        return {"english_lower": len(self.english_lower), "names": len(self.names),
                "word_names": len(self.word_names), "handles": len(self.handles), "tech": len(self.tech)}


def _split_name(text: str) -> list[str]:
    return [t for t in re.split(r"[\s,.&/'-]+", text) if t]


def harvest(commit_messages: Iterable[str], comment_texts: Iterable[str]) -> tuple[Counter, Counter, Counter]:
    """Collect personal-name tokens and handles from attribution/mention slots.

    Returns (strong names, handles, weak names). Strong names come from slots that only ever hold
    people (contributed-by, via, trailers, in-reply-to); weak names from greeting/address patterns,
    which also capture ordinary words.
    """
    names: Counter = Counter()
    weak: Counter = Counter()
    handles: Counter = Counter()
    for msg in commit_messages:
        if not msg:
            continue
        for m in CONTRIB_RE.finditer(msg):
            for t in _split_name(m.group(1)):
                (names if t[:1].isupper() else handles)[t] += 1
        for m in VIA_RE.finditer(msg):
            for t in _split_name(m.group(1)) + _split_name(m.group(2)):
                (names if t[:1].isupper() else handles)[t] += 1
        for m in TRAILER_RE.finditer(msg):
            for t in _split_name(EMAIL_RE.sub(" ", m.group(0).split(":", 1)[-1])):
                if t[:1].isupper():
                    names[t] += 1
        for m in MOZ_REVIEW_RE.finditer(msg):
            for h in re.split(r"[=+,\s]+", m.group(0))[1:]:
                h = h.strip(":|/ ").lower()
                if len(h) >= 3:
                    handles[h] += 1
        for m in GITHUB_MERGE_RE.finditer(msg):
            handles[m.group(3).lower()] += 1
    for text in comment_texts:
        if not text:
            continue
        for rx in (JIRA_MENTION_RE, AT_MENTION_RE, GITHUB_USER_RE, BZ_NICK_RE):
            for m in rx.finditer(text):
                handles[m.group(1).lower().strip(".")] += 1
        for m in IN_REPLY_RE.finditer(text):
            for t in _split_name(BZ_NICK_RE.sub(" ", m.group(1))):
                if t[:1].isupper():
                    names[t] += 1
        for rx in ADDRESS_PATTERNS:
            for m in rx.finditer(text):
                weak[m.group(1)] += 1
    return names, handles, weak


def build_lexicon(commit_messages: list[str], comment_texts: list[str], technical_texts: list[str],
                  tech_min_docs: int = 3) -> NameLexicon:
    english_lower, given = _load_words()
    strong, h_handles, weak = harvest(commit_messages, comment_texts)
    synthetic = set(SOURCE_FIRST) | set(SOURCE_LAST) | set(TARGET_FIRST) | set(TARGET_LAST)
    df: Counter = Counter()
    df_lower: Counter = Counter()
    for t in technical_texts:
        df.update(set(CAP_WORD_RE.findall(t or "")))
        df_lower.update({w.lower() for w in re.findall(r"\b[A-Za-z][a-z0-9]{2,}\b", t or "")})
    # Weakly harvested tokens (address patterns also catch "Hi All,", "Findbugs:") that are frequent in
    # attribution-stripped summaries/subjects are technical terms unless they are given names.
    # Strong-slot names ("Contributed by Arpit ...", "Jing Zhao") are never exempted.
    strong_names = {n for n in strong if len(n) >= 2 and n[:1].isupper()}
    technical_harvest = {w for w in weak if df[w] >= 5 and w not in given and w not in strong_names}
    weak_names = {n for n in weak if not is_word(n, english_lower)} - technical_harvest
    candidates = (given | strong_names | weak_names | synthetic) - TECH_NAME_ALLOW - NOT_NAMES
    # Names that are also English words ("Will", "Jing") are rejected only when capitalized mid-sentence.
    word_names = {n for n in candidates if is_word(n, english_lower) and n not in synthetic}
    names = candidates - word_names
    handles = {h for h in h_handles if not is_word(h, english_lower) and len(h) >= 3
               and (df_lower[h] < 5 or h.capitalize() in given)}
    tech = {w for w, c in df.items() if c >= tech_min_docs and w not in names and w not in word_names}
    tech |= TECH_NAME_ALLOW | technical_harvest
    return NameLexicon(english_lower=english_lower, names=names, word_names=word_names, handles=handles, tech=tech)


def reject_reason(text: str, lex: NameLexicon, min_chars: int = 25) -> str | None:
    """Why a carrier paragraph must not be used, or None if it is acceptable."""
    if len(text.strip()) < min_chars or len(re.findall(r"[A-Za-z]{2,}", text)) < 3:
        return "too_short"
    if EMAIL_RE.search(text):
        return "email"
    if (JIRA_MENTION_RE.search(text) or AT_MENTION_RE.search(text) or GITHUB_USER_RE.search(text)
            or GITHUB_MERGE_RE.search(text) or BZ_NICK_RE.search(text) or NEEDINFO_RE.search(text)
            or PUSHED_BY_RE.search(text)):
        return "mention"
    if QUOTE_HEADER_RE.search(text):
        return "quote_header"
    if LEFTOVER_ASSIGN_RE.search(text):
        return "assign_leftover"
    for rx in ADDRESS_PATTERNS:
        m = rx.search(text)
        if m and (m.group(1) in lex.names or m.group(1) in lex.word_names or not lex.is_word(m.group(1))):
            return f"address:{m.group(1)}"
    m = LOWER_ADDRESS_RE.search(text)
    if m and (m.group(1).capitalize() in lex.names or m.group(1).capitalize() in lex.word_names
              or m.group(1) in lex.handles):
        return f"lower_address:{m.group(1)}"
    if INITIALS_RE.search(text):
        return "initials"
    for m in CAP_WORD_RE.finditer(text):
        w = m.group(0)
        if w in TECH_NAME_ALLOW:
            continue
        if w in lex.names:
            return f"name:{w}"
        if w in lex.word_names:
            if not SENTENCE_START_RE.search(text[: m.start()]):
                return f"name_mid_sentence:{w}"
            continue
        if w in lex.tech or lex.is_word(w):
            continue
        return f"unknown_proper:{w}"
    for tok in LOWER_WORD_RE.findall(text):
        for w in dict.fromkeys([tok, *re.split(r"[._-]+", tok)]):  # "grep.alex" hides "alex"; ordered
            if len(w) < 3:
                continue
            if w in lex.handles:
                return f"handle:{w}"
            cap = w.capitalize()
            if cap in lex.names and cap not in TECH_NAME_ALLOW and not lex.is_word(w):
                return f"lower_name:{w}"
    return None


@dataclass
class CommitSlots:
    """What the stripped attribution slots of a commit message looked like (pattern only, no names)."""

    contributed_by: bool = False
    via: bool = False
    trailing_paren: bool = False
    trailers: int = 0
    moz_review: bool = False


def strip_commit_message(msg: str, source: str) -> tuple[str, CommitSlots]:
    """Remove attribution slots; returns (text, which slot patterns were present)."""
    slots = CommitSlots()
    out = msg.replace("\r\n", "\n")
    if source == "mozilla":
        if MOZ_REVIEW_RE.search(out):
            slots.moz_review = True
            out = MOZ_REVIEW_RE.sub("", out)
        out = re.sub(r"\s*\b[Pp]atch (?:by|from)\s+[^,.;\n]+", "", out)
        out = re.sub(r"[\s,;]+\b(?:r|sr|a|f|rs)\s*[,;]?\s*(?=$|\n)", "", out)  # dangling "r," after stripping
        out = re.sub(r"\s*\((?:[^()]*\[:[\w.-]+\][^()]*)\)", "", out)  # "(Name [:nick])" credits
    else:
        if VIA_RE.search(out):
            slots.via = True
            out = VIA_RE.sub("", out)
        if CONTRIB_RE.search(out):
            slots.contributed_by = True
            out = CONTRIB_RE.sub("", out)
    out = GITHUB_MERGE_RE.sub(lambda m: f"{m.group(1)} #{m.group(2)}", out)
    n_trailers = len(TRAILER_RE.findall(out))
    if n_trailers:
        slots.trailers = n_trailers
        out = TRAILER_RE.sub("", out)
    lines = out.split("\n")
    if lines:
        m = TRAILING_PAREN_RE.search(lines[0])
        if m and not (set(m.group(1).lower().replace(",", " ").split()) & NON_NAME_PARENS):
            slots.trailing_paren = True
            lines[0] = lines[0][: m.start()]
    out = "\n".join(line.rstrip() for line in lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    out = re.sub(r"[\s,;]+$", "", out)
    return out, slots
