#!/usr/bin/env python3
"""Run one scheduled, behavior-preserving simplification slice."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
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
from policy import ConfigurationFailure, validate_config


SKILL_ROOT = SCRIPT_DIR / "skills" / "code-simplifier"
CHECKLIST_NAME = "simplification.md"
BRANCH_PREFIX = "code-simplify"
ITEM_PATTERN = re.compile(r"^(?P<prefix>\s*-\s*\[)(?P<status>[ xX])(?P<suffix>\]\s*)(?P<title>.+?)\s*$")
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
)


class SimplifierFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class ChecklistItem:
    line_index: int
    title: str
    line: str


def load_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SimplifierFailure(f"invalid simplifier config: {error}") from error
    if not isinstance(value, dict):
        raise SimplifierFailure("simplifier config must be a JSON object")
    try:
        validate_config(value)
    except ConfigurationFailure as error:
        raise SimplifierFailure(str(error)) from error
    return value


def first_unchecked_item(text: str) -> ChecklistItem | None:
    for index, line in enumerate(text.splitlines()):
        match = ITEM_PATTERN.match(line)
        if match and match.group("status") == " ":
            return ChecklistItem(index, match.group("title"), line)
    return None


def completed_checklist_text(original: str, item: ChecklistItem) -> str:
    lines = original.splitlines(keepends=True)
    line = lines[item.line_index]
    match = ITEM_PATTERN.match(line.rstrip("\r\n"))
    if not match or match.group("status") != " ":
        raise SimplifierFailure("selected checklist item changed unexpectedly")
    ending = line[len(line.rstrip("\r\n")) :]
    lines[item.line_index] = (
        f"{match.group('prefix')}x{match.group('suffix')}{match.group('title')}{ending}"
    )
    return "".join(lines)


def require_exact_transition(original: str, current: str, item: ChecklistItem) -> None:
    if current != completed_checklist_text(original, item):
        raise SimplifierFailure(
            "agent must change exactly the selected simplification marker from [ ] to [x]"
        )


def select_project(
    config: dict[str, Any], requested: str | None, rotation_path: Path
) -> dict[str, Any] | None:
    projects = config["projects"]
    if requested:
        return next(
            (project for project in projects if project["name"] == requested and project["enabled"]),
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
                checklist_file=Path(project["checklist_file"]),
                checklist_name=CHECKLIST_NAME,
                automation_label="simplifier",
                stream=stream,
            )
        return clones.prepare_workspace(
            source_path=Path(project["source_path"]),
            workspace_root=root,
            project_name=project["name"],
            base_branch=project["base_branch"],
            branch_prefix=BRANCH_PREFIX,
            environment_file=Path(project["environment_file"]),
            checklist_file=Path(project["checklist_file"]),
            checklist_name=CHECKLIST_NAME,
            automation_label="simplifier",
        )
    except (clones.WorkspaceFailure, worktrees.WorktreeFailure) as error:
        raise SimplifierFailure(str(error)) from error


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
                ref,
                check=False,
            ).returncode
            == 0
            for ref in (
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


def agent_prompt(
    workspace: Path,
    project: dict[str, Any],
    item: ChecklistItem,
    branch: str,
    resuming: bool,
) -> str:
    resume = (
        "\nThis is a resumed interrupted run. Inspect and continue the existing correct "
        "working-tree changes before making new edits.\n"
        if resuming
        else ""
    )
    return f"""Use the scheduled code-simplifier skill at {SKILL_ROOT / 'SKILL.md'}.

Run one complete simplification lifecycle in {workspace} on branch {branch}, based on origin/{project['base_branch']}.
The controller selected this exact first unchecked checklist item:

{item.line}

Repository validation commands: {json.dumps(project['validation_commands'])}

Read the skill and project instructions completely. Spawn its three read-only specialist sub-agents concurrently. You own focused checks, repository validation, iteration, and the fresh verifier. Never commit, push, create a PR, change Git configuration, or edit another checklist marker. Mark exactly the selected marker [x] only after the slice is ready. The controller trusts your validation judgment and only enforces safety, publication, and cleanup.{resume}

