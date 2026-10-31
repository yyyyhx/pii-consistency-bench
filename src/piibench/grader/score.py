"""Grade a cleaned task directory against its gold manifest. Pure stdlib.

Score (grader 0.3) = gate x HM(entity_recall, continuity, precision, integrity), where the gate requires
schema validity, untouched links (no foreign key changed or left dangling), and text preservation of at
least ``TEXT_FLOOR``. ``score_v1`` (schema gate only) is reported for comparison.
See docs/DESIGN.md section 6 for the reasoning behind each rule.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import GRADER_VERSION
from ..gold import Gold, Occurrence, leaf_paths
from ..personas import FORM_CLASS
from .align import align
from .leaks import combined_pattern, entity_patterns, entity_substring_tokens, entity_tokens, fold

PRIMARY = ("entity_recall", "continuity", "precision", "integrity")
PERSONA_FIELDS = ("full_name", "first_name", "last_name", "email", "username")
# Non-PII text kept verbatim, as a fraction of the original. Below this the output is no longer a usable
# export whatever it does for privacy (deleting the prose around the names scores about 0.01-0.08).
TEXT_FLOOR = 0.90
# A cleaned value this much longer than the original is not aligned (difflib is quadratic).
MAX_GROWTH = 20
MAX_ALIGN_CHARS = 200_000


def _norm(s: str) -> str:
    return fold(re.sub(r"\s+", " ", s).strip())


class PersonaLexicon:
    """Maps rendered persona strings back to their (persona_id, form) candidates.

    One normalized string can render several forms (a username equal to the lowercased first name) or
    several personas (two declared personas sharing a surname), so each string keeps every candidate.
    """

    def __init__(self, personas: list[dict]):
        self.exact: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for p in personas:
            first, last = p["first_name"], p["last_name"]
            forms = {"full_name": p["full_name"], "first_name": first, "last_name": last,
                     "initial_last": f"{first[0]}. {last}", "last_first": f"{last}, {first}",
                     "email": p["email"], "username": p["username"]}
            for form, value in forms.items():
                cand = (p["persona_id"], form)
                if cand not in self.exact[_norm(value)]:
                    self.exact[_norm(value)].append(cand)
        self.exact = dict(self.exact)
        alts = sorted((re.escape(k) for k in self.exact), key=len, reverse=True)
        self._rx = re.compile(r"(?<![a-z0-9])(?:" + "|".join(alts) + r")(?![a-z0-9])") if alts else None

    def lookup(self, text: str) -> list[tuple[str, str]]:
        n = _norm(text)
        # "a.b at x dot org" (mail-archive obfuscation) is the same email as "a.b@x.org"
        n = re.sub(r"\s+dot\s+", ".", re.sub(r"\s+at\s+", "@", n)) if " at " in n else n
        if n in self.exact:
            return self.exact[n]
        if self._rx is None:
            return []
        hits = self._rx.findall(n)
        if not hits:
            return []
        return self.exact[max(hits, key=len)]


@dataclass
class OccResult:
    occ: Occurrence
    replacement: str | None
    changed: bool
    leaked: bool
    persona: str | None
    form: str | None
    form_ok: bool


@dataclass
class Report:
    score: float
    gate: bool
    components: dict[str, float]
    diagnostics: dict[str, Any]
    failures: dict[str, list] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"score": self.score, "gate": self.gate, "components": self.components,
                "diagnostics": self.diagnostics, "failures": self.failures}


def harmonic_mean(xs: list[float]) -> float:
    if any(x <= 0 for x in xs):
        return 0.0
    return len(xs) / sum(1.0 / x for x in xs)


def _parse_jsonl(path: Path) -> tuple[list[dict] | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"unreadable: {exc}"
    rows = []
    for i, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            return None, f"line {i + 1}: {exc.msg}"
        if not isinstance(obj, dict):
            return None, f"line {i + 1}: not an object"
        rows.append(obj)
    return rows, None


def _type(v: Any) -> str:
    return type(v).__name__ if v is not None else "null"


def decoded_text(path: Path) -> str:
    """All string values of a JSONL file, decoded and newline-joined (falls back to unescaped raw text).

    Scanning raw JSON would miss tokens after an escaped newline (``\\nName`` looks like ``nName``).
    """
    if not path.exists():
        return ""
    rows, _ = _parse_jsonl(path)
    if rows is not None:
        return "\n".join(str(v) for r in rows for v in leaf_paths(r).values() if isinstance(v, str))
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return re.sub(r"\\[nrtbf\"\\/]", " ", raw)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def load_declared_personas(output_dir: Path, pool: list[dict] | None = None) -> tuple[list[dict], list[str], str]:
    """Personas the agent invented, from an optional ``mapping.json`` next to the data files.

    Returns ``(personas, errors, text)``. ``text`` is every string in the file; the grader scans it for
    original identifiers, because a mapping that lists the real names is a re-identification key.
    Each object needs non-empty full_name, first_name, last_name, email and username. Invalid rows are
    skipped and reported in ``errors`` (surfaced as ``diagnostics.mapping_errors``) instead of failing
    silently. A persona whose full name, email or username repeats another persona's is kept, so the
    lexicon sees the ambiguity and continuity fails for both people, as for any merge.
    """
    path = Path(output_dir) / "mapping.json"
    if not path.is_file():
        return [], [], ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [], [f"mapping.json is not valid JSON: {exc}"], ""
    text = "\n".join(_strings(data))
    if not isinstance(data, list):
        return [], ["mapping.json must be a JSON list of persona objects"], text
    errors: list[str] = []
    declared: list[dict] = []
    seen_ids: set[str] = {p["persona_id"] for p in pool or []}
    taken: dict[tuple[str, str], str] = {}
    restated: set[tuple[str, ...]] = set()  # pool personas, and declared ones already accepted
    for p in pool or []:
        restated.add(tuple(_norm(p[f]) for f in PERSONA_FIELDS))
        for f in ("full_name", "email", "username"):
            taken[(f, _norm(p[f]))] = p["persona_id"]
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            errors.append(f"entry {i}: not an object")
            continue
        vals = {f: str(row.get(f) or "").strip() for f in PERSONA_FIELDS}
        missing = [f for f, v in vals.items() if not v]
        if missing:
            errors.append(f"entry {i}: missing {', '.join(missing)}")
            continue
        sig = tuple(_norm(vals[f]) for f in PERSONA_FIELDS)
        if sig in restated:  # agents often list the pool personas they used; that is not a new identity
            continue
        restated.add(sig)
        pid = str(row.get("persona_id") or f"declared-{i + 1}")
        if pid in seen_ids:
            pid = f"{pid}#{i + 1}"
            errors.append(f"entry {i}: duplicate persona_id, renamed to {pid}")
        seen_ids.add(pid)
        for f in ("full_name", "email", "username"):
            other = taken.get((f, _norm(vals[f])))
            if other is not None:
                errors.append(f"entry {i}: {f} {vals[f]!r} is also persona {other}")
            taken[(f, _norm(vals[f]))] = pid
        declared.append({"persona_id": pid, **vals})
    return declared, errors, text


def _fk_target(spec: dict, path: str) -> str | None:
    """Foreign-key target file for a leaf path; ``a.*`` in the spec matches list items ``a.0``, ``a.1``."""
    fks = spec.get("fk", {})
    if path in fks:
        return fks[path]
    generic = re.sub(r"\.\d+(?=\.|$)", ".*", path)
    return fks.get(generic)


def grade(input_dir: Path, output_dir: Path, gold: Gold, personas: list[dict] | None = None) -> Report:
    input_dir, output_dir = Path(input_dir), Path(output_dir)
    if personas is None:
        personas = json.loads((input_dir / "pseudonyms.json").read_text(encoding="utf-8"))
    declared, mapping_errors, mapping_text = load_declared_personas(output_dir, personas)
    personas = list(personas) + declared
    lex = PersonaLexicon(personas)
    ent_by_id = {e.entity_id: e for e in gold.entities}
    ent_rx = {e.entity_id: entity_patterns(e) for e in gold.entities}
    ent_sub = {e.entity_id: entity_substring_tokens(e) for e in gold.entities}

    occ_by_field: dict[tuple[str, str, str], list[Occurrence]] = defaultdict(list)
    for o in gold.occurrences:
        occ_by_field[(o.file, o.key, o.path)].append(o)

    schema_errors: list[str] = []
    integrity_checks = 0
    integrity_fail: list[str] = []
    link_fail: list[str] = []  # foreign keys changed or dangling: a hard gate
    overscrub: list[str] = []
    fp = 0
    out_keys_by_file: dict[str, set[str]] = {}
    out_rows_by_file: dict[str, list[dict]] = {}
    preserved_chars = literal_chars = 0
    results: list[OccResult] = []
    out_text: dict[str, str] = {}

    def unresolved(occs) -> list[OccResult]:
        return [OccResult(o, None, True, False, None, None, False) for o in occs]

    for rel, spec in gold.files.items():
        orig_rows, err = _parse_jsonl(input_dir / rel)
        assert orig_rows is not None, f"pristine input unreadable: {rel}: {err}"
        out_path = output_dir / rel
        out_rows, err = _parse_jsonl(out_path) if out_path.exists() else (None, "missing")
        out_text[rel] = decoded_text(out_path)
        key = spec["key"]
        integrity_checks += 1
        if out_rows is None:
            schema_errors.append(f"{rel}: {err}")
            integrity_fail.append(f"{rel}: unparseable/missing")
            out_rows = []
        elif len(out_rows) != len(orig_rows):
            schema_errors.append(f"{rel}: {len(out_rows)} records, expected {len(orig_rows)}")
            integrity_fail.append(f"{rel}: record count {len(out_rows)} != {len(orig_rows)}")
        out_rows_by_file[rel] = out_rows
        out_by_key: dict[str, dict] = {}
        for r in out_rows:
            if key in r:
                out_by_key.setdefault(str(r[key]), r)
        out_keys = set(out_by_key)

        for orow in orig_rows:
            k = str(orow[key])
            integrity_checks += 1
            orec = out_by_key.get(k)
            if orec is None:
                integrity_fail.append(f"{rel}:{k}: record missing or key changed")
                schema_errors.append(f"{rel}:{k}: record missing or key changed")
                for (f, kk, _p), occs in occ_by_field.items():
                    if f == rel and kk == k:
                        results += unresolved(occs)
                continue
            o_leaves, n_leaves = leaf_paths(orow), leaf_paths(orec)
            if set(o_leaves) != set(n_leaves):
                schema_errors.append(f"{rel}:{k}: field set changed")
            for path, ov in o_leaves.items():
                nv = n_leaves.get(path, None)
                if path in n_leaves and _type(ov) != _type(nv):
                    schema_errors.append(f"{rel}:{k}:{path}: type {_type(ov)} -> {_type(nv)}")
                occs = occ_by_field.get((rel, k, path), [])
                if _fk_target(spec, path) is not None:
                    integrity_checks += 1
                    if nv != ov:
                        msg = f"{rel}:{k}:{path}: foreign key changed {ov!r} -> {nv!r}"
                        integrity_fail.append(msg)
                        link_fail.append(msg)
                if not occs:
                    if nv != ov:
                        fp += 1
                        overscrub.append(f"{rel}:{k}:{path}: {str(ov)[:60]!r} -> {str(nv)[:60]!r}")
                    continue
                structured = [o for o in occs if o.span is None]
                spanned = sorted((o for o in occs if o.span is not None), key=lambda o: o.span[0])
                for o in structured:
                    rep = nv if isinstance(nv, str) else None
                    results.append(_eval(o, rep, ent_rx, ent_sub, lex))
                if spanned:
                    if not isinstance(nv, str):
                        results += unresolved(spanned)
                        continue
                    if len(nv) > max(MAX_ALIGN_CHARS, MAX_GROWTH * len(ov)):
                        fp += 1
                        literal_chars += len(ov) - sum(o.span[1] - o.span[0] for o in spanned)
                        overscrub.append(f"{rel}:{k}:{path}: value grew from {len(ov)} to {len(nv)} chars")
                        results += unresolved(spanned)
                        continue
                    al = align(ov, nv, [tuple(o.span) for o in spanned])
                    preserved_chars += al.preserved_chars
                    literal_chars += al.literal_chars
                    if al.overscrub_edits:
                        fp += al.overscrub_edits
                        overscrub.append(f"{rel}:{k}:{path}: {al.overscrub_edits} non-PII edit(s)")
                    for o, rep in zip(spanned, al.replacements, strict=True):
                        results.append(_eval(o, rep, ent_rx, ent_sub, lex))
            # non-PII text fields: count preservation for utility
            for tf in spec.get("text_fields", []):
                if tf in o_leaves and not occ_by_field.get((rel, k, tf)) and isinstance(o_leaves[tf], str):
                    literal_chars += len(o_leaves[tf])
                    if n_leaves.get(tf) == o_leaves[tf]:
                        preserved_chars += len(o_leaves[tf])
        out_keys_by_file[rel] = out_keys

    # foreign-key resolution against output files (list-valued keys are checked per element)
    for rel, spec in gold.files.items():
        if not spec.get("fk"):
            continue
        for r in out_rows_by_file.get(rel, []):
            for path, v in leaf_paths(r).items():
                target = _fk_target(spec, path)
                if target is None or v is None:
                    continue
                integrity_checks += 1
                if str(v) not in out_keys_by_file.get(target, set()):
                    msg = f"{rel}:{r.get(spec['key'])}:{path}: dangling reference {v!r}"
                    integrity_fail.append(msg)
                    link_fail.append(msg)

    # residual leaks anywhere in the output (outside or inside gold locations, and in mapping.json). A
    # token shared by several entities (deliberate name collisions) is charged to an entity only for hits
    # not already explained by leaked occurrences of the other sharers.
    owners: dict[str, set[str]] = defaultdict(set)
    for e in gold.entities:
        for t in entity_tokens(e):
            owners[fold(t)].add(e.entity_id)
    hits: Counter = Counter()
    all_rx = combined_pattern(gold.entities)
    for t in [*out_text.values(), mapping_text]:
        for m in all_rx.finditer(t):
            hits[m.group(0).casefold()] += 1
    mapping_leaks = sorted({m.group(0) for m in all_rx.finditer(mapping_text)})
    leaked_by: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        if r.leaked and r.replacement:
            for m in all_rx.finditer(r.replacement):
                leaked_by[r.occ.entity_id][m.group(0).casefold()] += 1
    residual: dict[str, int] = {e.entity_id: 0 for e in gold.entities}
    for tok, n in hits.items():
        own = owners.get(tok, set())
        for eid in own:
            if len(own) == 1:
                residual[eid] += n
            else:
                explained = sum(leaked_by[o][tok] for o in own if o != eid)
                residual[eid] += max(0, n - explained)

    # distractor preservation (diagnostic)
    in_text = {rel: decoded_text(input_dir / rel) for rel in gold.files}
    d_total = d_kept = 0
    for d in gold.distractors:
        rx = re.compile(r"(?<![A-Za-z0-9])" + re.escape(d["value"]) + r"(?![A-Za-z0-9])")
        n_in = sum(len(rx.findall(t)) for t in in_text.values())
        n_out = sum(len(rx.findall(t)) for t in out_text.values())
        d_total += n_in
        d_kept += min(n_in, n_out)

    # ---------------------------------------------------------------- aggregate
    by_entity: dict[str, list[OccResult]] = defaultdict(list)
    for r in results:
        by_entity[r.occ.entity_id].append(r)
    dominant: dict[str, str | None] = {}
    for eid in ent_by_id:
        votes = Counter(r.persona for r in by_entity[eid] if r.changed and not r.leaked and r.persona)
        dominant[eid] = votes.most_common(1)[0][0] if votes else None
    persona_users: dict[str, set[str]] = defaultdict(set)
    for r in results:
        if r.persona and r.changed and not r.leaked:
            persona_users[r.persona].add(r.occ.entity_id)

    er_ok, cc_ok, collisions = {}, {}, {}
    cont_fail: list[dict] = []
    for eid, rs in by_entity.items():
        er_ok[eid] = not any(r.leaked for r in rs) and residual[eid] == 0
        replaced = [r for r in rs if r.changed and not r.leaked]
        p = dominant[eid]
        shared = p is not None and len(persona_users[p]) > 1
        collisions[eid] = shared
        ok = bool(replaced) and p is not None and not shared and all(r.persona == p and r.form_ok for r in replaced)
        cc_ok[eid] = ok
        if not ok:
            cont_fail.append({"entity": eid, "personas": dict(Counter(str(r.persona) for r in replaced)),
                              "bad_form": sum(1 for r in replaced if r.persona == p and not r.form_ok),
                              "shared_with": sorted(persona_users[p] - {eid}) if p else [],
                              "examples": [[r.occ.surface, r.occ.value, r.replacement] for r in replaced
                                           if r.persona != p or not r.form_ok][:4]})
    n_ent = len(by_entity) or 1
    tp = sum(1 for r in results if r.changed)
    comps = {
        "entity_recall": sum(er_ok.values()) / n_ent,
        "continuity": sum(cc_ok.values()) / n_ent,
        "precision": tp / (tp + fp) if (tp + fp) else 1.0,
        "integrity": 1.0 - len(integrity_fail) / integrity_checks if integrity_checks else 1.0,
    }
    text_preservation = preserved_chars / literal_chars if literal_chars else 1.0
    gate_schema = not schema_errors
    gate_links = not link_fail
    gate_text = text_preservation >= TEXT_FLOOR
    gate = gate_schema and gate_links and gate_text
    hm = harmonic_mean([comps[k] for k in PRIMARY])
    score = hm if gate else 0.0
    bc_p, bc_r = _bcubed(results)

    n_occ = len(results) or 1
    struct = [r for r in results if r.occ.span is None]
    text = [r for r in results if r.occ.span is not None]
    replaced_all = [r for r in results if r.changed and not r.leaked]
    surf: dict[str, list[OccResult]] = defaultdict(list)
    for r in results:
        surf[r.occ.surface].append(r)
    diag = {
        "grader_version": GRADER_VERSION,
        "harmonic_mean": hm,
        "score_v1": hm if gate_schema else 0.0,
        "min_component": min(comps.values()) if gate else 0.0,
        "solved": gate and all(v >= 1.0 - 1e-9 for v in comps.values()),
        "gate_schema": gate_schema,
        "gate_links": gate_links,
        "gate_text": gate_text,
        "occurrence_recall": sum(not r.leaked for r in results) / n_occ,
        "occurrence_recall_structured": sum(not r.leaked for r in struct) / len(struct) if struct else None,
        "occurrence_recall_free_text": sum(not r.leaked for r in text) / len(text) if text else None,
        "separation": 1.0 - sum(collisions.values()) / n_ent,
        # people for whom nothing leaked AND who became one persona of their own: the per-person view of success
        "entity_success": sum(er_ok[e] and cc_ok[e] for e in by_entity) / n_ent,
        "entities_ok": sorted(e for e in by_entity if er_ok[e] and cc_ok[e]),
        "bcubed_precision": bc_p,
        "bcubed_recall": bc_r,
        "form_accuracy": sum(r.form_ok for r in replaced_all) / len(replaced_all) if replaced_all else 0.0,
        "persona_resolved": sum(r.persona is not None for r in replaced_all) / len(replaced_all) if replaced_all else 0.0,
        "text_preservation": text_preservation,
        "distractor_preservation": d_kept / d_total if d_total else 1.0,
        "schema_valid": gate_schema,
        "n_entities": len(by_entity),
        "n_occurrences": len(results),
        "n_leaked": sum(r.leaked for r in results),
        "n_residual_token_hits": sum(residual.values()),
        "n_overscrub_edits": fp,
        "n_integrity_failures": len(integrity_fail),
        "n_link_failures": len(link_fail),
        "n_schema_errors": len(schema_errors),
        "n_declared_personas": len(declared),
        "mapping_errors": mapping_errors[:25],
        "mapping_leaks": len(mapping_leaks),
        "recall_by_surface": {s: round(sum(not r.leaked for r in rs) / len(rs), 4) for s, rs in sorted(surf.items())},
    }
    failures = {
        "leaks": [[r.occ.occ_id, r.occ.surface, r.occ.value, r.replacement] for r in results if r.leaked][:25],
        "continuity": cont_fail[:25],
        "overscrub": overscrub[:25],
        "integrity": integrity_fail[:25],
        "links": link_fail[:25],
        "schema": schema_errors[:25],
    }
    return Report(score=score, gate=gate, components=comps, diagnostics=diag, failures=failures)


def _bcubed(results: list[OccResult]) -> tuple[float, float]:
    """B-cubed precision (penalizes merges) and recall (penalizes splits) of the replacement clustering.

    Each occurrence's predicted cluster is the persona it resolved to; an occurrence that leaked, was
    left unchanged or did not resolve to a persona is its own singleton cluster.
    """
    if not results:
        return 1.0, 1.0
    labels = []
    for i, r in enumerate(results):
        ok = r.changed and not r.leaked and r.persona is not None
        labels.append((r.occ.entity_id, r.persona if ok else f"_unresolved{i}"))
    by_pred = Counter(p for _, p in labels)
    by_gold = Counter(g for g, _ in labels)
    by_both = Counter(labels)
    prec = sum(by_both[(g, p)] / by_pred[p] for g, p in labels) / len(labels)
    rec = sum(by_both[(g, p)] / by_gold[g] for g, p in labels) / len(labels)
    return prec, rec


def _eval(o: Occurrence, rep: str | None, ent_rx: dict, ent_sub: dict, lex: PersonaLexicon) -> OccResult:
    if rep is None:
        return OccResult(o, None, True, False, None, None, False)
    changed = rep != o.value
    folded = fold(rep)
    # boundary-delimited match, or an original identifier of 4+ characters embedded in a longer string
    # ("QuentinWojcik", "xqwojcik"): both still reveal the person
    leaked = bool(ent_rx[o.entity_id].search(rep)) or any(t in folded for t in ent_sub[o.entity_id])
    cands = lex.lookup(rep) if changed else []
    pids = {pid for pid, _ in cands}
    persona = next(iter(pids)) if len(pids) == 1 else None  # a string shared by two personas proves nothing
    forms = [f for pid, f in cands if pid == persona]
    good = [f for f in forms if FORM_CLASS[f] in o.accept]
    form = (good or forms or [None])[0]
    return OccResult(o, rep, changed, leaked, persona, form, bool(good))


def grade_dirs(task_dir: Path, output_dir: Path) -> Report:
    task_dir = Path(task_dir)
    gold = Gold.load(task_dir / "gold.json")
    return grade(task_dir / "input", output_dir, gold)
