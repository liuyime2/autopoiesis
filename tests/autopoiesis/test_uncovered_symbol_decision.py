"""A symbol no strategy covers must still get a decision, and it must not be a strategy's.

Measured 2026-10-14: roughly 316 cycles a day across the watchlist and XLB were
`selection_unavailable: no matching frozen strategy covering snapshot symbol`, so the
model was never asked about them at all. The plan authorises asking it, with HOLD as the
baseline and a reason required to depart from it.

Three properties are load-bearing and each is asserted here: HOLD is the baseline, the
decision carries no `strategy_id` so it can never enter a strategy's metrics or its
lifecycle, and a selector that failed is an outage rather than an uncovered symbol and
must not spend a model call.

All fixtures synthetic: no model, no broker, no account.

Usage: pytest tests/autopoiesis/test_uncovered_symbol_decision.py -q
"""

from __future__ import annotations

import pytest

from autopoiesis.llm_decision import NO_STRATEGY_MARKER, HybridDecisionEngine
from autopoiesis.models import TradeDecision


class _Snapshot:
    symbol = "XLB"
    quote_time = None


class _LLM:
    """Records the calls and returns whatever it was told to."""

    def __init__(self, decision=None, error=None):
        self._decision = decision
        self._error = error
        self.calls = []

    def decide(self, context):
        self.calls.append(context)
        if self._error is not None:
            raise self._error
        return self._decision


def _engine(llm, *, selection_broke=False, library_empty=False):
    engine = HybridDecisionEngine.__new__(HybridDecisionEngine)
    engine.llm = llm
    engine._selected_spec = None
    engine._selected_provenance = None
    engine._rule_decision = None
    engine._selection_broke = selection_broke
    engine._library_empty = library_empty
    # None leaves the path exactly as it was before the stale-quote guard existed; the
    # guard has its own tests.
    engine.stale_after_seconds = None
    return engine


def _decision(action="HOLD", *, symbol="XLB", reason=None, confidence=0.4):
    return TradeDecision(
        symbol=symbol, action=action, quantity=1 if action != "HOLD" else 0,
        confidence=confidence, rationale="model says so", override_reason=reason,
    )


# --- the model is asked at all, which it was not -------------------------------


def test_an_uncovered_symbol_is_asked_of_the_model():
    llm = _LLM(_decision("HOLD"))
    decision = _engine(llm)._decide_uncovered(_Snapshot(), {})

    assert len(llm.calls) == 1, (
        "the defect was that the model was never asked about these symbols"
    )
    assert decision.decision_source == "llm", (
        "the model answered, and the monitor that counts whether it answered must see it"
    )
    assert NO_STRATEGY_MARKER in decision.rationale, decision.rationale


def test_an_uncovered_symbol_carries_no_strategy_id():
    """Which is what keeps it out of every per-strategy metric and out of the lifecycle.

    The plan's acceptance is that the count of `no_strategy` decisions inside strategy
    scoring is zero, and `strategy_id is None` is the mechanism.
    """
    llm = _LLM(_decision("HOLD"))
    decision = _engine(llm)._decide_uncovered(_Snapshot(), {})

    assert decision.strategy_id is None, (
        "a decision no strategy made must not be attributed to one"
    )
    assert decision.strategy_provenance is None


# --- HOLD is the baseline ------------------------------------------------------


def test_a_departure_without_a_reason_is_not_taken():
    """The same rule the covered path applies, for the same measured reason."""
    llm = _LLM(_decision("BUY", reason=None))
    decision = _engine(llm)._decide_uncovered(_Snapshot(), {})

    assert decision.action == "HOLD", (
        "HOLD is the baseline for an uncovered symbol and a departure needs a reason"
    )
    assert decision.quantity == 0
    assert decision.decision_source == "llm", "the model still answered"
    assert NO_STRATEGY_MARKER in decision.rationale, decision.rationale
    assert "BUY" in decision.rationale, (
        "the refusal must name what was refused: " + decision.rationale
    )


def test_a_departure_with_a_reason_is_taken():
    llm = _LLM(_decision("SELL", reason="the position is oversized against the risk budget"))
    decision = _engine(llm)._decide_uncovered(_Snapshot(), {})

    assert decision.action == "SELL", "a reasoned departure is the point of asking"
    assert decision.strategy_id is None, "and it is still not a strategy's decision"


# --- an outage is not an uncovered symbol --------------------------------------


def test_a_broken_selector_does_not_spend_a_model_call():
    """`test_a_broken_policy_engine_abstains_before_the_llm` pins this on the covered path.

    The two states were indistinguishable - the selection handler set `strategy_id = None`
    for a broken selector and for an uncovered symbol alike - and they call for opposite
    behaviour. An uncovered symbol may be asked of the model; a selector that threw may
    not, because the strategy library could not be read.
    """
    llm = _LLM(_decision("HOLD"))
    decision = _engine(llm, selection_broke=True)._decide_uncovered(_Snapshot(), {})

    assert llm.calls == [], "a failed selector must not trigger a model call"
    assert decision.decision_source == "policy_engine"
    assert "selection could not run" in decision.rationale, decision.rationale


def test_a_model_that_does_not_answer_abstains_rather_than_guessing():
    llm = _LLM(error=TimeoutError("slow"))
    decision = _engine(llm)._decide_uncovered(_Snapshot(), {})

    assert decision.action == "HOLD"
    assert decision.decision_source == "policy_engine", (
        "nothing was decided by the model, so nothing may be attributed to it"
    )
    assert "TimeoutError" in decision.rationale, decision.rationale


