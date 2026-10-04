# Put selection pressure inside the lifecycle rules, where any caller gets it

Date: 2026-10-03
Baseline commit for rollback: `c78d6e0`
Status: plan written before code, per AGENTS.md #4.

---

## 0. What "self-evolution" can honestly mean today

The request was to make the system self-evolving. Measured first, because the honest answer
is narrower than the phrase:

- 87.7% of realised PnL is one exit at one price, so **no strategy has a proven edge**.
- Confidence is bimodal — 637 near 0, 542 near 1, nothing between — and Brier is 0.5342
  against the 0.25 of a constant 0.5 claim.

Against that evidence, "self-evolution makes the strategies better" is not a falsifiable
claim. What **is** falsifiable, and is what this builds: the system's own selection pressure
reacts to its own scored evidence. That is the first time the selection pressure will have
ever run against evidence rather than against silence.

## 1. The measurement that found the defect

Driving the real loop over 847 real bars, then feeding the resulting scored decisions through
the real screen and the real lifecycle manager:

```
该策略的决策裁定: 819
离线筛查: verdict=REJECT_POOR_DECISIONS scored=580 correct_outcome_ratio=0.475862
  good_holds=6 good_trades=270 false_trades=304 missed_alpha=0
lifecycle 裁定: None（留在 PROBATION）
```

**A screen that says `REJECT_POOR_DECISIONS` produced no ruling at all.** A strategy with 304
losing trades out of 580 scored stayed in probation, and nothing anywhere said so.

Isolated to a minimal case — 20 `GOOD_TRADE` and 25 `FALSE_TRADE`, ratio 0.444 — the same
thing: `lifecycle 裁定: None`. And `_review_one`'s source does not contain
`REJECT_POOR_DECISIONS` at all.

### Root cause

Rejection is implemented in **`AgentDaemon._apply_offline_rejection`**, not in
`StrategyLifecycleManager`. `_screen_all_strategies` calls it as a side effect when
`result.rejected`. So the veto lives in the daemon's control flow rather than in the rule
set, which means:

- any other caller of `StrategyLifecycleManager.review()` gets promotion gating but **no
  rejection** — an asymmetry with no principled basis;
- the lifecycle rules and the screen disagree about who is responsible, so the journal can
  contain `REJECT_POOR_DECISIONS` for a strategy that is still `PROBATION`;
- `PAUSED` and `RETIRED` are both used by the daemon path, and the distinction is invisible
  from the rules.

This is the same class of defect as the three drifted verdict copies fixed earlier: one
authoritative rule, restated as control flow somewhere else, and the two drift.

## 2. The minimal change

Move the veto into `_review_one`, where every other lifecycle rule already lives, and let the
daemon keep calling it — so there is one authoritative implementation and no behaviour change
on the live path.

- `_review_one` gains a branch: an `evidence` verdict of `REJECT_POOR_DECISIONS` **retires**
  the strategy, with the ratio and the counts in the reason.
- `RETIRED`, not `PAUSED`. PAUSED is what the daemon used, but PAUSED is not terminal in the
  rules while RETIRED is, and a strategy whose own recorded decisions were 48% correct has
  earned a final answer rather than another probation. This is a deliberate change from the
  daemon's current `PAUSED`, and it is the one behavioural difference the change introduces.
- The reason records the evidence, so the journal says *why* in numbers rather than asserting
  that the strategy was bad.

## 3. Why this counts as closing the loop

Success is not "the strategy got worse". It is that **one authoritative rule set now produces
a ruling from scored evidence**, verifiable three ways:

1. the same scored decisions yield the same ruling when read by an independent path;
2. the ruling is journalled with its evidence in the reason;
3. `make verify` proves the rule is reachable from `StrategyLifecycleManager` and not only
   from the daemon.

## 4. What is deliberately not built

- **No strategy generator.** Mutating strategies around the rejection reason is the obvious
  next step and it is exactly the premature capability the objective forbids: with zero
  promotions and one rejection there is nothing yet to steer a generator. It becomes
  justified when rejections exist *and* something is promoted from them.
- **No promotion speed-up, no queue change.** Measured already: adding candidates lengthens
  the serial queue rather than accelerating any candidate.
- **No off-hours loop, no parameter search, no confidence recalibration.** Each was measured
  and deferred with its reasoning recorded.
- **Nothing touches the live account.** This runs in replay over real bars.

## 5. Verification

- A test that `REJECT_POOR_DECISIONS` retires, with the ratio in the reason.
- A test that `INCONCLUSIVE` and `PASS_SCREENED` do **not** retire — the veto must not fire on
  silence, which is the failure the admission gate already got wrong once.
- A test that no evidence at all does not retire.
- The replay chain re-run end to end, asserting it now yields a ruling.
- `make verify` green; the daemon restarted onto the new source.

## 6. Rollback

One commit. Reverting returns the tree to `c78d6e0`.