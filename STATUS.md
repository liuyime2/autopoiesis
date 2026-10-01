# QuantGroup — status

Last updated: 2026-10-01. This file replaces `LEARNING_STATUS.md`,
`learning_daemon_summary.md`, `findings.md`, `progress.md` and `task_plan.md`,
all of which described the system as running when it had been dead for 97 days.

## Where things stand

`minictrl doctor` is the source of truth. Run it; do not trust this file over it.

```
./minictrl doctor
```

Ollama is serving `qwen3.8:27b` on `:11434`, both LLM paths are
verified live against the real model, and the credentials work. The credentials
live in `$XDG_CONFIG_HOME/min-agent/env` (mode 600, outside the repository) — the
earlier "credentials FAIL" in this file was stale. With the stack up, `doctor`
reports **RESULT OK with 8 warnings**, all of them evidence still being gathered rather than
anything broken - unmanaged exposure, risk-driven halts, an incomplete experiment chain,
unscored LLM decisions, model attribution, and the fallback and champion figures. Run
`minictrl doctor` for the current list; the count moves as cycles accumulate.

| Check | State |
|---|---|
| alpaca credentials | **PASS** — present in the external env file; paper endpoint reachable, market OPEN |
| daemon | PASS — `min-agent.service` up, pid alive, heartbeat fresh |
| proof: submitted>0 | **PASS** — 44 orders submitted, 95.0 shares filled (44 broker-confirmed) |
| proof: filled>0 | **PASS** — 75 shares filled, 24 broker-confirmed, `fill_quantity_ratio` 1.0 |
| proof: per-strategy pnl | **PASS** — 3 strategies, broker-verified: `tiny-fixed-size-001` +362.66, `fixed-size-buy-001` +120.37, `trend-follow-buy-001` +81.36 |
| pnl evidence | **PASS** — `broker_strategy_closed_lot_pnl_verified`, 23 closed lots, 0 open |
| journal | OK — 56 lines live plus rotated generations, 1048 cycles total across all of them |
| strategy library | OK — 40 total, 13 selectable, including one SELL strategy |

### The self-evolution loop now runs, and it closed a position

This is the substantive change since the last update. Curriculum had been on
deterministic fallback for the entire audit — the loop was dead while every health
check reported green. It now runs end to end against the real model, and the
result was a real trade:

```
18:15:18 SUCCESS  source=llm  trend-follow-sell-002   →  ACCEPTED into PROBATION
19:01:04 SELL 52  trend-follow-sell-002                →  Guardian approved
19:12:12 FILLED  52 @ 766.57                            →  broker-confirmed
```

The model closed its own capability gap. It proposed `fixed-size-sell-002` with
`action=SELL` after the engine stated which action the library could not express,
and the daemon admitted `trend-follow-sell-002`, after which
`capability_coverage` reported `uncovered_actions_now: []`.

The cross-strategy attribution that made this work is the point: the 23 lots were
opened by three *different* strategies across June, and `trend-follow-sell-002`
closed all of them. The realized PnL is attributed to the opener, and the closing
strategy is recorded separately. Total broker-verified realized PnL: **+564.39**.

### What is still missing, and why

One criterion remains: **a lifecycle transition driven by PnL.** The mechanism is
implemented and tested — broker-verified PnL now both retires a strategy on a loss
and, once probation's cycle bar is met, promotes one on a gain, with the figure
recorded in the transition reason. It has not fired in this session because all
three strategies carrying verified PnL are already `ACTIVE` or `RETIRED`, and the
agent has just sold its last agent-owned share, so nothing is in `PROBATION` with a
closed lot behind it. It needs a new round trip, not another fix.

## What was wrong, and what is fixed

Full detail, with reproduction evidence, in
[`docs/superpowers/plans/2026-09-28-quantgroup-recovery-and-refactor.md`](docs/superpowers/plans/2026-09-28-quantgroup-recovery-and-refactor.md).

Fifteen defects were confirmed against the live code and the 16.9 MB journal.
The ones that made the system structurally incapable of profiting:

- **Positions were never read.** `alpaca-trade-api 3.2.0` has no
  `get_all_positions` / `get_orders`; both calls raised `AttributeError` and
  were swallowed into `()`. All 851 recorded cycles carry an empty position
  book, so every SELL was rejected and there was no aggregate exposure cap. A
  second latent bug sat in the same path: Alpaca returns order `side` lowercase
  while the model requires uppercase, so open orders were being dropped too.
- **The reward function paid the maximum score for doing nothing**, and the
  selector was a deterministic `argmax` on it. The system evolved toward HOLD
  and locked itself there: 800 of 851 recorded cycles were HOLD.
