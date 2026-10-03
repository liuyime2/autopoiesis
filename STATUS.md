# QuantGroup — status

Last updated: 2026-10-01. This file replaces `LEARNING_STATUS.md`,
`learning_daemon_summary.md`, `findings.md`, `progress.md` and `task_plan.md`,
all of which described the system as running when it had been dead for 97 days.
| State | Where to read it |
|---|---|
| market open/closed, account, positions | `minictrl status` |
| every health and evidence check | `minictrl doctor` |
| submitted / filled / realized PnL | `minictrl profit`, `minictrl evidence report latest` |
| cycle count, strategy library | `minictrl doctor` |

Live numbers are queried, not written down. A figure that moves while the agent trades
cannot be maintained in a document: an earlier version of this file carried the cycle count
and the order count, and the gate that compared them against `doctor` went red on every
fill - which teaches an operator to ignore the one gate that matters. What this file holds is
what does not change:

| Fixed | Value |
|---|---|
| mode | `paper` - `cli.py` refuses to build a client against any host but Alpaca's paper |
| allowlist | AAPL, MSFT, NVDA, QQQ, SPY |
| hard limits | $5,000 per position, $20,000 total exposure, $500 daily loss, 10 trades/day |
| model | `qwen3.8:27b` on Ollama at `127.0.0.1:11434` |
| credentials | `$XDG_CONFIG_HOME/min-agent/env`, mode 600, outside the repository |
| units | `~/.config/systemd/user/`, four enabled, durable across reboot |

## Open finding: shares sold that no BUY accounts for

`minictrl doctor` reports `UNMATCHED SELLS {'trend-follow-sell-002': 29.0}`: 29 shares were
sold with no linked BUY inside that strategy's lot tracker. The journal covers 2026-06-09
onward and the sells are later than that, so this is not a gap at the start of the record -
the tracker genuinely cannot pair them. Run `minictrl doctor` for the current figure; it moves
as the agent trades.

What it does to the numbers: the headline realized PnL includes part that cannot be
attributed to a strategy, so it is an upper bound rather than an exact result. `minictrl
profit` evaluates the target against the same figures.

Not yet diagnosed. The likeliest explanation is that `trend-follow-sell-002` was re-admitted
mid-flight - it is the strategy restored after the `loop._agent_holding` defect - and its BUY
history predates its current admission, so the tracker starts with no open lots to sell
against. That is a hypothesis, not a finding.

Recorded rather than fixed because this is a domain question, not a repository defect, and
getting it wrong would change reported PnL. It is a WARN rather than a FAIL for the same
reason `make evaluate` exits zero when the profit target is unmet: a property of the data is
not a property of the code.


> **Dated section starts here.** Everything below records the position at the time it was
> written. Its figures were correct then and are not claims about the present. For current
> numbers run `minictrl doctor`.

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

| State | Where to read it |
|---|---|
| market open/closed, account, positions | `minictrl status` |
| every health and evidence check | `minictrl doctor` |
| submitted / filled / realized PnL | `minictrl profit`, `minictrl evidence report latest` |
| cycle count, strategy library | `minictrl doctor` |

This table used to carry those numbers directly, and the gate compared the document against
`doctor`. That was wrong in a way that only shows up once the agent trades: every cycle and
every fill moved a figure, so a gate that checked them went red on every order. That is a
gate that trains you to ignore it.

The numbers are live state, so they are queried, not written down. What this file holds is
what does not change while the agent runs:

| Fixed | Value |
|---|---|
| mode | `paper` — `cli.py` refuses to build a client against a non-paper URL |
| allowlist | AAPL, MSFT, NVDA, QQQ, SPY |
| hard limits | $5,000 per position, $20,000 total exposure, $500 daily loss, 10 trades/day |
| model | `qwen3.8:27b` on Ollama at `127.0.0.1:11434` |
| credentials | `$XDG_CONFIG_HOME/min-agent/env`, mode 600, outside the repository |
| units | `~/.config/systemd/user/`, four enabled, durable across reboot |

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

