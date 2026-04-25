"""Claude Code Token Usage Dashboard."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

# Allow running via `uv run streamlit run src/claude_usage_dashboard/app.py`
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from claude_usage_dashboard.loader import load_sessions, load_sessions_from_jsonl, export_raw_token_data

st.set_page_config(
    page_title="Claude Code Usage Dashboard",
    page_icon="🤖",
    layout="wide",
)


# ── Sidebar ───────────────────────────────────────────────────────────────────

st.sidebar.title("Settings")
claude_dir = st.sidebar.text_input("~/.claude path", value=str(Path.home() / ".claude"))
data_source = st.sidebar.radio(
    "Data source",
    options=["JSONL (local estimate)", "session-meta (OAuth API)"],
    index=0,
)

st.sidebar.markdown("---")

# Export raw token data as a downloadable file
@st.cache_data(show_spinner="Scanning JSONL files…")
def _cached_export(claude_dir: str) -> tuple[bytes, int]:
    return export_raw_token_data(claude_dir)

_export_data, _export_count = _cached_export(claude_dir)
_filename = f"claude-raw-tokens-{date.today()}.jsonl"
st.sidebar.download_button(
    label=f"Download raw token data ({_export_count:,} records)",
    data=_export_data,
    file_name=_filename,
    mime="application/x-ndjson",
    disabled=_export_count == 0,
)

st.sidebar.markdown("---")

if data_source == "JSONL (local estimate)":
    df_all = load_sessions_from_jsonl(claude_dir)
    _token_col = "weighted_total"
    if df_all.empty:
        st.error(f"No JSONL data found in `{claude_dir}/projects/`.")
        st.stop()
else:
    df_all = load_sessions(claude_dir)
    _token_col = "total_tokens"
    if df_all.empty:
        st.error(f"No session data found in `{claude_dir}/usage-data/session-meta/`.")
        st.stop()

# Date range filter — default to current month 1st ~ today
_today = date.today()
_month_start = _today.replace(day=1)
min_date = df_all["date"].min()
max_date = df_all["date"].max()

_default_from = max(min_date, _month_start)
_default_to = min(max_date, _today)

date_from = st.sidebar.date_input("From", value=_default_from, min_value=min_date, max_value=max_date)
date_to = st.sidebar.date_input("To", value=_default_to, min_value=min_date, max_value=max_date)

# Project filter
all_projects = sorted(df_all["project_name"].unique())
selected_projects = st.sidebar.multiselect("Projects", options=all_projects, default=all_projects)

# Apply filters
mask = (
    (df_all["date"] >= date_from)
    & (df_all["date"] <= date_to)
    & (df_all["project_name"].isin(selected_projects))
)
df = df_all[mask].copy()

if df.empty:
    st.warning("No sessions match the current filter.")
    st.stop()


# ── Header ────────────────────────────────────────────────────────────────────

st.title("🤖 Claude Code Usage Dashboard")
st.caption(f"Loaded **{len(df):,}** sessions · {date_from} → {date_to}")


# ── KPI Row ───────────────────────────────────────────────────────────────────

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Weighted Total" if _token_col == "weighted_total" else "Total Tokens",
            f"{df[_token_col].sum():,.1f}" if _token_col == "weighted_total" else f"{df[_token_col].sum():,}")
col2.metric("Input Tokens", f"{df['input_tokens'].sum():,}")
col3.metric("Output Tokens", f"{df['output_tokens'].sum():,}")
col4.metric("Sessions" if _token_col == "total_tokens" else "Messages", f"{len(df):,}")
col5.metric("Total Minutes", f"{df['duration_minutes'].sum():,}" if "duration_minutes" in df.columns else "N/A")

if _token_col == "weighted_total":
    c1, c2, c3 = st.columns(3)
    c1.metric("Cache Creation Tokens", f"{df['cache_creation_input_tokens'].sum():,}")
    c2.metric("Cache Read Tokens", f"{df['cache_read_input_tokens'].sum():,}")
    c3.metric(
        "Effective vs Raw ratio",
        f"{df[_token_col].sum() / max(df['total_tokens'].sum(), 1):.2f}×",
        help="weighted_total / (input+output). Shows cache contribution factor.",
    )

st.markdown("---")


# ── Daily Token Trend ─────────────────────────────────────────────────────────

st.subheader("Daily Token Usage")

_daily_cols = ["input_tokens", "output_tokens", _token_col]
_daily_cols = list(dict.fromkeys(_daily_cols))  # dedupe while preserving order
daily = (
    df.groupby("date")[[c for c in _daily_cols if c in df.columns]]
    .sum()
    .reset_index()
    .sort_values("date")
)
daily["date"] = pd.to_datetime(daily["date"])

fig_daily = px.bar(
    daily,
    x="date",
    y=["input_tokens", "output_tokens"],
    labels={"value": "Tokens", "variable": "Type", "date": "Date"},
    barmode="stack",
    color_discrete_map={"input_tokens": "#4C78A8", "output_tokens": "#F28E2B"},
)
fig_daily.update_layout(legend_title_text="Token Type", height=300)
st.plotly_chart(fig_daily, use_container_width=True)


# ── Project Breakdown ─────────────────────────────────────────────────────────

st.subheader("Token Usage by Project")

by_project = (
    df.groupby("project_name")[_token_col]
    .sum()
    .reset_index()
    .sort_values(_token_col, ascending=False)
)
fig_proj = px.bar(
    by_project,
    x=_token_col,
    y="project_name",
    orientation="h",
    labels={_token_col: "Tokens", "project_name": "Project"},
    color=_token_col,
    color_continuous_scale="Blues",
)
fig_proj.update_layout(showlegend=False, height=max(300, len(by_project) * 28 + 60))
st.plotly_chart(fig_proj, use_container_width=True)

fig_pie = px.pie(
    by_project,
    values=_token_col,
    names="project_name",
    hole=0.4,
    title="Share by Project",
)
fig_pie.update_layout(height=400)
st.plotly_chart(fig_pie, use_container_width=True)


# ── Sessions Over Time Heatmap ────────────────────────────────────────────────

st.subheader("Session Count Heatmap (Day × Project)")

pivot = (
    df.groupby(["date", "project_name"])[_token_col]
    .sum()
    .unstack(fill_value=0)
)
if not pivot.empty:
    fig_heat = px.imshow(
        pivot.T,
        labels={"x": "Date", "y": "Project", "color": "Tokens"},
        aspect="auto",
        color_continuous_scale="YlOrRd",
    )
    fig_heat.update_layout(height=max(300, len(pivot.columns) * 30 + 80))
    st.plotly_chart(fig_heat, use_container_width=True)


# ── Weekday × Hour Heatmap ────────────────────────────────────────────────────

st.subheader("Usage by Weekday & Hour (KST)")

_ts_col = "timestamp" if "timestamp" in df.columns else ("start_time" if "start_time" in df.columns else None)
if _ts_col:
    _ts = pd.to_datetime(df[_ts_col], utc=True, errors="coerce").dt.tz_convert("Asia/Seoul")
    _wday_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    _wday_map = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
    _heat_df = df[[_token_col]].copy()
    _heat_df["weekday"] = _ts.dt.dayofweek.map(_wday_map)
    _heat_df["slot"] = _ts.dt.hour * 6 + _ts.dt.minute // 10  # 0–143
    _heat_df = _heat_df.dropna(subset=["weekday", "slot"])
    _heat_df["slot"] = _heat_df["slot"].astype(int)

    # x=10-min slot (06:00 ~ 05:50), y=weekday (Mon at top)
    _all_slots = list(range(144))
    _slot_order = list(range(6 * 6, 144)) + list(range(0, 6 * 6))  # start at 06:00
    _pivot_wh = (
        _heat_df.groupby(["weekday", "slot"])[_token_col]
        .sum()
        .unstack(fill_value=0)
        .reindex(index=_wday_order)
    )
    _pivot_wh = _pivot_wh.reindex(columns=_all_slots, fill_value=0).iloc[:, _slot_order]

    # x-axis labels: show HH:00 at :00 slots, empty otherwise
    _slot_labels = [
        f"{s // 6:02d}:00" if s % 6 == 0 else ""
        for s in _slot_order
    ]

    _fig_wh = px.imshow(
        _pivot_wh,
        labels={"x": "Hour (KST)", "y": "Weekday", "color": "Tokens"},
        x=_slot_labels,
        y=_wday_order,
        aspect="auto",
        color_continuous_scale="YlOrRd",
    )
    _fig_wh.update_layout(height=320)
    st.plotly_chart(_fig_wh, use_container_width=True)
else:
    st.info("Timestamp data not available for weekday/hour heatmap.")


# ── Tool Usage ────────────────────────────────────────────────────────────────

st.subheader("Tool Usage Across Sessions")

tool_counts: dict[str, int] = {}
for raw in df["tool_counts"].dropna() if "tool_counts" in df.columns else []:
    if isinstance(raw, dict):
        for tool, cnt in raw.items():
            tool_counts[tool] = tool_counts.get(tool, 0) + int(cnt)

if tool_counts:
    tool_df = (
        pd.DataFrame(list(tool_counts.items()), columns=["tool", "count"])
        .sort_values("count", ascending=False)
        .head(20)
    )
    fig_tools = px.bar(
        tool_df,
        x="tool",
        y="count",
        labels={"tool": "Tool", "count": "Total Calls"},
        color="count",
        color_continuous_scale="Teal",
    )
    fig_tools.update_layout(showlegend=False, height=320)
    st.plotly_chart(fig_tools, use_container_width=True)


# ── Session Detail Table ──────────────────────────────────────────────────────

st.subheader("Session Details")

if _token_col == "weighted_total":
    display_cols = [
        "date", "project_name", "first_prompt", "model", "weighted_total",
        "input_tokens", "output_tokens",
        "cache_creation_input_tokens", "cache_read_input_tokens",
        "session_id", "message_id",
    ]
else:
    display_cols = [
        "date", "project_name", "total_tokens", "input_tokens", "output_tokens",
        "duration_minutes", "user_message_count", "assistant_message_count",
        "files_modified", "lines_added", "lines_removed",
        "git_commits", "tool_errors", "first_prompt",
    ]
available = [c for c in display_cols if c in df.columns]

_sort_options = [_token_col, "date"] + (["duration_minutes"] if "duration_minutes" in df.columns else [])
sort_col = st.selectbox("Sort by", options=_sort_options, index=0)
asc = st.checkbox("Ascending", value=False)

show_df = df[available].sort_values(sort_col, ascending=asc).reset_index(drop=True)
if "weighted_total" in show_df.columns:
    show_df["weighted_total"] = show_df["weighted_total"].round(0).astype(int)
# Truncate first_prompt for readability
if "first_prompt" in show_df.columns:
    show_df["first_prompt"] = show_df["first_prompt"].astype(str).str[:80]

st.dataframe(show_df, use_container_width=True, height=400)
