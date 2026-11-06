"""A single digest summarising the code, config, model, prompt and schema in force.

Step 1.10 of docs/EXECUTION_PLAN_2026-11-05.md: a strategy version must move
when *any* of those moves, including the environment file (which the old
source-fingerprint did not cover). Secrets are never included.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from autopoiesis.config import AgentConfig
from autopoiesis.models import STRATEGY_PARAMETER_SCHEMAS


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _config_summary(config: AgentConfig | None) -> dict[str, Any]:
    if config is None:
        try:
            config = AgentConfig.from_env()
        except Exception:
            return {}
    # Exclude anything that looks like a credential or a path to one.
    secret_keys = {"api_key", "secret", "token", "password", "env_file"}
    out: dict[str, Any] = {}
    for key, value in (config.model_dump() if hasattr(config, "model_dump") else {}).items():
        if any(s in key.lower() for s in secret_keys):
            continue
        if isinstance(value, (str, int, float, bool, tuple, list)):
            out[key] = value
    return out


def _prompt_digest() -> str:
    # Hash the curriculum module, which owns the prompt.
    path = Path(__file__).resolve().parent / "curriculum.py"
    try:
        return _hash_bytes(path.read_bytes())
    except OSError:
        return "unknown"


def _schema_digest() -> str:
    try:
        raw = json.dumps(STRATEGY_PARAMETER_SCHEMAS, sort_keys=True, default=str).encode()
    except Exception:
        return "unknown"
    return _hash_bytes(raw)


def version_summary(config: AgentConfig | None = None) -> dict[str, Any]:
    cfg = _config_summary(config)
    cfg_blob = json.dumps(cfg, sort_keys=True, default=str).encode()
    source_fp = "unknown"
    try:
        from autopoiesis.daemon import source_fingerprint

        source_fp = source_fingerprint()
    except Exception:
        # Mirror without importing the package.
        package_dir = Path(__file__).resolve().parent
        digest = hashlib.sha256()
        for path in sorted(package_dir.glob("*.py")):
            digest.update(path.name.encode())
            try:
                digest.update(path.read_bytes())
            except OSError:
                continue
        source_fp = digest.hexdigest()[:16]

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
