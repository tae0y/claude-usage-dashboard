"""Load and parse Claude Code session-meta data from ~/.claude/usage-data/session-meta/."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _project_name(raw_path: str) -> str:
    """Convert encoded project path to a human-readable name.

    e.g. '/Users/foo/-Users-foo-10-SrcHub-myproject' -> 'myproject'
    """
    if not raw_path:
        return "(unknown)"
    return Path(raw_path).name


def load_sessions(claude_dir: str | Path = "~/.claude") -> pd.DataFrame:
    """Read all session-meta JSON files and return a flat DataFrame.

    Each file may be a pretty-printed JSON object (single session).
    Some files contain multiple top-level JSON objects separated by whitespace —
    we handle that by trying multi-object streaming parse as a fallback.
    """
    base = Path(claude_dir).expanduser()
    meta_dir = base / "usage-data" / "session-meta"

    if not meta_dir.exists():
        return pd.DataFrame()

    records: list[dict] = []

    for f in sorted(meta_dir.glob("*.json")):
        text = f.read_text(encoding="utf-8")
        # Fast path: single JSON object
        try:
            obj = json.loads(text)
            records.append(obj)
            continue
        except json.JSONDecodeError:
            pass
        # Fallback: streaming multi-object parse
        decoder = json.JSONDecoder()
        pos = 0
        text = text.strip()
        while pos < len(text):
            try:
                obj, idx = decoder.raw_decode(text, pos)
                if isinstance(obj, dict):
                    records.append(obj)
                pos = idx
                # skip whitespace
                while pos < len(text) and text[pos] in " \t\n\r":
                    pos += 1
            except json.JSONDecodeError:
                break

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)

    # Derive project name
    df["project_name"] = df.get("project_path", pd.Series(dtype=str)).fillna("").apply(_project_name)

    # Parse timestamps
    df["start_time"] = pd.to_datetime(df["start_time"], utc=True, errors="coerce")
    df["date"] = df["start_time"].dt.tz_convert("Asia/Seoul").dt.date

    # Numeric columns — fill missing with 0
    for col in ("input_tokens", "output_tokens", "duration_minutes",
                "user_message_count", "assistant_message_count",
                "lines_added", "lines_removed", "files_modified",
                "git_commits", "tool_errors"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        else:
            df[col] = 0

    df["total_tokens"] = df["input_tokens"] + df["output_tokens"]

    return df.sort_values("start_time", ascending=False).reset_index(drop=True)