def test_a_model_naming_another_symbol_abstains():
    """It is not deciding about this instrument, and Guardian would refuse it anyway."""
    llm = _LLM(_decision("BUY", symbol="SPY", reason="because"))
    decision = _engine(llm)._decide_uncovered(_Snapshot(), {})

    assert decision.action == "HOLD"
    assert decision.decision_source == "policy_engine"
    assert "another symbol" in decision.rationale, decision.rationale


def test_the_marker_is_a_single_shared_constant():
    """So a reader counting these decisions has one string to match."""
    assert NO_STRATEGY_MARKER == "no_strategy"
    assert " " not in NO_STRATEGY_MARKER, (
        "a marker with a space inside it is a marker that matches by accident"
    )


def test_an_empty_library_does_not_ask_the_model():
    """Nothing is covered because nothing exists - a configuration failure, not a gap.

    Kept distinct from an uncovered symbol: the plan authorises asking about a symbol no
    strategy covers, not about a system that holds no strategies at all.
    """
    llm = _LLM(_decision("HOLD"))
    decision = _engine(llm, library_empty=True)._decide_uncovered(_Snapshot(), {})

    assert llm.calls == [], "an empty library is not a question for the model"
    assert decision.decision_source == "policy_engine"
    assert "library is empty" in decision.rationale, decision.rationale


@pytest.mark.parametrize("action", ["BUY", "SELL", "SHORT", "COVER"])
def test_every_departure_needs_a_reason_not_only_the_dangerous_ones(action):
    """HOLD is the baseline; any departure is a departure.

    Not only the exposure-adding ones. SELL and COVER reduce, but a reduction an
    uncovered symbol did not justify is still an unexplained trade, and the covered path
    requires a reason for every disagreement with its rule.
    """
    llm = _LLM(_decision(action, reason=None))
    decision = _engine(llm)._decide_uncovered(_Snapshot(), {})

    assert decision.action == "HOLD", f"{action} without a reason must not be taken"


# --- the quote guard: do not pay for a decision that will be refused -------


def _aged_snapshot(*, quote_age_seconds: int):
    from datetime import datetime, timedelta, timezone

    class _Aged:
        symbol = "XLB"

        def __init__(self):
            self.timestamp = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)
            self.quote_time = self.timestamp - timedelta(seconds=quote_age_seconds)

    return _Aged()


def test_a_stale_quote_is_not_asked_of_the_model():
    """Measured 2026-10-15: 48 cycles in one session asked about a price Guardian refused.

    The call bought nothing and cost about forty seconds of the round.
    """
    llm = _LLM(_decision("HOLD"))
    engine = _engine(llm)
    engine.stale_after_seconds = 900

    decision = engine._decide_uncovered(_aged_snapshot(quote_age_seconds=3600), {})

    assert llm.calls == [], "a price already too old for Guardian must not cost a model call"
    assert decision.decision_source == "policy_engine", (
        "nothing was decided by the model, so nothing may be attributed to it"
    )
    assert "already stale" in decision.rationale, decision.rationale


def test_a_fresh_quote_is_still_asked():
    """And the guard must not silence the path it was added to protect."""
    llm = _LLM(_decision("HOLD"))
    engine = _engine(llm)
    engine.stale_after_seconds = 900

    engine._decide_uncovered(_aged_snapshot(quote_age_seconds=30), {})

    assert len(llm.calls) == 1


def test_the_boundary_is_guardians_own_ceiling():
    """Not a second number: this refuses exactly what Guardian would refuse."""
    llm = _LLM(_decision("HOLD"))
    engine = _engine(llm)
    engine.stale_after_seconds = 900

    engine._decide_uncovered(_aged_snapshot(quote_age_seconds=900), {})
    assert len(llm.calls) == 1, "exactly at the ceiling is not stale"

    engine._decide_uncovered(_aged_snapshot(quote_age_seconds=901), {})
    assert len(llm.calls) == 1, "one second past it is"


def test_without_a_ceiling_the_path_is_unchanged():
    """None leaves the uncovered path exactly as it was before the guard existed."""
    llm = _LLM(_decision("HOLD"))
    engine = _engine(llm)
    assert engine.stale_after_seconds is None

    engine._decide_uncovered(_aged_snapshot(quote_age_seconds=86400), {})

    assert len(llm.calls) == 1, "with no ceiling there is nothing to compare against"


def test_a_snapshot_without_a_quote_time_is_not_stale():
    """Absent is not stale, which is the same rule Guardian applies."""
    llm = _LLM(_decision("HOLD"))
    engine = _engine(llm)
    engine.stale_after_seconds = 900

    engine._decide_uncovered(_Snapshot(), {})

    assert len(llm.calls) == 1


def test_the_engine_takes_guardians_ceiling_from_the_caller():
    """One source for the number. A second would drift from what Guardian enforces."""
    import inspect

    from autopoiesis import cli

    assert "stale_after_seconds" in inspect.getsource(HybridDecisionEngine.__init__)
    assert "stale_after_seconds=config.stale_after_seconds" in inspect.getsource(cli), (
        "the CLI must pass Guardian's own ceiling, not a second number"
    )
