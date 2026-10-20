from __future__ import annotations

import errno
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def ensure_private(path: Path | str, *, mode: int) -> None:
    """Narrow a path to `mode`, and say nothing when it is already gone.

    Private has to be established where a file is *created*, not only on the service that happens to
    create most of them. Measured 2026-10-20: `doctor`, run by the gate on a fresh checkout, created
    `runtime/autopoiesis` and a journal lock with the process umask - 0755 and 0644 - so a runtime with
    no account data in it was still readable by every local user, and the gate class that guards the
    real one failed on a tree that had never traded.

    `FileNotFoundError` is the only refusal swallowed: a caller that lost the race to delete the file
    has nothing left to protect, and every other failure is the caller's to see.
    """
    try:
        os.chmod(path, mode)
    except FileNotFoundError:
        return


def narrow_private(path: Path | str, *, mode: int) -> None:
    """Best-effort narrowing of a directory this runtime owns, never failing a write over it.

    Separate from `ensure_private` because the two failures mean different things. A *file* this code
    just wrote is ours, so anything but a delete race is the caller's to see. A *directory* may not be
    ours at all - the first version narrowed `parent.parent`, which is `/tmp` for a probe writing
    `/tmp/x/journal.jsonl` and the home directory for a journal a few levels down. It raised
    PermissionError on `/tmp` and would silently have narrowed a home directory, which is worse than
    the leak it was fixing.
    """
    try:
        os.chmod(path, mode)
    except OSError:
        return


def write_text_atomic(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Replace a file's contents without ever exposing a partial read.

    `Path.write_text` truncates first and writes second, so a concurrent reader
    can observe a 0-byte or half-written file. The strategy library is re-read
    from disk on every trading cycle, and a torn read there made
    StrategyLibrary.list's `except Exception: continue` silently drop the
    strategy for that cycle, producing a phantom HOLD.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding=encoding,
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
        _fsync_dir(target.parent)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def write_json_atomic(path: Path | str, payload: object, *, indent: int | None = 2) -> None:
    write_text_atomic(path, json.dumps(payload, indent=indent, sort_keys=indent is not None) + "\n")


def read_json(path: Path | str) -> object | None:
    """Read JSON, returning None for a missing, empty, or corrupt file."""
    target = Path(path)
    if not target.exists():
        return None
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


@contextmanager
def file_lock(path: Path | str, *, exclusive: bool = True, timeout: float = 10.0) -> Iterator[None]:
    """Advisory lock guarding a shared runtime file.

    auto-fix.sh and auto_reviewer.py both wrote the same four core strategy
    files, with no lock against each other or against a running daemon.
    """
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        # The lock file is in the runtime directory and its name alone says a journal exists.
        ensure_private(lock_path, mode=0o600)
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            import time

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                time.sleep(0.05)
                try:
                    fcntl.flock(fd, mode | fcntl.LOCK_NB)
                    break
                except OSError:
                    continue
            else:
                raise TimeoutError(f"could not acquire lock on {lock_path}") from exc
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
