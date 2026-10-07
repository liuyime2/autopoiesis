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
from pathlib import Path

import pytest

from min_agent.config import AgentConfig

SRC = Path(__file__).resolve().parents[2] / "src" / "min_agent"
REPO = Path(__file__).resolve().parents[2]
CONFIG_PY = SRC / "config.py"
OPERATOR_ENTRY_POINT = REPO / "minictrl"
PRODUCTION = "\n".join(
    p.read_text() for p in sorted(SRC.glob("*.py")) if p.name != "config.py"
)
CONFIG_SRC = CONFIG_PY.read_text()

FIELDS = [f.name for f in dataclasses.fields(AgentConfig)]


def _entry_point_reads() -> str:
    """`minictrl`'s executable code, whole-line comments dropped.

    The env file is dual-purpose: `minictrl` sources the same file the loader reads and then
    does its own shell work before it ever invokes `python -m min_agent.cli`. MIN_AGENT_ENVBIN
    is the live case - it names the environment's bin directory and nothing in Python reads it.

    Scanning the file whole would be worthless, because its header documents every shell knob
    it honours and a docstring is not a read; the header is entirely `#` lines, so dropping
    those makes the scan mean "this code reads it".
    """
    code = [
        line
        for line in OPERATOR_ENTRY_POINT.read_text().splitlines()
        if not line.lstrip().startswith("#")
    ]
    return "\n".join(code)


def _env_vars_read() -> set[str]:
    """Every MIN_AGENT_* variable an operator's env file can actually be consumed by.

    Two readers, not one. Scanning only `config.py` reported MIN_AGENT_ENVBIN as a dead knob
    on a host where it is the reason `minictrl install-service` finds the environment at all -
    the same "looks configurable, does nothing" failure this file exists to catch, pointed the
    other way, and the obvious response to a red gate of that kind is to delete the line from
    the env file and break the install. The sibling gate `tools/verify.py::
    check_config_example_names_are_real` had the same blind spot in the other direction.
    """
    return set(re.findall(r"MIN_AGENT_[A-Z0-9_]+", CONFIG_SRC + _entry_point_reads()))


def _env_vars_in_the_shipped_config() -> set[str]:
    """Every MIN_AGENT_* variable an operator could actually set."""
    path = Path(os.environ.get("XDG_CONFIG_HOME", "")) / "min-agent" / "env"
    if not path.exists():
        pytest.skip("no runtime env file on this host")
    return set(re.findall(r"MIN_AGENT_[A-Z0-9_]+", path.read_text()))


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
        f"set in the runtime env but read by neither config.py nor minictrl: {unknown}. "
        "Either wire them up or remove them - an ignored setting is worse than "
        "an absent one, because it looks like it is working"
    )


def test_the_config_loader_refuses_live_mode():
    """Not a wiring check, but it belongs with the others: the one setting that
    must never take effect is the one that would risk real money."""
    previous = os.environ.get("MIN_AGENT_MODE")
    os.environ["MIN_AGENT_MODE"] = "live"
    try:
        with pytest.raises(ValueError):
            AgentConfig.from_env()
    finally:
        if previous is None:
            os.environ.pop("MIN_AGENT_MODE", None)
        else:
            os.environ["MIN_AGENT_MODE"] = previous


def test_paper_endpoint_is_a_host_check_not_a_substring():
    """`"paper" in url` accepted hosts that are not Alpaca's paper host.

    The check appeared in five places across three modules, each as a substring test. That
    rejected Alpaca's live URL - real safety - while accepting anything with the letters
    p-a-p-e-r in it, including `https://paper-api.alpaca.markets.evil.example`, which is a
    host an attacker chooses. It was a check on the wrong property, expressed five times so
    the five could drift.
    """
    from min_agent.config import is_paper_endpoint

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

    import min_agent

    root = pathlib.Path(min_agent.__file__).parent
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
        f"min_agent.config.is_paper_endpoint(): {offenders}"
    )
