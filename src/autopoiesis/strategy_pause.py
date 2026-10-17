"""An audited way to take a strategy out of service, and to prove afterwards why.

Measured 2026-10-14 on the live account. The curriculum read "this position has no
strategy that can sell it" as a gap to fill, and filled it the only way its weakest
kind could express - an unconditional `SELL 1`:

    fixed-size-tlt-sell-001  TLT  SELL 1  no market condition  no cumulative cap
    fixed-size-xle-sell-001  XLE  SELL 1  no market condition  no cumulative cap
    fixed-size-xlf-sell-001  XLF  SELL 1  no market condition  no cumulative cap

Such a strategy has no condition to become false, so it fires on every cycle until
the position is gone. Measured outcome, broker-verified fills: TLT 188 shares sold,
XLE 36 of 36 - emptied - XLF 31 of 31 - emptied. `trend-follow-sell-001` in the same
library carries `reference_price` and `threshold_pct`, so the library can express a
*conditional* sell; the choice of an unconditional kind was the defect, not the gap.

Pausing them is not a risk-limit change and not a deletion. It moves the lifecycle to
PAUSED, which `StrategyLibrary` already excludes from selection, and it records who
paused what and why, so the pause can be audited and reversed. It is deliberately
separate from retirement: retirement is terminal, and these strategies have not failed
- they are mis-specified, and that is fixable.

`StrategyLibrary.list()` reads every spec from disk on each call, so a pause written
here is seen by the running daemon on its next cycle without a restart or a deploy.
That is what makes this the right instrument for a live position drain: it stops the
loss without putting unreviewed code into the trading path.

Usage: python -m autopoiesis.strategy_pause <strategy_id> --reason "<why>"

This reads no credentials and needs none: pausing a strategy writes one strategy file and
one journal line, and touches neither the broker nor the model. The paths come from the
caller, and `make pause-strategy` supplies the live ones. Resolving a config file here
would have meant constructing a `$HOME` path, which the `home-independence` gate forbids
in production code and which the Makefile already resolves in one place.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from autopoiesis.journal import JournalEvent, JsonlJournal
from autopoiesis.models import StrategySpec
from autopoiesis.strategy_engine import StrategyLibrary

EVENT_TYPE = "STRATEGY_LIFECYCLE_UPDATED"

#: Terminal by design (see `StrategyLifecycleManager`): a retired strategy must not be
#: resurrected by an operator typo, because its evidence is what retired it.
TERMINAL = {"RETIRED"}


class StrategyPauseRefused(Exception):
    """The pause would be wrong, and the caller is told why rather than being obeyed."""


def pause_strategy(
    *,
    library: StrategyLibrary,
    journal: JsonlJournal | None,
    strategy_id: str,
    reason: str,
    actor: str = "operator",
    now: datetime | None = None,
) -> StrategySpec:
    """Move one strategy to PAUSED, record it, and return the saved spec.

    Raises `StrategyPauseRefused` for a strategy that does not exist, is already
    terminal, or is already paused with this same reason - the last so that repeating
    the command does not append a second identical line of audit trail.
    """
    strategy_id = (strategy_id or "").strip()
    if not strategy_id:
        raise StrategyPauseRefused("no strategy_id given")
    cleaned = (reason or "").strip()
    if not cleaned:
        raise StrategyPauseRefused(
            "a pause with no reason is indistinguishable from a mistake; "
            "give --reason"
        )

    spec = library.try_load(strategy_id)
    if spec is None:
        raise StrategyPauseRefused(f"no such strategy: {strategy_id}")
    if spec.lifecycle in TERMINAL:
        raise StrategyPauseRefused(
            f"{strategy_id} is {spec.lifecycle}, which is terminal and is not "
            f"reversible from here; a retired strategy is re-admitted through the "
            f"admission gate with evidence, not through an operator pause"
        )
    if spec.lifecycle == "PAUSED" and spec.lifecycle_reason == cleaned:
        raise StrategyPauseRefused(
            f"{strategy_id} is already PAUSED for this exact reason; "
            f"nothing was written"
        )

    previous = spec.lifecycle
    updated = spec.model_copy(update={"lifecycle": "PAUSED", "lifecycle_reason": cleaned})
    library.save(updated)

    if journal is not None:
        stamp = now or datetime.now(timezone.utc)
        journal.append_event(
            JournalEvent(
                event_id=f"pause-{strategy_id}-{int(stamp.timestamp())}",
                event_type=EVENT_TYPE,  # type: ignore[arg-type]
                timestamp=stamp,
                status="SUCCESS",
                message=f"paused by {actor}: {cleaned}",
                strategy_id=strategy_id,
                payload={
                    "phase": "applied",
                    "source": "operator_pause",
                    "actor": actor,
                    "previous_lifecycle": previous,
                    "lifecycle": "PAUSED",
                    # The key the existing reader uses. `_check_lifecycle_provenance` in
                    # doctor.py builds its "which state did the journal decide" map from
                    # `payload["new_lifecycle"]`, and a state it cannot trace there is
                    # reported as a blocking finding. Writing only `lifecycle` left three
                    # paused strategies failing the doctor with "reached a state with no
                    # matching recorded decision", which was true of the reader and false
                    # of the fact. The pause is a recorded decision; it has to be recorded
                    # in the shape the reader already reads.
                    "new_lifecycle": "PAUSED",
                    "lifecycle_reason": cleaned,
                },
            )
        )
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("strategy_id", nargs="+")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--actor", default="operator")
    parser.add_argument("--journal", default=None)
    parser.add_argument("--strategy-dir", default=None)
    parser.add_argument(
        "--library", default=None, help="alias for --strategy-dir", dest="_library"
    )
    args = parser.parse_args(argv)

    directory = Path(args._library or args.strategy_dir or "runtime/autopoiesis/strategies")
    journal = JsonlJournal(Path(args.journal)) if args.journal else None
    library = StrategyLibrary(directory)

    failures = 0
    for strategy_id in args.strategy_id:
        try:
            updated = pause_strategy(
                library=library,
                journal=journal,
                strategy_id=strategy_id,
                reason=args.reason,
                actor=args.actor,
            )
        except StrategyPauseRefused as exc:
            print(f"REFUSED {strategy_id}: {exc}")
            failures += 1
            continue
        print(f"PAUSED  {updated.strategy_id}  {updated.lifecycle}  {updated.lifecycle_reason}")
    if journal is not None:
        print(json.dumps({"journaled_to": str(journal.path), "failures": failures}))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
