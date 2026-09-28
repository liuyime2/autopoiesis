# Autonomous Trading Agent Design

## Goal

Build a self-evolving autonomous trading agent whose long-term success metric is
real profitability over time.

The first milestone is not a full trading company or multi-agent platform. The
first milestone is a minimal paper-trading agent that can complete the whole
learning loop:

```text
real data -> LLM decision -> Guardian review -> paper execution -> journal -> reflection
```

## Non-Negotiable Boundaries

- Paper/live paths must use real data only.
- Mock, fixture, sample, or fabricated data is allowed only in demo/test mode.
- The LLM can propose and explain trades, but cannot bypass Guardian checks.
- The LLM cannot execute shell commands or mutate code directly.
- Live trading is forbidden until the paper-trading loop is audited.
- Hard risk limits cannot be weakened by the LLM.

## Why Start With A Minimal Agent

The project's core objective is profitability, so the system needs real feedback
as early as possible. A large research platform without execution feedback can
look intelligent while failing to improve trading outcomes. A minimal paper
trading loop makes every future capability measurable against real decisions and
real results.

## Phase 1 Scope

Phase 1 builds one autonomous paper-trading agent with seven components.

### 1. Data Gateway

Fetches real market and account data.

Initial sources:

- Alpaca paper account state.
- Alpaca market clock.
- Alpaca latest quotes or bars for a small allowlist of liquid symbols.

Every data payload must include:

- source;
- fetched timestamp;
- symbol or account identifier;
- mode;
- `is_real=true`.

Paper/live execution must reject missing or non-real data.

### 2. LLM Decision Engine

Calls a large language model and requires structured JSON output.

The decision schema is:

```json
{
  "action": "BUY | SELL | HOLD",
  "symbol": "SPY",
  "confidence": 0.0,
  "target_position_pct": 0.0,
  "reasoning": "short explanation",
  "risk_notes": ["specific risk"],
  "evidence": ["data point used"]
}
```

The LLM receives recent market data, current account state, current positions,
active risk limits, and recent reflection memory.

### 3. Guardian

Guardian is the final authority before execution.

Initial hard limits:

- paper mode only;
- allowlist symbols only;
- max single position size;
- max total exposure;
- max daily loss;
- max trade count per day;
- market must be open;
- account state must be fresh;
- no trade on malformed LLM output.

Guardian returns either:

```json
{
  "approved": true,
  "approved_action": {}
}
```

or:

```json
{
  "approved": false,
  "reason": "specific rejection reason"
}
```

### 4. Paper Executor

Submits approved orders to Alpaca paper only.

Phase 1 supports:

- market buy;
- market sell;
- hold/no-op;
- order status capture.

It must never call a live endpoint.

### 5. Decision Journal

Persists every cycle in append-only JSONL.

Each record includes:

- cycle id;
- timestamp;
- mode;
- data snapshot ids;
- LLM prompt summary;
- raw LLM output;
- parsed decision;
- Guardian result;
- execution result;
- errors, if any.

### 6. Reflector

Runs after market close or on demand.

It reads journal entries and real account/position changes, then writes a compact
reflection memory:

- what worked;
- what failed;
- which assumptions were wrong;
- what to watch next;
- what prompt or soft policy should change.

The Reflector can update memory and soft strategy notes. It cannot change hard
risk limits or code.

### 7. Evolution Policy

Defines what the system may self-improve.

Allowed in Phase 1:

- reflection memory;
- watchlist candidates inside an approved allowlist;
- prompt context;
- soft preferences such as "be more conservative after two losses".

Forbidden in Phase 1:

- changing source code;
- changing hard risk limits;
- enabling live trading;
- adding unapproved symbols;
- bypassing Guardian.

## Data Flow

```text
Scheduler
  -> Data Gateway
  -> LLM Decision Engine
  -> Guardian
  -> Paper Executor
  -> Decision Journal
  -> Reflector
  -> Memory for next cycle
```

## Initial Trading Universe

Start with a small, liquid allowlist:

- `SPY`
- `QQQ`
- `AAPL`
- `MSFT`
- `NVDA`

The allowlist is intentionally small so early failures are easier to inspect.

## Initial Success Metrics

The ultimate metric is profit over time. Phase 1 also tracks operational
readiness metrics:

- percentage of cycles with valid real data;
- percentage of LLM decisions parsed successfully;
- Guardian rejection rate and reasons;
- paper order success rate;
- realized and unrealized PnL;
- max drawdown;
- number of reflection updates generated from real outcomes.

No metric should be reported without the exact data source and time period.

## Error Handling

- Missing real data produces a no-trade cycle.
- Market closed produces a no-trade cycle.
- Invalid LLM JSON produces a no-trade cycle.
- Guardian rejection produces a no-trade cycle.
- Broker error is recorded and does not trigger retry loops without a fresh
  Guardian review.

## Testing Strategy

Tests may use fixtures and stubs, but those tests must run in test mode. The
implementation must prove that test/demo data cannot enter paper/live execution.

Required test categories:

- schema parsing;
- data provenance checks;
- Guardian rejection paths;
- paper/live mode separation;
- journal append behavior;
- reflection memory update behavior.

## Out Of Scope For Phase 1

- live trading;
- multi-agent organization;
- automatic code editing;
- reinforcement learning;
- large data lake;
- complex portfolio optimization;
- web dashboard;
- automatic skill modification.

These are later phases after the minimal paper-trading loop is stable.

## Approval State

This design reflects the approved direction:

- start with the recommended minimal closed loop;
- allow autonomy for paper trading, research, reflection, and soft policy
  evolution;
- preserve hard safety boundaries before any real-money deployment.