- **Risk limits were auto-raised outside Guardian.** `auto-fix.sh` rewrote
  `max_position_value` upward and force-re-enabled strategies the lifecycle
  manager had retired, bypassing admission — forbidden by AGENTS.md §6.
- **A dead daemon reported healthy for 97 days** and passed 112 consecutive
  automated reviews, because `--status` replayed a file and always exited 0.
- **No fill feedback.** Orders filled; the journal recorded `filled_quantity
  0.0`; per-strategy PnL was therefore never computable.
- **The LLM made no trade decisions at all** in daemon mode, and the curriculum
  prompt contained a hard-coded example price that the model copied into nine
  admitted strategies.

All fifteen are fixed, and so were nine more found by a full trace of the
SELL → closed-lot → per-strategy-PnL path. 800 tests pass, and
`tools/audit_defects.py` passes 61/61 across the 15 original defects. The nine:

- **The lot ledger was per-strategy.** The selector returns one strategy per cycle,
  so the strategy that opened a lot and the strategy that closed it were always
  different, and a SELL could only match lots in its own tracker. All four SELL
  decisions this agent ever made closed nothing, so `strategy_realized_pnl` was
  empty and could never be otherwise. Lots are now account-level, matched FIFO per
  symbol, attributed to the strategy that *opened* each lot.
- **The PnL evidence window was 24h** while the lots were opened weeks earlier, so
  a SELL inside the window matched nothing. `confirmed_fill_activities()` rebuilds
  broker-confirmed fills from the journal with no time limit.
- **Verified PnL was dropped before it could be used.** `if strategy_id in buckets`
  discarded it for any strategy without a cycle in the current window — computed,
  reported, then thrown away with no log line. The lifecycle manager's `cycles > 0`
  gate then ran before the PnL rule, making the only PnL-driven transition in the
  system unreachable for exactly the strategies that had real PnL.
- **`max_position_value` was applied to SELL.** A cap on new exposure silently
  rewrote an exit to HOLD, or truncated it. Now BUY-gated, matching Guardian.
- **`Guardian.min_confidence` was a hardcoded 0.5 that admission never checked**,
  so a strategy could be admitted that could never place an order.
- **The curriculum could not encode a conditional schema.** ollama's constrained
  decoder honours a flat `required` list and `enum` but ignores `const` and
  `additionalProperties` inside a `oneOf`; a `oneOf` schema was accepted in 6.4s
  and then ignored. Generation is now two flat constrained calls.
- **The model had to invent `created_at`.** A strategy admitted today came back
  with `created_at 2024-06-14`, and that field orders probation. The system owns
  timestamps now.
- **The decision context omitted the position's cost and the account's exposure.**
  `PositionSnapshot` carries only quantity and market_value, so the model was asked
  to decide about a position it had no way to evaluate, with no idea that every BUY
  was already impossible. It held for 26 consecutive cycles and bought 49 times,
  rejected 49 times.
- **No LLM decision was ever attributed to a strategy.** All 908 cycles carried
  `strategy_id=None`, so per-strategy metrics could not accumulate for the LLM path
  at all.

A tenth fix, found by running `--ingest-evidence`: a 30-day window spans 31
calendar dates, so Alpaca rejects the `15Min` timeframe and portfolio history came
back empty. Verified against the live API before changing anything.

## What is still required

1. ~~Rotate the Alpaca paper credentials~~ — **CLOSED BY OPERATOR DECISION, 2026-09-30.**
   The key pair was pasted into an earlier session and is in the transcript, so it was
   flagged as a standing exposure. The operator reviewed it and decided rotation is
   not required, on the grounds that this is a **paper** trading account whose purpose
   is testing the Alpaca integration.

   That is a decision about the operator's own paper credentials, and it is theirs to
   make. Recorded here rather than deleted so the item's history is visible: the
   concern was real, it was raised, and it was answered. Anyone reading this later
   should know the key is knowingly retained, not overlooked.

   One consequence is worth stating: because the key is knowingly retained and is in
   the transcript, this account must never be promoted to live trading with these
   credentials. That constraint is now on the record rather than implied.

2. **One more round trip.** Per-strategy PnL is proven; a lifecycle transition
   *driven by* it is not yet, because nothing is in `PROBATION` with a closed lot
   behind it. See "What is still missing, and why" above. **This needs market hours,
   not another fix** — it is the one remaining item on this list and no code change
   can close it. It resolves when the market opens and a probation strategy completes
   a round trip, and the system is correctly refusing to promote anything before then.