**The agent had not closed a lot at the time of writing, so per-strategy PnL was still
empty.** It is not empty now: 27 closed lots, and per-strategy PnL is broker-verified -
see the acceptance audit above. The
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
Alpaca UI. This was closed by operator decision on 2026-09-30, recorded above; it is
left here as the original finding rather than deleted.

## 2026-09-28 — verification gate and the two missing loop stages

**`make verify` is the single gate.** 17 check classes, 0 failed, 472 distinct
tests, 61/61 defect audit, `doctor` RESULT OK, exit 0. Per-class re-runs:
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

## 2026-10-03 — the profit is one exit event, and the budget now buys measurement

**Measured, not assumed.** The broker-verified record is 35 closed lots, 62 linked
fills, net PnL **+643.40** (+630.13 after an assumed 0.05% round trip; observed broker
fees 0.00): `tiny-fixed-size-001` +366.03, `fixed-size-buy-001` +146.94,
`trend-follow-buy-001` +81.36. Grouped by exit price, **a single exit at 766.57 closed
23 of the 35 lots and produced 87.7% of the net PnL**, involving all three strategies at
once. Ten distinct exit prices exist across the record; the other nine contribute +29.94
combined. So the headline is **one market move, not thirty-five decisions**, and the
ranking between the three reflects how many shares each happened to hold.

The champion's +366.03 is 8 of its 10 lots exiting at 766.57, bought across 727–742 while
SPY was in its June range; its other two lots exited at 769.31 for +3.37 combined.
**No strategy on record has a demonstrated repeatable edge.** The doctor already carries
part of this and says so: `regime=CANNOT ATTRIBUTE`, `signal=CANNOT ATTRIBUTE`,
`by regime: {'UNKNOWN': 594.33}`, and `UNMATCHED SELLS 29` makes the headline an upper
bound rather than an exact result.

