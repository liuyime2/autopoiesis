# The profit is one exit event, not thirty-five decisions

Date: 2026-10-03
Status: **measurement complete; the selection policy decision is the owner's**
Baseline commit for rollback: `c7ab775`
Scope: no source change.

---

## 0. Why this was measured

Turn 10 named the missing input to the selection-policy decision: *"whether the champion's
own result justifies its 100% of argmax cycles — broker-verified +362.66 is the only evidence
that it does, and it is one closed lot."* That is a measurement, and it had not been taken.

## 1. The champion's record

Latest broker-verified payload: `broker_strategy_closed_lot_pnl_verified`, 35 closed lots,
62 linked fills, net PnL **+643.40** (+630.13 after an assumed 0.05% round trip; observed
broker fees 0.00).

```
strategy_realized_pnl
  tiny-fixed-size-001     +366.03
  fixed-size-buy-001      +146.94
  trend-follow-buy-001     +81.36
```

## 2. Grouped by exit price, the result is one event

```
  exit price   lots        pnl   share of net  strategies
      766.57     23     564.39         87.7%  fixed-size-buy-001, tiny-fixed-size-001, trend-follow-buy-001
      769.09      2       8.93          1.4%  fixed-size-buy-001
      768.72      2       7.62          1.2%  fixed-size-buy-001
      769.74      1       5.57          0.9%  fixed-size-buy-001
       769.1      1       5.43          0.8%  fixed-size-buy-001
       769.31      2       3.37          0.5%  tiny-fixed-size-001
      763.48      1       0.38          0.1%  fixed-size-buy-001
      763.03      1      -0.14         -0.0%  fixed-size-buy-001
      763.51      1      -0.30         -0.0%  fixed-size-buy-001
      762.85      1      -0.92         -0.1%  fixed-size-buy-001
```

**A single exit at 766.57 closed 23 of the 35 lots and produced 87.7% of the net PnL**, and
it involved all three strategies at once. Ten distinct exit prices exist across the whole
record; the other nine contribute $29.94 combined.

So the headline figure is **one market move**, not thirty-five independent decisions. The
three strategies were not three results - they were three positions in the same trade, and
the ranking between them reflects how many shares each happened to be holding, which is a
function of how many cycles it was selected and when.

## 3. What this means for the champion

`tiny-fixed-size-001`'s +366.03 is **8 of its 10 lots exiting at 766.57**, bought across
727–742 while SPY was in its June range. Two of its lots exited at 769.31 for +3.37 total.

There is no evidence on record that the strategy has a repeatable edge. What is on record is
that it was holding when SPY rallied. That is not nothing - it is a reason to keep holding -
but it is not a reason to conclude it is better than `fixed-size-buy-001` (+146.94, also
mostly the 766.57 exit) or `trend-follow-buy-001` (+81.36, all four lots at 766.57), and it
is certainly not a reason to spend 100% of argmax cycles on it.

The doctor already carries part of this and states it honestly: `regime=CANNOT ATTRIBUTE`,
`signal=CANNOT ATTRIBUTE`, `by regime: {'UNKNOWN': 594.33}`, and `UNMATCHED SELLS 29` makes
the headline "an upper bound rather than an exact result". This measurement is the same fact
from the other end - the result is real, its *attribution across strategies* is not
determined.

## 4. The cost of the alternative, measured

Turn 1 established that a `FIXED_SIZE` needs **11–13 selected cycles** to reach the
10-informative gate, at an observed informative rate of 0.75–0.91 per selected cycle.

Turn 10 established that **6 of 60 strategies are selectable**, of which **5 are in
PROBATION** (two FIXED_SIZE, three tradeable TREND_FOLLOW) plus the one ACTIVE champion.

So evaluating every currently-tradeable candidate to the point where the screen can judge it
costs `5 x 13 = 65` cycles. At the observed 66 market-open cycles per trading day, **that is
one trading day.**

That is the whole trade: the champion forgoes about one day of throughput, and in exchange
every candidate that *can trade* reaches the evidence gate the promotion rule requires.
Today none of them can, because probation serves each for 3 cycles - about 2 informative
decisions against a gate of 10.

## 5. What is not concluded here

- Not that the champion should be retired. Its position may be the right one to hold.
- Not that the three strategies are equivalent. They may differ; nothing on record can say.
- Not that a re-adjudication path for PAUSED strategies should be built. That instance still
  arrives with the next trading day, and it changes strategy state.

The measurement supplies the missing input and sharpens the question. The question itself is
worth more than a trading day of the champion's throughput to some observers than to others,
and that is not a measurement.
