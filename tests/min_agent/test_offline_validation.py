"""The missing stage between "candidate" and "trades real orders".

A candidate used to go straight from admission into PROBATION and trade. What sits
in between is a screen over the decisions the candidate has actually produced,
read from journalled counterfactual verdicts.

These tests pin what makes it a screen rather than a backtest: it can reject, it
can never promote, and it says so when it does not know.
"""

import pytest

from min_agent import offline_validation as ov


def _d(verdict, net=None, action="HOLD", cycle="c"):
    return ov.DecisionRecord(cycle_id=cycle, action=action, verdict=verdict,
                             net_return_pct=net)


def test_a_candidate_with_no_history_is_inconclusive_not_a_pass():
    """The common case for a brand-new candidate. Reading this as a pass is how a
    strategy with no evidence at all gets treated as validated."""
    result = ov.validate([], strategy_id="new-one")

    assert result.verdict == ov.INCONCLUSIVE
    assert result.reason.startswith("no recorded decisions")
    assert not result.rejected
    assert result.good_hold_ratio is None


def test_too_few_scored_decisions_is_inconclusive_not_a_pass():
    """Five good decisions are not evidence. The bar exists so silence is never
    reported as success."""
    result = ov.validate(
        [_d(ov.GOOD_HOLD, -1.0, cycle=f"c{i}") for i in range(5)],
        strategy_id="thin", min_scored=10,
    )

    assert result.verdict == ov.INCONCLUSIVE
    assert result.scored == 5
    assert not result.rejected


def test_pending_and_gap_rows_are_excluded_from_the_screen():
    """They say nothing about whether a decision was right, and counting them as
    either good or bad would manufacture a number."""
    result = ov.validate(
        [_d("PENDING"), _d("GAP"), _d(ov.GOOD_HOLD, -1.0, cycle="real")],
        strategy_id="mixed", min_scored=1,
    )

    assert result.decisions == 3
    assert result.scored == 1
    assert result.good_holds == 1


def test_a_candidate_with_losing_trades_is_rejected():
    result = ov.validate(
        [_d(ov.FALSE_TRADE, -2.0, action="BUY", cycle=f"c{i}") for i in range(10)],
        strategy_id="loser", min_scored=10,
    )

    assert result.rejected
    assert result.false_trades == 10
    assert result.correct_outcomes == 0
    # The reason names the losing fills, because "all ten of your trades lost" is the
    # thing a reader needs and the ratio alone does not say it.
    assert "10 of them filled trades that lost money" in result.reason


def test_a_candidate_that_misses_most_alpha_is_rejected():
    decisions = [_d(ov.MISSED_ALPHA, 1.5, cycle=f"c{i}") for i in range(8)]
    decisions += [_d(ov.GOOD_HOLD, -1.0, cycle=f"g{i}") for i in range(2)]
    result = ov.validate(decisions, strategy_id="chases-rallies", min_scored=10)

    assert result.rejected
    assert result.good_hold_ratio == 0.2


def test_a_sound_candidate_screens_through_but_is_still_only_a_screen():
    decisions = [_d(ov.GOOD_HOLD, -1.0, cycle=f"c{i}") for i in range(9)]
    decisions += [_d(ov.MISSED_ALPHA, 1.0, cycle="c9")]
    result = ov.validate(decisions, strategy_id="sound", min_scored=10)

    assert result.verdict == ov.PASS_SCREENED
    assert not result.rejected


def test_the_screen_has_no_code_path_that_promotes():
    """The whole point. Promotion to ACTIVE requires prospective live cycles; if
    this stage could promote, a screen would short-circuit live evidence."""
    verdicts = set()
    for holds in range(30):
        for trades in range(5):
            decisions = [_d(ov.GOOD_HOLD, -1.0, cycle=f"h{i}") for i in range(holds)]
            decisions += [
                _d(ov.MISSED_ALPHA, 1.0, cycle=f"m{i}") for i in range(30 - holds - trades)
            ]
            decisions += [
                _d(ov.FALSE_TRADE, -1.0, action="BUY", cycle=f"f{i}")
                for i in range(trades)
            ]
            verdicts.add(ov.validate(decisions, strategy_id="x", min_scored=1).verdict)

    assert verdicts <= {ov.PASS_SCREENED, ov.REJECT_POOR_DECISIONS, ov.INCONCLUSIVE}
    assert "PROMOTED" not in verdicts and "ACTIVE" not in verdicts


