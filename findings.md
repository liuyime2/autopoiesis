# Findings & Decisions

## Requirements
- Review all current work/code under `/localscratch/liuyime2/QuantGroup`.
- Explain what each project/code area does.
- Identify characteristics and notable risks.
- Use truthful data only; do not invent capabilities, benchmark numbers, or results.

## Research Findings
- QuantGroup root contains `history_version/`.
- `history_version/` contains: `CortexHive`, `Janus`, `PosSearch`, `QuantAgent`, `edict-main`, `edict-run`.
- CodeGraph is not initialized at QuantGroup root.
- No `AGENTS.md` was found in QuantGroup root during initial scan.
- Directory sizes: `CortexHive` 16M, `Janus` 1.7G, `PosSearch` 36M, `QuantAgent` 2.7G, `edict-main` 47M, `edict-run` 24M.
- Git roots: `CortexHive`, `Janus`, and `QuantAgent` are git repositories. `PosSearch`, `edict-main`, and `edict-run` are not root-level git repositories.
- Dirty status line counts: `CortexHive` 5, `Janus` 272, `QuantAgent` 4332.
- `CortexHive` README describes a hybrid intelligence quantitative trading platform with CEO, Strategy, Risk, Portfolio, and Execution agents; code confirms Redis blackboard, mixed LLM router, FastAPI services, and Alpaca integration.
- `Janus` README describes an AI trading company / learning platform with transparent agent debates; code confirms a CLI launcher with manual, managed, and backtest modes plus FastAPI dashboard endpoints.
- `PosSearch` README describes an intelligent job recommendation system using Groq LLM; code confirms pipeline orchestration for scraping jobs, loading candidates, matching, generating outreach emails, saving results, and scheduling.
- `QuantAgent` metadata describes `quant-agent` version `2.0.0`, an "Omni-Channel Autonomous Quantitative Trade Engine"; source includes agents, API, execution, monitoring, Telegram interface, scheduler, and workflows.
- `edict-main` README describes 三省六部 Edict, a 12-agent AI collaboration/dashboard architecture with institutional review, Kanban, model switching, skills management, news, and audit trails.
- `edict-main` contains both a stdlib `dashboard/server.py` local API and a newer `edict/backend` FastAPI/Postgres/Redis backend plus `edict/frontend` React/Vite frontend.
- `edict-run` contains only `venv/`; `pyvenv.cfg` identifies Python 3.13.2 and a creation command for `/localscratch/liuyime2/QuantGroup/edict_run/venv`.
- Multiple projects contain `.env`, logs, PID files, caches, and runtime data. Secret-bearing `.env` files were not read.
- PosSearch and edict-run file counts are inflated by vendored/virtual environment files; primary PosSearch source files are the root Python modules, while edict-run is environment-only.
- QuantAgent has a known risk already recorded previously: `src/execution/online_loop.py` forces market-open behavior with `return True` before real calendar checking.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Review each `history_version/*` directory separately | They appear to be distinct projects or historical snapshots. |
| Use README and build metadata first | These are the most reliable project-level intent sources. |
| Use source tree and entry points to validate README claims | Avoid relying only on descriptions. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| CodeGraph unavailable | Use filesystem discovery and direct source inspection. |

## Resources
- `/localscratch/liuyime2/QuantGroup/history_version`
- `history_version/CortexHive/README.md`
- `history_version/Janus/README.md`
- `history_version/PosSearch/README.md`
- `history_version/QuantAgent/pyproject.toml`
- `history_version/edict-main/README.md`

## Visual/Browser Findings
- None.
