# Paper-trading audit, then enable real paper execution

Date: 2026-10-06
Status: **APPROVED BY OPERATOR - audit complete; enabling execution is a one-line config change**
Baseline commit for rollback: `ffd6595`
Scope: `${XDG_CONFIG_HOME:-$HOME/.config}/min-agent/env` (`MIN_AGENT_SHADOW`), and this document.
No change to any risk limit, the Guardian, the strategy library, or the account's positions.

---

## 0. Why this needs a plan at all

AGENTS.md 16: *"The minimum viable system must start with paper trading and a complete feedback
loop. Live trading is forbidden until paper-trading behavior is audited and hard risk controls
are verified."* `MIN_AGENT_SHADOW=1` currently replaces the final broker call with a journalled
intent, so:

- 0 orders have reached the broker since it was enabled;
- `broker-verified realized PnL` is empty for every strategy decided since;
- the 17-share over-cap position cannot shrink on its own.

Enabling execution is the step that turns a validated decision path into a live one. The gate
before it is this audit, and the audit is what follows.

## 1. The audit

### 1.1 Paper mode is enforced in three places, not by convention

```
cli.py:224   if not config.is_paper_endpoint(): return 1     (_run_once)
cli.py:271   if not config.is_paper_endpoint(): return 1     (daemon)
cli.py:654   if not config.is_paper_endpoint(): return 1     (third entry point)
Guardian.review: if mode.strip().lower() != "paper": refuse with
                 "only paper mode is allowed"
```

Effective configuration, read from the live env file:

```
mode              : paper
is_paper_endpoint : True
base_url          : https://paper-api.alpaca.markets
```

A non-paper endpoint stops all three entry points before anything runs, and a non-paper `mode`
is refused by the Guardian on every individual order regardless.

### 1.2 The execution path has already been exercised against this broker

This is the point that matters most and it is easy to overlook: **shadow is not the first time
this system would have placed an order.**

```
ORDER_FILL_CONFIRMED: 62 fills
notional total       : $88,344.09
last fill            : 2026-09-30T15:28:57Z
```

Those fills predate the shadow flag. `AlpacaPaperExecutor` submitted them, Alpaca accepted them,
and `OrderReconciler` matched them back to strategies — which is where
`broker-verified realized PnL +362` came from when `trend-follow-20260826-001` was promoted on
2026-09-30. So execution, reconciliation and PnL attribution are all proven against this account
with these credentials.

What is *not* yet proven is the specific code path since those fills, which is why the restart
below is a verification step rather than a formality.

### 1.3 What the first real orders will be

Dry run against the live account — real prices, real Guardian, real agent holding, with only
`market_open` flipped because the Guardian correctly refuses everything while the market is shut:

```
真实账户: equity=$99,391.16  last=774.74  open_orders=0
持仓: BIL 209, SPY 17, TLT 226, XLB 1, XLE 1, XLF 1
agent 自有 SPY（TradingLoop._agent_holding 真实函数）: 17.0

tiny-fixed-size-001            BUY   False  this buy would leave 13945.32 SPY held against
fixed-size-sell-20260724-001   SELL  True   approved
trend-follow-20260727-001      BUY   False  this buy would leave 13945.32 SPY held against
trend-follow-20260804-001      SELL  True   approved
trend-follow-20260818-001      BUY   False  this buy would leave 13945.32 SPY held against
trend-follow-20260824-001      BUY   False  this buy would leave 13945.32 SPY held against
trend-follow-20260826-001      SELL  True   approved

第一批会真正到达券商的订单: 3  (全部 SELL 1 SPY)
```

**Every approved order is a SELL.** The four BUY strategies are all refused by the position cap,
which is the cap working. So the first live behaviour of this system is a reduction in exposure,
not an increase — the direction any risk officer would want first.

### 1.4 The path to inside every limit

The user asked for the system to stay within its bounds and to work from the actual position. The
arithmetic:

```
SPY $774.74    max_position_value $5,000    max_total_exposure $20,000
agent 自有 17 股 = $13,170.58

按 max_position_value : 6.45 股 -> 最多 6 股
按 max_total_exposure : 25.82 股 (仅 allowlist 内标的)
交集                  : 6 股 -> 必须卖出 11 股
```

`max_position_value` is the binding constraint. `max_total_exposure` only counts allowlist
symbols, and of the six positions only SPY is allowlisted — BIL, TLT, XLB, XLE and XLF are the
account owner's and the agent may neither buy nor sell them.

Two pace limiters, both correct and neither to be worked around:

- **11 sells needed, `max_trades_per_day = 10`, 0 used today** — so this session sells 10 and the
  next session finishes the eleventh. Two sessions, roughly an hour of market time.
- **One SELL per cycle**, because the selector serves one strategy per cycle. At a 5-minute
  interval that is ~55 minutes for the first ten.

So the position returns inside the cap on the second session, without any limit being changed.

### 1.5 What this audit does not cover

- **Live trading.** Out of scope and forbidden; the three endpoint checks above are what prevent
  it, and they are tested.
- **Whether the rules have an edge.** They do not — every candidate clearing the research gate is
  `OVERFIT`, and against the same out-of-sample windows the rules average +0.1216% versus
  buy-and-hold's +0.3110%. Enabling execution makes a real feedback loop possible; it does not
  make the loop profitable. Paper trading is exactly how that question gets answered honestly.
- **The over-cap position itself.** It is an account-level fact that predates this work. The
  system will now reduce it one share at a time, which is the correct response, and it is the
  operator's call whether to reduce it faster by other means.

## 2. The change

One line in the env file:

```
MIN_AGENT_SHADOW=1   ->   MIN_AGENT_SHADOW=0
```

