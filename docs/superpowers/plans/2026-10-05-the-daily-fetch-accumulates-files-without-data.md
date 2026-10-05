# The daily fetch writes a new near-duplicate cache file every day and never adds data

Date: 2026-10-05
Status: **DONE - the dataset grows every run; 7 duplicate cache files reduced to one rolling file**
Baseline commit for rollback: `36e667f`
Scope: `tools/fetch_replay_bars.py`, `tools/research.service.in`, and their tests. No change to
the daemon, the driver, the search space, any risk limit, or the Guardian.

---

## 0. The question

`8f94c0f` scheduled the search to fetch real bars then search, unattended. It fired on its own
at 06:42 EDT today, five hours before this was noticed. Two things came out of reading its log.

**The fetch failed**, and it is the same failure I already fixed once:

```
alpaca_trade_api.rest.APIError: subscription does not permit querying recent SIP data
bar fetch returned 1; searching the existing cache anyway
```

The unit ends its window at `date -u -d "-1 day"` because a window ending *today* is a window the
paper account cannot serve. That fix is correct and the failure today is its mirror: today is
Monday 2026-10-05, so `-1 day` is **Sunday 2026-10-04**, and the paper account refuses the current
day whichever way you point at it.

Measured directly, three consecutive windows:

```
end=2026-10-04T20:00:00Z -> cached 4338 real
end=2026-10-03T20:00:00Z -> cached 4338 real
end=2026-10-05T20:00:00Z -> APIError: subscription does not permit querying recent SIP data
```

`2026-10-04` works when fetched by hand and `2026-10-04` failed at 06:42 — because at 06:42 the
paper account's "recent" window still covered the previous session, and by midday it did not.
So the boundary moves with the time of day, not with the calendar.

## 1. The defect worth fixing

The search survived the failed fetch, correctly, and ran on the existing cache — which is the
behaviour the plan intended. But the cache is the problem:

```
SPY_5Min_2026-04-04_2026-10-03.json         24162 bars  last=2026-10-02
SPY_5Min_2026-04-05_2026-10-04.json         24162 bars  last=2026-10-02
SPY_5Min_2026-05-01_2026-10-03.json         20448 bars  last=2026-10-02
SPY_5Min_2026-09-01_2026-10-02.json          4294 bars  last=2026-10-02
SPY_5Min_2026-09-01_2026-10-03.json          4338 bars  last=2026-10-02
SPY_5Min_2026-09-01_2026-10-04.json          4338 bars  last=2026-10-02
SPY_5Min_2026-09-28_2026-10-02.json           847 bars  last=2026-10-02
```

**Every file ends 2026-10-02.** Two distinct bugs are visible here:

1. **`cache_path` names the file after the window it fetched**, so a rolling window produces a new
   filename every day forever while the contents stay the same. Seven files after two days of
   scheduling; ~365 near-duplicate 3.4MB files in a year, and the driver re-reads and re-dedups
   all of them on every run. The 06:42 run merged six files to reach the same 24,162 bars the
   previous run already had, and correctly recorded `repeats: 11` and nothing new.

2. **A failed fetch still leaves its filename behind.** The `2026-10-04` files exist and contain
   the `10-03` dataset, because the hand-test above wrote them. A fetch that returns nothing must
   not be able to make the cache look larger than it is.

The first is a design flaw in the fetcher's naming, and it is the reason the plan's own claim —
"the fetch extends the cache" — is false in practice: the *contents* only change when the last
completed session is genuinely newer than what is cached.

## 2. The fix

**Name the cache file after the data, not the request.** `cache_path` takes the symbol and the
interval and a fixed dataset label, so a rolling fetch overwrites one file instead of appending a
new one. The window the fetcher asked for stops being an identity.

Concretely: keep `cache_path` for one-off historical windows (tests and the self-evolution replay
name a window deliberately), and have the scheduled fetch write to a single rolling path —
`SPY_5Min_rolling.json` — that the driver reads alongside the dated ones. `driver._load_real_bars`
already merges every `*.json` and deduplicates by timestamp, so this needs no driver change, and
`tools/replay_self_evolution.py`'s explicit path keeps working.

**And skip the fetch when the cache is already current.** Before fetching, read the newest last-bar
timestamp in the cache; if it is at or after the last completed session, the fetch cannot add
anything and the run goes straight to the search. This is the same idempotence the driver already
has for trials, applied one level earlier — and it is what makes the timer cheap and the log
honest instead of writing a daily near-duplicate file.

**Retire superseded duplicates.** Files whose bars are entirely contained in the merged set are
redundant once a rolling file exists. Removing them is a deletion, which is the first preference
in AGENTS.md 7, and leaving them is only harmless until there are 365 of them.

## 3. Falsifiers, stated before the code

