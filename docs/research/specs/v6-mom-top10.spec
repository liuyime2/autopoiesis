# v6: one candidate, 12-1 momentum, top tenth of the survivorship-free top-500. Frozen 2026-11-24, before any bar of the window below was read.
# Why one candidate: docs/evidence/xsection_v5/power.md measured that a one-year holdout under Holm-3 needs IR ~2.4 for even odds; a family of one removes the Holm penalty.
# Selection, stated plainly: this is the best of 28 exploratory development configurations (docs/evidence/xsection_v6/probe.md, sessions 2021-08-02..2025-10-31 only). Its development IR (~0.83) is therefore optimistic; published 12-1 momentum IRs are ~0.5. It was preferred over the short-term reversal composite of similar IR because its break-even cost is ~200 bps per unit turnover against ~36.
# Evidence schedule: three looks on all sessions from 2025-11-01, each opened once. Look 1 = the window below (bars through 2026-11-20, universe set ac0531645963ae97). Look 2 through 2028-11-17, look 3 through 2030-11-15, each after refetching the universe with tools/fetch_universe_bars.py --end <look date> and recording its set_sha256.
# Power stated in advance (normal approximation, two-sided alpha 0.05/3, z 2.394): at IR 0.83, 5.9% at look 1, 16.9% by look 2, 29.5% by look 3; at IR 0.5, 2.9%, 6.3%, 10.1%. The most likely look-1 result is not PROVEN; it is recorded as is.
# Stopping rule: PROVEN at any look -> a separate forward paper-trading plan (Guardian and market-open checks unchanged). annual CI high < 0 at any look, or not PROVEN at look 3 -> v6 closed negative. No look is re-run, and no parameter changes between looks.
# The v5 holdout over this window was never opened; v5 is closed unopened (docs/evidence/xsection_v5/closed.md) so this window is tested once.
hypothesis: MOM_12_1_TOP10, holding the tenth of the point-in-time top-500 US listed equities with the highest return over sessions [t-252, t-21), equal weight, rebalanced every 5 sessions, beats all members equal weight after cost
family: xsection-top500-v6
instrument: universe-1Day-adjall set_sha256 ac0531645963ae972dd3d4c0725ae92a5993aafe7b65f5538bf2ab6199e44351 (alpaca iex daily, adjustment all, 2020-07-27..2026-11-20, merger acquirees included) for look 1; refetched per look after
interval: 1Day
window_start_utc: 2025-11-01T00:00:00Z
window_end_utc: 2026-11-21T00:00:00Z
rule: x2 (gate_calibration.v4_verdicts, ci_floor 0) on a family of one: paired block bootstrap block 5, 2000 resamples, seed 20261124; PROVEN at a look iff two-sided p < 0.05/3 (Bonferroni over three looks) and mean >= 1%/yr and CI_low > 0
primary_metric: daily return of the candidate minus all-member equal weight, after cost, over all sessions from 2025-11-01 to the look
expected_trades: 53
candidates: MOM_12_1_TOP10 = the len(members)//10 eligible members with the highest compounded return over [t-252, t-21), ties by symbol
eligibility: a member needs 252 sessions of its own history; ineligible members stay in the benchmark only
membership: research.universe.members_at top_n 500, lookback 63, min_price 5, min_history 126, recomputed every 5 sessions; rebalance on every membership date
cost: 0.0003 x turnover on the candidate (gate_calibration.COST)
returns: close over last close seen; 0 after a symbol's last bar (cash at the last close)
code: research/xsection.py run_family(V6, every=V6_EVERY, keep=V6_KEEP, alpha=V6_ALPHA); tools/run_xsection_v5.py --phase look1
ledger_ref: none
