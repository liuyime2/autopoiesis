# The RSI kernel, reviewed: what it does, what it is not, and what to change

Date: 2026-10-06
Status: **ACTED ON, 2026-10-07** — every item's outcome is in §13 at the end. §3.1, §3.2 and
the verdict half of §3.3 were corrected on 2026-10-06; see the correction at the head of §3.
Baseline commit: `b97a6b2`
Supersedes: nothing. `STATUS.md` remains the record of what has happened; this file is the
record of what should happen next, and it is a dated document too — when it is acted on, the
dated sections of `STATUS.md` say so.

Read with `docs/history/plans/2026-10-06-a-falsifiable-target-and-the-llm-increment.md`
(the falsifiable target), `docs/ARCHITECTURE.md` (the current system), and `STATUS.md`
(what happened, in order).

**How the evidence in this document was produced.** Every number carries one of three marks:

- **[measured]** — recomputed from `runtime/autopoiesis/journal.jsonl*` or from a command run in
  this repository while writing this document. The command is given.
- **[recorded]** — quoted from an existing project document, whose own measurement is trusted.
- **[unverified]** — believed, not re-measured here.

---

## 0. The two questions this document has to answer, and the order

The review request asked what the project wants, what it does, and how the three directions
(RSI / AI-native / trading) stand. Answering that order is the mistake: the three directions
are not separable, and one of them determines the other two.

1. **The selection signal is not measuring the thing it is supposed to measure.** The
   promotion gate reads `correct_outcome_ratio` from a one-share, 24-hour-ahead price probe.
   Section 3 shows that signal swings with which way the market went, and that on this record
   it is directionally skewed: SELL decisions score 26.5% correct (9 of 34) against BUY's 66.2%
   (45 of 68). ~~every SELL in the journal lost money, so every SELL strategy scores 0.000~~ —
   **retracted**: that figure came from reading the oldest counterfactual row per cycle, not the
   latest. See the correction at the head of section 3.
2. **Nothing in the loop can change.** The strategy parameters are not executed; the prompt is
   a frozen constant; the scoring function is fixed; the model weights are fixed. Section 2.

Consequence: the loop is a **filter**, and it is filtering on a signal that mostly encodes
market direction. Fixing the strategy engine without fixing the signal produces a better
filter on a wrong target. That is why section 4 puts the signal first.

---

## 1. What the project is, precisely

**Claimed** (`README.md:3-4`): a paper-trading agent that decides for itself whether its own
strategies are any good, using broker evidence, and retires the ones that are not.

**Actual, day to day** **[measured]**, recomputed over 19,516 journal events
(`json.loads` per line over `journal.jsonl{,.1,.2}`):

| Fact | Value |
| --- | --- |
| cycle records | 1284 |
| actions | 1141 HOLD (89%), 100 BUY, 43 SELL |
| `decision_source` recorded | 851 absent (pre-provenance), 381 `llm`, 51 `fallback_policy_engine`, 1 `baseline` |
| order fills confirmed | 68 |
| strategies on disk | 75 — PAUSED 37, PROBATION 23, RETIRED 13, ACTIVE 1, BASELINE 1 |
| lifecycle transitions journalled | 108 |
| offline screens run | 10303 (7840 INCONCLUSIVE, 1522 PASS, 941 REJECT) |
| SPY symbols traded | 1 (`SPY` only, in all 1284 cycles) |

So: one symbol, a five-minute cadence, a local 27B model asked to emit
`{action, quantity, confidence, rationale}` from a context containing **one price and no
bar history** (`src/autopoiesis/llm_decision.py:302-361`), a hard risk check, and a maintenance
pass every fifteen minutes that scores decisions after the fact, screens strategies on those
scores, and moves lifecycle states.

**The one genuinely novel mechanism**, and it is worth keeping: *a strategy is graded on
whether the decisions it actually produced were later judged correct by real prices, and that
grade is allowed to delete the strategy.* It is wired end to end — 941 REJECT verdicts, 12
`PROBATION -> RETIRED` on that reason, verified by `make verify`'s
`selection-pressure-reaches-the-rules` class.

**The gap between claimed and actual** **[measured]**: the strategies are not executable rules.
`src/autopoiesis/offline_validation.py:9-15` states it plainly — `parameters["action"]` is never
read on the execution path, and `TREND_FOLLOW`'s `reference_price`/`threshold_pct` are read
only by admission and validation. `StrategyExecutor.decide()` (`strategy_engine.py:781`) exists
and does execute those parameters, but its only callers are the admission/selector capability
probes (`curriculum.py:846`, `strategy_engine.py:895`) and `policy_engine.py:62` (the fallback
path, 51 cycles). **The live trading path never executes a strategy.**

A strategy is therefore a JSON parameter block handed to a frozen model as prompt context,
and the model writes the action itself. "Strategy evolution" is changing that block.

---

## 2. RSI kernel: closed loop, nothing inside it can learn

### 2.1 What is closed, with live evidence **[recorded]**

| Stage | Closed? | Evidence |
| --- | --- | --- |
| decision -> counterfactual verdict -> offline screen -> retirement | yes | 941 REJECT verdicts; 12 `PROBATION -> RETIRED`; `verify.py` class `selection-pressure-reaches-the-rules` |
| curriculum -> admission | yes | 261 `STRATEGY_ADMISSION_REVIEWED` events |
| admission -> file write -> journal | yes | atomic write before `accepted=True` |
| knowledge lesson -> next prompt | wired, **no effect** | 264 `KNOWLEDGE_ARTIFACT_PROPOSED`; 17-20 near-identical restatements of one calibration finding, which cannot change a HOLD |

### 2.2 What is frozen **[measured / recorded]**

| Component | Frozen? | Where |
| --- | --- | --- |
| Strategy parameters at trade time | yes, never executed | `offline_validation.py:9-15` |
| Decision instruction | yes, one constant, 975 chars | `llm_decision.py:53-68` |
| Model weights | yes | local Ollama, fixed model |
| Scoring function | yes | `counterfactual.py`, verdict set frozen at module level |
| Admission gate | yes | `strategy_admission.py` |
| Risk limits | yes, and must stay so | `guardian.py`, `AGENTS.md` §17 |

