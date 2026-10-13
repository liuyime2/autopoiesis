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