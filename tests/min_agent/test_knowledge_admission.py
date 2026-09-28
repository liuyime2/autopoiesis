from datetime import datetime, timezone

from min_agent.knowledge_admission import KnowledgeAdmission
from min_agent.knowledge_library import KnowledgeLibrary
from min_agent.models import KnowledgeArtifact


def make_artifact(*, artifact_id="qa-one", source_kind="operator_note", source_refs=(), answer="Use Guardian checks."):
    return KnowledgeArtifact(
        artifact_id=artifact_id,
        artifact_type="QA",
        question="What should the agent do before trading?",
        answer=answer,
        summary="Guardian remains mandatory.",
        tags=("risk",),
        source_kind=source_kind,
        source_refs=source_refs,
        created_at=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
        rationale="operator guidance",
    )


def test_knowledge_admission_accepts_safe_operator_note(tmp_path):
    library = KnowledgeLibrary(tmp_path / "knowledge")
    admission = KnowledgeAdmission(knowledge_library=library)

    result = admission.admit(make_artifact())

    assert result.accepted is True
    assert library.load("qa-one").status == "ACCEPTED"


def test_knowledge_admission_rejects_duplicate_artifact_id(tmp_path):
    library = KnowledgeLibrary(tmp_path / "knowledge")
    admission = KnowledgeAdmission(knowledge_library=library)

    first = admission.admit(make_artifact())
    second = admission.admit(make_artifact(answer="A changed answer."))

    assert first.accepted is True
    assert second.accepted is False
    assert "already exists" in second.reason


def test_knowledge_admission_accepts_cited_journal_artifact(tmp_path):
    library = KnowledgeLibrary(tmp_path / "knowledge")
    admission = KnowledgeAdmission(knowledge_library=library)

    result = admission.admit(make_artifact(artifact_id="lesson-one", source_kind="journal", source_refs=("cycle-1",)))

    assert result.accepted is True
    assert library.load("lesson-one").source_refs == ("cycle-1",)


def test_knowledge_admission_rejects_external_stub_until_provenance_exists(tmp_path):
    library = KnowledgeLibrary(tmp_path / "knowledge")
    admission = KnowledgeAdmission(knowledge_library=library)

    result = admission.admit(make_artifact(artifact_id="external", source_kind="external_stub", source_refs=("https://example.com",)))

    assert result.accepted is False
    assert "external_stub" in result.reason
    assert library.list() == []
