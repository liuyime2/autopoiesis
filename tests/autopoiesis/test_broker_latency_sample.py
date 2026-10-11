import importlib.util
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "broker_latency_sample_under_test", ROOT / "tools" / "broker_latency_sample.py"
)
assert SPEC and SPEC.loader
sample = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sample
SPEC.loader.exec_module(sample)


def config(*, mode="paper", url="https://paper-api.alpaca.markets", key="secret-key", secret="secret-value"):
    return sample.BrokerConfig(mode, url, key, secret)


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        self.value += 0.01
        return self.value


class FakeClient:
    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = set(fail_on)

    def get_clock(self):
        self.calls.append("clock")
        if "clock" in self.fail_on:
            raise RuntimeError("secret-key account-id response-body")
        return {"account_id": "must-not-be-read"}

    def get_account(self):
        self.calls.append("account")
        if "account" in self.fail_on:
            raise RuntimeError("secret-value account-id response-body")
        return {"account_id": "must-not-be-read"}


def test_samples_bounded_read_only_operations_and_reports_actual_latency(capsys):
    client = FakeClient()
    report = sample.run_sample(config(), lambda cfg: client, count=3, clock=FakeClock(), sdk_version="3.2.0")

    assert client.calls == ["clock", "account"] * 3
    assert report["requested_iterations"] == 3
    assert report["operation_count"] == 6
    assert report["successes"] == 6
    assert report["failures"] == 0
    assert report["latency_ms"]["max"] > 0
    assert "timestamp_utc" in report and report["sdk_version"] == "3.2.0"
    sample.print_report(report)
    output = capsys.readouterr().out
    assert "secret-key" not in output and "secret-value" not in output
    assert "account-id" not in output and "response-body" not in output


def test_sdk_client_retries_are_disabled_only_for_audited_version():
    class SDKClient:
        _retry = 3
        _retry_wait = 3
        _retry_codes = [429, 504]
        _session = requests.Session()
    seen = {}

    def constructor(**kwargs):
        seen.update(kwargs)
        return SDKClient()

    client = sample.build_client(constructor, config(), sdk_version="3.2.0")
    assert client._retry == 0
    assert seen["base_url"] == "https://paper-api.alpaca.markets"
    assert seen["api_version"] == "v2"
    with pytest.raises(ValueError, match="only audited"):
        sample.build_client(constructor, config(), sdk_version="3.3.0")


def test_project_pins_the_sdk_version_audited_by_the_sampler():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"alpaca-trade-api=={sample.SUPPORTED_SDK_VERSION}"' in project


def test_percentile_uses_nearest_rank():
    assert sample._percentile([4.0, 1.0, 3.0, 2.0], 0.50) == 2.0
    assert sample._percentile([4.0, 1.0, 3.0, 2.0], 0.95) == 4.0


def test_reports_failures_without_exposing_exception_or_response(capsys):
    client = FakeClient(fail_on={"account"})
    report = sample.run_sample(config(), lambda cfg: client, count=2, clock=FakeClock(), sdk_version="3.2.0")
    assert report["operation_count"] == 4
    assert report["successes"] == 2
    assert report["failures"] == 2
    sample.print_report(report)
    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert "secret-value" not in output and "account-id" not in output
    assert "response-body" not in output


@pytest.mark.parametrize(
    "cfg,count",
    [
        (config(url="https://api.alpaca.markets"), 1),
        (config(url="https://paper-api.alpaca.markets.evil.example"), 1),
        (config(url="https://credential:secret@paper-api.alpaca.markets"), 1),
        (config(url="http://paper-api.alpaca.markets"), 1),
        (config(mode="live"), 1),
        (config(key=""), 1),
        (config(secret=""), 1),
        (config(), 0),
        (config(), 51),
    ],
)
def test_refuses_non_paper_missing_credentials_or_unbounded_count_before_client(cfg, count):
    called = False

    def factory(_cfg):
        nonlocal called
        called = True
        raise AssertionError("client must not be created")

    with pytest.raises(ValueError):
        sample.run_sample(cfg, factory, count=count, clock=FakeClock(), sdk_version="3.2.0")
    assert not called
