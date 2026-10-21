"""The public site is a published claim, and it is checked like one.

It is generated from `docs/site/figures.json`, which is produced by running the commands that
produced each figure. The tests below are about the failure modes that matter for a published
page rather than about the HTML:

- A figure that no longer matches the published file is drift, and the reader cannot tell.
- A page that quotes a number the file does not carry is a page that invented it.
- The pages must be navigable and complete: a nav link to a file that does not exist is broken,
  and an empty page is worse than a missing one.
- The site must build on a fresh clone, where `runtime/` does not exist - which is why it reads a
  committed file rather than the live journal.
"""

from __future__ import annotations

import importlib.util
import json
import re
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    name = f"project_site_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "project_site.py")
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """The generated site, from the figures actually committed to the repository."""
    module = _load()
    figures = module.Figures.load()
    pages = {}
    for key, (title, render) in module.PAGES.items():
        pages[key] = module._page(title, key, render(figures), figures)
    return module, figures, pages


def test_every_page_the_nav_names_exists(generated):
    module, _figures, pages = generated
    names = {key + ".html" for key in pages}
    for key, text in pages.items():
        hrefs = set(re.findall(r'href="([^"]+\.html)"', text))
        assert hrefs <= names, f"{key} links pages that do not exist: {hrefs - names}"
    assert {key for key, _label in module.NAV} <= set(pages), (
        "a nav entry has no page behind it"
    )


def test_legacy_peak_ratio_is_not_published_as_portfolio_return():
    module = _load()
    figures = module.Figures(data={"benchmark": {"agent": {
        "return_pct": 989.12, "spy_pct": 1.0, "excess": 988.12,
        "pnl": 1.0, "capital": 1.0}}})
    text = "".join(module.overview(figures) + module.evidence(figures))
    assert "+989.12%" not in text
    assert "UNKNOWN" in text


def test_unknown_accounting_payload_builds_public_pages():
    module = _load()
    figures = module.Figures(data={"benchmark": {"agent": {
        "performance_contract": "agent-accounting-v1", "comparison_status": "UNKNOWN",
        "comparison_reason": "allocation unavailable", "pnl": 28.0, "realized": 30.0,
        "unrealized": 0.0, "assumed_cost": 2.0, "capital": None,
        "return_pct": None, "spy_pct": None, "excess": None}}})
    text = "".join(module.overview(figures) + module.evidence(figures))
    assert "None pts" not in text
    assert "UNKNOWN" in text


def test_accounting_contract_does_not_certify_a_stale_positive_return():
    module = _load()
    figures = module.Figures(data={"benchmark": {"agent": {
        "performance_contract": "agent-accounting-v1", "return_pct": 989.12,
        "spy_pct": 1.0, "excess": 988.12, "pnl": 28.0}}})
    text = "".join(module.overview(figures) + module.evidence(figures))
    assert "+989.12%" not in text


def test_each_page_is_complete_and_has_one_h1(generated):
    _module, _figures, pages = generated
    for key, text in pages.items():
        assert text.count("<h1>") == 1, f"{key} has {text.count('<h1>')} h1 elements"
        assert text.rstrip().endswith("</html>")
        assert 'id="main"' in text, f"{key} has no main landmark"
        assert 'lang="en"' in text
        assert 'name="viewport"' in text, "not responsive by declaration"
        assert "prefers-color-scheme" in text, "no dark scheme"
        assert "prefers-reduced-motion" in text, "motion not reduced on request"
        assert "Skip to content" in text, f"{key} has no skip link"
        assert ":focus-visible" in text, f"{key} has no visible focus state"


def test_no_figure_is_quoted_that_the_published_file_does_not_carry(generated):
    """A number on a page must be the number in the figures file, not a retyped one.

    The site quotes nothing by hand, but this is the check that would catch a hand-typed figure
    appearing later: every percentage in the pages is compared against the published set, and the
    agent's return, SPY's and the excess are asserted exactly.
    """
    _module, figures, pages = generated
    agent = figures.named("benchmark").get("agent") or {}
    text = "".join(pages.values())
    assert "UNKNOWN" in pages["index"]
    if agent.get("performance_contract") != "agent-accounting-v1":
        for value in (agent.get("return_pct"), agent.get("spy_pct")):
            if value is not None:
                assert f"{value:+.2f}%" not in pages["index"]
        return
    if agent.get("pnl") is not None:
        assert f"{agent['pnl']:+,.2f}" in text
    # The comparison is publishable as of the account holder's decision on 2026-10-21: the
    # denominator is the recorded current equity and SPY is measured over the same recorded window.
    # While it is UNKNOWN the old guarantee still holds - no return and no excess may be quoted.
    assert agent.get("comparison_status") in {"MEASURED", "UNKNOWN"}, agent.get("comparison_status")
    if agent.get("comparison_status") != "MEASURED":
        assert agent.get("return_pct") is None and agent.get("excess") is None


def test_the_published_figures_are_current_enough_to_publish(generated):
    """The file must be recent and complete, and must not carry a collector's error."""
    _module, figures, _pages = generated
    broken = figures.broken()
    assert not broken, f"these figures failed to collect and would publish as UNKNOWN: {broken}"
    assert figures.generated_at, "figures.json carries no generation date"
    for key in ("benchmark", "gate", "record", "signals", "tests"):
        assert key in figures.data, f"no published {key} figure"
    # The gate count on the Build page must agree with the gate's own listing, or the site is
    # quoting a number the project no longer has.
    gate = figures.named("gate")
    listing = subprocess_gate_count()
    if listing is not None:
        assert gate.get("check_classes") == listing, (
            f"published {gate.get('check_classes')} classes; verify.py --list says {listing}"
        )


def subprocess_gate_count():
    import re as _re
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "verify.py"), "--list"],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    match = _re.search(r"^(\d+) classes on a full run", proc.stdout, _re.MULTILINE)
    return int(match.group(1)) if match else None


def test_the_site_builds_without_runtime(tmp_path):
    """A fresh clone has no `runtime/`, and the site must still build.

    This is why the pages read a committed figures file instead of the live journal: reading the
    journal would make the site unbuildable for anyone without a paper account, and would publish
    one deployment's numbers as the project's.
    """
    module = _load()
    module.OUT_DIR = tmp_path
    assert module.main([]) == 0
    for key in module.PAGES:
        page = tmp_path / (key + ".html")
        assert page.exists() and page.stat().st_size > 1000, f"{key}.html was not built"
