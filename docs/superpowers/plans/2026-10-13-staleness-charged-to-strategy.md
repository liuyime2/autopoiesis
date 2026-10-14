# Bounded plan: staleness refusals charged to strategies, and the gate's safe-abstention model

Written 2026-10-13 10:10 EDT, from the real 09:45 EDT open session. This is a pre-edit plan. No code change has been made and none is authorised yet.

## Problem, with the measurement that establishes it

During the first scheduled open session after deploying `9fb9066d820f27c9`, the learner retired a live strategy for a data event.

`fixed-size-bil-sell-001` moved PROBATION -> RETIRED at 09:39:38 EDT with reason
`severe operational failure rate 1.00`. The single charged cycle is 09:34:40 EDT, BIL,
execution REJECTED, Guardian reason:

    'the price quote is 1611s stale, beyond the 900s limit'

`is_system_rejection()` returns `False` for that string, because
`SYSTEM_REJECTION_REASONS` holds only the non-interpolated `'data snapshot is stale'`
and `SYSTEM_REJECTION_REASON_PREFIXES` holds no staleness prefix. So the refusal was
counted as `strategy_fault_rejections`, and with `cycles == 1` the rate is exactly 1.00,
which clears `severe_failure_rate`.

Baseline for the unit: the module comment at `src/autopoiesis/evaluator.py:61` already
records this defect class retiring the library's only SELL-capable strategy, and the
prefix list was the fix for the interpolated messages reachable then. The interpolated
staleness message was not in that set. This is a recurrence, not a new idea.

Success criterion: a Guardian staleness refusal never increments
`strategy_fault_rejections`, and a strategy cannot be retired by the severe-failure rule
on a sample below a stated minimum. Verified by a focused test that feeds the exact
observed string, plus the existing lifecycle and evaluator regressions, plus the frozen
full check.

## What is deliberately not being changed

`stale_after_seconds` stays at 900. Guardian still refuses stale quotes. The retirement
that already happened is not hand-reverted: restoring it outside the admission gate is
the failure the `strategy_engine` comment documents.

## Unit A — classify every staleness refusal as a system rejection

Target `src/autopoiesis/evaluator.py`. Add a prefix covering the interpolated staleness
messages `_stale_reason` produces, which name the aged field: snapshot, price quote, and
any other label it can emit. The existing test that pins these prefixes against
`guardian.py` literals must be extended rather than satisfied by a looser match, so a
reworded message cannot silently reclassify again.

Risk: a prefix broad enough to swallow a strategy's own fault. The staleness messages are
unambiguous, so this is a literal-prefix addition, not a substring search.

## Unit B — a sample floor on the severe-failure rule

`strategy_engine.py` evaluates `severe_failure_rate` on `result.cycles` with no floor, so
one rejection retires. Decide and write down the minimum sample before editing; the
existing `max_error_rate` and `max_rejection_rate` branches have the same exposure and
should be considered together rather than one at a time. A strategy below the floor should
receive an absence of verdict, which the module already has a concept for, not a verdict.

## Unit C — the collector's safe-abstention predicate

`tools/market_readiness.py` treats a `selection_unavailable:` HOLD as safe only when
`execution.status == "SKIPPED"`. A mandate refusal such as PFAI's
`'symbol is priced below the minimum'` is an equally safe abstention and currently trips
`SESSION_PROVENANCE_INVALID`. Separate the collector's two questions, which are currently
conflated in one assertion: did the daemon act on unattributed data, and did the refusal
path hold. This changes what the gate measures, so it needs its own specification review
before the next session, and it must not be relaxed far enough that a genuine
misattributed submission would pass.

## Sequencing and stop conditions

Units A and B are learner correctness and come first. Unit C changes an acceptance gate
and is reviewed separately; it is explicitly not to be applied to today's session verdict.
Each unit needs a written target, focused tests, independent specification review, then
quality review, then the frozen full check.

