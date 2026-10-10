"""`AUTOPOIESIS_MANAGE_ACCOUNT`: what a SELL may reach.

Off (the default), a SELL is bounded by the shares the agent itself bought, so an account
holder's pre-existing position is never sold. On, the account holder has handed the agent the
whole account, and a SELL is bounded by the account's position instead.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from autopoiesis.config import AgentConfig
from autopoiesis.loop import TradingLoop


def _loop(manage_account, agent_holding=0.0):
    loop = TradingLoop(
        data_gateway=None, decision_engine=None, guardian=None, executor=None,
        journal=None, mode="paper", manage_account=manage_account,
    )
    loop._agent_holding = lambda symbol: agent_holding  # type: ignore[method-assign]
    return loop


SNAPSHOT = SimpleNamespace(positions=[
    SimpleNamespace(symbol="TLT", quantity=226),
    SimpleNamespace(symbol="SPY", quantity=6),
])


def test_by_default_only_the_agent_s_own_shares_may_be_sold():
    assert _loop(False, agent_holding=0.0)._sellable_holding("TLT", SNAPSHOT) == 0.0


def test_managing_the_account_the_account_s_position_may_be_sold():
    loop = _loop(True, agent_holding=0.0)
    assert loop._sellable_holding("TLT", SNAPSHOT) == 226
    assert loop._sellable_holding("QQQ", SNAPSHOT) == 0


def test_the_setting_is_off_unless_set_and_refuses_anything_but_true_or_false(monkeypatch):
    monkeypatch.delenv("AUTOPOIESIS_MANAGE_ACCOUNT", raising=False)
    assert AgentConfig.from_env().manage_account is False
    monkeypatch.setenv("AUTOPOIESIS_MANAGE_ACCOUNT", "true")
    assert AgentConfig.from_env().manage_account is True
    monkeypatch.setenv("AUTOPOIESIS_MANAGE_ACCOUNT", "yes")
    with pytest.raises(ValueError):
        AgentConfig.from_env()


def test_a_long_round_shortens_the_sleep_and_a_closed_market_sleep_is_kept():
    from autopoiesis.daemon import AgentDaemon

    daemon = AgentDaemon.__new__(AgentDaemon)
    daemon.config = SimpleNamespace(daemon_interval_seconds=300)
    daemon._sleep_seconds = lambda: 300  # type: ignore[method-assign]
    assert daemon._round_sleep_seconds(100) == 200
    assert daemon._round_sleep_seconds(480) == 30
    daemon._sleep_seconds = lambda: 3600  # type: ignore[method-assign]
    assert daemon._round_sleep_seconds(480) == 3600


def test_the_decision_timeout_is_configurable_and_defaults_above_the_slowest_measured_answer(monkeypatch):
    monkeypatch.delenv("AUTOPOIESIS_LLM_TIMEOUT_SECONDS", raising=False)
    assert AgentConfig.from_env().llm_timeout_seconds == 240
    monkeypatch.setenv("AUTOPOIESIS_LLM_TIMEOUT_SECONDS", "90")
    assert AgentConfig.from_env().llm_timeout_seconds == 90
    monkeypatch.setenv("AUTOPOIESIS_LLM_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError):
        AgentConfig.from_env()
