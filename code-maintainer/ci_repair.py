#!/usr/bin/env python3
"""Resume the exact maintenance Codex session after authoritative CI fails."""

from __future__ import annotations

import argparse
import fcntl
import json
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
for path in (REPO_ROOT, SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from automation import runtime, worktrees
from controller import (
    BRANCH_PREFIX,
    MaintainerFailure,
    atomic_json,
    ensure_disk_capacity,
    load_config,
    pending_path,
    protected_paths,
)


def _load_pending(project_name: str) -> tuple[Path, dict[str, object]]:
    path = pending_path(project_name)
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise MaintainerFailure("maintenance CI event has no valid pending run") from error
    if not isinstance(value, dict) or value.get("version") != 1:
        raise MaintainerFailure("maintenance CI event has invalid pending state")
    return path, value


def _project(config: dict[str, object], name: str) -> dict[str, object]:
    projects = config.get("projects")
    if not isinstance(projects, list):
        raise MaintainerFailure("maintainer projects are unavailable")
    value = next(
        (item for item in projects if isinstance(item, dict) and item.get("name") == name),
        None,
    )
    if value is None:
        raise MaintainerFailure(f"unknown maintainer project: {name}")
    return value


def _gh_json(command: list[str], stream: object) -> dict[str, object]:
    result = runtime.run(command, stream=stream)  # type: ignore[arg-type]
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise MaintainerFailure("GitHub returned an invalid object")
    return value


def handle_ci_result(
    config_path: Path,
    project_name: str,
    pr_number: int,
    run_id: int,
    head_sha: str,
    conclusion: str,
    stream: object,
    workflow_name: str | None = None,
) -> str:
    config = load_config(config_path)
    project = _project(config, project_name)
    workflows = project.get(
        "maintenance_workflow_names",
        [project.get("maintenance_workflow_name", "Code Quality")],
    )
    workflow_name = workflow_name or str(workflows[0])
    if workflow_name not in workflows:
        raise MaintainerFailure("CI workflow is not configured for this project")
    pending_file, pending = _load_pending(project_name)
    if pending.get("pull_request") != pr_number:
        raise MaintainerFailure("CI event does not match the pending maintenance PR")
    if pending.get("head_sha") != head_sha:
        raise MaintainerFailure("CI event is stale for the pending maintenance head")
    branch = pending.get("branch")
    if not isinstance(branch, str) or not branch.startswith(f"{BRANCH_PREFIX}/"):
        raise MaintainerFailure("pending maintenance branch is invalid")

    pr = _gh_json(
        [
            "gh", "pr", "view", str(pr_number), "--repo", str(project["repository"]),
            "--json", "state,headRefOid,headRefName,baseRefName",
        ],
        stream,
    )
    if (
        pr.get("state") != "OPEN"
        or pr.get("headRefOid") != head_sha
        or pr.get("headRefName") != branch
        or pr.get("baseRefName") != project["base_branch"]
    ):
        raise MaintainerFailure("CI event no longer matches the open PR head")

    updated = dict(pending)
    recorded = dict(pending.get("ci_workflows") or {})
    recorded[workflow_name] = {"run_id": run_id, "conclusion": conclusion}
    updated["ci_workflows"] = recorded
    outcomes = [recorded.get(name, {}).get("conclusion", "pending") for name in workflows]
    if "failure" in outcomes:
        updated["ci_conclusion"] = "failure"
    elif all(value == "success" for value in outcomes):
        updated["ci_conclusion"] = "success"
    else:
        updated["ci_conclusion"] = "pending"
    updated["ci_run_id"] = run_id
    updated["ci_updated_at"] = runtime.now_iso()
    if conclusion == "success":
        atomic_json(pending_file, updated)
        if updated["ci_conclusion"] == "success":
            return f"{project_name}: CI passed for {head_sha}"
        return f"{project_name}: {workflow_name} passed; remaining CI is {updated['ci_conclusion']}"
    if conclusion != "failure":
        atomic_json(pending_file, updated)
        return f"{project_name}: recorded CI conclusion {conclusion}"

    session_id = pending.get("codex_session_id")
    if not isinstance(session_id, str) or not session_id:
        raise MaintainerFailure("pending maintenance run has no resumable Codex session")
    attempts = pending.get("ci_repair_attempts", 0)
    if not isinstance(attempts, int) or isinstance(attempts, bool):
        raise MaintainerFailure("pending maintenance repair count is invalid")
    maximum = int(config.get("ci_repair_max_attempts", 2))
    if attempts >= maximum:
        raise MaintainerFailure("maintenance CI repair retry limit reached")

    ensure_disk_capacity(config, stream)  # type: ignore[arg-type]
    log = runtime.run(
        [
            "gh", "run", "view", str(run_id), "--repo", str(project["repository"]),
            "--log",
        ],
        stream=stream,  # type: ignore[arg-type]
    ).stdout
    max_log_bytes = int(config.get("max_ci_log_bytes", 5_000_000))
    encoded = log.encode()
    if len(encoded) > max_log_bytes:
        raise MaintainerFailure(
            f"complete CI log is {len(encoded)} bytes, above the configured "
            f"{max_log_bytes}-byte limit"
        )

    workspace_root = Path(str(config.get("workspace_root", SCRIPT_DIR / "state/workspaces")))
    workspace = worktrees.safe_workspace(
        workspace_root, project_name, Path(str(project["source_path"])), "maintenance CI repair"
    )
    if workspace.exists():
        raise MaintainerFailure("maintenance CI repair workspace already exists")
    source = Path(str(project["source_path"])).expanduser().resolve(strict=True)
    worktrees.git(source, "fetch", "--prune", "origin", branch, stream=stream)  # type: ignore[arg-type]
    remote_head = worktrees.git(source, "rev-parse", f"origin/{branch}").stdout.strip()
    if remote_head != head_sha:
        raise MaintainerFailure("remote maintenance branch changed before CI repair")
    worktrees.git(
        source, "worktree", "add", "--detach", str(workspace), f"origin/{branch}", stream=stream  # type: ignore[arg-type]
    )

    terminal_cleanup = False
    try:
        original_config = runtime.protected_repository_config(workspace)
        prompt = f"""The authoritative GitHub CI check failed for the maintenance PR you created.

Resume only the existing maintenance lifecycle. The exact failing head is {head_sha} and the
current workspace is checked out at that commit. Diagnose the attached failed-job output,
make only the smallest proven repair inside the original semantic slice, and inspect the final
diff. Prefer removing or correcting the change that broke CI over adding code; never add a
fallback, retry, or compatibility path to make a check pass. Do not rerun specialists. Do not
run tests, typechecks, linters, builds, or validation; GitHub CI owns validation and will rerun
after publication. Run only `git diff --check`.
Never commit, push, alter Git configuration, or change controller/CI policy.

FAILED_CI_LOG_BEGIN
{log}
FAILED_CI_LOG_END
"""
        result = runtime.resume_codex_session(
            config,
            workspace,
            session_id,
            prompt,
            stream,  # type: ignore[arg-type]
            environment_file=SCRIPT_DIR / ".env",
        )
        if result.returncode != 0:
            raise MaintainerFailure(f"resumed Codex session exited with code {result.returncode}")
        resumed_id, _message = runtime.codex_session(result.stdout)
        if resumed_id != session_id:
            raise MaintainerFailure("Codex resumed a different maintenance session")
        if runtime.protected_repository_config(workspace) != original_config:
            raise MaintainerFailure("resumed agent changed local Git configuration")
        if runtime.git(workspace, "rev-parse", "HEAD").stdout.strip() != head_sha:
            raise MaintainerFailure("resumed agent changed commit history")
        paths = runtime.git(workspace, "status", "--porcelain").stdout.splitlines()
        changed = [line[3:] for line in paths]
        if not changed:
            raise MaintainerFailure("resumed agent produced no CI repair")
        unsafe = protected_paths(changed)
        if unsafe:
            raise MaintainerFailure("CI repair changed protected paths: " + ", ".join(unsafe))
        if len(changed) > int(config.get("max_changed_files", 80)):
            raise MaintainerFailure("CI repair exceeds the configured file budget")
        diff_bytes = len(runtime.git(workspace, "diff", "--binary").stdout.encode())
        if diff_bytes > int(config.get("max_diff_bytes", 750_000)):
            raise MaintainerFailure("CI repair exceeds the configured diff budget")
        runtime.git(workspace, "diff", "--check", stream=stream)  # type: ignore[arg-type]
        runtime.git(workspace, "add", "-A", stream=stream)  # type: ignore[arg-type]
        runtime.git(
            workspace,
            "commit",
            "-m",
            "maintenance: repair CI failure",
            stream=stream,  # type: ignore[arg-type]
        )
        new_head = runtime.git(workspace, "rev-parse", "HEAD").stdout.strip()
        runtime.git(
            workspace,
            "push",
            f"--force-with-lease=refs/heads/{branch}:{head_sha}",
            "origin",
            f"HEAD:refs/heads/{branch}",
            stream=stream,  # type: ignore[arg-type]
        )
        updated.update(
            head_sha=new_head,
            ci_repair_attempts=attempts + 1,
            ci_conclusion="pending",
            ci_repair_session_id=session_id,
            ci_workflows={},
        )
        atomic_json(pending_file, updated)
        terminal_cleanup = True
        return f"{project_name}: pushed CI repair {new_head}"
    finally:
        if terminal_cleanup and workspace.exists():
            worktrees.remove_linked_worktree(
                source_path=source,
                workspace=workspace,
                branch_prefix=BRANCH_PREFIX,
                stream=stream,  # type: ignore[arg-type]
            )


def reconcile(config_path: Path, stream: object) -> list[str]:
    """Poll pending PR checks as a fallback for missed workflow webhooks."""

    config = load_config(config_path)
    results: list[str] = []
    pending_root = SCRIPT_DIR / "state" / "pending"
    for path in sorted(pending_root.glob("*.json")):
        try:
            pending = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(pending, dict) or not pending.get("codex_session_id"):
            continue
        project_name = pending.get("project")
        pr_number = pending.get("pull_request")
        head_sha = pending.get("head_sha")
        if (
            not isinstance(project_name, str)
            or not isinstance(pr_number, int)
            or not isinstance(head_sha, str)
        ):
            continue
        project = _project(config, project_name)
        value = _gh_json(
            [
                "gh", "api",
                f"repos/{project['repository']}/actions/runs?head_sha={head_sha}&per_page=100",
            ],
            stream,
        )
        checks = value.get("workflow_runs")
        if not isinstance(checks, list):
            continue
        workflows = project.get(
            "maintenance_workflow_names",
            [project.get("maintenance_workflow_name", "Code Quality")],
        )
        for workflow_name in workflows:
            matching = [
                check for check in checks
                if isinstance(check, dict)
                and check.get("name") == workflow_name
                and check.get("event") == "pull_request"
                and check.get("head_sha") == head_sha
                and check.get("head_branch") == pending.get("branch")
            ]
            if not matching:
                continue
            check = max(matching, key=lambda entry: entry.get("id", 0))
            conclusion = check.get("conclusion")
            if check.get("status") != "completed" or conclusion not in {"success", "failure"}:
                continue
            previous = (pending.get("ci_workflows") or {}).get(workflow_name, {})
            if previous == {"run_id": check["id"], "conclusion": conclusion}:
                continue
            results.append(
                handle_ci_result(
                    config_path, project_name, pr_number, check["id"], head_sha,
                    conclusion, stream, workflow_name=workflow_name,
                )
            )
            if conclusion == "failure":
                break
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=SCRIPT_DIR / "config.json")
    parser.add_argument("--project")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--head-sha")
    parser.add_argument("--conclusion")
    parser.add_argument("--workflow-name")
    parser.add_argument("--reconcile", action="store_true")
    args = parser.parse_args()
    logs = SCRIPT_DIR / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    try:
        lock_path = REPO_ROOT / "state" / "maintenance.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            with (logs / f"ci_repair_{stamp}.log").open("a") as stream:
                if args.reconcile:
                    messages = reconcile(args.config, stream)
                else:
                    if None in (
                        args.project, args.pr, args.run_id, args.head_sha, args.conclusion
                    ):
                        raise MaintainerFailure("CI event arguments are incomplete")
                    messages = [
                        handle_ci_result(
                            args.config,
                            args.project,
                            args.pr,
                            args.run_id,
                            args.head_sha,
                            args.conclusion,
                            stream,
                            workflow_name=args.workflow_name,
                        )
                    ]
        for message in messages:
            print(message)
        return 0
    except (OSError, ValueError, MaintainerFailure, runtime.RuntimeFailure, worktrees.WorktreeFailure) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
