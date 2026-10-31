"""Loader for the 20-MAD sample (https://github.com/M3SOulu/MozillaApacheDataset-sample)."""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import pandas as pd

BOT_PATTERNS = {
    "Hadoop QA": re.compile(r"^\s*(?:[-+]1 overall|\|\s*\(x\)|\|\s*Vote\s*\||This message is automatically generated)", re.M),
    "Hudson": re.compile(r"(?:SUCCESS|FAILURE|ABORTED|UNSTABLE)?:?\s*Integrated in ", re.I),
    "ASF GitHub Bot": re.compile(r"\bgit[Hh]ub user \S+ (?:opened|closed|commented)", re.I),
}
BOT_ACCOUNTS = {
    "Hadoop QA": {"username": "hadoopqa", "display_name": "Hadoop QA"},
    "Hudson": {"username": "hudson", "display_name": "Hudson"},
    "ASF GitHub Bot": {"username": "githubbot", "display_name": "ASF GitHub Bot"},
}


def default_data_dir() -> Path:
    env = os.environ.get("PIIBENCH_DATA_DIR")
    base = Path(env) if env else Path(__file__).resolve().parents[3].parent / "dataset"
    return base / "MozillaApacheDataset-sample"


@dataclass
class MadSample:
    root: Path

    def _read(self, name: str, columns: list[str] | None = None) -> pd.DataFrame:
        return pd.read_parquet(self.root / name, columns=columns)

    @cached_property
    def commits(self) -> pd.DataFrame:
        return self._read("commits.parquet")

    @cached_property
    def issues(self) -> pd.DataFrame:
        return self._read("issues.parquet")

    @cached_property
    def comments(self) -> pd.DataFrame:
        return self._read("comments.parquet")

    @cached_property
    def idmerging(self) -> pd.DataFrame:
        return self._read("idmerging.parquet")

    def comment_paragraphs(self, source: str, product: str) -> pd.DataFrame:
        tracker = "jira" if source == "apache" else "bugzilla"
        files = sorted(glob.glob(str(self.root / "nlp" / tracker / source / product / "*_nlcomments.parquet")))
        if not files:
            raise FileNotFoundError(f"no nlcomments for {source}/{product}")
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        return df.sort_values(["comment_id", "paragraph_id"]).reset_index(drop=True)

    def key_to_person(self, source: str) -> dict[tuple[str, str], int]:
        """(type, key_hash) -> merged_id. Keys mapped to several persons resolve to the busiest one."""
        idm = self.idmerging[self.idmerging.source == source]
        idm = idm.sort_values("N", ascending=False).drop_duplicates(["type", "key_hash"])
        return {(t, k): int(m) for t, k, m in zip(idm["type"], idm["key_hash"], idm["merged_id"], strict=True)}

    def ambiguous_keys(self, source: str) -> set[str]:
        idm = self.idmerging[self.idmerging.source == source]
        n = idm.groupby(["type", "key_hash"]).merged_id.nunique()
        return {k for (_, k), c in n.items() if c > 1}


def detect_bots(comments: pd.DataFrame, paragraphs: pd.DataFrame, min_comments: int = 20,
                min_share: float = 0.5) -> dict[str, str]:
    """author_key -> bot name, by message templates (20-MAD's is_bot misses Apache Jira bots)."""
    first = paragraphs.groupby("comment_id").text.apply(lambda s: "\n".join(s.head(3)))
    out: dict[str, str] = {}
    for key, grp in comments.groupby("author_key"):
        if len(grp) < min_comments:
            continue
        texts = first.reindex(grp.comment_id.head(300)).dropna()
        if texts.empty:
            continue
        for bot, rx in BOT_PATTERNS.items():
            share = texts.map(lambda t, rx=rx: bool(rx.search(t))).mean()
            if share >= min_share:
                out[key] = bot
                break
    return out
