from __future__ import annotations

import math
import os
from collections.abc import Mapping
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
    #: Whether the agent manages every position in the account, not only the shares it
    #: bought itself. Off by default: a SELL is then bounded by the agent's own confirmed
    #: fills, so an account holder's pre-existing shares are never sold. Turning it on is the
    #: account holder's decision, made in the env file, and every other Guardian rule still
    #: applies - allowlist, position and exposure caps, daily loss, trade count, paper only.
    manage_account: bool = False
    #: Which symbols the agent may trade, a decision of the account holder made in the env file.
    #: `allowlist` (default): only `AUTOPOIESIS_ALLOWLIST`. `account`: that list plus every symbol
    #: the account holds, so nothing in the account is out of reach. `tradable`: that, plus any
    #: active US equity on a major exchange priced at least `min_price`. Under `account` and
    #: `tradable` the exposure cap measures the whole account and a SELL is bounded by the
    #: account's position. Everything else the Guardian checks - paper only, market open, a fresh
    #: snapshot, position and exposure caps, daily loss, trades per day, confidence - still applies.
    universe: str = "allowlist"
    min_price: float = 5.0
    #: Extra symbols looked at each round beyond the configured and held ones, chosen from the
    #: broker's most-active list after quality filters. Zero (default) looks at none.
    attention_slots: int = 0
    #: The core volatility-targeted sleeve (see sleeve.py). Off unless both are set: the account
    #: holder names the symbol and a pool of at most `sleeve.SLEEVE_POOL_CAP` in the env file.
    #: Strategy kinds admission refuses. Default TREND_FOLLOW: its anchor is a price the model picks
    #: by hand, 36 of the 37 strategies created since 2026-10-15 were this kind and 20 of them were retired, and the signal ledger holds
    #: no price-level signal. An empty list (`AUTOPOIESIS_REFUSED_KINDS=none`) admits every kind.
    refused_kinds: tuple[str, ...] = ("TREND_FOLLOW",)
    sleeve_symbol: str = ""
    sleeve_pool: float = 0.0
    sleeve_target_vol_pct: float = 10.0
    max_daily_cycles: int = 288
    #: How long one trading decision may take before the rule decides instead. The model
    #: thinks before it answers - 30-50s on SPY, 122s once on a symbol it had not seen - so
    #: the old fixed 120s turned a slow answer into a silent fallback to the rule.
    llm_timeout_seconds: int = 240
    heartbeat_path: Path = Path("runtime/autopoiesis/heartbeat.json")
    pidfile_path: Path = Path("runtime/autopoiesis/daemon.pid")
    strategy_dir: Path = Path("runtime/autopoiesis/strategies")
    knowledge_dir: Path = Path("runtime/autopoiesis/knowledge")
    reflection_window: int = 50
    curriculum_enabled: bool = False
    reflect_every: int = 10
    curriculum_every: int = 50
    maintenance_interval_seconds: int = 900
    evidence_interval_seconds: int = 3600
    reflection_interval_seconds: int = 1800
    curriculum_interval_seconds: int = 3600
    profit_target_return_pct: float = 0.10

    @property
    def sleeve_enabled(self) -> bool:
        return bool(self.sleeve_symbol) and self.sleeve_pool > 0

    @property
    def whole_account(self) -> bool:
        """The agent manages every position in the account: asked for directly, or implied by a
        universe wider than the allowlist (a position it may trade must be one it may sell)."""
        return self.manage_account or self.universe != "allowlist"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AgentConfig:
        """Read the operator's environment. `environ` defaults to the process environment."""
        allowlist = _split_symbols(_env_required("AUTOPOIESIS_ALLOWLIST", "SPY,QQQ,AAPL,MSFT,NVDA", environ))
        symbols = _split_symbols(_env_required("AUTOPOIESIS_SYMBOLS", "SPY", environ))
        source = os.environ if environ is None else environ
        mode = _env_required("AUTOPOIESIS_MODE", "paper", environ).strip().lower()
        if mode == "live":
            raise ValueError("live mode is not allowed in phase 1")

        max_position_value = _positive_float("AUTOPOIESIS_MAX_POSITION_VALUE", 5000.0, environ)

        return cls(
            mode=mode,
            symbols=symbols,
            allowlist=frozenset(allowlist),
            alpaca_api_key=source.get("ALPACA_API_KEY") or source.get("APCA_API_KEY_ID"),
            alpaca_secret_key=source.get("ALPACA_SECRET_KEY") or source.get("APCA_API_SECRET_KEY"),
            alpaca_base_url=source.get("ALPACA_BASE_URL")
            or source.get("APCA_API_BASE_URL")
            or "https://paper-api.alpaca.markets",
            ollama_base_url=source.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
            model=_env_required("AUTOPOIESIS_MODEL", "qwen3.8:27b", environ),
            gpu_devices=_env_required("AUTOPOIESIS_GPU_DEVICES", "0", environ),
            journal_path=Path(_env_required("AUTOPOIESIS_JOURNAL", "runtime/autopoiesis/journal.jsonl", environ)),
            max_position_value=max_position_value,
            max_daily_loss=_positive_float("AUTOPOIESIS_MAX_DAILY_LOSS", 500.0, environ),
            max_total_exposure=_positive_float("AUTOPOIESIS_MAX_TOTAL_EXPOSURE", max_position_value * 4.0, environ),
            stale_after_seconds=_positive_int("AUTOPOIESIS_STALE_AFTER_SECONDS", 900, environ),
            daemon_interval_seconds=_positive_int("AUTOPOIESIS_DAEMON_INTERVAL_SECONDS", 300, environ),
            max_trades_per_day=_positive_int("AUTOPOIESIS_MAX_TRADES_PER_DAY", 10, environ),
            min_confidence=_bounded_float("AUTOPOIESIS_MIN_CONFIDENCE", 0.5, 0.0, 1.0, environ),
            max_account_value=_optional_float("AUTOPOIESIS_MAX_ACCOUNT_VALUE", environ),
            assumed_round_trip_cost_pct=_positive_float(
                "AUTOPOIESIS_ASSUMED_ROUND_TRIP_COST_PCT", 0.05, environ),
            counterfactual_horizon_hours=_positive_float(
                "AUTOPOIESIS_COUNTERFACTUAL_HORIZON_HOURS", 24.0, environ),
            shadow=_flag("AUTOPOIESIS_SHADOW", False, environ),
            shorts=_choice("AUTOPOIESIS_SHORTS", ("off", "shadow", "paper"), "off", environ),
            manage_account=_choice("AUTOPOIESIS_MANAGE_ACCOUNT", ("false", "true"), "false", environ) == "true",
            universe=_choice("AUTOPOIESIS_UNIVERSE", ("allowlist", "account", "tradable"), "allowlist", environ),
            min_price=_bounded_float("AUTOPOIESIS_MIN_PRICE", 5.0, 0.0, 10_000.0, environ),
            attention_slots=_bounded_int("AUTOPOIESIS_ATTENTION_SLOTS", 0, 0, 25, environ),
            refused_kinds=_refused_kinds(source.get("AUTOPOIESIS_REFUSED_KINDS")),
            sleeve_symbol=source.get("AUTOPOIESIS_SLEEVE_SYMBOL", "").strip().upper(),
            sleeve_pool=_bounded_float("AUTOPOIESIS_SLEEVE_POOL", 0.0, 0.0, 20_000.0, environ),
            sleeve_target_vol_pct=_bounded_float("AUTOPOIESIS_SLEEVE_TARGET_VOL", 10.0, 1.0, 30.0, environ),
            max_daily_cycles=_positive_int("AUTOPOIESIS_MAX_DAILY_CYCLES", 288, environ),
            llm_timeout_seconds=_positive_int("AUTOPOIESIS_LLM_TIMEOUT_SECONDS", 240, environ),
            heartbeat_path=Path(_env_required("AUTOPOIESIS_HEARTBEAT", "runtime/autopoiesis/heartbeat.json", environ)),
            pidfile_path=Path(_env_required("AUTOPOIESIS_PIDFILE", "runtime/autopoiesis/daemon.pid", environ)),
            strategy_dir=Path(_env_required("AUTOPOIESIS_STRATEGY_DIR", "runtime/autopoiesis/strategies", environ)),
            knowledge_dir=Path(_env_required("AUTOPOIESIS_KNOWLEDGE_DIR", "runtime/autopoiesis/knowledge", environ)),
            reflection_window=_positive_int("AUTOPOIESIS_REFLECTION_WINDOW", 50, environ),
            curriculum_enabled=_bool_env("AUTOPOIESIS_CURRICULUM_ENABLED", False, environ),
            reflect_every=_positive_int("AUTOPOIESIS_REFLECT_EVERY", 10, environ),
            curriculum_every=_positive_int("AUTOPOIESIS_CURRICULUM_EVERY", 50, environ),
            maintenance_interval_seconds=_positive_int("AUTOPOIESIS_MAINTENANCE_INTERVAL_SECONDS", 900, environ),
            evidence_interval_seconds=_positive_int("AUTOPOIESIS_EVIDENCE_INTERVAL_SECONDS", 3600, environ),
            reflection_interval_seconds=_positive_int("AUTOPOIESIS_REFLECTION_INTERVAL_SECONDS", 1800, environ),
            curriculum_interval_seconds=_positive_int("AUTOPOIESIS_CURRICULUM_INTERVAL_SECONDS", 3600, environ),
            profit_target_return_pct=_positive_float("AUTOPOIESIS_PROFIT_TARGET_RETURN_PCT", 0.10, environ),
        )

    @classmethod
    def documented_defaults(cls) -> AgentConfig:
        """The configuration `from_env` falls back to when the environment says nothing.

        `tools/benchmark.py` reaches for this when the operator's environment cannot be read -
        "a missing key file must not hide the benchmark". It used to call `AgentConfig()` there,
        which raises `TypeError`: eleven fields have no default, so the benchmark crashed on the
        one path written to survive a missing configuration. mypy reported it for five months and
        nothing caught it at runtime, because this machine has a readable config.

        The defaults live in one place - as the second argument of every `_env_required` above -
        so this reads them from an empty mapping rather than restating them. A second list of
        literals is a second place for them to drift, and the failure is silent: a default that
        disagrees between the two copies changes what the benchmark measures.
        """
        return cls.from_env({})

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


