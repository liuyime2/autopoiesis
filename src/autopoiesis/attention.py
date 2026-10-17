"""Which symbols are worth a look, chosen by the model from the broker's own candidates.

Measured 2026-10-14. `DataGateway.attention_symbols` takes the top `k` of the broker's
most-actives and movers after quality filters, mechanically. The review's finding was that
the watchlist was therefore chosen by a volume sort, and the plan's answer is that the model
should choose from that list and say why.

What this module is careful about, because a model choosing what the system looks at is a
place where a plausible answer is not the same as a valid one:

- **The choice is constrained to the candidates.** A symbol the model names that was not
  offered is dropped, not fetched. A model that can widen the universe is a model that can
  make the system look anywhere, including at instruments the quality filter just removed.
- **At most `k`.** A model that returns forty symbols has not chosen.
- **A reason is required, per symbol.** An unexplained choice is a choice nobody can review,
  and the plan asks for the reasons specifically so the choice can be evaluated later.
- **Any failure falls back to the mechanical list.** The model is an improvement on a volume
  sort, not a dependency: a timeout, a malformed reply, or an empty selection leaves the
  caller with exactly what it had before. This is the same discipline the rest of the
  decision path keeps - a missing model must not degrade the system into a guess.

Pure standard library. Calls a transport the caller supplies; touches no order.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

#: What the model is asked to return. Kept to two fields so a small model can hold it.
ATTENTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "required": ["choices"],
    "properties": {
        "choices": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["symbol", "reason"],
                "properties": {
                    "symbol": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}


@dataclass(frozen=True)
class AttentionChoice:
    """The symbols to look at this round, and where the choice came from."""

    symbols: list[str]
    reasons: dict[str, str] = field(default_factory=dict)
    #: "model" when the model chose within the constraints, "mechanical" when it did not and
    #: the caller's own list stands. Recorded so a later reader can tell the two apart -
    #: evaluating whether model-chosen symbols are more informative needs to know which
    #: rounds the model actually chose.
    source: str = "mechanical"
    #: Why the mechanical list stands, when it does. Empty when the model chose.
    note: str = ""


def build_prompt(candidates: Sequence[str], k: int, context: dict | None = None) -> str:
    """The question, with the constraint stated where the choice is made."""
    lines = [
        f"Choose at most {k} symbol(s) from the list below for the system to watch this "
        f"round. Every choice needs a reason. You may choose fewer than {k}, and choosing "
        f"none is allowed.",
        "You may only choose from this list. A symbol not on it will be ignored.",
        "Candidates: " + ", ".join(candidates),
    ]
    if context:
        lines.append("Context: " + ", ".join(f"{key}={value}" for key, value in sorted(context.items())))
    return "\n".join(lines)


def select_attention(
    candidates: Sequence[str],
    *,
    k: int,
    transport: Callable[[str, dict], object] | None,
    context: dict | None = None,
) -> AttentionChoice:
    """The model's choice from `candidates`, or the mechanical first `k` if it cannot choose.

    Never raises. A caller in the trading loop must not lose a cycle to a model that is
    slow, absent or wrong about the schema.
    """
    mechanical = list(candidates)[:max(0, k)]
    if transport is None or k <= 0 or not candidates:
        return AttentionChoice(symbols=mechanical, note="no model asked")

    offered = {s.upper() for s in candidates}
    try:
        raw = transport(build_prompt(candidates, k, context), ATTENTION_SCHEMA)
        choices = _parse(raw)
    except Exception as exc:
        return AttentionChoice(
            symbols=mechanical, note=f"model did not answer: {type(exc).__name__}"
        )
    if choices is None:
        return AttentionChoice(symbols=mechanical, note="model reply had no usable choices")

    selected: list[str] = []
    reasons: dict[str, str] = {}
    dropped: list[str] = []
    for symbol, reason in choices:
        upper = symbol.upper()
        if upper not in offered:
            dropped.append(upper)
            continue
        if upper in selected:
            continue
        if len(selected) >= k:
            dropped.append(upper)
            continue
        if not reason.strip():
            dropped.append(upper)
            continue
        selected.append(upper)
        reasons[upper] = reason.strip()

    if not selected:
        note = "model chose nothing admissible"
        if dropped:
            note += f"; dropped {', '.join(sorted(set(dropped))[:5])}"
        return AttentionChoice(symbols=mechanical, note=note)
    note = ""
    if dropped:
        note = f"dropped {', '.join(sorted(set(dropped))[:5])}"
    return AttentionChoice(symbols=selected, reasons=reasons, source="model", note=note)


def _parse(raw: object) -> list[tuple[str, str]] | None:
    """`[(symbol, reason)]` from the reply, or None if it carries none.

    Accepts a dict, a JSON string, or an object with a `response`/`choices` attribute, so
    the caller does not have to know which transport it passed.
    """
    payload = raw
    if isinstance(payload, str):
        import json

        text = payload
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            payload = json.loads(text[start : end + 1])
        except ValueError:
            return None
    if hasattr(payload, "response"):
        return _parse(payload.response)
    if not isinstance(payload, dict):
        return None
    # The transport hands back the parsed Ollama body: a dict whose answer is under
    # `response`, as JSON text. `hasattr` above does not see a dict key, so without this
    # the reader looked for `choices` at the top level, found none, and every round fell
    # back to the mechanical list. Measured 2026-10-15: nine ATTENTION_SELECTED events in
    # one session, all `source=mechanical`, all "model reply had no usable choices".
    if "choices" not in payload and isinstance(payload.get("response"), str):
        return _parse(payload["response"])
    entries = payload.get("choices")
    if not isinstance(entries, list):
        return None
    out: list[tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        symbol = entry.get("symbol")
        reason = entry.get("reason")
        if isinstance(symbol, str) and isinstance(reason, str):
            out.append((symbol, reason))
    return out
