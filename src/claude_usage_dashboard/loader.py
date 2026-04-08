"""Load and parse Claude Code session-meta data from ~/.claude/usage-data/session-meta/
or estimate token usage from ~/.claude/projects/**/*.jsonl."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Calibrated weights for cache token estimation
# Source: refer.jsonl-estimation-history.md (last calibration: Max 5×, daily 61%)
_CC_WEIGHT = 0.025   # cache_creation_input_tokens
_CR_WEIGHT = 0.0015  # cache_read_input_tokens

_MAX_FILES = 200
_MAX_AGE_DAYS = 30


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


def _parse_cache_creation_tokens(usage: dict) -> int:
    """Extract cache_creation_input_tokens, falling back to cache_creation subfields."""
    val = usage.get("cache_creation_input_tokens")
    if val is not None:
        return int(val)
    # Fallback: sum ephemeral_5m + ephemeral_1h
    cc = usage.get("cache_creation") or {}
    return int(cc.get("ephemeral_5m_input_tokens", 0) + cc.get("ephemeral_1h_input_tokens", 0))


def load_sessions_from_jsonl(claude_dir: str | Path = "~/.claude") -> pd.DataFrame:
    """Estimate token usage from ~/.claude/projects/**/*.jsonl.

    - Scans all JSONL files modified within the last 30 days (max 200 files).
    - Only processes records with type == "assistant" that contain usage data.
    - Deduplicates by message.id, keeping the record with the highest weighted_total.
    - Applies calibrated weights for cache tokens.

    weighted_total = input_tokens + output_tokens
                   + cache_creation_input_tokens * cc_weight
                   + cache_read_input_tokens     * cr_weight
    """
    base = Path(claude_dir).expanduser()
    projects_dir = base / "projects"

    if not projects_dir.exists():
        return pd.DataFrame()

    now = datetime.now(tz=timezone.utc)
    cutoff_seconds = _MAX_AGE_DAYS * 86400

    # Collect JSONL files, filter by age, sort newest first
    jsonl_files = [
        f for f in projects_dir.rglob("*.jsonl")
        if (now.timestamp() - f.stat().st_mtime) <= cutoff_seconds
    ]
    jsonl_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    jsonl_files = jsonl_files[:_MAX_FILES]

    # message_id -> best record (highest weighted_total)
    best: dict[str, dict] = {}

    for jsonl_path in jsonl_files:
        project_dir_name = jsonl_path.parent.name  # e.g. "-Users-foo-10-SrcHub-myproject"
        try:
            lines = jsonl_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if record.get("type") != "assistant":
                continue

            message = record.get("message") or {}
            usage = message.get("usage") or {}
            if not usage:
                continue

            message_id = message.get("id")
            if not message_id:
                continue

            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
            cache_creation = _parse_cache_creation_tokens(usage)
            cache_read = int(usage.get("cache_read_input_tokens", 0))

            weighted_total = (
                input_tokens
                + output_tokens
                + cache_creation * _CC_WEIGHT
                + cache_read * _CR_WEIGHT
            )

            existing = best.get(message_id)
            if existing is None or weighted_total > existing["weighted_total"]:
                best[message_id] = {
                    "message_id": message_id,
                    "timestamp": record.get("timestamp"),
                    "session_id": record.get("sessionId"),
                    "project_dir": project_dir_name,
                    "model": message.get("model", ""),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": cache_creation,
                    "cache_read_input_tokens": cache_read,
                    "weighted_total": weighted_total,
                }

    if not best:
        return pd.DataFrame()

    df = pd.DataFrame(best.values())

    # Derive project name from encoded dir name
    df["project_name"] = df["project_dir"].apply(
        lambda s: s.lstrip("-").rsplit("-", 1)[-1] if "-" in s else s
    )

    # Parse timestamps
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["date"] = df["timestamp"].dt.tz_convert("Asia/Seoul").dt.date

    # Numeric columns
    for col in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

    df["weighted_total"] = (
        df["input_tokens"]
        + df["output_tokens"]
        + df["cache_creation_input_tokens"] * _CC_WEIGHT
        + df["cache_read_input_tokens"] * _CR_WEIGHT
    )
    df["total_tokens"] = df["input_tokens"] + df["output_tokens"]

    return df.sort_values("timestamp", ascending=False).reset_index(drop=True)
