"""The audited, paper-only Alpaca client and finite Requests timeout policy."""
from __future__ import annotations

import importlib.metadata
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests
from urllib3.util import Timeout

from autopoiesis.config import is_paper_endpoint

SUPPORTED_SDK_VERSION = "3.2.0"
ORDER_PAGE_LIMIT = 500


def list_open_orders(client: Any) -> list[Any]:
    """Read one complete raw order list through the pinned SDK, or refuse it.

    A full page cannot prove completeness. Missing or malformed rows cannot prove
    an empty/conflict-free book either; every risk and audit consumer shares this boundary.
    """
    try:
        orders = client.list_orders(status="open", limit=ORDER_PAGE_LIMIT)
    except Exception as exc:
        raise RuntimeError(f"open order read failed ({type(exc).__name__})") from exc
    if not isinstance(orders, list):
        raise RuntimeError("open order list is unavailable or invalid")
    if len(orders) >= ORDER_PAGE_LIMIT:
        raise RuntimeError("OPEN_ORDERS_INCOMPLETE: broker returned a full page of open orders")
    for order in orders:
        for field in ("id", "symbol", "status"):
            value = getattr(order, field, None)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError(f"open order has invalid {field}")
        if str(getattr(order, "side", "")).lower() not in {"buy", "sell"}:
            raise RuntimeError("open order has invalid side")
        quantity = getattr(order, "qty", None)
        try:
            valid = quantity is not None and not isinstance(quantity, bool) and math.isfinite(float(quantity)) and float(quantity) > 0
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            raise RuntimeError("open order has invalid qty")
    return orders


@dataclass(frozen=True)
class RequestTimeoutPolicy:
    total: float
    connect: float
    read: float

    def new_timeout(self) -> Timeout:
        return Timeout(total=self.total, connect=self.connect, read=self.read)


REQUEST_TIMEOUT = RequestTimeoutPolicy(total=7, connect=2, read=5)


class TimedRequestsSession(requests.Session):
    """Supply a fresh timeout to every call made through the pinned SDK session."""

    def request(
        self,
        method: str,
        url: str | bytes,
        # Everything `Session.request` takes after `url` is positional-or-keyword, so a caller
        # may hand it `params`, `data`, `headers`... positionally. `**kwargs` alone accepts
        # those by name only, which is narrower than the supertype and exactly what mypy
        # flagged. `*args` is what makes this override accept every call the base does, which
        # is the whole point of overriding it rather than wrapping it.
        *args: Any,
        **kwargs: Any,
    ) -> requests.Response:
        # Never inherit Requests' no-timeout default and never reuse urllib3's stateful
        # Timeout instance. This bounds connect/read inactivity and urllib3's total budget,
        # but it is not a hard process deadline for slow-trickle responses or DNS stalls.
        kwargs["timeout"] = REQUEST_TIMEOUT.new_timeout()
        return super().request(method, url, *args, **kwargs)


def _sdk_version() -> str:
    return importlib.metadata.version("alpaca-trade-api")


def configure_alpaca_client(client: Any, *, sdk_version: str) -> Any:
    """Install the reviewed request/retry policy on the only supported SDK version."""
    if sdk_version != SUPPORTED_SDK_VERSION:
        raise RuntimeError(f"only audited alpaca-trade-api {SUPPORTED_SDK_VERSION} is supported")
    for attribute in ("_session", "_retry", "_retry_wait", "_retry_codes"):
        if not hasattr(client, attribute):
            raise RuntimeError(f"audited Alpaca SDK contract is missing {attribute}")
    client._session.close()
    client._session = TimedRequestsSession()
    client._retry = 0
    client._retry_wait = 0
    client._retry_codes = []
    return client


def create_alpaca_client(
    *,
    key_id: str,
    secret_key: str,
    base_url: str,
    rest_constructor: Callable[..., Any] | None = None,
) -> Any:
    """Create a paper-only Alpaca REST client with retries disabled and finite timeouts."""
    if not key_id or not secret_key:
        raise ValueError("Alpaca paper credentials are required")
    if not is_paper_endpoint(base_url):
        raise ValueError("Alpaca client factory accepts only the paper endpoint")
    version = _sdk_version()
    if version != SUPPORTED_SDK_VERSION:
        raise RuntimeError(f"only audited alpaca-trade-api {SUPPORTED_SDK_VERSION} is supported")
    if rest_constructor is None:
        import alpaca_trade_api as tradeapi

        rest_constructor = tradeapi.REST
    client = rest_constructor(
        key_id=key_id,
        secret_key=secret_key,
        base_url=base_url,
        api_version="v2",
    )
    return configure_alpaca_client(client, sdk_version=version)
