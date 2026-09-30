"""The infrastructure checks must be able to fail.

The objective's recurring lesson from this audit is that a check which cannot fail
is not a control. `_check_admission_provenance` was wired, tested and green while
five unapproved strategies held the library; `doctor: daemon` went red twice on a
daemon that was provably working. Both were found by asking, for each check, what
input would make it say something other than OK.

These are the environment and reachability checks, where a silent failure is the
most damaging kind: an unreachable broker, a wrong interpreter, or an unreadable
library each look identical to health if the check cannot go red.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import min_agent.doctor as doctor_module
from min_agent.doctor import DoctorReport


def _report():
    return DoctorReport()


def _by_name(report, name):
    return [c for c in report.checks if c.name == name]


# --- python version ----------------------------------------------------------


def test_an_old_interpreter_is_a_failure(monkeypatch):
    """A 3.9 interpreter would break every dependency check downstream."""
    monkeypatch.setattr(sys, "version_info", (3, 9, 18, "final", 0))
    report = _report()
    doctor_module._check_environment(report, None)
    check = _by_name(report, "python")[0]
    assert check.status.value == "fail", f"{check.status.value}: {check.detail}"
    assert "3.9.18" in check.detail
    assert "llm conda env" in check.hint


def test_the_current_interpreter_passes(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 10, 18, "final", 0))
    report = _report()
    doctor_module._check_environment(report, None)
    assert _by_name(report, "python")[0].status.value == "ok"


def test_a_missing_dependency_is_a_failure(monkeypatch):
    """`dep:` lines are the only thing standing between a wrong env and a
    confusing runtime traceback."""

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "requests":
            raise ImportError("No module named 'requests'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    report = _report()
    doctor_module._check_environment(report, None)
    fails = [c for c in report.checks if c.name == "dep:requests"]
    assert len(fails) == 1 and fails[0].status.value == "fail", report.checks
    # The other dependencies are still reported, so one missing module does not
    # hide the state of the rest.
    assert len([c for c in report.checks if c.name.startswith("dep:")]) == 3


# --- broker reachability -----------------------------------------------------


class _Clock:
    def __init__(self, is_open=True, next_open="2026-09-30T09:30:00-04:00"):
        self.is_open = is_open
        self.next_open = next_open


class _BrokerStub:
    def __init__(self, *, clock=None, boom=False):
        self._clock = clock or _Clock()
        self._boom = boom

    def get_clock(self):
        if self._boom:
            raise ConnectionError("connection reset by peer")
        return self._clock

    def list_positions(self):
        if self._boom:
            raise ConnectionError("connection reset by peer")
        return []

    def list_orders(self, status=None):
        if self._boom:
            raise ConnectionError("connection reset by peer")
        return []


class _Cfg:
    def __init__(self, **kw):
        self.allowlist = ["SPY"]
        self.missing_alpaca_credentials = lambda: False
        self.__dict__.update(kw)


def test_an_unreachable_broker_is_a_failure():
    """Silently skipping on a broker outage would make an outage look healthy."""
    report = _report()
    doctor_module._check_broker(report, _Cfg(), _BrokerStub(boom=True), skip=False)
    check = _by_name(report, "broker clock")[0]
    assert check.status.value == "fail", f"{check.status.value}: {check.detail}"
    assert "paper API" in check.hint


def test_a_closed_market_is_not_a_failure():
    report = _report()
    doctor_module._check_broker(
        report, _Cfg(), _BrokerStub(clock=_Clock(is_open=False)), skip=False
    )
    check = _by_name(report, "broker clock")[0]
    assert check.status.value == "ok"
    assert "CLOSED" in check.detail


def test_missing_credentials_skip_rather_than_pass():
    """Unknown is not healthy. SKIP says so; OK would not."""
    report = _report()
    doctor_module._check_broker(
        report, _Cfg(missing_alpaca_credentials=lambda: True), _BrokerStub(), skip=False
    )
    assert _by_name(report, "broker clock")[0].status.value == "skip"


def test_skip_flag_is_honoured():
    report = _report()
    doctor_module._check_broker(report, _Cfg(), _BrokerStub(boom=True), skip=True)
    check = _by_name(report, "broker clock")[0]
    assert check.status.value == "skip"
    assert "--skip-broker" in check.detail


# --- knowledge library readability -------------------------------------------


class _UnreadableLibrary:
    def list(self, status=None, tags=None):
        raise PermissionError("knowledge directory is not readable")


def test_an_unreadable_knowledge_library_fails(monkeypatch, tmp_path):
    """A library that cannot be read must not read as an empty one.

    I wrote this expecting a warning. FAIL is right and the check already says so:
    an unreadable knowledge directory is a broken install, not a finding to
    observe, and a warning here would be indistinguishable from the empty-library
    case below.
    """
    monkeypatch.setattr(
        doctor_module, "KnowledgeLibrary", lambda *_a, **_k: _UnreadableLibrary()
    )
    report = _report()
    doctor_module._check_knowledge(report, _Cfg(knowledge_dir=tmp_path))
    check = _by_name(report, "knowledge library")[0]
    assert check.status.value == "fail", f"{check.status.value}: {check.detail}"
    assert "PermissionError" in check.detail


def test_an_empty_knowledge_library_warns(monkeypatch, tmp_path):
    """Also not what I expected: empty is a warning, not OK.

    With nothing accepted, the knowledge path cannot inform a decision - the
    reflection-to-decision link is silently inert. That is worth saying out loud,
    and the check already says it.
    """
    class _Empty:
        def list(self, status=None, tags=None):
            return []

    monkeypatch.setattr(doctor_module, "KnowledgeLibrary", lambda *_a, **_k: _Empty())
    report = _report()
    doctor_module._check_knowledge(report, _Cfg(knowledge_dir=tmp_path))
    check = _by_name(report, "knowledge library")[0]
    assert check.status.value == "warn", f"{check.status.value}: {check.detail}"
    assert "cannot inform a decision" in check.hint
