#!/usr/bin/env python3
"""Run one evidence-backed slice in a perpetual code-maintenance cycle."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from automation import clones, completion_reporter, pull_requests, runtime, worktrees
from context_evidence import (
    ContextFailure,
    ensure_provider_context,
    prepare_context_evidence,
)
from cycles import (
    CycleFailure,
    CyclePosition,
    advance,
    atomic_json,
    checkpoint,
    load_position,
)
from policy import ConfigurationFailure, resolve_context, validate_config
from profiles import (
    MaintenanceSlice,
    ProfileFailure,
    ProjectProfile,
    load_project_profile,
    missing_profile_selectors,
    stale_selector_failure,
    validate_profile_selectors,
)
from slice_repair import SliceRepairFailure, repair_stale_slice_registry
from reporting import (
    MAINTENANCE_REPORT_PROMPT,
    REPORT_FIELD,
    ReportFailure,
    maintenance_report_sections,
    parse_maintenance_report,
)

MAINTAINER_PROCESS_GUIDANCE = """
Shared process rules:
- Do not run tests, typechecks, linters, builds, or repository validation commands.
- Do not start detached or background commands.
- Inspect the final diff and run only `git diff --check`; GitHub PR checks own validation.
""".strip()


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


def ensure_disk_capacity(config: dict[str, Any], stream: TextIO) -> None:
    """Prune recoverable package cache and fail before setup when disk is tight."""

    minimum = int(config.get("minimum_free_bytes", 1024**3))
    free = shutil.disk_usage(REPO_ROOT).free
    if free >= minimum:
        return
    stream.write(
        f"Free disk is {free} bytes; pruning the pnpm store before maintenance.\n"
    )
    stream.flush()
    runtime.run(["pnpm", "store", "prune"], stream=stream)
    free = shutil.disk_usage(REPO_ROOT).free
    if free < minimum:
        raise MaintainerFailure(
            f"insufficient disk before maintenance: {free} bytes free, "
            f"{minimum} required"
        )


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


def enabled_slice(
    config: dict[str, Any], item: MaintenanceSlice
) -> MaintenanceSlice:
    """Return the selected slice with runtime-disabled roles removed."""

    agents = config.get("agents", {})
    roles = tuple(role for role in item.roles if agents.get(role, True))
    if not roles:
        raise MaintainerFailure(
            f"semantic slice {item.identifier} has no enabled specialist roles"
        )
    return replace(item, roles=roles)


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
        "\nThis is a resumed interrupted run whose specialists and bounded edits "
        "already completed. Do not rerun specialists or make new edits. Inspect "
        "the existing working-tree diff, reconcile its evidence, run only "
        "`git diff --check`, and return the required report immediately.\n"
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
Pull-request validation commands (context only; do not run): {json.dumps(project['validation_commands'])}

Read the skill, repository instructions, project manifest, shared context, and
the exact role-specific references routed by the manifest. Resolve selectors
against the current repository before drawing conclusions; search terms are
discovery hints, never authorization to edit unrelated code. Run every
specialist role listed in the slice, in bounded concurrent batches if provider
limits prevent one batch. The specialists are read-only. You alone reconcile
evidence, make bounded edits, and inspect the final diff. Do not run tests,
typechecks, linters, builds, validation commands, or a separate verifier; the
repository's pull-request checks own validation after publication.

Never commit, push, create a PR, alter Git configuration, edit maintenance
cycle state, or modify trusted agent policy. The controller owns publication
and advances this semantic slice only after a no-change audit or merged PR.
{resume}

{MAINTENANCE_REPORT_PROMPT}

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
    try:
        report = parse_maintenance_report(agent_output, item.roles)
    except ReportFailure as error:
        raise MaintainerFailure(
            f"invalid maintenance publication report: {error}"
        ) from error
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
                f"{maintenance_report_sections(report)}\n\n"
                "## Pull-request validation\n\n"
                "Validation is delegated to the repository's checks after this PR "
                "is opened. The maintainer did not run these commands locally:\n\n"
                f"```text\n{validation}\n```\n\n"
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
    config: dict[str, Any],
    project: dict[str, Any],
    *,
    apply: bool,
    stream: TextIO,
    completion: dict[str, str] | None = None,
) -> str:
    if completion is not None:
        completion["repo"] = project["repository"]
    if apply:
        ensure_disk_capacity(config, stream)
    profile = profile_for(project)
    identifiers = slice_ids(profile)
    if apply:
        context_config = dict(config)
        context_config["context"] = resolve_context(config, project)
        domains = tuple(
            sorted(
                {
                    domain
                    for maintenance_slice in profile.slices
                    for domain in maintenance_slice.guidance_domains
                }
            )
        )
        ensure_provider_context(
            context_config,
            project["name"],
            domains,
            stream,
        )
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
        if completion is not None:
            completion["summary"] = pending_message
            match = re.search(r"https://github\.com/[^\s]+/pull/\d+", pending_message)
            if match:
                completion["pr_url"] = match.group(0)
        return finish_without_agent(pending_message)
    if not resuming:
        missing = missing_profile_selectors(profile, workspace)
        if missing:
            if not apply:
                raise stale_selector_failure(profile, workspace, missing)
            repair_stale_slice_registry(
                config, project, profile, workspace, missing, stream
            )
            profile = profile_for(project)
            identifiers = slice_ids(profile)
            validate_profile_selectors(profile, workspace)
    position = load_position(cycle_path(project["name"]), identifiers)
    item = enabled_slice(config, current_slice(profile, position))
    active = active_maintainer_pr(project, stream)
    if active:
        message = f"{project['name']}: waiting for maintainer PR {active}"
        if completion is not None:
            completion.update(summary=message, pr_url=active)
        return finish_without_agent(message)
    if not apply:
        message = (
            f"{project['name']}: cycle {position.cycle} next slice is "
            f"{item.identifier} — {item.title}"
        )
        if completion is not None:
            completion["summary"] = message
        return finish_without_agent(message)
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
            stream.write(
                "Resuming preserved worktree without rerunning dependency or "
                "local-service setup.\n"
            )
            stream.flush()

        if resuming:
            evidence = SCRIPT_DIR / "state" / "context" / project["name"] / "evidence.json"
            if not evidence.is_file():
                raise MaintainerFailure(
                    "preserved worktree cannot resume without its audited context evidence"
                )
            stream.write(f"Reusing audited context evidence: {evidence}\n")
            stream.flush()
        else:
            evidence_config = dict(config)
            evidence_config["context"] = resolve_context(config, project)
            evidence = prepare_context_evidence(
                evidence_config,
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
            report_field=REPORT_FIELD,
            persist_session=config.get("provider", "codex") == "codex",
            process_guidance=MAINTAINER_PROCESS_GUIDANCE,
        )
        if agent.returncode != 0:
            raise MaintainerFailure(
                f"maintenance agent exited with code {agent.returncode}"
            )
        session_id = ""
        agent_output = agent.stdout
        if config.get("provider", "codex") == "codex":
            session_id, agent_output = runtime.codex_session(agent.stdout)
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
            message = (
                f"{project['name']}: cycle {position.cycle} {item.identifier} "
                "required no source changes"
            )
            if completion is not None:
                completion["summary"] = message
            return message
        url = publish(
            workspace,
            config,
            project,
            item,
            position,
            branch,
            agent_output,
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
                "head_sha": runtime.git(workspace, "rev-parse", "HEAD").stdout.strip(),
                "codex_session_id": session_id,
                "ci_repair_attempts": 0,
            },
        )
        terminal_cleanup = True
        message = f"{project['name']}: created {url}"
        if completion is not None:
            completion.update(
                summary=message,
                pr_url=url,
                commit=runtime.git(workspace, "rev-parse", "HEAD").stdout.strip(),
            )
        return message
    except (
        ContextFailure,
        CycleFailure,
        ProfileFailure,
        SliceRepairFailure,
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
    result = 2
    completion = {
        "repo": args.project or "code-maintainer",
        "summary": "Code maintenance controller crashed before producing a result.",
        "pr_url": "",
        "commit": "",
    }
    try:
        runtime.load_environment_file(
            SCRIPT_DIR / ".env", os.environ, require_private=True
        )
        config = load_config(args.config)
        if not config["enabled"]:
            completion["summary"] = "DISABLED — skipping code maintainer"
            print(completion["summary"])
            result = 0
            return result
        state = SCRIPT_DIR / "state"
        state.mkdir(parents=True, exist_ok=True)
        shared_state = REPO_ROOT / "state"
        shared_state.mkdir(parents=True, exist_ok=True)
        with (shared_state / "maintenance.lock").open("a+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                completion["summary"] = (
                    "SKIPPED — another scheduled maintenance agent is running"
                )
                print(completion["summary"])
                result = 0
                return result
            project = select_project(
                config, args.project, state / "rotation-index"
            )
            if not project:
                completion["summary"] = "SKIPPED — no enabled maintainer projects"
                print(completion["summary"])
                result = 0
                return result
            logs = SCRIPT_DIR / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            with (logs / f"maintain_{stamp}.log").open("a") as stream:
                message = execute_project(
                    config,
                    project,
                    apply=args.apply,
                    stream=stream,
                    completion=completion,
                )
            runtime.prune_logs(logs, "maintain_*.log")
            print(message)
        result = 0
        return result
    except (
        OSError,
        MaintainerFailure,
        ContextFailure,
        CycleFailure,
        ProfileFailure,
        SliceRepairFailure,
        runtime.RuntimeFailure,
        worktrees.WorktreeFailure,
        subprocess.TimeoutExpired,
    ) as error:
        completion["summary"] = f"Blocked: {error}"
        print(f"BLOCKED: {error}", file=sys.stderr)
        result = 2
        return result
    finally:
        completion_reporter.report_completion(
            job="code-maintainer",
            repo=completion["repo"],
            status="success" if result == 0 else "failure",
            summary=completion["summary"],
            pr_url=completion["pr_url"],
            commit=completion["commit"],
            env_path=SCRIPT_DIR / ".env",
        )


if __name__ == "__main__":
    raise SystemExit(main())
