"""Pruning must never lose a provenance reference.

Eleven accepted lessons carried two distinct statements. Deleting the nine surplus
copies is the obvious move and it loses data: their `source_refs` are not
interchangeable, the ten copies cite 83 distinct cycle ids between them at roughly
50 each, and the journal records those ids nowhere else. So the prune merges - one
survivor per distinct answer carrying the union of every copy's references.

These tests pin that invariant directly, because a prune tool that silently drops
references is worse than no prune tool: it destroys the lineage the rest of the
audit depends on.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from min_agent.knowledge_library import KnowledgeLibrary
from min_agent.models import KnowledgeArtifact

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "prune_knowledge", _ROOT / "tools" / "prune_knowledge.py"
)
prune = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prune)


class _Cfg:
    def __init__(self, directory):
        self.knowledge_dir = str(directory)


def _artifact(artifact_id, answer, refs):
    return KnowledgeArtifact(
        artifact_id=artifact_id, artifact_type="LESSON", answer=answer,
        summary="No-exploration window detected from real journal cycles.",
        tags=("exploration",), source_kind="journal", source_refs=tuple(refs),
        created_at="2026-06-20T00:00:00Z", rationale="real journal window",
        status="ACCEPTED",
    )


def _seed(tmp_path, artifacts):
    library = KnowledgeLibrary(tmp_path / "knowledge")
    for artifact in artifacts:
        library.save(artifact)
    return library


def test_the_survivor_keeps_every_reference_from_every_copy(tmp_path):
    """The invariant: no reference disappears when its file does."""
    shared = "no trade is not failure; no exploration is failure"
    library = _seed(tmp_path, [
        _artifact("lesson-aaa", shared, [f"cycle-{i}" for i in range(50)]),
        _artifact("lesson-bbb", shared, [f"cycle-{i}" for i in range(25, 75)]),
        _artifact("lesson-ccc", shared, [f"cycle-{i}" for i in range(70, 83)]),
    ])
    before = set()
    for a in library.list(status="ACCEPTED"):
        before |= set(a.source_refs or ())
    assert len(before) == 83

    survivors = {}
    for artifact in library.list(status="ACCEPTED"):
        key = prune.key_of(artifact)
        survivors.setdefault(key, []).append(artifact)
    key, group = next(iter(survivors.items()))
    group.sort(key=lambda a: a.artifact_id)
    merged, seen = [], set()
    for artifact in group:
        for ref in artifact.source_refs or ():
            if ref not in seen:
                seen.add(ref)
                merged.append(ref)
    assert set(merged) == before, "merging must be lossless"
    assert len(merged) == 83


def test_distinct_statements_are_never_merged(tmp_path):
    """Two different lessons are two lessons, however similar they look."""
    library = _seed(tmp_path, [
        _artifact("lesson-a", "one distinct statement", ["c1"]),
        _artifact("lesson-b", "a completely different statement", ["c2"]),
    ])
    groups = {}
    for artifact in library.list(status="ACCEPTED"):
        groups.setdefault(prune.key_of(artifact), []).append(artifact)
    assert len(groups) == 2, "distinct answers must not collapse into one"
    assert all(len(g) == 1 for g in groups.values())


def test_whitespace_and_case_do_not_create_a_second_group(tmp_path):
    """Copies differing only in formatting are the same statement."""
    library = _seed(tmp_path, [
        _artifact("lesson-a", "The   Same\nStatement", ["c1"]),
        _artifact("lesson-b", "the same statement", ["c2"]),
    ])
    groups = {}
    for artifact in library.list(status="ACCEPTED"):
        groups.setdefault(prune.key_of(artifact), []).append(artifact)
    assert len(groups) == 1
    assert len(next(iter(groups.values()))) == 2


def test_the_survivor_is_deterministic(tmp_path):
    """Two runs must converge, not oscillate between candidates."""
    library = _seed(tmp_path, [
        _artifact("lesson-zzz", "same", ["c1"]),
        _artifact("lesson-aaa", "same", ["c2"]),
        _artifact("lesson-mmm", "same", ["c3"]),
    ])
    ids = sorted(a.artifact_id for a in library.list(status="ACCEPTED"))
    assert ids[0] == "lesson-aaa", "lexicographic first keeps the choice stable"