**Decision (owner's).** Spend about one trading day of the incumbent's throughput so
every candidate that can actually trade reaches the evidence gate.

**Change.** `StrategySelector.min_probation_cycles` 3 → 13.

- Why 13: the offline screen refuses to judge below ten informative decisions
  (`DEFAULT_MIN_SCORED_DECISIONS`), and a `FIXED_SIZE` produces them at an observed
  0.75–0.91 per selected cycle — 11 to 13 selected cycles. The budget of 3 bought about
  two informative decisions, so every candidate was judged before it could be measured and
  the screen returned `INCONCLUSIVE` for all of them. **Zero trading strategies have ever
  passed it.**
- The cost is deliberate and accepted, not discovered: probation has absolute priority in
  `select`, so 13 candidates × 13 cycles is cycles taken from the incumbent.
- Verified with the shipped code at spot 769.58: **6 candidates claim 13 cycles each =
  78 cycles ≈ 1.2 trading days** at the observed 66 market-open cycles per day, and the
  **9 dormant candidates claim nothing** because their trigger band contains spot and
  the selector skips them. That filter is what makes the budget mean anything.
- **Not a one-time cost.** A newly admitted candidate re-enters probation and claims 13
  again; at the current admission rate this is a standing claim of roughly 20–40% of
  throughput. The 6-claimant count moved from 5 to 6 between measurements because a
  seventh candidate was admitted, which is the refill in action.

**Still not fixed, and the next honest step.** The four terminal PAUSED strategies whose
stated reasons have expired (2 price-gated above spot, 2 refresh-gated) still cannot return
to the candidate pool — nothing re-adjudicates PAUSED. That arrives with the next trading
day, and it is deliberately not pre-built. Together with the profit being one exit event,
the honest summary is: **nothing is proven about any strategy's edge, and this change buys
the measurements that could prove something.**

`make verify`: 49 classes, 0 failed, 1331 test executions across 61 files, exit 0.

**Is the budget sustainable, or does the queue outgrow it?** This is the question that
would invalidate the change, so it was measured rather than assumed. Only *claimant*
admissions consume budget — a candidate whose trigger band contains spot claims nothing.
Of the 15 probation candidates admitted since 2026-09-25, **6 claim and 9 are dormant**;
claimant admissions run **1.50/day**, against a sustainable **5.1/day** (66 cycles/day ÷ 13).
**Sustainable**, at roughly 30% of throughput.

Two honest caveats. Dormancy is **price-dependent** — if spot leaves those bands those 9
become claimants, and the queue is currently 6 × 13 = 78 cycles, so all 15 claiming would
be 195 cycles ≈ 3 days, still bounded because a candidate claims 13 cycles once and then
leaves probation. And a first pass at this measurement counted *all* admissions (7.33/day)
and wrongly concluded the budget was unsustainable; counting dormant candidates as budget
consumers is the same error as the turn-10 harness, in a new place. Admission count is not
claimant count.

## 2026-10-03 — journal rotation is imminent, and retention is ~14 days

`doctor` warns `journal headroom 84% of the 67MB cap`. Investigated before it could fire
during trading rather than after.

**Rotation does not destroy the PnL record — measured, not assumed.** Each
`PNL_EVIDENCE_RECORDED` event carries the *cumulative* closed-lot list in its payload rather
than reconstructing it on read, so the newest event always holds the whole record.
Re-reading with the `.1` generation removed returns `closed_lots 35`, `net_pnl 643.4`,
`linked_fills 62` — byte-identical to reading both generations. The gap I expected here does
not exist.

**But the design has a growth term, and retention is short.** Measured on the live file:

- 18.7 MB/day; 4 generations (`.1`–`.3` + live) = 268 MB → **~14 days of retention**.
- `PNL_EVIDENCE_RECORDED` is 174 events / 12.4 MB = **22% of the journal**, averaging
  **71 KB per event** at 58 events/day ≈ 4.1 MB/day.
- Each event embeds *all* closed lots, so per-event size grows as lots accumulate. The
  growth term is superlinear in lots, and it will get worse, not better.

**Why this is a finding and not a fix.** The PnL record survives rotation by design. What
14 days does bound is anything *recomputed from the journal each cycle* — model calibration
(314 decisions, 210 scored), the experiment chain (123 strategies), champion history. Past
~14 days those analyses silently lose their older history rather than failing loudly, and
there is no test pairing rotation with any of them: rotation and closed-lot evidence are
never exercised together anywhere in the suite.

**Not changed today.** Rewriting the evidence path from cumulative snapshot to delta is
exactly the kind of change that must not be made unilaterally while paper trading is live,
and it is not needed before Monday. Recorded so it is a decision with a number attached
rather than a surprise discovered during a trading session.

## 2026-10-03 — the audit's miscalibration claim survives; a silent verdict filter did not

An audit reported the model as miscalibrated (Brier 0.433 against 0.25) and proposed a
self-healing calibration loop. Investigating that produced a real defect and **a correction
to the correction.**

**The defect.** Three hand-declared copies of the verdict set existed in `counterfactual`,
`calibration` and `offline_validation`, and `calibration`'s had drifted: its `INFORMATIVE`
omitted `GOOD_TRADE` entirely, and `CalibrationRow.correct` required `GOOD_HOLD`. So a
winning trade was first dropped from calibration and then, had it survived, counted as
wrong. `counterfactual` now owns the set and the other two import it; `correct` accepts
`GOOD_HOLD` and `GOOD_TRADE`. Three tests added, one asserting the sets are the same
objects so they cannot drift apart again.

**The correction.** The plan claimed this dropped "49 of 607 scored decisions" and that
"Brier 0.433 describes this bug rather than the model". **Both were wrong.** 49 is the
counterfactual report's own count, which includes rows with no matching journal cycle;
joining verdicts to cycles that actually recorded a confidence, on the live journal:

| | informative | correct | base rate | Brier |
|---|---|---|---|---|
| before | 497 | 287 | 0.5775 | **0.5399** |
| after | 505 | 295 | 0.5842 | **0.5342** |

**8 decisions, not 49**, and the Brier score moves by 0.0059. So:

- the defect was worth removing — a silent filter on the evidence layer, and three copies
  of a verdict set is how it drifted;
- **but it does not explain MIS-CALIBRATED.** Brier stays far above the 0.25 of a constant
  0.5 claim, so the model genuinely is miscalibrated, and the self-healing loop is a real
  gap rather than an artifact to dismiss;
- and the recorded confidence distribution explains why: it is **bimodal, 637 near 0 and
  542 near 1 with nothing in between**, so the model states near-certainty and is wrong
  about 46% of the time. A graded confidence that is really a coin flip cannot be
  calibrated by better bookkeeping.

Also established, and it lowers the severity of two other audit claims: the **offline
screen does not use `confidence` at all** (it gates on `correct_outcome_ratio`, judged from
real outcomes), so miscalibration has never corrupted a promotion decision. And confidence
is consumed only for measurement, never for sizing or ranking.

`make verify`: 49 classes, 0 failed, 1331 executions; 858 pytest pass.

## 2026-10-03 — the decision instruction now exists as data, and had a measured hole

The whole of the system's instruction to the model was a string literal inside
`llm_decision.decide()`. There was nowhere else it lived, so it could not be audited
without reading a method body, diffed as a change to intent, or corrected without a code
change and a daemon restart. It is now `DECISION_INSTRUCTION`, one owned constant, with the
prompt assembled from it.

**The hole it had.** It spelled out the `action` enum, the `quantity` rule and every
`hold_reason` value — and never said `confidence` must lie in [0, 1]. The JSON schema passed
alongside it *does* carry `minimum: 0, maximum: 1`, so the bound was available to constrained
decoding and 92 cycles still reached validation with an out-of-range value: a numeric range
does not survive into the decoding grammar, which leaves the prose as the only enforcement
point. **Cost: 100 cycles lost to a malformed `confidence` plus 135 fail-closed HOLDs on
invalid output — about 20% of the entire decision history, every one of them a cycle the
market was open for.** The 28 HOLDs that carried a non-zero quantity despite the rule being
stated are addressed by stating the quantity/hold_reason coupling in one sentence.

The prompt grew 781 → 975 characters. The file's measured budget said prompt 3441 at
`num_ctx` 6144 using 4584; it is now ~3635 using ~4778, leaving 1366 of headroom, so the
recorded budget still holds and was not widened to hide the increase.

**A correspondence test, not a string test.** `test_the_instruction_states_every_constraint_
the_validator_enforces` walks the actual schema: every `action` enum, every `hold_reason`
value, every required field, and the confidence bound must all appear in the instruction. A
field added to the schema without a line here now fails the build instead of costing 20% of
the history.

**Not built, deliberately:** a prompt-management subsystem or an auto-rewriting optimiser.
The observed need was one missing line in a hardcoded string; a constant plus a
correspondence test covers it. Whether the model should ever rewrite its own instruction is
unresolved and unevidenced — see the calibration section above for why the confidence problem
is not a prompting problem.

`make verify`: 49 classes, 0 failed, 1337 executions across 61 files; 860 pytest pass.

## 2026-10-03 — PAUSED is terminal by construction, and the reason was never kept

The audit reported "PAUSED 无重新裁决路径" as a gap. Investigating it found the cause is
earlier and more basic than a missing rule.

**`StrategyEngine._review_one` returns immediately for PAUSED**
(`strategy_engine.py:192`), before any lifecycle rule runs. So a PAUSED strategy is not
merely unlikely to come back — it is never examined again by the reviewer at all.

**And there was no way to ask whether its reason still held, because the reason was not
kept.** `StrategySpec` had no `lifecycle_reason` field: the authoritative strategy file
carried `lifecycle: PAUSED` and nothing else. All 33 reasons survive in the journal, and
`experiment_registry` kept its own `lifecycle_reason` — so the reason lived in the derived
view and not in the single source of truth, which is backwards. That is also what the
doctor's repeated "reconciled by doctor: the strategy file carried a gated life" (17
passes) was colliding with: a terminal state it could not explain.

