"""A model may choose what the system looks at, but not what it is allowed to look at.

Measured 2026-10-14: the watchlist was the top `k` of the broker's most-actives and movers
after quality filters, chosen by a volume sort. The plan asks the model to choose from that
list and say why. A model choosing the universe is the one place in this system where a
plausible answer and a valid one are easy to confuse, so every constraint is asserted here.

All fixtures synthetic: no broker, no model, no network.

Usage: pytest tests/autopoiesis/test_attention_selection.py -q
"""

from __future__ import annotations

import json

import pytest

from autopoiesis.attention import (
    ATTENTION_SCHEMA,
    AttentionChoice,
    build_prompt,
    select_attention,
)

CANDIDATES = ("SPY", "TLT", "XLE", "XLF", "BIL", "XLB")


def _transport(payload):
    def call(prompt, schema):
        assert schema is ATTENTION_SCHEMA, "the schema must be the one this module declares"
        return payload
    return call


def _choices(*pairs):
    return {"choices": [{"symbol": s, "reason": r} for s, r in pairs]}


# --- the choice is constrained ------------------------------------------------


def test_a_symbol_that_was_not_offered_is_dropped():
    """A model that can widen the universe can undo the quality filter.

    The filter removes penny stocks, warrants, units and rights before the model ever sees
    the list. Honouring a name that was not offered would let a reply re-admit exactly what
    the filter just removed.
    """
    choice = select_attention(
        CANDIDATES, k=3,
        transport=_transport(_choices(("SPY", "liquid"), ("AAPL", "well known"))),
    )

    assert choice.symbols == ["SPY"], choice.symbols
    assert "AAPL" not in choice.symbols
    assert "dropped" in choice.note and "AAPL" in choice.note, choice.note


def test_the_choice_is_capped_at_k():
    choice = select_attention(
        CANDIDATES, k=2,
        transport=_transport(_choices(
            ("SPY", "a"), ("TLT", "b"), ("XLE", "c"), ("XLF", "d"),
        )),
    )

    assert len(choice.symbols) == 2, choice.symbols
    assert choice.symbols == ["SPY", "TLT"], "the model's own order decides the cap"


def test_a_choice_without_a_reason_is_dropped():
    """The plan asks for reasons so the choice can be evaluated later, not for decoration."""
    choice = select_attention(
        CANDIDATES, k=3,
        transport=_transport(_choices(("SPY", "liquid"), ("TLT", "   "))),
    )

    assert choice.symbols == ["SPY"], choice.symbols
    assert "TLT" not in choice.reasons


def test_a_reason_is_recorded_per_symbol():
    choice = select_attention(
        CANDIDATES, k=3,
        transport=_transport(_choices(("SPY", "the book is concentrated here"))),
    )

    assert choice.source == "model"
    assert choice.reasons == {"SPY": "the book is concentrated here"}


def test_a_repeated_symbol_is_counted_once():
    choice = select_attention(
        CANDIDATES, k=3,
        transport=_transport(_choices(("SPY", "first"), ("SPY", "second"))),
    )

    assert choice.symbols == ["SPY"], choice.symbols
    assert choice.reasons["SPY"] == "first", "the first reason is the one the model gave"


def test_case_does_not_admit_a_symbol_that_was_not_offered():
    """Upper-cased before the membership test, not after."""
    choice = select_attention(
        CANDIDATES, k=3,
        transport=_transport(_choices(("spy", "liquid"), ("aapl", "well known"))),
    )

    assert choice.symbols == ["SPY"], choice.symbols
    assert "AAPL" not in choice.symbols


# --- the mechanical list is the floor -----------------------------------------


def test_no_transport_leaves_the_mechanical_list():
    choice = select_attention(CANDIDATES, k=3, transport=None)

    assert choice.symbols == ["SPY", "TLT", "XLE"]
    assert choice.source == "mechanical"
    assert "no model asked" in choice.note


