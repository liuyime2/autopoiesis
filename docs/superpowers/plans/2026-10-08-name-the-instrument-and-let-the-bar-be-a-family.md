# Say what the instrument is, show the tape, and stop the multiplicity bar from being the whole answer

Date: 2026-10-08
Status: **PLAN**
Baseline commit: `58d94e5` (HEAD == origin/main)
Scope: `data_gateway`, `models`, `llm_decision`, `daemon`, `research/signal_test`, their tests. No change to
the Guardian, to any configured limit, to `minictrl`, or to the research scripts under `docs/evidence/`.

## 0. The three findings this plan closes

All three are measured, and all three were found in the same working session (2026-10-08).

**F1 — the agent cannot say what it is trading.** `data_gateway` exposes no instrument identity at
all: a symbol is a bare string everywhere from `DataSnapshot` to the decision context. Under
`AUTOPOIESIS_UNIVERSE=tradable` with `ATTENTION_SLOTS=4`, `AgentDaemon._round_symbols()` returns ten
symbols, and two of them are products the research never examined:

```
round = SPY, BIL, TLT, XLB, XLE, XLF, INTC, NVDA, SOXS, BITO
```

`SOXS` is *Direxion Daily Semiconductor Bear 3X ETF* and `BITO` is *ProShares Bitcoin ETF*. The quality
filter in `attention_symbols` (`data_gateway.py:107-140`) removes warrants, units and rights by
symbol suffix and prices below `min_price`; it does not classify what survived, so a 3x inverse
leveraged fund and a bitcoin-futures ETF reach the model labelled only by ticker. The decision
context (`llm_decision.py:302-361`) then tells the model a price and nothing else about the
instrument.

The fix is not to block them. The broker states what each one is, and the earlier research found no
reason to exclude product classes on a hunch. The fix is to **say it out loud**: put the broker's own
asset record and a measured behaviour profile into the context, so the model is deciding about a
*known* instrument.

**F2 — the agent sees no tape and no news.** `grep news src/autopoiesis/*.py` returns nothing. The
decision context carries `last_price`, account, positions, the selected strategy's parameters and
the `risk` block — no bar history, no return series, and no headlines. Verified reachable from this
broker: `GET /v1beta1/news?symbols=INTC&limit=2` returns real Benzinga headlines, and
`GET /v2/assets/SOXS` returns the full name and attributes. The capability simply is not wired.

**F3 — the multiplicity bar has become the whole answer, and it is now too strict to detect anything
real.** `runtime/autopoiesis/signal_trials.jsonl` holds 161 rows for 119 distinct hypotheses, so
`threshold()` sets the bar at `0.05/161 = 3.1e-4`. Two effects that are real and behave as the
literature predicts cannot clear it:

```
fundamentals:EAR->d20    +0.072  p=0.0040   -> NOT_SIGNIFICANT   (post-earnings drift; a published effect)
spy-only:RV21->F21       +0.168  p=0.0050   -> NOT_SIGNIFICANT   (sign positive in 2000-02/2007-09/2020/2022)
spy-only:RV21->FRV21     +0.508  p=0.00025  -> SURVIVES          (volatility persistence)
```

So "nothing survives" does not distinguish *no effect* from *an effect smaller than 161 hypotheses
permit*. That is the same failure the project has corrected four times in other instruments: a
confident number computed under an unstated assumption. Here the unstated assumption is that one
flat Bonferroni family over every hypothesis ever recorded is the right bar for every question.

Second half of F3, found while measuring it: `judge()` keys nothing on `name`, so re-running the same
hypothesis appends a new row and raises the bar. My session re-ran `long_history.py` and added 18
rows whose numbers are identical to four decimal places — a genuine reproducibility check, but the
ledger cannot tell that from 18 new trials.

## 1. What gets built

### 1.1 `InstrumentProfile` — what this instrument is, from the broker and from its own tape

A new model in `models.py` and `AlpacaDataGateway.instrument_profile(symbol)` in `data_gateway.py`:

| Field | Source | Why it is there |
| --- | --- | --- |
| `symbol`, `name` | `GET /v2/assets/{symbol}` | "Intel Corporation Common Stock" vs "Direxion Daily Semiconductor Bear 3X ETF" — the answer to what it is, verbatim, not parsed |
| `asset_class`, `exchange`, `status` | same | us_equity, ARCA/NASDAQ, active |
| `tradable`, `marginable`, `shortable`, `easy_to_borrow`, `fractionable` | same | what the account may actually do with it |
| `last_price`, `annualized_vol_pct`, `avg_dollar_volume`, `bars_used` | real bars, `adjustment="raw"` | measured, not asserted |
| `beta_spy`, `corr_spy`, `corr_qqq`, `corr_tlt`, `corr_gld` | real bars vs the four references | what it behaves like — this is what says "3x inverse" without parsing a name |
| `flags: list[str]` | derived from the two above | e.g. `leveraged_or_inverse` when `|beta_spy|` is far from 1 and `corr_spy` is strongly signed; `non_equity_reference` when `corr_gld` or `corr_tlt` dominates `corr_spy`; `unclassified` otherwise |

`flags` must carry the number it was derived from (`"leveraged_or_inverse: beta -2.7 vs SPY over 120 days"`),
so a reader can see why and disagree. Nothing here is asserted from the ticker.

Caching: one fetch per symbol per day, keyed like `RiskJudgment`'s cache, because a round covers ten
symbols and the profile is read on every cycle.