3. ~~Reboot-persistent units~~ — **RESOLVED, and this entry was stale.** It
   previously said the units sit under `/run/user/$UID/systemd/user` and do not
   survive a reboot "because $HOME is out of quota". That was the state before the
   unit-location defect was found and fixed. The truth now:

   ```
   min-agent.service       /home/liuyime2/.config/systemd/user/min-agent.service
   ollama.service          /home/liuyime2/.config/systemd/user/ollama.service
   quant-watchdog.timer    /home/liuyime2/.config/systemd/user/quant-watchdog.timer
   links into tmpfs: 0
   ```

   `$HOME` is no longer at quota, the units load from a durable path, no enablement
   link points into tmpfs, and the `units-where-systemd-looks` gate fails the build if
   either regresses. The quota was the cause after all — the earlier claim that "the
   quota was never the blocker" was wrong, and this entry is the stale remnant of that
   error.

4. ~~Ollama after a reboot~~ — **RESOLVED.** The port mismatch recorded in
   `ollama.log` (`:11435`, `CUDA_VISIBLE_DEVICES=3`) was resolved earlier; the unit
   is now installed durably alongside the others and will come back on reboot. It
   serves `qwen3.8:27b` on `127.0.0.1:11434`, resident on GPU 0, verified against the
   real model. The `deepseek-r1:8b` text below predates the single-model change.

   ```bash
   OLLAMA_MODELS=/localscratch/liuyime2/ollama_local/models \
   OLLAMA_HOST=127.0.0.1:11434 CUDA_VISIBLE_DEVICES=0 \
     /localscratch/liuyime2/ollama_local/bin/ollama serve
   ```

## The only success criterion

A complete paper session in which, read from the journal:

- `submitted > 0` — **met**, 24 broker-confirmed
- **and** `filled > 0` — **met**, 75 shares, `fill_quantity_ratio` 1.0
- **and** per-strategy PnL attribution is non-empty and broker-derived — **met**,
  3 strategies, +564.39 total, all from closed lots
- **and** at least one lifecycle transition was driven by that PnL — **not yet**,
  see above

Three of the four are met by real broker evidence. Cycle counts, green review
files, and "the daemon is running" are **not** evidence. `minictrl doctor` prints
these as `proof: submitted>0`, `proof: filled>0` and `proof: per-strategy pnl` for
exactly that reason.

---

## What is NOT working, stated plainly

> Dated section, written 2026-09-28. Each item below was true then and the causes
> named in each were real bugs, since fixed. For whether any of them still holds, run
> `minictrl doctor` - the file says so at the top and that instruction is the current
> one. This block is kept because each bug it describes was found the expensive way,
> and a reader who cannot see the failure is likely to reintroduce it.

**The agent has never closed a lot, so per-strategy PnL is still empty.** The
account holds 23 SPY bought in June. Four SELL decisions were attempted across the
whole history and all four were rejected; the causes were real bugs, now fixed, but
no SELL has reached the broker since. The agent has also bought 49 times and been
rejected 49 times on `total exposure exceeds hard limit` — the account carries
~$76k of mostly non-allowlist positions against a $20k cap, so **every BUY is
structurally impossible** until those are reduced. That limit has not been touched.

**The LLM holds, and mostly for reasons that are its own.** 26 consecutive
`llm`-sourced HOLD cycles before the recent BUY, with `hold_reason` of
`market_uncertain` or `no_signal`. The context has since been corrected to carry
cost basis, exposure, the hard limits and the selected strategy's mandate — the
model previously had none of these and could not have reasoned otherwise. What it
does with them is its own judgment, and it is not something to tune.

**The strategy library is accumulating near-duplicates.** 23 strategies, 10
selectable, 7 in PROBATION. The probation queue is served oldest-first, so a newly
proposed SELL strategy is 6th in line behind five BUY strategies. This is fair
rotation, not a bug, but it means a new capability takes roughly 90 minutes of
5-minute cycles to be exercised.

**The systemd units are now reboot-persistent.** They live in
`/home/liuyime2/.config/systemd/user`, which exists and holds the enablement
symlinks; `min-agent.service`, `quant-watchdog.service` and `quant-watchdog.timer`
are all `enabled` with FragmentPath under that durable directory. This paragraph used
to say the opposite - that they were confined to `/run/user/$UID` and needed `$HOME`
freed - and it contradicted the RESOLVED entry above it. Corrected here, in the same
document that made the claim, so the two can no longer disagree.

**The key pair was pasted into a conversation and should be rotated** in the
Alpaca UI.

## 2026-09-28 — verification gate and the two missing loop stages

> Dated section. The figures below are what the gate reported on 2026-09-28 and are
> correct for that date. For the current figures see `README.md`; the gate now runs
> 43 check classes. The sections below are a record of what was true when each was
> written, not a description of the present.

