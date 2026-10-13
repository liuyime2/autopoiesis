# Audit repair execution plan

Goal: make research and strategy evaluation fail closed on untrustworthy evidence,
restore reproducible checks, and report what the paper record actually establishes.
Architecture: reuse the existing Make entry points, preregistration, journal and
evaluators. Preserve original records. No broker orders, risk relaxation or live mode.
Tech stack: Python, pytest, Make, existing systemd templates; no new dependencies.

## Baseline and acceptance

Baseline commit: 80d0593 (source baseline 49063c8). Main has an existing uncommitted
prereg.py path edit; preserve it. Implementation is isolated in codex/rsi-audit-repairs.
Native worktree tools did not return; a normal Git worktree provides the isolation.
planning-with-files is not installed; this written plan and evidence log provide
the repository's documentation gate, with installed Superpowers execution skills.

Measured prior audit: UTC CI 1 failed / 1323 passed / 5 skipped; local check 1329
passed in 315.80s. Research uses 134456 bars despite a six-month declaration;
862/1602 attributed cycles have symbol mismatch. These are baseline observations,
not claims about a new run. Reproduce focused failures before changing their cause.

- [ ] 1. Deterministic CI and truthful provenance. Reproduce the VIX test under UTC;
  use explicit synthetic test dates without weakening the four-day production
  freshness rule. Fix Git status detection and remove accidental production-host
  work from ordinary tests. Verify via Make focused tests, lint and type checks.
- [ ] 2. Mandatory research contract. Connect registration verification, declared
  symbol/interval/window, tape validation, evaluator identity and trial persistence
  to driver.py, Makefile and research.service.in. Fix registry location without
  writing site-packages. Reject conflicting overlapping bars. Test refusals before
  backtests/ledger writes, exact repeat identity and supported rule constraints.
  Run the gate on an isolated copy of real cached bars; report actual outcomes.
- [ ] 3. Exclude contaminated learning evidence. Trace all journal consumers;
  centralize eligibility, preserve accounting/history, record exclusions, snapshot
  strategy universe for future decisions and refuse unknown historical attribution
  for strategy learning. Test mismatches at real consumers and audit real journal
  read-only. No retroactive invented strategy universes or lifecycle reversals.
- [ ] 4. Correct performance claims. Trace capital denominator and benchmark
  alignment. Replace unsupported relative-return claims with a verifiable contract
  or explicit unavailable status; keep owner flows separate and preserve real PnL.
  Test overlapping exposures/owner flows and run real read-only benchmark.
- [ ] 5. Reliability and remaining gaps. Inspect backups, health, stop budgets and
  deploy entry points. Repair measured defects with focused regression tests; run
  snapshot/restore in disposable paths. Offsite durability and long-term RSI uplift
  remain pending if an external destination or new real observations are needed.
- [ ] 6. Integration evidence. Independent spec/code review after each unit. Run
  make check, UTC tests, required verify gates and credential-free fresh clone on
  committed changes. Reconcile RSI plan/evidence/docs. Publish a reviewable branch
  and inspect GitHub CI if authentication permits; never report local as remote.

## Evidence and follow-up

Each unit records commands, outputs, root cause, limits and commit below. Synthetic
fixtures prove software behavior only. Real research results cannot establish live
profitability. A profitable, autonomous recursive improvement loop still requires
frozen control/treatment experiments and clean paper observations over time.