Every one of the items above is something that *could* learn. The only things the loop varies
are which strategy is selected and when. That is search, presented as learning.

### 2.3 The promotions, examined **[measured]**

108 lifecycle transitions, of which **8 reached `ACTIVE`**:

| Date | Count | Recorded reason |
| --- | --- | --- |
| 2026-06-11 | 1 | "probation completed with acceptable operational metrics" |
| 2026-06-12 | 2 | same |
| 2026-06-18 | 3 | same |
| 2026-09-30 | 2 | "probation completed on 6 cycles with broker-verified realized PnL +362.66" |

The June rule was a rule that judged **operational health** — did the strategy avoid errors —
and it has since been deleted and replaced. Six of the eight promotions were made by a rule
that no longer exists, on a criterion unrelated to whether the strategy made money.

The two September promotions cite `+362.66` broker-verified PnL. That figure belongs to the
exit at price 766.57 on 2026-09-28, which closed 23 lots at one price in one session — 74.5%
of the entire `+757.95` net PnL on the current 47-lot basis **[measured]** (grouped by exit
price; recomputed from the cumulative `PNL_EVIDENCE_RECORDED` payload; the earlier 87.7% figure
was the 35-lot basis and is superseded).

**So: no promotion on record was made on a repeatable edge.** "Zero promotions based on
capability" — which `STATUS.md` retracted in favour of "8 promotions" — is the accurate
statement, and it is a stronger finding than the one it replaced.

### 2.4 The dominant transition is starvation **[measured]**

45 of 108 transitions carry the reason "no exploration evidence". 37 of 75 strategies are
`PAUSED`, and `StrategyEngine._review_one` returns immediately for `PAUSED`
(`strategy_engine.py:227`) — **`PAUSED` is terminal by construction**. Half the library is a
graveyard that the loop cannot reopen.

### 2.5 The knowledge channel does nothing **[measured]**

`knowledge_admission.py:32-39` de-duplicates artifacts by **exact string**, so re-stating one
calibration finding with a different number produces a new artifact. 264 proposals exist; the
live ones are restatements of "your stated confidence does not predict correctness". A lesson
whose content is about *not acting* cannot change an action. Five of these are injected into
every prompt (`policy_engine.py:107`), costing tokens to no effect.

---

## 3. The measurement is broken, and it is biased in a direction **[measured]

This is the finding that changes the priority order, so it is stated with its reproduction.

> **Correction, 2026-10-06, same day.** The first version of 3.1, 3.2 and the verdict half of
> 3.3 was wrong, and wrong in the direction that made the finding more dramatic.
>
> *Root cause.* `COUNTERFACTUAL_EVALUATED` is emitted on every maintenance pass and re-emits
> every cycle's row, so one cycle carries many rows, early ones `PENDING` and later ones
> resolved. The reproduction kept "the last row per cycle_id" in **file read order**:
> `journal.jsonl`, then `.1`, then `.2`. Rotation puts the *oldest* history in `.2`, so the row
> that survived was the oldest one, not the newest — 140 HOLD rows still `PENDING` instead of
> 42, and the 9 SELL rows that later resolved as `GOOD_TRADE` still in their earlier state.
>
> *How it was caught.* The production screen disagreed: the journal's own latest
> `OFFLINE_VALIDATION_COMPLETED` for `fixed-size-sell-005` reads "7 of 14 scored decisions went
> the right way", while the table below said `scored 5, ratio 0.000`.
>
> *The fix.* Keep, per cycle, the row from the event with the greatest `timestamp`, and score
> with `counterfactual.CORRECT` / `counterfactual.INFORMATIVE` — the screen's own sets, which
> exclude `NEUTRAL`. Run twice, byte-identical output. `fixed-size-sell-005` then reproduces the
> production figure exactly (7 of 14 = 0.500), and the §3.3 rule/LLM pair counts reproduce the
> original exactly (323 comparable, 58 skipped, 126/59/53/46/38/1), which isolates the error to
> the verdict join.
>
> The superseded tables are kept below, marked, because deleting them would erase why the
> priority order was first argued from them. Corrected tables follow each one.
>
> <details><summary>Reproduction script (read-only over the journal; run from the repo root
> with <code>PYTHONPATH=src</code>)</summary>
>
> ```python
> """Recompute PLAN_RSI_KERNEL section 3 with the LATEST counterfactual row per cycle.
>
> Read-only over runtime/autopoiesis/journal.jsonl{,.1,.2}. A cycle's counterfactual is
> re-emitted by every COUNTERFACTUAL_EVALUATED pass, so the row to keep is the one from the
> event with the greatest timestamp - not whichever file happened to be read last.
> """
> import collections, glob, json
> from pathlib import Path
> from autopoiesis.models import DataSnapshot, StrategySpec
> from autopoiesis.strategy_engine import StrategyExecutor
>
> paths = sorted(glob.glob("runtime/autopoiesis/journal.jsonl*"))
> paths = [p for p in paths if not p.endswith(".lock")]
> latest, cycles = {}, {}
> for p in paths:
>     for line in open(p, encoding="utf-8"):
>         e = json.loads(line)
>         if e.get("event_type") == "COUNTERFACTUAL_EVALUATED":
>             for r in e["payload"].get("rows", []):
>                 k = r["cycle_id"]
>                 if k not in latest or e["timestamp"] > latest[k][0]:
>                     latest[k] = (e["timestamp"], r)
>         elif "event_type" not in e and "decision" in e and "snapshot" in e:
>             cycles[e["cycle_id"]] = e
> rows = {k: r for k, (_, r) in latest.items()}
> from autopoiesis.counterfactual import CORRECT, INFORMATIVE as SCORED  # the screen's own sets
>
> print(f"files {paths}\ncycles with a counterfactual row {len(rows)}; cycle records {len(cycles)}\n")
> print("3.1 by action (latest row per cycle)")
> by = collections.defaultdict(collections.Counter)
> for r in rows.values():
>     by[r["action"]][r["verdict"]] += 1
> for a in ("HOLD", "BUY", "SELL"):
>     v = by[a]; n = sum(v[x] for x in SCORED); c = sum(v[x] for x in CORRECT)
>     print(f"  {a:5} rows {sum(v.values()):4}  scored {n:4}  correct {c:4}  rate {c/n:6.1%}  {dict(v)}")
>
> print("\n3.1 per strategy, strategies named in the plan")
> for sid in ("fixed-size-buy-001", "fixed-size-sell-005", "fixed-size-sell-20260724-001", "trend-follow-sell-002"):
>     v = collections.Counter(r["verdict"] for r in rows.values() if r.get("strategy_id") == sid)
>     n = sum(v[x] for x in SCORED); c = sum(v[x] for x in CORRECT)
>     print(f"  {sid:30} scored {n:3} correct {c:3} ratio {(c/n if n else float('nan')):.3f}")
>
> print("\n3.2 by decision day")
> day = collections.defaultdict(lambda: [0, 0])
> for r in rows.values():
>     if r["verdict"] in SCORED:
>         d = r["decided_at"][:10]; day[d][0] += 1; day[d][1] += r["verdict"] in CORRECT
> for d in sorted(day):
>     n, c = day[d]
>     print(f"  {d}  scored {n:4}  ratio {c/n:.3f}")
>
> print("\n3.3 rule vs llm on stored snapshots")
> ex, pairs, skipped = StrategyExecutor(), collections.Counter(), 0
> value = collections.defaultdict(lambda: [0, 0])
> for cid, rec in cycles.items():
>     if rec["decision"].get("decision_source") != "llm":
>         continue
>     sid = rec.get("strategy_id") or rec["decision"].get("strategy_id")
>     f = Path("runtime/autopoiesis/strategies") / f"{sid}.json"
>     if not sid or not f.exists():
>         skipped += 1; continue
>     rule = ex.decide(StrategySpec.model_validate_json(f.read_text()), DataSnapshot.model_validate(rec["snapshot"])).action
>     llm = rec["decision"]["action"]
>     pairs[(rule, llm)] += 1
>     r = rows.get(cid)
>     if r and r["verdict"] in SCORED:
>         value[(rule, llm)][0] += 1; value[(rule, llm)][1] += r["verdict"] in CORRECT
> print(f"  comparable {sum(pairs.values())}  skipped {skipped}")
> for (rule, llm), n in pairs.most_common():
>     s, c = value[(rule, llm)]
>     print(f"  rule {rule:4} llm {llm:4}  n {n:4}  scored {s:4}  correct {c:4}  {(c/s if s else float('nan')):6.1%}")
> ```
>
> </details>

