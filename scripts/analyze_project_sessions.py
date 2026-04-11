#!/usr/bin/env python3
"""Analyze Claude Code session patterns for a specific project.

Reads ~/.claude/projects/<project-dir>/**/*.jsonl and outputs:
  - Per-session summary (tokens, tools, first prompt)
  - Aggregate tool usage ranking
  - Token distribution statistics
  - Daily activity breakdown

Usage:
    uv run python scripts/analyze_project_sessions.py --project TIL
    uv run python scripts/analyze_project_sessions.py --project TIL --days 60 --format json
    uv run python scripts/analyze_project_sessions.py --project TIL --skill readwise-digest
    uv run python scripts/analyze_project_sessions.py --list-projects
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ── helpers ──────────────────────────────────────────────────────────────────

def _parse_cache_creation(usage: dict) -> int:
    val = usage.get("cache_creation_input_tokens")
    if val is not None:
        return int(val)
    cc = usage.get("cache_creation") or {}
    return int(
        cc.get("ephemeral_5m_input_tokens", 0)
        + cc.get("ephemeral_1h_input_tokens", 0)
    )


def _first_user_text(lines: list[str]) -> str:
    """Return the first non-empty user text from a JSONL session."""
    for line in lines:
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("type") != "user":
            continue
        content = (r.get("message") or {}).get("content") or []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text", "")
            # skip IDE context injections
            if text and not text.strip().startswith("<"):
                return text.strip()[:120]
    return ""


def _find_project_dirs(
    projects_dir: Path, keyword: str
) -> list[Path]:
    """Return project dirs whose encoded name contains keyword (case-insensitive)."""
    kw = keyword.lower()
    return [
        d for d in projects_dir.iterdir()
        if d.is_dir() and kw in d.name.lower()
    ]


# ── core analysis ─────────────────────────────────────────────────────────────

def _session_contains_skill(jsonl_path: Path, skill: str) -> bool:
    """Return True if any line in the JSONL file mentions the given skill name."""
    try:
        text = jsonl_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return skill in text


def analyze(
    claude_dir: Path,
    project_keyword: str,
    days: int,
    skill: str | None = None,
) -> dict:
    """Return analysis dict for a project over the last `days` days.

    If `skill` is given, only sessions whose JSONL file mentions that skill string
    are included in the analysis.
    """
    projects_dir = claude_dir / "projects"
    if not projects_dir.exists():
        print(f"ERROR: {projects_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    project_dirs = _find_project_dirs(projects_dir, project_keyword)
    if not project_dirs:
        print(f"ERROR: No project directory matching '{project_keyword}'", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(tz=timezone.utc)
    cutoff_ts = now.timestamp() - days * 86400

    # Collect JSONL files across all matching dirs
    jsonl_files: list[Path] = []
    for pdir in project_dirs:
        for f in pdir.rglob("*.jsonl"):
            if f.stat().st_mtime < cutoff_ts:
                continue
            if skill and not _session_contains_skill(f, skill):
                continue
            jsonl_files.append(f)

    if not jsonl_files:
        qualifier = f" with skill '{skill}'" if skill else ""
        print(
            f"No JSONL files found for '{project_keyword}'{qualifier} in the last {days} days",
            file=sys.stderr,
        )
        sys.exit(0)

    # session_id → session record
    sessions: dict[str, dict] = {}
    # message dedup
    seen_msg_ids: set[str] = set()
    # global tool counter
    tool_counter: dict[str, int] = defaultdict(int)
    # daily stats
    daily: dict[str, dict] = defaultdict(
        lambda: {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0, "messages": 0}
    )

    for jsonl_path in sorted(jsonl_files, key=lambda f: f.stat().st_mtime):
        try:
            raw_lines = jsonl_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        # Determine session_id from path: top-level file → stem, subagent → parent session
        parts = jsonl_path.relative_to(projects_dir).parts
        # parts[0] = project-dir, parts[1] = session-id or file
        if len(parts) >= 3 and parts[2] == "subagents":
            # subagent file: attribute to parent session
            session_id = parts[1]
            is_subagent = True
        else:
            # top-level session file: stem is session_id
            session_id = jsonl_path.stem
            is_subagent = False

        if session_id not in sessions:
            sessions[session_id] = {
                "session_id": session_id,
                "is_subagent_only": is_subagent,
                "first_timestamp": None,
                "last_timestamp": None,
                "first_prompt": "",
                "model": "",
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_create_tokens": 0,
                "cache_read_tokens": 0,
                "message_count": 0,
                "tools": defaultdict(int),
                "subagent_files": 0,
            }

        sess = sessions[session_id]
        if is_subagent:
            sess["subagent_files"] += 1

        # Extract first prompt from non-subagent files
        if not is_subagent and not sess["first_prompt"]:
            sess["first_prompt"] = _first_user_text(raw_lines)

        for line in raw_lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts_str = record.get("timestamp") or ""
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                ts = None

            if ts:
                if sess["first_timestamp"] is None or ts < sess["first_timestamp"]:
                    sess["first_timestamp"] = ts
                if sess["last_timestamp"] is None or ts > sess["last_timestamp"]:
                    sess["last_timestamp"] = ts

            if record.get("type") != "assistant":
                continue

            msg = record.get("message") or {}
            usage = msg.get("usage") or {}
            if not usage:
                continue

            msg_id = msg.get("id")
            if not msg_id or msg_id in seen_msg_ids:
                continue
            seen_msg_ids.add(msg_id)

            inp = int(usage.get("input_tokens", 0))
            out = int(usage.get("output_tokens", 0))
            cc = _parse_cache_creation(usage)
            cr = int(usage.get("cache_read_input_tokens", 0))

            sess["input_tokens"] += inp
            sess["output_tokens"] += out
            sess["cache_create_tokens"] += cc
            sess["cache_read_tokens"] += cr
            sess["message_count"] += 1
            if not sess["model"] and msg.get("model"):
                sess["model"] = msg["model"]

            # Tool calls in this assistant message
            content = msg.get("content") or []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "unknown")
                    sess["tools"][name] += 1
                    tool_counter[name] += 1

            # Daily bucket (KST)
            if ts:
                kst_day = (ts + timedelta(hours=9)).strftime("%Y-%m-%d")
                d = daily[kst_day]
                d["input"] += inp
                d["output"] += out
                d["cache_create"] += cc
                d["cache_read"] += cr
                d["messages"] += 1

    # Convert tool dicts to plain dicts for JSON serialisation
    for sess in sessions.values():
        sess["tools"] = dict(sess["tools"])
        sess["first_timestamp"] = (
            sess["first_timestamp"].isoformat() if sess["first_timestamp"] else None
        )
        sess["last_timestamp"] = (
            sess["last_timestamp"].isoformat() if sess["last_timestamp"] else None
        )

    return {
        "meta": {
            "project_keyword": project_keyword,
            "matched_dirs": [str(d.name) for d in project_dirs],
            "days": days,
            "skill_filter": skill,
            "total_sessions": len(sessions),
            "total_messages": len(seen_msg_ids),
            "jsonl_files_scanned": len(jsonl_files),
        },
        "sessions": list(sessions.values()),
        "tool_ranking": sorted(tool_counter.items(), key=lambda x: x[1], reverse=True),
        "daily": dict(daily),
    }


# ── formatters ────────────────────────────────────────────────────────────────

def _fmt_number(n: int) -> str:
    return f"{n:>10,}"


def print_text(data: dict) -> None:
    meta = data["meta"]
    sessions = data["sessions"]

    # ── Meta ──────────────────────────────────────────────────────────────────
    print("=" * 70)
    print(f"  PROJECT ANALYSIS: {meta['project_keyword'].upper()}")
    print(f"  Dirs   : {', '.join(meta['matched_dirs'])}")
    print(f"  Period : last {meta['days']} days")
    if meta.get("skill_filter"):
        print(f"  Skill  : {meta['skill_filter']}")
    print(f"  Files  : {meta['jsonl_files_scanned']} JSONL scanned")
    print(f"  Sessions: {meta['total_sessions']}  Messages: {meta['total_messages']}")
    print("=" * 70)

    # ── Aggregate token totals ────────────────────────────────────────────────
    total_inp = sum(s["input_tokens"] for s in sessions)
    total_out = sum(s["output_tokens"] for s in sessions)
    total_cc = sum(s["cache_create_tokens"] for s in sessions)
    total_cr = sum(s["cache_read_tokens"] for s in sessions)
    cr_ratio = total_cr / (total_inp + total_cc + 1) * 100

    print("\n── TOTALS ──────────────────────────────────────────────────────────")
    print(f"  Input tokens       : {_fmt_number(total_inp)}")
    print(f"  Output tokens      : {_fmt_number(total_out)}")
    print(f"  Cache create tokens: {_fmt_number(total_cc)}")
    print(f"  Cache read tokens  : {_fmt_number(total_cr)}")
    print(f"  Cache read ratio   :    {cr_ratio:>8.0f}%  (cr / (input+cc))")

    # ── Daily breakdown ───────────────────────────────────────────────────────
    daily = data["daily"]
    if daily:
        print("\n── DAILY (KST) ─────────────────────────────────────────────────────")
        print(f"  {'Date':<12} {'Input':>8} {'Output':>9} {'CacheCreate':>12} {'CacheRead':>12} {'Msgs':>5} {'CR%':>7}")
        for day in sorted(daily.keys(), reverse=True):
            d = daily[day]
            r = d["cache_read"] / (d["input"] + d["cache_create"] + 1) * 100
            print(
                f"  {day:<12} {d['input']:>8,} {d['output']:>9,} "
                f"{d['cache_create']:>12,} {d['cache_read']:>12,} "
                f"{d['messages']:>5} {r:>6.0f}%"
            )

    # ── Tool ranking ─────────────────────────────────────────────────────────
    print("\n── TOOL RANKING ────────────────────────────────────────────────────")
    for rank, (tool, cnt) in enumerate(data["tool_ranking"][:25], 1):
        bar = "█" * min(cnt // 5, 40)
        print(f"  {rank:>2}. {tool:<45} {cnt:>5}  {bar}")

    # ── Session details ───────────────────────────────────────────────────────
    print("\n── SESSION DETAILS (sorted by output tokens desc) ──────────────────")
    header = f"  {'Session ID':<38} {'Date':<12} {'In':>7} {'Out':>8} {'CCr':>8} {'CRd':>9} {'Msgs':>5} {'Sub':>4}  First prompt"
    print(header)
    print("  " + "-" * (len(header) - 2))

    sorted_sessions = sorted(sessions, key=lambda s: s["output_tokens"], reverse=True)
    for s in sorted_sessions:
        date_str = ""
        if s["first_timestamp"]:
            try:
                dt = datetime.fromisoformat(s["first_timestamp"])
                date_str = (dt + timedelta(hours=9)).strftime("%Y-%m-%d")
            except ValueError:
                pass

        prompt_preview = (s["first_prompt"] or "")[:60].replace("\n", " ")
        sub = s["subagent_files"]
        print(
            f"  {s['session_id']:<38} {date_str:<12} "
            f"{s['input_tokens']:>7,} {s['output_tokens']:>8,} "
            f"{s['cache_create_tokens']:>8,} {s['cache_read_tokens']:>9,} "
            f"{s['message_count']:>5} {sub:>4}  {prompt_preview}"
        )

    # ── Token distribution ────────────────────────────────────────────────────
    if sessions:
        out_vals = sorted(s["output_tokens"] for s in sessions if s["output_tokens"] > 0)
        if out_vals:
            n = len(out_vals)
            p50 = out_vals[n // 2]
            p90 = out_vals[int(n * 0.9)]
            p99 = out_vals[min(int(n * 0.99), n - 1)]
            print("\n── OUTPUT TOKEN DISTRIBUTION (per session) ─────────────────────────")
            print(f"  min={out_vals[0]:,}  p50={p50:,}  p90={p90:,}  p99={p99:,}  max={out_vals[-1]:,}")

        # sessions by subagent count
        sub_sessions = [(s["session_id"][:36], s["subagent_files"], s["output_tokens"]) for s in sessions if s["subagent_files"] > 0]
        if sub_sessions:
            print("\n── SESSIONS WITH SUBAGENTS ─────────────────────────────────────────")
            for sid, sub_cnt, out in sorted(sub_sessions, key=lambda x: x[1], reverse=True)[:10]:
                print(f"  {sid:<38} subagents={sub_cnt}  output={out:,}")

    print()


# ── list projects ─────────────────────────────────────────────────────────────

def list_projects(claude_dir: Path) -> None:
    projects_dir = claude_dir / "projects"
    dirs = sorted(d.name for d in projects_dir.iterdir() if d.is_dir())
    for d in dirs:
        # human-readable short name
        short = d.lstrip("-").rsplit("-", 1)[-1] if "-" in d else d
        file_count = len(list((projects_dir / d).rglob("*.jsonl")))
        print(f"  {short:<35} {d}  ({file_count} jsonl)")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze Claude Code session patterns for a specific project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project", "-p",
        default="TIL",
        help="Keyword to match against project directory names (default: TIL)",
    )
    parser.add_argument(
        "--days", "-d",
        type=int,
        default=30,
        help="Look back N days (default: 30)",
    )
    parser.add_argument(
        "--format", "-f",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--claude-dir",
        default="~/.claude",
        help="Path to ~/.claude directory (default: ~/.claude)",
    )
    parser.add_argument(
        "--skill", "-s",
        default=None,
        help="Filter to sessions that mention this skill name (e.g. readwise-digest)",
    )
    parser.add_argument(
        "--list-projects",
        action="store_true",
        help="List all available project directories and exit",
    )

    args = parser.parse_args()
    claude_dir = Path(args.claude_dir).expanduser()

    if args.list_projects:
        list_projects(claude_dir)
        return

    data = analyze(claude_dir, args.project, args.days, skill=args.skill)

    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print_text(data)


if __name__ == "__main__":
    main()
