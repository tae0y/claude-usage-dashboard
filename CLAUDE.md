# claude-usage-dashboard — Claude Code Guide

Claude API 사용량을 추적하고 시각화하는 대시보드 프로젝트.

---

## Reading Order for New Sessions

When starting a session in this project, read in this order:
1. `CLAUDE.md` (this file) — project overview and communication style
2. `.claude/WORKFLOW.md` — trigger map: what runs when, and how
3. `.claude/rules/` — behavioral constraints applied to every response
4. `.claude/skills/` — domain-specific patterns loaded as background context
5. `localdocs/worklog.doing.md` — active task state (if resuming work)

---

## Approach

- Think before acting. Read existing files before writing code.
- Be concise in output but thorough in reasoning.
- Prefer editing over rewriting whole files.
- Do not re-read files you have already read unless the file may have changed.
- Test your code before declaring done.
- No sycophantic openers or closing fluff.
- Keep solutions simple and direct.
- User instructions always override this file.

---

## Project Context

Claude API 사용 현황(토큰 수, 비용, 모델별 분포 등)을 수집하고 대시보드로 제공하는 Python 프로젝트.

- **패키지**: `src/claude_usage_dashboard/`
- **Python**: 3.12+
- **패키지 관리**: uv
- **테스트**: pytest + coverage (≥80%)
- **품질 도구**: ruff, pyright, bandit, pre-commit

---

## Rule Hierarchy

When rules from different files apply to the same response, use this priority order:

- Non-code responses: `thinking-guidelines` > `CLAUDE.md`
- Code tasks: `coding-guidelines` > `CLAUDE.md`
- When rules conflict: prefer thinking (surface trade-offs, ask) over acting (proceed and fix later)
