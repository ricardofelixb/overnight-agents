#!/usr/bin/env python3
"""Run one scheduled, behavior-preserving codebase organization slice."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SHARED_WORKSPACE_MODULE = REPO_ROOT / "code-simplifier" / "workspace.py"
SKILL_ROOT = SCRIPT_DIR / "skills" / "codebase-organizer"
CHECKLIST_NAME = "organization.md"
BRANCH_PREFIX = "code-organize"
ITEM_PATTERN = re.compile(
    r"^- \[(?P<status>[ xX])\] \*\*(?P<id>[a-z0-9][a-z0-9-]*)\*\*\s+[—-]\s+(?P<title>.+?)\s*$"
)


class OrganizerFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class ChecklistItem:
    line_index: int
    item_id: str
    title: str
    block: str


def load_workspace_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "organizer_shared_workspace", SHARED_WORKSPACE_MODULE
    )
    if not spec or not spec.loader:
        raise OrganizerFailure("could not load shared workspace module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise OrganizerFailure(f"invalid organizer config: {error}") from error
    if not isinstance(value, dict):
        raise OrganizerFailure("organizer config must be a JSON object")
    validate_config(value)
    return value


def validate_config(config: dict[str, Any]) -> None:
    if not isinstance(config.get("enabled", False), bool):
        raise OrganizerFailure("enabled must be a boolean")
    if not isinstance(config.get("schedule"), str) or not config["schedule"].strip():
        raise OrganizerFailure("schedule must be a non-empty string")
    provider = config.get("provider", "codex")
    if provider not in {"codex", "claude"}:
        raise OrganizerFailure("provider must be codex or claude")
    if not isinstance(config.get("projects"), list) or not config["projects"]:
        raise OrganizerFailure("projects must be a non-empty array")
    names: set[str] = set()
    for project in config["projects"]:
        if not isinstance(project, dict):
            raise OrganizerFailure("each project must be an object")
        name = project.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise OrganizerFailure("project name is missing or unsafe")
        if name in names:
            raise OrganizerFailure(f"duplicate project name: {name}")
        names.add(name)
        if not isinstance(project.get("enabled", False), bool):
            raise OrganizerFailure(f"project {name} enabled must be a boolean")
        for field in (
            "source_path",
            "repository",
            "base_branch",
            "environment_file",
            "checklist_file",
        ):
            if not isinstance(project.get(field), str) or not project[field]:
                raise OrganizerFailure(f"project {name} requires {field}")
        commands = project.get("validation_commands")
        if not isinstance(commands, list) or not commands:
            raise OrganizerFailure(f"project {name} requires validation_commands")
        for command in commands:
            if (
                not isinstance(command, list)
                or not command
                or any(not isinstance(part, str) or not part for part in command)
            ):
                raise OrganizerFailure(
                    f"project {name} validation commands must be non-empty string arrays"
                )


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: int | None = None,
    stream: TextIO | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=timeout,
    )
    if stream:
        stream.write(f"[{now_iso()}] RUN {' '.join(command[:3])}\n")
        stream.write(result.stdout)
        if result.stdout and not result.stdout.endswith("\n"):
            stream.write("\n")
        stream.flush()
    if check and result.returncode != 0:
        raise OrganizerFailure(
            f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}"
        )
    return result


def git(
    cwd: Path,
    *arguments: str,
    check: bool = True,
    stream: TextIO | None = None,
) -> subprocess.CompletedProcess[str]:
    return run(["git", *arguments], cwd=cwd, check=check, stream=stream)


def first_unchecked_item(text: str) -> ChecklistItem | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = ITEM_PATTERN.match(line)
        if not match or match.group("status") != " ":
            continue
        end = len(lines)
        for candidate in range(index + 1, len(lines)):
            if ITEM_PATTERN.match(lines[candidate]):
                end = candidate
                break
        return ChecklistItem(
            line_index=index,
            item_id=match.group("id"),
            title=match.group("title"),
            block="\n".join(lines[index:end]).strip(),
        )
    return None


def completed_checklist_text(original: str, item: ChecklistItem) -> str:
    lines = original.splitlines(keepends=True)
    line = lines[item.line_index]
    if "- [ ]" not in line:
        raise OrganizerFailure("selected checklist marker changed unexpectedly")
    lines[item.line_index] = line.replace("- [ ]", "- [x]", 1)
    return "".join(lines)


def require_exact_checklist_transition(
    original: str, current: str, item: ChecklistItem
) -> None:
    expected = completed_checklist_text(original, item)
    if current != expected:
        raise OrganizerFailure(
            "agent must change exactly the selected organization marker from [ ] to [x]"
        )


def atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def select_project(
    config: dict[str, Any], requested: str | None, state_file: Path
) -> dict[str, Any] | None:
    enabled = [project for project in config["projects"] if project["enabled"]]
    if requested:
        for project in enabled:
            if project["name"] == requested:
                return project
        raise OrganizerFailure(f"enabled project not found: {requested}")
    if not enabled:
        return None
    try:
        index = int(state_file.read_text().strip()) if state_file.exists() else 0
    except ValueError:
        index = 0
    selected = enabled[index % len(enabled)]
    atomic_write(state_file, str((index + 1) % len(enabled)) + "\n")
    return selected


def active_organizer_pr(repository: str, stream: TextIO) -> dict[str, Any] | None:
    result = run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repository,
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,headRefName,url",
        ],
        stream=stream,
    )
    values = json.loads(result.stdout)
    for value in values:
        if str(value.get("headRefName", "")).startswith(f"{BRANCH_PREFIX}/"):
            return value
    return None


def restore_item_marker(checklist_path: Path, item_id: str) -> None:
    text = checklist_path.read_text()
    pattern = re.compile(
        rf"^- \[[xX]\] \*\*{re.escape(item_id)}\*\*", re.MULTILINE
    )
    updated, count = pattern.subn(f"- [ ] **{item_id}**", text, count=1)
    if count != 1:
        raise OrganizerFailure(
            f"could not restore checklist item after an unmerged PR: {item_id}"
        )
    atomic_write(checklist_path, updated)


def reconcile_pending(
    project: dict[str, Any], checklist_path: Path, stream: TextIO
) -> str | None:
    pending_path = SCRIPT_DIR / "state" / "pending" / f"{project['name']}.json"
    if not pending_path.exists():
        return None
    try:
        pending = json.loads(pending_path.read_text())
        number = int(pending["pull_request"])
        item_id = str(pending["item_id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise OrganizerFailure(f"invalid pending organizer state: {error}") from error
    result = run(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            project["repository"],
            "--json",
            "state,mergedAt,url",
        ],
        stream=stream,
    )
    value = json.loads(result.stdout)
    if value.get("state") == "OPEN":
        return f"{project['name']}: waiting for organizer PR {value['url']}"
    if value.get("mergedAt"):
        pending_path.unlink()
        return None
    restore_item_marker(checklist_path, item_id)
    pending_path.unlink()
    return None


def agent_prompt(
    *,
    workspace: Path,
    base_branch: str,
    branch: str,
    item: ChecklistItem,
    resuming: bool,
    validation_commands: list[list[str]],
) -> str:
    resume = (
        "\nThis is a resumed interrupted run. Inspect and continue the existing working-tree "
        "changes; do not discard correct unfinished work.\n"
        if resuming
        else ""
    )
    return f"""Use the codebase-organizer skill at {SKILL_ROOT / 'SKILL.md'}.