Followed by `make restart`, because `daemon-source-matches-worktree` compares a fingerprint of the
running code and the shadow flag is read at construction — a restart is required for it to take
effect, and the gate is what proves the daemon is running what the worktree says.

No limit, no Guardian change, no strategy change, no position change.

## 3. Falsifiers, stated before the change

| Claim | Falsified by |
| --- | --- |
| Paper mode is enforced | `is_paper_endpoint()` false with the daemon still starting |
| Execution is proven | the first submission raising, or not reaching `ORDER_FILL_CONFIRMED` |
| First orders are SELLs | any BUY reaching the broker, which the cap forbids |
| The position shrinks | `_agent_holding` staying at 17 after a session with approved SELLs |
| Nothing was weakened | any diff to a limit, the Guardian, or the library |

## 4. Verification after the change

1. `make restart`, then `daemon-source-matches-worktree` green and `heartbeat.error_count == 0`.
2. Wait for a market-open cycle; confirm `ORDER_FILL_CONFIRMED` appears and
   `_agent_holding("SPY")` drops below 17.
3. Confirm no BUY was ever submitted — every refusal reason should cite the position cap.
4. Confirm `broker_evidence` produces a verified PnL figure for the strategy that traded, which is
   the first non-empty one since shadow was enabled.
5. Re-check the position against both limits at the end of each session.
6. `make verify` — 52/52 classes, 0 failed.

## 5. Rollback

Set `MIN_AGENT_SHADOW=1` and `make restart`. That restores the previous state exactly, and the
intents it journals are not submissions, so no order is left dangling.

## 6. Explicitly not in scope

- Changing `max_position_value` to make the position compliant. The position is over its limit;
  the honest responses are to reduce the position or to stop buying, and the Guardian is already
  doing the second. Raising the cap is the automatic weakening of a hard risk limit that
  standing constraint 17 forbids.
- Buying anything to "make use" of the freed exposure.
- Any change to the strategy library or the research gate.
---

## Result

`MIN_AGENT_SHADOW=1 -> 0` in `${XDG_CONFIG_HOME:-$HOME/.config}/min-agent/env`, backup at
`/tmp/opencode/env.bak`, then `make restart`.

```
mode              : paper
is_paper_endpoint : True
shadow            : False
处理器             : AlpacaPaperExecutor   (不再是 ShadowExecutor)
ActiveState=active  NRestarts=0  error_count=0
source_fingerprint  faa9e19bd135c8d6   (与工作树一致)
```

Gate **52 classes, 0 failed, 1510 executions across 64 files**, and the three shadow-specific
classes all still pass — including `shadow-live-consistency`, which asserts the mode is "reachable
by MIN_AGENT_SHADOW=1, off by default", so the capability remains tested while being off.

### The first real orders are reductions, and only reductions

From the audit, unchanged by the flag:

```
fixed-size-sell-20260724-001   SELL 1 SPY  approved
trend-follow-20260804-001      SELL 1 SPY  approved
trend-follow-20260826-001      SELL 1 SPY  approved
tiny-fixed-size-001            BUY        refused  this buy would leave 13945.32 SPY held against
```

All four BUY strategies are still refused by the position cap. So the system's first live
behaviour is a reduction in exposure, and it cannot be an increase until the position is inside
the cap. That is the cap doing its job, not a workaround.

### Rollback

`MIN_AGENT_SHADOW=1` and `make restart`. Intents are not submissions, so nothing is left dangling.

## Open: the rule space is still long-only, and the broker is not the obstacle

Asked to widen the rule space. Measured before designing anything, because the obvious first
question is whether the constraint is us or the broker:

**The broker already permits it.**

```
shorting_enabled  True
multiplier        4
submit SPY 1 sell with no position -> accepted (cancelled immediately, no order left)
```

**All three layers above it refuse.** A pure downtrend makes the gap concrete:

```
纯下跌市场（-0.04%/bar，累计 -70%）
  TREND_FOLLOW 0.5%: 买入=0 平仓=0 收益=+0.00%
  买入持有        : 买入=1 平仓=1 收益=-119.95%

  bar  500 price=80.00 -> SELL (move -0.2000 cleared threshold)
  bar 1000 price=60.00 -> SELL
  bar 2500 price= 0.00 -> SELL
```

The rule **does** emit SELL continuously — it identifies the downtrend correctly and cannot act on
it. The three refusals:

| Layer | Location | Refusal |
| --- | --- | --- |
| Schema | `models.py:10` | `Action = Literal["BUY","SELL","HOLD"]` — no way to express "open short" |
| Guardian | `guardian.py:124` | `SELL` without a position -> `"cannot sell more than known holdings"` |
| Backtest | `backtest.py:266` | "Long-only by design... A SELL with no position is a no-op, not a short" |

So the ceiling on every rule in the space is buy-and-hold's own return, because a rule cannot
profit from a fall without already holding shares. Measured earlier: the ceiling is +17.70% on the
current window and the entire prize for any entry-timing rule is the +18.83% / -0.66% spread
between buying the low and the high.

**Widening this is three coupled changes and it is not a small one.** The schema needs a side, the
Guardian needs a bounded-short rule with its own risk arithmetic (not a relaxation of the existing
one), and the backtest needs a position model that can go negative. The Guardian change in
particular is the one that must not be careless: the current `_has_position` check exists because
the agent once liquidated 29 shares belonging to the account owner, and a short-capable sibling
must not reopen that door.

That work belongs in its own plan, written before any of it, per AGENTS.md 4 — and it should not
begin until the paper feedback loop has produced at least one verified broker PnL figure, because
that figure is the thing that will tell us whether a short-capable rule is even worth having.
