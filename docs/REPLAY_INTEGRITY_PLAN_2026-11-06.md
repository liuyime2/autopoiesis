# Replay Integrity Plan — 2026-11-06

## 1. Why this exists

The four-arm smoke run committed on 2026-11-06 (`0dce796`) is **not evidence**. The
simulator added for G3 (`src/autopoiesis/research/simulation/`) bypasses the production
Guardian and has no cash constraint, so the reported returns are a leverage artifact.
This plan replaces that simulator before any further experiment is run.

The repository already contains a production-faithful replay:
`src/autopoiesis/replay.py` (`ReplayBook`, `ReplayDataGateway`, `ReplayExecutor`) and
`tools/replay_self_evolution.py`, which drives the real `TradingLoop` and real `Guardian`.
The G3 work duplicated it with a weaker implementation. Per `AGENTS.md` §7, the duplicate
goes first.

## 2. Baseline (measured, not estimated)

Command:

```
python /tmp/opencode/diag_leverage.py
```

It reproduces the committed B/C decision rules over **1,579 real SPY IEX daily bars**
(2020-07-27 .. 2026-11-05), starting equity 100,000, commission 0.5%, slippage 0.1%.

| arm | fills | final equity | max drawdown | max abs qty | max gross exposure | min cash |
|---|---:|---:|---:|---:|---:|---:|
| B (SMA20 ±1%) | 1,080 | 174,076.59 | 24.01% | 400 | 311,212 | -119,386.48 |
| C (SMA20/50) | 1,009 | 205,030.63 | 30.71% | 602 | 466,040 | -236,489.83 |

Facts established by that run:

1. Gross exposure reached **4.66x** starting equity.
2. Cash went to **-236,489.83**, i.e. the simulator borrowed without limit.
3. `research/simulation/runner.py` never calls `Guardian.review`; `hard_risk_breaches`
   is never incremented.
4. Fills use the **same bar close** that the decision used (`run_replay` passes
   `price=bar["price"]` straight to `account.apply_fill`), not the next bar's open.
5. Position size is fixed at **1 share per signal** with no cap; the strategy accumulates
   hundreds of shares because nothing stops it.
6. The "D arm LLM" called Ollama once, printed the response, then **ignored it**; the
   replayed rule used hard-coded `1.005/0.995` thresholds. The driver script lived only in
   `/tmp`, so the committed report is not reproducible.

## 3. Problem statement

The experiment currently measures "how much unlimited leverage a rule would use in a bull
market", not "whether a rule has an edge under the system that actually trades". Every
number produced by this path is therefore invalid, including the `INSUFFICIENT` verdict.

## 4. Required invariants

A replay is valid only if all of these hold:

- **I1 — one path.** Only `autopoiesis.replay` primitives are used. No second account,
  clock, or executor.
- **I2 — real Guardian.** Every BUY/SELL passes `Guardian.review` with the paper config.
- **I3 — no leverage.** `book.cash >= 0` at all times; a BUY is refused when
  `buying_power < order_value`.
- **I4 — mandate.** Resulting position value `<= max_position_value`; gross exposure
  `<= max_total_exposure`; `max_trades_per_day` respected.
- **I5 — no same-bar fill.** A decision made from bar `t` fills at `t+1` open; the last
  bar cannot fill.
- **I6 — costs.** Slippage and commission are explicit parameters; the default is zero to
  preserve existing replay behaviour, and every experiment declares its values.
- **I7 — reproducibility.** Every run writes a manifest containing: spec digest, dataset
  fingerprint, config digest, code commit, seed, cost model, arm definitions.
- **I8 — honest statistics.** Multi-seed aggregation is correct; D−C is the primary
  comparison; Holm/FDR is applied to the declared family; an arm that did not run is
  reported as `NOT_RUN`, never as a placeholder with the same rule as another arm.

## 5. Work items

### P0.1 — Delete the duplicate simulator

- Delete `src/autopoiesis/research/simulation/` (4 files).
- Rewrite `research/experiment.py` on `ReplayBook`, `ReplayDataGateway`, `ReplayExecutor`,
  and `Guardian`.
- Acceptance: `grep -rn "research.simulation" src/ tests/ tools/` returns nothing;
  `tests/autopoiesis/test_replay.py` still passes.

### P0.2 — Enforce risk and per-day accounting

- Use the paper config's `max_position_value`, `max_daily_loss`, `max_total_exposure`,
  `max_trades_per_day`.
- Reset `book.day_start_equity` and the trade counter when the UTC date changes.
- Record every refusal with its Guardian reason.
- Acceptance: a synthetic BUY large enough to breach each limit is refused; `min_cash >= 0`
  on the real run.

### P0.3 — Correct fill timing

- Use `ReplayExecutor` (`next_open`), not the decision bar's close.
- Skip the final bar's order so the clamped `next_open` cannot fill in the past.
- Acceptance: for a one-bar decision, the fill price equals the next bar's open.

### P0.4 — Costs

- Add `slippage_pct` and `commission_pct` to `ReplayExecutor` (default `0.0`).
- Effective buy price `p * (1 + slippage + commission)`, sell
  `p * (1 - slippage - commission)`.
- Acceptance: a test asserts the exact cash change; a 2x-cost stress run is reported.

### P0.5 — Manifest and pre-registration link

- Write `reports/YYYYMMDD/run_manifest.json` with the fields in I7.
- `prereg verify-run <manifest>` must fail when the spec digest or dataset fingerprint
  differs.
- Acceptance: a deliberately edited manifest fails verification.

### P0.6 — Statistics

- Aggregate seeds correctly (do not overwrite `by_arm` with the last seed).
- Add a two-sided bootstrap p-value; apply Holm to `{D-C, C-A, B-A}`.
- Require both `ci_low > 0` and `mean_diff >= min_effect` before `PROVEN`.
- If D did not run, report `NOT_RUN` for D and `INSUFFICIENT` for the primary comparison.
- Acceptance: a test with two synthetic seeds shows both are used; the Holm-adjusted
  p-values are monotone.

### P0.7 — Negative controls

- Add random-direction, reversed-rule, shuffled-label, and future-injection controls.
- Acceptance: each control is rejected or reports the expected failure mode.

## 6. Re-run and acceptance

After P0.1–P0.6, re-run A/B/C on the same 1,579 SPY daily bars under the paper Guardian.
D stays `NOT_RUN` until a real weekly LLM engine with persisted request/response hashes
exists. The run is accepted only when:

```
python -m pytest -q tests/autopoiesis/test_replay.py tests/autopoiesis/test_experiment.py
python tools/run_experiment.py --spec configs/experiment_4arm.spec --out reports/YYYYMMDD
```

produce a manifest and a report whose `min_cash >= 0`, `max_gross_exposure <=
max_total_exposure`, and whose D row is `NOT_RUN`.

## 7. Non-goals

- No new data sources (VIX, FRED, EDGAR, news) in P0; that is G2.4, next.
- No deployment, no live/paper order, no change to production Guardian limits.
- No profit claim. A replay profit is not a profit.
