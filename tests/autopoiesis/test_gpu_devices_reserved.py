"""GPUs 5 to 9 belong to work that is not this project's. AGENTS.md section 18.

Measured 2026-10-16, and the reason this is a check rather than a sentence: the agent's
Ollama was pinned to GPU 0 in the unit while a second instance, started outside the unit,
ran on GPU 2 and port 11500 - two servers competing for a device neither was documented as
owning. A pin nobody verifies is a preference.

The check reads what a runtime would read: the unit template, the installed unit, and the
configuration's `AUTOPOIESIS_GPU_DEVICES`.

Usage: pytest tests/autopoiesis/test_gpu_devices_reserved.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import verify


def test_the_allowed_set_is_zero_to_four():
    assert frozenset({"0", "1", "2", "3", "4"}) == verify.ALLOWED_GPU_DEVICES
    for reserved in ("5", "6", "7", "8", "9"):
        assert reserved not in verify.ALLOWED_GPU_DEVICES


def test_the_template_pin_is_within_the_allowed_set():
    """The pin that survives a reinstall, and the one a fresh clone gets."""
    template = ROOT / "tools" / "ollama.service.in"
    lines = [
        line for line in template.read_text(encoding="utf-8").splitlines()
        if line.startswith("Environment=CUDA_VISIBLE_DEVICES=")
    ]
    assert lines, "the template must pin a device, or the agent may reach any of them"
    for line in lines:
        named = set(line.split("=", 2)[2].split(","))
        assert named <= verify.ALLOWED_GPU_DEVICES, (
            f"{line} names a reserved device; AGENTS.md 18 reserves 5-9"
        )


def test_the_check_passes_on_this_checkout():
    """Skipped rather than failed where there is no pin to read, which is a fresh clone."""
    result = verify.check_gpu_devices_reserved()
    if result.status == verify.SKIP:
        pytest.skip(result.detail)
    assert result.status == verify.PASS, result.detail


def test_a_reserved_device_in_a_pin_is_a_failure(monkeypatch, tmp_path):
    """The property the whole check exists for, driven directly rather than trusted."""
    fake = tmp_path / "ollama.service.in"
    fake.write_text("Environment=CUDA_VISIBLE_DEVICES=4,5\n", encoding="utf-8")
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    monkeypatch.setattr(verify, "_gpu_pins", lambda: [(str(fake), "4,5")])

    result = verify.check_gpu_devices_reserved()

    assert result.status == verify.FAIL, result.detail
    assert "5" in result.detail and "reserved" in result.detail


@pytest.mark.parametrize("pin", ["0", "4", "2,3", "0,1,2,3,4"])
def test_every_allowed_pin_passes(monkeypatch, pin):
    monkeypatch.setattr(verify, "_gpu_pins", lambda: [("somewhere", pin)])
    assert verify.check_gpu_devices_reserved().status == verify.PASS


@pytest.mark.parametrize("pin", ["5", "9", "4,5", "5,6,7,8,9", "0,7"])
def test_every_reserved_pin_fails(monkeypatch, pin):
    monkeypatch.setattr(verify, "_gpu_pins", lambda: [("somewhere", pin)])
    result = verify.check_gpu_devices_reserved()
    assert result.status == verify.FAIL, f"{pin} must be refused: {result.detail}"


def test_no_pin_at_all_skips_rather_than_passing(monkeypatch):
    """Absence is not compliance. A check that passes on nothing says nothing."""
    monkeypatch.setattr(verify, "_gpu_pins", list)
    assert verify.check_gpu_devices_reserved().status == verify.SKIP


def test_the_class_is_declared_and_run():
    """A class in the registry that nothing runs is a comment with extra steps."""
    source = (ROOT / "tools" / "verify.py").read_text(encoding="utf-8")
    assert '"gpu-devices-reserved"' in source
    assert 'check_gpu_devices_reserved, "gpu-devices-reserved"' in source, (
        "the class must be appended to the run list, not only declared"
    )
