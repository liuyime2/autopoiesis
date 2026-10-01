from __future__ import annotations

import contextlib
import itertools
import os
from collections import deque
from pathlib import Path

from min_agent.atomicio import file_lock
from min_agent.models import CycleRecord, JournalEvent

# Substring prefilter, verified against the recorded journal. A CycleRecord
# always carries `execution` and never carries `event_id`; a JournalEvent is the
# other way round. Without this, validating line by line spends nearly all its
# time constructing pydantic ValidationErrors for the ~84% of lines that are
# events rather than cycles.
_CYCLE_MARKER = '"execution":'
_EVENT_MARKER = '"event_id":'
_EVENT_MARKER_ABSENT = '"event_id":'


class JsonlJournal:
    def __init__(
        self,
        path: Path | str,
        *,
        max_bytes: int = 64 * 1024 * 1024,
        backups: int = 3,
        lock: bool = True,
    ):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backups = backups
        self.use_lock = lock
        self.last_dropped_lines = 0
        self.last_dropped_samples: list[str] = []

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

    def stats(self) -> dict[str, object]:
        if not self.path.exists():
            return {"exists": False, "path": str(self.path), "bytes": 0, "lines": 0}
        size = self.path.stat().st_size
        with self._read_lock(), self.path.open("rb") as handle:
            lines = sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b""))
        return {
            "exists": True,
            "path": str(self.path),
            "bytes": size,
            "lines": lines,
            "max_bytes": self.max_bytes,
            "utilization": round(size / self.max_bytes, 6) if self.max_bytes else None,
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

        The journal is append-only and generations are strictly ordered by age, so reading
        oldest-first and in file order reconstructs the original sequence. A limit applied
        across the whole history still yields the most recent records, because the deque is
        filled in chronological order.
        """
        # `.N` holds the oldest generation, so the backups read in descending index order
        # (oldest first) and the live file comes last because it is the newest.
        paths = []
        for index in range(self.backups, 0, -1):
            candidate = self.path.with_suffix(self.path.suffix + f".{index}")
            if candidate.exists():
                paths.append(candidate)
        if self.path.exists():
            paths.append(self.path)
        return paths

    def _scan(self, model, *, limit, keep=None):
        paths = self.history_paths()
        if not paths:
            return [], 0
        collected: deque = deque(maxlen=limit) if limit else None
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
        if self.backups <= 0:
            self.path.unlink()
            return
        oldest = self.path.with_suffix(self.path.suffix + f".{self.backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_suffix(self.path.suffix + f".{index}")
            if source.exists():
                os.replace(source, self.path.with_suffix(self.path.suffix + f".{index + 1}"))
        os.replace(self.path, self.path.with_suffix(self.path.suffix + ".1"))

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