### 3.1 The selection signal

The promotion gate is `correct_outcome_ratio >= 0.5` (`offline_validation.py:248`), where the
ratio is `(GOOD_HOLD + GOOD_TRADE) / scored` over verdicts produced by a **one-share, buy at the
decision price, 24 hours later, net of an assumed 0.05%** probe (`counterfactual.py:1-46`).

Reproduction — group every scored verdict by the action the decision actually took:

```
SUPERSEDED - oldest row per cycle, see the correction above
823 distinct counterfactual rows recovered by taking the last row per cycle_id
across all COUNTERFACTUAL_EVALUATED events

action   n     correct   rate     verdict distribution
HOLD     694   287       41.4%    GOOD_HOLD 287, MISSED_ALPHA 225, NEUTRAL 7, GAP 35, PENDING 140
BUY       86    13       15.1%    NEUTRAL 33, GOOD_TRADE 13, PENDING 16, FALSE_TRADE 11, GAP 13
SELL      43     0        0.0%    FALSE_TRADE 21, PENDING 18, NEUTRAL 2, GAP 2
```

Corrected — latest row per cycle, `scored` = `INFORMATIVE` (NEUTRAL excluded):

```
823 cycles with a counterfactual row

action   rows  scored  correct  rate    verdict distribution
HOLD     694   582     340      58.4%   GOOD_HOLD 340, MISSED_ALPHA 242, NEUTRAL 35, GAP 35, PENDING 42
BUY       86    68      45      66.2%   GOOD_TRADE 45, FALSE_TRADE 23, GAP 14, PENDING 3, NEUTRAL 1
SELL      43    34       9      26.5%   FALSE_TRADE 25, GOOD_TRADE 9, PENDING 6, GAP 2, NEUTRAL 1
```

~~**All 43 SELL decisions in the journal lost money. Not one won.** Every SELL strategy therefore
scores `0.000`~~ — retracted. Per strategy:

```
SUPERSEDED
fixed-size-buy-001                 BUY   scored 5   ratio 0.000
fixed-size-sell-005                SELL  scored 5   ratio 0.000
fixed-size-sell-20260724-001       SELL  scored 15  ratio 0.000
trend-follow-sell-002              HOLD  scored 42  ratio 0.071

Corrected
fixed-size-buy-001                       scored 21  correct 16  ratio 0.762
fixed-size-sell-005                      scored 14  correct  7  ratio 0.500   = production screen
fixed-size-sell-20260724-001             scored 15  correct  0  ratio 0.000
trend-follow-sell-002                    scored 43  correct  4  ratio 0.093
```

~~Against a 0.5 gate, all of these are `REJECT_POR_DECISIONS` → `RETIRED`.~~ Corrected: two of
the four are below the gate, both SELL strategies; one SELL strategy sits exactly on it and the
BUY strategy clears it.

~~**The self-evolution loop is currently learning "never sell"**~~ — overstated. What the record
supports is weaker and still worth acting on: **on sixteen days of a mostly rising market, SELL
decisions are right 26.5% of the time and BUY decisions 66.2%, so a direction-blind gate will
tend to retire sellers.** The same days would tilt the other way on a falling market.
`STATUS.md` already discovered this day-effect for the *calibration* report (Brier 0.396 raw vs
0.271 day-adjusted, `STATUS.md` 2026-10-03 calibration entry) and corrected it there. **The
correction was never applied to the offline screen, which is the signal that actually decides
promotions.** Whether the skew is market direction or genuinely bad sell rules is what P0.1's
falsifier decides; this record cannot.

### 3.2 The signal swings by day, not by skill

Same data, grouped by decision day, against that day's SPY move:

