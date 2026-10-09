from __future__ import annotations

from dataclasses import dataclass

from autopoiesis.knowledge_library import KnowledgeLibrary, lesson_key
from autopoiesis.models import KnowledgeArtifact


@dataclass(frozen=True)
class KnowledgeAdmissionResult:
    accepted: bool
    reason: str
    artifact_id: str | None = None


class KnowledgeAdmission:
    def __init__(self, *, knowledge_library: KnowledgeLibrary):
        self.knowledge_library = knowledge_library
        # Track content hashes across admissions in the same session so identical
        # lessons (same summary+answer) with different artifact_ids are rejected.
        self._content_hashes: set[int] = set()

    @staticmethod
    def _content_hash(artifact: KnowledgeArtifact) -> int:
        return hash((artifact.summary, artifact.answer))

    def _already_on_disk(self, artifact: KnowledgeArtifact) -> bool:
        try:
            existing = self.knowledge_library.list(status="ACCEPTED")
        except Exception:
            # A library we cannot read must not become a way to force duplicates.
            return False
        summary = " ".join(artifact.summary.lower().split())
        answer = " ".join(artifact.answer.lower().split())
        for other in existing:
            if " ".join(other.summary.lower().split()) == summary and \
                    " ".join(other.answer.lower().split()) == answer:
                return True
        return False

    def admit(self, artifact: KnowledgeArtifact) -> KnowledgeAdmissionResult:
        if self.knowledge_library.exists(artifact.artifact_id):
            return KnowledgeAdmissionResult(
                accepted=False,
                reason="artifact_id already exists; duplicate admission rejected",
                artifact_id=artifact.artifact_id,
            )

        c_hash = self._content_hash(artifact)
        if c_hash in self._content_hashes:
            return KnowledgeAdmissionResult(
                accepted=False,
                reason="content hash already admitted this session; identical lesson rejected",
                artifact_id=artifact.artifact_id,
            )
        # The in-memory set is a fast path, not the rule. It resets on every
        # restart, so an identical lesson re-derived the next day was accepted
        # again: eleven accepted artifacts held two distinct statements, ten of
        # them variants of one sentence, and all eleven were fed to every
        # decision. Dedupe has to survive the process.
        if self._already_on_disk(artifact):
            return KnowledgeAdmissionResult(
                accepted=False,
                reason="an identical lesson is already in the library; duplicate rejected",
                artifact_id=artifact.artifact_id,
            )

        rejection_reason = self._rejection_reason(artifact)
        if rejection_reason is not None:
            return KnowledgeAdmissionResult(
                accepted=False,
                reason=rejection_reason,
                artifact_id=artifact.artifact_id,
            )

        self.knowledge_library.save(artifact.model_copy(update={"status": "ACCEPTED"}))
        self._content_hashes.add(c_hash)
        self._retire_earlier_versions(artifact)
        return KnowledgeAdmissionResult(
            accepted=True,
            reason="approved",
            artifact_id=artifact.artifact_id,
        )

    def _retire_earlier_versions(self, artifact: KnowledgeArtifact) -> None:
        """A newly accepted lesson supersedes accepted copies of itself with older figures.

        They are RETIRED, not deleted: each cites its own journal cycles, and those references
        are evidence nothing else records. Best-effort, like the on-disk dedup above.
        """
        if artifact.artifact_type != "LESSON":
            return
        key = lesson_key(artifact)
        try:
            existing = self.knowledge_library.list(status="ACCEPTED")
        except OSError:  # `list` already skips unparseable files; only I/O is left to fail
            return
        for other in existing:
            if (
                other.artifact_id != artifact.artifact_id
                and other.artifact_type == "LESSON"
                and lesson_key(other) == key
                and other.created_at <= artifact.created_at
            ):
                self.knowledge_library.save(other.model_copy(update={"status": "RETIRED"}))

    def _rejection_reason(self, artifact: KnowledgeArtifact) -> str | None:
        if artifact.source_kind == "external_stub":
            return "external_stub knowledge is a placeholder and requires future provenance review"
        if artifact.source_kind != "operator_note" and not artifact.source_refs:
            return "knowledge artifacts require cited source_refs"
        return None
