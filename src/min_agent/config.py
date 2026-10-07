from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

# Alpaca's paper hosts, exactly. Anything else is not the paper endpoint, whatever
# its URL happens to contain.
PAPER_ALPACA_HOSTS = frozenset({"paper-api.alpaca.markets", "api.paper.trading.alpaca.com"})


def is_paper_endpoint(url: str) -> bool:
    """Whether `url` resolves to Alpaca's paper host. The single definition of paper-only.

    AgentConfig.is_paper_endpoint() delegates here, and so does the executor - which is handed
    a URL string rather than a config, and sits at the last gate before `submit_order`. One
    function, so the two cannot drift apart.
    """
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return host.lower() in PAPER_ALPACA_HOSTS


@dataclass(frozen=True)
class AgentConfig:
    mode: str
    symbols: tuple[str, ...]
    allowlist: frozenset[str]
    alpaca_api_key: str | None
    alpaca_secret_key: str | None
    alpaca_base_url: str
    ollama_base_url: str
    model: str
    gpu_devices: str
    journal_path: Path
    max_position_value: float
    max_daily_loss: float
    max_total_exposure: float = 0.0
    stale_after_seconds: int = 900

    # Daemon settings
    daemon_interval_seconds: int = 300
    max_trades_per_day: int = 10
    min_confidence: float = 0.5
    max_account_value: float = 0.0
    # How long to wait before scoring a decision against the market. The paper
    # broker reports zero commission, so a gross-only figure would flatter the
    # system by what it would really have paid; this is that assumption.
    assumed_round_trip_cost_pct: float = 0.05
    counterfactual_horizon_hours: float = 24.0
    # Phase 5 shadow trading. A flag rather than a fourth `mode`, because
    # `mode` carries a hard safety meaning: anything but 'paper' raises at
    # load. Overloading it with 'paper but pretend' would blur the one line
    # that must stay sharp. With this on, the real loop runs and the final
    # broker call is replaced by a journalled intent.
    shadow: bool = False
    #: Short selling: `off` (default) refuses SHORT and COVER at the Guardian; `shadow` lets
    #: them through the Guardian and journals them as intents while long orders stay real;
    #: `paper` submits them. A rollout switch, so shorts can be watched before they trade.
    shorts: str = "off"
    max_daily_cycles: int = 288
    heartbeat_path: Path = Path("runtime/min_agent/heartbeat.json")
    pidfile_path: Path = Path("runtime/min_agent/daemon.pid")
    strategy_dir: Path = Path("runtime/min_agent/strategies")
    knowledge_dir: Path = Path("runtime/min_agent/knowledge")
    reflection_window: int = 50
    curriculum_enabled: bool = False
    reflect_every: int = 10
    curriculum_every: int = 50
    maintenance_interval_seconds: int = 900
    evidence_interval_seconds: int = 3600
    reflection_interval_seconds: int = 1800
    curriculum_interval_seconds: int = 3600
    profit_target_return_pct: float = 0.10

    @classmethod
    def from_env(cls) -> AgentConfig:
        allowlist = _split_symbols(os.getenv("MIN_AGENT_ALLOWLIST", "SPY,QQQ,AAPL,MSFT,NVDA"))
        symbols = _split_symbols(os.getenv("MIN_AGENT_SYMBOLS", "SPY"))
        mode = os.getenv("MIN_AGENT_MODE", "paper").strip().lower()
        if mode == "live":
            raise ValueError("live mode is not allowed in phase 1")

        max_position_value = _positive_float("MIN_AGENT_MAX_POSITION_VALUE", 5000.0)

        return cls(
            mode=mode,
            symbols=symbols,
            allowlist=frozenset(allowlist),
            alpaca_api_key=os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID"),
            alpaca_secret_key=os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY"),
            alpaca_base_url=os.getenv("ALPACA_BASE_URL")
            or os.getenv("APCA_API_BASE_URL")
            or "https://paper-api.alpaca.markets",
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
            model=os.getenv("MIN_AGENT_MODEL", "qwen3.8:27b"),
            gpu_devices=os.getenv("MIN_AGENT_GPU_DEVICES", "0"),
            journal_path=Path(os.getenv("MIN_AGENT_JOURNAL", "runtime/min_agent/journal.jsonl")),
            max_position_value=max_position_value,
            max_daily_loss=_positive_float("MIN_AGENT_MAX_DAILY_LOSS", 500.0),
            max_total_exposure=_positive_float("MIN_AGENT_MAX_TOTAL_EXPOSURE", max_position_value * 4.0),
            stale_after_seconds=_positive_int("MIN_AGENT_STALE_AFTER_SECONDS", 900),
            daemon_interval_seconds=_positive_int("MIN_AGENT_DAEMON_INTERVAL_SECONDS", 300),
            max_trades_per_day=_positive_int("MIN_AGENT_MAX_TRADES_PER_DAY", 10),
            min_confidence=_bounded_float("MIN_AGENT_MIN_CONFIDENCE", 0.5, 0.0, 1.0),
            max_account_value=_optional_float("MIN_AGENT_MAX_ACCOUNT_VALUE"),
            assumed_round_trip_cost_pct=_positive_float(
                "MIN_AGENT_ASSUMED_ROUND_TRIP_COST_PCT", 0.05),
            counterfactual_horizon_hours=_positive_float(
                "MIN_AGENT_COUNTERFACTUAL_HORIZON_HOURS", 24.0),
            shadow=_flag("MIN_AGENT_SHADOW", False),
            shorts=_choice("MIN_AGENT_SHORTS", ("off", "shadow", "paper"), "off"),
            max_daily_cycles=_positive_int("MIN_AGENT_MAX_DAILY_CYCLES", 288),
            heartbeat_path=Path(os.getenv("MIN_AGENT_HEARTBEAT", "runtime/min_agent/heartbeat.json")),
            pidfile_path=Path(os.getenv("MIN_AGENT_PIDFILE", "runtime/min_agent/daemon.pid")),
            strategy_dir=Path(os.getenv("MIN_AGENT_STRATEGY_DIR", "runtime/min_agent/strategies")),
            knowledge_dir=Path(os.getenv("MIN_AGENT_KNOWLEDGE_DIR", "runtime/min_agent/knowledge")),
            reflection_window=_positive_int("MIN_AGENT_REFLECTION_WINDOW", 50),
            curriculum_enabled=_bool_env("MIN_AGENT_CURRICULUM_ENABLED", False),
            reflect_every=_positive_int("MIN_AGENT_REFLECT_EVERY", 10),
            curriculum_every=_positive_int("MIN_AGENT_CURRICULUM_EVERY", 50),
            maintenance_interval_seconds=_positive_int("MIN_AGENT_MAINTENANCE_INTERVAL_SECONDS", 900),
            evidence_interval_seconds=_positive_int("MIN_AGENT_EVIDENCE_INTERVAL_SECONDS", 3600),
            reflection_interval_seconds=_positive_int("MIN_AGENT_REFLECTION_INTERVAL_SECONDS", 1800),
            curriculum_interval_seconds=_positive_int("MIN_AGENT_CURRICULUM_INTERVAL_SECONDS", 3600),
            profit_target_return_pct=_positive_float("MIN_AGENT_PROFIT_TARGET_RETURN_PCT", 0.10),
        )

    def is_paper_endpoint(self) -> bool:
        """Whether `alpaca_base_url` resolves to Alpaca's paper host.

        One definition, used by every caller. The test used to be `"paper" in url`, repeated
        in five places across three modules. A substring accepts any host with the letters
        p-a-p-e-r somewhere in it:

            https://evil.example/?next=paper              -> accepted
            https://paper-api.alpaca.markets.evil.example -> accepted
            https://live-api.paper-trading.example        -> accepted

        All three are rejected here, and only Alpaca's own paper hosts are accepted. The
        previous check was not *absent* safety - a live Alpaca URL still failed it - but it
        was a check on the wrong property, and one that could be satisfied by a URL nobody
        intended to use.
        """
        return is_paper_endpoint(self.alpaca_base_url)

    def missing_alpaca_credentials(self) -> list[str]:
        missing = []
        if not self.alpaca_api_key:
            missing.append("api_key")
        if not self.alpaca_secret_key:
            missing.append("secret_key")
        return missing


def _split_symbols(raw: str) -> tuple[str, ...]:
    symbols = tuple(item.strip().upper() for item in raw.split(",") if item.strip())
    if not symbols:
        raise ValueError("at least one symbol is required")
    return symbols


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _choice(name: str, allowed: tuple[str, ...], default: str) -> str:
    """One of a fixed set of values; anything else is a configuration error, not a default."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}; got {raw!r}")
    return value


def _flag(name: str, default: bool) -> bool:
    """A boolean setting.

    Only an exact `1` or `true` enables it. Truthiness is the wrong rule for a switch
    that decides whether real orders reach a broker: `MIN_AGENT_SHADOW=0` must not
    read as on, and a typo like `=flase` must not silently mean "on" either.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{name}={raw!r} is not a boolean; use 1/0, true/false, yes/no or on/off"
    )


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _optional_float(name: str) -> float:
    """An optional cap: absent, or any positive value, meaning unset."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return 0.0
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive when set")
    return value


def _bounded_float(name: str, default: float, low: float, high: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")
