# Claude Code Usage Dashboard

A Streamlit dashboard for monitoring Claude Code token usage across projects, dates, and sessions.

## What it does

Visualizes token usage from two data sources (switchable in the sidebar):

- **Daily token trend** — input vs. output tokens per day
- **Project breakdown** — bar chart + pie chart by project
- **Date × Project heatmap** — spot which project spiked on which day
- **Tool usage** — Bash, Read, Edit, Grep, etc. call counts (session-meta mode only)
- **Session detail table** — sortable, with per-message cache breakdown (JSONL mode)

## Quick start

```bash
uv sync
uv run streamlit run src/claude_usage_dashboard/app.py
```

Open http://localhost:8501 in your browser.

Use the sidebar to select a data source, filter by date range, and select projects.

## Data sources

### session-meta (OAuth API) — default

Claude Code writes session summaries to `~/.claude/usage-data/session-meta/<session-id>.json`.
Each file contains token counts, tool usage, duration, first prompt, and more.
Requires Claude Code with OAuth API enabled.

### JSONL (local estimate)

Reads raw conversation logs from `~/.claude/projects/**/*.jsonl` and estimates token
consumption using calibrated cache weights:

```
weighted_total = input_tokens + output_tokens
               + cache_creation_input_tokens × 0.025
               + cache_read_input_tokens     × 0.0015
```

Scan limits: files modified within the last 30 days, up to 200 files.
Deduplication: per `message.id`, keeping the record with the highest weighted total.

JSONL mode adds a second KPI row showing raw cache token counts and the
effective-vs-raw ratio.

## Project structure

```
src/claude_usage_dashboard/
├── loader.py   # load_sessions() + load_sessions_from_jsonl() → DataFrame
└── app.py      # Streamlit dashboard
```