Landed in this commit: `StrategySpec.lifecycle_reason`, written at all three lifecycle
write sites (two in the daemon, one at admission), so the reason survives a reload. It
defaults to empty so every strategy written earlier still loads. Two tests: the field
exists and defaults empty, and all three write sites actually populate it — a field nothing
writes would be documentation rather than a fix.

**This does NOT restore the 33 PAUSED strategies. P1-a is not done.** Restoring them needs
two further changes, and neither is a one-liner:

1. **`review()` has no price parameter**, so the rule cannot ask whether a paused strategy
   is *currently actionable* — which is the test that matters, because 18 of the 33 are
   paused for "probation produced no exploration evidence" and were dormant for their whole
   probation. Their pause is an artifact of the old oldest-first queue, which has since
   been fixed to skip strategies that cannot act at the current price; the cause is gone
   but the 18 pauses it produced are stranded.
2. **Re-entering PROBATION alone would churn**, because a strategy that already has
   `cycles >= min_active_cycles` and `trade_attempts == 0` re-pauses immediately. It needs
   a genuinely fresh probation window, which means resetting its evaluation record — a
   real operation with a real risk of erasing evidence, and not one to rush into the
   session before an open.

Deliberately not landed on a partial basis: re-adjudication changes which strategies trade,
so it wants its own commit with its own tests rather than riding along on the enabling
change.

