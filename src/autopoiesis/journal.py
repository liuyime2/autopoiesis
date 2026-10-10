from __future__ import annotations

import contextlib
import itertools
import os
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from autopoiesis.atomicio import file_lock
from autopoiesis.models import CycleRecord, JournalEvent

# Substring prefilter, verified against the recorded journal. A CycleRecord
# always carries `execution` and never carries `event_id`; a JournalEvent is the
# other way round. Without this, validating line by line spends nearly all its
# time constructing pydantic ValidationErrors for the ~84% of lines that are
# events rather than cycles.
_CYCLE_MARKER = '"execution":'
_EVENT_MARKER = '"event_id":'


class RetainedWindow(TypedDict):
    """What `retained_window` returns, spelled out rather than left as `object`.

    It was `dict[str, object]`, which pushed the cost of the missing type onto the reader:
    the doctor had to `str()` and slice values it already knew were strings, and mypy
    reported `Value of type "object" is not indexable` on the one line that indexes. The
    shape is fixed at five keys, so it is declared here once and both readers are checked
    against it.
    """

    cycles: int
    oldest: str | None
    newest: str | None
    generations: int
    truncated: bool | None


class JsonlJournal:
    def __init__(
        self,
        path: Path | str,
        *,
        max_bytes: int = 64 * 1024 * 1024,
        backups: int = 3,
        lock: bool = True,
        archive_dir: Path | str | None = None,
    ):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backups = backups
        self.use_lock = lock
        self.last_dropped_lines = 0
        self.last_dropped_samples: list[str] = []
        # Where a generation goes when the rotation horizon reaches it. Same filesystem as the
        # journal on purpose, so the move is an `os.replace` and not a copy: there is no window
        # in which the history exists in only one of the two places.
        #
        # `None` means no archive, which is what it used to be unconditionally: the oldest
        # generation was unlinked and the history was gone. See `_rotate_if_needed`.
        self.archive_dir = (
            Path(archive_dir)
            if archive_dir is not None
            else self.path.parent / "journal-archive"
        )

    def append(self, record: CycleRecord) -> None:
        self._append_json(record.model_dump_json())

    def append_event(self, event: JournalEvent) -> None:
        self._append_json(event.model_dump_json())

    def read_all(self) -> list[CycleRecord]:
        records, _ = self._scan(CycleRecord, limit=None)
        return records

    def last_n(self, n: int) -> list[CycleRecord]:
        records, _ = self._scan(CycleRecord, limit=n)
        return records

    def read_events(self, event_type: str | None = None, limit: int | None = None) -> list[JournalEvent]:
        normalized = event_type.strip().upper() if event_type else None
        events, _ = self._scan(
            JournalEvent,
            limit=limit,
            keep=lambda event: normalized is None or event.event_type == normalized,
        )
        return events

    def count_submitted_on(self, day) -> int:
        """Count SUBMITTED executions on a UTC date without materialising records.

        `TradeCounter` called read_all() on every trading cycle, so its cost was
        the entire journal: 0.73s at 16.9MB and growing linearly. A cheap
        substring test first means only actual cycle lines get validated.
        """
        if not self.path.exists():
            return 0
        count = 0
        self.last_dropped_lines = 0
        date_text = day.isoformat()
        # Across every generation, not just the live file. This is the daily trade cap that
        # Guardian enforces, and it read only `journal.jsonl`: a rotation partway through a
        # session moved the day's orders to `.1` and the counter returned 0, so the
        # 10-trades-per-day limit stopped binding for the rest of the day with nothing in any
        # record to show why. Reproduced in a scratch directory - 30 real SUBMITTED orders
        # spread over four generations, count 0.
        #
        # `_scan` was fixed for exactly this when rotation was first handled; this method
        # sits below it and reads the file directly, and its own docstring still names it as a
        # reader that concluded the agent had never run. A limit that can silently stop
        # limiting is worse than no limit, because the report says it is in force.
        paths = self.history_paths()
        if not paths:
            return 0
        with self._read_lock(), contextlib.ExitStack() as stack:
            handles = [stack.enter_context(p.open("r", encoding="utf-8")) for p in paths]
            for line in itertools.chain.from_iterable(handles):
                if _EVENT_MARKER in line or _CYCLE_MARKER not in line or date_text not in line:
                    continue
                try:
                    record = CycleRecord.model_validate_json(line)
                except Exception:
                    self._note_drop(line)
                    continue
                if record.snapshot.timestamp.date() != day:
                    continue
                if record.execution.status != "SUBMITTED":
                    continue
                if record.decision.action == "HOLD":
                    continue
                count += 1
        return count

    def retained_window(self, *, known_since: datetime | None = None) -> RetainedWindow:
        """The span of history still on disk, and whether it is the whole story.

        Rotation deletes the oldest generation, so the journal stops being the record of
        everything that happened and becomes a recent fragment. Nothing in `stats()` said
        so: the doctor reported "1179 cycles" as if it were a total, and on this host
        those cycles are only what four generations still hold - about 14 days at the
        measured 18.7 MB/day.

        That matters because several analyses are recomputed from the journal on every
        pass - model calibration (314 decisions), the experiment chain (123 strategies),
        champion history. Past the retention horizon they keep returning a confident
        number computed from a fragment, which is the worst failure mode available:
        silently wrong rather than loudly absent.

        `truncated` is judged against a `known_since` instant rather than by counting
        generations, because a generation boundary cannot distinguish "never had more
        history" from "had more and lost it". If the caller knows the system was already
        running at some earlier time and the oldest retained record is later than that,
        the intervening cycles are gone.
        """
        records = self.read_all()
        stamps = sorted(r.snapshot.timestamp for r in records if getattr(r.snapshot, "timestamp", None))
        window: RetainedWindow = {
            "cycles": len(records),
            "oldest": stamps[0].isoformat() if stamps else None,
            "newest": stamps[-1].isoformat() if stamps else None,
            "generations": len(self.history_paths()),
            "truncated": None,
        }
        if known_since is not None and stamps:
            window["truncated"] = stamps[0] > known_since
        return window

    def stats(self) -> dict[str, object]:
        if not self.path.exists():
            return {"exists": False, "path": str(self.path), "bytes": 0, "lines": 0}
        size = self.path.stat().st_size
        with self._read_lock(), self.path.open("rb") as handle:
            lines = sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b""))
        archived = self._archive_paths()
        return {
            "exists": True,
            "path": str(self.path),
            "bytes": size,
            "lines": lines,
            "max_bytes": self.max_bytes,
            "utilization": round(size / self.max_bytes, 6) if self.max_bytes else None,
            # Beside the live file rather than inside it: a reader looking at the live file's
            # utilization needs to know that the record it is looking at is not the whole one.
            # Without these two, the fact that 195 MiB of history sits one directory over is
            # invisible to every operator-facing surface, which is how a journal gets described
            # as "the record" while most of the record is elsewhere.
            "archive_dir": str(self.archive_dir),
            "archive_generations": len(archived),
            "archive_bytes": sum(p.stat().st_size for p in archived),
        }

    # --- internals -----------------------------------------------------------

    def history_paths(self) -> list:
        """Every generation of the journal, oldest first.

        `_rotate_if_needed` moves the live file to `.1` and shifts older generations up, so
        the current file alone stops being the record: on this host the rotation that fired
        during the refactor left 17 lines in `journal.jsonl` and 10,587 in `journal.jsonl.1`,
        and every reader that opened only the live path - `read_all`, `read_events`,
        `count_submitted_on`, `replay_audit.py`, `check_runtime_integrity.py` - concluded
        the agent had never run a cycle. That is the failure this method exists to prevent.

        The archive is read first, for the same reason. A generation the rotation horizon
        archived is still part of the record; a `history_paths` that ignored it would
        reintroduce the failure above in a place where it is much harder to see - the
        record would shrink, the doctor would keep reporting a window, and nothing would
        say the shrink happened.

        The journal is append-only and generations are strictly ordered by age, so reading
        oldest-first and in file order reconstructs the original sequence. A limit applied
        across the whole history still yields the most recent records, because the deque is
        filled in chronological order.
        """
        paths = self._archive_paths()
        # `.N` holds the oldest generation, so the backups read in descending index order
        # (oldest first) and the live file comes last because it is the newest.
        for index in range(self.backups, 0, -1):
            candidate = self.path.with_suffix(self.path.suffix + f".{index}")
            if candidate.exists():
                paths.append(candidate)
        if self.path.exists():
            paths.append(self.path)
        return paths

    def _archive_paths(self) -> list:
        """Archived generations, oldest first.

        The sequence number in the name is the rotation count at which the file was archived,
        so sorting by name sorts by age and the original append order is reconstructed without
        reading a single line. `r000000` is the oldest thing the journal ever held.

        It assumes the archive directory keeps its contents in order, which is true for
        everything the journal itself writes. An operator who moves an archived file by hand
        can break the order, which is why these files are named for their rotation rather
        than for their mtime: a rename that alters a number is visible, a rename that alters
        only a timestamp is not.
        """
        if not self.archive_dir.exists():
            return []
        archived = sorted(self.archive_dir.glob("*.jsonl"))
        return [p for p in archived if p.is_file()]

    def _scan(self, model, *, limit, keep=None):
        paths = self.history_paths()
        if not paths:
            return [], 0
        collected: deque | None = deque(maxlen=limit) if limit else None
        kept: list = []
        dropped = 0
        want_events = model is JournalEvent
        # Locked once across every generation: rotation replaces the live file, so taking
        # the lock per generation could interleave a rotation between reads and duplicate
        # or skip records.
        with self._read_lock(), contextlib.ExitStack() as stack:
          handles = [stack.enter_context(p.open("r", encoding="utf-8")) for p in paths]
          for line in itertools.chain.from_iterable(handles):
                stripped = line.strip()
                if not stripped:
                    continue
                is_event = _EVENT_MARKER in stripped
                is_cycle = (not is_event) and _CYCLE_MARKER in stripped
                if not is_event and not is_cycle:
                    # Neither shape: a torn write, a partial line from a hard
                    # kill, or schema drift. Previously dropped in silence with
                    # no counter, so this was invisible for the whole run.
                    dropped += 1
                    self._note_drop(stripped)
                    continue
                if is_event != want_events:
                    # A well-formed record of the other kind. Not an error.
                    continue
                try:
                    parsed = model.model_validate_json(stripped)
                except Exception:
                    dropped += 1
                    self._note_drop(stripped)
                    continue
                if keep is not None and not keep(parsed):
                    continue
                if collected is not None:
                    collected.append(parsed)
                else:
                    kept.append(parsed)
        self.last_dropped_lines = dropped
        return (list(collected) if collected is not None else kept), dropped

    def _note_drop(self, line: str) -> None:
        if len(self.last_dropped_samples) < 3:
            self.last_dropped_samples.append(line[:200])

    def _append_json(self, payload: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            self._rotate_if_needed(len(payload) + 1)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def _rotate_if_needed(self, incoming: int) -> None:
        if not self.path.exists():
            return
        if self.path.stat().st_size + incoming <= self.max_bytes:
            return
        oldest = self.path.with_suffix(self.path.suffix + f".{self.backups}")
        if self.backups <= 0:
            # `backups = 0` asks the journal to keep nothing but the live file. That is a
            # retention choice, not permission to destroy what is about to leave the horizon,
            # so the live file is archived and a fresh one starts. Deleting it here is how
            # this setting once turned "keep only the recent window" into "throw the record
            # away", which no caller that passes 0 has ever asked for.
            self._archive_generation(self.path)
            return
        if oldest.exists():
            self._archive_generation(oldest)
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_suffix(self.path.suffix + f".{index}")
            if source.exists():
                os.replace(source, self.path.with_suffix(self.path.suffix + f".{index + 1}"))
        os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))

    def _archive_generation(self, generation: Path) -> None:
        """Move a generation that is leaving the rotation horizon into the archive.

        It used to be `oldest.unlink(missing_ok=True)`, which is the whole of why the journal
        was not the record the README claimed. Rotation at 64 MiB x 3 generations holds about
        ten days of this host's record; the rotation after that deleted the oldest ten, and
        every derived figure - model calibration, the experiment chain, the strategy ledger -
        silently recomputed itself from the fragment that was left. `retained_window` and the
        doctor's WARN named the truncation after it had already happened.

        The move is `os.replace` into the archive directory on the same filesystem, so it is
        atomic: the history is in both places until the move commits, and in one place after.
        There is no copy that could fail half-written, and no window in which the record exists
        nowhere.

        When the archive cannot be written this raises and the append that triggered the
        rotation fails with it. That is deliberate and it is the most important line here: the
        alternative is to catch the error and delete the generation instead, which converts a
        loud "the disk is full" into a quiet "forty percent of the record is gone". A daemon
        that stops appending is recoverable by its operator; a daemon that destroys its own
        history to keep trading is not recoverable at all.
        """
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        sequence = len([p for p in self._archive_paths() if p.is_file()])
        while True:
            target = self.archive_dir / f"r{sequence:06d}.jsonl"
            if not target.exists():
                break
            sequence += 1
        os.replace(generation, target)
        # Durability of the move, not just of the write: without the directory fsync the rename
        # can be visible while the new name is not yet on disk, and a crash at that moment
        # leaves the generation under neither name.
        try:
            fd = os.open(self.archive_dir, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            # A filesystem that cannot fsync a directory still holds the rename in its cache.
            # The record moved; failing the append over it would trade a real loss for a
            # hypothetical one.
            pass

    def _locked(self):
        """Exclusive, for appends."""
        return self._read_lock(exclusive=True)

    def _read_lock(self, *, exclusive: bool = False):
        """Readers share; the appender excludes.

        Appends already hold this lock, but readers took none, so a scan running
        while the daemon was mid-append could observe a partial line and count it
        as corruption. That showed up as an intermittent test failure with the
        daemon live. A shared lock makes the two agree, so a reader either sees
        the file before an append or after it, never during.
        """
        if not self.use_lock:
            from contextlib import nullcontext

            return nullcontext()
        return file_lock(self.path.with_suffix(self.path.suffix + ".lock"), exclusive=exclusive)