def test_decisions_are_read_from_the_journal_with_the_latest_winning():
    """The daemon re-evaluates the whole journal each maintenance pass, so the
    same cycle appears in several events. Counting them twice would inflate every
    number on the screen."""
    events = [
        {"payload": {"rows": [
            {"cycle_id": "c1", "strategy_id": "s", "action": "HOLD",
             "verdict": "PENDING", "net_return_pct": None},
        ]}},
        {"payload": {"rows": [
            {"cycle_id": "c1", "strategy_id": "s", "action": "HOLD",
             "verdict": "GOOD_HOLD", "net_return_pct": -1.0},
        ]}},
    ]
    decisions = ov.collect_decisions(events, "s")

    assert len(decisions) == 1
    assert decisions[0].verdict == "GOOD_HOLD"


def test_decisions_are_scoped_to_one_strategy():
    """A screen that read the whole journal would grade every candidate on every
    other candidate's decisions."""
    events = [{"payload": {"rows": [
        {"cycle_id": "c1", "strategy_id": "mine", "action": "HOLD",
         "verdict": "GOOD_HOLD", "net_return_pct": -1.0},
        {"cycle_id": "c2", "strategy_id": "theirs", "action": "HOLD",
         "verdict": "FALSE_TRADE", "net_return_pct": -1.0},
    ]}}]
    decisions = ov.collect_decisions(events, "mine")

    assert [d.cycle_id for d in decisions] == ["c1"]


def test_a_strategy_with_no_strategy_id_is_never_screened_on_others_behalf():
    """864 of 920 cycles predate strategy attribution, so their strategy_id is
    None. Attributing them to whichever candidate happens to be selected now
    would credit it with decisions it did not make."""
    events = [{"payload": {"rows": [
        {"cycle_id": "c1", "strategy_id": None, "action": "HOLD",
         "verdict": "GOOD_HOLD", "net_return_pct": -1.0},
    ]}}]
    assert ov.collect_decisions(events, "anyone") == []


def test_neutral_decisions_are_silence_not_failure():
    """A move that did not clear the cost taught us nothing either way.

    The first cut counted NEUTRAL in the denominator, and it rejected
    tiny-fixed-size-001 on 15 neutral decisions - the largest PnL contributor on
    record at +362.66. Silence read as failure.
    """
    result = ov.validate(
        [_d("NEUTRAL", -0.01, cycle=f"n{i}") for i in range(15)],
        strategy_id="quiet", min_scored=10,
    )

    assert result.neutral == 15
    assert result.scored == 0
    assert result.verdict == ov.INCONCLUSIVE
    assert not result.rejected
    assert "less than cost" in result.reason


def test_neutral_decisions_do_not_inflate_a_strategys_good_ratio():
    """A strategy must not look disciplined because it sat still through moves
    that never cleared the cost."""
    decisions = [_d(ov.GOOD_HOLD, -1.0, cycle=f"g{i}") for i in range(5)]
    decisions += [_d(ov.MISSED_ALPHA, 1.0, cycle=f"m{i}") for i in range(5)]
    decisions += [_d("NEUTRAL", -0.01, cycle=f"n{i}") for i in range(40)]

    result = ov.validate(decisions, strategy_id="padded", min_scored=10)

    assert result.scored == 10
    assert result.good_hold_ratio == 0.5
    assert result.neutral == 40


# --- the screen can only reward holding --------------------------------------
#
# For an action that was actually taken, `counterfactual` produced exactly two verdicts:
# FALSE_TRADE when it lost net of cost, and NEUTRAL otherwise. A filled order that *won*
# was NEUTRAL - the same label as a move too small to clear the cost - and NEUTRAL is
# excluded from `scored`. So a strategy's scored count was made only of its mistakes, and
# `if result.false_trades: REJECT` then rejected any strategy that had scored anything at
# all. A trading strategy could not pass the screen at any profit.