**`make verify` is the single gate.** 17 check classes, 0 failed, 472 distinct
tests, 91/91 defect audit, `doctor` RESULT OK, exit 0. Per-class re-runs:
`make verify CLASS=pnl-accounting`. `make verify-self-test` breaks the gate on
purpose and asserts it reports failure, because a verification command that cannot
fail is believed rather than run.

**Offline validation now exists, and the obvious implementation would have been
fake.** A candidate used to go from admission straight into PROBATION and trade
paper orders. Replaying the strategy's own rule against history would have graded a
rule the system never executes — `parameters["action"]` is never checked on the
execution path and TREND_FOLLOW's `threshold_pct` never is either; the strategy is
context handed to the model. The screen instead reads the counterfactual verdicts of
the decisions the strategy actually produced. It can only reject, never promote.
Live: 4 screened through, 2 rejected, 10 inconclusive. Both rejected strategies were
already PAUSED by the lifecycle manager, so the screen agreed independently.

**Hold quality is 0.687 over 350 scored decisions, after cost** — 224 good holds, 95
missed alpha, 24 neutral, 7 false trades, 67 pending, 50 refused as gaps. An earlier
figure of 0.295 was an artifact of a cycle-counted horizon spanning 3 minutes to 97
days; the horizon is wall-clock now and repeated quotes are collapsed.

**The experiment registry is a view, not a store.** Every link of the chain is
already journalled, so a persisted registry would be a second source of truth. It
derives the per-strategy chain and writes nothing. 35 strategies, 23 admitted, 15
with an incomplete chain — all but one historical, the live one a brand-new PROBATION
awaiting its first evaluation window.

**Six strategies were admitted and have no registry file and no recorded
retirement.** `runtime/` is gitignored so the cause cannot be reconstructed; it is
recorded as UNKNOWN in `docs/superpowers/known_state_findings.json` and no synthetic
retirement was written. It cannot recur — `admit()` writes the file atomically
before returning `accepted=True`.

**The shadow *mechanism* is implemented; the shadow *stage* has never run.** The
sink is real and switchable by `MIN_AGENT_SHADOW`, and `shadow-not-executed`
proves no module counts a shadow order as a fill. But the journal holds **zero**
`SHADOWED` executions, the flag is off, and the single `SHADOW_ORDER_INTENT` carries
the rationale `"shadow stage smoke test"`. `PHASES.md` previously recorded Phase 5 as
MET quoting `status=SHADOWED` and a `920/920` cycle count that the live journal does
not contain; that row is corrected, and a `shadow-stage-exercised` gate now reports
mechanism and stage separately so the exit criterion cannot be read off code that has
never run. The wiring point: shadow is a global executor swap rather than an automatic
per-strategy stage, so progression to paper is a manual flag flip. It is **not** a
dead end - shadow decisions are scored by the counterfactual ledger and do feed the
promotion gate, since offline validation reads verdicts rather than fills. Only the
separate `submitted_orders > 0` precondition needs real orders, which is the gate's
purpose. The objective does not require automatic progression, so this is a design
observation rather than a defect; the earlier "dead end" claim in `SYSTEM_AUDIT.md`
§45 was wrong and has been withdrawn.

Two earlier notes on shadow are **withdrawn as wrong**, and are kept here because a
withdrawn claim left standing is worse than one never made:

1. *"Shadow appears only in docstrings and is recorded as missing."* True when
   written, false now. `MIN_AGENT_SHADOW=1` swaps the final broker call and nothing
   else — real data, real model, real Guardian, real journal.
2. *"Shadow is merged into probation, not missing."* The reasoning was sound and the
   conclusion was wrong: the objective names shadow trading as **Phase 5**, separate
   from evidence-gated probation in **Phase 6**, so it is a stage in its own right
   rather than something probation already covers. A shadow executor must still swap
   only the execution sink and never fork the decision path, which was the one
   durable insight in the withdrawn argument.

**The model has contributed 0.00 to the PnL.** All 23 closed lots — the whole
+564.39 — were opened by the `baseline` decision source on 2026-06-11, 06-12 and
06-18. The LLM path's first decision was 2026-09-28 11:20, two and a half months
after the last profitable lot. The headline profit is not evidence that the model
works. Details and the full cause breakdown are in `docs/superpowers/PHASES.md` §4.

**Verified against the real account, not a fixture.** The account holds 5 positions
worth $37,082, all outside the allowlist (BIL, TLT, XLB, XLE, XLF). At that state a
BUY of 5 SPY is APPROVED — the scoping fix works. The $20,000 cap still binds on the
agent's own book: $15,000 held approves, $19,000 held rejects with
`agent exposure 19000.00 + 3827.45 exceeds the 20000.00 limit`. The optional
`max_account_value` backstop also rejects correctly when enabled. The per-position
$5,000 limit is what refuses larger single orders, not the account total.
