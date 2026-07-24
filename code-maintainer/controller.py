#!/usr/bin/env python3
"""Run one evidence-backed slice in a perpetual code-maintenance cycle."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from automation import clones, pull_requests, runtime, worktrees
from context_evidence import ContextFailure, prepare_context_evidence
from cycles import (
    CycleFailure,
    CyclePosition,
    advance,
    atomic_json,
    checkpoint,
    load_position,
)
from policy import ConfigurationFailure, validate_config
from profiles import (
    MaintenanceSlice,
    ProfileFailure,
    ProjectProfile,
    load_project_profile,
)


SKILL_ROOT = SCRIPT_DIR / "skills" / "code-maintainer"
BRANCH_PREFIX = "code-maintain"
PROTECTED_PATTERNS = (
    re.compile(r"(^|/)\.env(?:\.|$)"),
    re.compile(r"(^|/)__pycache__(?:/|$)|\.pyc$"),
    re.compile(
        r"(^|/)(?:package\.json|pyproject\.toml|Cargo\.toml|requirements[^/]*\.txt|"
        r"pnpm-lock\.yaml|package-lock\.json|yarn\.lock|uv\.lock|Cargo\.lock)$"
    ),
    re.compile(r"(^|/)[^/]+\.config\.[^/]+$"),
    re.compile(r"(^|/)(?:migrations?|\.github/workflows)(?:/|$)"),
    re.compile(r"(^|/)(?:_generated|generated)(?:/|$)"),
    re.compile(r"(^|/)(?:AGENTS|CLAUDE)\.md$"),
    re.compile(r"(^|/)(?:\.agents|\.claude|\.codex)(?:/|$)"),
)


class MaintainerFailure(RuntimeError):
    pass


def load_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise MaintainerFailure(f"invalid maintainer config: {error}") from error
    if not isinstance(value, dict):
        raise MaintainerFailure("maintainer config must be a JSON object")
    try:
        validate_config(value)
    except ConfigurationFailure as error:
        raise MaintainerFailure(str(error)) from error
    value["_config_dir"] = str(path.expanduser().resolve().parent)
    return value


def profile_for(project: dict[str, Any]) -> ProjectProfile:
    try:
        return load_project_profile(SKILL_ROOT, project["name"])
    except ProfileFailure as error:
        raise MaintainerFailure(str(error)) from error


def select_project(
    config: dict[str, Any], requested: str | None, rotation_path: Path
) -> dict[str, Any] | None:
    projects = config["projects"]
    if requested:
        return next(
            (
                project
                for project in projects
                if project["name"] == requested and project["enabled"]
            ),
            None,
        )
    enabled = [project for project in projects if project["enabled"]]
    if not enabled:
        return None
    try:
        index = int(rotation_path.read_text())
    except (OSError, ValueError):
        index = 0
    selected = enabled[index % len(enabled)]
    rotation_path.parent.mkdir(parents=True, exist_ok=True)
    rotation_path.write_text(str((index + 1) % len(enabled)))
    return selected


def prepare_workspace(
    config: dict[str, Any], project: dict[str, Any], stream: TextIO
) -> dict[str, str | bool]:
    root = Path(config.get("workspace_root", SCRIPT_DIR / "state" / "workspaces"))
    workspace_config = project.get("workspace")
    try:
        if isinstance(workspace_config, dict):
            return worktrees.prepare_linked_worktree(
                source_path=Path(project["source_path"]),
                workspace_root=root,
                project_name=project["name"],
                base_branch=project["base_branch"],
                branch_prefix=BRANCH_PREFIX,
                checklist_file=None,
                automation_label="maintainer",
                stream=stream,
            )
        return clones.prepare_workspace(
            source_path=Path(project["source_path"]),
            workspace_root=root,
            project_name=project["name"],
            base_branch=project["base_branch"],
            branch_prefix=BRANCH_PREFIX,
            environment_file=Path(project["environment_file"]),
            checklist_file=None,
            automation_label="maintainer",
        )
    except (clones.WorkspaceFailure, worktrees.WorktreeFailure) as error:
        raise MaintainerFailure(str(error)) from error


def unique_branch(workspace: Path) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    base = f"{BRANCH_PREFIX}/{date}"

    def exists(branch: str) -> bool:
        return any(
            runtime.git(
                workspace,
                "show-ref",
                "--verify",
                "--quiet",
                reference,
                check=False,
            ).returncode
            == 0
            for reference in (
                f"refs/heads/{branch}",
                f"refs/remotes/origin/{branch}",
            )
        )

    if not exists(base):
        return base
    timestamped = f"{base}-{datetime.now().strftime('%H%M%S')}"
    candidate = timestamped
    counter = 2
    while exists(candidate):
        candidate = f"{timestamped}-{counter}"
        counter += 1
    return candidate


def cycle_path(project_name: str) -> Path:
    return SCRIPT_DIR / "state" / "cycles" / f"{project_name}.json"


def pending_path(project_name: str) -> Path:
    return SCRIPT_DIR / "state" / "pending" / f"{project_name}.json"


def slice_ids(profile: ProjectProfile) -> tuple[str, ...]:
    return tuple(item.identifier for item in profile.slices)


def current_slice(
    profile: ProjectProfile, position: CyclePosition
) -> MaintenanceSlice:
    return profile.slices[position.index]


def agent_prompt(
    workspace: Path,
    project: dict[str, Any],
    profile: ProjectProfile,
    item: MaintenanceSlice,
    position: CyclePosition,
    branch: str,
    context_evidence: Path,
    resuming: bool,
) -> str:
    resume = (
        "\nThis is a resumed interrupted run. Inspect and continue only the "
        "existing correct working-tree changes before making new edits.\n"
        if resuming
        else ""
    )
    return f"""Use the scheduled code-maintainer skill at {SKILL_ROOT / 'SKILL.md'}.