Stop and roll back if any unit requires relaxing `stale_after_seconds`, widening a hard
risk limit, bypassing Guardian, or restoring a retired strategy outside the admission
gate.

## Carried-forward follow-ups from this session

- `Makefile`'s `READINESS_UNIT_DIR` default resolves to `$(XDG_CONFIG_HOME)/systemd/user`,
  which on this host is not where the systemd user manager reads units, and the install
  still exits 0. Derive the manager's real path or fail when the two disagree.
- `daemon._round_symbols` adds attention names via a price filter and `guardian._outside_mandate`
  re-checks the floor against a later snapshot, so a symbol can be selected at $5.00 and
  refused at $4.755. Decide which of the two is authoritative.
- Deployment stop latency is still not measured, from the 06:08 deployment.
- `tools/self_evolution_probe.py` reports three problems on the clean committed tree:
  `the STRATEGY_LIFECYCLE_UPDATED stage never fired`, `no lifecycle ruling was produced,
  so selection pressure never acted on the scored evidence`, and `the screen never scored a
  decision, so no evidence reached the lifecycle`. Measured identically with and without the
  Unit A change, so it is a pre-existing red baseline and not a consequence of it. It is the
  tool that exists to demonstrate the RSI objective, and it currently demonstrates nothing.

## Outcome, 2026-10-13 13:50 EDT

One unit shipped, one was withdrawn, one became a pinning test, one stays deferred.

### Unit A - shipped

`SYSTEM_REJECTION_REASON_PREFIXES` gained `"the snapshot is "` and `"the price quote is "`,
the two labels `_stale_reason` interpolates into its message. 14 lines in
`src/autopoiesis/evaluator.py`, no behaviour outside the classification changed.
`stale_after_seconds` stays 900 and Guardian still refuses every stale quote; only the
learner stops charging the refusal to the strategy.

`tests/autopoiesis/test_staleness_not_strategy_fault.py`, 21 tests. Red-to-green measured by
reverting the source file and re-running: **7 of the 21 fail without the change**, including
`test_a_stale_refusal_alone_cannot_retire_a_strategy`, which returns
`RETIRED / 'severe operational failure rate 1.00'` on the unpatched tree. The staleness
messages are produced by running the real Guardian over both labels and both directions
rather than copied as literals, so a rewording cannot silently reclassify them again, and the
four refusals `evaluator.py` deliberately leaves charged are pinned as still charged.

### Unit B - withdrawn, with the evidence that withdrew it

The premise was that one fault rejection on one cycle should not retire a strategy. The
repository already decided the opposite on purpose: `test_genuine_rejection_storms_still_retire`
asserts that cycles=2 with 2 fault rejections returns RETIRED, and the module's own comment
records that a terminal verdict for the unevaluable case was tried here and reverted because
it shortened the evidence run until `self_evolution_probe.py` could score nothing.

More decisively, the live incident does not need the floor. With Unit A in place the BIL
cycle charges zero fault rejections, so `failure_rate` is 0 and no retirement follows. The
floor would have been a second, larger behaviour change guarding a defect that was a
symptom. It is recorded here rather than deleted so the next reader knows it was considered
and why it lost. A pinned test now asserts that one genuine fault on one cycle still
retires, so the decision is explicit rather than accidental.

### B2 - investigated, no change, and the first reading was wrong

`trend-follow-20260824-001` reached `probation_restarts: 5` against `MAX_PROBATION_RESTARTS = 3`
in 45 minutes. This file called that a livelock. It is not. `_needs_probation` grants
`PROBATION_CYCLES * (1 + min(probation_restarts, MAX_PROBATION_RESTARTS))` = 52 for every
restart from the third onward, so the candidate drains once `cumulative_cycles` reaches it;
the live one was at 16 of 52 at 10:24 EDT and still improving.

