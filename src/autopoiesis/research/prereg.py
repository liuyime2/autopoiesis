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
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Private by default: a preregistered spec is a research contract, and the runtime
# directory that holds it is world-readable by default on this host. Narrow on
# creation so the gate's runtime-files-private class never has to chase it.
try:
    from autopoiesis.atomicio import ensure_private as _ensure_private
except Exception:  # pragma: no cover - atomicio is a core module
    _ensure_private = None

DEFAULT_REGISTRY = Path("runtime") / "autopoiesis" / "preregistered"


def registry_path(registry: Path | None = None) -> Path:
    """Runtime storage relative to the working directory, never to installed source."""
    return Path(registry if registry is not None else
                os.getenv("AUTOPOIESIS_PREREGISTRY", str(DEFAULT_REGISTRY)))

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


def load_spec_from_text(text: str) -> dict[str, str]:
    """Parse frozen declaration bytes without writing a temporary file."""
    fields: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"invalid spec line: {line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        if key in fields:
            raise ValueError(f"duplicate spec field: {key}")
        fields[key] = value.strip()
    return fields


def load_spec(path: Path) -> dict[str, str]:
    return load_spec_from_text(Path(path).read_text(encoding="utf-8"))


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


def register(spec_path: Path, registry: Path | None = None) -> dict:
    """Register immutable bytes and metadata; identical registration keeps its first time."""
    spec_path = Path(spec_path)
    frozen = spec_path.read_bytes()
    spec = load_spec_from_text(frozen.decode("utf-8"))
    problems = check_spec(spec)
    if problems:
        raise SystemExit("the spec cannot be registered:\n  " + "\n  ".join(problems))
    registry = registry_path(registry)
    digest = hashlib.sha256(frozen).hexdigest()
    target = registry / f"{digest}.spec"
    metadata = registry / f"{digest}.json"
    if target.exists() or metadata.exists():
        ok, problems, _payload = _verify_bytes(frozen, registry)
        if not ok:
            raise ValueError("existing registration is corrupt: " + "; ".join(problems))
        return _payload
    registry.mkdir(parents=True, exist_ok=True)
    if _ensure_private is not None:
        _ensure_private(registry, mode=0o700)
    payload = {
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "sha256": digest,
        "source": str(spec_path),
        "fields": spec,
    }
    # Exclusive creation refuses to rewrite history, including interrupted registrations.
    with target.open("xb") as handle:
        handle.write(frozen)
        handle.flush()
        os.fsync(handle.fileno())
    with metadata.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    if _ensure_private is not None:
        _ensure_private(target, mode=0o600)
        _ensure_private(metadata, mode=0o600)
    return payload


def _verify_bytes(frozen: bytes, registry: Path | None = None) -> tuple[bool, list[str], dict]:
    digest = hashlib.sha256(frozen).hexdigest()
    registry = registry_path(registry)
    registered = registry / f"{digest}.spec"
    metadata = registry / f"{digest}.json"
    if not registered.exists():
        return False, [f"no spec registered under {digest}; a hypothesis stated after the fact "
                       "is a description of the data, not a hypothesis"], {}
    try:
        if registered.read_bytes() != frozen:
            return False, ["the registered copy differs from the file on disk"], {}
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        fields = load_spec_from_text(frozen.decode("utf-8"))
        if payload.get("sha256") != digest or payload.get("fields") != fields:
            return False, ["registration metadata does not match spec bytes and fields"], {}
        registered_at = _parse_utc(payload["registered_at"])
        if registered_at > datetime.now(timezone.utc):
            return False, ["registration timestamp is in the future"], {}
        if not isinstance(payload.get("source"), str) or not payload["source"]:
            return False, ["registration metadata has no source"], {}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return False, [f"invalid registration metadata: {exc}"], {}
    problems = check_spec(fields)
    return not problems, problems, payload


def load_verified_spec(spec_path: Path, registry: Path | None = None) -> tuple[dict, str, dict]:
    """One immutable read controls registration checks, execution fields and identity."""
    frozen = Path(spec_path).read_bytes()
    ok, problems, registration = _verify_bytes(frozen, registry)
    if not ok:
        raise ValueError("unverified research spec: " + "; ".join(problems))
    return (load_spec_from_text(frozen.decode("utf-8")), hashlib.sha256(frozen).hexdigest(),
            registration)


def verify(spec_path: Path, registry: Path | None = None) -> tuple[bool, list[str]]:
    """Verify the same frozen bytes against both stored bytes and first-registration metadata."""
    ok, problems, _registration = _verify_bytes(Path(spec_path).read_bytes(), registry)
    return ok, problems


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
        if not isinstance(timestamp, datetime):
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
        if (isinstance(price, bool) or not isinstance(price, (int, float))
                or not math.isfinite(price) or price <= 0):
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
    reg.add_argument("--registry", default=None)

    ver = sub.add_parser("verify", help="check a spec against its registration")
    ver.add_argument("spec")
    ver.add_argument("--registry", default=None)

    chk = sub.add_parser("check-spec", help="check a spec is complete and checkable")
    chk.add_argument("spec")

    args = parser.parse_args(argv)
    if args.command == "register":
        payload = register(Path(args.spec), Path(args.registry) if args.registry else None)
        print(f"registered {payload['sha256'][:16]}... from {payload['source']}")
        print(f"  hypothesis: {payload['fields'].get('hypothesis')}")
        print(f"  instrument: {payload['fields'].get('instrument')} over "
              f"{payload['fields'].get('window_start_utc')} .. "
              f"{payload['fields'].get('window_end_utc')}")
        print(f"  family:     {payload['fields'].get('family')}")
        print("verify a run against it with: research/prereg.py verify <spec>")
        return 0

    if args.command == "verify":
        ok, problems = verify(Path(args.spec), Path(args.registry) if args.registry else None)
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
