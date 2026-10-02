> **SUPERSEDED — historical plan, not current instructions.** This document describes a design that was never built: it references `history_version/*` modules that do not exist in `src/min_agent/`. It is kept as a record of what was considered, and nothing in it is a task to perform. The architecture that actually exists is in `docs/ARCHITECTURE.md`.

# History Systems Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the historical systems under `history_version/` into a clear optimization roadmap for the next QuantGroup system.

**Architecture:** Preserve lessons from the strongest historical components while rejecting demo-only, mock-driven, and unsafe execution paths. The future system should combine Edict-style governance, QuantAgent-style real-data execution boundaries, CortexHive-style event flow, and Janus-style transparent decision records.

**Tech Stack:** Python, FastAPI, Redis Streams, PostgreSQL, Alpaca API, Superpowers skills, markdown planning files.

---

## Current Historical Systems

### CortexHive

**Purpose:** Early Redis Blackboard multi-agent trading platform.

**Main files:**
- `history_version/CortexHive/main.py`
- `history_version/CortexHive/core/blackboard.py`
- `history_version/CortexHive/core/llm_router_enhanced.py`
- `history_version/CortexHive/agents/strategy_agent_optimized.py`
- `history_version/CortexHive/agents/risk_agent.py`
- `history_version/CortexHive/agents/portfolio_agent.py`
- `history_version/CortexHive/agents/execution_agent.py`

**Useful ideas:**
- Central event bus through Redis Streams.
- Clear agent roles: CEO, Strategy, Risk, Portfolio, Execution.
- Separate LLM router with provider selection.
- Control API for pause, resume, risk parameter updates, and status.

**Problems to avoid:**
- Strategy signal generation uses random symbol, direction, and confidence.
- Portfolio sizing uses assumed demo prices instead of market data.
- TCA metrics are placeholders.
- LLM-generated explanations are mixed into operational decision flow.

**Optimization decision:** Keep the Blackboard/event-bus idea and role naming. Do not reuse trading logic without replacing all demo data paths with verified real data.

### Janus

**Purpose:** Transparent “AI trading company” prototype focused on explainability and agent debate.

**Main files:**
- `history_version/Janus/janus.py`
- `history_version/Janus/core/base_agent.py`
- `history_version/Janus/core/debate_engine.py`
- `history_version/Janus/core/manager_agent.py`
- `history_version/Janus/core/trading_engine.py`
- `history_version/Janus/core/backtest_engine.py`

**Useful ideas:**
- Debate records capture opinions, votes, confidence, reasoning, data used, consensus, and final decision.
- Manager interface makes agent state understandable to a user.
- Manual, managed, and backtest modes are separated conceptually.

**Problems to avoid:**
- Some modules reference undefined `MessageType` and `MessagePriority`.
- Backtest metrics include incomplete or incorrect logic, such as win rate based only on sell count.
- Multiple features are described but left as pass statements or unfinished markers.
- Some release-status language is stronger than the verified implementation.

**Optimization decision:** Reuse the debate/audit schema concept. Rebuild execution and backtest paths from tested, real-data components.

### QuantAgent

**Purpose:** Largest and most relevant historical trading system. Current trunk lives under `history_version/QuantAgent/src/`; legacy contains AgentV1, Meridians, StockSearch, and ValueCell.

**Main files:**
- `history_version/QuantAgent/src/workflows/meridians_loop.py`
- `history_version/QuantAgent/src/api/main.py`
- `history_version/QuantAgent/src/execution/alpaca_client.py`
- `history_version/QuantAgent/src/execution/online_loop.py`
- `history_version/QuantAgent/src/agents/guardian.py`
- `history_version/QuantAgent/src/agents/brain.py`
- `history_version/QuantAgent/src/scheduler/daily_scheduler.py`

**Useful ideas:**
- `AlpacaClient` establishes a strict API boundary and rejects invalid credentials in live-required mode.
- `Guardian` encodes hard risk red lines that LLMs cannot override.
- Live mode requires explicit human confirmations.
- The system separates scheduler, execution, monitoring, data collectors, and interfaces.
- Meridians legacy contains useful research ideas: alpha factory, causal filters, meta-RL, risk management, shadow trading, and daily review.

