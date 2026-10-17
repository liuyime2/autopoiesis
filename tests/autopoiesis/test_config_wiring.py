"""A setting that exists is not a setting that works.

The objective is explicit that configuration, functions, tests and documentation
existing must never be treated as the capability being real. This pins that for
`AgentConfig` in both directions, because drift goes both ways: a field nothing
reads is a control that does nothing, and an environment variable nothing maps is
an operator knob that silently does nothing when set.
"""

import dataclasses
import os
import re
import sys
from pathlib import Path

import pytest

from autopoiesis.config import AgentConfig

SRC = Path(__file__).resolve().parents[2] / "src" / "autopoiesis"
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
import verify

CONFIG_PY = SRC / "config.py"
PRODUCTION = "\n".join(
    p.read_text() for p in sorted(SRC.glob("*.py")) if p.name != "config.py"
)
CONFIG_SRC = CONFIG_PY.read_text()

FIELDS = [f.name for f in dataclasses.fields(AgentConfig)]


def _env_vars_read() -> set[str]:
    """Loader keys plus actual shell and Make references; never operator secrets."""
    return set(re.findall(r"AUTOPOIESIS_[A-Z0-9_]+", CONFIG_SRC + verify._entry_point_reads()))


def _env_vars_in_the_shipped_config() -> set[str]:
    """Active key assignments in the shipped example, independent of the host."""
    path = REPO / "configs" / "paper.env.example"
    return set(re.findall(r"^[ \t]*(AUTOPOIESIS_[A-Z0-9_]+)=", path.read_text(), re.M))


@pytest.mark.parametrize("name", FIELDS)
def test_every_config_field_is_either_read_or_populated_from_the_environment(name):
    """A field must be consumed by production code or wired to an env var.

    Both halves matter. A field read nowhere is a control nobody can turn; a field
    with no env var and no reader is decoration that looks configurable.
    """
    read_in_production = re.search(rf"\.{re.escape(name)}\b", PRODUCTION) is not None
    populated = re.search(rf'"{re.escape(name)}"', CONFIG_SRC) is not None
    assert read_in_production or populated, (
        f"AgentConfig.{name} is neither read in production nor populated from the "
        "environment, so it cannot affect behaviour"
    )


def test_the_wiring_check_would_actually_catch_a_dead_field():
    """A check that cannot fail is decoration. This plants a field that is neither
    read in production nor populated, and asserts the rule rejects it - the same
    discipline make verify-self-test applies to the gate.

    An earlier version of this file had `assert ... or True`, which always passed
    while looking like a real assertion.
    """
    def wired(name: str) -> bool:
        return (
            re.search(rf"\.{re.escape(name)}\b", PRODUCTION) is not None
            or re.search(rf'"{re.escape(name)}"', CONFIG_SRC) is not None
        )

    assert not wired("a_field_nobody_reads")
    assert wired("max_position_value"), "a real field must pass its own rule"


def test_every_operator_knob_is_actually_read_by_the_loader():
    """The inverse drift: a variable an operator sets that maps to nothing means the
    setting is believed to be in force and is not."""
    read = _env_vars_read()
    configured = _env_vars_in_the_shipped_config()
    unknown = sorted(configured - read)
    assert not unknown, (
        f"set in the shipped example but read by no loader, shell or Make consumer: {unknown}. "
        "Either wire them up or remove them - an ignored setting is worse than "
        "an absent one, because it looks like it is working"
    )


