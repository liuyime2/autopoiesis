"""The audited, paper-only Alpaca client and finite Requests timeout policy."""
from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests
from urllib3.util import Timeout

from autopoiesis.config import is_paper_endpoint

SUPPORTED_SDK_VERSION = "3.2.0"


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

    def request(self, method, url, **kwargs):
        # Never inherit Requests' no-timeout default and never reuse urllib3's stateful
        # Timeout instance. This bounds connect/read inactivity and urllib3's total budget,
        # but it is not a hard process deadline for slow-trickle responses or DNS stalls.
        kwargs["timeout"] = REQUEST_TIMEOUT.new_timeout()
        return super().request(method, url, **kwargs)


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