Bounding the *returns* rather than the *budget* was implemented and then reverted: it broke
`test_returning_to_probation_keeps_counting_retries_without_a_verdict_from_the_count`, which
pins this branch as deliberately unbounded and gives the same reason. The contract wins.
`tests/autopoiesis/test_probation_restart_budget.py`, 8 tests, is now a pinning test only -
it asserts the behaviour as it is, including the counter being inert past the cap and the
queue draining - and passes with and without any change, which is what a pinning test is for
and is not evidence of a fix.

### Unit C - still deferred

The gate question is unchanged and is not answered here. `SESSION_QUOTE_NOT_FRESH` fails the
whole window on any stale cycle, BIL produced four in one hour, and three of them were
`HOLD 0` abstentions recorded `REJECTED` rather than `SKIPPED` because Guardian checks
staleness before the HOLD branch. Two decisions remain open: what a correctly-refused stale
abstention means for the gate, and whether an instrument that cannot quote inside the
freshness limit belongs in the configured symbol list. Neither may be answered by loosening
`stale_after_seconds`, and neither should be changed in the same run as Unit A.

## Verification for Unit A

- Focused: 21 new tests, plus 94 passing across `test_score_latch.py`,
  `test_guardian.py`, `test_guardian_trade_freshness.py`, and 235 passing across the
  lifecycle/evaluator/admission/selector/probation/reflection/curriculum selection.
- Full: `make check ENVFILE=/dev/null TEST_ARGS='-W error::pytest.PytestUnhandledThreadExceptionWarning'`
  **1789 passed / 3 skipped**. The clean committed tree measures **1760 passed / 3 skipped**
  under the identical command, so the +29 is exactly the 21 + 8 new tests and the skip count
  is unchanged. The "6 skipped" recorded in earlier evidence was produced under a different
  environment; it is not what this tree does today.
- `make type`: 49 source files, no issues. `ruff check src/ tools/ tests/ examples/`: clean.
- README's stated test-file count moved 94 -> 96, which `test_governance.py` enforces against
  the tree.

## Added after the 10:30 run

Unit B is larger than a probation sample floor and needs its own unit: `trend-follow-20260824-001`
was returned to probation five times in 45 minutes and reached `probation_restarts: 5` against
`MAX_PROBATION_RESTARTS = 3`, so the restart cap is not binding on that path. That is a livelock,
not a slow promotion, and it is the mechanism behind the library's long-standing zero-ACTIVE
state, so it blocks the RSI work the same way this staleness classification does.

Unit C is structural rather than incidental. All four stale cycles of the 09:30-10:30 session are
BIL, a bond ETF whose prints sit 20 to 34 minutes apart in the first hour after an overnight
close. Four of BIL's eight cycles exceeded the 900 s limit, so `SESSION_QUOTE_NOT_FRESH` cannot
pass with BIL in `AUTOPOIESIS_SYMBOLS`. Three of those were `HOLD 0` with no strategy recorded as
`REJECTED` rather than `SKIPPED`, because Guardian checks staleness before the HOLD branch, which
would independently trip `SESSION_PROVENANCE_INVALID`. Unit C therefore has to decide two things,
not one: what a correctly-refused stale abstention means for the gate, and whether a configured
symbol whose instrument cannot quote inside the freshness limit belongs in the configured symbol
list at all. Neither may be answered by loosening `stale_after_seconds`.
## Closing state, 2026-10-13 22:50 EDT

Unit A shipped and deployed as part of `3619ff2ddac17037`. Unit B withdrawn with the evidence
that withdrew it. B2 investigated, first reading corrected, no change. Two further defects
found and fixed the same evening, both recorded in the evidence file: the readiness installer's
unit directory, and realized-PnL attribution to strategies that never sold.

The `self-evolution-closes` gate class is still red. It is not an independent defect: its cause
is the provenance gap before 2026-10-13 plus the 24-hour horizon, both of which resolve with
elapsed time rather than with a code change. The prediction recorded earlier in the evidence
file stands and is checkable by the 2026-10-14 timers.
