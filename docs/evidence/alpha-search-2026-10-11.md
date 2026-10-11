# SPY price-rule search: 2026-10-11

This is a real-data diagnostic run, not evidence that a strategy is ready for promotion. It
records the first complete fetch → fingerprint → walk-forward search loop in the isolated RSI
execution worktree. The raw market bars remain in the ignored runtime cache and are not committed.

## Data and commands

Read-only fetch through the configured paper account:

```sh
make fetch-replay-bars \
  REPLAY_SYMBOL=SPY \
  REPLAY_START=2026-04-10T13:30:00Z \
  REPLAY_END=2026-10-09T19:40:00Z \
  REPLAY_INTERVAL=5Min \
  REPLAY_ROLLING=1
```

The fetch returned 24,237 real 5-minute SPY bars from 2026-04-10 13:30 UTC through 2026-10-09
19:40 UTC and wrote `runtime/autopoiesis/replay/SPY_5Min_rolling.json`. Recorded source:
`alpaca historical bars; not synthetic`. SHA-256 of the ordered timestamp/close series consumed
by the walk-forward driver:

```text
sha256:9137cff68e231f520d1b8ceaa7f3cb8592398162d22088ea0ee508afcd70296b
```

Search command: `make research-search REPLAY_SYMBOL=SPY REPLAY_CACHE=runtime/autopoiesis/replay`.
It evaluated the existing 10 TREND_FOLLOW thresholds plus one FIXED_SIZE buy-and-hold candidate,
with the existing walk-forward, cost, and stress rules. The full first run took about 2 minutes
53 seconds and appended 11 trials to the ignored runtime ledger.

## Results

| Candidate | Verdict | OOS return | OOS trades |
|---|---:|---:|---:|
| TREND_FOLLOW 0.0005 | OVERFIT | +0.010% | 115 |
| TREND_FOLLOW 0.0010 | OVERFIT | +0.013% | 81 |
| TREND_FOLLOW 0.0020 | OVERFIT | -0.011% | 53 |
| TREND_FOLLOW 0.0050 | INSUFFICIENT | -0.041% | 33 |
| TREND_FOLLOW 0.0075 | INSUFFICIENT | +0.069% | 22 |
| TREND_FOLLOW 0.0100 | INSUFFICIENT | -0.006% | 19 |
| TREND_FOLLOW 0.0150 | INSUFFICIENT | -0.066% | 10 |
| TREND_FOLLOW 0.0200 | INSUFFICIENT | +0.027% | 2 |
| TREND_FOLLOW 0.0250 | INSUFFICIENT | -0.004% | 2 |
| TREND_FOLLOW 0.0300 | INSUFFICIENT | -0.025% | 1 |
| FIXED_SIZE buy-and-hold | OVERFIT | +0.383% | 34 |

Total: 11 trials, 0 passed; 4 OVERFIT and 7 INSUFFICIENT. Positive OOS return alone did not
clear the existing generalisation/sample gate. This result does not justify admitting a strategy,
loosening the trade-count rule, or claiming alpha. The sweep is a narrow price-only family over a
single recent window and is diagnostic, not a preregistered final holdout.

## Efficiency follow-up

Before the repeat-run fix, `search()` reran every walk-forward fold even when it labelled all 11
candidates as repeats and would append nothing. After persisting the exact data fingerprint, the
same search returned the 11 stored outcomes with `backtests_run=0`, `repeats=11`, and no ledger
growth in 5.461 seconds. This is roughly a 31× wall-time reduction for an unchanged cache; new or
changed data still takes the full path.

The experiment confirms the fetch/search loop and exposes a useful negative result. It does not
validate a second independent holdout, broader regimes, LLM decision quality, RSI treatment versus
control, or the 60-trading-day paper objective.
