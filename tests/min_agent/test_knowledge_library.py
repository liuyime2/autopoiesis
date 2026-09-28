from datetime import datetime, timezone

from min_agent.knowledge_library import KnowledgeLibrary
from min_agent.models import KnowledgeArtifact


def make_artifact(*, artifact_id="qa-one", status="PROPOSED", tags=("risk",)):
    return KnowledgeArtifact(
        artifact_id=artifact_id,
        artifact_type="QA",
        question="What should the agent do before trading?",
        answer="Use admitted strategies and Guardian checks only.",
        summary="Guardian remains mandatory.",
        tags=tags,
        source_kind="operator_note",
        source_refs=(),
        created_at=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
        status=status,
        rationale="operator guidance",
    )


def test_knowledge_library_save_and_load(tmp_path):
    library = KnowledgeLibrary(tmp_path / "knowledge")
    library.save(make_artifact())

    loaded = library.load("qa-one")

    assert loaded.artifact_id == "qa-one"
    assert loaded.status == "PROPOSED"


def test_knowledge_library_exists_and_try_load(tmp_path):
    library = KnowledgeLibrary(tmp_path / "knowledge")
    assert library.exists("missing") is False
    assert library.try_load("missing") is None

    library.save(make_artifact(artifact_id="present"))

    assert library.exists("present") is True
    assert library.try_load("present").artifact_id == "present"


def test_knowledge_library_filters_by_status_and_tags(tmp_path):
    library = KnowledgeLibrary(tmp_path / "knowledge")
    library.save(make_artifact(artifact_id="accepted-risk", status="ACCEPTED", tags=("risk", "guardian")))
    library.save(make_artifact(artifact_id="proposed-risk", status="PROPOSED", tags=("risk",)))
    library.save(make_artifact(artifact_id="accepted-ops", status="ACCEPTED", tags=("ops",)))

    accepted_risk = library.list(status="accepted", tags=("risk",))

    assert [artifact.artifact_id for artifact in accepted_risk] == ["accepted-risk"]
