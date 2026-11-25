"""The v6 forward basket's targets tool. All bars here are synthetic (labelled)."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from autopoiesis.replay import ReplayBar

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import basket_targets as bt

START = date(2024, 1, 1)


def _bars(n: int, drift: float, start: date = START) -> list[ReplayBar]:
    out, close = [], 50.0
    for i in range(n):
        close *= 1 + drift
        stamp = datetime.combine(start + timedelta(days=i), datetime.min.time(), tzinfo=timezone.utc)
        out.append(ReplayBar(timestamp=stamp, open=close, high=close, low=close, close=close, volume=1e6))
    return out


def _universe(n: int = 320) -> dict:
    return {f"S{i:02d}": _bars(n, 0.0001 * i) for i in range(30)}  # synthetic: S29 trends most


def test_the_tenth_with_the_strongest_momentum_is_held():
    effective = START + timedelta(days=320)
    members, held = bt.select_targets(_universe(), effective)
    assert len(members) == 30 and held == ["S29", "S28", "S27"]  # 30 // 10


def test_no_bar_on_or_after_the_effective_session_is_read():
    effective = START + timedelta(days=300)
    base = _universe(320)
    flipped = {s: b[:300] + _bars(20, -0.2 if s >= "S25" else 0.3, start=effective) for s, b in base.items()}
    assert bt.select_targets(base, effective) == bt.select_targets(flipped, effective)


def test_the_body_digest_covers_every_field_but_itself():
    body = {"strategy_id": "v6-mom-top10", "symbols": ["A"], "effective": "2026-11-30"}
    body["sha256"] = bt.body_sha256(body)
    assert bt.body_sha256(body) == body["sha256"]
    assert bt.body_sha256({**body, "symbols": ["B"]}) != body["sha256"]


def test_settled_end_waits_for_the_close_in_new_york():
    ny = bt.NY
    assert bt.settled_end(datetime(2026, 11, 27, 16, 0, tzinfo=ny)) == date(2026, 11, 26)
    assert bt.settled_end(datetime(2026, 11, 27, 18, 0, tzinfo=ny)) == date(2026, 11, 27)