`make verify`: 49 classes, 0 failed, 1339 executions across 61 files; 862 pytest pass.

## 2026-10-03 — the journal now states the window it covers

Rotation deletes the oldest generation, so the journal stops being the record of everything
that happened and becomes a recent fragment. Nothing said so. The doctor reported
"1179 cycles" as if it were a total, and those cycles are only what two generations still
hold — measured 2026-10-03: `retained 2026-06-09T06:00:45 .. 2026-10-02T15:58:54 across
2 generation(s); older cycles have been rotated away`.

That matters because several analyses are **recomputed from the journal on every pass** —
model calibration (314 decisions), the experiment chain (123 strategies), champion history.
Past the retention horizon they keep returning a confident number computed from a fragment.
Silently wrong is worse than loudly absent.

`JsonlJournal.retained_window(known_since=...)` reports the retained span, the generation
count, and whether older cycles are gone. Truncation is judged against the **strategy
registry**, not by counting generations: the registry survives rotation, so its earliest
admission proves when the system existed. A generation boundary cannot distinguish "never
had more history" from "had more and lost it" — the same reasoning used for
`lifecycle_reason` landing in the same window.

**This makes truncation visible; it does not extend retention.** ~14 days at the measured
18.7 MB/day is still the horizon, and whether 14 days is enough depends on what the analyses
need — which is the next thing to measure, not the next thing to build.

Note the doctor's new check needed no blind `except`: `StrategyLibrary.list()` never raises,
returning `[]` for a missing directory and collecting parse failures internally. An earlier
draft guarded it anyway, which added a 63rd `BLE001` and turned the ruff-count gate red —
deleted rather than suppressed.

`make verify`: 49 classes, 0 failed, 1340 executions across 61 files; 863 pytest pass.

**Is ~14 days actually too short?** Measured rather than assumed, per day-of-journal each
analysis actually spans:

| analysis | days spanned | vs ~14-day horizon |
|---|---|---|
| `COUNTERFACTUAL_EVALUATED` | 5 (2026-09-29 .. 10-03) | fine |
| `OFFLINE_VALIDATION_COMPLETED` | 5 | fine |
| `CURRICULUM_PROPOSED` | 15 | marginal |
| `PNL_EVIDENCE_RECORDED` | 20 | exceeds |
| `STRATEGY_EVALUATION_RECORDED` | 20 | exceeds |

The two that exceed it do not actually depend on the old records: the PnL payload is
cumulative, so the newest event carries the whole closed-lot record (verified earlier —
dropping the `.1` generation returns an identical `closed_lots 35 / net_pnl 643.4`), and
strategy evaluation runs a 50-cycle window, about one trading day.

**So the honest conclusion is that retention is currently adequate, and extending it would be
building for a problem that has not arrived.** The value of the previous commit is the
visibility: if an analysis ever does start needing more than the window holds, the doctor
will now say so instead of the number quietly changing. That is the whole requirement — a
check that fires when the constraint binds, not a larger buffer built in anticipation.

Also worth stating plainly: the retained window is **not** 14 days of trading. It spans
2026-06-09 to 2026-10-03 in timestamps, with a 98-day hole in the middle (2026-06-22 to
2026-09-28) when the system was not running at all. Two generations are on disk, 67MB and
57MB. Any analysis that assumes continuous history is already wrong on this host for a
reason that has nothing to do with rotation.

`make verify`: 49 classes, 0 failed, 1340 executions across 61 files; 863 pytest pass.
