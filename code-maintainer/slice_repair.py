"""One-shot Codex repair for stale maintenance slice selectors."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, TextIO

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
MAINTAINER_DIR = Path(__file__).resolve().parent
if str(MAINTAINER_DIR) not in sys.path:
    sys.path.insert(0, str(MAINTAINER_DIR))

from automation import runtime
from policy import resolve_slice_repair
from profiles import (
    ProjectProfile,
    load_project_profile,
    missing_profile_selectors,
    stale_selector_failure,
)


class SliceRepairFailure(RuntimeError):
    pass


def allowed_repair_paths(project_name: str) -> tuple[Path, ...]:
    project_root = (
        MAINTAINER_DIR / "skills" / "code-maintainer" / "references" / "projects" / project_name
    )
    return (
        project_root / "slices.json",
        MAINTAINER_DIR / "tests" / "test_profiles.py",
    )


def allowed_repair_relatives(project_name: str) -> frozenset[str]:
    return frozenset(
        path.relative_to(REPO_ROOT).as_posix() for path in allowed_repair_paths(project_name)
    )


def slice_repair_prompt(
    project: dict[str, Any],
    profile: ProjectProfile,
    workspace: Path,
    missing: tuple[tuple[str, str], ...],
) -> str:
    details = "\n".join(
        f"- `{identifier}`: `{selector}`" for identifier, selector in missing
    )
    allowed = "\n".join(f"- `{path}`" for path in sorted(allowed_repair_relatives(project["name"])))
    slices = profile.slices_path.relative_to(REPO_ROOT).as_posix()
    return f"""
<task>
The scheduled code-maintainer failed closed because slice selectors for project `{project["name"]}` no longer resolve in the prepared product worktree. Repair the overnight-agents slice registry so the same maintainer run can continue.

Product worktree (read-only evidence): `{workspace}`
Product repository: `{project["repository"]}`
Product base branch: `{project["base_branch"]}`
Slice registry to edit: `{slices}`

Missing selectors:
{details}
</task>

<allowed_files>
Edit only these overnight-agents files:
{allowed}
Do not edit the product worktree. Do not edit controller code, config, skills, other projects, or canonical policy documents.
</allowed_files>

<repair_rules>
- Inspect the product worktree and retarget every missing selector to the current semantic owner.
- Keep stable slice IDs when the owner still exists under a new path.
- Delete a slice only when that semantic owner is gone from the worktree and no honest replacement path exists.
- Every remaining selector must resolve with `Path.glob` in the product worktree, including the selectors that were not in the failure list.
- If `tests/test_profiles.py` asserts identifiers or paths that the registry change would break, update those assertions to match the repaired registry. Do not weaken the tests.
- Do not invent directories. Do not add glob stars to hide a missing owner.
</repair_rules>

<verification_loop>
1. Confirm each previously missing selector now resolves, or the slice was deleted with evidence.
2. Confirm no selector in `{slices}` is stale against `{workspace}`.
3. Run `python3 -m unittest tests.test_profiles` from `code-maintainer/`.
4. Stop with a dirty tree of allowed files only. Do not `git add`, `git commit`, `git push`, or change Git config. The controller publishes after it re-validates.
</verification_loop>

