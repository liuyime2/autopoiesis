from __future__ import annotations

from enum import StrEnum


class TradingMode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


def normalize_mode(value: str) -> TradingMode:
    mode = TradingMode(value.strip().lower())
    if mode is TradingMode.LIVE:
        raise ValueError("live mode is not allowed in phase 1")
    return mode