The bars used for the profile are the same bars `daily_closes` already fetches. The gateway must
fetch them **once per symbol per day** and serve both, or a ten-symbol round doubles its broker
traffic for no new information.

### 1.2 `recent_news` — headlines in the context, labelled as what they are

`AlpacaDataGateway.recent_news(symbol, limit=5)` against `/v1beta1/news`, returning
`NewsItem(symbol, headline, source, created_at, summary)`; `summary` truncated to a stated length so
the prompt budget is a decision rather than an accident.

Wired into the context as a `news` block. `DECISION_INSTRUCTION` gains one sentence stating that
headlines are context describing what has already been reported and are **not** a validated trading
signal — because they are not one: the same session measured the next-day tradable correlation of
every reader at within 0.03 of zero (`docs/evidence/signal-research-2026-10-08/README.md` §5). The
`test_the_instruction_states_every_constraint_the_validator_enforces` correspondence test must still
pass; adding a block adds no constraint, but the sentence is required so the block is not read as
endorsement.

**Token budget is a gate, not a note.** `DECISION_NUM_CTX = 4096` with about 253 tokens of headroom
was measured in `STATUS.md`. The new blocks must report their token cost, and if they do not fit the
new instruction goes in and one of the existing fields comes out. This is measured before commit,
not asserted.

### 1.3 `judge()` — families, replication, and honest power

```
judge(name, selection, test, *, family, control=None, kind="confirmatory"|"exploratory", ledger=None, extra=None)
```

1. **Family counts within the family.** `bar = FAMILY_ALPHA / (hypotheses already recorded in this family + 1)`.
   `family` is required and is the search the hypothesis belongs to (`intraday`, `daily`, `xs`,
   `long`, `spy-only`, `news`, `options`, `fundamentals`, `1min`). The whole-ledger Bonferroni is
   still computed and written to the row as `ledger_bar`, so nothing is hidden by the change.
2. **Confirmatory vs exploratory.** `kind="confirmatory"` is held to the family bar. `kind="exploratory"`
   is *reported* with its uncorrected p and is never allowed to produce `SURVIVES`; its verdict is
   `EXPLORATORY` with the uncorrected p alongside, so a lead can be published and chased without
   being smuggled in as a finding. Round 2's two real effects become `EXPLORATORY`, which is what
   they are.
3. **Replication, not a new trial.** If `name` is already in the ledger, the row is recorded with
   `replicates: <original index>` and `trials_so_far` inherited from the original; the bar does not
   move. If the numbers differ materially from the original, the row also carries
   `replication_delta` so a divergent re-run is visible. A re-run that cannot reproduce is a
   finding, not a duplicate.
4. **Power is stated, not implied.** Every row carries `effective_n` and the smallest effect the
   test could have detected at that n, computed from the block count. A null with no power is
   labelled `UNDERPOWERED` rather than `NOT_SIGNIFICANT`, which is the difference between "we looked
   and saw nothing" and "we could not have seen anything".

Reads `read_ledger` for the family count; the ledger path stays
`runtime/autopoiesis/signal_trials.jsonl`.

### 1.4 Attention: label, do not block

`attention_symbols` keeps leveraged ETFs, commodity ETFs and individual stocks. No new exclusion.
The instrument profile is what tells the model what it is being asked about — that is the fix, and it
is strictly better than a blocklist that would go stale and would have hidden the finding.

## 2. Falsifiers, stated before the code

| Claim | Falsified by |
| --- | --- |
| The broker names the instrument | any symbol in a ten-symbol round whose profile has no `name` |
| The profile distinguishes a 3x inverse fund from an equity | `SOXS` and `INTC` profiles that do not differ in `flags` or `corr_*` |
| The context can carry the new blocks within budget | the token cost exceeding the measured headroom, in which case a field is dropped and this plan says which |
| News is reachable | `recent_news` returning `[]` for an actively traded symbol while the raw endpoint returns items |
| The family bar is at least as strict as intended | a `SURVIVES` verdict whose `p` would fail the whole-ledger Bonferroni |
| Re-running a hypothesis does not cheapen the verdict | the ledger's `trials_so_far` rising on a duplicate `name` |
| The gate still runs | `make verify` below 52 classes, or `verify-self-test` no longer able to fail |

## 3. Explicitly not in scope

- Any change to the Guardian, to `min_confidence`, to the position/exposure/loss limits, or to the
  paper-only refusal. `AUTOPOIESIS_UNIVERSE=tradable` and `AUTOPOIESIS_MANAGE_ACCOUNT=true` are already
  recorded as the account holder's decisions in `runtime/autopoiesis/risk_baseline.json`.
- Removing `SOXS`/`BITO`-class products from the pool. See 1.4.
- New signals. Nothing here claims a direction; the research ledger's verdicts are unchanged by this
  work except for the family correction.
- Anything under `docs/evidence/signal-research-2026-10-08/part2/`, which stays uncommitted until
  asked for separately.

## 4. Order

1. `models`: `InstrumentProfile`, `NewsItem`.
2. `data_gateway`: `instrument_profile`, `recent_news`, shared bars cache. Tests first against the
   live broker, then unit tests with a fake client.
3. `llm_decision`: the two context blocks, the instruction sentence, and a **measured** token delta.
4. `daemon`: pass the profile/news through to the loop; the selected symbol's profile is always
   present.
5. `signal_test`: families, replication, power. Existing ledger rows are **not** rewritten; new rows
   carry the new fields, and the reader tolerates a row without them.
6. Tests, `make check`, `make verify`, the fresh-clone script, then commit.