@pytest.mark.parametrize("host_env_present", [False, True])
def test_declared_knobs_use_shipped_assignments_without_opening_host_env(
    tmp_path, monkeypatch, host_env_present,
):
    """Synthetic files: operator secrets and documentation are not declarations."""
    example = tmp_path / "configs" / "paper.env.example"
    example.parent.mkdir()
    example.write_text(
        "AUTOPOIESIS_MODE=paper\n"
        "  AUTOPOIESIS_SYMBOLS=SPY\n"
        "# AUTOPOIESIS_COMMENT_ONLY=ignored\n"
        "# Explain AUTOPOIESIS_PROSE without setting it.\n"
        "unrelated=AUTOPOIESIS_VALUE\n"
    )
    host_env = tmp_path / "host" / "autopoiesis" / "env"
    host_env.parent.mkdir(parents=True)
    if host_env_present:
        host_env.write_text("AUTOPOIESIS_HOST_ONLY=synthetic\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(host_env.parent.parent))
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    original = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert path != host_env, "ordinary wiring checks must not open the host env"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    assert _env_vars_in_the_shipped_config() == {"AUTOPOIESIS_MODE", "AUTOPOIESIS_SYMBOLS"}


def test_actual_make_research_references_are_configuration_readers():
    """These are the repository's real Make references, without executing research."""
    assert {
        "AUTOPOIESIS_RESEARCH_SPEC", "AUTOPOIESIS_PREREGISTRY", "AUTOPOIESIS_RESEARCH_TRIALS",
    } <= _env_vars_read()


@pytest.fixture
def synthetic_config_workspace(tmp_path, monkeypatch):
    """Minimal synthetic source tree for the existing config-example gate."""
    (tmp_path / "configs").mkdir()
    (tmp_path / "src" / "autopoiesis").mkdir(parents=True)
    (tmp_path / "src" / "autopoiesis" / "config.py").write_text("")
    (tmp_path / "autopoiesis").write_text("#!/bin/sh\n")
    (tmp_path / "Makefile").write_text("")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty-host-config"))
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    monkeypatch.setattr(sys.modules[__name__], "CONFIG_SRC", "")
    return tmp_path


@pytest.mark.parametrize("check", ["inverse", "gate"])
def test_make_references_are_consumers_in_both_wiring_checks(synthetic_config_workspace, check):
    """Synthetic declarations use the three actual research reference forms."""
    root = synthetic_config_workspace
    names = {"AUTOPOIESIS_RESEARCH_SPEC", "AUTOPOIESIS_PREREGISTRY", "AUTOPOIESIS_RESEARCH_TRIALS"}
    (root / "configs" / "paper.env.example").write_text("\n".join(f"{name}=synthetic" for name in names))
    (root / "Makefile").write_text(
        "SPEC ?= $(AUTOPOIESIS_RESEARCH_SPEC)\n"
        "PREREGISTRY ?= $(if $(AUTOPOIESIS_PREREGISTRY),$(AUTOPOIESIS_PREREGISTRY),runtime/registry)\n"
        "TRIALS ?= $(if $(AUTOPOIESIS_RESEARCH_TRIALS),$(AUTOPOIESIS_RESEARCH_TRIALS),runtime/trials)\n"
    )
    if check == "inverse":
        assert names <= _env_vars_read()
    else:
        assert verify.check_config_example_names_are_real().status == verify.PASS


@pytest.mark.parametrize("make_source", [
    "",
    "# $(AUTOPOIESIS_DEAD)\n",
    "OTHER=synthetic # $(AUTOPOIESIS_DEAD)\n",
    "AUTOPOIESIS_DEAD ?= synthetic\n",
])
def test_dead_make_names_are_rejected_by_both_checks(synthetic_config_workspace, make_source):
    """Synthetic dead, comment-only and assignment-only names are not reads."""
    root = synthetic_config_workspace
    (root / "configs" / "paper.env.example").write_text("AUTOPOIESIS_DEAD=synthetic\n")
    (root / "Makefile").write_text(make_source)
    assert "AUTOPOIESIS_DEAD" not in _env_vars_read()
    assert verify.check_config_example_names_are_real().status == verify.FAIL


@pytest.mark.parametrize("host_env_present", [False, True])
def test_host_env_cannot_validate_an_unknown_example_knob(
    synthetic_config_workspace, monkeypatch, host_env_present,
):
    """A synthetic external env must not provide evidence of a code reader."""
    root = synthetic_config_workspace
    (root / "configs" / "paper.env.example").write_text("AUTOPOIESIS_DEAD=synthetic\n")
    host_env = root / "host" / "autopoiesis" / "env"
    host_env.parent.mkdir(parents=True)
    if host_env_present:
        host_env.write_text("AUTOPOIESIS_DEAD=synthetic\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(host_env.parent.parent))
    assert verify.check_config_example_names_are_real().status == verify.FAIL


@pytest.mark.parametrize(("shell_source", "consumed"), [
    ("echo ${AUTOPOIESIS_SYNTHETIC:-default}\n", True),
    ("echo $AUTOPOIESIS_SYNTHETIC\n", True),
    ("# echo ${AUTOPOIESIS_SYNTHETIC}\n", False),
    ("AUTOPOIESIS_SYNTHETIC=synthetic\n", False),
    ('echo "set AUTOPOIESIS_SYNTHETIC in your env"\n', False),
    ('echo "${AUTOPOIESIS_SYNTHETIC:-default}"\n', True),
    ('echo "# $AUTOPOIESIS_SYNTHETIC"\n', True),
    ("echo value#$AUTOPOIESIS_SYNTHETIC\n", True),
    ("echo ok # ${AUTOPOIESIS_SYNTHETIC}\n", False),
    ("echo '$AUTOPOIESIS_SYNTHETIC'\n", False),
    ("echo '${AUTOPOIESIS_SYNTHETIC}'\n", False),
    ("echo \\${AUTOPOIESIS_SYNTHETIC}\n", False),
    ('echo "\\$AUTOPOIESIS_SYNTHETIC"\n', False),
])
@pytest.mark.parametrize("check", ["inverse", "gate"])
def test_shell_consumers_require_variable_expansion(
    synthetic_config_workspace, shell_source, consumed, check,
):
    """Synthetic shell source separates runtime expansion from declared names."""
    root = synthetic_config_workspace
    (root / "configs" / "paper.env.example").write_text("AUTOPOIESIS_SYNTHETIC=synthetic\n")
    (root / "autopoiesis").write_text(shell_source)
    if check == "inverse":
        assert ("AUTOPOIESIS_SYNTHETIC" in _env_vars_read()) is consumed
    else:
        expected = verify.PASS if consumed else verify.FAIL
        assert verify.check_config_example_names_are_real().status == expected


def test_config_example_gate_never_opens_host_env(synthetic_config_workspace, monkeypatch):
    """Synthetic host guard catches an attempted read even if the gate still fails."""
    root = synthetic_config_workspace
    (root / "configs" / "paper.env.example").write_text("AUTOPOIESIS_DEAD=synthetic\n")
    host_env = root / "host" / "autopoiesis" / "env"
    host_env.parent.mkdir(parents=True)
    host_env.write_text("AUTOPOIESIS_OTHER=synthetic\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(host_env.parent.parent))
    original = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert path != host_env, "config-example gate must not open the host env"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    assert verify.check_config_example_names_are_real().status == verify.FAIL


def test_the_config_loader_refuses_live_mode():
    """Not a wiring check, but it belongs with the others: the one setting that
    must never take effect is the one that would risk real money."""
    previous = os.environ.get("AUTOPOIESIS_MODE")
    os.environ["AUTOPOIESIS_MODE"] = "live"
    try:
        with pytest.raises(ValueError):
            AgentConfig.from_env()
    finally:
        if previous is None:
            os.environ.pop("AUTOPOIESIS_MODE", None)
        else:
            os.environ["AUTOPOIESIS_MODE"] = previous


def test_paper_endpoint_is_a_host_check_not_a_substring():
    """`"paper" in url` accepted hosts that are not Alpaca's paper host.

    The check appeared in five places across three modules, each as a substring test. That
    rejected Alpaca's live URL - real safety - while accepting anything with the letters
    p-a-p-e-r in it, including `https://paper-api.alpaca.markets.evil.example`, which is a
    host an attacker chooses. It was a check on the wrong property, expressed five times so
    the five could drift.
    """
    from autopoiesis.config import is_paper_endpoint

    accepted = [
        "https://paper-api.alpaca.markets",
        "https://api.paper.trading.alpaca.com",
        "https://PAPER-API.ALPACA.MARKETS",       # host comparison is case-insensitive
        "https://paper-api.alpaca.markets/v2",     # path is irrelevant
    ]
    rejected = [
        "https://api.alpaca.markets",              # live
        "https://live-api.alpaca.markets",
        "https://evil.example/?next=paper",        # substring in a query string
        "https://paper-api.alpaca.markets.evil.example",  # suffix attack
        "https://live-api.paper-trading.example",
        "https://paper-api.alpaca.markets.evil.example/path",
        "not a url",
        "",
    ]
    for url in accepted:
        assert is_paper_endpoint(url) is True, f"{url} is Alpaca's paper host and must be accepted"
    for url in rejected:
        assert is_paper_endpoint(url) is False, (
            f"{url!r} was accepted as a paper endpoint; the check is a substring test again"
        )


def test_every_paper_check_uses_the_one_definition():
    """No module may re-implement the paper test; they must all route through config.

    Five copies of a safety check is four opportunities to be weaker than the others, and
    the one nearest `submit_order` is the copy that matters most.
    """
    import pathlib

    import autopoiesis

    root = pathlib.Path(autopoiesis.__file__).parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        # Only code, not prose: config.py's own docstring quotes the old expression to
        # explain why it was replaced, and the first version of this check flagged that.
        import tokenize

        code_lines = set()
        with open(path, "rb") as handle:
            for token in tokenize.tokenize(handle.readline):
                if token.type == tokenize.NAME or (
                    token.type == tokenize.OP and token.string in "'\""
                ):
                    code_lines.add(token.start[0])
        for line_no, line in enumerate(text.splitlines(), 1):
            if line_no not in code_lines:
                continue
            code = line.split("#", 1)[0]
            if '"paper" in' in code or "'paper' in" in code or '"paper" not in' in code:
                offenders.append(f"{path.name}:{line_no}: {line.strip()[:70]}")
    assert not offenders, (
        "paper-only is re-implemented as a substring test; use "
        f"autopoiesis.config.is_paper_endpoint(): {offenders}"
    )
