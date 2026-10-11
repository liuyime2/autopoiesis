#!/usr/bin/env python3
"""Measure read-only clock/account latency against the configured Alpaca paper host.

Each iteration makes exactly one clock read and one account read. The tool never
prints broker response data, exception text, credentials, or account identifiers.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import math
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

# An iteration performs two broker operations. Keep manual sampling small so a
# mistaken large COUNT cannot hammer the paper account.
MAX_ITERATIONS = 50
SUPPORTED_SDK_VERSION = "3.2.0"


@dataclass(frozen=True)
class BrokerConfig:
    mode: str
    alpaca_base_url: str
    alpaca_api_key: str | None
    alpaca_secret_key: str | None

    @property
    def is_paper_endpoint(self) -> bool:
        from autopoiesis.config import is_paper_endpoint

        try:
            parsed = urlsplit(self.alpaca_base_url)
            return (
                parsed.scheme == "https"
                and is_paper_endpoint(self.alpaca_base_url)
                and parsed.username is None
                and parsed.password is None
                and parsed.port is None
                and parsed.path in ("", "/")
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            return False

    @property
    def endpoint_label(self) -> str:
        """Expose only the validated host; URL userinfo and other fields never print."""
        return f"https://{urlsplit(self.alpaca_base_url).hostname}"


def build_client(
    rest_constructor: Callable[..., object], config: BrokerConfig, *, sdk_version: str
) -> object:
    """Create a version-audited paper client with SDK retries disabled."""
    if sdk_version != SUPPORTED_SDK_VERSION:
        raise ValueError(f"sampler only audited for alpaca-trade-api {SUPPORTED_SDK_VERSION}")
    client = rest_constructor(
        key_id=config.alpaca_api_key,
        secret_key=config.alpaca_secret_key,
        base_url=config.alpaca_base_url,
        api_version="v2",
    )
    # The supported SDK has no public retry constructor option. Disable its 429/504
    # retry loop so one sampled operation corresponds to one HTTP attempt.
    if not hasattr(client, "_retry"):
        raise RuntimeError("audited Alpaca SDK retry control is unavailable")
    client._retry = 0
    return client
def _percentile(values: list[float], percentile: float) -> float | None:
    """Return the nearest-rank percentile (rank = ceil(p * n))."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _summary(values: list[float]) -> dict[str, float | None]:
    return {
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def run_sample(
    config: BrokerConfig,
    client_factory: Callable[[BrokerConfig], object],
    *,
    count: int,
    clock: Callable[[], float] = time.perf_counter,
    sdk_version: str,
) -> dict[str, object]:
    """Issue a bounded number of clock/account operations, retaining timings only."""
    if config.mode != "paper" or not config.is_paper_endpoint:
        raise ValueError("refusing to sample a non-paper broker configuration")
    if not config.alpaca_api_key or not config.alpaca_secret_key:
        raise ValueError("paper broker credentials are missing")
    if isinstance(count, bool) or not 1 <= count <= MAX_ITERATIONS:
        raise ValueError(f"COUNT must be between 1 and {MAX_ITERATIONS}")

    client = client_factory(config)
    durations: dict[str, list[float]] = {"clock": [], "account": []}
    errors: dict[str, list[str]] = {"clock": [], "account": []}
    for _ in range(count):
        for operation in ("clock", "account"):
            started = clock()
            try:
                getattr(client, "get_clock" if operation == "clock" else "get_account")()
            except Exception as exc:  # Broker exception messages may contain sensitive data.
                errors[operation].append(type(exc).__name__)
            finally:
                durations[operation].append(max(0.0, (clock() - started) * 1000.0))

    all_durations = durations["clock"] + durations["account"]
    total_failures = len(errors["clock"]) + len(errors["account"])
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "endpoint": config.endpoint_label,
        "sdk_version": sdk_version,
        "requested_iterations": count,
        "operation_count": len(all_durations),
        "successes": len(all_durations) - total_failures,
        "failures": total_failures,
        "latency_ms": _summary(all_durations),
        "operations": {
            name: {
                "operation_count": len(durations[name]),
                "successes": len(durations[name]) - len(errors[name]),
                "failures": len(errors[name]),
                "error_types": sorted(set(errors[name])),
                "latency_ms": _summary(durations[name]),
            }
            for name in ("clock", "account")
        },
    }


def print_report(report: dict[str, object]) -> None:
    """Print summary-only values; no response data or exception messages are retained."""
    print(f"timestamp_utc: {report['timestamp_utc']}")
    print(f"endpoint: {report['endpoint']}")
    print(f"alpaca_trade_api: {report['sdk_version']}")
    print(f"iterations: {report['requested_iterations']}")
    print(
        f"operations: {report['operation_count']} successes: {report['successes']} "
        f"failures: {report['failures']}"
    )
    _print_latency("all", report["latency_ms"])
    operations = report["operations"]
    assert isinstance(operations, dict)
    for name in ("clock", "account"):
        detail = operations[name]
        print(
            f"{name}: operations={detail['operation_count']} successes={detail['successes']} "
            f"failures={detail['failures']} error_types={','.join(detail['error_types']) or 'none'}"
        )
        _print_latency(name, detail["latency_ms"])


def _print_latency(label: str, values: object) -> None:
    assert isinstance(values, dict)
    rendered = " ".join(
        f"{name}={values[name]:.3f}ms" if values[name] is not None else f"{name}=n/a"
        for name in ("p50", "p95", "max")
    )
    print(f"latency_ms {label}: {rendered}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, required=True, help=f"iterations (1-{MAX_ITERATIONS})")
    args = parser.parse_args(argv)
    try:
        from autopoiesis.config import AgentConfig

        configured = AgentConfig.from_env()
        config = BrokerConfig(
            configured.mode,
            configured.alpaca_base_url,
            configured.alpaca_api_key,
            configured.alpaca_secret_key,
        )
        import alpaca_trade_api

        sdk_version = importlib.metadata.version("alpaca-trade-api")
        report = run_sample(
            config,
            lambda cfg: build_client(alpaca_trade_api.REST, cfg, sdk_version=sdk_version),
            count=args.count,
            sdk_version=sdk_version,
        )
        print_report(report)
        return 0 if report["failures"] == 0 else 1
    except Exception as exc:
        # Error text may contain URLs, credentials, or response bodies.
        print(f"broker latency sample failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
