"""The data-quality gate for a research run: is this tape one that can be judged?

Reads a registered spec, loads the bars the search is about to consume, and applies the checks in
`research/prereg.py` to them. It exists as a separate command because the check belongs *between*
fetching and searching: a verdict computed from a tape that is not the tape the spec declared is a
number with no meaning that looks exactly like one with some, and the only place that can be caught
is before the search spends two minutes producing it.

Nothing here measures an edge or promotes anything. Nothing here judges the spec - that is
`research-register`'s job, before the data is touched. This is the gate after the data exists and
before it is used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("spec", help="a registered .spec file")
    parser.add_argument("cache", help="the bar cache directory the search will read")
    parser.add_argument("--symbol", default=None, help="the instrument; default is the spec's")
    args = parser.parse_args(argv)

    from autopoiesis.replay import bar_files
    from autopoiesis.research import driver as _driver
    from autopoiesis.research import prereg

    spec_text = Path(args.spec).read_text(encoding="utf-8")
    spec = prereg.load_spec_from_text(spec_text)

    # The spec must be the one registered before the run. Checking it here rather than assuming it
    # is what makes the ordering enforceable: a run against a spec that moved is a run whose
    # hypothesis moved with the result.
    ok, problems = prereg.verify(Path(args.spec))
    if not ok:
        print("the spec is not verified against its registration, so the run does not start:")
        for problem in problems:
            print(f"  {problem}")
        return 2

    symbol = (args.symbol or spec.get("instrument") or "").strip().upper()
    start = end = None
    if spec.get("window_start_utc") and spec.get("window_end_utc"):
        start = prereg._parse_utc(spec["window_start_utc"])
        end = prereg._parse_utc(spec["window_end_utc"])
    files, ignored = bar_files(args.cache)
    mine = [p for p in files if p.name.upper().startswith(symbol + "_")]
    if not mine:
        print(f"no {symbol} bar file in {args.cache}; the cache holds "
              f"{[p.name for p in files][:5]}")
        return 2

    # The tape the search consumes, produced by the *same* code the search uses. Checking a series
    # this gate assembled itself would be a gate that passes while the thing it gates fails, and the
    # window filter in particular has to be the driver's: it is the one that decides what "the
    # declared window" means for a merged tape.
    bars, provenance, _ignored = _driver._load_real_bars(
        Path(args.cache), symbol, (start, end)
    )

    report = prereg.check_tape(bars, spec)
    print(f"symbol: {symbol}   files: {len(mine)} of {len(files)} in the cache")
    if ignored:
        print(f"ignored (not bar files): {', '.join(ignored)}")
    # The tape's own provenance, printed rather than discarded. Every verdict below is a claim
    # about this series, so the series' identity belongs next to the verdict - and a gate that
    # reports its own input is the difference between "the tape was the declared one" being
    # checkable and being asserted. The first version unpacked it and dropped it, which ruff
    # flagged as an unused variable and which hid the fingerprint the ledger is supposed to
    # carry: 99 of the ledger's records have no fingerprint at all, and the field the reader
    # looks up is not the name the writer used, so newer ones are missed too.
    if provenance:
        print(f"tape provenance: {', '.join(sorted(provenance))}")
    print(report.render())
    if not report.usable:
        print()
        print("the tape is not one this spec's claim can be judged on. Fix the data or the window"
              " before running the search; a verdict from this tape is not evidence.")
        return 1
    print("the tape is the one the spec declared, so a verdict from it means what it says")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
