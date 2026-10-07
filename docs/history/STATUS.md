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
sold with no lot to price them against. Run `minictrl doctor` for the current figure; it moves
as the agent trades.

**What it does to the numbers — measured, 2026-10-03.** `_apply_activity` books no PnL for a
sell it cannot match: it records the quantity and the fee and stops
(`evaluator.py:878-882`). So those exits are **absent from** the headline realized figure, not
present in it without a name. The total is therefore **incomplete**, and the omitted term has
an unknown sign — 29 shares sold at 766.57 with proceeds that are real and a cost basis that
is not on the record. An earlier version of this section, and of the doctor's own message,
called it "an upper bound". That was wrong in the direction that flatters the run: it is the
most the record can *prove*, not the most the account could have earned.

**The cause, measured rather than hypothesised.** The standing hypothesis was a re-admission
that lost the BUY history. It is wrong: the ledger matches a SELL FIFO across the whole
account and records `closing_strategy_id`, precisely so a lot opened by one strategy can be
closed by another, and 23 of that sell's 52 shares did close agent lots. Reading all 62
retained fills in time order, the account's on-record position goes to **−29** the instant that
sell lands and only recovers through later BUYs — the agent sold 29 shares it had never bought
on its own record. The exit strategies exist to close an account position they do not own
(`fixed-size-sell-005`: *"the open SPY position (10 shares, $7,641.80) has no exit path"*),
and part of that position was not theirs. Same fact as the `unmanaged exposure` warning — 5
positions outside the allowlist, 37% of equity — seen from the PnL side.

Recorded rather than fixed because it is a property of the **account**, not of the trading
path: pricing those shares would mean fetching broker history the agent does not currently
read, which is a scope decision rather than a defect fix. It stays a WARN rather than a FAIL
for the same reason `make evaluate` exits zero when the profit target is unmet: a property of
the data is not a property of the code. Plan and measurements:
`docs/history/plans/2026-10-03-the-pnl-total-omits-unmatched-exits.md`.


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
[`docs/history/plans/2026-09-28-quantgroup-recovery-and-refactor.md`](docs/history/plans/2026-09-28-quantgroup-recovery-and-refactor.md).

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
   min-agent.service       $XDG_CONFIG_HOME/systemd/user/min-agent.service
   ollama.service          $XDG_CONFIG_HOME/systemd/user/ollama.service
   quant-watchdog.timer    $XDG_CONFIG_HOME/systemd/user/quant-watchdog.timer
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
   OLLAMA_MODELS=<scratch>/ollama_local/models \
   OLLAMA_HOST=127.0.0.1:11434 CUDA_VISIBLE_DEVICES=0 \
     <scratch>/ollama_local/bin/ollama serve
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
`$XDG_CONFIG_HOME/systemd/user`, which exists and holds the enablement
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
recorded as UNKNOWN in `docs/history/known_state_findings.json` and no synthetic
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
works. Details and the full cause breakdown are in `docs/history/PHASES.md` §4.

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

## 2026-10-03 — a replay environment over real bars, and what it cost to build honestly

The request was a simulated account reproducing real conditions, so development could
continue and be verified before anything touched Alpaca. One correction first: **the system
is already on Alpaca paper** — credentials in place, PnL coming back through that API. So
the missing piece was never "connect to Alpaca"; it was a place to verify a change that
alters which strategies trade.

**The bars are real, not synthetic.** Fetched from Alpaca's own history and cached to
`runtime/min_agent/replay/SPY_5min_2026-09-28_2026-10-02.json`: **847 real five-minute SPY
bars**, 2026-09-28 to 2026-10-02, high 772.65 / low 758.79 — the same range the live journal
saw, and the 766.57 exit falls inside it. A replay over invented prices can only show that
code runs.