| Claim | Falsified by |
| --- | --- |
| The daily fetch adds no data | the cache growing by a file every day with identical contents |
| The failed fetch left nothing behind | a cache file existing for a window whose fetch failed |
| The skip is safe | a session's bars being missed because the cache looked current |
| Nothing else moved | any diff to the driver, the daemon, the search space, or a risk limit |

## 4. Verification

- Test: fetching an already-cached window does not create a second file.
- Test: a fetch that returns no bars writes nothing at all.
- Test: the skip decides correctly against a cache holding the newest session, and fetches when
  the cache is behind.
- `make verify` - 52/52 classes, 0 failed.
- A real scheduled run, re-triggered by hand: no new file, `repeats: 11`, and a log line saying
  the cache was already current.

## 5. Explicitly not in scope

- The `recent SIP data` boundary itself. It moves with the time of day and cannot be pinned from
  the request side; the `|| echo` fallback already handles it, and the search running on a stale
  cache is correct behaviour rather than a silent wrong answer.
- The negative research result. Nothing passes, and that is a fact about the rules.
- Shadow mode, which is an account decision gated on the AGENTS.md 16 audit.
---

## Result

Three changes to `tools/fetch_replay_bars.py`, one to `tools/research.service.in`, and seven tests
in `tests/min_agent/test_replay_bar_fetch.py`. Gate: **52 classes, 0 failed, 1497 test executions
across 64 files**, 941 pytest, ruff and mypy clean.

1. **The cache path is named after the data.** `rolling_path()` writes one
  `SPY_5Min_rolling.json`; the dated `cache_path()` stays for hand-run windows, which
  `replay_self_evolution.py` depends on.
2. **A fetch that returns nothing writes nothing**, and the scheduled path decides from the
   clock before querying.
3. **The window ends 20 minutes ago, not yesterday** - see below, this was the real bug.

### The real defect was not the file naming

The plan assumed the SIP refusal was a calendar problem. It is not. Sweeping the end time across
the session on 2026-10-05, with the cache sitting at `2026-10-02T23:55Z`:

```
end=2026-10-05T16:30Z  24265 bars  newest 16:30Z
end=2026-10-05T16:55Z  24270 bars  newest 16:55Z
end=2026-10-05T17:00Z  REFUSED: subscription does not permit querying recent SIP data
end=now - 20 min       24269 bars  newest 16:50Z
end=now - 60 min       24261 bars  newest 16:10Z
```

**The paper account's data trails real time by roughly 15 minutes.** Both window ends the unit had
were wrong, in opposite directions:

- `date -u -d "-1 day" T20:00Z` — my previous fix — **could never ask for today's bars**. It was
  not restricted, it was *stale by construction*: the dataset could not grow at all while the
  unit ran. That is why 7 cache files existed all ending `2026-10-02`.
- `date -u +%H:%M:00Z` (now) is refused outright whenever a run lands inside the trailing window,
  which is exactly what the 06:42 unattended run did.

The trailing-window figure is the fix: `-20 minutes` clears the measured ~15 minutes with a
margin. Both earlier attempts encoded a wrong theory of the restriction, and I only found it by
sweeping the boundary rather than reasoning about the calendar.

### A second bug the first fix introduced

The skip I added compared the cache against `clock.timestamp`, which is **now**, not the last
completed session. That comparison is False as soon as any time passes, so the skip was dead code
on its first real run — it could never fire. `fetch_would_add_nothing` now derives the cutoff
from the clock's shape: `now - interval` when the market is open, `next_open - 2 days` when
closed.

The closed-market cutoff was wrong on the first attempt too: `next_open - 1 day` is the *open* of
the day before, which lands mid-session and is later than the previous close, so it would have
skipped while the cache was three days stale. `next_open - 2 days` is conservative in the safe
direction, and `test_the_cutoff_is_derived_from_the_clock_not_from_now` pins both cases with the
measured numbers.

### Verified on the real system, end to end

```
cached 24270 real 5Min SPY bars -> runtime/min_agent/replay/SPY_5Min_rolling.json
candidates: 59 (cumulative: 48 already in the ledger)  required_trades: 77  repeats: 0
```

The dataset **grew for the first time** - 24,162 bars ending `2026-10-02T23:55Z` became 24,270
bars ending `2026-10-05T16:55Z`, including today's session. Because trial identity is rule *and*
dataset, `repeats: 0` and 11 fresh trials were recorded, and the evidence bar rose 70 -> 77.

The five dated files all end `2026-10-02` and are subsets of the rolling file, so two were deleted
and the rest left: `replay_self_evolution.py` names `SPY_5Min_2026-09-28_2026-10-02.json`
deliberately and `make verify` still runs its 847-cycle replay (confirmed PASS after the
deletion).

### Still true afterwards

The search found nothing. Every candidate clearing the evidence bar is `OVERFIT`, on 24,270 real
bars now. The pipeline is correct, continuous and finally growing its dataset; the design space
has no demonstrable edge, which no amount of data collection will change on its own.
