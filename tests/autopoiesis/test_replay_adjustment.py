"""fetch_bars passes `adjustment` only when asked (SYNTHETIC client)."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from autopoiesis.replay import fetch_bars


class FakeClient:
    def __init__(self):
        self.calls: list[dict] = []

    def get_bars(self, symbol, timeframe, start, end, **kwargs):
        self.calls.append(kwargs)
        frame = pd.DataFrame(
            {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [10]},
            index=pd.DatetimeIndex(["2025-12-05T05:00:00Z"]),
        )
        return SimpleNamespace(df=frame)


def test_adjustment_is_only_sent_when_requested():
    client = FakeClient()
    fetch_bars("XLE", "2025-12-01", "2025-12-10", client=client, interval="1Day", feed="iex")
    fetch_bars("XLE", "2025-12-01", "2025-12-10", client=client, interval="1Day", feed="iex", adjustment="all")
    fetch_bars("XLE", "2025-12-01", "2025-12-10", client=client, interval="1Day")
    assert client.calls == [{"feed": "iex"}, {"feed": "iex", "adjustment": "all"}, {}]