```
SUPERSEDED - oldest row per cycle
day          scored  ratio    SPY that day
2026-06-09     147   0.728    -0.38%
2026-06-10      59   0.780    -0.89%
2026-06-11      25   0.000    +1.25%
2026-06-12      15   0.000    +0.17%
2026-06-15      26   0.846    +0.36%
2026-06-16      28   1.000    -0.52%
2026-06-17      26   0.808    -1.35%
2026-09-28      66   0.939    +0.09%
2026-09-29      48   0.021    -0.34%
2026-10-01      65   0.123    +0.07%
2026-10-05      52   0.096    +0.56%
```

```
Corrected - latest row per cycle (SPY column not re-measured, so omitted)
day          scored  ratio
2026-06-09     149   0.732
2026-06-10      59   0.780
2026-06-11      28   0.107
2026-06-12      27   0.444
2026-06-15      26   0.846
2026-06-16      28   1.000
2026-06-17      26   0.808
2026-09-28      67   0.940
2026-09-29      64   0.266
2026-09-30      59   0.797
2026-10-01      65   0.123
2026-10-02      34   0.382
2026-10-05      52   0.096
```

The conclusion survives the correction: the daily ratio still ranges from 0.096 to 1.000, which
is too wide to be skill. The 24-hour horizon means the day label is approximate, so this table
is not a clean causal claim. The original sentence about correlation with SPY's daily move was
argued from the superseded table and has not been re-checked against the corrected one.

### 3.3 LLM versus the deterministic rule, on the same snapshots **[measured]**

`StrategyExecutor.decide()` was re-run against the stored `snapshot` of every LLM-sourced
cycle whose strategy file still exists, and compared with the decision actually taken
(re-run with the script above; identical counts):

```
comparable cycles 323   (58 skipped: strategy_id absent)

rule    llm     n
HOLD    HOLD    126
BUY     HOLD     59     <- rule traded, LLM held
SELL    HOLD     53     <- rule traded, LLM held
BUY     BUY      46
SELL    SELL     38
HOLD    BUY       1     <- LLM traded, rule held
```

**112 of 113 disagreements are the LLM refusing a trade the rule would have taken; exactly one
is the reverse.** The model's net contribution to behaviour is veto. (This half does not
depend on the verdict join and stands.)

What those vetoes were worth, joined to the verdicts:

```
SUPERSEDED - oldest row per cycle
rule=BUY  llm=HOLD   58 scored    5 correct    8.6%
rule=SELL llm=HOLD   53 scored    3 correct    5.7%
rule=BUY  llm=BUY    45 scored   13 correct   28.9%
rule=SELL llm=SELL   38 scored    0 correct    0.0%

Corrected - latest row per cycle, scored = INFORMATIVE
rule=HOLD llm=HOLD  126 n  101 scored   38 correct   37.6%
rule=BUY  llm=HOLD   59 n   34 scored   15 correct   44.1%
rule=SELL llm=HOLD   53 n   45 scored    3 correct    6.7%
rule=BUY  llm=BUY    46 n   40 scored   29 correct   72.5%
rule=SELL llm=SELL   38 n   31 scored    7 correct   22.6%
```

