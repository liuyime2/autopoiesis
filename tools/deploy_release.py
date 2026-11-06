"""The single entry point for a deployment.

Step 0.2 of docs/EXECUTION_PLAN_2026-11-05.md: one lock, one directory per
commit, one record, and a market-hours refusal. Nothing else may install a
build. Concurrency is serialised with an exclusive file lock; a second deploy
started while the first holds the lock is refused rather than queued, so two
operators cannot race.

Usage:
    python3 tools/deploy_release.py --approved-by <name> [--emergency REASON]
    python3 tools/deploy_release.py --dry-run --approved-by <name>

The lock is held for the whole run. On success a read-only release directory
is created under releases/<short-sha>/ and an entry is appended to
docs/evidence/deployments.jsonl.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = ROOT / "runtime" / "deploy.lock"
RELEASES_DIR = ROOT / "releases"
DEPLOYMENTS_LEDGER = ROOT / "docs" / "evidence" / "deployments.jsonl"
ET = ZoneInfo("America/New_York")

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


def _git_sha() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        raise RuntimeError(f"could not read HEAD: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _short_sha(sha: str) -> str:
    return sha[:12]


def market_is_open(now: datetime | None = None) -> bool:
    """Whether the US equity market is open at `now` (ET, weekdays 09:30-16:00).

    `AUTOPOIESIS_MARKET_OPEN` overrides the computation for tests and for a
    host whose clock is unreliable; "1"/"true"/"yes" forces open, "0"/"false"/
    "no" forces closed.
    """
    override = os.environ.get("AUTOPOIESIS_MARKET_OPEN")
    if override is not None:
        return override.strip().lower() in {"1", "true", "yes", "on"}
    if now is None:
        now = datetime.now(tz=ET)
    else:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(ET)
    if now.weekday() >= 5:
        return False
    t = now.timetz().replace(tzinfo=None) if False else now.time()
    return MARKET_OPEN <= t < MARKET_CLOSE


def _last_deployed_commit() -> str | None:
    if not DEPLOYMENTS_LEDGER.exists():
        return None
    last = None
    for line in DEPLOYMENTS_LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("commit"):
            last = entry["commit"]
    return last


def _source_fingerprint() -> str:
    package_dir = ROOT / "src" / "autopoiesis"
    digest = hashlib.sha256()
    try:
        for path in sorted(package_dir.glob("*.py")):
            digest.update(path.name.encode("utf-8"))
            try:
                digest.update(path.read_bytes())
            except OSError:
                continue
    except OSError:
        return "unknown"
    return digest.hexdigest()[:16]


def deploy(
    *,
    approved_by: str,
    emergency: str | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    install_service: bool = False,
) -> dict[str, object]:
    """Run one deployment. Raises SystemExit on refusal."""
    if not approved_by or not approved_by.strip():
        raise SystemExit("refused: --approved-by is required")

    sha = _git_sha()
    short = _short_sha(sha)
    release_dir = RELEASES_DIR / short

    # Market-hours gate (emergency override requires a stated reason).
    if market_is_open(now=now) and not emergency:
        raise SystemExit(
            "refused: market is open; deploy only outside trading hours "
            "(or pass --emergency REASON)"
        )
    if emergency is not None and not emergency.strip():
        raise SystemExit("refused: --emergency requires a non-empty reason")

    rollback_to = _last_deployed_commit()

    if dry_run:
        return {
            "dry_run": True,
            "commit": sha,
            "release_dir": str(release_dir),
            "rollback_to": rollback_to,
            "market_open": market_is_open(now=now),
            "emergency": emergency,
        }

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            raise SystemExit("refused: another deploy holds the lock") from None

        # Re-check under the lock: a concurrent deploy may have just recorded.
        if release_dir.exists():
            raise SystemExit(f"refused: release directory already exists: {release_dir}")

        # Create a read-only worktree for this commit.
        RELEASES_DIR.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["git", "worktree", "add", "--detach", str(release_dir), sha],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            raise SystemExit(f"git worktree add failed: {proc.stderr.strip()}")

        # Make the release tree read-only (code only; .git file stays usable).
        for path in release_dir.rglob("*"):
            try:
                if path.is_file() or path.is_symlink():
                    path.chmod(0o444)
                elif path.is_dir():
                    path.chmod(0o555)
            except OSError:
                pass
        try:
            release_dir.chmod(0o555)
        except OSError:
            pass

        entry = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "commit": sha,
            "commit_short": short,
            "release_dir": str(release_dir),
            "approved_by": approved_by.strip(),
            "rollback_to": rollback_to,
            "emergency": emergency,
            "source_fingerprint": _source_fingerprint(),
            "market_open_at_deploy": market_is_open(now=now),
        }
        DEPLOYMENTS_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with DEPLOYMENTS_LEDGER.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

        if install_service and not dry_run:
            import subprocess as _sp
            env = dict(os.environ)
            env["AUTOPOIESIS_DEPLOY_VIA_RELEASE"] = "1"
            proc = _sp.run([str(release_dir / "autopoiesis"), "install-service"],
                           cwd=str(release_dir), env=env, capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                raise SystemExit(f"install-service failed: {proc.stderr.strip()}")
            entry["install_service"] = "ok"
        return entry
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-by", required=True, help="name of the approving operator")
    parser.add_argument(
        "--emergency", default=None, metavar="REASON", help="bypass market-hours refusal"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    parser.add_argument(
        "--install-service", action="store_true",
        help="after deploying, install systemd units from the release tree",
    )
    args = parser.parse_args(argv)

    # Allow a test clock via env.
    now = None
    if os.environ.get("AUTOPOIESIS_DEPLOY_NOW"):
        now = datetime.fromisoformat(os.environ["AUTOPOIESIS_DEPLOY_NOW"])

    try:
        result = deploy(
            approved_by=args.approved_by,
            emergency=args.emergency,
            dry_run=args.dry_run,
            now=now,
            install_service=args.install_service,
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