@pytest.mark.parametrize("error", [TimeoutError("slow"), ConnectionError("refused"), ValueError("bad")])
def test_a_failing_model_leaves_the_mechanical_list(error):
    def call(prompt, schema):
        raise error

    choice = select_attention(CANDIDATES, k=3, transport=call)

    assert choice.symbols == ["SPY", "TLT", "XLE"], "a failed model must not empty the watchlist"
    assert choice.source == "mechanical"
    assert type(error).__name__ in choice.note


@pytest.mark.parametrize("payload", [
    "no json at all",
    {"choices": "not a list"},
    {"choices": [{"symbol": "SPY"}]},              # no reason
    {"unexpected": []},
    {"choices": [{"symbol": 5, "reason": "numeric"}]},
])
def test_a_malformed_reply_leaves_the_mechanical_list(payload):
    choice = select_attention(CANDIDATES, k=3, transport=_transport(payload))

    assert choice.symbols == ["SPY", "TLT", "XLE"], choice.symbols
    assert choice.source == "mechanical"


def test_a_reply_of_only_inadmissible_choices_leaves_the_mechanical_list():
    """Choosing nothing is allowed; the mechanical list is what the caller had anyway."""
    choice = select_attention(
        CANDIDATES, k=3,
        transport=_transport(_choices(("AAPL", "not offered"), ("MSFT", "also not offered"))),
    )

    assert choice.symbols == ["SPY", "TLT", "XLE"], choice.symbols
    assert choice.source == "mechanical"
    assert "nothing admissible" in choice.note


def test_an_empty_candidate_list_is_answered_without_asking():
    asked = []

    def call(prompt, schema):
        asked.append(prompt)
        return _choices(("SPY", "x"))

    choice = select_attention((), k=3, transport=call)

    assert choice.symbols == []
    assert asked == [], "there is nothing to choose from, so nothing to ask"


def test_k_of_zero_is_answered_without_asking():
    asked = []

    def call(prompt, schema):
        asked.append(prompt)
        return _choices(("SPY", "x"))

    choice = select_attention(CANDIDATES, k=0, transport=call)

    assert choice.symbols == []
    assert asked == []


def test_a_short_candidate_list_is_not_padded():
    choice = select_attention(("SPY",), k=5, transport=None)

    assert choice.symbols == ["SPY"], "the mechanical list cannot invent candidates"


# --- shapes the transport may hand back ---------------------------------------


def test_a_json_string_reply_is_accepted():
    """The real transport returns text; the caller should not have to pre-parse it."""
    choice = select_attention(
        CANDIDATES, k=3,
        transport=_transport(json.dumps(_choices(("TLT", "duration exposure")))),
    )

    assert choice.symbols == ["TLT"]
    assert choice.source == "model"


def test_a_reply_wrapped_in_a_response_attribute_is_accepted():
    class _Reply:
        response = json.dumps(_choices(("XLE", "energy exposure")))

    choice = select_attention(CANDIDATES, k=3, transport=_transport(_Reply()))

    assert choice.symbols == ["XLE"]
    assert choice.source == "model"


def test_the_prompt_states_the_constraint_where_the_choice_is_made():
    """The constraint has to be in the question, not only in the validator."""
    prompt = build_prompt(CANDIDATES, 3, {"open_positions": 2})

    assert "at most 3" in prompt
    assert "only choose from this list" in prompt
    assert "SPY" in prompt and "XLB" in prompt
    assert "open_positions=2" in prompt


def test_a_model_choice_never_returns_a_symbol_outside_the_candidates():
    """The single property the whole module exists to hold, asserted over many replies."""
    for reply in (
        _choices(("AAPL", "x")), _choices(("SPY", "x"), ("AAPL", "y")),
        _choices(("spy", "x"), ("aapl", "y")),
    ):
        choice = select_attention(CANDIDATES, k=6, transport=_transport(reply))
        assert set(choice.symbols) <= set(CANDIDATES), choice.symbols


def test_the_choice_object_is_immutable():
    """It is recorded as the reason a round looked where it did; it must not be edited later."""
    choice = select_attention(CANDIDATES, k=1, transport=None)
    assert isinstance(choice, AttentionChoice)
    with pytest.raises(Exception):
        choice.symbols = ["AAPL"]  # type: ignore[misc]
