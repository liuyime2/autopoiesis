

def test_an_identical_lesson_is_rejected_across_a_restart():
    """The content-hash dedupe was an in-memory set, so it reset every restart and
    an identical lesson re-derived the next day was accepted again: eleven accepted
    artifacts held two distinct statements, ten of them variants of one sentence, and
    all eleven were fed to every decision. Dedupe has to survive the process."""
    import pathlib
    from datetime import datetime, timezone

    from autopoiesis.knowledge_admission import KnowledgeAdmission
    from autopoiesis.knowledge_library import KnowledgeLibrary
    from autopoiesis.models import KnowledgeArtifact

    class _Dir(pathlib.Path):
        pass

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        library = KnowledgeLibrary(pathlib.Path(tmp) / "knowledge")

        def lesson(created: str) -> KnowledgeArtifact:
            return KnowledgeArtifact(
                artifact_id=f"lesson-{created[:10].replace('-', '')}",
                artifact_type="LESSON",
                answer="No trade is not failure; no exploration is failure.",
                summary="No-exploration window detected from real journal cycles.",
                tags=("exploration",),
                source_kind="journal",
                source_refs=("cycle-1",),
                created_at=datetime.fromisoformat(created),
                rationale="r",
            )

        first = KnowledgeAdmission(knowledge_library=library)
        assert first.admit(lesson("2026-06-16T18:46:57+00:00")).accepted is True

        # A brand-new admission object is what a restart looks like: the in-memory
        # set is empty again, so only a check against the library can catch this.
        after_restart = KnowledgeAdmission(knowledge_library=library)
        result = after_restart.admit(lesson("2026-09-28T18:22:23+00:00"))

        assert result.accepted is False
        assert "already in the library" in result.reason
        assert len(library.list(status="ACCEPTED")) == 1
