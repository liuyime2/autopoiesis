"""Every workflow file's `jobs:` actually contains the jobs it defines.

Added after a real one. A `release` job was appended to `ci.yml` at column 0 instead of under
`jobs:`, so the file parsed cleanly - it is valid YAML, with `release` as a sibling of `jobs`
rather than a child - and the workflow ran only `gate` while reporting failure with zero jobs. A
reader of the file sees a release job; GitHub sees none. Nothing on this host could see the
difference, because the only thing that reads these files is GitHub.

`yaml.safe_load` succeeding is therefore not the check. What is checked: the top-level keys are
exactly the ones Actions accepts, and every job is reachable from `jobs`.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: GitHub Actions' own top-level workflow keys. Anything else is a typo or, as here, a job that
#: lost its parent.
_TOP_LEVEL = {
    "name", "run-name", "permissions", "env", "defaults", "concurrency", "jobs",
    # YAML 1.1 resolves the bare `on:` key to the *boolean* `True`, not the string. A set written
    # with "true" in it reports both of this repository's workflows as broken, which is the check
    # being wrong rather than the file - and the first version of this check did exactly that for
    # one run before the difference was looked up instead of guessed at.
    True,
}


def main() -> int:
    import yaml

    files = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    files += sorted((ROOT / ".github" / "workflows").glob("*.yaml"))
    problems: list[str] = []
    for path in files:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{path.name}: does not parse as YAML: {exc}")
            continue
        if not isinstance(data, dict):
            problems.append(f"{path.name}: is not a mapping")
            continue
        stray = sorted(set(data) - _TOP_LEVEL)
        if stray:
            problems.append(
                f"{path.name}: {stray} at the top level, so a job has no jobs: parent and will "
                "not run"
            )
        jobs = data.get("jobs") or {}
        if not jobs:
            problems.append(f"{path.name}: declares no jobs")
        for name, job in jobs.items():
            if not isinstance(job, dict) or ("steps" not in job and "uses" not in job):
                problems.append(f"{path.name}: job {name!r} has neither steps nor uses")

    print(json.dumps({"workflows": [p.name for p in files], "problems": problems}, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
