"""Every timer's OnCalendar must be one systemd accepts.

Measured 2026-11-07: information-refresh.timer said `*-*-* 06:00,10:00,14:00,18:00`, which
systemd rejects ("Failed to parse calendar specification ... Timer unit lacks value setting").
install-service enabled it and it never fired. Skipped where systemd-analyze is absent (CI).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze not installed")
@pytest.mark.parametrize("timer", sorted(TOOLS.glob("*.timer")), ids=lambda p: p.name)
def test_on_calendar_parses(timer):
    for spec in re.findall(r"^OnCalendar=(.+)$", timer.read_text(encoding="utf-8"), flags=re.M):
        proc = subprocess.run(["systemd-analyze", "calendar", spec.strip()], capture_output=True, text=True)
        assert proc.returncode == 0, f"{timer.name}: {spec!r}: {proc.stderr.strip()}"