You are in {workspace} on branch {branch}, based on origin/{base_branch}.
Execute exactly this approved organization checklist item:

{item.block}

Run focused checks while working, then run the definitive validation command yourself and keep diagnosing and repairing until it passes or you reach a concrete blocker:

{json.dumps(validation_commands)}

The deterministic controller will independently rerun that validation before publication and owns commit, push, and PR creation. Do not commit, push, create or edit a PR, comment on GitHub, merge, weaken validation, or modify any other checklist item. Change exactly this top-level marker from [ ] to [x] only after the behavior-preserving source refactor is complete.{resume}
"""


def correction_prompt(
    item: ChecklistItem, output: str, validation_commands: list[list[str]]
) -> str:
    tail = output[-12000:]
    return f"""Use the codebase-organizer skill at {SKILL_ROOT / 'SKILL.md'}.

The approved item is still exactly:

{item.block}

The controller's independent validation failed. Diagnose the failure, continue repairing the approved organization slice, and run the definitive validation yourself until it passes or you reach a concrete blocker:

{json.dumps(validation_commands)}

You may iterate as many times as useful. Preserve behavior and do not weaken tests, types, lint, configuration, schema, permissions, or validation. Do not change additional checklist items, commit, push, or create a PR.

Validation output:
{tail}
"""


def working_tree_fingerprint(workspace: Path) -> str:
    digest = hashlib.sha256()
    digest.update(git(workspace, "diff", "--binary", "HEAD").stdout.encode())
    untracked = git(
        workspace, "ls-files", "--others", "--exclude-standard", "-z"
    ).stdout.split("\0")
    for relative in sorted(path for path in untracked if path):
        digest.update(relative.encode())
        path = workspace / relative
        if path.is_file() and not path.is_symlink():
            digest.update(path.read_bytes())
        elif path.is_symlink():
            digest.update(os.readlink(path).encode())
    return digest.hexdigest()


def validate_with_agent_corrections(
    config: dict[str, Any],
    workspace: Path,
    item: ChecklistItem,
    original_checklist: str,
    checklist_path: Path,
    commands: list[list[str]],
    stream: TextIO,
) -> None:
    validation = run_validation(workspace, commands, stream)
    while validation.returncode != 0:
        before = working_tree_fingerprint(workspace)
        correction = run_agent(
            config,
            workspace,
            correction_prompt(item, validation.stdout, commands),
            stream,
        )
        if correction.returncode != 0:
            raise OrganizerFailure(
                f"organizer correction agent exited with code {correction.returncode}"
            )
        require_exact_checklist_transition(
            original_checklist, checklist_path.read_text(), item
        )
        validation = run_validation(workspace, commands, stream)
        if validation.returncode != 0 and working_tree_fingerprint(workspace) == before:
            raise OrganizerFailure(
                "definitive validation still failed and the agent made no repository progress"
            )


def run_agent(
    config: dict[str, Any], workspace: Path, prompt: str, stream: TextIO
) -> subprocess.CompletedProcess[str]:
    provider = config.get("provider", "codex")
    timeout = int(config.get("agent_timeout_seconds", 7200))
    if provider == "codex":
        command = [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "-c",
            f'model_reasoning_effort="{config.get("codex_reasoning_effort", "medium")}"',
            "-m",
            str(config.get("codex_model", "gpt-5.6-terra")),
            "-C",
            str(workspace),
            prompt,
        ]
    else:
        command = [
            "claude",
            "--dangerously-skip-permissions",
            "--model",
            str(config.get("claude_model", "claude-opus-4-8")),
            "--effort",
            str(config.get("claude_effort", "medium")),
            "-p",
            prompt,
        ]
    environment = os.environ.copy()
    environment.pop("CLAUDECODE", None)
    return run(
        command,
        cwd=workspace,
        env=environment,
        check=False,
        timeout=timeout,
        stream=stream,
    )


def install_dependencies(workspace: Path, stream: TextIO) -> None:
    if (workspace / "pnpm-lock.yaml").is_file():
        run(
            ["pnpm", "install", "--frozen-lockfile"],
            cwd=workspace,
            stream=stream,
        )
    elif (workspace / "uv.lock").is_file():
        run(["uv", "sync"], cwd=workspace, stream=stream)


def run_validation(
    workspace: Path, commands: list[list[str]], stream: TextIO
) -> subprocess.CompletedProcess[str]:
    combined: list[str] = []
    last = subprocess.CompletedProcess([], 0, "", "")
    for command in commands:
        last = run(command, cwd=workspace, check=False, stream=stream)
        combined.append(last.stdout)
        if last.returncode != 0:
            return subprocess.CompletedProcess(
                command, last.returncode, "\n".join(combined), ""
            )
    return subprocess.CompletedProcess([], 0, "\n".join(combined), "")


def sensitive_staged_path(path: str) -> bool:
    name = Path(path).name
    return name == ".env" or name.startswith(".env.") or name.endswith(".pem")


def publish(
    *,
    workspace: Path,
    project: dict[str, Any],
    item: ChecklistItem,
    branch: str,
    validation_commands: list[list[str]],
    stream: TextIO,
) -> tuple[int, str]:
    git(workspace, "diff", "--check", stream=stream)
    git(workspace, "add", "-A", stream=stream)
    staged = git(workspace, "diff", "--cached", "--name-only", stream=stream).stdout.splitlines()
    unsafe = [path for path in staged if sensitive_staged_path(path)]
    if unsafe:
        raise OrganizerFailure(
            "refusing to publish sensitive-looking paths: " + ", ".join(unsafe)
        )
    if not staged:
        raise OrganizerFailure("organizer reported changes but staged diff is empty")
    git(workspace, "diff", "--cached", "--check", stream=stream)
    git(workspace, "commit", "-m", f"refactor: organize {item.title}", stream=stream)
    git(workspace, "push", "-u", "origin", branch, stream=stream)
    validation = "\n".join(" ".join(command) for command in validation_commands)
    body = (
        "## Organization slice\n\n"
        f"Completed `{item.item_id}` as one behavior-preserving backend/frontend source refactor.\n\n"
        "- Internal source vocabulary remains English.\n"
        "- Client-facing Spanish copy and routes are preserved.\n"
        "- Old paths were removed without barrels, aliases, shims, or legacy fallbacks.\n\n"
        "## Validation\n\n"
        f"```text\n{validation}\n```\n\n"
        "The complete configured validation gate passed before publication."
    )
    result = run(
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
            f"refactor: organize {item.title}",
            "--body",
            body,
        ],
        cwd=workspace,
        stream=stream,
    )
    url = result.stdout.strip().splitlines()[-1]
    number_match = re.search(r"/(\d+)$", url)
    if not number_match:
        raise OrganizerFailure(f"could not parse created PR URL: {url}")
    return int(number_match.group(1)), url


def unique_branch(workspace: Path, item: ChecklistItem) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    base = f"{BRANCH_PREFIX}/{item.item_id}-{date}"
    if git(
        workspace, "show-ref", "--verify", "--quiet", f"refs/heads/{base}", check=False
    ).returncode != 0:
        return base
    return f"{base}-{datetime.now().strftime('%H%M%S')}"


def prepare_workspace(
    project: dict[str, Any], config: dict[str, Any]
) -> dict[str, str | bool]:
    module = load_workspace_module()
    return module.prepare_workspace(
        source_path=Path(project["source_path"]),
        workspace_root=Path(
            config.get("workspace_root", SCRIPT_DIR / "state" / "workspaces")
        ),
        project_name=project["name"],
        base_branch=project["base_branch"],
        branch_prefix=BRANCH_PREFIX,
        environment_file=Path(project["environment_file"]),
        checklist_file=Path(project["checklist_file"]),
        checklist_name=CHECKLIST_NAME,
        automation_label="organizer",
    )


def execute_project(
    config: dict[str, Any], project: dict[str, Any], *, apply: bool, stream: TextIO
) -> str:
    checklist_path = Path(project["checklist_file"]).expanduser().resolve(strict=True)
    pending_message = reconcile_pending(project, checklist_path, stream)
    if pending_message:
        return pending_message
    original = checklist_path.read_text()
    item = first_unchecked_item(original)
    if not item:
        return f"{project['name']}: checklist complete"
    active = active_organizer_pr(project["repository"], stream)
    if active:
        return f"{project['name']}: waiting for organizer PR {active['url']}"
    if not apply:
        return f"{project['name']}: next item is {item.item_id} — {item.title}"

    prepared = prepare_workspace(project, config)
    workspace = Path(str(prepared["workspace"]))
    resuming = bool(prepared["resuming"])
    if resuming:
        branch = str(prepared["branch"])
    else:
        install_dependencies(workspace, stream)
        branch = unique_branch(workspace, item)
        git(workspace, "checkout", "-b", branch, stream=stream)

    prompt = agent_prompt(
        workspace=workspace,
        base_branch=project["base_branch"],
        branch=branch,
        item=item,
        resuming=resuming,
        validation_commands=project["validation_commands"],
    )
    agent = run_agent(config, workspace, prompt, stream)
    if agent.returncode != 0:
        atomic_write(checklist_path, original)
        raise OrganizerFailure(f"organizer agent exited with code {agent.returncode}")
    require_exact_checklist_transition(original, checklist_path.read_text(), item)

    status = git(workspace, "status", "--porcelain").stdout.strip()
    if not status:
        return f"{project['name']}: {item.item_id} required no source changes"

    commands = project["validation_commands"]
    try:
        validate_with_agent_corrections(
            config,
            workspace,
            item,
            original,
            checklist_path,
            commands,
            stream,
        )
    except OrganizerFailure:
        atomic_write(checklist_path, original)
        raise

    require_exact_checklist_transition(original, checklist_path.read_text(), item)
    number, url = publish(
        workspace=workspace,
        project=project,
        item=item,
        branch=branch,
        validation_commands=commands,
        stream=stream,
    )
    pending = {
        "item_id": item.item_id,
        "project": project["name"],
        "pull_request": number,
        "url": url,
        "branch": branch,
        "created_at": now_iso(),
    }
    pending_path = SCRIPT_DIR / "state" / "pending" / f"{project['name']}.json"
    atomic_write(pending_path, json.dumps(pending, indent=2, sort_keys=True) + "\n")
    return f"{project['name']}: created {url}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=SCRIPT_DIR / "config.json")
    parser.add_argument("--project")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        if not config["enabled"]:
            print("DISABLED — skipping codebase organizer")
            return 0
        state_root = SCRIPT_DIR / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        shared_state = REPO_ROOT / "state"
        shared_state.mkdir(parents=True, exist_ok=True)
        lock_path = shared_state / "maintenance.lock"
        with lock_path.open("a+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("SKIPPED — another scheduled maintenance agent is running")
                return 0
            project = select_project(
                config, args.project, state_root / "rotation-index"
            )
            if not project:
                print("SKIPPED — no enabled organizer projects")
                return 0
            logs = SCRIPT_DIR / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            with (logs / f"organize_{stamp}.log").open("a") as stream:
                message = execute_project(config, project, apply=args.apply, stream=stream)
            print(message)
            return 0
    except (OSError, OrganizerFailure, subprocess.TimeoutExpired) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
