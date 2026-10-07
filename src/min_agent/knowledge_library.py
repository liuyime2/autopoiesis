from __future__ import annotations

import re
from pathlib import Path

from min_agent.atomicio import write_text_atomic
from min_agent.models import KnowledgeArtifact

_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?%?")


def lesson_key(artifact: KnowledgeArtifact) -> str:
    """What a lesson says, with its figures masked.

    The calibration lesson is re-derived every reflection with fresh numbers - "the worst
    bucket is -16% ..., Brier 0.392 over 292 scored decisions" - and exact-text dedup saw each
    re-derivation as new. 18 of the 20 accepted lessons on 2026-10-07 were that one sentence,
    so the prompt's five lesson slots held five copies of it and the two other lessons never
    reached the model. Two lessons with the same key are the same lesson at different times.
    """
    text = artifact.answer or artifact.summary or ""
    return " ".join(_NUMBER.sub("#", text.lower()).split())


class KnowledgeLibrary:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)

    def save(self, artifact: KnowledgeArtifact) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self._path(artifact.artifact_id), artifact.model_dump_json(indent=2) + "\n")

    def exists(self, artifact_id: str) -> bool:
        return self._path(artifact_id).exists()

    def try_load(self, artifact_id: str) -> KnowledgeArtifact | None:
        path = self._path(artifact_id)
        if not path.exists():
            return None
        return KnowledgeArtifact.model_validate_json(path.read_text(encoding="utf-8"))

    def load(self, artifact_id: str) -> KnowledgeArtifact:
        return KnowledgeArtifact.model_validate_json(self._path(artifact_id).read_text(encoding="utf-8"))

    def list(self, status: str | None = None, tags: tuple[str, ...] | None = None) -> list[KnowledgeArtifact]:
        if not self.directory.exists():
            return []
        normalized_status = status.strip().upper() if status is not None else None
        normalized_tags = {tag.strip().lower() for tag in tags or ()}
        artifacts = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                artifact = KnowledgeArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if normalized_status is not None and artifact.status != normalized_status:
                continue
            if normalized_tags and not normalized_tags.issubset(set(artifact.tags)):
                continue
            artifacts.append(artifact)
        return artifacts

    def _path(self, artifact_id: str) -> Path:
        return self.directory / f"{artifact_id}.json"