**Problems to avoid:**
- `OnlineLoop._is_market_open()` currently returns `True` before the real market calendar check.
- Several modules still use mock/fallback paths, including scraper, sentiment, backtest, diagnostics, and some evolution logic.
- `Brain` persists to hardcoded absolute paths that do not match the current repository path.
- Some imports point to modules that are not present in the current `src/` tree.
- Runtime data, logs, model weights, databases, and large embedded external projects are mixed into history.

**Optimization decision:** Treat QuantAgent as the primary source for real-data boundaries and risk controls, but audit every fallback path before reuse.

### PosSearch

**Purpose:** Non-quant recruitment recommendation pipeline.

**Main files:**
- `history_version/PosSearch/main.py`
- `history_version/PosSearch/resume_parser.py`
- `history_version/PosSearch/job_scraper.py`
- `history_version/PosSearch/job_matcher.py`
- `history_version/PosSearch/data_manager.py`

**Useful ideas:**
- Clean linear pipeline: parse, scrape, match, generate email, store, schedule.
- Small components with obvious responsibilities.
- CSV storage is simple and easy to inspect for MVP workflows.

**Problems to avoid:**
- Sample candidate fallback is used when no candidate data exists.
- LLM is asked to generate fake embeddings as arrays of numbers.
- Web scraping selectors are brittle.
- Main scheduler loop calls `asyncio.sleep(1)` without awaiting it.

**Optimization decision:** Reuse pipeline decomposition style only. Do not reuse data-quality or scraping assumptions for trading.

### Edict

**Purpose:** Three Departments and Six Ministries governance system for multi-agent task orchestration.

**Main files:**
- `history_version/edict-main/docs/task-dispatch-architecture.md`
- `history_version/edict-main/scripts/kanban_update.py`
- `history_version/edict-main/scripts/file_lock.py`
- `history_version/edict-main/dashboard/server.py`
- `history_version/edict-main/edict/backend/app/main.py`
- `history_version/edict-main/edict/backend/app/workers/dispatch_worker.py`
- `history_version/edict-main/edict/backend/app/services/agent_runner.py`

**Useful ideas:**
- Institutional workflow: intake, planning, review, dispatch, execution, final report.
- Menxia review creates a required quality gate before execution.
- Permission matrix limits which agent can call which other agent.
- Kanban, task logs, flow logs, progress logs, and intervention controls improve observability.
- File locking and atomic JSON writes prevent data loss in concurrent workflows.
- Redis Stream dispatch worker points toward crash-recoverable orchestration.

**Problems to avoid:**
- `agent_runner.py` executes LLM output with `shell=True`.
- Some dashboard/server logic is very large and should be split.
- Demo scripts and production orchestration live close together.
- JSON file storage is useful for MVP but should migrate to structured DB for durable production state.

**Optimization decision:** Reuse governance, auditability, and state-machine ideas. Replace shell execution with structured, whitelisted tool calls.

## Future System Principles

### Principle 1: Governance Before Execution

No agent should move directly from idea to trade. Every trade-affecting proposal must pass:

1. Research evidence collection.
2. Strategy proposal.
3. Risk review.
4. Execution planning.
5. Final audit record.

### Principle 2: Real Data Is a Type Boundary

Production code must not silently downgrade to mock data. A data object should carry source metadata:

```python
{
    "source": "alpaca",
    "fetched_at": "2026-06-03T00:00:00Z",
    "symbol": "SPY",
    "is_real": true,
    "data": {}
}
```

Any `is_real=false` data must be blocked from paper/live trading paths.

### Principle 3: LLMs Explain and Propose, They Do Not Override Controls

LLMs can:
- summarize evidence;
- propose portfolio weights;
- explain risk tradeoffs;
- suggest parameter changes.

LLMs cannot:
- bypass market-open checks;
- bypass drawdown limits;
- bypass position limits;
- fabricate missing market data;
- execute shell commands.

### Principle 4: Keep Demo, Test, Paper, and Live Modes Separate

Each mode must have explicit allowed data sources:

| Mode | Allowed data | Allowed execution |
| --- | --- | --- |
| demo | fixture data only | no broker call |
| test | test fixtures and controlled stubs | no broker call |
| paper | real market/account data from paper broker | paper broker only |
| live | real market/account data from live broker | live broker after explicit confirmations |

