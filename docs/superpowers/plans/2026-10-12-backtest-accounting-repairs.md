# Backtest accounting and causal-check repair

Problem: the existing round-trip result charges entry commissions to `costs` but
does not subtract them from `net_pnl`. Mark-to-market multiplies an entry-relative
return on every bar, and drawdown observes only initial/final equity. The causal
check copies every prefix even for constant-time rules. These corrupt after-cost
and risk measurements and slow the experiment loop.

Baseline to reproduce before source changes: flat synthetic prices 100,100 with
0.1% commission each side should have net -0.20, not -0.10; synthetic 100,120,90,110
with no costs should preserve the 25% peak-to-trough drawdown even though the final
price rises. A 60-bar causal check currently copies 1,830 prefix elements. These
are explicitly synthetic arithmetic/complexity regressions, not market evidence.

Acceptance: net equals gross minus both commissions; wins/losses reflect full
round-trip cost; complete marked equity curve catches intra-trade drawdown;
causality comparison remains active and planted future-reading rules still fail;
the existing threshold grid's causal check requires no prefix copies. Preserve
the documented spent-notional return metric, clearly a diagnostic and not an
allocated-capital portfolio return.

Minimum design: subtract entry commission when opening; retain entry commission
for trade outcome classification; mark equity from accumulated realized PnL plus
current position PnL once per bar, using first-entry notional as its fixed starting
capital; use a bounded Sequence view for causal checks instead of a copied prefix.
Run focused red tests, then backtest/research tests, lint/type and independent
review. Re-run final real single-fetch diagnostic and repeat with new evaluator
identity; prior results belong to the old evaluator and must not be reused.

Red command: `make test ENVFILE=/dev/null
TEST_ARGS='tests/autopoiesis/test_research_backtest.py -k "synthetic_round or
synthetic_intra or synthetic_prefix"'`: **3 failed, 28 deselected in 0.38s**.
Observed synthetic gross 0.10 instead of 0, drawdown 0 instead of 25%, and
1,830 copied prefix elements. After the surgical repair, the complete backtest
file returned **31 passed in 1.50s**, including planted future-reading failure
detection. Initial lint found import ordering; corrected immediately. Additional
multiple-round-trip and cost-dependent win classification checks were added.

Final focused research/fetch command:
`make test ENVFILE=/dev/null TEST_ARGS='tests/autopoiesis/test_research*.py
tests/autopoiesis/test_replay_bar_fetch.py'` -> **142 passed in 6.76s**.
Scoped lint passed after correcting import order. Independent spec review passed
**33 backtest tests in 1.31s**; independent quality review passed **33 in 1.43s**
and approved accounting and bounded causal visibility. No broker or runtime state
changed. Final real run/repeat under the new hash remains task 6 verification.