def test_a_winning_fill_is_scored_as_a_good_trade():
    """The screen-level half. The primitive itself is pinned in
    `test_counterfactual.py::test_a_winning_fill_is_a_good_trade`."""
    decisions = [_d(ov.GOOD_TRADE, 1.0, action="BUY", cycle=f"w{i}") for i in range(12)]

    result = ov.validate(decisions, strategy_id="profitable-trader", min_scored=10)

    assert result.scored == 12
    assert result.good_trades == 12
    assert result.false_trades == 0
    assert result.verdict == ov.PASS_SCREENED, result.reason


def test_correct_outcomes_count_for_a_strategy_that_also_trades():
    """A strategy that both holds well and trades well is not penalised for trading.

    The stated design intent was "a strategy cannot buy a good ratio by trading
    constantly and luckily", which is a real concern. Implemented as `good_holds /
    scored`, it did not prevent that - it made passing impossible for any trader, because
    a pure trader's `good_holds` is zero by construction.
    """
    decisions = [_d(ov.GOOD_HOLD, -1.0, cycle=f"g{i}") for i in range(6)]
    decisions += [_d(ov.GOOD_TRADE, 1.0, action="SELL", cycle=f"t{i}") for i in range(6)]

    result = ov.validate(decisions, strategy_id="mixed", min_scored=10)

    assert result.scored == 12
    assert result.correct_outcomes == 12
    assert result.verdict == ov.PASS_SCREENED, result.reason
    assert "trade" in result.reason.lower(), "the reason must name what earned the pass"


def test_a_losing_trade_drags_the_ratio_rather_than_rejecting_on_presence():
    """Corrected from the version written yesterday, which asserted "one losing fill
    rejects". That assertion was true then and was the reason the champion of the system
    was refused by the evidence gate at a 0.857 correct-outcome ratio. The property that
    matters is that losses count: each one moves the ratio, and enough of them reject.

    Kept rather than deleted, because "a losing trade must be able to reject a strategy"
    is the real requirement and this is where it is pinned.
    """
    decisions = [_d(ov.GOOD_TRADE, 1.0, action="BUY", cycle=f"w{i}") for i in range(12)]
    one_loss = ov.validate(decisions + [_d(ov.FALSE_TRADE, -1.0, action="BUY", cycle="bad")],
                           strategy_id="one-loss", min_scored=10)
    many_losses = ov.validate(
        [_d(ov.GOOD_TRADE, 1.0, action="BUY", cycle=f"w{i}") for i in range(4)]
        + [_d(ov.FALSE_TRADE, -1.0, action="BUY", cycle=f"b{i}") for i in range(8)],
        strategy_id="mostly-losing", min_scored=10,
    )

    assert one_loss.false_trades == 1
    assert one_loss.correct_outcome_ratio == pytest.approx(12 / 13)
    assert one_loss.verdict == ov.PASS_SCREENED, one_loss.reason

    assert many_losses.false_trades == 8
    assert many_losses.correct_outcome_ratio == pytest.approx(4 / 12)
    assert many_losses.verdict == ov.REJECT_POOR_DECISIONS
    assert many_losses.rejected
    assert "8 of them filled trades that lost money" in many_losses.reason


def test_neutral_is_still_excluded_from_scored():
    """A move that cleared neither cost nor profit stays silence, not evidence."""
    decisions = [_d(ov.GOOD_TRADE, 1.0, action="BUY", cycle=f"w{i}") for i in range(12)]
    decisions += [_d("NEUTRAL", 0.01, action="BUY", cycle=f"n{i}") for i in range(20)]

    result = ov.validate(decisions, strategy_id="padded", min_scored=10)

    assert result.scored == 12
    assert result.neutral == 20


