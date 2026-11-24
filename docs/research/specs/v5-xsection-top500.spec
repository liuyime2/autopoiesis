# v5: three cross-sectional candidates on the survivorship-free top-500. Frozen before any candidate's returns were computed (2026-11-24).
# Economic prior: momentum (12-1), short-term reversal (1 month) and low volatility are the most replicated cross-sectional effects in US equities; their textbook parameters are used unchanged, so nothing here was chosen from this data.
# Power (docs/evidence/gate_calibration/2026-11-23-universe-placebo.md): over 252 sessions the x2 rule passes a meaningless half-selection 1.5% of the time and finds a 5%/yr edge 87% of the time, a 2%/yr edge 31%. Edges below ~5%/yr are not claimed detectable.
# Development (reported, not used to drop, add or tune anything): sessions 2021-08-02 .. 2025-10-31.
# Holdout: the window below, opened once by `make research-xsection PHASE=holdout`; a second open is refused.
# Stopping rule: a PROVEN candidate goes to a separate forward-paper plan (Guardian and market-open checks unchanged); otherwise v5 is closed negative and this holdout is not re-tested.
hypothesis: at least one of MOM_12_1, REV_1M, LOWVOL_3M, holding half of the point-in-time top-500 US listed equities equal weight and rebalanced every 20 sessions, beats all members equal weight after cost over the holdout
family: xsection-top500-v5
instrument: universe-1Day-adjall set_sha256 ac0531645963ae972dd3d4c0725ae92a5993aafe7b65f5538bf2ab6199e44351 (alpaca iex daily, adjustment all, 2020-07-27..2026-11-20, merger acquirees included)
interval: 1Day
window_start_utc: 2025-11-01T00:00:00Z
window_end_utc: 2026-11-21T00:00:00Z
rule: x2 (gate_calibration.v4_verdicts, ci_floor 0): family of 3, paired block bootstrap block 5, 2000 resamples, seed 20261124, Holm; PROVEN iff Holm p < 0.05 and mean >= 1%/yr and CI_low > 0
primary_metric: daily return of candidate minus all-member equal weight, after cost
expected_trades: 42
candidates: MOM_12_1 = highest half by return over sessions [t-252, t-21); REV_1M = lowest half by return over [t-21, t); LOWVOL_3M = lowest half by population stdev of daily returns over [t-63, t)
eligibility: a member needs as many sessions of its own history as its score reads; ineligible members stay in the benchmark only
membership: research.universe.members_at top_n 500, lookback 63, min_price 5, min_history 126, recomputed every 5 sessions; rebalance every 4th membership date
cost: 0.0003 x turnover on the candidate (gate_calibration.COST)
returns: close over last close seen; 0 after a symbol's last bar (cash at the last close)
code: research/xsection.py run_family; tools/run_xsection_v5.py
ledger_ref: none
