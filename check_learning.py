#!/usr/bin/env python3
import json
from min_agent.journal import JsonlJournal
from min_agent.config import AgentConfig

cfg = AgentConfig.from_env()
journal = JsonlJournal(cfg.journal_path)

print("=== 最近的学习事件 ===\n")

events = list(reversed(journal.read_events(limit=50)))
for e in events:
    if e.event_type in {'CURRICULUM_PROPOSED', 'REFLECTION_GENERATED', 'STRATEGY_EVALUATION_RECORDED'}:
        print(f"--- {e.event_type:30} {e.status:10} {e.timestamp} ---")
        if e.message:
            print(f"  Message: {e.message[:200]}")
        if e.payload:
            if e.event_type == 'CURRICULUM_PROPOSED':
                task_id = e.payload.get('task_id')
                summary = e.payload.get('summary')
                if task_id:
                    print(f"  Task: {task_id}")
                if summary:
                    print(f"  Summary: {summary[:200]}...")
        print()