# --- the pass condition is one bar on correct outcomes -----------------------
#
# `if result.false_trades: REJECT` tested for the *presence* of a losing fill and
# returned before the ratio was ever consulted. Two consequences, both measured on the
# live journal over the 13 strategies with >=10 scored decisions:
#
#   * it admitted strategies whose every scored decision was wrong -
#     `trend-follow-20260611-003` has a correct-outcome ratio of 0.000 and passed,
#     because it never placed a fill. `trend-follow-20260611-006` at 0.400 and
#     `trend-follow-sell-002` at 0.093 passed for the same reason, below the 0.5 bar.
#   * it rejected the broker-verified champion, `tiny-fixed-size-001` at +362.66, for
#     having three losing fills out of twenty-one scored.
#
# So the screen was inverted in both directions at once. The comment above the ratio
# already stated the intent - "so a strategy cannot buy a good ratio by trading
# constantly and luckily" - and the ratio is what expresses that. The presence test was
# stricter than the intent in one direction and looser in the other.


def test_a_strategy_whose_every_scored_decision_was_wrong_is_rejected():
    """The case the presence test waved through.

    Ten holds that each missed a rally: no fill was ever placed, so `false_trades` is 0
    and the old rule returned PASS before reading the ratio. This is
    `trend-follow-20260611-003` exactly - 0 good holds, 0 profitable fills, 0 losing
    fills, and a correct-outcome ratio of 0.000.
    """
    decisions = [_d(ov.MISSED_ALPHA, 1.5, cycle=f"m{i}") for i in range(10)]

    result = ov.validate(decisions, strategy_id="always-wrong", min_scored=10)

    assert result.scored == 10
    assert result.false_trades == 0
    assert result.correct_outcomes == 0
    assert result.verdict == ov.REJECT_POOR_DECISIONS
    assert result.rejected


def test_the_champion_with_three_losing_fills_is_not_rejected_for_them():
    """The case the presence test rejected. `tiny-fixed-size-001`: 15 profitable fills,
    3 good holds, 3 losing fills, 21 scored, broker-verified +362.66."""
    decisions = [_d(ov.GOOD_TRADE, 1.0, action="BUY", cycle=f"w{i}") for i in range(15)]
    decisions += [_d(ov.GOOD_HOLD, -1.0, cycle=f"g{i}") for i in range(3)]
    decisions += [_d(ov.FALSE_TRADE, -1.0, action="BUY", cycle=f"b{i}") for i in range(3)]

    result = ov.validate(decisions, strategy_id="champion", min_scored=10)

    assert result.scored == 21
    assert result.correct_outcome_ratio == pytest.approx(18 / 21)
    assert result.verdict == ov.PASS_SCREENED, result.reason


def test_trading_luckily_cannot_buy_a_pass():
    """The intent the presence test was reaching for, now expressed by the ratio.

    A strategy that fills constantly and wins just over half the time has a
    correct-outcome ratio of 0.5 and passes; one that wins less than half does not, and
    neither is waved through by having no losing fill at all.
    """
    lucky = [_d(ov.GOOD_TRADE, 0.2, action="BUY", cycle=f"w{i}") for i in range(6)]
    lucky += [_d(ov.FALSE_TRADE, -0.2, action="BUY", cycle=f"b{i}") for i in range(4)]
    unlucky = [_d(ov.GOOD_TRADE, 0.2, action="BUY", cycle=f"w{i}") for i in range(4)]
    unlucky += [_d(ov.FALSE_TRADE, -0.2, action="BUY", cycle=f"b{i}") for i in range(6)]

    assert ov.validate(lucky, strategy_id="lucky", min_scored=10).verdict == ov.PASS_SCREENED
    rejected = ov.validate(unlucky, strategy_id="unlucky", min_scored=10)
    assert rejected.verdict == ov.REJECT_POOR_DECISIONS
    assert rejected.rejected