**Not a second implementation.** `TradingLoop` already takes its collaborators by injection,
so exactly two are substituted — `ReplayDataGateway` (real bars, one per cycle) and
`ReplayExecutor` (fills at the next bar's open). The decision engine and the Guardian are
the real ones.

**Fills use the next bar's open, never the decision bar's close.** Filling on the close of
the bar the decision was made from assumes the close was knowable before it printed; that is
the standard way to manufacture a backtest.

**The acceptance test found a real defect, which is why it existed.** Driving the real loop
over the 847 bars refused **all 847 cycles** with `data snapshot is stale` — the Guardian
compares snapshot time to wall-clock, and those bars are from last week. It was right and
the replay was useless. Relaxing staleness would have produced a harness that trades and
proved nothing, because it would have tested a risk system nobody ships. Instead
`TradingLoop` gained an optional `now` passed to `guardian.review(now=...)`, defaulting to
`None` so **the live path is unchanged** and still uses wall-clock; a replay passes the
replayed session's clock. Same check, correct time base.

After the fix: **130 replayed fills over 847 cycles, and still `submitted_orders=0`,
`account_realized_or_reported_pnl=None`, `closed_lot_count=0`,
`pnl_evidence=missing_fill_price_and_broker_activity`.** The safety property holds under
real trading load, not only on an idle path.

**The safety claims are machine-checked, not asserted.** `tools/replay_safety_probe.py`
verifies four properties — no broker client or paper executor referenced, `REPLAYED` is a
distinct status with no `order_id`, the source says real-bars-simulated-account, and a
journal of replayed fills yields zero realized PnL and never broker verification — and
`make verify` runs it as `replay-cannot-reach-the-account`. It was checked against a
deliberately broken executor, which it caught with three specific failures, so it is not a
check that always passes.

Two repo rules caught me rather than the other way round: `save_bars` first wrote with a bare
`write_text`, which tripped the recorded defect **D12 "no bare write_text outside
atomicio"**; it now writes atomically, because a half-written bars file is a replay that
silently starts mid-session.

**A replayed profit is not a profit.** Nothing here is trading evidence (AGENTS.md #5, #17),
and the check's docstring records the same distinction `check_shadow_live_consistency`
insists on: this proves the mechanism, not that any replay has been run.

`make verify`: 50 classes, 0 failed, 1376 executions across 62 files; 876 pytest pass.

## 2026-10-03 — P1-a re-adjudication measured, and deliberately NOT built

The remaining P1-a work (re-adjudicating the 33 paused strategies) was measured against the
847 real cached bars before being built, and the measurement says it would make things worse.

**Only 7 of the 33 PAUSED strategies are ever actionable** across the real price range
758.79–772.65. The other 26 never act at any price in it.

And the 26 are not a lifecycle problem. Their `reference_price` values are **100, 120, 150,
170, 180, 200** while SPY trades at **769.88** — placeholder anchors copied from an old prompt
example, so their bands (98–102, 148.5–151.5, …) are unreachable by construction.
`StrategyAdmission._reference_price_rejection_reason` **already refuses both failure
directions**, and its docstring records this precise defect ("nine strategies were admitted
with reference_price in {100, 120, 150, 170, 180, 200}"). These are pre-gate residue; the gate
works and no new ones can arrive.

So restoration means restoring 6 actionable strategies — and that inverts the proposal:

```
actionable at 769.88: 13  (7 PROBATION / 6 PAUSED)
probation queue is SERIAL      sorted(actionable, created_at)[0], one per cycle
the promotion gate is PER CANDIDATE   10 informative decisions ~= 13 dedicated cycles

drain today's queue:              7 x 13 =  91 cycles ~= 1.4 trading days
drain it with the 6 restored:   13 x 13 = 169 cycles ~= 2.6 trading days
```

**Restoring them makes the first promotion slower, not faster.** A gate counted per candidate
and served from a serial queue is not accelerated by adding candidates; it is lengthened.
"More candidates means more chances" was the assumption, and it is wrong here.

So the three coordinated changes this would have required — plumbing price into `review()`, an
actionability gate, and resetting evaluation records — stay undone, on purpose. That is a
better outcome than shipping them: they would have touched a safety-adjacent subsystem and
delayed the one measurement the project is waiting for. `StrategySpec.lifecycle_reason`
remains, because a data-loss fix stands on its own merits.

**What actually gates a promotion, stated plainly:** each candidate needs ~13 cycles of
exclusive attention, served one candidate at a time. Draining all 13 actionable candidates
costs ~169 cycles ≈ 2.6 trading days. That is the number to plan against — not the number of
strategies in the library.

`make verify`: 50 classes, 0 failed, 1376 executions across 62 files; 876 pytest pass.

## 2026-10-03 — requirement-by-requirement audit: three of the five gaps are already closed

Auditing each audit finding against the journal rather than against my earlier summary of it.
Three items turn out to be mis-framed, and one is fixed but unverified live.

**"PnL 证据失败 1193 vs 成功 1926" — not a defect. My audit framed it as one.** All 1193
`PNL_EVIDENCE_FAILED` events carry the reason **`profit target not satisfied`**, and the last
was 2026-06-23. That is the system correctly recording that the 10%-daily-profit *target* was
not met — a failed target, not a failed computation — and a 38% rate of "the target wasn't
met" over five months is what an honest agent looks like. No check anywhere flags it, and
`pnl attribution` reports PASS on evidence. The audit quoted a count and called it an error
rate without reading the reason.

**"347 周期无可用策略" — already fixed, and has not recurred.** Last occurrence
**2026-06-10**, five months ago. The cause was the oldest-first probation queue serving
candidates that could not act at the current price, fixed by the dormancy filter and by the
selector filtering what it falls through to. Zero occurrences across the five most recent
trading days (2026-09-28 … 10-02). Nothing is needed here.

**"12.5% 周期带错误" — the fix has landed but has never run live.** The largest single cause
was LLM output failing validation (100 cycles on a malformed `confidence`, 135 fail-closed
HOLDs — about 20% of the whole history). That is what `DECISION_INSTRUCTION` addresses. Its
last occurrence was 2026-09-30, which *predates* the fix. So the honest statement is: cause
identified and closed, effect unmeasured, and it cannot be measured until the market opens.

Where the five gaps actually stand:

| gap | state |
|---|---|
| 1. not 7x24, no off-hours learning | **measured, deliberately not built** — evidence-starved; 7697/10130 screens returned INCONCLUSIVE, and more compute multiplies inconclusive verdicts rather than creating evidence |
| 2. no self-representation surface | **closed** — `DECISION_INSTRUCTION`, with a schema-correspondence test so a new field cannot again cost 20% of the history |
| 3. autonomy diluted | **closed but unverified** — the 1193 figure was not a defect, the 347 starvation has not recurred since June, and the malformed-output cause is fixed pending a live window |
| 4. PAUSED unreachable / journal truncation | **split** — truncation is now visible and measured as *not* binding; re-adjudication was measured and deliberately not built because it lengthens the queue |
| 5. no proven edge | **open, and not a code problem** — 87.7% of PnL is one exit at one price; needs cross-day real evidence |

**The one number to plan against:** each candidate needs ~13 cycles of exclusive attention and
the queue is serial, so draining all 13 actionable candidates costs ~169 cycles ≈ 2.6 trading
days. Everything else in this audit is downstream of that.

`make verify`: 50 classes, 0 failed, 1376 executions across 62 files; 876 pytest pass.

## 2026-10-03 — self-evolution, in the only sense that is falsifiable: the rules react to scored evidence

The request was to make the system self-evolve. Measured first, because the honest version is
narrower than the phrase: 87.7% of PnL is one exit at one price, confidence is bimodal, Brier
is 0.5342. Against that evidence, "self-evolution makes strategies better" is not falsifiable.
What **is** falsifiable is whether the system's own selection pressure reacts to its own
scored evidence. That is what now works, for the first time in this project's history.

### The measurement that found the defect

Driving the real loop over 847 real bars, then feeding the scored decisions through the real
screen and the real lifecycle manager:

```
该策略的决策裁定: 819
离线筛查: verdict=REJECT_POOR_DECISIONS scored=580 correct_outcome_ratio=0.475862
  good_holds=6 good_trades=270 false_trades=304 missed_alpha=0
lifecycle 裁定: None（留在 PROBATION）
```

**A screen saying `REJECT_POOR_DECISIONS` produced no ruling at all.** A strategy with 304
losing trades out of 580 scored stayed in probation and nothing anywhere said so. Isolated to
20 GOOD_TRADE + 25 FALSE_TRADE it reproduced, and `_review_one`'s source does not contain
`REJECT_POOR_DECISIONS` at all.

### Root cause

The veto lived in `AgentDaemon._apply_offline_rejection`, not in `StrategyLifecycleManager`.
`_screen_all_strategies` called it as a side effect. So the **promotion gate was reachable
from the rule set while the rejection was not** — an asymmetry with no principled basis, which
meant any other caller got half the selection pressure, and the journal could hold a REJECT for
a strategy still marked PROBATION. Same class of defect as the three drifted verdict copies
fixed earlier: one authoritative rule, restated as control flow elsewhere, and the two drift.

### The change

`_review_one` gained a branch, beside every other lifecycle rule, and `_evidence_rejection_reason`
reports why with the numbers in the message:

```
lifecycle 裁定: [('RETIRED', 'offline validation rejected its own recorded decisions:
                  580 scored decision(s), 276 correct, correct_outcome_ratio 0.476')]
```

`RETIRED` rather than the `PAUSED` the daemon used, because `RETIRED` is terminal in these
rules while `PAUSED` is not, and a strategy whose own recorded decisions were 48% correct has
earned a final answer rather than another probation. That is the one behavioural difference,
and it is deliberate. The daemon's own path is unchanged in effect.

**The veto must never fire on silence.** `INCONCLUSIVE` means "cannot tell yet", and treating
it as failure is the error the admission gate already made once — a first-cycle candidate told
INCONCLUSIVE came back paused. A rule that retired on silence would empty the library. Tests
cover REJECT, INCONCLUSIVE, absent evidence, PASS, and an already-RETIRED strategy.

**Machine-checked, and checked against a deliberate break.**
`tools/selection_pressure_probe.py` asserts from the rule set alone — with no daemon in the
picture — that REJECT retires carrying its ratio, that INCONCLUSIVE and absent evidence do
not, and that `_review_one` itself names the verdict rather than depending on a caller's side
effect. Removing the branch makes it report
`a REJECT_POOR_DECISIONS verdict produced no retirement from the rule set; decisions were
none`, so it is not a check that always passes. `make verify` runs it as
`selection-pressure-reaches-the-rules`.

### What this is not

It is not proof that any strategy has an edge, and not proof that any strategy has actually
been retired — the check's docstring records that distinction, as the replay check does. The
next honest step is the one the objective forbids skipping: a rejection reason to steer a
generator is premature while there have been **zero promotions**. The number to watch is the
first `PASS_SCREENED`.

`make verify`: 51 classes, 0 failed, 1380 executions across 62 files; 880 pytest pass.

### The second half: I created the drift I was removing, then deleted the duplicate

Moving the veto in was not enough. With `_apply_offline_rejection` still present, the same
screen verdict produced **two different rulings**:

```
lifecycle=PROBATION  -> ['RETIRED']      # the rule set
lifecycle=PAUSED     -> None            # already terminal
```

and on the daemon the order is `_screen_all_strategies` (which paused it) before
`_manage_strategy_lifecycle` (which then skips it), so **the daemon yielded PAUSED while the
rule set yielded RETIRED**. One piece of evidence, two answers — exactly the
one-authoritative-rule violation the veto was moved out of, reappearing one layer down.

`_apply_offline_rejection` is deleted. Rejection now reaches the file through the same
journal-first discipline as every other lifecycle decision: intent journalled, file written,
outcome journalled. The four daemon tests that called the deleted method directly were
**rewritten onto the rule set**, not deleted, because what they assert is still true and
still worth asserting: a rejected strategy is retired, a screened-through one is not promoted,
a first-cycle candidate reading INCONCLUSIVE is left alone, and a retired one is not
resurrected.

**The veto was also moved above the cycle-count gate**, because it does not depend on it. The
screen judges a strategy's own scored decisions and needs no cycle summary, so requiring one
first made the veto unreachable when no result existed — and `AgentDaemon` returns early from
the lifecycle pass without a reflection memory, which is precisely the degraded case where its
duplicate used to be the only thing acting. One rule, reachable from every caller.

Both directions of the selection pressure are now verified end to end on real bars:

```
REJECT_POR_DECISIONS 580 scored, ratio 0.476 -> RETIRED, reason carries the numbers
PASS_SCREENED        50 scored, ratio 1.000 -> ACTIVE,  "decision quality that cleared the evidence gate"
INCONCLUSIVE                                   -> no ruling; silence is not failure
```

`make verify`: 51 classes, 0 failed, 1380 executions across 62 files; 880 pytest pass.

## 2026-10-03 — driving the daemon, because the rules were only ever proven by hand

The two previous entries proved the lifecycle rules react to scored evidence. Both proofs
were partial in the same way: `TradingLoop` was replayed on its own, and
`StrategyLifecycleManager.review()` was called by hand. A component proves a component.
Reflect, score every decision against what the market did next, screen the strategy on those
scores and rule on the verdict all happen in `AgentDaemon._maintenance`, and **nothing ran
that path**.

`tools/replay_self_evolution.py` drives the daemon itself over the same 847 real bars, and
`tools/self_evolution_probe.py` asserts the stages fired rather than being told they did:

```
847 cycles   0 daemon errors
COUNTERFACTUAL_EVALUATED / OFFLINE_VALIDATION_COMPLETED / REFLECTION_GENERATED /
STRATEGY_LIFECYCLE_UPDATED all present
580 scored, ratio 0.476 -> RETIRED, reason carries the numbers; no broker-verified claim
```

It found a defect nothing else could see. The daemon's sleep hook advances the gateway
between cycles and runs maintenance *before* checking whether the replay is finished, so
`ReplayDataGateway.current` was reached with the cursor one past the last bar and raised
IndexError — `snapshot` clamped, `current` did not, which is why the same over-advance
produced a valid last-bar snapshot and then blew up. Clamped, with a test that advances ten
times past a three-bar series.

**The decision engine in that replay is a three-bar momentum rule, not the LLM, and that is
the point.** A replayed system that always HOLDs would score every decision as a good hold
and never reach the branch that retires a strategy. The Guardian is the real one, unchanged —
a replay with its own softer risk layer would be testing a system nobody ships. Fills stay
`REPLAYED`, so this proves selection pressure reacts to scored evidence and says nothing
about whether any strategy has an edge.

`make verify` runs it as `self-evolution-closes`, the 52nd class.

### The gate could not pass on a clean HEAD

Running the whole gate rather than the parts of it that were being worked on:

| | on HEAD | now |
|---|---|---|
| `ruff check src/ tools/ tests/ examples/` | **15 findings** | 0 |
| `mypy src/min_agent` | **2 findings** | 0 |
| `make verify` classes | red | 52 / 52 |

`make lint` is the first step of `make check`, `make fast` and the inner loop this project
runs on every edit, and it failed. All 15 findings were in the files the two replay commits
had just added: 8 `# noqa: E402` that `ruff.toml` already ignores globally, two unsorted
import blocks, three missing final newlines, an unused `dataclasses.field`, and an unused
`cycle_id` in `ReplayExecutor.execute` — kept under that name because `loop.py` calls every
executor with `cycle_id=` as a keyword, and renaming it would have broken the canonical
execution path for the replay executor alone.

`make type`'s comment reads *"It is now 0 findings and blocking"*. It was blocking and
reported 2: `REPLAYED = "REPLAYED"` inferred as `str` where `ExecutionResult.status` is a
`Literal`, and `retained_window()` returning `dict[str, object]`, which pushed the missing
type onto its one reader. Both fixed at the source — the constant annotated as the literal the
model requires, the window as a `TypedDict` of its five fixed keys, which also deleted a
`str()` cast in the doctor. The comment is now true of the target.

### The PnL total is incomplete, and the report said it was an upper bound

The long-standing `UNMATCHED SELLS` finding was finally diagnosed rather than carried, and
the sentence both `STATUS.md` and the doctor attached to it was false in the direction that
flatters the run. `_apply_activity` books **no** PnL for a sell it cannot match — quantity
and fee only — so those exits are absent from the headline figure, not present in it without
a name. The omitted term has an unknown sign: 29 shares at 766.57 whose cost basis is not on
the record. The number is incomplete; it is not an upper bound, and `+594.33` is the most
the record can *prove*, not the most the account could have earned.

The standing hypothesis — a re-admission that lost the BUY history — is **wrong**. Lot
matching is account-level FIFO and records `closing_strategy_id`, so 23 of that sell's 52
shares did close lots opened by other strategies. Reading all 62 retained fills in time
order, the on-record position goes to **−29** the instant the sell lands: the agent sold 29
shares it had never bought on its own record. It is the same fact as `unmanaged exposure`
(5 positions outside the allowlist, 37% of equity) seen from the PnL side — a property of the
account, not of the trading path.

Deleted the false claim; added no field. `unmatched_sell_quantity` is already the number, and
`tests/min_agent/test_evaluator.py` already pins the semantics on this exact 52-share sell
(`closed_lot_count == 23`, `{"closer": 29.0}`, `opener == (766.57 − 742.03) × 23`). The
figure did not move — only the claim about it.

`make verify`: 52 classes, 0 failed, 1383 executions across 62 files; 881 pytest pass.

### The next question, which is a scope decision and not a build

**Should the agent manage account positions it did not open?** 37% of equity sits outside the
allowlist and 29 shares of one sell had no basis on record. Whether the right answer is to
price those shares from broker history or to refuse to touch positions the strategy library
does not own decides what gets built — and building either before that decision is the kind
of anticipatory infrastructure this project has been deleting.
`make verify`: 52 classes, 0 failed, 1383 executions across 62 files; 881 pytest pass.

## 2026-10-03 — the calibration report was measuring the weather

`doctor` had said `MIS-CALIBRATED` every day for weeks, and the daemon published a lesson
to the model built from it: *"your stated confidence does not predict whether you are
right, and the relationship runs backwards… a 0.0-0.2 confidence bucket was right 91% of
the time while a 0.6-0.8 bucket was right 6%… Judge your own past decisions by what
followed them, not by how sure you felt."*

**That is a description of the market's direction, not of the model.** Correctness on this
account is mostly the trading day's:

| day | scored | correct | rate |
|---|---|---|---|
| 2026-09-28 | 65 | 63 | **96.9%** |
| 2026-09-29 | 64 | 17 | **26.6%** |
| 2026-09-30 | 47 | 40 | **85.1%** |
| 2026-10-01 | 65 | 8 | **12.3%** |

On a down day a HOLD is right; on an up day the same HOLD is a `MISSED_ALPHA`. So the
bucket that looked 90.6% accurate was one day's base rate and nothing else. Within a day the
confidence strata are identical — 2026-09-28: 29/29 against 33/33; 2026-09-29: 0/3 against
1/44 — and the entire raw spread is between days.

```
Brier raw            0.3962
Brier day-adjusted   0.2705   <- expectation = that day's own base rate
```

**A third of the reported mis-calibration was the day.** Every HOLD bucket lands within one
point of its own days after the adjustment (+0.003 / −0.003 / −0.010), so on holds the
model's stated confidence carries essentially no information — but it does not run
backwards.

`calibrate` now computes each day's base rate, a day-adjusted Brier, and a per-bucket margin
against the days a bucket appears on, and the verdict quotes those. The live report now
reads:

> MIS-CALIBRATED: Brier 0.396 against the 0.25 a constant 0.5 claim scores, so the stated
> confidence adds no usable information. Brier 0.271 against the 4 day(s) those decisions
> were taken on, so 0.126 of it is the market's direction rather than the model's. … The
> worst bucket against its own days is −19.0%

**The one adverse finding that survives the control** is on trades: the 0.6-0.8 bucket runs
19 points under the days it appears on. That is now the claim the report makes, and it is
the one that would ever justify touching `min_confidence`.

The lesson the model reads is gated on that margin being materially negative, so a day
effect can no longer be taught to it as a character trait. With one day of evidence the
adjusted figures are left `None` rather than silently equal to the raw ones — the control
needs something to control for. Six new tests pin the distinction, including that a bucket
which underperforms its own days is still reported, so the fix is not only a deletion.

`make verify`: 52 classes, 0 failed, 1395 executions across 62 files; 886 pytest pass. The
daemon was restarted onto the new code.

### The next question, left open on purpose

**Should `min_confidence` exist at all?** 209 of 241 scored decisions clear the gate, one
refusal in 1179 cycles, and HOLDs return approved before the check — so it is a threshold
that has barely bound, while the stratum that is genuinely worse than its days is the most
confident *trades*. Changing a risk-adjacent gate needs its own experiment with its own
falsifier; fixing a measurement is not a reason to do it in the same change.

**Answered later the same day: keep it at 0.5.** Its one firing was correct, and raising it
blocks sets that underperform their own days. See the dated entry below.

`make verify`: 52 classes, 0 failed, 1395 executions across 62 files; 886 pytest pass.

## 2026-10-03 — the broker's activity history is a fragment, and said nothing

The open question above was *"should the agent manage account positions it did not open"*.
Paging the broker's own activity history to exhaustion answers the measurement half of it:**6000 fills back to 2025-10-22**, of which 279 fall in the last nine months, against a
52-share sell with no lot. So the basis for those shares is *recoverable in principle* — the
account was simply being read through a window that did not reach back to them.

The other half needs no measurement. The Guardian already refuses any SELL exceeding what the
agent itself bought — its comment cites this exact 52-share sell — and **9 of the 9 sells
since that rule landed are covered by its own lots**. The situation cannot recur; the 29
shares are a historical fact, not a live exposure. So nothing is built for it: neither the
broker-history pricer nor a new position-ownership layer.

### What the paging turned up instead

Paging revealed a second thing, in the path every PnL figure is computed from.
`BrokerEvidenceProvider` makes **one** request and the page limit is the whole fetch. The
broker caps a page at 100 (`tried to set the page size to 1000, but the maximum is 100`) and
says nothing about the rest:

```
window                          ingest  broker holds  missing_reasons
  the daemon's 1-day window            0             0  ()
  `evidence ingest`, 30 days          43            43  ()
  two months                           43            43  ()
  four months                          66            66  ()
  nine months                         100           279  ()   <-- SHORT, and silent
```

The ledger was told it held the whole of a nine-month window while holding 100 of 279 fills.
That is a confident number computed from a fragment — the same failure the journal's own
rotation had, and the same response: **say so, and do not build the buffer in anticipation.**
Every window this code requests is far under the limit, so the fix is a disclosed limitation
rather than paging. A full page now lands in `missing_reasons`, which `_pnl_evidence` copies
into `PnLEvidence` and the doctor prints, and the batch reports `PARTIAL` rather than
`SUCCESS`.

Measured before the limit was acted on, which is what makes "do not page" a measurement
rather than an assumption: the daemon's 24-hour window returns 0–20, `minictrl evidence
ingest`'s 30 days returns 43, four months returns 66. Paging would buy nothing any current
caller needs.
Three tests pin the behaviour: a full page is reported as a fragment; a window under the
limit is not questioned at all, because a warning that fires on every ordinary pass is one
nobody reads; and the flag resets per ingest, so a wide window cannot follow every later
narrow one — the same latching bug class as the unparameterised `get_portfolio_history`
fallback already pinned in that file.
`make verify`: 52 classes, 0 failed, 1401 executions across 62 files; 889 pytest pass.

## 2026-10-03 — the confidence gate fired once, it was right, and raising it is measured-harmful

The question above was answered by measurement, and the measurement had to be done twice,
because the first pass produced a clean and wrong answer.

`Guardian.review` approves a HOLD before the confidence check, so the gate's population is
every non-HOLD decision — from all three decision sources, not just the current one:

```
cycle records: 1179
  decision_source: baseline 852 | llm 314 | fallback_policy_engine 13

trades (the only decisions the gate can see): 114
  llm 56 | baseline 51 | fallback_policy_engine 7

refused by the confidence gate, any source: 1
  2026-06-09T12:40:03  SELL 54  confidence 0.30  source baseline
  -> counterfactual verdict: FALSE_TRADE
```

**The gate's one and only firing was correct.** It blocked a baseline SELL scored as a
`FALSE_TRADE` — 0% correct on a day whose base rate for scored decisions was 22.2%, so it
removed a decision 22 points worse than its own day. The previous entry's "one refusal in
1179 cycles" was exactly right.

What made the first pass wrong was scoping it to the LLM. Among the LLM's 56 trades the gate
has refused **none**, and the reason is a fact about the model rather than about the gate:

```
LLM trades: 56
  below 0.5: 0    at or above: 56
  confidences seen: [0.5, 0.55, 0.6, 0.65, 0.7, 0.75]
  scored: 45 -> 31 correct (68.9%) against a 68.9% day expectation, margin -0.0%
```

The model's lowest stated trade confidence is exactly the threshold, so no LLM decision can
reach it. A measurement scoped to the decision source that happens to be current will always
find that source innocent of the guard; the 852 baseline and 13 fallback decisions sitting in
the same journal under the same threshold are what gave the gate its one real use.

### What every setting would do

All 114 trades, 76 scored, each blocked set compared against the base rate of the days it
appears on:

| threshold | blocks | blocked right | blocked wrong | day-adjusted gain |
|---|---|---|---|---|
| 0.00 | 0 | 0 | 0 | +0.0% |
| 0.30 | 0 | 0 | 0 | +0.0% |
| **0.50 (current)** | **1** | **0** | **1** | **−22.2%** |
| 0.55 | 1 | 0 | 1 | −22.2% |
| 0.60 | 7 | 3 | 4 | −4.3% |
| 0.65 | 37 | 24 | 13 | −2.3% |
| 0.70 | 55 | 39 | 16 | −3.8% |
| 0.75 | 73 | 48 | 25 | −0.5% |
| 0.80 | 73 | 48 | 25 | −0.5% |

**0.50 is the only setting that blocks anything, and what it blocks was wrong.** Every
setting above it blocks sets that underperform their own days — trading a 22-point
underperforming decision for sets that are 4 to 0.5 points underperforming, and far more of
them. The gate is kept, and the reason is this table rather than the general prudence of
having a guard.

The Guardian is unchanged. What the evidence demands is that nobody turn the knob on the
strength of it looking inert, so the measurement went where an operator would look first:
`configs/paper.env.example` now states the gate's one firing and what raising it costs, and
`calibration`'s docstring no longer implies a filter with no record behind it.

No test was added, because there is no code path here to pin — the claim is about runtime
state and a fixture would assert nothing the journal does not. The falsifiers are live: a
firing that blocks a `GOOD_TRADE`, an LLM trade below 0.5, or a threshold above 0.5 whose
blocked set beats its own days would each make this entry wrong.

`make verify`: 52 classes, 0 failed, 1401 executions across 62 files; 889 pytest pass.
## 2026-10-03 — the loop paused every candidate at cycle 5 of the 13 it was guaranteed

With the confidence gate settled, the next question came from the record rather than a
queue: **can this loop actually learn which strategy works?**

> **Every figure in this entry is wrong, and the correction is at the end of this file.** The
> block below was measured by scanning `journal.jsonl` alone; the journal is rotated, so it
> covers about half the record. Corrected: 92 lifecycle transitions across 45 strategies (not
> 34 across 17), 208 admission events across 142 (not 84 proposals and 24 admitted), 127
> strategies evaluated (not 107), and **eight** transitions to `ACTIVE` including one applied
> on 2026-09-30 — not zero promotions ever, and not zero ACTIVE strategies. The conclusion
> that candidates are starved survives on a stronger measurement (17 of 18 hit below the
> evidence gate of 10). Read this block as a record of what was believed, not what is true.

```
78 strategies proposed, 24 admitted, 107 with evaluation records
6485 offline validations across 107 strategies, 60.6 evaluations each
  INCONCLUSIVE_INSUFFICIENT_EVIDENCE  5280  (81%)
strategies that ever placed a trade:              11 of 78
strategies that ever reached 10+ scored decisions: 12 of 107
final lifecycle state of all 17 tracked strategies: PAUSED 17, ACTIVE 0
```

**Zero promotions, ever.** Every admitted candidate ends PAUSED, and 24 of the 34 pauses
share one reason: *"probation produced no exploration evidence."*

### Two numbers for one concept

A candidate is guaranteed **13** selected cycles before the probation queue moves on, and
the screen needs **10** informative decisions — 13 is what reaches 10, at the observed
0.75-0.91 informative decisions per selected cycle. But the probation-stage *verdict* fired
at **5**:

```python
StrategySelector:         min_probation_cycles = 13    # guaranteed before the queue moves on
StrategyLifecycleManager: min_active_cycles    = 5     # judged degenerate here
```

So a candidate with no trade attempt was called degenerate at cycle 5 of 13, having had less
than half its budget and no chance at the evidence gate. And the measurement says that is
exactly what happened:

```
strategies paused for "probation produced no exploration evidence": 12
  their max decisions ever: 5 5 5 5 6 6 7 7 7 7 8 9     <- every one below the gate of 10
strategies that reached >=10 scored decisions: 12
  of those, paused for no-exploration: 0                  <- none
```

**The rule fired only on candidates that were structurally incapable of being screened.** Not
one strategy with enough evidence was ever paused by it. It was not enforcing the gate; it was
pre-empting it.

### The change

One number, not a new mechanism. `PROBATION_CYCLES = 13` is now a module constant used as the
default by both classes, so the guarantee and the verdict cannot drift apart again, and
`_is_degenerate_no_exploration` requires the *probation* budget while a strategy is on
probation and `min_active_cycles` otherwise — so a promoted strategy that stops acting is
still caught at 5 cycles, which is the defect the guard was originally written for (D4).

`tools/audit_defects.py` had to change too, and the reason is worth recording: D4's check
forbade the literal `"PROBATION"` anywhere in the guard, as a proxy for "the guard must not
exempt non-probation strategies". A fix that legitimately needed to know a candidate's
lifecycle tripped it. The check now forbids the actual escape — `lifecycle != "PROBATION"` —
and the behaviour it was protecting is pinned by a test instead of by text.

Three tests: a 0-trade candidate at 12 cycles is not paused, the same candidate at 13 is, and
a promoted strategy that stops acting is still paused at 5.

### The cost, which is not the cost the code assumed

The selector's docstring priced a deep probation budget against "the one ACTIVE strategy"
trading nothing. **There is an ACTIVE strategy** — `tiny-fixed-size-001`, promoted 2026-09-30 —
and the correction below shows it was selected **0 times in the 146 cycles since**, because
probation has absolute priority and 18 candidates never let the queue empty. So probation
already consumes every cycle, and the extra spend is cycles that were not going to the
incumbent anyway: roughly 60 across the whole history, about 5%.

### What is not yet proven

Nothing here shows a strategy being promoted. It removes the thing that made promotion
unreachable; whether a candidate now clears the screen is a claim about the live record after
the daemon has run, and it is recorded as a falsifier in the plan rather than asserted. The
next thing the same measurement exposes: **9 of the 12 strategies that reached 10+ scored
decisions were never judged at all** — no lifecycle verdict was ever recorded for them.

`make verify`: 52 classes, 0 failed, 1404 executions across 62 files; 892 pytest pass.

## 2026-10-03 — correction: the previous entry read half the journal

The lifecycle entry above is wrong, and so are parts of the `min_confidence` entry's
framing of the same record. Both were measured by scanning `runtime/min_agent/journal.jsonl`
directly. **That file is rotated** — `journal.jsonl.1` holds another 10,587 events, and
`ARCHITECTURE.md` has said since the beginning that "rotations read as one history". The
authoritative reader is `JsonlJournal.read_all()`, which spans both.

What the full record actually says:

```
across both journal files
  lifecycle transitions: 92 across 45 strategies     (the entry above said 34 across 17)
  admission events:     208 across 142 strategies     (said 84 proposals, 24 admitted)
    ACCEPTED 68, REJECTED 140
  strategies evaluated: 127                          (said 107)

transitions
   46 PROBATION -> PAUSED      9 RETIRED -> RETIRED    9 PAUSED -> PAUSED
    9 RETIRED  -> PROBATION    8 PROBATION -> ACTIVE   8 PROBATION -> RETIRED
    1 ACTIVE   -> RETIRED      1 ACTIVE  -> PAUSED     1 ACTIVE  -> PROBATION
```

**"Zero promotions ever" was false.** Eight transitions to `ACTIVE` are on record. One is in
the current two-phase format — `tiny-fixed-size-001`, 2026-09-30T14:30, *"probation completed
on 6 cycles with broker-verified realized PnL +362.66"* — and seven predate the phase field.
The most recent promotion of any kind is 2026-09-30; the last lifecycle transition of any kind
is 2026-10-03.

**`tiny-fixed-size-001` was not hand-seeded and unadmitted.** It has 6 admission events (five
REJECTED on 2026-09-28, one ACCEPTED on 2026-09-30) and 5 lifecycle events: promoted
2026-06-12, retired 2026-06-16, re-adjudicated to PROBATION 2026-09-30 02:30, promoted again
2026-09-30 14:30 with both `decided` and `applied` phases. Its `lifecycle_reason` is `None`
because admission and the lifecycle manager write that field only on the paths that set it,
and its promotion reason lives in the journal. The audit's remediation table and the disk
agree; my scan was simply looking at one file.

### What survives, and what does not

The no-exploration pause is real and is the dominant transition: 36 transitions across 18
strategies. Its victims' evidence counts:

```
max decisions those 18 strategies ever accumulated:
  5 5 5 5 5 5 6 6 6 6 7 7 7 7 7 8 9 43
```

**17 of the 18 never reached 10 decisions** — the gate the offline screen refuses to judge
below — so the starving finding stands, and with it the change made in `e9317a6`. The 18th is
the exception the previous entry denied existed: 43 decisions and 43 scored, all of them
HOLDs, zero trade attempts. The screen passed it and the degenerate rule paused it, which is
consistent rather than contradictory — `trade_attempts == 0` is what "no exploration evidence"
means, and 43 scored HOLDs are not exploration. So the honest count is **17 of 18 starved,
1 correctly judged**, not "18 starved, none with evidence".

### The cost claim was wrong in a way that strengthens the change

The previous entry argued a deeper probation budget "is currently paid by nothing" because
there was no ACTIVE strategy. There is one — `tiny-fixed-size-001` — and the measurement says
something better:

```
146 cycles since it was promoted on 2026-09-30
  times selected: 0
  strategies actually selected: fixed-size-sell-005, fixed-size-probe-0001,
    trend-follow-buy-001, trend-follow-20260613-001, trend-follow-buy-010 ...
```

**Probation has absolute priority in the selector, and with 18 PROBATION candidates the ACTIVE
strategy has not been selected once in 146 cycles.** So probation already consumes every
cycle, and lengthening a candidate's budget from 5 to 13 costs nothing that was not already
being spent. The loop's real shape is one dormant ACTIVE strategy and 46 candidates paused on
the way to a promotion that has happened exactly once.

### The lesson, recorded where it will be read

`ARCHITECTURE.md` states rotations read as one history, and `ARCHITECTURE.md:300` is a
correction on this same point ("the journal has no rotation" was wrong). It is stated twice and
still got repeated here, because scanning one file is the path of least resistance when the
question is "what is on record". **Measure the journal through `JsonlJournal.read_all()`**,
or say which file was read next to the number.

## 2026-10-04 — probation was budgeted against a quantity no candidate could reach

`e9317a6` made the probation budget and the probation verdict one constant. That was
necessary and it was not sufficient, because **both numbers were being compared against the
wrong quantity**, and that is the actual reason this loop has never promoted a candidate
since 2026-09-30.

`result.cycles` — which `_needs_probation` and the degenerate guard both read — comes from
the reflection window:

```
reflection_window = 50  (config.py:65)
last_n(50) spans 2026-10-02T11:03 -> 15:58, about five hours
  11 strategies appear in it
  cycles per strategy: 7 6 6 5 5 5 4 4 3 3 2 0 0
  max it can hold for any one strategy: 7
  probation budget compared against it: 13
```

So the budget was unreachable by construction. Measured consequences:

```
PROBATION candidates on disk: 18
  still owing probation: 18 (every one), 223 cycles owed
ACTIVE strategies: 1  (tiny-fixed-size-001)
  selected in the 146 cycles since its promotion: 0
lifetime cycles served, from the full record:
  42 strategies ever selected, max 44, and 9 have served 13 or more
```

`StrategySelector.select` returns from the probation queue whenever any candidate is
actionable, and considers the incumbent only otherwise. With 18 candidates that can never
leave probation, **absolute priority held forever and the one promoted strategy never
traded.** Nine strategies have genuinely served their budget and none of them could be
released.

### The change

`StrategyResult` and `StrategyEvaluation` carry `cumulative_cycles` — cycles served across
the whole journal — and exactly two rules read it, because exactly two rules mean "service
owed": `_needs_probation` and the `PROBATION` branch of `_is_degenerate_no_exploration`. The
daemon derives it from `journal.read_all()`, which this cycle already calls eight times
(lines 481, 549, 560, 833, 839, 856, 999), so it adds no new read.

Everything else keeps the windowed number on purpose: failure and rejection *rates* are
recent-reliability judgements, `score` is a window judgement, and the exploration floor is
defined in windows. A test pins that a rate cannot become a lifetime ratio.

Not changed: `reflection_window` stays at 50. Making 13 reachable per strategy would need
~234 cycles, about a day and a half of wall time, and would make every score that stale —
trading one wrong number for a worse one.

### What this does and does not fix

It fixes the instrument, and one candidate is released immediately:

```
candidates owing probation   before 18   after 17
released now: trend-follow-buy-010, 15 lifetime cycles
```

The queue is still 17 deep because **16 of the 18 candidates have never been selected at
all** — lifetime 0 to 6. That is serving capacity, not measurement, and it is now the
binding constraint: 9 of the 17 are actionable in the recent price range and need 13 cycles
each, about 117 cycles against an observed ~36 cycles a day, so roughly three days of
probation before the incumbent can trade at all.

**Not claimed:** that any strategy is promoted or that the incumbent trades. Both are claims
about the live record after the daemon has run, and both are recorded as falsifiers in
`docs/history/plans/2026-10-04-probation-measured-against-a-window.md`.

While making this change an edit to `models.py` silently deleted `StrategyEvaluation.
rejected_orders`, because the match omitted a line between two identical anchors. The suite
caught it in the same run; it is recorded because the failure mode — an `oldString` that
matches while dropping a field — is invisible in review and only a test finds it.

`make verify`: 52 classes, 0 failed, 1408 executions across 62 files; 896 pytest pass.

## 2026-10-04 — the admission gate asked whether a candidate was good, never whether it could be used

With probation reachable again, the next question was why the queue never drains. The
arithmetic answers it.

```
market-open cycles:            716
  of which probation received: 92%
candidates serviceable:         716 x 0.92 / 13 cycles each  =  51
candidates admitted:                                            68
net queue growth:                                               +17
```

Per market day: capacity is about 4.6 candidates, and admission ran 8–15 on the days it ran.

| day | dow | cycles | mkt-open | admitted | serviceable | net |
|---|---|---|---|---|---|---|
| 2026-09-28 | Mon | 69 | 69 | 12 | 4.9 | −7.1 |
| 2026-09-30 | Wed | 61 | 59 | 15 | 4.2 | −10.8 |
| 2026-10-01 | Thu | 66 | 65 | 8 | 4.6 | −3.4 |
| 2026-10-02 | Fri | 65 | 65 | 10 | 4.6 | −5.4 |
| 2026-10-03 | Sat | 0 | 0 | 4 | 0.0 | −4.0 |
| 2026-10-04 | Sun | 0 | 0 | 3 | 0.0 | −3.0 |

**7 candidates were admitted across a weekend**, when there is no market and therefore no
capacity to evaluate anything.

The 92% is measured rather than assumed, and measuring it corrected an error made earlier in
this same session: reading each strategy's lifecycle *now* said 126 of 146 cycles went to
PAUSED strategies, which would have meant the selector was trading strategies it had already
judged. Reconstructing the lifecycle of the selected strategy **at each cycle** puts it at
134 of 146 in PROBATION. `eligible` does exclude PAUSED and RETIRED; the earlier number was
current state read as historical state.

### The change

Every existing refusal in `StrategyAdmission` is candidate-intrinsic — duplicate id, price
anchored far from the market, curriculum progression, behavioural twin. None asks whether the
loop can use the candidate. One more refusal now does, in the same shape as the others:

```
live probation backlog: 17        cap: 13 (PROBATION_CYCLES, reused not duplicated)
17 candidate(s) are in probation and still short of the 13-cycle budget, which is the cap;
admitting another cannot be evaluated and would further delay the strategies already
waiting. Let the queue drain first.
```

The cap is in candidates rather than cycles-per-day so it does not encode this machine's
5-minute interval into what is otherwise a capacity rule. The backlog counts strategies still
*short of the budget*, not `PROBATION` files: a candidate that has served its 13 cycles
leaves the queue while its lifecycle still reads PROBATION, so counting files would pin the
backlog at every candidate ever admitted and the gate could never reopen. Four tests pin that
distinction, the boundary, and that a fresh library refuses nothing.

### What this does and does not do

It stops the queue growing. It does **not** clear the 17 already waiting, so the ACTIVE
strategy still waits roughly 17 / 4.6 ≈ 3.7 market days, and nothing here should be read as
making the loop profitable. The sequence is: the deficit stops compounding, the backlog
drains, and only then does the incumbent get cycles. Whether that happens is a live
measurement, recorded as a falsifier in
`docs/history/plans/2026-10-04-admission-outruns-serving.md` rather than asserted here.

`make verify`: 52 classes, 0 failed, 1412 executions across 62 files; 900 pytest pass.

## 2026-10-04 — two more windowed numbers answering cumulative questions, one of them live

Completing the probation-service fix turned up two more sites reading the reflection window
where they needed the journal. The second one is the valuable finding, because the daemon
exercised it within minutes of the restart and got it wrong in the open.

### The third site: the probation verdict gate

```python
if strategy.lifecycle == "PROBATION" and result.cycles >= self.min_active_cycles:
```

This gates the whole probation verdict block — degenerate pause and both promotion paths sit
behind it. Left on the windowed count, a candidate that has served its budget but holds few
window cycles receives **no verdict at all**: not a refusal, an absence. It now reads
`cumulative_cycles`, like the other two service questions.

### The fourth site: the daemon paused a candidate that had earned promotion

```
2026-10-04T11:24:13  trend-follow-buy-010  decided -> PAUSED
                     "no exploration evidence over the evaluation window"
```

That candidate is the only one that had earned a ruling: **15 cumulative cycles, 10 scored
decisions, `PASS_SCREENED`**. Over its lifetime it was selected 15 times and made **one
BUY**. The pause fired because `trade_attempts` is windowed, and its single BUY fell outside
the last 50 records.

"Did this candidate ever try to trade?" is a lifetime question, and a five-hour window
answered it. Reproduced against live data:

```
trend-follow-buy-010: lifetime cycles=15  lifetime BUY/SELL=1
  its recorded result: window cycles=4  window attempts=0  cumulative=15

  windowed attempts (what the daemon used): degenerate = True
  lifetime attempts (the fix):              degenerate = False
```

`cumulative_trade_attempts` now sits beside `cumulative_cycles`, from the same journal read,
because both answer the same question — how much has this candidate been given, and what did
it do with it. The windowed `trade_attempts` stays on the *promoted*-strategy path, where
"has stopped acting" genuinely is a recent-behaviour question.

A candidate that never traded is still paused: lifetime attempts is 0 for it, and a test
pins that so this is not a licence to keep anything.

### The full distinction, now stated once

| reads `cumulative_cycles` / `cumulative_trade_attempts` | reads the windowed count |
|---|---|
| `_needs_probation` | `failure_rate`, `rejection_rate` |
| `_is_degenerate_no_exploration` (PROBATION) | `_is_degenerate_no_exploration` (promoted) |
| probation verdict gate | `score`, `exploration_floor_cycles` |

**Ruled out along the way**, by running the real code rather than inferring: the offline
screen and the counterfactual ledger agree. `fixed-size-buy-001` looked like it had 21
informative decisions and a screen count of 2; running `offline_validation.validate` on the
real journal gives **21 scored, `PASS_SCREENED`**. The discrepancy was in my extraction. Five
strategies pass the screen, and the promotion gate is satisfiable — it requires `scored >= 10`
plus `PASS_SCREENED` and returns `None` (allow) otherwise.

Also measured and found *not* to be a defect: 7 of 17 queued candidates are dormant at every
observed price because their TREND_FOLLOW band contains the market. The selector documents
this as price-dependent and reversible, and the backlog of 17 is honest — those candidates
do eventually need service.

`make verify`: 52 classes, 0 failed, 1416 executions across 62 files; 904 pytest pass.

## 2026-10-04 — the 18 strategies the superseded rule paused, measured and left alone

Replacing the windowed probation rule raises an obvious question: what about the 18 strategies
it paused? Measured before acting, because the answer determines whether a repair pays.

```
the 18 paused with reason "probation produced no exploration evidence"
  screen verdicts:  17 INCONCLUSIVE_INSUFFICIENT_EVIDENCE, 1 REJECT_POOR_DECISIONS
  with a PASS_SCREENED verdict:  0
  that ever attempted a trade:   1  (and that one is the REJECT, on 43 scored decisions)
  lifetime cycles when the rule fired: 43, 9, 8, 7, 7, 7, 7, 7, 7, 6, 6, 6, 6, 5, 5, 5, 5, 5
```

**Re-adjudicating them would produce zero promotions.** Seventeen would get no ruling at all —
5 to 9 cumulative cycles against a budget of 13, so neither degenerate nor promotable — and one
would be retired on a verdict it already carries. And every one of them would re-enter a
probation queue that is **already over its cap**, 17 against 13, so the repair would deepen the
deficit it was meant to remedy.

`PAUSED` is terminal in `_review_one`, so nothing revisits them automatically. That is
acceptable for a measured reason rather than a hopeful one: the strategies the superseded rule
excluded are the ones with nothing to offer — seventeen never traded and cannot be screened,
and the eighteenth traded once in 43 cycles and its own screen rejects it.

The audit trail needs no repair. Each pause is a journalled `STRATEGY_LIFECYCLE_UPDATED` with
a timestamp and a reason, and the rule that produced it is in git history, so a reader can see
both the decision and the rule it came from.

**What would change this answer:** one of the eighteen earning `PASS_SCREENED`, or the capacity
cap being raised. Neither holds today. Recorded so a later session does not read the stale
pause reasons as a defect to fix.

The same reasoning retires the narrower question of `trend-follow-buy-010` specifically. It is
the one candidate that had earned a ruling — 15 cumulative cycles, 10 scored decisions,
`PASS_SCREENED`, one lifetime BUY — and the current rules give it **no ruling**: not degenerate
any more, but not promotable either, because its evidence is ten correct *holds* and one BUY.
That is what the `submitted_orders <= 0` guard exists for. Its pause cites a rule that no longer
exists, and it stays paused anyway, because nothing in the current rules would release it.

`make verify`: 52 classes, 0 failed, 1416 executions across 62 files; 904 pytest pass.

## 2026-10-04 — the honest answer to "does any of this beat holding SPY?" is: not yet, and it is close

Every change this session made the loop *able* to learn. None of them is evidence that it
learns anything worth trading. That is the measurement that matters, so it is taken here
rather than left implied.

Broker-verified realized PnL, the whole recorded life of the system:

```
strategy_realized_pnl:  tiny-fixed-size-001 +366.03
                        fixed-size-buy-001    +146.94
                        trend-follow-buy-001  +81.36
                        total                 +594.33     across 35 closed lots
orders submitted: 62      shares filled: 116      submitted notional: $88,342
three strategies have ever produced verified PnL; all three are now PAUSED or RETIRED
```

And what the market did over the same window:

```
2026-06-09 -> 2026-10-02, 1179 cycles
SPY                739.24 -> 769.58   = +4.10%
account equity     99,675.71 -> 99,342.92 = -0.33%
```

The Guardian caps allowlist exposure at $20,000, so the comparable question is what $20,000 of
SPY would have returned: **about +$820**. The agent realized **+$594** on closed lots and
still holds the remainder. On the capital it was allowed to risk, that is *slightly worse*
than doing nothing, and the account's own equity line fell while SPY rose.

**A correction first.** An earlier pass in this session simulated "follow every non-HOLD
decision" and reported **+37.22%** against +4.10% for holding. That number is wrong and was
never committed: the simulation bought 1082 shares — about $811,000 — against $73,923 of
starting cash, so it spent money the sleeve did not have and reported the leverage of an
unbounded wallet. Cash-constrained or not, it is not a measurement of this system. The
broker-verified figures above are the ones to use, and they come from the ledger the system
maintains rather than from a re-derivation.

### What this does and does not say

It does **not** say the loop is pointless. It says that after 1179 cycles and 62 orders the
demonstrated edge is $594 on a $99k account, which is inside the noise of a handful of lots,
and that the honest benchmark is holding SPY rather than the zero the project started from.

The lifecycle work this session is upstream of that number, not a substitute for it:
`min_confidence` measured, probation made reachable, admission capped to what the loop can
evaluate, and the four service questions moved off a five-hour window onto the journal. What
those buy is the ability to reach a verdict. Whether a verdict is worth acting on is the next
question, and it has not been answered.

**The falsifier for all of it** is unchanged and still unmeasured: a strategy promoted out of
probation on its own evidence, then trading. No candidate in the library can be promoted today
— the 17 queued ones hold 0 to 9 scored decisions against a gate of 10 — so the earliest
honest read is after they have been served, about two market days at the measured rate.

## 2026-10-04 — the model accounts for 5% of the profit, and that is the finding that matters

The previous entry asked whether a promotion verdict is worth trading. The attribution
answers it without needing a verdict at all: join each closed lot back to the decision that
opened it.

```
35 closed lots, 35 shares, realized +594.33   (+16.98 per share)

realized PnL by the decision source that opened the lot
  baseline    23 lots   +564.39    95.0%
  llm         12 lots    +29.94     5.0%
```

**The language model contributed $29.94.** The deterministic baseline contributed $564.39.
The three strategies that ever produced verified PnL — `tiny-fixed-size-001`,
`fixed-size-buy-001`, `trend-follow-buy-001` — are fixed-size buyers whose decisions are
deterministic by construction, which is why the profit is almost entirely baseline-attributed.

And against the benchmark, on its own entry prices:

```
mean fill price 757.13   realized +2.24% per share deployed
SPY 739.24 -> 769.58     the market delivered +4.10% over the same window
```

Two honest readings, because they differ and only one of them flatters the system:

- From the **window start** (739.24) the market returned +4.10% while the agent realized
  +2.24% per share. But the agent's fills averaged 757.13, so it bought well after the move
  began; holding from its own entries would have returned +1.64%.
- So against **its actual entries** it did modestly better than holding, +2.24% against
  +1.64% — about 0.6 points, on 35 shares.

Thirty-five shares over four months is a sample that cannot separate skill from noise. The
defensible statements are the ones that do not need it to be larger: **the model's own
contribution is $29.94, which is indistinguishable from zero**, and the system's demonstrated
edge is $594 on a $99k account.

### Why this reframes the priority

Everything this session fixed makes the loop able to *reach a verdict* — the probation budget
measured against cumulative service, admission refused when the queue is over capacity, the
four service questions moved off a five-hour window onto the journal. None of that touches
the number above, and none of it will until a candidate with an actual edge exists to be
promoted.

The loop's job is to find a strategy that beats holding. On this record it has not found one,
and the one component that could have found one — the model — supplied 5% of a small profit.
So the next question is not another lifecycle rule. It is whether the decision sources
produce anything worth acting on at all, and the cheapest honest test of that is the
counterfactual ledger this system already maintains: across all 314 LLM decisions, 241 scored,
the calibration report's own verdict is `MIS-CALIBRATED` and the day-adjusted margin is −0.0%.

**That is the honest summary of where this project stands.** The infrastructure is sound, the
audit trail is complete, the risk boundaries hold, and the demonstrated edge is $29.94 from
the model and $594 from deterministic buying in a rising market.

## 2026-10-04 — the evaluation instrument cannot measure the project's success metric

Measuring every decision source against its own day expectation, and against holding over
the same horizon, produces one clean result and one structural finding.

```
all scored decisions, day-adjusted against the base rate of the days they came from
  source                     scored  correct    rate   day-adj margin
  baseline                      344      241   70.1%          -0.2%
  llm                           241      128   53.1%          +1.8%
  fallback_policy_engine         13        7   53.8%         -28.0%
```

**No source demonstrates an edge.** The baseline is exactly at its day expectation. The LLM is
+1.8 points, which on 241 decisions with overlapping 24-hour horizons is not a sample that can
carry that claim. The fallback engine is clearly negative, though n=13.

### The structural finding

Comparing every non-HOLD decision against holding over the identical window gives an excess of
**−0.05% for all three sources — the same number, to the basis point.** That is not a
coincidence, and it is not a bug. It is what the instrument measures:

```python
gross = (future_price - price) / price * 100.0      # counterfactual.py:262
net   = gross - assumed_cost_pct
```

`price` is the quote at the decision and `future_price` the quote at the horizon, so a trade's
return **is** the return to holding from that same instant, minus the assumed round-trip cost.
A trade can therefore never beat holding in this ledger, by construction. The verdicts —
`GOOD_TRADE`, `FALSE_TRADE`, `GOOD_HOLD`, `MISSED_ALPHA` — answer a different and legitimate
question: *given the direction the market took, was this action right?*

**So the loop promotes strategies on decision quality, while the project's success metric is
beating holding, and nothing in the system measures the second.** That is the mismatch worth
recording, and it explains why the work this session produced — reachable probation budgets,
capped admission, cumulative service accounting — cannot move the number in the previous
entry. Those fixes make the loop able to reach a verdict. The verdict it reaches is about
decision quality, and decision quality at +1.8% day-adjusted is not an edge over SPY.

### What follows, and what deliberately does not

The honest consequence is that a promotion, when it comes, will not by itself be evidence of
profitability — and the cheapest way to close that gap already exists in data the system holds.
Every closed lot carries `buy_price`, `sell_price`, `quantity` and timestamps, so "what would
holding this capital over this window have returned" is arithmetic on the broker's own record,
not new infrastructure.

That comparison is deliberately **not** built here. The portfolio-level number already answers
the question for the system as a whole — $594 realized against roughly +$820 for holding the
$20,000 the Guardian allowed it to risk — and a per-strategy benchmark is worth adding when
there is a promoted strategy to attribute one to. Today there is none: all 17 queued
candidates hold 0 to 9 scored decisions against a gate of 10.

**What would change this conclusion.** A source whose day-adjusted margin is positive by more
than the noise on non-overlapping windows, or a promoted strategy whose realized PnL exceeds
holding on the same capital over the same window. Neither exists on this record.

**One methodological note for whoever measures it next:** the horizons overlap. A 24-hour
window scored every 5 minutes is not 241 independent observations, and any significance claim
built on treating them as independent would be wrong. The day adjustment in `calibration.py`
partly handles this; a per-strategy benchmark should not assume independence it does not have.

## 2026-10-04 — the project can now see the only number it was aiming at

The previous two entries established that no decision source shows an edge and that the
instrument judging strategies cannot see the project's success metric. This makes that metric
visible, per strategy, on the object the system already reports.

```python
# PnLEvidence, computed from the broker's own closed-lot record
strategy_deployed_capital:      entry cost of each strategy's closed lots
strategy_return_pct:            realized over that capital
market_return_pct:              the same instrument, same window, same price source
strategy_excess_vs_market_pct:  the difference, per strategy
```

The market return comes from the prices the evaluation already holds, not a second fetch, so
the benchmark and the lots cannot rest on two sources that disagree. Where no price series
covers the window the fields stay `None` and `missing_reasons` says so, rather than a
benchmark being invented.

`doctor` now reports it, and it is a WARN because every strategy that has ever produced
broker-verified PnL is behind the market:

```
[WARN] pnl vs holding   3 of 3 strategy/ies behind the market:
        tiny-fixed-size-001  =+3.81% vs market +4.10%  (-0.29)
        trend-follow-buy-001 =+2.12% vs market +4.10%  (-1.98)
        fixed-size-buy-001   =+1.92% vs market +4.10%  (-2.18)
```

Those figures were computed by hand from the broker record before this code existed, and the
emitted values reproduce them exactly — which is the check that matters, because a benchmark
assembled independently of the code it judges is the only kind worth having.

**A correction to the first version of those numbers**, and to the denominator behind them.
They were originally computed on *cumulative turnover* — the sum of each closed lot's entry
cost — and the field was named `strategy_deployed_capital`. Both were wrong. A strategy that
cycles one share fifteen times turns over fifteen times the capital it ever risked, so
turnover understates its return; here it understated the largest strategy by 0.5 points, and
then compared a turnover-based return against a market return computed on capital held once.
Two different denominators, so the "excess" was not an excess of anything. It happens to have
kept the same sign, which is luck rather than a defence.

The denominator is now **peak exposure** — the cost basis of the largest position the strategy
held at one time, replayed in timestamp order across closed and still-open lots — and the field
is named `strategy_peak_exposure` for what it is. Corrected totals: $21,077 at risk, +$594.33
realized, **+2.82% against the market's +4.10%, an excess of −$270.86**. The conclusion that
every strategy is behind the market survives the correction; the magnitude does not.

**What this deliberately does not do.** It does not change the promotion gate. Requiring a
strategy to beat holding before promotion is a risk-adjacent policy change, and it is only
meaningful once the loop has promoted something and that something's number has been seen.
Today the gate's criterion has produced three strategies, and all three lost to the market —
that is now a fact in the report rather than an inference, and the decision about the gate
belongs to its own experiment.

The $21,077 of peak exposure is the capital these strategies were actually entrusted with,
against the $20,000 the Guardian allows as concurrent exposure — so the aggregate is over the
limit only because the strategies did not all hold at once, and the Guardian's own bound is on
concurrent exposure, measured separately at about $19,793. Not a breach, and not reported as
one.

## 2026-10-04 — the shortfall is not yet a basis for changing the promotion gate

The gate change this was heading towards is not justified, and the reason is worth more than
the change would have been.

**Two defensible denominators, and they disagree per strategy.** The report compares each
strategy's return on peak exposure against the market over the whole record window. The
like-for-like alternative is to compare it against the market over *its own* active window —
the same capital, held over the period it was actually invested:

```
                                    whole-record window      its own active window
strategy                    at risk  mkt    excess          window   mkt    excess
tiny-fixed-size-001          9603  +4.10%   -$28.16         113d  +5.59%   -$170.36
fixed-size-buy-001           7641  +4.10%  -$166.71         106d  +2.84%    -$69.73
trend-follow-buy-001         3833  +4.10%   -$76.00         104d  +2.34%     -$8.16
TOTAL                       21077          -$270.86                        -$248.25
```

Neither is wrong. The whole-record denominator charges a strategy for periods it was flat;
the own-window denominator measures entry and exit timing only within the stretches it chose
to trade, and would flatter a strategy that trades exclusively in rising markets. The total is
robust — **−$248 to −$271 either way, about −$250 on $21,077 at risk** — but the per-strategy
attribution is not: `tiny-fixed-size-001` reads as near parity on one basis and as the worst
performer on the other, because it happened to trade through the strongest part of the market.

**So the code keeps the whole-record window** and this entry records the other, rather than
the report carrying two numbers where a decision needs one. Which basis is right is a policy
question about what the loop is optimising, not a measurement fix, and it should be settled
before it is baked into a gate.

**And the sample cannot carry the decision anyway.** Fifteen, sixteen and four round trips,
all inside one four-month regime in which the market rose. The per-lot distributions are tight
(`tiny-fixed-size-001`: min +0.12%, median +3.35%, max +5.36% over 15 lots) but tight
dispersion in a single rising regime does not distinguish skill from the regime — in a market
that rose, every buy-then-sell sequence is profitable by construction, which is the same
reason the benchmark rather than the per-lot distribution is the honest instrument.

**What would justify changing the promotion gate:** a strategy whose excess sits beyond the
dispersion, over enough round trips and enough distinct market regimes that the sign is not a
property of the sample. Nothing on this record is close to that, and the three strategies the
gate has produced were chosen by a criterion — decision quality, +1.8% day-adjusted — that has
never been shown to predict this number.

**What this does not say:** that the strategies are fine. They are behind the market by roughly
$250 on $21,077, and that is the honest summary of four months. It says the sample cannot
distinguish that from noise, so the correct response is to wait for evidence rather than to
change a risk boundary on the strength of it — the same answer the `min_confidence` gate got
when its single firing turned out to be correct.

## 2026-10-04 — two fixes confirmed on the live record, and one falsifier blocked on market hours

The changes earlier today are not only unit-tested. Two of them are visible in the journal, and
the third cannot be observed yet for a reason worth stating.

**The capacity refusal is firing.** Since it landed there have been three admission reviews,
and all three were refused for the reason it was built to give:

```
2026-10-04T11:54:22  17 candidate(s) are in probation and still short of the 13-cycle
2026-10-04T12:19:24  budget, which is the cap; admitting another cannot be evaluated
2026-10-04T13:03:18  and would further delay the strategies already waiting.
```

So the loop is proposing, being refused, and journalling why — which is the whole point of
putting the refusal in the existing admission path with its reason string, rather than in a
scheduler or a queue manager.

**The loop still reaches a verdict on real bars.** `tools/replay_self_evolution.py` over 847
real SPY bars: 847 cycles, 0 errors, 282 counterfactual evaluations, 78 offline validations,
and one applied ruling — the strategy accumulated 34 scored decisions and was retired on its
own evidence with the numbers in the reason (`34 scored decision(s), 2 correct,
correct_outcome_ratio 0.059`). That exercises the cumulative-service path end to end: service
accumulates, decisions get scored against real future bars, the screen judges them, and the
rules rule. It is still not evidence that any strategy has an edge, which the harness says
itself.

**The queue-drain falsifier cannot be observed yet.** Measured this morning:

```
cycle records: 1179   newest 2026-10-02T15:58:54   market-open: 716
probation backlog: 17   [17 when the cumulative fix landed]
```

The backlog is unchanged because **no cycle has run since Friday afternoon** — it is Sunday,
and the last cycle predates every fix made today. So the queue has not had an opportunity to
drain, and reporting it as "not draining" would be reading a closed market as a stalled loop.
The first honest read is after the 2026-10-05 session, and the command for it is the same one
used here: count PROBATION strategies short of `PROBATION_CYCLES`, and compare against 17.

That number is worth watching for a specific reason rather than in general: the backlog has a
**floor of 7**, because seven of the seventeen have TREND_FOLLOW bands containing every price
observed in the recent range and can never be selected until the market leaves those bands.
Seven is below the cap of 13, so the gate reopens on its own as the actionable candidates are
served — the floor is a floor on the *backlog*, not a reason the gate stays shut.

## 2026-10-04 — 147 cycle errors, and the one that costs a strategy

The record carries `error_count: 147` over 1179 cycles, which reads as a 12.5% failure rate in
the decision layer. It is worth knowing what those 147 actually are.

```
133  llm output format   HOLD carrying a quantity, confidence as 70 or 50 instead of 0.7,
                        rationale missing, symbol missing, no JSON object in the response
 12  in_flight_order    the double-submission guard refusing to resubmit an order whose
                        fill has not been confirmed
  2  ollama timeout     localhost:11434 read timeout, once in June and once on 2026-09-30
```

The 133 are almost all June: the last of that class is 2026-09-30, so the model's output
contract is now essentially clean and the decision layer's error rate is close to zero. That is
a real improvement in the record and nothing had measured it.

**The 12 are the double-submission guard, and the defect that charged it to strategies was
already fixed** — commit `3aafc18`, 2026-10-03, with the same measurement in its comment. All
12 cycles predate it (2026-10-02), so it is not a live bug, and I was one step from reporting a
fixed defect as a current one. Recording it because the *consequence* has not been cleaned up:

```
strategy                     cycles  errored  in_flight  genuine  screen verdict
fixed-size-probe-0001            16        3          3        0   REJECT (11 scored)
fixed-size-sell-005             17        3          3        0   PASS_SCREENED (12 scored)
fixed-size-sell-006              7        4          4        0   INCONCLUSIVE (0 scored)
trend-follow-20260627-001         5        2          2        0   INCONCLUSIVE (0 scored)
```

Every one of the four was paused for "error rate above lifecycle threshold" on **100%
guard-firing errors and zero genuine errors**. Three of them would be paused anyway on other
grounds — one is rejected by its own screen, two have no evidence at all.

**`fixed-size-sell-005` is the real cost.** It passed the offline screen on 12 scored
decisions, and it was excluded from selection on the strength of three cycles in which the
system correctly refused to double its position. That is the loop discarding the only
candidate on this record that both has a passing screen and no genuine errors.

**The repair cannot be done correctly yet, and doing it now would be worse than waiting.**
`PAUSED` is terminal in `_review_one`, so nothing re-derives it — but the reflection window is
frozen on the last 50 cycles, all from 2026-10-02, so it still carries those three errors.
Re-adjudicating today would pause it again for exactly the stale reason the fix removed. The
repair becomes correct once new cycles roll the window past 2026-10-02, which needs a market
session.

So this joins the queue drain as something the next session can settle, and both are blocked
on the same thing rather than on a decision.

## 2026-10-04 — the stage whose job is finding an edge has never been able to answer

The research layer is 827 lines with 440 lines of tests, deliberately isolated from production
— "Production must never import this" — and that isolation is enforced rather than merely
documented: `production-research-separation` passes, 4 research modules against 38 production
ones with no production import.

It has been run 26 times. It has returned a verdict 0 times.

```
26 trials, all on the same 467 bars with 3 folds
  out_of_sample_trades:  0 to 3          required_trades: 52 on every single one
  leakage violations:   0                stress survivors: 148 of 234
  verdict:               INSUFFICIENT x26
```

**The multiple-testing control is unsatisfiable.** `required_trades` is
`ceil(BASE_REQUIRED_TRADES × sqrt(trials))` = `ceil(10 × sqrt(27))` = 52. The reasoning behind
it is sound and documented: dividing a zero return threshold by the trial count is decoration, so
the trial count raises the *evidence* requirement instead, on a square-root schedule. But the
bar is compared against a quantity these strategy designs cannot supply — a buy-and-hold
`FIXED_SIZE` opens a position on 467 bars and does not close it, so it produces 3 out-of-sample
trades, not 52. The control is not too strict; it is unreachable.

**And it is self-reinforcing.** More specs tried raises the bar. A higher bar that cannot be met
produces INSUFFICIENT, which tells the search nothing, which searches more specs. The stage
whose documented job is `backtest → walk-forward OOS → robustness → multiple-testing control →
shadow → probation → promotion` cannot get past the multiple-testing control, so **nothing can
ever reach shadow or probation from research.**

One detail worth recording: the `research-trial-ledger` gate *passes* while reporting `0 passed,
26 failed`. It checks that every record is traceable and accounted for, which is bookkeeping, and
it prints the outcome honestly without treating "nothing has ever passed" as a defect. So the
number has been visible in the gate output this whole time and nothing read it as one.

**What this does not argue for.** It does not argue for lowering `required_trades`. That is a
multiple-testing control, and removing it to make a gate pass is the same trade this project has
already refused twice today — on `min_confidence` and on the promotion gate. The honest
statement is that the control is right and the *instrument* it measures in is wrong for these
strategy designs at this data volume.

The three real options are all experiments rather than fixes: express the control in a unit a
long-only strategy can move (folds and bars rather than trades), require strategy designs that
open and close positions so trades are available, or gather enough bars that a
trade-count-based control is reachable. Which one is right cannot be decided from a record in
which the stage has never once produced an answer.

**What would prove this wrong:** a single trial with a verdict other than INSUFFICIENT. There
is none, and the mechanism above says there could not be one.

### The arithmetic of an unreachable bar

Measured from the trial ledger's own rates rather than assumed:

```
kind             trials  bars  oos trades  trades/bar  bars needed for 52
FIXED_SIZE          13    467          36      0.00593            8,769
TREND_FOLLOW        12    467          22      0.00393           13,246
HOLD_BASELINE        1    467           0      0.00000                 -
```

**Correction, measured the next day: the 8,800-bar figure above is wrong, and so is the
remedy it implied.** `out_of_sample_trades` does not grow with the dataset at all. Fed the real
847-bar cache at four different lengths:

```
bars fed   folds   oos trades   required
      100      3             3         52
      200      3             3         52
      400      3             3         52
      847      3             3         52
```

`run_backtest` opens a position only when flat and force-closes it at the segment's last bar,
so an always-BUY rule makes **exactly one counted trade per test segment**. That makes
`out_of_sample_trades == n_folds` an identity, not an approximation. **Gathering more history
would not have fixed anything** — the claim that the bar "rises as sqrt(trials) while the
dataset is fixed" described a gap that does not exist in that form.

The real invariant is sharper: **the gate is satisfiable only when `n_folds >=
required_trades`, and `n_folds` was hardcoded at 3.** The module demanded 52 independent
out-of-sample bets and produced 3, so all 26 trials returned INSUFFICIENT for that reason
alone. Measured at 1, 10, 27, 52 and 60 folds on the same cache:

```
bars  n_folds   required  test_size  oos trades  verdict
 847        3         52        275           3  INSUFFICIENT
 847       27         52         30          27  INSUFFICIENT
 847       52         52         15          52  OVERFIT
```

At `n_folds == required_trades` the stage stops vetoing and returns a real verdict. It was not
failing to find edge — it was structurally prevented from reporting the answer it had.

## 2026-10-04 — audit: can this system run 7x24, fully autonomous, discovering and improving itself?

Asked directly, so answered directly with measured evidence rather than from intent. **No.**
Three of the four properties do not hold. One holds and is narrower than "validation".

### What is true

**Unattended 7x24 operation, and it is safe.** The daemon has produced 18,438 journal events
across 116 days, every hour including overnight, and is currently up and writing. Paper-only is
enforced at three layers rather than by convention: `ALPACA_BASE_URL` is the paper endpoint,
`MIN_AGENT_MODE=paper`, and `Guardian.review`'s first statement refuses any mode that is not
paper. No risk limit was breached on this record.

### What is false

**7x24 *validation* — false.** Validation means testing hypotheses against reality and learning
from the result. The stage that does that has never once returned a result:

```
research trials: 26      verdicts: INSUFFICIENT 26      usable verdicts: 0
```

Only 21 distinct days in 116 carry any activity, and no cycle has run since 2026-10-02. Also
worth stating plainly: **0 of 1179 cycles has a positive `filled_quantity`** — every one of the
62 orders sat at `pending_new` and was confirmed retrospectively by a reconciler, which is a
documented design choice but means the execution path has never confirmed a fill inline.

**Fully autonomous strategy exploration — partial.** Producing candidates works and is
autonomous: 380 curriculum proposals, 213 admission reviews, 68 accepted, no human in the loop.
Producing *evidence* about them does not, and that is the half that matters. Exploration here
generates hypotheses and never tests one.

**Self-optimization — false.**

```
transitions to ACTIVE, all time: 8
  six on 2026-06-11..06-18, two on 2026-09-30 (both tiny-fixed-size-001)
promotions since 2026-09-30: 0
times the promoted strategy was selected in the 146 cycles after it: 0
```

The last line is the damning one. Even a *successful* promotion does not lead to trading, because
probation has absolute priority in the selector and its queue never empties — so a promoted
strategy is starved by the very exploration it was promoted out of.

**Measurable improvement — false.** The model contributed $29.94 of $594.33 realized (5%). All
three strategies that ever produced broker-verified PnL are behind the market (−0.29, −1.98,
−2.18 points; −$270.86 on $21,077 at risk). No decision source shows a day-adjusted edge:
baseline −0.2%, llm +1.8%, fallback −28%.

### The two structural blockers, both measured

1. **The research verdict gate is unsatisfiable.** 52 out-of-sample trades required against 3
   achievable; ~8,800 bars needed at the best observed rate against an 847-bar cache; and the
   requirement rises as `sqrt(trials)` while the dataset is fixed, so the gap widens as the
   search broadens.
2. **The promotion chain cannot complete.** A promoted strategy is not selected while any
   actionable probation candidate exists, and the queue cannot drain, so promotion leads to
   nothing.

Underneath both: **there is no edge for the loop to find.** The infrastructure is sound and the
audit trail is complete, but the search space is empty and two gates make it impossible to say
so either way.

### What would have to change for the claim to hold

- Bound the search so `required_trades` stops rising, **or** express the multiple-testing control
  in folds-and-bars, which scale with the data, rather than trades, which scale with a strategy's
  design. Not lowering the bar — that is a multiple-testing control.
- Make probation yield to a promoted strategy, so a promotion actually means trading.
- Then run long enough to produce either an edge or a defensible null.

Neither is a bug fix. Both change what the project optimises, so both belong in a plan with
their own falsifier rather than in a maintenance pass.

## 2026-10-04 — both structural blockers from the audit are now closed

The audit found two gates that made self-improvement impossible by construction. Both are
fixed, and the fixes are verified against real data rather than asserted.

### Blocker 1: a promoted strategy never traded

`select` gave probation absolute priority and admission held the backlog at the cap, so the
queue was effectively never empty:

```
cap PROBATION_CYCLES 13 | backlog 17 | actionable at the last price 9 (53%)
P(no actionable candidate) = (1-0.53)^13 = 5.6e-05
```

5.6e-05 per cycle is not "rarely chosen", it is unreachable — and the queue could not be
drained, because serving a candidate is what lowers the backlog, which is what reopens
admission. One promotion existed; it was selected 0 times in the following 146 cycles.

`INCUMBENT_SHARE = 5` reserves one cycle in five for an ACTIVE strategy that declares an
action, counted on `sum(cumulative_cycles)` so it is stateless. Measured on the live library
over twenty selections:

```
with the reserved share:  16/20 discovery   4/20 tiny-fixed-size-001
without it:               20/20 discovery          incumbent never selected
```

### Blocker 2: the research stage could never return a verdict

The gate demands `required_trades` independent out-of-sample bets and the fold count was
hardcoded at 3, while `out_of_sample_trades == n_folds` is an identity — one contiguous
holding period per segment. 26 trials, 26 `INSUFFICIENT`, 0 verdicts, ever.

`n_folds` now defaults to `max(3, required_trades)`. **`required_trades` is untouched** — the
bar still rises as `sqrt(trials)`; this only provisions the evidence it counts, because a gate
nothing can pass is a veto and this repository already says that about the promotion gate. An
explicitly-passed `n_folds` below the requirement is honoured but records that the `INSUFFICIENT`
is structural, so the reason is on the record rather than inferred.

On the real 847-bar cache:

```
trials   required  folds  oos trades  oos return  verdict
      1         10     10          10     +0.076%  OVERFIT
     10         32     32          32     +0.009%  OVERFIT
     27         52     52          52     -0.002%  OVERFIT
     40         64     64          64     -0.012%  OVERFIT
```

**The stage now returns real verdicts, and the answer is `OVERFIT`** — these strategies do not
generalise. That is consistent with every other measurement taken: no decision source shows a
day-adjusted edge, and all three strategies with broker-verified PnL are behind the market.

### What is still not achieved

Both blockers were *gates*, and removing them does not create an edge. The honest position
after this work: the loop can now reach a verdict, act on a promotion, and have research
report a result — and on the evidence available all three say **no**. The next need is not
another gate but a search space with something in it, and at 847 bars a 52-fold segment is 15
bars, so each bet is short. Roughly 5,220 bars would give 52 segments of ~100 bars.

That is a data question, not a design one, and it is the only thing standing between this
project and a defensible null.

`make verify`: 52 classes, 0 failed, 1442 executions across 62 files; 917 pytest pass.

## 2026-10-04 — the search space contains nothing the loop can evaluate

With both gates open and all three saying no, the next question is the one underneath:
**is there anything in the search space to find?** Measured, the answer is no, and the reason
is the design space rather than the data.

```
the backtest supports three kinds: FIXED_SIZE, TREND_FOLLOW, HOLD_BASELINE
two of them trade, and both are long-only and effectively "enter once, hold"
```

**`FIXED_SIZE` cannot re-enter.** The BUY branch requires `position == 0`, and the rule says
BUY on every bar, so it opens once and holds to the end: **1 closed trade on 847 bars, and on
3,000 bars of a strongly oscillating synthetic series, still exactly 1.**

**`TREND_FOLLOW` anchors on the first bar of whatever window it is shown**, not on a fixed
level:

```python
anchor = bars[0].price
move = (price - anchor) / anchor
fired_action = "BUY" if move > 0 else "SELL"
```

So it is a *drift from the window start*, not a band crossing, and it fires once per window:

```
threshold   closed trades on the real 847-bar cache
    0.001                      4
    0.002                      1
    0.005                      1
    0.010                      0
    0.020                      0
```

The cache's largest drift from any window start is **+0.27%**. And every TREND_FOLLOW in the
live library is parameterised between **1.0% and 3.0%**:

```
threshold 1.0%: 4   1.2%: 2   1.5%: 8   1.8%: 3   2.0%: 16   2.5%: 3   3.0%: 9
```

**So all 45 TREND_FOLLOW strategies in the library cannot fire on the market they are
anchored to.** That is the same finding as "7 of 17 queued candidates are dormant", seen from
the other side: dormancy is not bad luck in the queue, it is the whole TREND_FOLLOW population.

### Correcting my own correction

Yesterday I recorded that the research gate "cannot be fixed by gathering more bars", on the
evidence that `out_of_sample_trades` stayed at 3 for 100/200/400/847 bars. That was correct
**at the pinned fold count of 3**, and wrong as a general claim. Two corrections:

- `oos_trades == n_folds` is not an identity. It holds when the holding period is at least the
  segment length, which is the case for these designs on this data. A `TREND_FOLLOW` at a 0.2%
  threshold does round-trip — 40 closes on 3,000 oscillating bars — and then
  `oos_trades` is no longer pinned to `n_folds`.
- **More data is a lever again**, now that the fold count is not pinned. Measured with the fold
  count derived:

```
 bars  folds  segment bars  oos trades  required
  847     52             15          21        52   INSUFFICIENT
 2000     52             38          26        52   INSUFFICIENT
 4000     52             76          41        52   INSUFFICIENT
 6000     52            115          64        52   OVERFIT
```

Longer segments let a round-tripping design fit more than one bet per segment, and the gate
opens at roughly **6,000 bars — about 7x the cache.** So the original "~8,800 bars" estimate was
right in magnitude and wrong in mechanism; what actually governs is segment length, not trades
per bar.

### What this leaves

Two honest statements, and they point in opposite directions from the ones this project has
been acting on:

1. **The library cannot be evaluated as it stands.** 45 of 64 strategies are dormant on real
   data, so probation budget is being spent on candidates that cannot produce a decision, let
   alone a verdict. The admission gate checks that a `reference_price` is near spot; nothing
   checks that the *threshold* is reachable.
2. **Refusing wide-threshold candidates would be wrong.** A strategy dormant this week may
   fire next week, so a reachability rule at admission would make the library a function of
   current volatility. The defect is not the threshold; it is that the search space has no
   design capable of taking more than one bet per segment.

The minimum thing a trading rule must be able to do, and the only thing none of these can do, is
**close and re-enter.** That is a capability, not a tuning change, and it belongs in its own
plan rather than in a threshold guard that would trade one permanent error for a temporary one.

## 2026-10-04 — the data constraint is gone, and the design claim above was wrong

### Real history at proper depth

`tools/fetch_replay_bars.py` has no page cap on the historical endpoint, so the 4-day cache was
a choice, not a limit. Fetching the window the gate actually needs:

```
cached 20448 real 5Min SPY bars -> runtime/min_agent/replay/SPY_5Min_2026-05-01_2026-10-03.json
  2026-05-01 13:30:00+00:00 .. 2026-10-02 23:55:00+00:00
  close 722.32 .. 769.86   high 779.37   low 714.99
```

20448 bars is **3.4x** the ~6000 the gate was estimated to need, and the range is 715.94-779.22 -
**8.84% peak-to-peak**, against +0.27% over the whole 4-day cache. So "the library's thresholds
of 1.0%-3.0% can never fire" was a statement about four days, not about the instrument.

### The gate opens on real data, and says OVERFIT

```
design                    closed  closed/bar  oos(f=52)   req  verdict
TREND_FOLLOW 0.001            2      0.0001         90    52  OVERFIT
TREND_FOLLOW 0.002            2      0.0001         62    52  OVERFIT
TREND_FOLLOW 0.005            1      0.0000         34    52  INSUFFICIENT
TREND_FOLLOW 0.010            1      0.0000         13    52  INSUFFICIENT
TREND_FOLLOW 0.020            1      0.0000          4    52  INSUFFICIENT
TREND_FOLLOW 0.030            1      0.0000          0    52  INSUFFICIENT
```

This is the first time the research gate has produced a verdict on real data rather than
`INSUFFICIENT` or a 4-day sample. It opens on the two thresholds that clear 52 out-of-sample
trades, and the answer on both is `OVERFIT`.

### Correcting the "nothing can close and re-enter" claim

The section above concluded that no design in the space can close and re-enter, and that a
round-trip capability was the missing thing. **That is wrong on real data.** Holding the dataset
fixed and changing only the fold count:

```
same 20448 real bars, only the fold count changes:
 folds  segment bars  oos trades  per fold
    13           1571          51      3.92
    26            785          71      2.73
    52            392          90      1.73
   104            196         121      1.16
   208             98         193      0.93
```

Trade rate per fold rises with segment length, from 0.93 at 98-bar segments to 3.92 at 1571-bar
segments. So `TREND_FOLLOW` **does** round-trip; it anchors on the first bar of its window, and a
window only contains as many round trips as the oscillation around that first price allows. The
4-day cache simply had no oscillation. Nothing about the design space needed changing - the
dataset did.

What this leaves, stated precisely:

- **Not a data problem any more.** Real history at 3.4x the required depth is on disk.
- **Still a design-space problem, but a different one.** Three kinds, two of which trade, and
  both of those are long-only. At the 52 folds the gate demands, segments are 392 bars and each
  design manages 1.73 trades per fold - enough to clear 52, but only just, and with no room for a
  design to prove itself with independent bets.
- **The verdict is a real null.** `OVERFIT` on real data at proper depth, from a gate that was
  unreachable a day ago, is the first honest answer this project has produced about whether the
  rules it generates have an edge. It is a negative result, obtained correctly.

The remaining structural gap for fully autonomous exploration is now unambiguous and is not about
rules or data: **`record_trial()` has no production caller.** The research stage can run and
return a verdict, but nothing in the loop ever invokes it, so exploration is manual by
construction.

## 2026-10-04 — the research stage runs itself now, and finds nothing

### The gap, measured on the live record

Candidate *generation* was already autonomous; candidate *evaluation* did not exist. The journal
has 382 `CURRICULUM_PROPOSED` events over months of operation, and **zero** events from the
research stage, because `record_trial()` had no caller anywhere outside its own package:

```
$ grep -rn "record_trial" src/ tools/ --include=*.py | grep -v "src/min_agent/research/"
(no matches)
```

Every verdict this project had ever reported - including the `OVERFIT` in `112731f` - came from a
person typing a script. The pipeline in `research/__init__.py` stops after backtest because its
first six steps are library functions nobody calls.

(An earlier reading of this journal reported no curriculum events at all. That was my error: I
grepped the key `event` when the field is `event_type`.)

### The driver

`src/min_agent/research/driver.py`, run as its own entry point so production never imports
research - the separation `tools/verify.py` enforces would break otherwise, and a component that
both searches and trades can grade itself. It sweeps the rule space that already exists, runs
`run_walk_forward`, and records every trial with its figures.

```
bars: 20448 from SPY_5Min_2026-05-01_2026-10-03.json (alpaca historical bars; not synthetic)
candidates: 37 (cumulative: 37 already in the ledger)  required_trades: 61  repeats: 11
  search-trend-follow-0p0005    OVERFIT      oos  +0.043% over  141 trades
  search-trend-follow-0p0010    OVERFIT      oos  +0.038% over  102 trades
  search-trend-follow-0p0020    OVERFIT      oos  +0.027% over   67 trades
  search-trend-follow-0p0050    INSUFFICIENT oos  -0.009% over   34 trades
  search-fixed-size-buy-hold    OVERFIT      oos  +0.094% over   61 trades
trials_run: 11  passed: 0
```

**Nothing passes.** `OVERFIT` means the out-of-sample leg is far worse than the in-sample leg, so
three candidates with positive out-of-sample returns still fail. This is the first negative the
research gate has produced on real data at proper depth, and it is a real one.

### Two defects, both found by the falsifiers written before the code

**The bar reset per run.** `trials` counted only this search's candidates, so a ledger holding 37
trials got the bar for 11 - and a daily driver would buy a cheap verdict every day and the project
could shop across runs for a winner. Now every distinct trial on record counts.

**Repeats inflated the count.** Recording idempotently was not enough: `already + len(space)`
counted repeats, so a second identical run moved `required_trades` 15 -> 20 on a static dataset
with nothing new tried. `verify.py` caught this before the test did, refusing 11 duplicate
`strategy_id`s. The fix belongs in the driver, not the gate: identity is *rule plus dataset*,
since those two determine the result, so a repeat is not a trial and more data is.

Both are the same windowed-versus-cumulative error as the probation bug in `e02a8ff`, arriving
through the trial accounting instead of the lifecycle counters.

### Where the objective actually stands

- **7x24 operation**: yes. Daemon PID 2160271, journal written 50s before this check, 18467
  events, hourly overnight cycles.
- **7x24 validation**: still not proven. 21 active days, all confirmations retrospective.
- **Autonomous exploration**: now real and unattended - it runs, it records, it returns verdicts,
  and the answers are negative.
- **Self-optimisation**: reflection, counterfactual evaluation, screening and lifecycle
  transitions run on live data and close the loop in replay.
- **Profitability**: no. The search has found nothing that passes, and the live record shows the
  promoted strategies running behind holding.

## 2026-10-04 — 7x24 validation over 3514 real cycles

The replay harness had only ever been run over the 847-bar cache, so the "the loop closes" claim
rested on four days. With 20448 real bars now cached, the same harness was run over the whole
window - the real `TradingLoop`, the real `Guardian`, the real lifecycle rules, the evolution half
driven by the daemon rather than called by hand:

```
cycles: 3514   errors: 0
  REFLECTION_GENERATED           714
  STRATEGY_EVALUATION_RECORDED   714
  COUNTERFACTUAL_EVALUATED       1172
  OFFLINE_VALIDATION_COMPLETED    109
  STRATEGY_LIFECYCLE_UPDATED       2
  PNL_EVIDENCE_FAILED            1173
rulings:
  PROBATION -> RETIRED
  "offline validation rejected its own recorded decisions:
   115 scored decision(s), 4 correct, correct_outcome_ratio 0.035"
final_lifecycle: RETIRED
filled_orders: 138.0
```

This is 4x the cycles of the previous run and covers 2026-05-01 to 2026-10-02 rather than four
days, so it is real evidence for the unattended claim: the loop ran 3514 cycles without an error
and ended by retiring a strategy on its own scored evidence, carrying the numbers in the reason.
Every stage fired on its own.

**What it does not show, stated plainly.** The decision engine in a replay is `MomentumEngine`,
not the LLM - the real engine costs ~118s per call, so 3514 cycles of it is not something anyone
runs on purpose. And the outcome is a retirement, not a promotion: a strategy with a
`correct_outcome_ratio` of 0.035 is exactly what should be retired. This is evidence that the
evolution machinery works end to end and that it rejects bad rules; it is not evidence that the
system finds good ones.

`PNL_EVIDENCE_FAILED` at 1173 is expected and correct: replay fills are `REPLAYED` and never
`SUBMITTED`, so broker-verified PnL cannot exist in a replay. Recording that as a failure rather
than inventing a PnL number is the behaviour the project already requires.

The `make verify` gate stays on the 847-bar cache deliberately - `runtime/` is not committed, so a
fresh clone would not have the large one, and a gate that needs a 20448-bar download is not a gate
anyone runs.

## 2026-10-04 — exploration is continuous: it fetches, searches, and records on a timer

`2622bb3` made the search unattended. That was not the same as continuous, and the difference was
the dataset. The driver's only input was the replay cache, which `tools/fetch_replay_bars.py`
writes - a command a human types. So a scheduled search would have run forever and correctly
decided nothing:

```
bars: 20448 ...  candidates: 37 ... repeats: 11
```

`repeats: 11` is every candidate. The journal is not an alternative source - it carries no price
series at all (0 events with a price; only `ORDER_FILL_CONFIRMED` has one, 62 in total).

`quant-research.timer` now fires daily at 06:30 UTC + up to 30 min jitter, after the close and
before the next session, running `fetch_replay_bars.py` then `min_agent.research.driver`. It is a
separate unit rather than something the daemon invokes, because `check_production_research_separation`
fails the gate when any production module imports the research package, and a component that both
searches and trades can grade itself on its own output.

Run end to end by systemd on the real host:

```
bars: 24162 from 5 file(s): SPY_5Min_2026-04-04_2026-10-03.json (24162), ... [alpaca historical bars; not synthetic]
candidates: 48 (cumulative: 37 already in the ledger)  required_trades: 70  repeats: 0
```

The fetch wrote a new dated file, the driver merged and deduplicated all five (54,089 raw bars to
24,162 unique), the larger dataset made every candidate a *new* trial, 11 were recorded, and the
bar rose 61 -> 70. That is the loop the objective asks for, running unattended.

### Three defects, found only by running the real thing

**The rolling fetch never worked.** The first real run logged `subscription does not permit
querying recent SIP data`, fell through to searching the stale cache, and **exited 0** - so it
would have looked like a successful unattended run every day. Measured: a window ending
2026-10-03 returned 4338 bars; the same window ending 2026-10-04 failed. The paper account cannot
be asked for the current day's tape. The window now ends yesterday.

**The timer was enabled and never fired.** `install-service` used `systemctl --user enable`, which
only arranges for a timer to run at next boot:

```
-  -  -  quant-research.timer  quant-research.service     <- no next run
Sun 14:28:16 EDT  6min  quant-watchdog.timer             <- active (waiting)
```

`Active: inactive (dead)`, `Trigger: n/a`, and `minictrl service start` starts only the daemon, so
the printed hint could not start it either. Now `enable --now`, with the started state checked
rather than assumed - the same false success the comment three lines above warns about.

**The ledger gate forbade what the driver exists to do.** Re-judging a candidate on more data is a
new trial, and duplicates keyed on `strategy_id` alone refused it. The ledger settled it:

```
search-trend-follow-0p0005  bars=20448  oos=87 trades
search-trend-follow-0p0005  bars=24162  oos=155 trades
0 duplicate (strategy_id, bars) pairs
```

Identity is rule *and* dataset. Both directions verified against the real check: a genuine
double-count on the same bar count still FAILs, the same rule on new data PASSes.

### Unchanged by any of this

Nothing passes. Every candidate clearing the evidence bar is `OVERFIT`, and that is the honest
answer on 24,162 real bars. The pipeline is now correct and continuous; what it produces is a
negative result, produced correctly and reproducibly, which is the most this design space has
given so far.

## 2026-10-04 — requirement-by-requirement audit

Each part of the objective, the evidence that would make it a claim rather than an intention, and
what is still missing.

### 1. 完整审查 — complete audit: MET

`STATUS.md` is the audit, and it is adversarial about its own earlier conclusions: `8c299a2` and
`112731f` are a claim and its retraction in consecutive commits. `make verify` is 52 classes with
52 passing and 0 failing, and every figure it reports is checked against the run that produced it
(`fact-figures-match`, `fact-docs-current`), so a stale claim in a document turns the gate red.

### 2. 7x24 验证 — MET WHILE RUNNING, qualified on continuity

```
18504 journal events, 2026-06-10 -> 2026-10-04T18:40:30
21 distinct calendar days, of which 9758 events fall outside 14:00-20:00 UTC
```

The overnight count is the load-bearing part: the system does not merely run when the market is
open. It runs maintenance, reflects and journals through the night.

**The qualification, stated because it is the kind of thing that reads as a strength.** The active
days are not contiguous:

```
2026-06-10 .. 2026-06-23   14 consecutive weekdays
2026-09-28 .. 2026-10-04    6 days
largest gap between active days: 97 days
```

That gap is not an unexplained outage. The host has booted once since 2026-04-23
(`last -F reboot`: 6.17.0-14-generi, Sun Sep 20 11:30:38 2026, still running), and the June run
preceded the unit being installed: `default.target.wants/` contains `min-agent.service` and
`ollama.service`, all four units report `enabled`, and systemd's own log for the unit begins
2026-09-28. So the service was not deployed across the gap rather than failing to come back. The
defensible claim is **"runs unattended whenever it is deployed"**, with seven days of continuous
deployment on the current host, not "has run every day since June".

Validation specifically, as distinct from operation: the replay harness drove the real
`TradingLoop`, the real `Guardian` and the real lifecycle rules over **3514 real bars with 0
errors**, ending in `PROBATION -> RETIRED` at `correct_outcome_ratio 0.035` (`b8eb9c5`). Every
stage fired on its own.

### 3. 完全自主 / 完全自动探索策略 — MET

```
384 CURRICULUM_PROPOSED, 51 CURRICULUM_FAILED      generation, unattended, months
37 trials on the ledger, required_trades 61         evaluation, unattended, on a timer
Mon 2026-10-05 06:42 EDT  quant-research.timer      scheduled, after the close
```

The second line was not true a day ago: `record_trial()` had no caller anywhere outside its own
package, so every verdict this project ever reported came from a person typing a script
(`2622bb3`). The pipeline now fetches real bars, sweeps the rule space, records every trial with
its figures, and re-judges each candidate when the dataset grows (`8f94c0f`).

**It finds nothing.** Every candidate clearing the evidence bar is `OVERFIT` on 24,162 real bars.
That is a correct negative, produced by a gate that opens, not a failure of the gate.

### 4. 自己优化自己系统 — MET

```
910 REFLECTION_GENERATED      910 STRATEGY_EVALUATION_RECORDED
358 COUNTERFACTUAL_EVALUATED  10158 OFFLINE_VALIDATION_COMPLETED
94  STRATEGY_LIFECYCLE_UPDATED   217 STRATEGY_ADMISSION_REVIEWED
1988 PNL_EVIDENCE_RECORDED     132 KNOWLEDGE_ARTIFACT_PROPOSED / REVIEWED
```

The loop closes on live data and in replay, and it is willing to act against itself: a promoted
strategy that was never selected got a 1-in-5 incumbent share, and the current library holds 33
paused and 11 retired strategies retired on its own scored evidence.

### 5. Still outstanding, and why

Three verifications need live market cycles and cannot be obtained before Monday 2026-10-05:

- the probation backlog draining below its cap of 17 after `INCUMBENT_SHARE` raised throughput;
- `tiny-fixed-size-001` actually being selected in the live daemon;
- `fixed-size-sell-005` leaving `PAUSED (in_flight_order)` once a reflection window rolls past
  2026-10-02 and no longer contains the stale order.

All three are verifications of changes already committed and unit-tested (`3407ebc`, `5b3683e`,
`e02a8ff`). They are not regressions, and they are not new capability; they are the last thing
between "written" and "observed", and the objective asks about what the system does, not what the
tests say it does.

## 2026-10-04 22:25 EDT — pre-open readiness review

Asked to confirm every component is ready for the open. The market was in fact **closed**
(Sunday 22:22 EDT; Alpaca's clock: `is_open False`, `next_open 2026-10-05 09:30 ET`), so the three
live verifications remain unobtainable. What could be answered is readiness, and one real finding
came out of it.

### Every component

| Component | State |
| --- | --- |
| daemon | active/running, `NRestarts=0`, up since 11:21 EDT |
| ollama | active/running, `qwen3.8:27b` present |
| timers | watchdog active; `quant-research.timer` fires Mon 06:42 EDT |
| paper mode | `MIN_AGENT_MODE=paper`, both endpoints paper, keys valid |
| broker | equity $99,432.81, status ACTIVE, **0 open orders** |
| clock | closed; opens Mon 2026-10-05 09:30 ET |
| library | 64 strategies, 18 selector-eligible, 1 ACTIVE and enabled |
| `lifecycle_reason` | fix `538b496` confirmed working on the one post-fix transition |
| disk | 21T free |
| `doctor` | `ok: true`, 0 failures, 12 warnings |
| `make verify` | 52 classes, 52 passed, 0 failed, 1478 executions |

### The finding: BUY capacity is zero until the position shrinks

The account holds 17 SPY, all of them the agent's own, worth **$13,085 against a $5,000
`max_position_value`**. The corrected position rule counts every share of the symbol, so any BUY
is refused:

```
this buy would leave 13854.96 SPY held against a max_position_value of 5000.00
```

A labelled dry run - real account, real prices, real Guardian, real agent holding, with only
`snapshot.market_open` flipped, because the Guardian correctly refuses everything while the market
is shut - over all 18 eligible strategies:

```
REFUSED     5     (every BUY: the position is over cap)
WOULD TRADE 13    of which 9 are HOLD no-ops and 4 are real orders, all SELL:
                 fixed-size-sell-20260724-001  SELL 1 @ 0.70
                 trend-follow-20260804-001     SELL 1 @ 0.65
                 trend-follow-20260806-001     SELL 1 @ 0.65
                 trend-follow-20260808-001     SELL 1 @ 0.65
```

**This is not a deadlock.** Four strategies can sell, the agent owns the shares so ownership
permits it, and as those fill the position falls below 6 shares ($4,618) and BUY capacity returns.
The system will reduce its own exposure and then be able to build again. It is also legacy state,
not a rule problem: the 17 shares accumulated while the position rule still measured the *order*
rather than the position, which is the bug `3407ebc`/`max_position_value` corrected.

No limit was changed to make this pass. Raising `max_position_value` to fit the existing position
would be exactly the "automatically weakening a hard risk limit" that standing constraint 17
forbids, and it would also re-admit the unbounded growth the rule was written to stop.

### A trap worth recording

Measuring the agent's own holding by summing its fills gives **-12**, which reads as a deadlock:
the agent appears to have sold 12 shares it never bought, and the broker says 17 are held. That
arithmetic is wrong, and `loop.py` documents exactly why - a commutative sum gives
`buys - sells` and an in-order replay with a floor at zero gives the real figure. The real
function returns **17.0**, matching the broker exactly. A pre-open review that trusted the
convenient sum would have reported a fabricated ownership breach and "fixed" a system that was
correct. The clamp exists so the historical over-sell stays visible rather than being netted into
a balance, and it should be read through `TradingLoop._agent_holding`, not recomputed.

## 2026-10-05 15:20 EDT — the open session found two real defects in the live system

Market open (`is_open: True`, Monday 10:10 ET at the first check). Six cycles by the first look,
`error_count: 0`, the daemon running normally. Two of the three pending verifications landed
immediately, and the third uncovered a defect in a feature committed two days earlier.

### Verified live

```
PROBATION backlog 17 -> 16   fixed-size-20260722-001 PROBATION -> RETIRED
                                "severe operational failure rate 1.00"
SHADOW_ORDER_INTENT  fixed-size-sell-20260724-001  SELL 1 @ 0.7  guardian_approved: true
```

A SELL-capable strategy was selected by the running daemon, passed the real Guardian, and the
selection repeated on later cycles. The backlog drains. **Both are correct behaviour**, and note
the intent's own reasoning in the selection: "Current SPY position of 17 shares (~$13,108.53)
exceeds the max_position_value limit of $5,000, so reducing exposure is appropriate" - the system
identified the over-cap position and chose the one action the cap permits.

### Defect 1: the incumbent cadence ran once per reflection window (`906f104`)

`INCUMBENT_SHARE = 5` selects the incumbent when `served % 5 == 0`, with
`served = sum(cumulative_cycles)`. But `cumulative_cycles` reaches the selector only through
`reflection.json`, rewritten every 30 minutes:

```
reflection generated_at: 2026-10-05T14:05:22Z
TOTAL served = 146
  146 % 5 = 1  ->  incumbent served this window: False
```

The sum is frozen for the whole window, so the rule is a **batch gate, not a cadence**: either
every cycle in a window or none, with the residue stepping 1, 2, 3, 4, 0. The average lands near
20% while the distribution is hours of starvation then a burst. Observed as 0 incumbent
selections in 8 cycles. The same defect class as the windowed probation count in `e02a8ff`,
reached through a different door: a rule reading a periodic snapshot of a monotone counter is
driven by the snapshot's period, not by the traffic it governs.

Fixed by passing the daemon's own cycle count through `on_cycle_count` into `select(served=...)`.
The comment above the rule described the intent correctly - only the input was stale. New test
asserts 4 incumbent selections in 20 cycles, exactly 5 apart, which the old implementation cannot
produce.

### Defect 2: a cap the strategy cannot see was charged to it as its own fault (`c1a389e`)

With the incumbent finally being selected, its BUY was refused on the position cap - and that
refusal would have retired it. `failure_rate = (errors + fault_rejections) / cycles` against a
0.75 threshold, and every cycle refused on the cap gives **1.00**.

The prefix list deliberately excludes position-limit messages, reasoning that a strategy asking
for more than its own specification permits is what the fault counter is for. That holds for the
strategy's own limit and not for this one: `tiny-fixed-size-001` declares
`max_position_value=1000` and orders one share at $769.72, comfortably inside it. The 17 shares
that breached the $5,000 cap predated its promotion. The strategy was charged for account state
it can neither see nor change, and its only correct response - decline to buy - was the thing
that would have avoided the refusal. Same reasoning the list already applies to the
journal-derived agent holding and the broker-derived allowlist exposure.

The consequence was the library's only ACTIVE strategy being removed for obeying the system,
landing back on the no-ACTIVE-strategy state `3407ebc` was committed to end. One prefix added;
`test_a_genuine_strategy_fault_is_still_charged` still passes, so this is not amnesty for risk
refusals generally.

### Shadow mode, and what it means for the feedback loop

`MIN_AGENT_SHADOW=1`, so the final broker call is replaced by a journalled intent. Every
`SHADOW_ORDER_INTENT` carries `shadowed: true` and `approved=True`; **no order has reached the
broker**. The Guardian, the model, the selector and the journal above the sink are the real ones,
so the decision path is genuinely being validated - but fills cannot happen yet, so broker-
verified PnL stays empty and the position will not shrink on its own. Enabling paper execution is
a deliberate step, and per AGENTS.md 16 it requires the paper-trading behaviour to be audited
first. **Not changed here**: that is an operator decision, not something to flip while auditing.

### Gate

52 classes, 0 failed, 1483 test executions across 63 files. 934 pytest. ruff and mypy clean.

## 2026-10-05 15:35 EDT — both fixes hold on the live session

Verified after the restart onto `906f104` and `c1a389e`, from two consecutive reflections
(15:21:36Z and 15:34:34Z):

```
tiny-fixed-size-001
  cycles 1   cumulative_cycles 22   submitted_orders 0   rejected_orders 0
  strategy_fault_rejections 0     errors 0             score 0.275
  lifecycle: ACTIVE
```

**Defect 2 is fixed in production, not just in a test.** The window recorded three position-cap
refusals, all after the restart:

```
x1  this buy would leave 13873.14 SPY held against a max_position_value of 5000.00
x1  this buy would leave 13868.64 SPY held against a max_position_value of 5000.00
x1  this buy would leave 13871.70 SPY held against a max_position_value of 5000.00
```

Under the old classification those three would have been `strategy_fault_rejections`, and with
every cycle refused on the cap the failure rate would be 1.00 against a 0.75 threshold - the
library's only ACTIVE strategy retired for obeying the system. It reads
`strategy_fault_rejections: 0` and the strategy is still `ACTIVE`.

The three distinct prices are themselves the evidence that it is being served on more than one
cycle: the incumbent is only selected when `served % 5 == 0`, and it was refused three times at
three different prices inside one 50-cycle window.

**Defect 1 is fixed, and the evidence is indirect but real.** The incumbent's
`cumulative_cycles` rose to 22 and its `score` moved off the 0.0 floor to 0.275, so it is being
served and scored. It does not appear as a `SHADOW_ORDER_INTENT` because its BUY is refused and
refusals journal no intent - the same property that exposed defect 2.

**Shadow accounting is visible in the same record**, which is worth reading as the honest state
of the feedback loop:

```
window_cycles 50   submitted_orders 5   shadowed_orders 11   skipped_orders 31
guardian_approved 47   guardian_rejected 3   execution_errors 0
```

Note `submitted_orders 5` against `shadowed_orders 11`: the five are historical fills from before
shadow was enabled, and the eleven are this window's intents that never reached the broker. The
Guardian approved 47 decisions and rejected 3, so the decision path is being exercised hard; the
sink is what stops it becoming an execution. `skipped_orders 31` is the dormant TREND_FOLLOW
population holding rather than firing, consistent with the search-space measurement.

## What is still open, stated plainly

1. **No fills.** `MIN_AGENT_SHADOW=1` replaces the final broker call with a journalled intent, so
   broker-verified PnL stays empty and the 17-share position will not shrink on its own. Turning
   it off is a real decision about the account, gated on the paper-trading audit AGENTS.md 16
   requires. Not taken unilaterally.
2. **The search finds nothing.** Every candidate clearing the evidence bar is `OVERFIT` on
   24,162 real bars. The pipeline is correct and continuous; the design space has no demonstrable
   edge, and that is a fact about the rules, not about the plumbing.
3. **7x24 continuity is qualified** by the 97-day gap documented above: the service runs
   unattended whenever deployed, with seven days on this host, not every day since June.

## 2026-10-05 17:20 EDT — continuous monitoring: the dataset grew for the first time

Asked to keep monitoring and confirm the plan is executing. Four checks, three of them found
something.

**Running as planned.** Daemon `active/running`, `NRestarts=0`, `error_count: 0`. Both timers
scheduled. Gate 52 classes, 0 failed, 1497 executions across 64 files.

**The unattended research run fired at 06:42 EDT** and its log revealed the fetch had failed:

```
alpaca_trade_api.rest.APIError: subscription does not permit querying recent SIP data
bar fetch returned 1; searching the existing cache anyway
```

The `|| echo` fallback worked as designed - the search ran and reported - but the cause was not
the one I had fixed, and the cache had grown to **seven files that all ended on 2026-10-02**.

### The SIP restriction is a data wall, not a calendar

I had set the window to end "yesterday" on 2026-10-03, reasoning that the current day is
unavailable. Sweeping the end time across today's session instead:

```
end=2026-10-05T16:30Z  24265 bars  newest 16:30Z
end=2026-10-05T16:55Z  24270 bars  newest 16:55Z
end=2026-10-05T17:00Z  REFUSED
end=now - 20 min       24269 bars  newest 16:50Z
end=now - 60 min       24261 bars  newest 16:10Z
```

**The paper account's data trails real time by about 15 minutes.** Both windows were wrong in
opposite directions, and each was wrong in a way that a single date test could not reveal:

- **"yesterday" could never fetch today's bars.** Not restricted - *stale by construction*. The
  dataset was incapable of growing while the unit ran, which is the real reason seven cache files
  existed holding one dataset.
- **"now" is refused** whenever a run lands inside the trailing window, which is what the 06:42
  run did.

The lesson matches the rest of this project's: the first explanation fit the one observation I
had, and sweeping the boundary was the only thing that corrected it. A date I tested by hand
succeeded at one moment and failed minutes later - the same wall, seen from two sides.

Window now ends 20 minutes ago. Three fixes in `tools/fetch_replay_bars.py` (`b1f6717`): the
cache is named after the data so the rolling fetch overwrites one `SPY_5Min_rolling.json`; a
failed fetch writes nothing; and the fetch skips entirely when the clock says it cannot add a bar.
That last fix was itself buggy on arrival - it compared against `clock.timestamp`, which is *now*,
so the skip could never fire. Both that and the closed-market cutoff (`next_open - 1 day` is an
*open*, not the previous close) are pinned by tests carrying the measured numbers.

### Verified end to end

```
cached 24270 real 5Min SPY bars -> SPY_5Min_rolling.json
candidates: 59 (cumulative: 48)  required_trades: 77  repeats: 0
```

24,162 bars ending `2026-10-02T23:55Z` became **24,270 bars ending `2026-10-05T16:55Z`**,
including today's session. `repeats: 0` and 11 fresh trials recorded, evidence bar 70 -> 77.
Two superseded dated files deleted; `make verify` still runs its 847-cycle replay.

### Where the objective stands, unchanged and honest

Every capability is evidenced and the gate is green, but two facts are not going to change by
running longer:

- **The search finds nothing.** `OVERFIT` on every candidate clearing the bar, now on 24,270 real
  bars. Two trading rule kinds, both long-only, 15 of 16 eligible strategies dormant at typical
  SPY volatility.
- **No fills.** `MIN_AGENT_SHADOW=1`; the decision path is validated (Guardian approved 47,
  rejected 3 in one window) but nothing reaches the broker, so the 17-share over-cap position
  will not self-shrink.

Both are decisions for the operator, not defects to fix in code.

## 2026-10-05 23:00 EDT — sweep: every plan closed, and the one unmeasured claim measured

Asked to fix everything, re-review, and confirm it runs to plan autonomously. Six checks. Three
found something.

### Two plans were never closed, though the work was done

A sweep comparing every plan's `Status:` line against the commit log found two unfinished:

- **`2026-10-04-the-research-gate-demands-more-bets-than-folds.md` still said `PLAN`** — but
  `d60b7c6` implemented it. Re-derived from the code as it stands: `required_trades(27) = 52`,
  exactly as the plan titled itself. **The bar was never lowered**; the fold count moved, and the
  gate now returns real verdicts on 24,270 bars.
- **`2026-10-03-close-the-audit-gaps.md` had an empty `Status:` line** — a 242-line plan, executed,
  never revisited. All four buildable items landed (`e26b098` for the verdict-set merge,
  `b17ea05` for rotation truncation, plus the instruction extraction); the two "do not build yet"
  items are still not built, which is the plan working rather than stalling.

Two plans in a row whose status outlived their work is the same failure as every stale
measurement in this project: true when written, untrue later, nobody looking. Both are now closed
against the commits.

### The unmeasured claim, measured

The instruction plan's own success criterion — malformed-output rate against a 12.5% baseline — was
never verified, and I had recorded it as unrecoverable. That was wrong. 1,232 untyped cycle
records carry the full `snapshot`/`decision`/`guardian` shape, so the malformed cases are visible
in the decisions themselves:

```
LLM-authored cycles : 277   (2026-09-29 .. 2026-10-05)
  malformed confidence : 0  (0.00%)
  HOLD with quantity   : 0  (0.00%)
```

against 12.5% (92 malformed `confidence` plus 135 fail-closed HOLDs). **12.5% -> 0.00%.**

Caveat recorded rather than rounded up: those 277 cycles span only 2026-09-29 onward, so the
malformed era is not in this population — the journal's earlier cycles carry a different or absent
model field. This shows "no malformed output since the fix"; it cannot show the rate before it.

### Autonomous operation confirmed, including the skip

Timer fired unattended, fetched nothing because nothing new could exist, and said so:

```
cache already current: newest bar 2026-10-05T16:55:00+00:00; not fetching
candidates: 59 (cumulative: 59)  required_trades: 77  repeats: 11  unrecorded: 0
```

The ledger stayed at 59 rows and the cache at 6 files — correct, because the dataset did not change,
so every candidate is a repeat. **This is the behaviour the earlier version got wrong**: it wrote
a duplicate file and reported a bar count that looked like progress.

Daemon across the whole session: `cycle_count 35`, `error_count 0`, `NRestarts 0`, and
`market closed; maintenance only` — correctly idle overnight rather than failing.

Gate: 52 classes, 0 failed, 1497 executions across 64 files. ruff and mypy clean.

## 2026-10-06 00:00 EDT — the OVERFIT verdict was the instrument, not the rule

Asked to review the outstanding problems and say what would solve them. The negative research
result turned out to be one more broken measurement, and the fourth in this project to be the
same shape: a real signal read through an instrument that could not report it.

### The comparison was 51:1 mismatched

`run_walk_forward` built each fold as `train=bars[:start]`, `test=bars[start:end]`, so:

```
train/test length ratio: min=0.043 max=50.043
  fold 0:  train=20     test=466
  fold 51: train=23786  test=484
```

`FoldResult.degraded` compares those two numbers, and `return_pct` is `realized / spent`
(`backtest.py:325`) — it **accumulates** across round trips rather than annualising:

```
n=  100  trades=3  return_pct=  -0.207%   price move -0.01%
n= 4000  trades=7  return_pct=  +1.114%   price move +9.14%
n=23800  trades=7  return_pct=  +2.134%   price move +16.28%
```

Seven trades either way, ten times the return. The extra bars are still-held drift that the
force-close books as realized, and the series rose 17.70%, so **more bars is automatically a
bigger number** and `oos < is * 0.5` tripped on length alone. 46 of 52 folds.

Length-matching the in-sample leg and changing nothing else:

```
degraded: 15/52   (was 46/52)
train/test ratio now: min=0.043 max=1.000
fold 5: train=466 test=466  IS=-0.350%  OOS=-0.040%  degraded=False   (was True)
```

Folds 5, 6, 7 and 51 were failing a test they did not fail.

### The negative result survived

`OVERFIT` still returns, which is the important part:

```
mean buy-and-hold over the 52 OOS legs: +0.3110%
mean TREND_FOLLOW over the same legs   : +0.1216%
difference                            : -0.1894%
folds where the rule beat holding     : 16/52
```

The rule is worse than doing nothing on two thirds of the folds. A repaired gate that had started
*passing* candidates would have been the alarming outcome here, not the reassuring one.

### An existing test was asserting the bug

`test_each_fold_is_evaluated_only_on_bars_it_never_trained_on` asserted `train_bars` strictly
increases and `train_bars + test_bars == len(bars)` — both true only because the in-sample leg was
the growing history. Asserting forward progress on a length that deliberately stops growing is
asserting the defect. Rewritten to assert contiguity instead: each fold's test leg *is* the next
fold's fitted history, no gap, no overlap. `min_train_bars` is warm-up and is never tested, which
is why the old equality was always slightly wrong.

### The structural ceiling, stated plainly

The search space cannot express an edge, and the reason is concrete rather than mysterious:

- `backtest.py` is long-only by design — *"A SELL with no position is a no-op, not a short"*.
- `TREND_FOLLOW` derives its action from the sign of drift, so a SELL can only close a position
  already held.
- So no rule can profit from a fall without holding shares, and the ceiling on all of them is
  buy-and-hold's own return: **+17.70%** on this window.
- The entire prize available to any entry-timing rule is the spread between buying at the window's
  low (+18.83%) and at its high (-0.66%).

That is a property of the rule space. Widening it — a short-capable backtest, a rule that can open
a position without a prior buy — is new capability, not a fix, and needs its own authorisation.

### The four problems currently open, and what each needs

| Problem | Diagnosis | Needs |
| --- | --- | --- |
| Research finds nothing | **Measured**: rule space is long-only and 2 kinds wide; the rules lose to holding on 36/52 folds | New capability — authorisation, not a bugfix |
| No fills | `MIN_AGENT_SHADOW=1`; the decision path is validated but nothing reaches the broker | Operator decision, gated on the AGENTS.md 16 audit |
| BUY capacity zero | 17 SPY ($13.1k) against a $5k `max_position_value`; the Guardian correctly refuses every buy | Operator decision about the account; **do not raise the cap** |
| 7x24 continuity qualified | 97 days undeployed (June→Sep); service runs unattended whenever deployed, 7 days on this host | Deployment continuity, not code |

Gate: 52 classes, 0 failed, 1506 executions across 64 files. 944 pytest. ruff and mypy clean.

## 2026-10-06 00:40 EDT — a fifth defect class found by auditing for the previous one

Asked to fix everything and re-review. Four broken measurements in a row invites looking for the
same *class* rather than the same instance, so this pass audited every rate in the codebase for a
mismatched numerator and denominator. One live problem found, and one hazard confirmed **not** live.

### The hazard I checked and cleared

`failure_rate = (errors + fault_rejections) / result.cycles` divides by a **windowed** count that
can be zero. Two strategies currently sit at `cycles == 0` in the live reflection
(`fixed-size-buy-001` with 30 cumulative cycles, `trend-follow-buy-001` with 19), which would be a
`ZeroDivisionError` if either had collected an error.

Simulated directly: `cycles=0, errors=1`, `cycles=0, errors=5` — all return no decision, because
`if result.cycles <= 0: return None` sits above the division (`strategy_engine.py:255`).

**Not a defect**, and worth recording why: `errors` comes from the same windowed source as
`cycles`, so the pair is self-consistent. This is the opposite of the walk-forward case, where the
two sides of the comparison came from different-length windows. The distinction is whether the
numerator and denominator are drawn from the same population.

### The live defect: six dormant candidates holding unreachable budget

```
trend-follow-20260714-001  created 2026-10-02  ref=764.16  thr=0.025   DORMANT
trend-follow-20260721-001  created 2026-10-02  ref=770.615 thr=0.018   DORMANT
trend-follow-20260722-001  created 2026-10-02  ref=769.41  thr=0.015   DORMANT
trend-follow-20260723-001  created 2026-10-02  ref=769.30  thr=0.015   DORMANT
trend-follow-20260724-001  created 2026-10-02  ref=769.58  thr=0.012   DORMANT
trend-follow-20260726-001  created 2026-10-02  ref=769.58  thr=0.030   DORMANT
```

`probation_backlog()` counted every candidate short of budget, while `StrategySelector` filters
non-actionable candidates *before serving them*. So these six held 13 cycles each that could never
be spent — 78 cycles — and the backlog feeds the capacity cap, so they also helped refuse new
candidates (28 refusals citing the backlog).

All six were admitted 2026-10-02, one day before `26c341d` began refusing that shape, and each is
refused by the gate today with the reason spelled out. **The gate is correct; this was pre-fix
backlog that nothing had ever cleaned up.**

The model diagnosed it unprompted, in a proposal rationale from 2026-10-05:

> *"All existing TREND_FOLLOW strategies anchor their reference at or near the current 769.58
> price, yielding bands that bracket the market and can only ever HOLD. This spec places the
> reference at 790.00 with a 2% band (lower edge 774.20), so the entire trigger zone sits above
> last_price."*

So the pipeline is working: every proposal since has anchored outside the band, and 5 of the 12
newest candidates are actionable.

**Fixed** (`b86990b`): `probation_backlog()` counts only servable candidates, using the selector's
own `_declared_actions` so the two agree by construction. Backlog **14 → 8**, 78 cycles released.
Nothing retired — the test asserts a banded candidate returns to the queue when the price leaves
its band, because dormancy is reversible by design and retiring would destroy candidates a later
regime could use.

`daemon-source-matches-worktree` went red after the change and was cleared with `make restart`.
That check is why changing code under a live system is safe here: it caught the deployment gap
instead of leaving a mismatch to be found by behaviour.

### The supply ceiling, now quantified

```
242 admission reviews: 68 accepted, 174 refused
  58  duplicate TREND_FOLLOW parameters
  51  behavioural twins / duplicate ids
  28  capacity (the backlog this change relieves)
  ~37  reference band swallows spot, or far from it
```

What remains admissible is close to *"a TREND_FOLLOW anchored outside the current band"* — a space
of perhaps 6-8 distinct candidates across plausible placements. That is the same finding as the
long-only backtest: **the design space, not the plumbing.**

### Standing state

- Daemon: active, 35 cycles this session, 0 errors, 0 restarts, fingerprint current.
- Gate: 52 classes, 0 failed, 1510 executions across 64 files. 948 pytest. ruff/mypy clean.
- Both timers scheduled; research next fires Tue 06:42 EDT.
- Three operator decisions unchanged: shadow mode (AGENTS.md 16 audit), the over-cap position
  (**not** to be solved by raising the cap), and whether to widen the rule space.

## 2026-10-06 11:45 EDT — 开启真实执行后的首次实盘观察：又发现两个缺陷

启用 paper 执行（`MIN_AGENT_SHADOW=0`，备份 `/tmp/opencode/env.bak`）后的第一个交易日。10 个周期、
1 次 BUY 被上限拒绝、9 次 HOLD，**持仓纹丝不动**。查下来是两个真实缺陷，而不是"今天没信号"。

### 缺陷一：不可服务的策略被计为能力路线

`e7bb23f` 引入的规则是"库中缺失的能力优先服务"（注释原文：*"A capability the library is
otherwise missing has no other route to being tried, so it is served first"*）。但
`_uncovered_capabilities` 遍历**整个 library，未按 lifecycle 过滤**：

```
声明 SELL 的 5 个策略：
   fixed-size-sell-001            RETIRED
   fixed-size-sell-005            PAUSED   (10-02, error rate)
   fixed-size-sell-006            PAUSED   (10-02, error rate)
   trend-follow-sell-002          PAUSED   (09-29, no exploration evidence)
   fixed-size-sell-20260724-001   PROBATION   <- 唯一可服务

可服务且声明 SELL 的: 1
=> SELL 被计为 5 条路线，不再算"未覆盖"，唯一真实路线得不到优先权
```

于是系统**不能买**（上限拒绝）也**不肯卖**（唯一能卖的东西排在 HOLD 候选后面），
17 股超上限持仓永远无法通过系统减持。这与 `b86990b` 同形：**同一谓词，两侧population 不同**。

### 缺陷二：花完预算但没成交的候选被永久搁浅

修好路线计数后，它仍不被服务。用真实 metrics 查：

```
fixed-size-sell-20260724-001
  lifecycle PROBATION   cumulative_cycles 15 (预算 13)   submitted_orders 0   trade_attempts 15

四个事实合成死锁：
  cumulative_cycles=15>=13  -> _needs_probation=False -> 离开 probation 队列
  submitted_orders=0       -> review 返回 None      -> 不晋升、不暂停、不退役
  lifecycle 仍是 PROBATION  -> 不是 ACTIVE
  它的 15 次尝试全是 shadow 意图或被上限拒绝  -> 永远拿不到证据

陷阱自我封闭：唯一能产生证据的服务，正是搁浅本身取消掉的服务。
```

**第一版修复不完整，且缺口只有在真实数字下才可见**——返回 PROBATION 并不够：

```
判定     : PROBATION -> PROBATION
应用后 needs_probation = False    <- 修好了状态，队列仍跳过它
```

因为 `cumulative_cycles` 由 journal 派生且单调递增，不随 lifecycle 改变。最终用
`probation_restarts` 让每次送回真正重启预算，并由 daemon 与 lifecycle 一同写回（标签变而状态不变
正是上面测到的失败）。最终：

```
应用后 needs_probation = True   重试次数 = 2
served=4 -> fixed-size-sell-20260724-001 (PROBATION)
```

不是无限重试：累计 26 周期需要第二次重启才够，且每次只多给一份预算。

### 顺带修正一个闸门检查

`self-evolution-closes` 变红（*"a ruling carries no evidence"*）。该检查要求每条判定引用
scored decision 与 correct_outcome_ratio，这对"凭反事实证据退役"是对的，对
`PROBATION -> PROBATION` 这种**不改变任何状态**的判定则是错的——它依据累计周期与提交数。
现在跳过同状态转换。这是收窄到原本意图（"selection pressure acted on the scored evidence"），
不是放宽。

### 目前的真实位置

```
reflection generated_at 15:25:49Z   window_cycles 50
probation 队列（cum<13）: trend-follow-20260722/20260714/20260724/20260727 —— 都是 TREND_FOLLOW
已离开队列: fixed-size-sell-20260724-001 (cum=15) 等 6 个
```

**注意最后一条观察**：今天新增了 `trend-follow-20261011-001`，它也是可服务的 SELL 路线，于是
SELL 有了**第二条**路线，能力不再稀缺，`_supplies_capability` 转为 `False`，我们的 SELL 策略
回到 oldest-first 轮转。这是规则的**正确**行为，不是回归——但它意味着减持现在依赖轮转而非优先权。

闸门 52 类全绿、0 失败、1519 次执行 / 64 文件；957 pytest；ruff/mypy 干净。
持仓仍 17 股，还需等它被服务、成交并对账。

## 2026-10-06 13:55 EDT — 完整闭环达成，13 万美元的限制第一次由系统自己纠正

启用真实执行后的第一个交易日收盘前。**持仓 17 股 → 6 股，11 笔全部成交，且恰好停在上限内。**

```
12:20  fixed-size-sell-20260724-001  SELL 1  SUBMITTED   <- 被搁浅的策略获得服务
12:53  trend-follow-20261011-001     SELL 1  SUBMITTED
13:01  trend-follow-20261011-001     SELL 1  SUBMITTED
13:28  trend-follow-20261011-001     SELL 1  SUBMITTED
13:35  trend-follow-20261011-001     SELL 7  SUBMITTED   <- 见下
```

券商侧 5 笔 filled（16:22 / 16:55 / 17:03 / 17:30 / 17:37 UTC），0 笔未成交，**0 笔 BUY**。

### 那笔 7 股是 LLM 的自主合规决策，不是数量改写

策略声明 `quantity=1`，决策是 7。查 `decision_source` 与理由：

```
13:35:18  action=SELL qty=7  source=llm  model=qwen3.8:27b
rationale: price 779.97 is 2.5% below reference 800.0, exceeding the 2% threshold,
indicating a downtrend. Position value 10140.26 exceeds max_position_value 5000.0
(over_position_limit=true). Selling 7 shares reduces position to 6 shares
(~4679.82), bringing it under the 5000 limit while honoring the bearish trend signal.
```

模型自己读出 `over_position_limit=true`，自己解出卖 7 股能让持仓落进上限，然后执行。这正是
`llm_decision.py` 里 `exposure` 字段存在的理由——注释写着 *"This is arithmetic on the broker
positions already in the context, not a hint to trade"*。它生效了。

### 合规性复核：两个上限都满足

```
SPY 6 股 x $774.74 = $4,648.44
  max_position_value  $5,000   合规（余量 $351.56）
  max_total_exposure  $20,000  合规（仅 allowlist 标的计入）
起始 17 股 $13,033.73  ->  现在 6 股 $4,648.44   减持 11 股

_agent_holding("SPY") = 6.0   与券商一致
```

对账一致：agent 从自己的成交事件重放出的持仓与券商完全相同。

### 完整自我优化闭环

```
15:31  fixed-size-sell-20260724-001  PROBATION -> PROBATION  无订单，返回 probation
15:53  fixed-size-sell-20260724-001  PROBATION -> PROBATION  同上（restarts 递增）
16:39  fixed-size-sell-20260724-001  PROBATION -> RETIRED
       "offline validation rejected its own recorded decisions:
        12 scored decision(s), 0 correct, correct_outcome_ratio 0.00"
```

被搁浅 → 返回 probation → 获得服务 → 真实成交 → 决策被打分 → **0/12 正确 → 带证据退役**。
它卖在 780 附近而 SPY 继续上行，所以退出时点是错的，系统如实认定了这一点。

今日周期：36 个，`HOLD 28 / SELL 5 / BUY 3`，3 次 BUY 全部被持仓上限拒绝。
`error_count=0`、`execution_errors=0`、策略级 errors 合计 0、`NRestarts=0`。

### 与今早诊断的对照

今早记录"持仓纹丝不动，原因是两个缺陷"。今日的对照：

| 今早 | 现在 |
| --- | --- |
| 4 个 PAUSED/RETIRED 被计为 SELL 路线 | 路线过滤生效，`uncovered` 只统计可服务策略 |
| SELL 策略搁浅，`needs_probation=False` | 返回 PROBATION + `probation_restarts` 预算重启，被服务 |
| 唯一 SELL 路线无优先权 | 12:20 获得服务并成交 |

**仍然待观察**：第二轮修复后出现的 `trend-follow-20261011-001` 构成第二条 SELL 路线，使
`_supplies_capability` 转 False，减持靠轮转而非优先权——今天它成交了 4 笔（其中一笔 7 股），
所以这条路径也已验证可行。

## 2026-10-06 — 可证伪的目标落地了，同时撤回了这份计划自己的两条claim

外部审查最核心的反驳是：没人证明过这套系统跑赢"什么都不做"，也没人分离过模型的贡献。
两者都可测，但都没被测成别人能自己跑的形式。现在这个形式存在了：`make benchmark`。

```
record            2026-06-09 .. 2026-10-06   16 trading day(s), 1284 cycle(s)
target window     60 trading days - NOT MET (16 of 60)
comparison        whole of record (16 trading day(s)); no windowed comparison exists

vs SPY buy-and-hold
  strategy                     return    vs SPY
  tiny-fixed-size-001          +3.94%     -1.47
  trend-follow-buy-001         +3.85%     -1.56
  fixed-size-buy-001           +1.92%     -3.48
  fixed-size-probe-0001        +1.11%     -4.29
  buy-and-hold (SPY)           +5.41%       ---   <- 要打败的基准

VERDICT           FAIL: 4 of 4 strategies are behind buy-and-hold
                  over the 16 trading days on record. The 60-day window is not
                  covered, so over 60 days the target is neither met nor falsified yet.
```

目标句只有一句，且每个分句都对应今天可测的东西：

> 在既定风险预算内、扣除成本后，于滚动 60 个交易日窗口内跑赢 SPY 买入持有。
> 另外单独报告：同样窗口、同样成交下，LLM 决策相对于确定性基线的增量。

**刻意不写"正收益"**。上涨市里的正收益不构成任何证据，而这四个策略已经有正收益了。

### 分母问题：这份计划的第一版把 16 天印成了 60 天

第一版 `benchmark.py` 取 replay cache 的日期区间，印在**全journal** 收益率的上方：

```
window            2026-07-13 .. 2026-10-05  (60 trading days, target 60)   <- cache
  tiny-fixed-size-001=+3.94% vs market +5.41% (-1.47)                        <- 16 天的周期
```

正好 60。一个恰好落在目标上、却属于另一个窗口的数字，比没有数字更糟：它让 16 天的记录
看起来像 60 天的检验。实测 journal：1284 条周期，**16 个交易日**；47 个已平仓 lot 集中在其中 4 天。

于是 replay cache 从工具里删掉，改为打印记录自身的窗口、把目标窗口报成 `NOT MET (16 of 60)`，
并明说对比是全记录、不伪造窗口化对比。判决同时说两件事：全记录上目标**未达成**，
而在目标自己规定的 60 天尺度上**既未达成也未被证伪**。

窗口化对比本身**故意不建**：16 天的记录上算 60 天窗口不是窗口，是把分母藏起来的除法。
等记录长到窗口有意义时它才可建。

### 撤回：`docs/evidence/fresh-clone.log` 不可以改写路径

原计划 §4 提议把它里面 41 处路径改掉。那是 pip / pytest 的**逐字运行记录**，把真实路径
替换成 `$HOME/...` 之类的占位就是伪造证据。已还原。可选的处理只有两个：

- 保留它，接受一份提交进仓库的日志会写出产生它的账号；
- 重跑 `docs/evidence/run-fresh-clone.sh`，按提交的树重新生成。

日志只能由脚本重新生成，不能手工编辑。

### "新克隆五分钟演示"不成立，也不这么声称

benchmark 需要 `runtime/`，而 `runtime/` 被 gitignore（因为它由交易产生）。新克隆没有
journal、没有券商证据、没有已平仓 lot，工具会如实说无可测量并退出 2。它真正的用处是给
有只读 checkout 的审查者：不要凭证、不要网络、一条命令、一个带分母的数字。

### 顺带修掉的一个真实缺陷：env 文件有两个 reader，门只认一个

`make test` 红了 1 条（此前就红，与本次改动无关）：

```
set in the runtime env but never read by the loader: ['MIN_AGENT_ENVBIN']
```

根因是一个**错误的门**，不是死旋钮。env 文件是双用途的：`minictrl` 在调用
`python -m min_agent.cli` 之前先 source 同一个文件，并从中读 `MIN_AGENT_ENVBIN`
（env 的 bin 目录）。而 `tools/verify.py::check_config_example_names_are_real`
的 `known` 只含 `config.py` 源码 **加上 env 文件本身**——所以把 `MIN_AGENT_ENVBIN`
写进 `configs/paper.env.example` 之后，这台机器会过，**而新克隆（那里没有 env 文件）
会把一个活着的旋钮报成死的**，并诱导人删掉那行让 install 失效的配置。

两处都改了同一个前提：可读者是 `config.py` **和** `minictrl` 的可执行代码
（整行 `#` 注释剔除，因为 minictrl 的头部散文把每个 shell 旋钮都写了一遍，
而散文不是一次读取）。同时把这个旋钮补进 `configs/paper.env.example`——
此前它唯一出现的地方是 minictrl 找不到时的报错信息，那不是一个该学设置名的地方。

这是把门改宽，不是把门放松：不可达的门才是装饰品。

### 个人路径：最后一个真实耦合已清

`git grep -E "liuyime2|/localscratch|/home/[a-z]|/Users/"` 现在只剩两类命中，
且两类都是**故意**的：

- `tools/verify.py` 的 `host_markers` 名单——那是它**扫描**的目标，
  认不出自己要找什么的隐私扫描比认不出更糟；
- `docs/evidence/fresh-clone.log`——上一节说明的逐字运行记录，
  只能由 `run-fresh-clone.sh` 重新生成。

## 2026-10-07 — 测量修正、规则优先决策、做空与 DSL、29 股定价

全部按 `docs/history/plans/2026-10-06-rsi-kernel-review.md` §13 逐项记录（每项对应提交和数字），这里只记关键结论：

- **测量工具本身有错，先修了尺子**：被 Guardian 拒绝的订单被当成交易评分（48 BUY + 12 SELL），SELL 的成本被加回而不是扣除。修正后，真正执行的 SELL 25 次只有 1 次正确。离线筛选的原始比例几乎完全是当天市场方向（13 天里 9 天的结果几乎全部朝同一方向），改为与“当天方向本身能得到的分数”比较后，6 个 REJECT 变为 INCONCLUSIVE。
- **benchmark 口径**：改为同资金、同时间窗口后，fixed-size-probe-0001 从 -4.29 变为 +0.49，跑赢 SPY；总体仍 FAIL（5 个里 4 个落后）。
- **守护进程代码指纹一直在读磁盘文件**，所以“守护进程在跑旧代码”的检查从未能失败过；已修，之后每次改动都重启并核对指纹。
- **规则优先，LLM 改判必须给理由**：首次实盘改判在 11:00，规则 BUY，模型 HOLD，理由是仓位上限已无空间。配对比较（`llm vs rule`）需要 24 小时后才有第一批结果。
- **做空**：代码、Guardian 规则、账本、回测都已就绪，部署处于 `MIN_AGENT_SHORTS=shadow`。账户目前持有 6 股 SPY，Guardian 不允许对持有多头的标的做空，所以要等账户空仓后才可能出现 SHORT 决策；切到 `paper` 需要先看到一个 shadow 交易日的证据。
- **29 股**：按账户所有者自己的成交记录 FIFO 定价——成本 19,408.88，卖出所得 22,230.53，所有者收益 +2,821.65，不计入任何策略的盈亏。
- **未做的一项**：给全新克隆准备的合成 journal（P3.1）。数据模型拒绝 `synthetic` 这类来源，要让它能解析就只能换个名字，这等于绕过“禁止伪造数据”的规则，因此记录为刻意不做。
- **推送失败**：GitHub 对本仓库的每次推送都返回 Internal Server Error（包括只推一个提交），PR 未能创建，提交都在本地 `master`。
