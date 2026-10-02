"""One canonical way to read a typed value out of an untyped JSON-shaped value.

Journal payloads, broker SDK objects and LLM responses all arrive as
``dict[str, object]`` or as attribute bags: the shape is guaranteed, the static
type is not, so every reader has to narrow. Doing that ad hoc at each call site
repeats the same conversion rules in several modules and yields errors that
never say which field was bad.

This module is the single implementation of the *shared* conversions. It exists
because three modules had private copies of the same ISO/epoch timestamp reader
(``scheduler``, ``broker_evidence``, ``executor``); those are merged here.

Deliberately **not** merged here: readers whose failure policy differs. Turning a
bad field into ``0.0`` (``fill_reconciler``), into ``None``
(``broker_evidence``) and into ``BrokerDataUnavailable`` (``data_gateway``) are
three different contracts, and collapsing them behind a flag would hide which one
a caller depends on. Those stay at their call sites.

Every reader raises ``ValueError`` naming the field and the offending value, so a
corrupt record fails loudly at the boundary instead of silently becoming a zero
somewhere downstream.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TypeGuard


class FieldError(ValueError):
    """A payload field could not be read as its declared type."""


def _fail(field: str, value: object, expected: str) -> FieldError:
    return FieldError(f"{field}: cannot read {expected} from {value!r}")


def field_of(obj: object, name: str, default: object = None) -> object:
    """Read a named field from a mapping *or* an object, whichever it is.

    Three modules carried their own copy. Two were byte-identical
    ``isinstance(obj, dict)`` versions; the third used ``Mapping`` and explained
    why - ``JsonlJournal.read_events`` returns ``JournalEvent`` objects rather
    than dicts, so the first cut's ``obj.get(...)`` raised ``AttributeError``
    on every real call, and the daemon swallowed it into a FAILED event: a
    screen that looked wired and was never reached. The ``Mapping`` version is
    the one that was right, so it is the one that survives.
    """
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def is_number(value: object) -> TypeGuard[int | float]:
    """True for a real number.

    ``bool`` is excluded on purpose: ``isinstance(True, int)`` is true, so a
    boolean that reached a numeric field would silently become 1 or 0.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def datetime_utc(value: object) -> datetime | None:
    """Read a timestamp as an aware UTC ``datetime``, or ``None`` when absent.

    Accepts ``None``/``""``, an existing ``datetime``, a POSIX epoch number, or
    an ISO-8601 string with or without a trailing ``Z``. Naive input is assumed
    to be UTC, which is what both Alpaca and this project's journal emit; aware
    input is converted to UTC, so callers can subtract ``datetime.now(tz=utc)``
    without first checking the offset.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, bool):
        raise _fail("timestamp", value, "datetime")
    elif isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise _fail("timestamp", value, "ISO-8601 datetime or epoch") from None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def field_str(value: object, field: str, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def field_int(value: object, field: str, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            raise _fail(field, value, "int") from None
    raise _fail(field, value, "int")


def _read_float(value: object, field: str) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            raise _fail(field, value, "float") from None
    raise _fail(field, value, "float")


def field_float(value: object, field: str, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    return _read_float(value, field)


def field_float_or(value: object, field: str, default: float) -> float:
    """``field_float`` for a field the schema declares non-nullable.

    Kept separate so the nullability stays visible at the call site instead of
    being papered over with an ``or 0.0`` - which would also rewrite a genuine
    ``0.0`` into the default.
    """
    if value is None or value == "":
        return default
    return _read_float(value, field)


def field_dict(value: object, field: str) -> dict[str, object]:
    """A nested JSON object.

    Absent (``None``) reads as empty, because "this record has no pnl block" and
    "this record has an empty pnl block" mean the same thing to every caller that
    walks into it with ``.get``. A present-but-not-an-object value is an error:
    that is a schema break, not a missing section.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _fail(field, value, "object")
    return value


def field_count_dict(value: object, field: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise _fail(field, value, "dict[str, int]")
    return {str(key): field_int(item, f"{field}[{key}]") for key, item in value.items()}


def field_dict_tuple(value: object, field: str) -> list[dict[str, object]]:
    """A JSON array whose elements are objects.

    `field_str_tuple` is for arrays of strings; given an array of objects it
    raises on the first element, naming that element rather than the field.
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise _fail(field, value, "array of objects")
    return [field_dict(item, f"{field}[{index}]") for index, item in enumerate(value)]


def field_str_tuple(value: object, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple, set, frozenset)):
        raise _fail(field, value, "sequence of str")
    return tuple(field_str(item, f"{field}[{index}]") for index, item in enumerate(value))
