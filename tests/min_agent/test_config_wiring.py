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
CONFIG_PY = SRC / "config.py"
PRODUCTION = "\n".join(
    p.read_text() for p in sorted(SRC.glob("*.py")) if p.name != "config.py"
)
CONFIG_SRC = CONFIG_PY.read_text()

FIELDS = [f.name for f in dataclasses.fields(AgentConfig)]


def _env_vars_read() -> set[str]:
    """Every MIN_AGENT_* variable the loader looks at."""
    return set(re.findall(r"MIN_AGENT_[A-Z0-9_]+", CONFIG_SRC))


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
        f"set in the runtime env but never read by the loader: {unknown}. "
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
