#!/usr/bin/env python3
"""Collapse duplicate knowledge artifacts, preserving every provenance reference.

Eleven accepted lessons carried two distinct statements: ten copies of one answer
and one of another. They are historical duplicates from an earlier artifact_id
scheme - the current scheme hashes `subtype|answer`, so a repeat run produces the
same id and `KnowledgeAdmission`'s content-hash dedup catches it. Nothing prunes
the ones already on disk.

Deleting the nine surplus copies would be the obvious move and would lose data.
Their `source_refs` are **not** interchangeable: the ten copies cite 83 distinct
cycle ids between them, roughly 50 each, so dropping nine would drop on the order
of 33 journal references that nothing else records. So this merges instead:
one survivor per distinct answer, carrying the union of every copy's `source_refs`.

Read-time deduplication in `PolicyEngine.relevant_lessons` already removed the
functional harm - five lesson slots were being filled with copies of one statement.
This is the cleanup of what the deduplication hides, and it is deliberately
conservative: nothing is deleted unless its references survive elsewhere.

Usage: python tools/prune_knowledge.py [--apply]
Without --apply it reports what it would do and changes nothing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from min_agent.config import AgentConfig
from min_agent.knowledge_library import KnowledgeLibrary
from min_agent.models import KnowledgeArtifact


def key_of(artifact: KnowledgeArtifact) -> str:
    return " ".join((artifact.answer or artifact.summary or "").lower().split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="write the merged survivors and remove the surplus")
    args = parser.parse_args()

    config = AgentConfig.from_env()
    library = KnowledgeLibrary(config.knowledge_dir)
    artifacts = library.list(status="ACCEPTED")
    if not artifacts:
        print("no accepted artifacts; nothing to prune")
        return 0

    groups: dict[str, list[KnowledgeArtifact]] = {}
    for artifact in artifacts:
        groups.setdefault(key_of(artifact), []).append(artifact)

    surplus = sum(len(g) - 1 for g in groups.values())
    if not surplus:
        print(f"{len(artifacts)} accepted artifact(s), all distinct; nothing to prune")
        return 0

    print(f"{len(artifacts)} accepted artifact(s), {len(groups)} distinct statement(s), "
          f"{surplus} surplus\n")
    for key, group in groups.items():
        if len(group) == 1:
            continue
        # Deterministic survivor: the lexicographically first id, so repeated runs
        # converge instead of oscillating between candidates.
        group.sort(key=lambda a: a.artifact_id)
        survivor = group[0]
        merged: list[str] = []
        seen: set[str] = set()
        for artifact in group:
            for ref in artifact.source_refs or ():
                if ref not in seen:
                    seen.add(ref)
                    merged.append(ref)
        original = sum(len(a.source_refs or ()) for a in group)
        print(f"  {len(group)} copies -> keep {survivor.artifact_id}")
        print(f"    answer: {key[:78]}")
        print(f"    source_refs: {original} across the copies, {len(merged)} distinct "
              f"-> the survivor carries all {len(merged)}")
        if not args.apply:
            print(f"    would remove: {', '.join(a.artifact_id for a in group[1:])}")
        if args.apply:
            library.save(survivor.model_copy(update={"source_refs": tuple(merged)}))
            for artifact in group[1:]:
                (Path(config.knowledge_dir) / f"{artifact.artifact_id}.json").unlink()
            print(f"    removed {len(group) - 1} surplus file(s)")

    if not args.apply:
        print("\ndry run; pass --apply to merge")
        return 0

    after = library.list(status="ACCEPTED")
    print(f"\n{len(artifacts)} -> {len(after)} accepted artifact(s), "
          f"{len({key_of(a) for a in after})} distinct statement(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
