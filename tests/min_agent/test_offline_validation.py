"""The missing stage between "candidate" and "trades real orders".

A candidate used to go straight from admission into PROBATION and trade. What sits
in between is a screen over the decisions the candidate has actually produced,
read from journalled counterfactual verdicts.

These tests pin what makes it a screen rather than a backtest: it can reject, it
can never promote, and it says so when it does not know.
"""

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
    assert result.reason.startswith("10 of 10")


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


def test_a_losing_trade_is_still_rejected():
    """The fix must not weaken the gate. One losing fill rejects, as before."""
    decisions = [_d(ov.GOOD_TRADE, 1.0, action="BUY", cycle=f"w{i}") for i in range(12)]
    decisions += [_d(ov.FALSE_TRADE, -1.0, action="BUY", cycle="bad")]

    result = ov.validate(decisions, strategy_id="mixed-results", min_scored=10)

    assert result.false_trades == 1
    assert result.verdict == ov.REJECT_POOR_DECISIONS
    assert result.rejected


def test_neutral_is_still_excluded_from_scored():
    """A move that cleared neither cost nor profit stays silence, not evidence."""
    decisions = [_d(ov.GOOD_TRADE, 1.0, action="BUY", cycle=f"w{i}") for i in range(12)]
    decisions += [_d("NEUTRAL", 0.01, action="BUY", cycle=f"n{i}") for i in range(20)]

    result = ov.validate(decisions, strategy_id="padded", min_scored=10)

    assert result.scored == 12
    assert result.neutral == 20
