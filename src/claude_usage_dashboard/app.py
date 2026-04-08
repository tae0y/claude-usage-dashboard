"""Claude Code Token Usage Dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

# Allow running via `uv run streamlit run src/claude_usage_dashboard/app.py`
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from claude_usage_dashboard.loader import load_sessions

st.set_page_config(
    page_title="Claude Code Usage Dashboard",
    page_icon="🤖",
    layout="wide",
)


# ── Sidebar ───────────────────────────────────────────────────────────────────

st.sidebar.title("Settings")
claude_dir = st.sidebar.text_input("~/.claude path", value=str(Path.home() / ".claude"))

st.sidebar.markdown("---")

df_all = load_sessions(claude_dir)

if df_all.empty:
    st.error(f"No session data found in `{claude_dir}/usage-data/session-meta/`.")
    st.stop()

# Date range filter
min_date = df_all["date"].min()
max_date = df_all["date"].max()

date_from = st.sidebar.date_input("From", value=min_date, min_value=min_date, max_value=max_date)
date_to = st.sidebar.date_input("To", value=max_date, min_value=min_date, max_value=max_date)

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
col1.metric("Total Tokens", f"{df['total_tokens'].sum():,}")
col2.metric("Input Tokens", f"{df['input_tokens'].sum():,}")
col3.metric("Output Tokens", f"{df['output_tokens'].sum():,}")
col4.metric("Sessions", f"{len(df):,}")
col5.metric("Total Minutes", f"{df['duration_minutes'].sum():,}")

st.markdown("---")


# ── Daily Token Trend ─────────────────────────────────────────────────────────

st.subheader("Daily Token Usage")

daily = (
    df.groupby("date")[["input_tokens", "output_tokens", "total_tokens"]]
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

col_a, col_b = st.columns(2)

with col_a:
    by_project = (
        df.groupby("project_name")["total_tokens"]
        .sum()
        .reset_index()
        .sort_values("total_tokens", ascending=False)
    )
    fig_proj = px.bar(
        by_project,
        x="total_tokens",
        y="project_name",
        orientation="h",
        labels={"total_tokens": "Total Tokens", "project_name": "Project"},
        color="total_tokens",
        color_continuous_scale="Blues",
    )
    fig_proj.update_layout(showlegend=False, height=max(300, len(by_project) * 28 + 60))
    st.plotly_chart(fig_proj, use_container_width=True)

with col_b:
    fig_pie = px.pie(
        by_project,
        values="total_tokens",
        names="project_name",
        hole=0.4,
        title="Share by Project",
    )
    fig_pie.update_layout(height=400)
    st.plotly_chart(fig_pie, use_container_width=True)


# ── Sessions Over Time Heatmap ────────────────────────────────────────────────

st.subheader("Session Count Heatmap (Day × Project)")

pivot = (
    df.groupby(["date", "project_name"])["total_tokens"]
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


# ── Tool Usage ────────────────────────────────────────────────────────────────

st.subheader("Tool Usage Across Sessions")

tool_counts: dict[str, int] = {}
for raw in df["tool_counts"].dropna():
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

display_cols = [
    "date", "project_name", "total_tokens", "input_tokens", "output_tokens",
    "duration_minutes", "user_message_count", "assistant_message_count",
    "files_modified", "lines_added", "lines_removed",
    "git_commits", "tool_errors", "first_prompt",
]
available = [c for c in display_cols if c in df.columns]

sort_col = st.selectbox("Sort by", options=["total_tokens", "date", "duration_minutes"], index=0)
asc = st.checkbox("Ascending", value=False)

show_df = df[available].sort_values(sort_col, ascending=asc).reset_index(drop=True)
# Truncate first_prompt for readability
if "first_prompt" in show_df.columns:
    show_df["first_prompt"] = show_df["first_prompt"].astype(str).str[:80]

st.dataframe(show_df, use_container_width=True, height=400)