{pull_requests.MANUAL_UI_CHECKS_PROMPT}
"""


def protected_paths(paths: list[str]) -> list[str]:
    return [path for path in paths if any(pattern.search(path) for pattern in PROTECTED_PATTERNS)]


def pending_path(project_name: str) -> Path:
    return SCRIPT_DIR / "state" / "pending" / f"{project_name}.json"


def restore_pending_item(checklist: Path, pending: dict[str, Any]) -> None:
    current = checklist.read_text()
    completed = pending.get("completed_line")
    original = pending.get("original_line")
    if not isinstance(completed, str) or not isinstance(original, str):
        raise SimplifierFailure("pending simplification state cannot restore its checklist item")
    matches = [index for index, line in enumerate(current.splitlines()) if line == completed]
    if len(matches) != 1:
        raise SimplifierFailure("closed simplification PR checklist marker changed unexpectedly")
    lines = current.splitlines(keepends=True)
    line = lines[matches[0]]
    ending = line[len(line.rstrip("\r\n")) :]
    lines[matches[0]] = original + ending
    checklist.write_text("".join(lines))


def reconcile_pending(project: dict[str, Any], checklist: Path, stream: TextIO) -> str | None:
    path = pending_path(project["name"])
    if not path.exists():
        return None
    pending = json.loads(path.read_text())
    result = runtime.run(
        [
            "gh", "pr", "view", str(pending["pull_request"]),
            "--repo", project["repository"],
            "--json", "state,mergedAt,url",
        ],
        stream=stream,
    )
    value = json.loads(result.stdout)
    if value.get("state") == "OPEN":
        return f"{project['name']}: waiting for simplifier PR {value['url']}"
    if value.get("mergedAt"):
        path.unlink()
        return None
    restore_pending_item(checklist, pending)
    path.unlink()
    return None


def active_simplifier_pr(project: dict[str, Any], stream: TextIO) -> str | None:
    result = runtime.run(
        [
            "gh", "pr", "list", "--repo", project["repository"],
            "--state", "open", "--limit", "100", "--json", "url,headRefName",
        ],
        stream=stream,
    )
    for pull_request in json.loads(result.stdout):
        if str(pull_request.get("headRefName", "")).startswith(f"{BRANCH_PREFIX}/"):
            return str(pull_request["url"])
    return None


def publish(
    workspace: Path,
    project: dict[str, Any],
    item: ChecklistItem,
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
        raise SimplifierFailure(
            "refusing to publish protected paths: " + ", ".join(unsafe)
        )
    if not staged:
        raise SimplifierFailure("simplifier reported changes but staged diff is empty")
    runtime.git(workspace, "diff", "--cached", "--check", stream=stream)
    target = item.title[:72]
    runtime.git(workspace, "commit", "-m", f"refactor: simplify {target}", stream=stream)
    runtime.git(workspace, "push", "-u", "origin", branch, stream=stream)
    validation = "\n".join(" ".join(command) for command in project["validation_commands"])
    ui_section = pull_requests.manual_ui_section(
        pull_requests.manual_ui_checks(agent_output, staged, item.title)
    )
    result = runtime.run(
        [
            "gh", "pr", "create", "--repo", project["repository"],
            "--base", project["base_branch"], "--head", branch,
            "--title", f"refactor: simplify {target}",
            "--body", (
                f"## Simplification slice\n\nCompleted `{item.title}` with behavior-preserving "
                "reuse, maintainability, and efficiency review.\n\n## Validation\n\n"
                f"```text\n{validation}\n```\n\nValidation was owned by the simplification agent; "
                "the controller enforced publication safety and workspace cleanup.\n\n"
                f"{ui_section}"
            ),
        ],
        cwd=workspace,
        stream=stream,
    )
    url = result.stdout.strip().splitlines()[-1]
    if not re.fullmatch(r"https://github\.com/[^/]+/[^/]+/pull/\d+", url):
        raise SimplifierFailure(f"could not parse created PR URL: {url}")
    return url


def execute_project(
    config: dict[str, Any], project: dict[str, Any], *, apply: bool, stream: TextIO
) -> str:
    prepared = prepare_workspace(config, project, stream)
    workspace = Path(str(prepared["workspace"]))
    resuming = bool(prepared["resuming"])
    workspace_config = project.get("workspace")

    def finish_without_agent(message: str) -> str:
        if isinstance(workspace_config, dict) and not resuming and workspace.exists():
            worktrees.remove_linked_worktree(
                source_path=Path(project["source_path"]),
                workspace=workspace,
                branch_prefix=BRANCH_PREFIX,
                stream=stream,
            )
        return message

    checklist = workspace / CHECKLIST_NAME
    original = checklist.read_text()
    pending_message = reconcile_pending(project, checklist, stream)
    if pending_message:
        return finish_without_agent(pending_message)
    original = checklist.read_text()
    item = first_unchecked_item(original)
    if not item:
        return finish_without_agent(f"{project['name']}: checklist complete")
    active = active_simplifier_pr(project, stream)
    if active:
        return finish_without_agent(
            f"{project['name']}: waiting for simplifier PR {active}"
        )
    if not apply:
        return finish_without_agent(f"{project['name']}: next item is {item.title}")

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

        git_config = runtime.protected_repository_config(workspace)
        original_head = runtime.git(workspace, "rev-parse", "HEAD").stdout.strip()
        agent = runtime.run_agent(
            config,
            workspace,
            agent_prompt(workspace, project, item, branch, resuming),
            stream,
            environment_file=SCRIPT_DIR / ".env",
        )
        if agent.returncode != 0:
            checklist.write_text(original)
            raise SimplifierFailure(f"simplifier agent exited with code {agent.returncode}")
        if runtime.protected_repository_config(workspace) != git_config:
            checklist.write_text(original)
            raise SimplifierFailure("simplifier changed local Git configuration")
        if runtime.git(workspace, "rev-parse", "HEAD").stdout.strip() != original_head:
            checklist.write_text(original)
            raise SimplifierFailure("simplifier changed commit history")
        if runtime.git(workspace, "branch", "--show-current").stdout.strip() != branch:
            checklist.write_text(original)
            raise SimplifierFailure("simplifier changed the controller-owned branch")
        require_exact_transition(original, checklist.read_text(), item)
        status = runtime.git(workspace, "status", "--porcelain").stdout.strip()
        if not status:
            terminal_cleanup = True
            return f"{project['name']}: {item.title} required no source changes"
        url = publish(workspace, project, item, branch, agent.stdout, stream)
        completed_line = completed_checklist_text(original, item).splitlines()[
            item.line_index
        ]
        state = {
            "project": project["name"],
            "pull_request": int(url.rsplit("/", 1)[-1]),
            "url": url,
            "branch": branch,
            "created_at": runtime.now_iso(),
            "original_line": item.line,
            "completed_line": completed_line,
        }
        state_path = pending_path(project["name"])
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        terminal_cleanup = True
        return f"{project['name']}: created {url}"
    except (runtime.RuntimeFailure, worktrees.WorktreeFailure) as error:
        raise SimplifierFailure(str(error)) from error
    finally:
        if hook_active and isinstance(workspace_config, dict):
            token = workspace_config.get("management_token_file")
            worktrees.run_cleanup_hook(
                workspace,
                workspace_config["cleanup_command"],
                management_token_file=Path(token) if token else None,
                stream=stream,
            )
        if terminal_cleanup and isinstance(workspace_config, dict) and workspace.exists():
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
            print("DISABLED — skipping code simplifier")
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
            project = select_project(config, args.project, state / "rotation-index")
            if not project:
                print("SKIPPED — no enabled simplifier projects")
                return 0
            logs = SCRIPT_DIR / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            with (logs / f"simplify_{stamp}.log").open("a") as stream:
                message = execute_project(config, project, apply=args.apply, stream=stream)
            runtime.prune_logs(logs, "simplify_*.log")
            print(message)
        return 0
    except (
        OSError,
        SimplifierFailure,
        runtime.RuntimeFailure,
        worktrees.WorktreeFailure,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
