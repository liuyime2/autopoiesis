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
    def whole_account(self) -> bool:
        """The agent manages every position in the account: asked for directly, or implied by a
        universe wider than the allowlist (a position it may trade must be one it may sell)."""
        return self.manage_account or self.universe != "allowlist"

    @classmethod
    def from_env(cls) -> AgentConfig:
        allowlist = _split_symbols(_env_required("AUTOPOIESIS_ALLOWLIST", "SPY,QQQ,AAPL,MSFT,NVDA"))
        symbols = _split_symbols(_env_required("AUTOPOIESIS_SYMBOLS", "SPY"))
        mode = _env_required("AUTOPOIESIS_MODE", "paper").strip().lower()
        if mode == "live":
            raise ValueError("live mode is not allowed in phase 1")

        max_position_value = _positive_float("AUTOPOIESIS_MAX_POSITION_VALUE", 5000.0)

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
            model=_env_required("AUTOPOIESIS_MODEL", "qwen3.8:27b"),
            gpu_devices=_env_required("AUTOPOIESIS_GPU_DEVICES", "0"),
            journal_path=Path(_env_required("AUTOPOIESIS_JOURNAL", "runtime/autopoiesis/journal.jsonl")),
            max_position_value=max_position_value,
            max_daily_loss=_positive_float("AUTOPOIESIS_MAX_DAILY_LOSS", 500.0),
            max_total_exposure=_positive_float("AUTOPOIESIS_MAX_TOTAL_EXPOSURE", max_position_value * 4.0),
            stale_after_seconds=_positive_int("AUTOPOIESIS_STALE_AFTER_SECONDS", 900),
            daemon_interval_seconds=_positive_int("AUTOPOIESIS_DAEMON_INTERVAL_SECONDS", 300),
            max_trades_per_day=_positive_int("AUTOPOIESIS_MAX_TRADES_PER_DAY", 10),
            min_confidence=_bounded_float("AUTOPOIESIS_MIN_CONFIDENCE", 0.5, 0.0, 1.0),
            max_account_value=_optional_float("AUTOPOIESIS_MAX_ACCOUNT_VALUE"),
            assumed_round_trip_cost_pct=_positive_float(
                "AUTOPOIESIS_ASSUMED_ROUND_TRIP_COST_PCT", 0.05),
            counterfactual_horizon_hours=_positive_float(
                "AUTOPOIESIS_COUNTERFACTUAL_HORIZON_HOURS", 24.0),
            shadow=_flag("AUTOPOIESIS_SHADOW", False),
            shorts=_choice("AUTOPOIESIS_SHORTS", ("off", "shadow", "paper"), "off"),
            manage_account=_choice("AUTOPOIESIS_MANAGE_ACCOUNT", ("false", "true"), "false") == "true",
            universe=_choice("AUTOPOIESIS_UNIVERSE", ("allowlist", "account", "tradable"), "allowlist"),
            min_price=_bounded_float("AUTOPOIESIS_MIN_PRICE", 5.0, 0.0, 10_000.0),
            attention_slots=_bounded_int("AUTOPOIESIS_ATTENTION_SLOTS", 0, 0, 25),
            max_daily_cycles=_positive_int("AUTOPOIESIS_MAX_DAILY_CYCLES", 288),
            llm_timeout_seconds=_positive_int("AUTOPOIESIS_LLM_TIMEOUT_SECONDS", 240),
            heartbeat_path=Path(_env_required("AUTOPOIESIS_HEARTBEAT", "runtime/autopoiesis/heartbeat.json")),
            pidfile_path=Path(_env_required("AUTOPOIESIS_PIDFILE", "runtime/autopoiesis/daemon.pid")),
            strategy_dir=Path(_env_required("AUTOPOIESIS_STRATEGY_DIR", "runtime/autopoiesis/strategies")),
            knowledge_dir=Path(_env_required("AUTOPOIESIS_KNOWLEDGE_DIR", "runtime/autopoiesis/knowledge")),
            reflection_window=_positive_int("AUTOPOIESIS_REFLECTION_WINDOW", 50),
            curriculum_enabled=_bool_env("AUTOPOIESIS_CURRICULUM_ENABLED", False),
            reflect_every=_positive_int("AUTOPOIESIS_REFLECT_EVERY", 10),
            curriculum_every=_positive_int("AUTOPOIESIS_CURRICULUM_EVERY", 50),
            maintenance_interval_seconds=_positive_int("AUTOPOIESIS_MAINTENANCE_INTERVAL_SECONDS", 900),
            evidence_interval_seconds=_positive_int("AUTOPOIESIS_EVIDENCE_INTERVAL_SECONDS", 3600),
            reflection_interval_seconds=_positive_int("AUTOPOIESIS_REFLECTION_INTERVAL_SECONDS", 1800),
            curriculum_interval_seconds=_positive_int("AUTOPOIESIS_CURRICULUM_INTERVAL_SECONDS", 3600),
            profit_target_return_pct=_positive_float("AUTOPOIESIS_PROFIT_TARGET_RETURN_PCT", 0.10),
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


