# Pre-registration and the tape gate: what they found on the first real run

Date: 2026-10-11. This records the first time the pre-registration mechanism and the data-quality
gate were pointed at the tape a real search had consumed, and what they said. It is not evidence
that any strategy works.

## The mechanism

`src/autopoiesis/research/prereg.py` holds two halves of one question - "can this experiment be
trusted?" - from either side.

**`register` / `verify`.** A spec states the hypothesis, the instrument, the window, the rule, the
primary metric, the family and the expected trade count *before* the run. Registering writes a copy
named by the spec's own SHA-256 under `runtime/autopoiesis/preregistered/`; `verify` recomputes the
hash of the file on disk, so a spec edited after the fact no longer matches its registration and the
verification says so. A hypothesis stated after the data was examined is a description of the data,
not a hypothesis.

**`check_tape`.** The data-quality gate, run between fetching and searching. It refuses a tape with
a duplicate timestamp (which breaks the fingerprint, because the fingerprint is over ordered
(timestamp, price) pairs), a non-chronological series, a price that is not a usable positive number,
or a bar outside the window the spec declared.

## What it found

The spec used here declared a SPY window of 2026-04-10T13:30Z .. 2026-10-09T19:40Z. The tape the
driver would consume for that instrument is every SPY bar file in the cache merged by timestamp,
which is what `_load_real_bars` does. `make research-check-tape` over the real cache:

```
symbol: SPY   files: 8 of 31 in the cache
  bars: 134425 over 3931.8 days (2016-01-04 05:00:00+00:00 .. 2026-10-09 23:55:00+00:00)
  110064 problem(s):
    ... by kind: 110014x before the declared window, 50x after the declared window
```

**The tape spans 2016 to 2026; the spec declared six months.** 110,014 bars precede the declared
window and 50 follow it. So the search that recorded those trials ran over a decade of SPY it never
said it would use, and the evidence record for that run describes "24,237 real 5-minute SPY bars from
2026-04-10" - which is one file, not the merged series the driver actually reads.

That is not a corruption and not a broker fault. It is the exact failure this gate exists for: the
hypothesis claimed a window, the run used a different and much larger one, and every verdict in the
ledger is therefore a statement about data outside its own specification. The gate returns 1 and the
search does not start.

## What is not claimed

- No strategy has been shown to work. The trials on record are a diagnostic over a tape that did not
  match its spec; they are not evidence either way.
- The registered spec used above is a demonstration of the mechanism, not a hypothesis anybody has
  committed to. It is written in `/tmp` and is not part of the repository.
- Nothing here promotes anything. `verdict` is a research signal, and the offline screen still
  decides admission.

## The fix, and what it proves

`driver._load_real_bars` now takes the spec's window and filters the merged series to it, and the
gate checks the tape *that loader produces* rather than one it assembled itself. That second part
matters as much as the first: two halves that each built their own idea of "the tape" could agree
about a series neither of them searched, so the gate is now the same code path as the search.

Against the real cache, with the same spec:

```
symbol: SPY   files: 8 of 31 in the cache
  bars: 24361 over 182.3 days (2026-04-10 13:30:00+00:00 .. 2026-10-09 19:40:00+00:00)
  usable
the tape is the one the spec declared, so a verdict from it means what it says
```

134,425 bars became 24,361, all inside the declared window, and `make research-check-tape` returns 0.
So the loop the first run refused now runs: register the spec, fetch, check the tape, search, and the
fingerprint recorded with each trial is the fingerprint of the tape the spec declared.

## What is still not claimed

No verdict from that tape has been produced by this change, and none should be read as a result. What
changed is that a verdict from it would now mean what it says. The candidates on the existing ledger
were recorded against the unfiltered tape and are not comparable to anything a windowed run
produces.

## The search that ran, and what it said

With the window applied, a search over the same cache and spec ran end to end:

```
bars: 24361 ... dataset_fingerprint: sha256:9d2b1f7a38a6f335fb5636f833b8a5635b8c76e737b05b6131d2a0e8a8ebdb81
candidates: 121 (cumulative: 121 already in the ledger)  required_trades: 110  backtests_run: 11
  search-trend-follow-0p0005  OVERFIT       oos +0.024% over 197 trades
  search-trend-follow-0p0010  OVERFIT       oos +0.032% over 140 trades
  search-trend-follow-0p0020  INSUFFICIENT  oos -0.031% over 102 trades
  ...
  search-fixed-size-buy-hold  OVERFIT       oos +0.115% over 110 trades
trials_run: 11  passed: 0
```

Eleven candidates, zero passed, on the tape the spec declared. **The same eleven candidates on the
decade-long tape were also zero passed**, so the window change did not produce a different answer -
it produced an answer that means what it says. A second run of the identical search reused all
eleven (`repeats: 11, backtests_run: 0`) rather than recomputing them, which is the property the
dataset fingerprint exists for.

One defect surfaced while reading the ledger back: `search()` had the `fingerprint` parameter and the
payload had the field, and the one call site did not connect them - so all 121 rows read
`dataset_fingerprint: null`, and the deduplication was falling back to the bar count. The ledger now
carries a fingerprint per row, and the runs before and after the window filter are distinguishable.

**Not claimed:** that any of these candidates works. Zero passed is a result, and it is the same
result as before - which is the point. What changed is that the number is now about the data the
hypothesis declared.