def test_a_hold_only_strategy_faces_the_same_bar_as_one_that_trades():
    """One bar, applied to correct outcomes whatever action produced them."""
    holds = [_d(ov.GOOD_HOLD, -1.0, cycle=f"g{i}") for i in range(6)]
    holds += [_d(ov.MISSED_ALPHA, 1.0, cycle=f"m{i}") for i in range(4)]
    trades = [_d(ov.GOOD_TRADE, 1.0, action="BUY", cycle=f"w{i}") for i in range(6)]
    trades += [_d(ov.MISSED_ALPHA, 1.0, cycle=f"m{i}") for i in range(4)]

    from_holding = ov.validate(holds, strategy_id="holder", min_scored=10)
    from_trading = ov.validate(trades, strategy_id="trader", min_scored=10)

    assert from_holding.correct_outcome_ratio == from_trading.correct_outcome_ratio
    assert from_holding.verdict == from_trading.verdict == ov.PASS_SCREENED


def test_a_result_survives_the_journal_round_trip():
    """The daemon reads screens back from the journal; every count must come back.

    It used to rebuild them by hand without `good_trades`, so a strategy right 7 times out of
    14 by trading read back as 0 of 14 and was retired with a reason quoting 0.000.
    """
    from min_agent.offline_validation import OfflineValidationResult

    original = OfflineValidationResult(
        strategy_id="s", verdict="REJECT_POOR_DECISIONS", reason="r", decisions=20, scored=14,
        good_holds=0, missed_alpha=2, false_trades=5, good_trades=7, neutral=3,
        good_hold_ratio=0.0, mean_net_pct=0.12,
    )
    back = OfflineValidationResult.from_payload("s", original.to_payload(), "evt")
    assert back == original
    assert back.correct_outcome_ratio == 0.5


def _dr(i, action, verdict, day):
    from min_agent.offline_validation import DecisionRecord

    return DecisionRecord(cycle_id=f"c{i:03d}", action=action, verdict=verdict,
                          net_return_pct=0.0, day=day)


def test_day_direction_is_the_market_s_move_not_the_decisions_grade():
    """The base rate comes from the price, so a strategy is not measured against itself.

    The first version read it from verdicts; with one strategy trading a day, its margin was
    zero by construction. Here every row on the 29th is a losing SELL, yet the day is up -
    because the price rose - and that is what the SELLs have to be read against.
    """
    from min_agent.offline_validation import day_direction

    class _E:
        def __init__(self, rows):
            self.payload = {"rows": rows}

    def row(cid, action, verdict, day, net):
        return {"cycle_id": cid, "action": action, "verdict": verdict,
                "decided_at": f"2026-09-{day}T14:00:00", "net_return_pct": net}

    rows = [
        row("a", "SELL", "FALSE_TRADE", 29, 0.8),
        row("b", "SELL", "FALSE_TRADE", 29, 0.6),
        row("c", "HOLD", "GOOD_HOLD", 30, -0.4),
        row("d", "BUY", "GOOD_TRADE", 30, 0.3),
        row("e", "HOLD", "NEUTRAL", 30, 0.01),   # inside the band: no direction
    ]
    stale = [row("a", "SELL", "GOOD_TRADE", 29, -0.8)]  # superseded by the later row
    assert day_direction([_E(stale), _E(rows)]) == {"2026-09-29": 1.0, "2026-09-30": 0.5}


def test_a_seller_on_rising_days_is_not_rejected_for_the_market():
    """0 of 15 sells right, all on days every seller lost: that is the days, not the rule.

    fixed-size-sell-20260724-001 was rejected exactly so under the raw gate.
    """
    from min_agent.offline_validation import REJECT_POOR_DECISIONS, validate

    days = ["2026-10-01"] * 8 + ["2026-10-05"] * 7
    decisions = [_dr(i, "SELL", "FALSE_TRADE", d) for i, d in enumerate(days)]
    raw = validate(decisions, strategy_id="s")
    adjusted = validate(decisions, strategy_id="s", day_up={"2026-10-01": 0.9, "2026-10-05": 0.9})
    assert raw.verdict == REJECT_POOR_DECISIONS
    assert adjusted.verdict != REJECT_POOR_DECISIONS
    assert adjusted.day_margin == -0.1