#: The prefix this project was renamed away from, and why it is still *detected*.
#:
#: A prefix rename is not a find-and-replace. `MIN_AGENT_MAX_POSITION_VALUE` and
#: `MIN_AGENT_ALLOWLIST` live in an operator's env file outside the repository, so renaming the
#: constant outright would have made every one of them silently unread - and the defaults
#: underneath are *narrower* than what this account is authorised to trade (SPY alone, $5,000 a
#: position). The agent would have started, looked healthy, and been trading a smaller universe
#: under smaller limits than the account holder approved, with nothing in any log saying so.
#:
#: That is why the first version kept reading the old prefix forever. It was the wrong fix: a
#: fallback that is live forever means the rename never completes, and `doctor` reporting "14
#: settings still use the legacy prefix" on every run is a warning nobody reads the hundredth time.
#: The old prefix is now read only long enough to say a migration is unfinished, and
#: `assert_not_legacy_only` refuses to start on one - because a process that cannot read its
#: configuration and then runs on defaults is more dangerous than one that refuses to start, and
#: `AGENTS.md` 9 requires the environment to be explicit rather than implicit.
#:
#: The migration is one command, and this code never rewrites the operator's file:
#: `sed 's/^MIN_AGENT_/AUTOPOIESIS_/'` into the new path, then remove the old one. Both files
#: existing at once is the intended intermediate state - the new one wins, so it can be verified
#: before the old one is deleted.
LEGACY_ENV_PREFIX = "MIN_AGENT_"
ENV_PREFIX = "AUTOPOIESIS_"


def env(name: str, default: str | None = None) -> str | None:
    """One configuration value. `AUTOPOIESIS_*` first, and only for the migration window, then
    `MIN_AGENT_*`.

    The current prefix wins when both are set, so an operator migrating one variable at a time is
    not half-migrated in a way that depends on file order. `assert_not_legacy_only` is what stops
    the window becoming permanent.
    """
    if name.startswith(LEGACY_ENV_PREFIX):
        legacy, current = name, ENV_PREFIX + name[len(LEGACY_ENV_PREFIX) :]
    else:
        current, legacy = name, LEGACY_ENV_PREFIX + name[len(ENV_PREFIX) :]
    found = os.getenv(current)
    if found is not None:
        return found
    return os.getenv(legacy, default)


def legacy_env_names_in_use() -> list[str]:
    """Every legacy-prefixed variable that is actually set, so the migration can be reported and
    refused rather than silently depended on."""
    out = []
    for key, value in os.environ.items():
        if key.startswith(LEGACY_ENV_PREFIX) and value.strip() and not key.startswith(ENV_PREFIX):
            out.append(key)
    return sorted(out)


def assert_not_legacy_only() -> None:
    """Refuse to run when the only configuration on offer uses the retired prefix.

    Not a warning. An agent that starts on defaults it was never given is the failure this whole
    mechanism exists to prevent: it would look healthy, pass the gate, and trade a narrower universe
    under tighter limits than the account holder approved, with nothing anywhere saying so.

    Two conditions are refused, and they are different:

    * **no** `AUTOPOIESIS_*` set but `MIN_AGENT_*` is - the operator has not migrated, and every
      limit is about to come from the defaults;
    * both set, for names the current prefix does not cover - a half-migration, where which
      variable wins depends on the order they were renamed, which is exactly the ambiguity a risk
      setting must not contain.
    """
    legacy = legacy_env_names_in_use()
    if not legacy:
        return
    current = [k for k in os.environ if k.startswith(ENV_PREFIX)]
    migrate = (
        f"migrate with:  mkdir -p \"${{XDG_CONFIG_HOME:-$HOME/.config}}/autopoiesis\" && \\\n"
        f"    sed 's/^{LEGACY_ENV_PREFIX}/{ENV_PREFIX}/' "
        f"\"${{XDG_CONFIG_HOME:-$HOME/.config}}/min-agent/env\" "
        f"> \"${{XDG_CONFIG_HOME:-$HOME/.config}}/autopoiesis/env\" "
        f"&& chmod 600 \"${{XDG_CONFIG_HOME:-$HOME/.config}}/autopoiesis/env\""
    )
    if not current:
        raise SystemExit(
            f"REFUSING TO START: only {len(legacy)} retired {LEGACY_ENV_PREFIX}* variable(s) are set "
            f"and no {ENV_PREFIX}* one is. Running anyway would use the built-in defaults, which are "
            f"narrower than what this account is authorised to trade (SPY alone, $5,000 a position, "
            f"10 trades a day) - so the agent would look healthy while quietly trading a different "
            f"configuration from the one you approved.\n  {migrate}\n  then re-run. The old file "
            f"can stay until you have verified the new one."
        )
    raise SystemExit(
        f"REFUSING TO START: {len(legacy)} retired {LEGACY_ENV_PREFIX}* variable(s) are still set "
        f"alongside {len(current)} {ENV_PREFIX}* one(s): "
        + ", ".join(legacy[:6]) + (f" and {len(legacy) - 6} more" if len(legacy) > 6 else "")
        + f".\n  A half-migrated configuration makes which limit wins depend on the order you "
          f"renamed them, which is not ambiguity a risk setting may contain.\n  {migrate}\n  "
          f"then re-run."
    )


def _split_symbols(raw: str) -> tuple[str, ...]:
    symbols = tuple(item.strip().upper() for item in raw.split(",") if item.strip())
    if not symbols:
        raise ValueError("at least one symbol is required")
    return symbols


def _env_required(name: str, default: str) -> str:
    """`env()` narrowed to a value. The default argument is what makes it non-optional, so the
    callers below can read a float or an int without a None check they would otherwise have to
    write and forget."""
    return env(name, default) or ""


def _positive_int(name: str, default: int) -> int:
    value = int(_env_required(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _choice(name: str, allowed: tuple[str, ...], default: str) -> str:
    """One of a fixed set of values; anything else is a configuration error, not a default."""
    raw = env(name)
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
    raw = env(name)
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


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    value = int(_env_required(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(_env_required(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _optional_float(name: str) -> float:
    """An optional cap: absent, or any positive value, meaning unset."""
    raw = env(name)
    if raw is None or not raw.strip():
        return 0.0
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive when set")
    return value


def _bounded_float(name: str, default: float, low: float, high: float) -> float:
    value = float(_env_required(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = env(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")