### Principle 5: Every Decision Must Be Replayable

A decision record should include:
- task or cycle id;
- timestamp;
- input data source metadata;
- agent opinions;
- risk checks;
- proposed action;
- final approved action;
- execution result;
- post-trade review link.

## Proposed Target Architecture

```text
User / Scheduler
  -> Edict Governance Layer
  -> Research Agents
  -> Strategy Agent
  -> Debate / Review Layer
  -> Guardian Risk Layer
  -> Execution Planner
  -> Broker Adapter
  -> Audit Log / Review / Optimization
```

### Core Components

**Governance Layer**
- Based on Edict state machine.
- Owns task intake, planning, review, dispatch, intervention, and final reports.

**Data Layer**
- Fetches real market, macro, news, sentiment, and portfolio data.
- Stores source metadata and freshness.
- Rejects unverified data in trading modes.

**Strategy Layer**
- Produces structured proposals, not direct orders.
- Includes assumptions, confidence, evidence, and alternatives.

**Guardian Layer**
- Enforces hard red lines.
- Has final blocking authority.
- Produces structured rejection feedback.

**Execution Layer**
- Converts approved target state into broker orders.
- Checks market status, liquidity, order type, slippage limits, and reconciliation.

**Audit Layer**
- Records every decision and every state transition.
- Makes post-mortem and optimization possible.

## Reuse / Rewrite / Reject Matrix

| Source | Reuse | Rewrite | Reject |
| --- | --- | --- | --- |
| CortexHive | Redis event bus, role split, control API shape | Strategy, portfolio sizing, TCA | random trading signal path |
| Janus | debate records, transparent dashboard concepts | manager/backend integration | incomplete production claims |
| QuantAgent | Alpaca boundary, Guardian red lines, live confirmations | Brain persistence, scheduler imports, backtest | forced market-open override |
| PosSearch | small pipeline decomposition | storage and scheduler robustness | fake embeddings and sample data fallback |
| Edict | governance state machine, review gate, task logs, file lock | shell execution, monolithic server | LLM-generated shell execution |

## Implementation Tasks

### Task 1: Create Architecture Decision Record

**Files:**
- Create: `docs/adr/0001-future-quant-system-architecture.md`

- [ ] **Step 1: Create ADR directory**

Run:

```bash
mkdir -p docs/adr
```

Expected: `docs/adr` exists.

- [ ] **Step 2: Write ADR**

Create `docs/adr/0001-future-quant-system-architecture.md` with:

```markdown
# ADR 0001: Future Quant System Architecture

## Status

Proposed

## Context

The historical systems under `history_version/` contain useful ideas but also mix demo logic, mock data, runtime artifacts, and incomplete production paths.

## Decision

Build the future system around an Edict-style governance layer, QuantAgent-style real-data and risk boundaries, CortexHive-style event flow, and Janus-style transparent decision records.

## Consequences

- Every trade-affecting proposal passes review and Guardian checks.
- Mock data is blocked from paper/live paths.
- LLMs can propose and explain but cannot execute or override controls.
- All decisions are replayable.
```

- [ ] **Step 3: Review ADR for unsafe claims**

Read the ADR and verify it does not claim unverified performance, certain
outcomes, or live-trading readiness.

Expected: no unsupported claim remains.

### Task 2: Define Trading Mode Contract

**Files:**
- Create: `docs/contracts/trading_modes.md`

- [ ] **Step 1: Create contracts directory**

Run:

```bash
mkdir -p docs/contracts
```

Expected: `docs/contracts` exists.

- [ ] **Step 2: Write trading mode contract**

Create `docs/contracts/trading_modes.md` with:

```markdown
# Trading Mode Contract

## Modes

| Mode | Data Source | Broker Access | Mock Allowed |
| --- | --- | --- | --- |
| demo | fixtures | none | yes |
| test | fixtures and controlled stubs | none | yes |
| paper | real paper account and market APIs | paper only | no |
| live | real live account and market APIs | live only | no |

## Blocking Rules

- Any `is_real=false` input blocks paper and live execution.
- Missing market status blocks paper and live execution.
- Missing account state blocks paper and live execution.
- Guardian rejection blocks paper and live execution.
```

