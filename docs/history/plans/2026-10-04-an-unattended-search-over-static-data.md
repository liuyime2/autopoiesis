# Autonomous exploration that keeps running needs data that keeps arriving

Date: 2026-10-04
Status: **DONE - the search fetches and runs on a daily timer; it still finds nothing**

Baseline commit for rollback: `b8eb9c5`
Scope: `tools/research.service.in`, `tools/research.timer`, `minictrl install-service`, and
`src/autopoiesis/research/driver.py`. No change to production code, the daemon, any risk limit, or
the production/research separation.

> **Scope correction, recorded after writing it.** The first draft of this plan scoped out the
> research driver too. That was wrong, and the falsifier caught it: `tools/fetch_replay_bars.py`
> names its cache file after the window it fetched (`SPY_5Min_2026-05-01_2026-10-03.json`), so a
> daily rolling fetch writes a *new* dated file every run and `_load_real_bars` - which took the
> single largest file - would have pinned the search to whatever window happened to be widest,
> permanently ignoring the newest tape. The driver now merges every cache file and deduplicates by
> timestamp. That is inside `src/autopoiesis/research/`, which is not production: no top-level
> `src/autopoiesis/*.py` module is touched, and `check_production_research_separation` is unaffected.

---

## 0. The question

`2622bb3` made exploration unattended: `src/autopoiesis/research/driver.py` runs, sweeps, records
trials and returns verdicts with nobody deciding anything. But unattended is not the same as
continuous, and the difference here is the dataset.

```
bars: 20448 from SPY_5Min_2026-05-01_2026-10-03.json
candidates: 37 (cumulative: 37 already in the ledger)  required_trades: 61  repeats: 11
```

**`repeats: 11` is every candidate.** The driver's only input is the replay cache, the cache is
written by `tools/fetch_replay_bars.py`, and that is a command a human types. So the search would
run on a schedule and correctly decide nothing, forever - idempotent, green, and incapable of
ever learning anything, because nothing new would ever arrive.

The journal is not an alternative source. It carries no price series:

```
events carrying a price: 0
only ORDER_FILL_CONFIRMED has one ('filled_avg_price'), 62 events total
```

So there is exactly one real price series in this system and it is fetched by hand.

## 1. Two ways to close it, and which is right

**Modify production so the daemon persists each cycle's price.** It already sees every price
through `data_gateway.snapshot()`, so this is nearly free at runtime. Rejected for this unit
anyway: it puts market data into the production process, changes the journal schema or adds a new
production-managed file, and makes the dataset's provenance depend on the daemon being healthy.
A dataset that silently stops growing because the trading daemon crashed is worse than one that
stops growing because a fetch failed loudly.

**Schedule the existing fetch, then the existing search.** No production change, credentials
already reach systemd through the same `EnvironmentFile` the watchdog uses, and both halves
already exist and already refuse to invent data. Chosen.

The failure modes are the reason this is safe to automate: `fetch_replay_bars.py` returns 1 and
writes nothing if the broker has no bars, and the driver exits non-zero if there is no cache. A
network failure therefore stops the unit with a real reason instead of quietly producing verdicts
on invented prices.

## 2. What gets installed

```
tools/research.timer        -> quant-research.timer   (daily, jittered, Persistent=true)
tools/research.service.in   -> quant-research.service (oneshot: fetch, then search)
```

`minictrl install-service` already renders `@ROOT@`, `@PYTHON@`, `@ENVBIN@` and `@ENVFILE@` and
already enables `quant-watchdog.timer`, so the research unit joins that list rather than
introducing a second convention.

`ExecStart` is the fetch **then** the search, as two commands, with the search unconditional: a
failed fetch means no new bars, not no search, and the search on an unchanged cache is a cheap
no-op by the idempotence `2622bb3` added. Making the search depend on the fetch succeeding would
mean a broker outage silently stops research, which is the coupling being avoided.

## 3. Falsifiers, stated before the code

| Claim | Falsified by |
| --- | --- |
| The dataset is manual | a scheduled run leaving the cache's bar count unchanged with the fetch succeeding |
| The unit is real | `systemd-analyze verify` rejecting it, or `systemctl --user` not seeing it |
| No production change | `git diff --name-only` touching `src/autopoiesis/**` |
| The search still refuses to invent data | the service succeeding with an empty or missing cache |

## 4. Verification

- `systemd-analyze verify` on the rendered unit, as `minictrl` already does for the other three.
- `minictrl install-service`, then `systemctl --user start quant-research.service` and its journal.
- A real run: the fetch extends the cache or reports why it could not, and the search then records
  or reports every candidate as a repeat. **A run that records nothing is a success**, provided the
  fetch is the reason and the reason is visible.
- `make verify` - 52/52 classes, 0 failed.

## 5. Explicitly not in scope

- Any change under `src/autopoiesis/`. The driver and the fetcher are already correct; only their
  scheduling is missing.
- Making the daemon persist prices, for the reasons in §1.
- Changing the fetch window. The service fetches a rolling window ending today, which is what
  makes the cache grow.
- Wiring verdicts into production admission. Still unmeasured, still out of scope.
---

## Result

Installed and verified on the real host. `quant-research.timer` is scheduled for
`Mon 2026-10-05 06:55 EDT`, and the pipeline was run end to end by systemd:

```
bars: 24162 from 5 file(s): SPY_5Min_2026-04-04_2026-10-03.json (24162), ... [alpaca historical bars; not synthetic]
candidates: 48 (cumulative: 37 already in the ledger)  required_trades: 70  repeats: 0
```

The fetch wrote a new dated file, the driver merged and deduplicated all five (54,089 raw bars
to 24,162 unique), the larger dataset made every candidate a new trial, 11 were recorded, and the
evidence bar rose from 61 to 70. Gate: **52 classes, 0 failed, 1478 test executions across 63
files**, and 12 driver tests.

### Three defects, all found by running the real thing

**The rolling fetch always failed.** The first real run logged
`subscription does not permit querying recent SIP data`, fell through to searching the stale
cache, and exited 0 — so it *looked* like a successful unattended run for a day. Measured: a
window ending 2026-10-03 returned 4338 bars, and the same window ending 2026-10-04 failed. The
paper account cannot be asked for the current day's tape, so a window ending "now" is a window
that never works. Now ends yesterday, which is always a completed session.

**The timer was enabled and never ran.** `install-service` used `systemctl --user enable`, which
only arranges for a timer to fire at next boot. `list-timers` showed
`quant-research.timer` with no next run, `Active: inactive (dead)`, `Trigger: n/a`, while the
watchdog beside it was `active (waiting)` — and `minictrl service start` starts only the daemon,
so the printed hint could not start it either. This is the same false success the surrounding
comment warns about, three lines above where it happens. Now `enable --now`, and the started state
is *checked* rather than assumed.

**The ledger gate forbade the behaviour the driver exists to produce.** Re-evaluating a candidate
on a larger dataset is a new trial, and keying duplicates on `strategy_id` alone refused it. The
ledger settles which model is right:

```
search-trend-follow-0p0005  bars=20448  oos=87 trades
search-trend-follow-0p0005  bars=24162  oos=155 trades
0 duplicate (strategy_id, bars) pairs
```

Identity is rule *and* dataset. Verified both directions against the real check: a genuine
double-count on the same bar count still FAILs, and the same rule on new data PASSes.

### What this does not do

It does not make the search find anything. It makes the search keep running on data that keeps
arriving, which is what was missing. The verdict is still `OVERFIT` for every candidate that
clears the evidence bar, and that remains the honest answer.
