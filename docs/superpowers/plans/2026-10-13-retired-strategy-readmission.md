# Bounded plan: an audited re-admission path for an erroneously retired strategy

Written 2026-10-13 14:30 EDT, from the 2026-10-13 open session. This is a pre-edit plan. No
code change has been made. The session it responds to is described in
`../../evidence/market-open-readiness-2026-10-13.md`.

## Problem, with the measurement that establishes it

`fixed-size-bil-sell-001` was retired at 09:39:38 EDT with
`severe operational failure rate 1.00`, charged for one cycle in which Guardian refused to
trade on a 1,546-second-old BIL quote. The attribution was wrong and the fix for it is now
deployed as `3619ff2ddac17037`. The strategy itself was never at fault and nothing about the
market has changed.

Recovery is nonetheless impossible, by construction rather than by policy. Two independent
blocks, both read out of the code:

- `StrategyAdmissionGate.admit` returns `accepted=False` with
  `strategy_id already exists; duplicate admission rejected` before any other check runs.
  The branch tests `self.strategy_library.exists(strategy.strategy_id)` and never inspects
  the lifecycle, so `RETIRED` is indistinguishable from `ACTIVE`.
- `_behavioural_duplicate` iterates the whole library with no lifecycle filter, so any new
  `BIL/SELL/1/25000` proposal is refused as behaviourally identical to the retired copy. A
  different `strategy_id` with the same behaviour cannot take the slot back either.

`StrategyLibrary` exposes `save`, `exists`, `try_load`, `load` and `list`. There is no delete
and no reinstate. So a wrong retirement is permanent inside the gate, and the only ways out
are hand-editing the live library - the bypass this project removed - or deleting the file.

The recorded reason for the retirement survives in the journal and in
`deployment-checkpoints/20261013/predeploy-state-1408`, so the evidence is not lost either way.
What is lost is the capability: BIL has no strategy and can only abstain.

## What this is not

It is not a proposal to stop retiring. The severe-failure rule is doing its job and the
staleness fix means refusals it should never have charged are no longer charged. It is also
not a general "retry" button. Retirement has to stay a real, evidence-backed, terminal state
for strategies that deserve it.

## The design constraint that decides the shape

A re-admission that is merely available is a loop: retire, re-admit, retire again, each
cycle churning the library and each churn event becoming learning evidence. The bound the
module already reaches for elsewhere is the same one needed here.

The stronger constraint is this: **a re-admission must be refused while the recorded reason
for the retirement still holds.** For `fixed-size-bil-sell-001` the reason was
`severe operational failure rate 1.00` derived from a refusal that has since been
reclassified as a system rejection, so the reason no longer describes the strategy. For a
strategy retired on `broker-verified negative realized PnL`, the reason still holds and
re-admission must be refused until the evidence has moved.

## Recommended minimal shape

Three parts, each independently testable.

**1. A lifecycle-aware branch at the top of `admit`.** When the id exists and its lifecycle is
`RETIRED`, do not short-circuit. Re-run the existing checks - capacity, reference price,
progression, `guardian.review_strategy` - unchanged, and on a pass save with
`lifecycle=PROBATION` and an incremented re-admission counter. Every other rejection reason
keeps working exactly as it does now, and an `ACTIVE` or `PROBATION` id is still refused as a
duplicate. No new gate is introduced, which is the point: the re-admitted strategy is held to
the same standard as a fresh one.

**2. A `retirement_reason`-derived refusal for the reasons that do not expire.** At minimum
`broker-verified negative realized PnL`, which is a statement about money that has already
happened, and `severe operational failure rate` while the fault rejections behind it are
still attributed to the strategy. The refusal reason must name which recorded reason blocked
it. Without this the path is a resurrection button for strategies the evidence already
condemned.

**3. A bound.** A per-strategy re-admission count with its own constant, on the model of
`MAX_PROBATION_RESTARTS`. The re-admitted candidate starts on `PROBATION`, so it must earn its
place again through the same probation and promotion path; the bound only stops an unbounded
re-admission loop.

`StrategyLifecycleDecision.update` is the existing vehicle for state written alongside a
lifecycle change, and the `lifecycle_reason` field is where the new reason text belongs, so
neither part needs a new persistence mechanism.

## Audit

Re-admission is a lifecycle change and must produce the same
`STRATEGY_LIFECYCLE_UPDATED` decided/applied event pair the retirement produced, carrying the
original retirement reason alongside the new one. Without that, the journal would show a
strategy reappearing with no record of why it left.

## Success criterion, and how it is verified

- The live case: `fixed-size-bil-sell-001`, retired at 09:39:38 EDT, can be put back on
  `PROBATION` through the gate, with a journal event naming both reasons.
- A strategy retired for `broker-verified negative realized PnL` is refused, by name.
- An `ACTIVE` strategy's id is still refused as a duplicate.
- A strategy retired on a system refusal is admitted; a strategy retired on its own fault is
  not, unless the evidence behind that fault has changed.
- The re-admission bound holds: the fourth attempt is refused.
- The full suite, including `test_strategy_engine.py`, `test_score_latch.py` and the staleness
  attribution tests added today, passes unchanged otherwise.

## Sequencing and stop conditions

This must not be implemented while a session is being observed. It changes what the admission
gate accepts, and the gate is the only thing standing between the curriculum and a strategy
book full of copies of the same trade - which the live record shows rejecting four proposals
today for exactly that reason.

Stop and roll back if any part requires retiring less, relaxing Guardian, widening a hard
limit, deleting a strategy file, or letting a re-admitted strategy skip probation.

## Follow-ups recorded alongside it

- `Makefile`'s `READINESS_UNIT_DIR` default writes to `$XDG_CONFIG_HOME/systemd/user` while
  the manager reads `$HOME/.config/systemd/user`, and exits 0 either way. Measured twice today.
- `_behavioural_duplicate` ignoring lifecycle is the second half of this problem: even with a
  re-admission path, a retired strategy would still block an equivalent new one. Whether that
  is correct is a separate decision and is deliberately not bundled here.
- `SESSION_QUOTE_NOT_FRESH` is unpassable with BIL in `AUTOPOIESIS_SYMBOLS`, for the structural
  reason recorded in the evidence. Removing the gate's structural failure does not remove
  BIL's chronic staleness, and the two questions are separate.