- [ ] **Step 3: Verify no mock allowance leaks into paper/live**

Run:

```bash
grep -n "paper.*yes\\|live.*yes" docs/contracts/trading_modes.md
```

Expected: no output.

### Task 3: Inventory Reuse Candidates

**Files:**
- Create: `docs/inventory/history_reuse_matrix.md`

- [ ] **Step 1: Create inventory directory**

Run:

```bash
mkdir -p docs/inventory
```

Expected: `docs/inventory` exists.

- [ ] **Step 2: Write reuse matrix**

Create `docs/inventory/history_reuse_matrix.md` with:

```markdown
# Historical Reuse Matrix

| System | Keep | Rewrite | Do Not Reuse |
| --- | --- | --- | --- |
| CortexHive | Redis Streams Blackboard, agent role split, control API shape | strategy and portfolio logic | random signals, assumed prices, placeholder TCA |
| Janus | debate records and explainable decision UI | manager integration and backtest | incomplete message types and unverifiable performance claims |
| QuantAgent | AlpacaClient, Guardian red lines, live confirmations | Brain persistence, scheduler wiring, mode separation | forced market-open override and live-path mock fallback |
| PosSearch | small pipeline module boundaries | scheduler and storage | fake embeddings and sample-candidate fallback |
| Edict | state machine, review gate, task logs, permission matrix | command execution and monolithic dashboard server | LLM-generated shell command execution |
```

- [ ] **Step 3: Verify all systems are represented**

Run:

```bash
for name in CortexHive Janus QuantAgent PosSearch Edict; do grep -q "$name" docs/inventory/history_reuse_matrix.md || exit 1; done
```

Expected: exit code 0.

### Task 4: Add Safety Audit Checklist

**Files:**
- Create: `docs/checklists/safety_audit.md`

- [ ] **Step 1: Create checklist directory**

Run:

```bash
mkdir -p docs/checklists
```

Expected: `docs/checklists` exists.

- [ ] **Step 2: Write audit checklist**

Create `docs/checklists/safety_audit.md` with:

```markdown
# Safety Audit Checklist

- [ ] Paper/live path has no mock data fallback.
- [ ] Market-open status comes from a real broker or verified calendar source.
- [ ] Account state comes from broker API.
- [ ] Position state is reconciled before order submission.
- [ ] Guardian checks run before every order batch.
- [ ] Guardian rejection cannot be overridden by LLM.
- [ ] LLM output is parsed as structured data, not executed as shell.
- [ ] All order submissions include mode, source data ids, and audit id.
- [ ] All decision records include input evidence and final execution outcome.
- [ ] Backtest results are labeled with data source, period, fees, slippage, and exact command.
```

- [ ] **Step 3: Verify checklist coverage**

Run:

```bash
grep -n "mock\\|market-open\\|Guardian\\|LLM\\|Backtest" docs/checklists/safety_audit.md
```

Expected: at least five matching lines.

## Immediate Risk Register

| Risk | Evidence | Required action |
| --- | --- | --- |
| Forced market-open behavior | `history_version/QuantAgent/src/execution/online_loop.py` returns `True` before calendar check | Block live/paper use until removed and tested |
| Mock fallback in trading-adjacent modules | QuantAgent scraper, sentiment, diagnostics, backtest contain mock/fallback paths | Add mode contract and enforce at boundaries |
| LLM command execution | Edict `agent_runner.py` executes code block lines with `shell=True` | Replace with whitelisted structured tools |
| Runtime artifacts in repo | logs, model weights, DB files, zip archives, build output under history | Separate source, artifacts, data, external vendors |
| Large monolithic files | AgentV1 engine and Edict dashboard server exceed maintainable size | Split by responsibility before reuse |

## Completion Criteria

- The future architecture ADR exists.
- The trading mode contract exists.
- The historical reuse matrix exists.
- The safety audit checklist exists.
- No document claims unverified performance.
- No implementation work starts before these docs are reviewed.

## Execution Options

1. Subagent-Driven: dispatch separate agents for ADR, contracts, inventory, and checklist.
2. Inline Execution: implement each document in this session with review after every file.