#: Every setting is read under one prefix. There is no second spelling, no fallback and no
#: migration window: a project that was renamed reads the name it was renamed to, and an
#: environment variable under any other name is simply one nothing reads - which is what
#: `configs/paper.env.example` and the README both say, and what `doctor` reports if an
#: operator sets one by mistake.
#:
#: One prefix, one spelling. There was a second one once that was read as a fallback for as
#: long as the project lived, so the rename never completed; it was removed rather than deprecated,
#: and a variable under a name nothing reads is now simply one nothing reads.
ENV_PREFIX = "AUTOPOIESIS_"


def env(
    name: str,
    default: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """One setting, under one prefix.

    `environ` defaults to the process environment. It is a parameter so the defaults below can
    be read from an empty mapping - `documented_defaults()` needs exactly that, and without the
    seam the only way to get it is a second, drifting copy of every literal in `from_env`.
    """
    source = os.environ if environ is None else environ
    found = source.get(name)
    return found if found is not None else default


def _refused_kinds(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return ("TREND_FOLLOW",)
    return tuple(item.strip().upper() for item in raw.split(",") if item.strip() and item.strip().lower() != "none")


def _split_symbols(raw: str) -> tuple[str, ...]:
    symbols = tuple(item.strip().upper() for item in raw.split(",") if item.strip())
    if not symbols:
        raise ValueError("at least one symbol is required")
    return symbols


def _env_required(
    name: str,
    default: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    """`env()` narrowed to a value. The default argument is what makes it non-optional, so the
    callers below can read a float or an int without a None check they would otherwise have to
    write and forget."""
    return env(name, default, environ) or ""


def _positive_int(
    name: str,
    default: int,
    environ: Mapping[str, str] | None = None,
) -> int:
    value = int(_env_required(name, str(default), environ))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _choice(
    name: str,
    allowed: tuple[str, ...],
    default: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    """One of a fixed set of values; anything else is a configuration error, not a default."""
    raw = env(name, None, environ)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}; got {raw!r}")
    return value


def _flag(
    name: str,
    default: bool,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """A boolean setting.

    Only an exact `1` or `true` enables it. Truthiness is the wrong rule for a switch
    that decides whether real orders reach a broker: `AUTOPOIESIS_SHADOW=0` must not
    read as on, and a typo like `=flase` must not silently mean "on" either.
    """
    raw = env(name, None, environ)
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


def _bounded_int(
    name: str,
    default: int,
    low: int,
    high: int,
    environ: Mapping[str, str] | None = None,
) -> int:
    value = int(_env_required(name, str(default), environ))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _finite(name: str, value: float) -> float:
    """A risk setting must be a real number. NaN and both infinities are rejected.

    Why this exists: `NaN <= 0` is False, so a NaN limit sailed through the positivity check and
    reached the Guardian, where every comparison against it - `value > max_position_value`,
    `exposure > max_total_exposure` - is also False. Measured with an offline synthetic fixture, no
    broker and no order: a BUY of 10,000 that the configured 5,000 limit correctly refuses is
    **approved** when the limit is NaN. The Guardian is not bypassed, which is what makes it
    dangerous: the one component that "cannot be bypassed" silently stops comparing, and every
    report stays green.
    """
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number; got {value!r}")
    return value


def _positive_float(
    name: str,
    default: float,
    environ: Mapping[str, str] | None = None,
) -> float:
    value = _finite(name, float(_env_required(name, str(default), environ)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _optional_float(name: str, environ: Mapping[str, str] | None = None) -> float:
    """An optional cap: absent, or any positive value, meaning unset."""
    raw = env(name, None, environ)
    if raw is None or not raw.strip():
        return 0.0
    value = _finite(name, float(raw))
    if value <= 0:
        raise ValueError(f"{name} must be positive when set")
    return value


def _bounded_float(
    name: str,
    default: float,
    low: float,
    high: float,
    environ: Mapping[str, str] | None = None,
) -> float:
    value = float(_env_required(name, str(default), environ))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _bool_env(
    name: str,
    default: bool,
    environ: Mapping[str, str] | None = None,
) -> bool:
    raw = env(name, None, environ)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")