Run one complete maintenance lifecycle in {workspace} on branch {branch}, based on origin/{project['base_branch']}.
Project: {project['name']}
Project context manifest: {profile.manifest_path}
Semantic slice registry: {profile.slices_path}
Maintenance cycle: {position.cycle}
Selected semantic slice:
{json.dumps(item.prompt_payload(), indent=2, sort_keys=True)}

Audited current skills and official-documentation evidence: {context_evidence}
Repository validation commands: {json.dumps(project['validation_commands'])}

Read the skill, repository instructions, project manifest, shared context, and
the exact role-specific references routed by the manifest. Resolve selectors
against the current repository before drawing conclusions; search terms are
discovery hints, never authorization to edit unrelated code. Run every
specialist role listed in the slice, in bounded concurrent batches if provider
limits prevent one batch. The specialists are read-only. You alone reconcile
evidence, make bounded edits, run focused checks and definitive validation, and
run the fresh verifier.

Never commit, push, create a PR, alter Git configuration, edit maintenance
cycle state, or modify trusted agent policy. The controller owns publication
and advances this semantic slice only after a no-change audit or merged PR.
{resume}

{pull_requests.MANUAL_UI_CHECKS_PROMPT}
"""


def protected_paths(paths: list[str]) -> list[str]:
    return [
        path
        for path in paths
        if any(pattern.search(path) for pattern in PROTECTED_PATTERNS)
    ]


def active_maintainer_pr(project: dict[str, Any], stream: TextIO) -> str | None:
    result = runtime.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            project["repository"],
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "url,headRefName",
        ],
        stream=stream,
    )
    for pull_request in json.loads(result.stdout):
        if str(pull_request.get("headRefName", "")).startswith(
            f"{BRANCH_PREFIX}/"
        ):
            return str(pull_request["url"])
    return None


def reconcile_pending(
    project: dict[str, Any], profile: ProjectProfile, stream: TextIO
) -> str | None:
    path = pending_path(project["name"])
    if not path.exists():
        return None
    try:
        pending = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise MaintainerFailure(f"invalid pending maintainer state: {error}") from error
    if not isinstance(pending, dict) or pending.get("version") != 1:
        raise MaintainerFailure("invalid pending maintainer state")
    identifiers = slice_ids(profile)
    pending_slice = pending.get("slice")
    if not isinstance(pending_slice, str) or pending_slice not in identifiers:
        raise MaintainerFailure("pending maintainer slice is no longer registered")
    position = CyclePosition(
        cycle=pending.get("cycle"),
        index=identifiers.index(pending_slice),
    )
    if (
        not isinstance(position.cycle, int)
        or load_position(cycle_path(project["name"]), identifiers) != position
    ):
        raise MaintainerFailure("pending maintainer slice no longer matches cycle state")
    result = runtime.run(
        [
            "gh",
            "pr",
            "view",
            str(pending["pull_request"]),
            "--repo",
            project["repository"],
            "--json",
            "state,mergedAt,url",
        ],
        stream=stream,
    )
    value = json.loads(result.stdout)
    if value.get("state") == "OPEN":
        return f"{project['name']}: waiting for maintainer PR {value['url']}"
    if value.get("mergedAt"):
        advance(
            cycle_path(project["name"]),
            position,
            identifiers,
            slice_id=pending["slice"],
            outcome="merged",
        )
    path.unlink()
    return None


def changed_diff_bytes(workspace: Path) -> int:
    value = runtime.git(workspace, "diff", "--cached", "--binary").stdout
    return len(value.encode())


def publish(
    workspace: Path,
    config: dict[str, Any],
    project: dict[str, Any],
    item: MaintenanceSlice,
    position: CyclePosition,
    branch: str,
    agent_output: str,
    stream: TextIO,
) -> str:
    runtime.git(workspace, "diff", "--check", stream=stream)
    runtime.git(workspace, "add", "-A", stream=stream)
    staged = runtime.git(
        workspace, "diff", "--cached", "--name-only", stream=stream
    ).stdout.splitlines()
    unsafe = protected_paths(staged)
    if unsafe:
        raise MaintainerFailure(
            "refusing to publish protected paths: " + ", ".join(unsafe)
        )
    if not staged:
        raise MaintainerFailure("maintainer reported changes but staged diff is empty")
    if len(staged) > int(config.get("max_changed_files", 80)):
        raise MaintainerFailure("maintainer change exceeds the configured file budget")
    if changed_diff_bytes(workspace) > int(config.get("max_diff_bytes", 750_000)):
        raise MaintainerFailure("maintainer change exceeds the configured diff budget")
    runtime.git(workspace, "diff", "--cached", "--check", stream=stream)
    target = item.title[:72]
    runtime.git(
        workspace,
        "commit",
        "-m",
        f"maintenance: {target}",
        stream=stream,
    )
    runtime.git(workspace, "push", "-u", "origin", branch, stream=stream)
    validation = "\n".join(
        " ".join(command) for command in project["validation_commands"]
    )
    ui_section = pull_requests.manual_ui_section(
        pull_requests.manual_ui_checks(agent_output, staged, item.title)
    )
    result = runtime.run(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            project["repository"],
            "--base",
            project["base_branch"],
            "--head",
            branch,
            "--title",
            f"maintenance: {target}",
            "--body",
            (
                f"## Maintenance slice\n\nCycle {position.cycle}, semantic slice "
                f"`{item.identifier}`: {item.title}.\n\n"
                "Reviewed through specialized reuse, organization, efficiency, "
                "correctness, and security lenses routed by the project profile.\n\n"
                "## Validation\n\n"
                f"```text\n{validation}\n```\n\n"
                "The maintenance agent owned focused checks, definitive validation, "
                "and an independent final verifier. The controller enforced fresh "
                "context evidence, change budgets, publication safety, and cycle state.\n\n"
                f"{ui_section}"
            ),
        ],
        cwd=workspace,
        stream=stream,
    )
    url = result.stdout.strip().splitlines()[-1]
    if not re.fullmatch(r"https://github\.com/[^/]+/[^/]+/pull/\d+", url):
        raise MaintainerFailure(f"could not parse created PR URL: {url}")
    return url


def execute_project(
    config: dict[str, Any], project: dict[str, Any], *, apply: bool, stream: TextIO
) -> str:
    profile = profile_for(project)
    identifiers = slice_ids(profile)
    prepared = prepare_workspace(config, project, stream)
    workspace = Path(str(prepared["workspace"]))
    resuming = bool(prepared["resuming"])
    workspace_config = project.get("workspace")

    def finish_without_agent(message: str) -> str:
        if (
            isinstance(workspace_config, dict)
            and not resuming
            and workspace.exists()
        ):
            worktrees.remove_linked_worktree(
                source_path=Path(project["source_path"]),
                workspace=workspace,
                branch_prefix=BRANCH_PREFIX,
                stream=stream,
            )
        return message

    pending_message = reconcile_pending(project, profile, stream)
    if pending_message:
        return finish_without_agent(pending_message)
    position = load_position(cycle_path(project["name"]), identifiers)
    item = current_slice(profile, position)
    active = active_maintainer_pr(project, stream)
    if active:
        return finish_without_agent(
            f"{project['name']}: waiting for maintainer PR {active}"
        )
    if not apply:
        return finish_without_agent(
            f"{project['name']}: cycle {position.cycle} next slice is "
            f"{item.identifier} — {item.title}"
        )
    checkpoint(cycle_path(project["name"]), position, identifiers)

    branch = str(prepared["branch"]) if resuming else unique_branch(workspace)
    hook_active = False
    terminal_cleanup = False
    try:
        if not resuming:
            if isinstance(workspace_config, dict):
                token = workspace_config.get("management_token_file")
                worktrees.run_setup_hook_with_rollback(
                    source_path=Path(project["source_path"]),
                    workspace=workspace,
                    branch_prefix=BRANCH_PREFIX,
                    setup_command=workspace_config["setup_command"],
                    cleanup_command=workspace_config["cleanup_command"],
                    management_token_file=Path(token) if token else None,
                    resuming=False,
                    stream=stream,
                )
                hook_active = True
            elif (workspace / "pnpm-lock.yaml").is_file():
                runtime.run(
                    ["pnpm", "install", "--frozen-lockfile"],
                    cwd=workspace,
                    env=runtime.agent_environment(workspace),
                    stream=stream,
                )
            elif (workspace / "uv.lock").is_file():
                runtime.run(["uv", "sync"], cwd=workspace, stream=stream)
            runtime.git(workspace, "checkout", "-b", branch, stream=stream)
        elif isinstance(workspace_config, dict):
            token = workspace_config.get("management_token_file")
            worktrees.run_setup_hook_with_rollback(
                source_path=Path(project["source_path"]),
                workspace=workspace,
                branch_prefix=BRANCH_PREFIX,
                setup_command=workspace_config["setup_command"],
                cleanup_command=workspace_config["cleanup_command"],
                management_token_file=Path(token) if token else None,
                resuming=True,
                stream=stream,
            )
            hook_active = True

        evidence = prepare_context_evidence(
            config,
            project["name"],
            item.guidance_domains,
            workspace,
            stream,
        )
        git_config = runtime.protected_repository_config(workspace)
        original_head = runtime.git(workspace, "rev-parse", "HEAD").stdout.strip()
        agent = runtime.run_agent(
            config,
            workspace,
            agent_prompt(
                workspace,
                project,
                profile,
                item,
                position,
                branch,
                evidence,
                resuming,
            ),
            stream,
            environment_file=SCRIPT_DIR / ".env",
        )
        if agent.returncode != 0:
            raise MaintainerFailure(
                f"maintenance agent exited with code {agent.returncode}"
            )
        if runtime.protected_repository_config(workspace) != git_config:
            raise MaintainerFailure("maintenance agent changed local Git configuration")
        if runtime.git(workspace, "rev-parse", "HEAD").stdout.strip() != original_head:
            raise MaintainerFailure("maintenance agent changed commit history")
        if runtime.git(workspace, "branch", "--show-current").stdout.strip() != branch:
            raise MaintainerFailure("maintenance agent changed the controller-owned branch")
        status = runtime.git(workspace, "status", "--porcelain").stdout.strip()
        if not status:
            advance(
                cycle_path(project["name"]),
                position,
                identifiers,
                slice_id=item.identifier,
                outcome="audited-no-change",
            )
            terminal_cleanup = True
            return (
                f"{project['name']}: cycle {position.cycle} {item.identifier} "
                "required no source changes"
            )
        url = publish(
            workspace,
            config,
            project,
            item,
            position,
            branch,
            agent.stdout,
            stream,
        )
        atomic_json(
            pending_path(project["name"]),
            {
                "version": 1,
                "project": project["name"],
                "cycle": position.cycle,
                "index": position.index,
                "slice": item.identifier,
                "pull_request": int(url.rsplit("/", 1)[-1]),
                "url": url,
                "branch": branch,
                "created_at": runtime.now_iso(),
            },
        )
        terminal_cleanup = True
        return f"{project['name']}: created {url}"
    except (
        ContextFailure,
        CycleFailure,
        ProfileFailure,
        runtime.RuntimeFailure,
        worktrees.WorktreeFailure,
    ) as error:
        raise MaintainerFailure(str(error)) from error
    finally:
        if hook_active and isinstance(workspace_config, dict):
            token = workspace_config.get("management_token_file")
            worktrees.run_cleanup_hook(
                workspace,
                workspace_config["cleanup_command"],
                management_token_file=Path(token) if token else None,
                stream=stream,
            )
        if (
            terminal_cleanup
            and isinstance(workspace_config, dict)
            and workspace.exists()
        ):
            worktrees.remove_linked_worktree(
                source_path=Path(project["source_path"]),
                workspace=workspace,
                branch_prefix=BRANCH_PREFIX,
                stream=stream,
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=SCRIPT_DIR / "config.json")
    parser.add_argument("--project")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        runtime.load_environment_file(
            SCRIPT_DIR / ".env", os.environ, require_private=True
        )
        config = load_config(args.config)
        if not config["enabled"]:
            print("DISABLED — skipping code maintainer")
            return 0
        state = SCRIPT_DIR / "state"
        state.mkdir(parents=True, exist_ok=True)
        shared_state = REPO_ROOT / "state"
        shared_state.mkdir(parents=True, exist_ok=True)
        with (shared_state / "maintenance.lock").open("a+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("SKIPPED — another scheduled maintenance agent is running")
                return 0
            project = select_project(
                config, args.project, state / "rotation-index"
            )
            if not project:
                print("SKIPPED — no enabled maintainer projects")
                return 0
            logs = SCRIPT_DIR / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            with (logs / f"maintain_{stamp}.log").open("a") as stream:
                message = execute_project(
                    config, project, apply=args.apply, stream=stream
                )
            runtime.prune_logs(logs, "maintain_*.log")
            print(message)
        return 0
    except (
        OSError,
        MaintainerFailure,
        ContextFailure,
        CycleFailure,
        ProfileFailure,
        runtime.RuntimeFailure,
        worktrees.WorktreeFailure,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
