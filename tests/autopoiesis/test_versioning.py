"""The version digest must move with the config and must never carry a credential."""
from __future__ import annotations

from dataclasses import replace

from autopoiesis.config import AgentConfig
from autopoiesis.versioning import version_summary


def _config(monkeypatch) -> AgentConfig:
    monkeypatch.setenv("ALPACA_API_KEY", "key-never-in-digest")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret-never-in-digest")
    return AgentConfig.from_env()


def test_a_config_change_changes_the_digest(monkeypatch):
    """Measured 2026-11-07: the config digest was the hash of `{}` whatever the env said."""
    config = _config(monkeypatch)
    before = version_summary(config)
    after = version_summary(replace(config, sleeve_pool=config.sleeve_pool + 1.0))
    assert before["config_digest"] != after["config_digest"]
    assert before["digest"] != after["digest"]
    assert before["model"] == config.model


def test_credentials_are_not_part_of_the_summary(monkeypatch):
    from autopoiesis.versioning import _config_summary

    summary = _config_summary(_config(monkeypatch))
    assert summary, "the summary must not be empty"
    assert not any("key-never" in str(v) or "secret-never" in str(v) for v in summary.values())