<default_follow_through_policy>
Do the repair. Do not ask questions. If a current owner cannot be proven in the product worktree, leave the registry unchanged and explain the blocker in the final message.
</default_follow_through_policy>
""".strip()


def changed_relatives(repo: Path) -> frozenset[str]:
    names = set()
    names.update(runtime.git(repo, "diff", "--name-only").stdout.splitlines())
    names.update(runtime.git(repo, "diff", "--cached", "--name-only").stdout.splitlines())
    names.update(
        runtime.git(repo, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    )
    return frozenset(name for name in names if name)


def _require_publishable_checkout(repo: Path, stream: TextIO) -> str:
    branch = runtime.git(repo, "branch", "--show-current").stdout.strip()
    if branch != "main":
        raise SliceRepairFailure(
            f"overnight-agents is on `{branch}`, not `main`; refusing slice repair"
        )
    if runtime.git(repo, "status", "--porcelain").stdout.strip():
        raise SliceRepairFailure(
            "overnight-agents working tree is dirty; refusing slice repair"
        )
    runtime.git(repo, "fetch", "--prune", "origin", branch, stream=stream)
    head = runtime.git(repo, "rev-parse", "HEAD").stdout.strip()
    upstream = runtime.git(repo, "rev-parse", f"origin/{branch}").stdout.strip()
    if head == upstream:
        return branch
    merge_base = runtime.git(repo, "merge-base", "HEAD", f"origin/{branch}").stdout.strip()
    if merge_base == head:
        runtime.git(repo, "merge", "--ff-only", f"origin/{branch}", stream=stream)
        return branch
    raise SliceRepairFailure(
        "overnight-agents HEAD is not a fast-forward of origin/main; refusing slice repair"
    )


def repair_stale_slice_registry(
    config: dict[str, Any],
    project: dict[str, Any],
    profile: ProjectProfile,
    workspace: Path,
    missing: tuple[tuple[str, str], ...],
    stream: TextIO,
) -> None:
    repair = resolve_slice_repair(config)
    if repair is None:
        raise stale_selector_failure(profile, workspace, missing)
    branch = _require_publishable_checkout(REPO_ROOT, stream)
    allowed = allowed_repair_relatives(project["name"])
    original_head = runtime.git(REPO_ROOT, "rev-parse", "HEAD").stdout.strip()
    prompt = slice_repair_prompt(project, profile, workspace, missing)
    agent_config = {
        "provider": "codex",
        "codex_model": repair["codex_model"],
        "codex_reasoning_effort": repair["codex_reasoning_effort"],
        "agent_timeout_seconds": repair["timeout_seconds"],
    }
    stream.write(
        "REPAIRING stale slice selectors with "
        f"{repair['codex_model']} {repair['codex_reasoning_effort']}\n"
    )
    stream.flush()
    agent = runtime.run(
        runtime.agent_command(agent_config, REPO_ROOT, prompt),
        cwd=REPO_ROOT,
        env=runtime.agent_environment(REPO_ROOT, MAINTAINER_DIR / ".env"),
        check=False,
        timeout=int(repair["timeout_seconds"]),
        stream=stream,
    )
    if agent.returncode != 0:
        raise SliceRepairFailure(
            f"slice repair agent exited with code {agent.returncode}"
        )
    if runtime.git(REPO_ROOT, "rev-parse", "HEAD").stdout.strip() != original_head:
        raise SliceRepairFailure("slice repair agent changed commit history")
    changed = changed_relatives(REPO_ROOT)
    if not changed:
        raise stale_selector_failure(profile, workspace, missing)
    unexpected = changed - allowed
    if unexpected:
        raise SliceRepairFailure(
            "slice repair changed files outside the allowlist: "
            + ", ".join(sorted(unexpected))
        )
    repaired = load_project_profile(
        MAINTAINER_DIR / "skills" / "code-maintainer",
        project["name"],
    )
    remaining = missing_profile_selectors(repaired, workspace)
    if remaining:
        raise stale_selector_failure(repaired, workspace, remaining)
    runtime.run(
        [sys.executable, "-m", "unittest", "tests.test_profiles"],
        cwd=MAINTAINER_DIR,
        stream=stream,
    )
    runtime.git(REPO_ROOT, "add", "--", *sorted(changed), stream=stream)
    runtime.git(
        REPO_ROOT,
        "commit",
        "-m",
        (
            f"Fix {project['name']} maintenance slice selectors "
            "to match the current repository."
        ),
        stream=stream,
    )
    runtime.git(REPO_ROOT, "push", "origin", f"HEAD:{branch}", stream=stream)
