"""Register a hypothesis before the data is examined, and refuse a tape that cannot be judged.

What this is for. A hypothesis stated after seeing the data is not a hypothesis, it is a description
of the data - and the project has already measured what that costs: 47 price hypotheses through a
family-wise gate, none with a direction, which is what a search that keeps looking until something
finds something produces. Pre-registration is the mechanism that makes the difference checkable: the
spec is written and hashed before the run, and the run verifies it afterwards, so a spec edited to
match the result is a different file and the run says so.

Two things live here because they answer the same question - "can this experiment be trusted?" -
from either side:

**`register` / `verify`.** A spec is a small text file with the fields a reader needs to falsify the
claim: the hypothesis, the instrument, the data window, the rule, the primary metric, the family, and
the sample size the result will be judged on. Registering it writes a copy under
`runtime/autopoiesis/preregistered/` named by the spec's own SHA-256. `verify` recomputes the hash of
the file on disk and compares, so tampering after registration is detectable without trusting
anybody's word. The registered copy is the one the run must verify against.

**`check_tape`.** Data quality, before any verdict is computed from it. A tape with a duplicate
timestamp is a tape whose fingerprint does not identify it, because the fingerprint is over ordered
(timestamp, price) pairs. A non-monotonic one silently reorders a return series. A zero or negative
price makes every ratio downstream meaningless. A bar outside the declared window means the run
looked at data its spec did not claim, which is the look-ahead failure in its most literal form.
Each is a hard error rather than a warning, because a verdict computed from a broken tape is a
number with no meaning that looks exactly like one with some.

Nothing here promotes anything, and nothing here measures an edge. It is the gate before the
measurement, which is the part that makes the measurement worth something.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "runtime" / "autopoiesis" / "preregistered"

#: The fields a spec must carry to be falsifiable. Not a schema checked by a library: a reader (or a
#: reviewer, or an agent) is the one who needs to see these, and the list here is checked literally
#: so a spec missing one is refused rather than quietly under-specified.
REQUIRED_FIELDS = (
    "hypothesis",
    "family",
    "instrument",
    "window_start_utc",
    "window_end_utc",
    "rule",
    "primary_metric",
    "expected_trades",
)


@dataclass
class TapeReport:
    """What checking a tape found, as a list of problems rather than a verdict."""

    bars: int = 0
    problems: list[str] = field(default_factory=list)
    first: datetime | None = None
    last: datetime | None = None
    span_days: float = 0.0

    @property
    def usable(self) -> bool:
        return not self.problems

    def render(self, limit: int = 6) -> str:
        """The report. `limit` bounds how many problems are printed, and the rest are counted.

        A gate that prints one line per defect is unusable on exactly the tapes that need it: the
        first run of this against the real cache had 110,064 out-of-window bars and printed every
        one, which buried the finding - that the search's tape spans a decade the spec never
        declared - in a hundred thousand lines of the same sentence.
        """
        if self.first:
            head = (f"  bars: {self.bars} over {self.span_days:.1f} days "
                    f"({self.first} .. {self.last})")
        else:
            head = f"  bars: {self.bars}"
        lines = [head]
        if not self.problems:
            lines.append("  usable")
            return "\n".join(lines)
        lines.append(f"  {len(self.problems)} problem(s):")
        for problem in self.problems[:limit]:
            lines.append(f"    - {problem}")
        if len(self.problems) > limit:
            kinds: dict[str, int] = {}
            for problem in self.problems:
                key = problem.split(" is ", 1)[-1].split(" at ", 1)[0][:40]
                kinds[key] = kinds.get(key, 0) + 1
            summary = ", ".join(f"{count}x {kind}" for kind, count in sorted(
                kinds.items(), key=lambda kv: -kv[1]))
            lines.append(f"    ... and {len(self.problems) - limit} more, by kind: {summary}")
        return "\n".join(lines)


def _parse_utc(value: str) -> datetime:
    """A declared window bound. Must be an instant with a timezone, not a bare date."""
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError(
            f"{value!r} has no timezone; a window bound without one cannot be compared to a tape"
        )
    return moment


def load_spec_from_text(text: str) -> dict:
    """Parse a spec from text rather than a file, so a test can hold one inline.

    The file path is the same parser: this writes nothing and calls the same field reader, so a
    test's spec and a registered one cannot diverge in what they mean.
    """
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".spec", delete=False,
                                     encoding="utf-8") as handle:
        handle.write(text)
        name = handle.name
    try:
        return load_spec(Path(name))
    finally:
        Path(name).unlink(missing_ok=True)


def load_spec(path: Path) -> dict:
    """A spec file, as its key/value fields.

    Text `key: value` lines, one per field, with `#` comments and blank lines ignored. Not JSON,
    because a spec is read by a person as often as by a program and a format that needs a parser to
    read is a format nobody proofs.
    """
    fields: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def spec_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_spec(spec: dict) -> list[str]:
    """What a spec must carry to be checkable afterwards."""
    missing = [name for name in REQUIRED_FIELDS if not spec.get(name)]
    problems = [f"spec has no {name}, so the claim it makes cannot be checked" for name in missing]
    for name in ("window_start_utc", "window_end_utc"):
        if spec.get(name):
            try:
                _parse_utc(spec[name])
            except ValueError as exc:
                problems.append(f"{name}: {exc}")
    if spec.get("window_start_utc") and spec.get("window_end_utc") and len(problems) <= 1:
        try:
            if _parse_utc(spec["window_start_utc"]) >= _parse_utc(spec["window_end_utc"]):
                problems.append("window_start_utc is not before window_end_utc")
        except ValueError:
            pass  # already reported above
    return problems


def register(spec_path: Path, registry: Path = DEFAULT_REGISTRY) -> dict:
    """Copy a spec into the registry, named by its own hash, and return what was recorded.

    The registry entry is the thing a later run verifies against, so a spec edited after
    registration no longer matches it - which is the whole mechanism. Registering twice records the
    second spec too rather than refusing: two different specs are two different experiments, and
    silently dropping one would hide that a hypothesis moved.
    """
    spec_path = Path(spec_path)
    spec = load_spec(spec_path)
    problems = check_spec(spec)
    if problems:
        raise SystemExit("the spec cannot be registered:\n  " + "\n  ".join(problems))

    digest = spec_hash(spec_path)
    registry.mkdir(parents=True, exist_ok=True)
    target = registry / f"{digest}.spec"
    target.write_text(spec_path.read_text(encoding="utf-8"), encoding="utf-8")
    payload = {
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "sha256": digest,
        "source": str(spec_path),
        "fields": {name: spec.get(name) for name in REQUIRED_FIELDS},
    }
    (registry / f"{digest}.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def verify(spec_path: Path, registry: Path = DEFAULT_REGISTRY) -> tuple[bool, list[str]]:
    """Is this spec byte-identical to the one registered for its own hash?"""
    spec_path = Path(spec_path)
    digest = spec_hash(spec_path)
    registered = registry / f"{digest}.spec"
    if not registered.exists():
        return False, [
            f"no spec registered under {digest}; a hypothesis stated after the fact is a "
            "description of the data, not a hypothesis"
        ]
    if registered.read_bytes() != spec_path.read_bytes():
        return False, ["the registered copy differs from the file on disk"]
    problems = check_spec(load_spec(spec_path))
    if problems:
        return False, [f"the registered spec is not checkable: {problem}" for problem in problems]
    return True, []


def check_tape(bars, spec: dict | None = None) -> TapeReport:
    """The data-quality gate: is this series one that can be judged at all?

    `bars` is a sequence of objects with `.timestamp` and `.price` - the same `Bar` the driver and
    the fingerprint use, so this is the same data those see rather than a second copy of it.

    Checks, each of which is a hard error because each makes every downstream number meaningless
    while looking exactly like a number:

    - **Non-monotonic timestamps.** The fingerprint is over the *ordered* pairs, so a tape with an
      out-of-order pair fingerprints as a series that was never actually fetched.
    - **Duplicate timestamps.** Two bars at one instant is two prices at one instant, and the
      fingerprint cannot distinguish the series from the one with either of them.
    - **A price that is not a usable positive number.** Every ratio, return and cost estimate is
      computed from it, so a zero or a NaN does not produce a bad answer, it produces an unanswerable
      one.
    - **A bar outside the declared window.** The spec claims a window; a bar outside it is data the
      hypothesis did not say it would see, which is look-ahead in its most literal form.
    """
    report = TapeReport(bars=len(bars))
    seen: set = set()
    previous: datetime | None = None
    start = end = None
    if spec:
        try:
            start = _parse_utc(spec["window_start_utc"])
            end = _parse_utc(spec["window_end_utc"])
        except (KeyError, ValueError):
            start = end = None  # an unstated window is not a reason to reject the tape

    for index, bar in enumerate(bars):
        timestamp = getattr(bar, "timestamp", None)
        # `regime.Bar` names it `price`; `replay.ReplayBar` names it `close`. Both are the same
        # number to everything downstream - the driver builds one from the other - and a gate that
        # knew only one spelling would report every real tape as unusable, which is what the first
        # version of this did against the live cache.
        price = getattr(bar, "price", None)
        if price is None:
            price = getattr(bar, "close", None)
        if timestamp is None:
            report.problems.append(f"bar {index} has no timestamp")
            continue
        if timestamp.tzinfo is None:
            report.problems.append(f"bar {index} has a timestamp with no timezone")
            continue
        if previous is not None and timestamp < previous:
            report.problems.append(
                f"bar {index} at {timestamp} precedes bar {index - 1} at {previous}; "
                "the series is not chronological, and the fingerprint is over ordered pairs"
            )
        previous = timestamp
        if timestamp in seen:
            report.problems.append(f"bar {index} repeats the timestamp {timestamp}")
        seen.add(timestamp)
        if not isinstance(price, (int, float)) or price != price or price <= 0:
            report.problems.append(f"bar {index} at {timestamp} has an unusable price {price!r}")
        if start is not None and timestamp < start:
            report.problems.append(f"bar {index} at {timestamp} is before the declared window")
        if end is not None and timestamp > end:
            report.problems.append(f"bar {index} at {timestamp} is after the declared window")

    stamps = sorted(seen) if seen else []
    if stamps:
        report.first, report.last = stamps[0], stamps[-1]
        report.span_days = (stamps[-1] - stamps[0]).total_seconds() / 86400.0
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)

    reg = sub.add_parser("register", help="register a spec before the run")
    reg.add_argument("spec")
    reg.add_argument("--registry", default=str(DEFAULT_REGISTRY))

    ver = sub.add_parser("verify", help="check a spec against its registration")
    ver.add_argument("spec")
    ver.add_argument("--registry", default=str(DEFAULT_REGISTRY))

    chk = sub.add_parser("check-spec", help="check a spec is complete and checkable")
    chk.add_argument("spec")

    args = parser.parse_args(argv)
    if args.command == "register":
        payload = register(Path(args.spec), Path(args.registry))
        print(f"registered {payload['sha256'][:16]}... from {payload['source']}")
        print(f"  hypothesis: {payload['fields'].get('hypothesis')}")
        print(f"  instrument: {payload['fields'].get('instrument')} over "
              f"{payload['fields'].get('window_start_utc')} .. "
              f"{payload['fields'].get('window_end_utc')}")
        print(f"  family:     {payload['fields'].get('family')}")
        print("verify a run against it with: research/prereg.py verify <spec>")
        return 0

    if args.command == "verify":
        ok, problems = verify(Path(args.spec), Path(args.registry))
        if ok:
            print("verify: the spec is byte-identical to the one registered before the run")
            return 0
        print("verify: the spec does not match its registration")
        for problem in problems:
            print(f"  {problem}")
        return 1

    problems = check_spec(load_spec(Path(args.spec)))
    if problems:
        print("check-spec: the spec is not checkable")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("check-spec: the spec carries everything a reader needs to check the claim")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
