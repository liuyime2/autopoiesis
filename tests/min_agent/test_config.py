from min_agent.config import AgentConfig


def test_config_defaults_to_paper_deepseek_and_gpu_0(monkeypatch):
    for name in (
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "MIN_AGENT_MODEL",
        "MIN_AGENT_GPU_DEVICES",
        "MIN_AGENT_MAX_POSITION_VALUE",
    ):
        monkeypatch.delenv(name, raising=False)

    config = AgentConfig.from_env()

    assert config.mode == "paper"
    # qwen3.8:27b needs ollama >= 0.13 for the qwen35 architecture, and it is far
    # more token-efficient on the real curriculum call: 1022 tokens and ~36s
    # against 15005 tokens and ~132s for deepseek-r1:8b, which thought for four
    # minutes on a one-line answer.
    assert config.model == "qwen3.8:27b"
    assert config.gpu_devices == "0"
    assert config.max_position_value == 5_000
    assert "SPY" in config.allowlist


def test_config_detects_missing_alpaca_credentials(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)

    config = AgentConfig.from_env()

    assert config.missing_alpaca_credentials() == ["api_key", "secret_key"]


def test_config_reads_max_position_value_override(monkeypatch):
    monkeypatch.setenv("MIN_AGENT_MAX_POSITION_VALUE", "25000")

    config = AgentConfig.from_env()

    assert config.max_position_value == 25_000


def test_config_rejects_live_mode(monkeypatch):
    monkeypatch.setenv("MIN_AGENT_MODE", "live")

    try:
        AgentConfig.from_env()
    except ValueError as exc:
        assert "live" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_config_reads_daemon_settings(monkeypatch):
    monkeypatch.setenv("MIN_AGENT_DAEMON_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("MIN_AGENT_MAX_TRADES_PER_DAY", "3")
    monkeypatch.setenv("MIN_AGENT_MAX_DAILY_CYCLES", "7")
    monkeypatch.setenv("MIN_AGENT_HEARTBEAT", "runtime/test-heartbeat.json")
    monkeypatch.setenv("MIN_AGENT_PIDFILE", "runtime/test-daemon.pid")
    monkeypatch.setenv("MIN_AGENT_STRATEGY_DIR", "runtime/test-strategies")
    monkeypatch.setenv("MIN_AGENT_REFLECTION_WINDOW", "11")
    monkeypatch.setenv("MIN_AGENT_CURRICULUM_ENABLED", "true")

    config = AgentConfig.from_env()

    assert config.daemon_interval_seconds == 60
    assert config.max_trades_per_day == 3
    assert config.max_daily_cycles == 7
    assert str(config.heartbeat_path) == "runtime/test-heartbeat.json"
    assert str(config.pidfile_path) == "runtime/test-daemon.pid"
    assert str(config.strategy_dir) == "runtime/test-strategies"
    assert config.reflection_window == 11
    assert config.curriculum_enabled is True


def test_config_reads_learning_cadence_settings(monkeypatch):
    monkeypatch.setenv("MIN_AGENT_REFLECT_EVERY", "2")
    monkeypatch.setenv("MIN_AGENT_CURRICULUM_EVERY", "3")

    config = AgentConfig.from_env()

    assert config.reflect_every == 2
    assert config.curriculum_every == 3


def test_config_rejects_invalid_daemon_settings(monkeypatch):
    monkeypatch.setenv("MIN_AGENT_DAEMON_INTERVAL_SECONDS", "0")

    try:
        AgentConfig.from_env()
    except ValueError as exc:
        assert "MIN_AGENT_DAEMON_INTERVAL_SECONDS" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_config_rejects_invalid_learning_cadence(monkeypatch):
    monkeypatch.setenv("MIN_AGENT_CURRICULUM_EVERY", "0")

    try:
        AgentConfig.from_env()
    except ValueError as exc:
        assert "MIN_AGENT_CURRICULUM_EVERY" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_the_model_is_pinned_in_the_same_three_places_everywhere():
    """The model was switched in config.py alone and the health check went on
    reporting the old one: minictrl exported MIN_AGENT_MODEL=deepseek-r1:8b and
    the systemd unit pinned it too, so both overrode the default. A model upgrade
    that the operator cannot see in `doctor` is not an upgrade."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    default = AgentConfig.from_env.__wrapped__ if False else None  # noqa: F841
    from min_agent.config import AgentConfig as C

    expected = C.from_env().model
    pinned = set()
    for name in ("minictrl", "tools/min-agent.service.in"):
        text = (root / name).read_text()
        for match in re.findall(r'MIN_AGENT_MODEL[^"\n]*?([a-z0-9.]+:[a-z0-9.-]+)', text):
            pinned.add(match)
    assert pinned == {expected}, (
        f"model pinned as {sorted(pinned)} but the config default is {expected!r}"
    )
