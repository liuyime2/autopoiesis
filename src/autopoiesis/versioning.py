"""A single digest summarising the code, config, model, prompt and schema in force.

Step 1.10 of docs/EXECUTION_PLAN_2026-11-05.md: a strategy version must move
when *any* of those moves, including the environment file (which the old
source-fingerprint did not cover). Secrets are never included.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from autopoiesis.config import AgentConfig
from autopoiesis.models import STRATEGY_PARAMETER_SCHEMAS


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _config_summary(config: AgentConfig | None) -> dict[str, Any]:
    """Every scalar config field except credentials and paths to them.

    The first version read `config.model_dump()`, which a dataclass does not have, so the
    summary was always `{}` and the config digest was the hash of an empty object: a change
    to the env file never changed the version digest (measured 2026-11-07).
    """
    if config is None:
        config = AgentConfig.from_env()
    secret_keys = {"api_key", "secret", "token", "password", "env_file"}
    out: dict[str, Any] = {}
    for item in fields(config):
        key = item.name
        if any(s in key.lower() for s in secret_keys):
            continue
        value = getattr(config, key)
        if isinstance(value, frozenset):
            value = sorted(value)
        if isinstance(value, (str, int, float, bool, tuple, list)):
            out[key] = list(value) if isinstance(value, tuple) else value
    return out


def _prompt_digest() -> str:
    # Hash the curriculum module, which owns the prompt.
    path = Path(__file__).resolve().parent / "curriculum.py"
    try:
        return _hash_bytes(path.read_bytes())
    except OSError:
        return "unknown"


def _schema_digest() -> str:
    return _hash_bytes(json.dumps(STRATEGY_PARAMETER_SCHEMAS, sort_keys=True, default=str).encode())


def version_summary(config: AgentConfig | None = None) -> dict[str, Any]:
    cfg = _config_summary(config)
    cfg_blob = json.dumps(cfg, sort_keys=True, default=str).encode()
    from autopoiesis.daemon import source_fingerprint

    source_fp = source_fingerprint()
    model = cfg.get("model", "")
    components = {
        "source_fingerprint": source_fp,
        "config_digest": _hash_bytes(cfg_blob),
        "model": model,
        "prompt_digest": _prompt_digest(),
        "schema_digest": _schema_digest(),
    }
    digest_value = hashlib.sha256(
        json.dumps(components, sort_keys=True).encode()
    ).hexdigest()[:16]
    components["digest"] = digest_value
    return components