def test_doing_much_worse_than_the_days_is_still_rejected():
    from min_agent.offline_validation import REJECT_POOR_DECISIONS, validate

    # mixed days: a direction-blind BUY is right half the time; this one is never right
    days = ["2026-10-01"] * 10 + ["2026-10-02"] * 10
    decisions = [_dr(i, "BUY", "FALSE_TRADE", d) for i, d in enumerate(days)]
    result = validate(decisions, strategy_id="s", day_up={"2026-10-01": 0.5, "2026-10-02": 0.5})
    assert result.verdict == REJECT_POOR_DECISIONS
    assert result.day_margin == -0.5
    assert result.day_margin < -2 * result.day_margin_se


def test_slightly_below_the_days_is_noise_not_a_rejection():
    from min_agent.offline_validation import INCONCLUSIVE, validate

    days = ["2026-10-01"] * 10 + ["2026-10-02"] * 10
    verdicts = ["GOOD_TRADE"] * 9 + ["FALSE_TRADE"] * 11
    decisions = [_dr(i, "BUY", v, d) for i, (v, d) in enumerate(zip(verdicts, days, strict=True))]
    result = validate(decisions, strategy_id="s", day_up={"2026-10-01": 0.5, "2026-10-02": 0.5})
    assert result.verdict == INCONCLUSIVE
    assert -2 * result.day_margin_se < result.day_margin < 0


def test_a_decision_without_a_day_keeps_the_raw_gate():
    """Rows journalled before they carried a decision time cannot be read against a day."""
    from min_agent.offline_validation import validate

    decisions = [_dr(i, "SELL", "FALSE_TRADE", None) for i in range(12)]
    result = validate(decisions, strategy_id="s", day_up={"2026-10-01": 1.0})
    assert result.verdict == "REJECT_POOR_DECISIONS"
    assert result.day_margin is None


def test_one_day_is_enough_because_the_base_rate_is_every_strategy_s():
    from min_agent.offline_validation import PASS_SCREENED, validate

    decisions = [_dr(i, "BUY", "GOOD_TRADE" if i % 2 else "FALSE_TRADE", "2026-10-02") for i in range(12)]
    result = validate(decisions, strategy_id="s", day_up={"2026-10-02": 0.4})
    assert result.verdict == PASS_SCREENED
    assert result.day_margin > 0


def test_days_that_decided_every_outcome_neither_pass_nor_reject():
    """fixed-size-sell-20260724-001: 15 sells on 2026-10-05, a day every graded outcome was up.

    The raw gate rejected it; a margin of zero with zero variance would pass it. Neither is
    supported - the day decided all fifteen outcomes.
    """
    from min_agent.offline_validation import INCONCLUSIVE, validate

    decisions = [_dr(i, "SELL", "FALSE_TRADE", "2026-10-05") for i in range(15)]
    result = validate(decisions, strategy_id="s", day_up={"2026-10-05": 1.0})
    assert result.verdict == INCONCLUSIVE
    assert result.day_margin_se == 0.0


def test_a_strategy_is_screened_on_its_rule_not_on_the_model_s_override():
    """The model vetoed the strategy's SELL; the strategy is graded on the SELL it decided."""
    from min_agent.offline_validation import collect_decisions

    class _E:
        payload = {"rows": [
            {"cycle_id": "c1", "strategy_id": "s", "action": "HOLD", "verdict": "MISSED_ALPHA",
             "rule_action": "SELL", "rule_verdict": "FALSE_TRADE", "net_return_pct": 1.0,
             "decided_at": "2026-10-07T15:00:00"},
            {"cycle_id": "c2", "strategy_id": "s", "action": "HOLD", "verdict": "GOOD_HOLD",
             "net_return_pct": -1.0, "decided_at": "2026-10-07T16:00:00"},
        ]}

    first, second = collect_decisions([_E()], "s")
    assert (first.action, first.verdict) == ("SELL", "FALSE_TRADE")
    assert (second.action, second.verdict) == ("HOLD", "GOOD_HOLD"), "unpaired rows are unchanged"