**A further instrument problem the corrected table exposes.** A HOLD is graded against a
one-share *buy* probe whatever the alternative was: `GOOD_HOLD` means the price fell,
`MISSED_ALPHA` that it rose (`counterfactual.py:30-31`). For a vetoed **SELL**, a rising price
means keeping the position paid — yet it is scored `MISSED_ALPHA`, i.e. wrong. So the 6.7% on
`rule=SELL llm=HOLD` does not show the vetoes were bad; on this probe it says the price mostly
rose after the model declined to sell, which is the case where declining was right for the
position. The screen cannot currently tell a good sell-veto from a bad one. This belongs to
P2.1 (score against the rule's own counterfactual), and it is a reason not to read the veto
value as "the LLM is worse than the rule" in either direction.

### 3.4 The confidence distribution **[measured]**

`STATUS.md` reports "bimodal, 637 near 0 and 542 near 1". Over the 381 LLM-sourced decisions
with a confidence value:

```
0.0 -> 34    0.3 -> 1    0.5 -> 41    0.6 -> 132    0.7 -> 152    0.8 -> 18    0.9 -> 3
```

There is no second mode near 1. The LLM's confidences are **narrowly concentrated in
0.6-0.7**; the apparent bimodality comes from folding in HOLD decisions, whose confidence is
structurally 0.0. The earlier reading was an artifact of mixing two populations.

### 3.5 What this means for the target

`make benchmark` **[measured, run in this repository]**:

```
record            2026-06-09 .. 2026-10-06   16 trading day(s), 1284 cycle(s)
target window     60 trading days - NOT MET (16 of 60)

vs SPY buy-and-hold
  tiny-fixed-size-001          +3.94%     -1.47
  trend-follow-buy-001         +3.85%     -1.56
  fixed-size-buy-001           +1.92%     -3.48
  fixed-size-probe-0001        +1.11%     -4.29
  buy-and-hold (SPY)           +5.41%

LLM increment     +757.95 across 47 closed lots: model +193.56, baseline +564.39
attribution       970 of 1284 decisions (76%) cannot be attributed to a model
VERDICT           FAIL
```

Two of those numbers need their own correction before they are used as a target:

- The per-strategy return is computed on **peak exposure** (`evaluator.py:556`, via
  `_peak_exposure_by_strategy`) while the market leg is a buy-and-hold over the whole record.
  Different denominators, which this project has retracted four times already. **Part of the
  current FAIL may be instrumentation.** That must be resolved before the target is trusted,
  in either direction.
- `+193.56` "model contribution" is an attribution over lots the model happened to open. It is
  not a counterfactual: no paired comparison was run. The file itself says so
  (`docs/history/plans/2026-10-06-a-falsifiable-target-and-the-llm-increment.md` §2).

---

## 4. What to change, in order

Ordered by "if we do the later things first, the earlier ones were wasted". Every item states
its falsifier, per `AGENTS.md` §1.

### P0 — Fix the instrument before touching the thing it measures

**P0.1 Day-adjust the offline screen, or replace the gate with a paired comparison.**
The screen already receives every scored decision. The calibration module already computes a
day base rate (`STATUS.md` 2026-10-03). Reuse it.

*Motivation, as corrected in §3:* SELL decisions score 26.5% against BUY's 66.2%, and the daily
ratio ranges 0.096 to 1.000. The skew is real but far smaller than the retracted "every SELL
scores 0.000"; this item is still first because a direction-blind gate will keep retiring
sellers on a rising market, but its expected effect size is smaller than first argued.

- *Done when*: a strategy's grade reports both raw and day-adjusted ratio, and the lifecycle
  rule reads the adjusted one.
- *Falsifier*: the SELL strategies' grades do not move materially when the day base rate is
  controlled for. If they do not move, the day-effect hypothesis is wrong and this item is
  closed as wrong rather than left open.

**P0.2 Resolve the denominator mismatch in `pnl vs holding`.**
Either compute the market leg over each strategy's own holding window, or state both on the
same capital base. Do not change the target until this is done.

- *Done when*: the comparison is computable from one documented definition, and the benchmark
  prints that definition next to the number.
- *Falsifier*: re-running with a per-strategy window moves any strategy across the 0 line. If
  it does, the FAIL verdict changes and the target must be restated before anything else.

**P0.3 Report the selection signal's directional bias as a standing check.**
Add a `verify` class that reports, from the journal alone: the verdict distribution by action,
the per-day ratio spread, and how many strategies currently sit below the gate **solely
because they are SELL strategies**. It must read the **latest** counterfactual row per cycle by
event timestamp — the error §3 retracts came from reading the oldest, and a standing check
built on the same join would report the same wrong number every day.

- *Done when*: the check fails when the number of gate-failing SELL strategies exceeds the
  number of gate-failing BUY strategies by more than the record can explain.
- *Falsifier*: removing the check changes nothing (`verify-self-test` must be able to break it).

### P1 — Make strategies executable, so "evolution" has a subject

**P1.1 Route the selected strategy's own rule through the execution path.**
`StrategyExecutor.decide()` already computes an action from `parameters`. Today the loop calls
the LLM instead. Make the strategy's rule the default execution, and let the LLM act as an
*override that must state a reason*, journalled with the rule's own action alongside it.

- *Done when*: the journal records, for every cycle, both the rule's action and the taken
  action, so the increment is a paired comparison on identical inputs.
- *Falsifier*: the paired comparison cannot be computed for any cycle.
- *Risk*: this changes which strategies trade. It wants its own commit, its own plan, and a
  paused daemon, per `AGENTS.md` §6.

**P1.2 Give the model a market state instead of one price.**
`llm_decision.py:302-361` passes `last_price` and nothing else — no bars, no returns, no
volatility, no regime. `regime.py` already computes point-in-time regime. Add a bounded
`last_N_bars` or a returns/volatility/regime block.

- *Done when*: an A/B over the same cycles reports `correct_outcome_ratio` for both context
  variants **with both denominators**, and the token budget is reported with it
  (`DECISION_NUM_CTX = 4096` is a hard constraint).
- *Falsifier*: the day-adjusted ratio does not move outside its own confidence interval, in
  which case the context is not the limiting factor and the honest conclusion is recorded.

### P2 — Stop the loop from learning the wrong lesson

**P2.1 Make the offline screen direction-neutral, or make the direction explicit.**
Option A: score a strategy against its own rule's counterfactual (paired), not against an
absolute price probe. Option B: keep the probe, and stratify by regime so a SELL strategy is
graded on down days.

- *Done when*: no strategy's grade is dominated by the direction of the market on the days it
  happened to trade.
- Option A also fixes the problem §3.3 exposes: a HOLD that vetoed a SELL is graded against a
  buy probe, so a veto that kept a rising position is scored wrong. Under a paired score it is
  graded against the SELL it replaced.

**P2.2 Fix or remove the knowledge channel.**
Either give each lesson an attributable effect (which decisions changed because of it, measured
against the rule's action with the lesson removed) or demote lessons to a doctor report and take
them out of the prompt.

- *Done when*: every lesson in the prompt has a non-zero count of decisions it changed, or the
  injection is gone.
- *Falsifier*: no lesson can be shown to have changed any action, which is the current state
  and is not an acceptable steady state for something labelled a self-evolution channel.

**P2.3 Re-open `PAUSED`.**
`strategy_engine.py:227` returns immediately, so 37 of 75 strategies are permanent.
`StrategySpec.lifecycle_reason` exists (`STATUS.md` 2026-10-03) — the data needed for a
time-bounded re-adjudication is already there.

- *Done when*: a `PAUSED` strategy whose reason has expired is re-examined, and the re-adjudication
  is journalled like every other transition.
- *Caution recorded in the project already*: restoring paused strategies lengthens the serial
  probation queue. Measure before doing it (§"P1-a" in `STATUS.md`).

### P3 — Make the evidence base able to answer the question

**P3.1 Publish a sanitised journal fixture so `make benchmark` is runnable by a newcomer.**
A fresh clone has no `runtime/`, so `benchmark` exits 2 ("nothing to measure") — which is the
one number the project exists to move.

- *Done when*: `make benchmark` runs end to end on a fresh clone against a fixture whose header
  states in the file itself that it is synthetic, and which is used **only** to exercise the
  code path, never to support any claim.
- `AGENTS.md` §5 permits labelled synthetic data in tests; this must never appear on a path
  that produces evidence.

**P3.2 Move `runtime/autopoiesis/research_trials.jsonl` out of the ignored tree.**
59 recorded trials — 47 INSUFFICIENT, 12 OVERFIT, 0 PASS — are the search space the system has
already proven does not work. It is the single most useful thing an outside contributor could
be handed, and it is invisible because `runtime/` is gitignored.

**P3.3 Start an attribution epoch.**
970 of 1284 decisions predate provenance and cannot be attributed. Back-filling would be
fabrication. Do not compare across the boundary; label it.

**P3.4 Fix the benchmark's own two smaller defects** (both found by running it, both already
described in the plan's §7d): the duplicated PnL paragraph when `doctor` emits
`pnl attribution` twice, and the `"; "` split that dropped the `pnl vs holding` table.

---

## 5. What is *not* wrong, and should not be touched

Stated because a review that only lists problems invites someone to "fix" the good parts.

- **`make verify` is 52/52 green, 1519 test executions across 64 files, exit 0** — run in this
  repository while writing this document. Including `verify-self-test`, which breaks the gate on
  purpose. This is a better gate than most projects of any size have.
- The Guardian is unbypassable on the single execution path and the paper-only refusal is in
  code, not configuration.
- The broker-evidence discipline — no PnL without a linked fill, unmatched sells reported as a
  limit on the record rather than papered over — is genuinely good and is why the numbers above
  can be trusted enough to be useful.
- Five separate occasions where a measurement was found to be broken and the correction was
  published rather than quietly applied. That instinct is the project's real asset.
- Zero fabricated data, zero bypassed risk checks. `AGENTS.md` §17 has held under pressure.

The failure mode here is not dishonesty. It is building an excellent instrument around a
question that the instrument cannot answer.

---

# Part 2 — Making this an open-source project

## 6. Position: what the project claims, and what it must not

The single most important edit is to the README's first screen. A project that places orders
daily, uses a 27B model, and ships a strategy-retirement mechanism will be read as profitable
unless the README says otherwise in the first screen. This is the one change with real
downside risk if it is skipped.

The claim structure that is both honest and marketable:

> **What this project claims: nothing about returns.**
>
> It claims three engineering properties, each checked by a command you can run:
>
> | Claim | How to check it |
> | --- | --- |
> | Paper-only, enforced in code | `config.is_paper_endpoint` is the single definition; `cli` and the executor both go through it |
> | Risk limits cannot be bypassed | the Guardian is called on the one path that submits an order |
> | The agent can retire its own strategies | `make status` — strategies move on measured evidence |
>
> It does **not** claim that any strategy makes money. Over the 16 trading days measured so far
> against a 60-day target, all four strategies underperformed buy-and-hold SPY, and 76% of
> decisions cannot be attributed to a model. Those numbers move; `make benchmark` prints the
> current ones with their denominators.

Three requirements on the wording, from the external review:

1. Imperative heading, first screen, not a footer.
2. "No alpha claim" and "not financial advice" **bound together**. A disclaimer in one place
   and an implied profitability claim elsewhere is the form that gets called out.
3. State the actual behaviours (refuses non-paper endpoints, will place orders, has hard
   position and loss limits), not abstract boilerplate.

A new `docs/EVIDENCE.md` is the right home for the detail, structured as **Known** /
**Not known, and not estimated** — the second half is what makes the first credible: 29 shares
sold with no matching lot, headline PnL incomplete with an unknown-sign omission, ~24% of
decisions attributable.

Keep `LICENSE` as Apache-2.0. For this project the meaningful difference from MIT is the §3
patent grant and §4's requirement that modified files carry change notices — a legal backstop
against a fork quietly removing the paper-only refusal. Do not add a second disclaimer to
`LICENSE`; its Appendix already carries the warranty disclaimer. Add `SECURITY.md` with an
explicit out-of-scope section: **do not report broker-credential problems as issues; if you
suspect a leak, revoke and reissue.** And tell contributors not to paste `make doctor` output
into a public issue without checking it for credentials first.

## 7. Naming: settle on `min-agent`, and stop the drift

**Recommendation: repository name `min-agent`.** The distribution name in
`pyproject.toml:6`, the README H1, the CLI entry point (`pyproject.toml:45`), the credential
path, and the config directory all say `min-agent`. Only `STATUS.md:1` says `QuantGroup`.
`min-agent` -> `autopoiesis` is the normal PEP 503 normalisation and is not a problem.

`QuantGroup` as the public name is worth avoiding for three reasons: it is an organisation
name, not a software name; it is a common-enough string that the PyPI name is almost certainly
taken; and "Group" implies multi-agent, which this is not.

`QuantVoyager` appears in five tracked systemd unit templates (`tools/*.service.in`,
`tools/*.timer`) **[measured]** and will be written onto a user's machine. It must be changed
before publication.

Do not rename for searchability. `min-agent` describes nothing functional; the
`pyproject.toml` description sentence is the real positioning and is already good — reuse it
verbatim as the GitHub repository description and the README subheading.

Add `[project.urls]` in the same commit as any rename, so the PyPI page and the badges do not
become dead links.

## 8. Blockers before anything is pushed **[measured]**

The remote is `https://github.com/<account>/autopoiesis` and is **private** — an unauthenticated
`GET /repos/<account>/autopoiesis` returns 404, `git ls-remote` requires credentials. Nothing has
leaked yet. Everything below is about keeping it that way deliberately rather than by accident.

| # | Blocker | Evidence | Action |
| --- | --- | --- | --- |
| 1 | 260 commits authored by a personal identity | 248 of 260 author emails are a personal GitHub noreply address; `origin` URL contains the account name | Decide explicitly: publish as-is (open-source projects normally do — history is provenance) or rewrite with `git filter-repo`. **Record the decision**; do not do it silently |
| 2 | ~~Committed LICENSE names a person~~ | **Done** in `51050b3`: `LICENSE:190` now reads `Copyright 2026 QuantGroup contributors` | — |
| 3 | Host paths inside a tracked "evidence" file | `docs/evidence/fresh-clone.log` has 41 lines of `<home>/...`, including `interpreter: <home>/miniconda3/bin/python3` | **Do not rewrite it.** It is a transcript; editing it makes the evidence a fabrication (`AGENTS.md` §5). Regenerate it by re-running `docs/evidence/run-fresh-clone.sh` |
| 4 | Absolute host paths in git history | `git show cdea541^:minictrl` has `<home>/miniconda3/bin/conda` and `<scratch>/conda_env/llm/bin` | Accept as history, or filter-repo together with #1 |
| 5 | Agent session state is tracked | `git ls-files .claude` returns `.claude/scheduled_tasks.lock`; it matches `.gitignore:42` but is already in the index | `git rm --cached` it; the ignore rule then works |
| 6 | Clone command is a placeholder | `README.md:57` `git clone <repo> && cd min-agent` | Substitute the real URL when the name is settled |
| 7 | Local `master` is 49 commits ahead of `origin/main` | `git rev-list --left-right --count origin/main...master` -> `0  49` | The push *is* the publication moment. Do the doc work first |
| 8 | `QuantVoyager` in five tracked unit templates | `tools/*.service.in`, `tools/*.timer` | Rename before publishing |
| 9 | No community files | `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md`, `NOTICE` all absent | Write them (section 10) |
| 10 | CI has never been proven on GitHub, and omits mypy | `.github/workflows/ci.yml` is the only workflow; no `mypy` anywhere in it, while `README.md:88-89` says `make type` is part of `make check` | Push to a private fork first, get a green run, then add mypy to the workflow |
| 11 | `make doctor`/`status`/`smoke` require conda | `minictrl:74-75` exits FATAL without it; `minictrl:127` hardcodes the env name `llm` | See section 9 |
| 12 | The reproducibility log disagrees with CI | `fresh-clone.log:14` says Python 3.13.2; `ci.yml:23` and `pyproject.toml:10,58` say 3.10 | Regenerate the log, or state the range |

## 9. The contributor's first five minutes

Ranked by how hard each one blocks someone who has never seen the repository.

**conda is a false wall.** `Makefile:29-43` already falls back to the active environment's
python when conda is absent, and there are three pure-Python runtime dependencies
(`alpaca-trade-api`, `pydantic`, `requests`) — nothing needs a package manager beyond `pip`.
`README.md:37-38` and `Makefile:215` teach conda anyway. Make `python -m venv .venv` the
documented path and conda the alternative. Twenty minutes of documentation; removes the only
hard blocker.

**No credentials means nothing is visible.** `make smoke-offline` works with no broker account
and no model, and `make verify` runs offline — those two are the project. They belong in the
first screen, because right now they are buried under a conda-and-credentials setup that a new
contributor cannot pass. Say explicitly: *no credentials needed to start; CI needs no secrets.*

**No reproducible data.** A fresh clone has no `runtime/`, so `make benchmark` exits 2. P3.1
fixes this.

**Bug reports.** The issue template must require: paper or live; the exact `make` command and
its exit code; `make doctor` output **with a warning not to paste credentials**. Without this
the project gets "why isn't my strategy making money" threads and secret leakage in issues.

**What to add, later, not first**: Docker, notebooks, a GitHub Projects board. The
quant-community norm (`freqtrade`, `LEAN`, `qlib`, `nautilus_trader`) is a committed
backtest configuration plus a documented reproduction path — P3.1 and P3.2 are the cheap version
of that.

## 10. Documentation: the part most likely to be judged badly, for the wrong reason

`STATUS.md` is 3453 lines / 191 KB of dated internal log. `docs/history/plans/` holds 37
one-shot plan files whose **names are the argument trail**:
`2026-10-05-the-overfit-check-compares-window-length.md`,
`2026-10-06-dormant-candidates-hold-budget-they-cannot-spend.md`.
`SYSTEM_AUDIT.md` is 3789 lines and self-describes as a historical defect log.

Internally this is the project's best feature: falsifiable culture, retracted claims recorded
rather than deleted, and the `STATUS.md` entry that corrects "upper bound" to "incomplete"
because the old wording flattered the run. None of that should be destroyed.

The risk is external. A dated log entry reading "the self-optimisation loop now closes" can be
screenshotted out of context and used as a performance claim — which is the one thing the
README says is not being made. And 37 plan files named after design-defect arguments read, from
outside, as a project repeatedly overturning itself.

Recommended structure — the principle is *move what changes out of the documents; leave only
what is invariant*:

```
README.md               the only front door. what it is, what it claims, what it does not claim,
                        install (venv first), the commands, the first-screen disclaimer
LICENSE  NOTICE  CHANGELOG.md  CONTRIBUTING.md  CODE_OF_CONDUCT.md  SECURITY.md
docs/
  ARCHITECTURE.md       keep. the invariant system description
  OPERATIONS.md         split out of README: hard limits, systemd, doctor, journal reading
  EVIDENCE.md           new. Known / Not known and not estimated. the denominators
  LIMITATIONS.md        new. the 29 unmatched shares, the 16-day sample, the frozen prompt
  MIGRATION.md          keep
  QUICKSTART.md         new. 30 seconds offline / 2 minutes to the gate / full loop needs Alpaca
STATUS.md + docs/history/   move to a private branch or private repository
```

Where the content goes instead:

| Content | Destination |
| --- | --- |
| figures that move while the agent trades | nowhere in the docs; `make status` / `make doctor` |
| what is being worked on now | GitHub Projects or Discussions |
| shipped and breaking changes | `CHANGELOG.md` |
| known gaps | `docs/LIMITATIONS.md` and public `label:limitation` issues |
| the reasoning behind a decision | the conclusion goes in `ARCHITECTURE.md`; the argument stays private |

`AGENTS.md` is publishable and its safety invariants are genuinely valuable to a reader; put
those in `docs/` and keep the internal workflow wording out of the public copy.

There is also a live contradiction to fix while reorganising, because it is visible on arrival:
the check-class count is 52 everywhere except `docs/ARCHITECTURE.md:339`, which says 29;
test-file count is 62 (`README.md:118`), 60 (`ARCHITECTURE.md:339`) and 64 (actual); module
count is 40 (`README.md:106`) against 38 (`src/autopoiesis/*.py`) or 43 including `research/`;
realised PnL is `+563.41` (`README.md:260`) and `+564.39` (`STATUS.md`, several places);
`README.md:281` says the fresh-clone script runs seven steps and `run-fresh-clone.sh:5` says six.
`make verify` already has a `gate-class-count-consistent` class — it passes, so the check does
not cover `ARCHITECTURE.md`. Extend it.

## 11. Suggested order of work

**Before publishing (no design change):**

1. Commit the `LICENSE` change; `git rm --cached .claude/scheduled_tasks.lock`.
2. Rewrite the README first screen per section 6. Highest risk-reduction per minute spent.
3. Write `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md`.
4. Fix the README's counts and the `ARCHITECTURE.md:339` contradictions; extend
   `gate-class-count-consistent` to cover them.
5. venv-first install instructions; point at `make help`.
6. Issue and PR templates.
7. Settle the repository name, rename `QuantVoyager` in the unit templates, add `[project.urls]`.
8. Regenerate `docs/evidence/fresh-clone.log` on the committed tree.
9. Push to a private fork, get CI green with mypy added, then publish.

**Technical work, in the order of section 4:**

10. P0.1 day-adjusted screen. **Do this before anything about strategies**, because every
    promotion number in this document depends on it.
11. P0.2 denominator mismatch in `pnl vs holding`.
12. P0.3 the standing check that reports the bias.
13. Then, and only then, decide on P1 (executable strategies, richer context) — those are real
    capability changes to a system that is currently trading paper, and they want their own
    plans with their own falsifiers.

## 12. The one-paragraph honest summary

The engineering here is real and unusually disciplined: a single source of truth, a
non-bypassable risk layer, a 52-class gate that can prove it fails, and a documented habit of
retracting its own numbers when they flatter it. The gap is that the loop cannot change
anything that would learn — the strategy parameters are never executed, the prompt is a
constant, the scoring function is fixed — and the one signal that does drive selection is a
one-share price probe that is direction-blind: on sixteen days of a mostly rising market SELL
decisions score 26.5% and BUY decisions 66.2%, and it grades a HOLD that declined a sell as if
the alternative had been a buy. (An earlier version of this paragraph said every SELL scored
0.000 and the system was converging on "never sell"; that came from reading the oldest
counterfactual rows and is retracted in §3.) So the loop filters on a signal that partly encodes
market direction, and calls it self-improvement. Fix the measurement first, make strategies executable
second, and open-source the result with the negative results stated on the front page — which
is a more credible and more useful artefact than a profit claim would have been.

## 13. What was done, item by item (2026-10-07)

Each row names the commit; each commit message carries its measurement. "Waits on data" means
the instrument is built and running and the answer needs market days, not code.

### Measurement (section 4, P0)

| Item | Outcome |
| --- | --- |
| P0.2 benchmark denominators | **Done** `73c6a0c`. Both legs on the same capital and window. Falsifier fired: fixed-size-probe-0001 went from -4.29 to +0.49 against SPY. Verdict still FAIL, 4 of 5 behind. Also fixed: the verdict named the least-bad strategy as "worst" (`c037f11`) |
| P0.1 day-adjusted screen | **Done** `7147b6d`, `a1733b6`. On 9 of 13 days nearly every outcome went one way; raw ratios sat within ~0.12 of what the days predicted. 6 rejections became INCONCLUSIVE; the falsifier ("SELL grades do not move") is refuted |
| P0.3 standing bias check | **Done** `c0ac0da`, `screen-direction-neutral`; a fixture proves it fails |

Defects in the instrument found while doing this, all fixed: refused orders graded as trades
and a SELL credited the cost it pays (`7186d4b`) — on the corrected instrument executed SELLs
were right **1 of 25**, close to the retracted "0 of 43" but for the right reason; screens read
back without `good_trades` (`bf51a89`); a maintenance pass acting on stale reads (`3115fe1`);
calibration graded with default cost (`b10486c`); the daemon's code fingerprint read from disk,
so the stale-code check could never fail (`561fc64`).

### Making the loop able to learn (P1, P2)

| Item | Outcome |
| --- | --- |
| P1.1 rule as the default, LLM overrides with a reason | **Done** `ba9e421`. First live override, 2026-10-07 11:00: rule BUY, model HOLD, reason "the position cap leaves no room" |
| P2.1 paired / direction-neutral scoring | **Done** `49fd028`. Strategies screened on their own rule; the model on `override_value_pct`. The vetoed-SELL grading flaw of §3.3 is gone for paired cycles. **Waits on data**: the first paired cycle scores 24h after it is decided |
| P1.2 market context | **Done** `f63a08f`. Regime, trend, volatility, 1h/1d returns; ~40 tokens. Whether it helps waits on the paired comparison |
| P2.2 knowledge channel | **Done** `9bd1d04` (dedup: 5 copies of one lesson → 4 distinct), `683fe1b` (each lesson shown on half of cycles), `ad595fe` (retire after 10 trading days with no effect). **Waits on data**: inert until 10 days of ablation |
| P2.3 re-open PAUSED | **Done** `bfcbdc7`, bounded. Today the queue is over its cap (16/13), so nothing reopens; with room, 1 of 6 eligible strategies would, the other 5 being re-paused at once by the existing rules |
| Short selling and a rule DSL (from the first review) | **Built** — `docs/history/plans/2026-10-07-bounded-short-and-rule-dsl.md`. Rollout at `AUTOPOIESIS_SHORTS=shadow`; `paper` waits on a shadow session, and no SHORT can be approved until the account is flat |

### Evidence base (P3)

| Item | Outcome |
| --- | --- |
| P3.1 synthetic journal so `make benchmark` runs on a fresh clone | **Not built, deliberately.** Every snapshot model refuses `source` values like `synthetic` or `mock` — the code form of `AGENTS.md` §17. A fixture would parse only by calling itself something else, which defeats the guard rather than satisfying it, and the real bars the replay uses cannot be redistributed here. A fresh clone gets `make benchmark` exit 2 ("nothing to measure"), the no-broker example cycle, and the full offline gate |
| P3.2 research trials out of the ignored tree | **Done** `5b08b8e`: 59 trials, 47 INSUFFICIENT, 12 OVERFIT, 0 PASS |
| P3.3 attribution epoch | **Done** with P1.1: cycles from 2026-10-07 10:55 carry `rule_action`; the paired comparison counts only those and does not estimate the rest |
| P3.4 benchmark's own defects | **Done** in `8727f17` and `c037f11` |
| The 29 unmatched shares | **Done** `cf4ac8d`: priced against the owner's recorded fills - 29 shares, cost 19,408.88, proceeds 22,230.53, owner gain +2,821.65, outside every strategy's PnL |

### Open source (sections 8-11)

All done: LICENSE (`51050b3`), host paths (`51050b3`), lock file and unit names (`2378c0c`), CI
mypy and conda-free operation (`92977d1`), docs reorganised into `docs/history/` with git history
kept (`b3d435b`), community files (`63c3cef`), README front page and counts guarded by a test
(`0014a9c`), fresh-clone evidence regenerated in a new venv (`adeffd1`, after fixing the one
gate check that failed on a clone, `49b3b1c`). **Left for the operator:** the repository name
and `[project.urls]`, and pushing — GitHub refused every push with an internal server error on
2026-10-07.
