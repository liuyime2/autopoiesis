# A promoted strategy must trade, or promotion is meaningless

Date: 2026-10-04
Status: **DONE - the incumbent is served 1 in 5; whether that is the right share is a live measurement**
Baseline commit for rollback: `49ff013`
Scope: `src/autopoiesis/strategy_engine.py` and its tests. No change to the admission cap, the
probation budget, the screen, or the Guardian.

---

## 0. The question

The objective asks for a system that optimises itself. Measured on the live record, the loop
cannot act on what it learns:

```
transitions to ACTIVE, all time: 8   (six mid-June, two on 2026-09-30, none since)
times the promoted strategy was selected in the 146 cycles after promotion: 0
```

A strategy that is promoted and then never runs has not been promoted. The loop has produced
one promotion in the current era and it has never traded.

## 1. Why, measured

`StrategySelector.select` returns from the probation queue whenever **any** probation
candidate is actionable at the current price, and considers the incumbent only otherwise.
Admission refuses new candidates while the backlog is at the cap, and reopens the moment it
falls below — so the backlog sits at the cap and the queue is never empty.

```
cap (PROBATION_CYCLES)                13
backlog now                           17 of 17 PROBATION
actionable at the last price          9      (53%)
expected actionable at backlog 13     6.9
P(no actionable candidate)            (1-0.53)^13 = 5.6e-05
cycles to drain the actionable queue  ~89  — during which admission refills it
```

**5.6e-05 per cycle.** That is not "the incumbent is rarely chosen"; it is that the
incumbent is unreachable in practice. And the drain cannot fix it, because serving a candidate
is what lowers the backlog, which is what reopens admission.

This is the second structural blocker found in the audit, and it is the one that matters most:
the first (the research gate) stops evidence being produced, this one stops evidence being
*used*.

## 2. Why not just reorder

Serving the incumbent first whenever it is actionable is the obvious change and the wrong one.
It would fund exploitation entirely from discovery, emptying the probation queue, and undo the
admission cap that was just added for the opposite reason. The trade the selector's own
docstring accepts — "a day of the incumbent's throughput to find out whether anything else
does" — is only affordable if the incumbent *has* 13-cycle budgets' worth of throughput to
trade. It does not, because the queue is refilled as it drains.

The honest position is that the split is a real trade with no obviously correct answer, and
the loop cannot learn which way is right because it has never run the experiment. So the
minimal change is the one that makes the experiment possible at all: **give the incumbent a
reserved, stated share of selections, and keep the rest with probation.**

## 3. The change

One constant and one branch, evaluated before the probation queue:

```python
#: Selected cycles between which an actionable ACTIVE strategy is served.
INCUMBENT_SHARE = 5
```

- When a strategy is `ACTIVE` **and** declares an action at the current price, and the total
  service served across the library is a multiple of `INCUMBENT_SHARE`, `select` returns it.
- The cadence is derived from `sum(result.cumulative_cycles)`, so it is **stateless** — no
  counter to persist, nothing to reset, and it degrades to today's behaviour when no ACTIVE
  strategy exists or none is actionable.

Twenty per cent of cycles. Enough for the incumbent to open and close lots — 35 lots came from
62 orders over 1179 cycles, so roughly a dozen cycles is several lots — and enough to leave
discovery four fifths of the throughput.

**Why stateless.** Anything that counted selections itself would need state that survives a
restart and is consistent with the journal, and the journal already holds the number. Deriving
it means the cadence cannot drift from the record.

**Why not reduce the admission cap instead.** A smaller cap would drain the queue and let the
incumbent through, but it does the same thing by starving discovery, and it also weakens the
guard added for the measured admission deficit. This keeps both: the cap stays, and the
incumbent gets a share regardless of queue depth.

## 4. What this does and does not do

It makes promotion mean something: a promoted strategy will trade. That is necessary for
self-optimisation and is not sufficient — the incumbent still has to have an edge, and the
measured incumbent has none demonstrated (−0.29 points against the market).

| Prediction | Falsifier |
|---|---|
| The incumbent is selected | The ACTIVE strategy still unselected after the change |
| Discovery continues | The probation backlog stops falling, or admission refusals stop |
| The two do not simply trade places | Incumbent share far from 1 in 5, or probation share near zero |

The middle row is the one that keeps this honest. If reserving cycles for the incumbent starves
discovery, the cap and the share are trading against each other and one of them has to give.

## 5. Tests

- An ACTIVE strategy that declares an action is served on its cadence turn, and not on the
  turns between.
- The cadence advances on cumulative service, not on windowed cycles — the distinction this
  whole session has been about, and the one that broke probation twice.
- With no ACTIVE strategy, `select` behaves exactly as before.
- With an ACTIVE strategy that declares nothing at the current price, probation still wins
  every turn.
- The existing probation-exit tests still pass for the stated reason: the candidate leaves
  probation on cumulative service, not because the incumbent started winning earlier.

## 6. Verification

- `ruff`, `mypy`, the lifecycle and selector tests, then the full `make verify`.
- The behavioural claim — the incumbent trades — is a live measurement, recorded above as a
  prediction rather than asserted here.
## 7. Result

`INCUMBENT_SHARE = 5` and one branch in `select`, evaluated before the probation queue and
gated on the ACTIVE strategy declaring an action at the current price. The cadence reads
`sum(result.cumulative_cycles)`, so it is stateless — nothing to persist, nothing to reset,
and it cannot drift from the journal.

Measured on the live library over twenty simulated selections, advancing cumulative service by
one per turn as the daemon would:

```
with the reserved share:     16/20  fixed-size-20260722-001   4/20  tiny-fixed-size-001
without it (today's code):   20/20  fixed-size-20260722-001          tiny-fixed-size-001 never
```

Exactly the intended 1 in 5, with discovery keeping four fifths. Four tests: the incumbent is
served on its turn and not between; the cadence advances on cumulative service rather than the
windowed count (the instrument that made probation unfinishable twice); with no ACTIVE strategy
selection is unchanged; and an incumbent that cannot act at the current price leaves every turn
to probation, so a reserved turn is never a wasted one.

**Still open, deliberately.** Whether one fifth is the right share is a real trade the loop
cannot settle without running it. The falsifier that matters is in the middle of the table in
§4: if the probation backlog stops falling, or admission stops being refused, the share and
the cap are trading against each other and one has to give. And the incumbent still has no
demonstrated edge — reserving cycles lets it accumulate evidence, which is the first time that
has been possible; it is not a claim that it has one.
