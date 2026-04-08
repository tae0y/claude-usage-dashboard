# Claude Code Usage Dashboard

A Streamlit dashboard for monitoring Claude Code token usage across projects, dates, and sessions.

## What it does

Reads session metadata from `~/.claude/usage-data/session-meta/` and visualizes:

- **Daily token trend** — input vs. output tokens per day
- **Project breakdown** — bar chart + pie chart by project
- **Date × Project heatmap** — spot which project spiked on which day
- **Tool usage** — Bash, Read, Edit, Grep, etc. call counts
- **Session detail table** — sortable, with first prompt preview

## Quick start

```bash
uv sync
uv run streamlit run src/claude_usage_dashboard/app.py
```

Open http://localhost:8501 in your browser.

Use the sidebar to filter by `~/.claude` path, date range, and project.

## Project structure

```
src/claude_usage_dashboard/
├── loader.py   # Parse ~/.claude/usage-data/session-meta/*.json → DataFrame
└── app.py      # Streamlit dashboard
```

## Data source

Claude Code writes session summaries to `~/.claude/usage-data/session-meta/<session-id>.json`.
Each file contains token counts, tool usage, duration, first prompt, and more.
