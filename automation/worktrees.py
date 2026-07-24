#!/usr/bin/env python3
"""Controller-owned linked worktrees with repository-defined lifecycle hooks."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO


class WorktreeFailure(RuntimeError):
    pass


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
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
    )
    if stream:
        stream.write(f"RUN {' '.join(command[:3])}\n")
        stream.write(result.stdout)
        if result.stdout and not result.stdout.endswith("\n"):
            stream.write("\n")
        stream.flush()
    if check and result.returncode != 0:
        raise WorktreeFailure(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stdout}"
        )
    return result


def git(
    cwd: Path,
    *arguments: str,
    check: bool = True,
    stream: TextIO | None = None,
) -> subprocess.CompletedProcess[str]:
    return run(["git", *arguments], cwd=cwd, check=check, stream=stream)


def safe_workspace(
    workspace_root: Path,
    project_name: str,
    source_path: Path,
    automation_label: str,
) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", project_name):
        raise WorktreeFailure(
            "project name must contain only letters, digits, dots, underscores, or hyphens"
        )
    root = workspace_root.expanduser().resolve()
    workspace = (root / project_name).resolve()
    source = source_path.expanduser().resolve()
    if root == Path("/") or workspace == root or root not in workspace.parents:
        raise WorktreeFailure(f"unsafe {automation_label} workspace path")
    if workspace == source or workspace in source.parents or source in workspace.parents:
        raise WorktreeFailure(f"{automation_label} workspace overlaps the source checkout")
    return workspace


def git_common_directory(cwd: Path) -> Path:
    value = git(cwd, "rev-parse", "--git-common-dir").stdout.strip()
    path = Path(value)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve(strict=True)


def is_linked_to_source(workspace: Path, source: Path) -> bool:
    if not (workspace / ".git").is_file():
        return False
    try:
        return git_common_directory(workspace) == git_common_directory(source)
    except (OSError, WorktreeFailure):
        return False


def quarantine_directory(workspace: Path, reason: str) -> Path:
    root = workspace.parent / ".quarantine"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = root / f"{workspace.name}-{stamp}-{os.getpid()}-{reason}"
    workspace.rename(destination)
    return destination


def quarantine_linked_worktree(source: Path, workspace: Path, reason: str) -> Path:
    root = workspace.parent / ".quarantine"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = root / f"{workspace.name}-{stamp}-{os.getpid()}-{reason}"
    git(source, "worktree", "move", str(workspace), str(destination))
    return destination


def require_ignored(cwd: Path, relative_path: str) -> None:
    if git(cwd, "check-ignore", "-q", relative_path, check=False).returncode != 0:
        raise WorktreeFailure(f"{relative_path} must be ignored before provisioning")


def ensure_locally_ignored(cwd: Path, relative_path: str) -> None:
    if "\n" in relative_path or relative_path.startswith("/") or ".." in Path(relative_path).parts:
        raise WorktreeFailure("unsafe local exclude path")
    if git(cwd, "check-ignore", "-q", relative_path, check=False).returncode == 0:
        return
    value = git(cwd, "rev-parse", "--git-path", "info/exclude").stdout.strip()
    exclude = Path(value)
    if not exclude.is_absolute():
        exclude = cwd / exclude
    exclude = exclude.resolve()
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text() if exclude.exists() else ""
    entry = f"/{relative_path}\n"
    if entry not in existing:
        separator = "" if not existing or existing.endswith("\n") else "\n"
        exclude.write_text(existing + separator + entry)
    require_ignored(cwd, relative_path)


def provision_checklist(
    workspace: Path, checklist_file: Path | None, checklist_name: str
) -> None:
    if checklist_file is None:
        return
    tracked = git(
        workspace,
        "ls-files",
        "--error-unmatch",
        checklist_name,
        check=False,
    ).returncode == 0
    if tracked:
        return
    checklist = checklist_file.expanduser().resolve(strict=True)
    if not checklist.is_file():
        raise WorktreeFailure("checklist is not a regular file")
    ensure_locally_ignored(workspace, checklist_name)
    destination = workspace / checklist_name
    if destination.is_symlink():
        if destination.resolve(strict=False) == checklist:
            return
        raise WorktreeFailure(f"{checklist_name} points to an unexpected file")
    if destination.exists():
        raise WorktreeFailure(
            f"{checklist_name} exists but is not a controller-managed symlink"
        )
    destination.symlink_to(checklist)


def prepare_linked_worktree(
    *,
    source_path: Path,
    workspace_root: Path,
    project_name: str,
    base_branch: str,
    branch_prefix: str,
    checklist_file: Path | None = None,
    checklist_name: str = "simplification.md",
    automation_label: str = "maintenance",
    stream: TextIO | None = None,
) -> dict[str, str | bool]:
    source = source_path.expanduser().resolve(strict=True)
    git_common_directory(source)
    workspace = safe_workspace(
        workspace_root, project_name, source, automation_label
    )
    workspace.parent.mkdir(parents=True, exist_ok=True)

    if workspace.exists():
        if (workspace / ".git").is_dir():
            quarantine_directory(workspace, "legacy-clone")
        elif not (workspace / ".git").is_file():
            quarantine_directory(workspace, "not-a-worktree")
        elif not is_linked_to_source(workspace, source):
            raise WorktreeFailure(
                f"existing {automation_label} worktree belongs to another repository"
            )

    if workspace.exists():
        status = git(workspace, "status", "--porcelain").stdout.strip()
        branch = git(
            workspace, "branch", "--show-current", check=False
        ).stdout.strip()
        if status and branch.startswith(f"{branch_prefix}/"):
            provision_checklist(workspace, checklist_file, checklist_name)
            return {
                "workspace": str(workspace),
                "resuming": True,
                "branch": branch,
                "created": False,
            }
        if status:
            quarantine_linked_worktree(source, workspace, "dirty-unexpected-branch")

    git(source, "fetch", "--prune", "origin", base_branch, stream=stream)
    if workspace.exists():
        git(workspace, "checkout", "--detach", f"origin/{base_branch}", stream=stream)
        git(workspace, "reset", "--hard", f"origin/{base_branch}", stream=stream)
        created = False
    else:
        git(
            source,
            "worktree",
            "add",
            "--detach",
            str(workspace),
            f"origin/{base_branch}",
            stream=stream,
        )
        created = True
    if git(workspace, "status", "--porcelain").stdout.strip():
        raise WorktreeFailure(
            f"prepared {automation_label} worktree is unexpectedly dirty"
        )
    provision_checklist(workspace, checklist_file, checklist_name)
    return {
        "workspace": str(workspace),
        "resuming": False,
        "branch": "",
        "created": created,
    }


def resolve_hook_command(workspace: Path, command: list[str]) -> list[str]:
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise WorktreeFailure("worktree hook must be a non-empty string array")
    executable = Path(command[0])
    if executable.is_absolute():
        raise WorktreeFailure("worktree hook executable must be repository-relative")
    resolved = (workspace / executable).resolve(strict=True)
    if workspace != resolved and workspace not in resolved.parents:
        raise WorktreeFailure("worktree hook executable escapes the repository")
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise WorktreeFailure("worktree hook executable is not an executable file")
    return [str(resolved), *command[1:]]


def run_worktree_hook(
    workspace: Path,
    command: list[str],
    *,
    hook_root: Path | None = None,
    environment_overrides: dict[str, str] | None = None,
    stream: TextIO | None = None,
) -> None:
    workspace = workspace.expanduser().resolve(strict=True)
    trusted_root = (
        hook_root.expanduser().resolve(strict=True)
        if hook_root is not None
        else workspace
    )
    environment = os.environ.copy()
    environment["CODEX_WORKTREE_PATH"] = str(workspace)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if environment_overrides:
        environment.update(environment_overrides)
    run(
        resolve_hook_command(trusted_root, command),
        cwd=workspace,
        env=environment,
        stream=stream,
    )


def run_setup_hook(
    workspace: Path,
    command: list[str],
    stream: TextIO | None = None,
    *,
    hook_root: Path | None = None,
) -> None:
    run_worktree_hook(
        workspace,
        command,
        hook_root=hook_root,
        stream=stream,
    )


def run_setup_hook_with_rollback(
    *,
    source_path: Path,
    workspace: Path,
    branch_prefix: str,
    setup_command: list[str],
    cleanup_command: list[str],
    resuming: bool,
    stream: TextIO | None = None,
    management_token_file: Path | None = None,
) -> None:
    """Run repository setup and roll back a newly prepared worktree on failure."""
    try:
        run_setup_hook(workspace, setup_command, stream=stream)
    except WorktreeFailure as setup_error:
        try:
            run_cleanup_hook(
                workspace,
                cleanup_command,
                management_token_file=management_token_file,
                stream=stream,
            )
        except (OSError, WorktreeFailure) as cleanup_error:
            raise WorktreeFailure(
                f"{setup_error}; setup rollback failed: {cleanup_error}"
            ) from setup_error
        if not resuming and workspace.exists():
            remove_linked_worktree(
                source_path=source_path,
                workspace=workspace,
                branch_prefix=branch_prefix,
                stream=stream,
            )
        raise


def run_cleanup_hook(
    workspace: Path,
    command: list[str],
    stream: TextIO | None = None,
    *,
    hook_root: Path | None = None,
    management_token_file: Path | None = None,
) -> None:
    overrides = None
    if management_token_file is not None:
        overrides = {
            "CONVEX_MANAGEMENT_TOKEN": read_management_token(
                management_token_file
            )
        }
    run_worktree_hook(
        workspace,
        command,
        hook_root=hook_root,
        environment_overrides=overrides,
        stream=stream,
    )


def read_management_token(path: Path) -> str:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise WorktreeFailure("management token path is not a regular file")
    if stat.S_IMODE(resolved.stat().st_mode) & 0o077:
        raise WorktreeFailure(
            "management token file must not be accessible by group or other users"
        )
    token = resolved.read_text().strip()
    if not token or any(character.isspace() for character in token):
        raise WorktreeFailure("management token file must contain exactly one token")
    return token


def cleanup_linked_worktree(
    *,
    source_path: Path,
    workspace: Path,
    branch_prefix: str,
    cleanup_command: list[str],
    management_token_file: Path | None = None,
    stream: TextIO | None = None,
) -> None:
    source = source_path.expanduser().resolve(strict=True)
    workspace = workspace.expanduser().resolve(strict=True)
    if not is_linked_to_source(workspace, source):
        raise WorktreeFailure("refusing to clean a worktree not owned by the source repository")
    tracked_status = git(
        workspace, "status", "--porcelain", "--untracked-files=no"
    ).stdout.strip()
    if tracked_status:
        raise WorktreeFailure("refusing to remove a worktree with tracked changes")
    branch = git(workspace, "branch", "--show-current", check=False).stdout.strip()
    if branch and not branch.startswith(f"{branch_prefix}/"):
        raise WorktreeFailure("refusing to remove a worktree on an unexpected branch")

    run_cleanup_hook(
        workspace,
        cleanup_command,
        management_token_file=management_token_file,
        stream=stream,
    )
    remove_linked_worktree(
        source_path=source,
        workspace=workspace,
        branch_prefix=branch_prefix,
        stream=stream,
    )


def remove_linked_worktree(
    *,
    source_path: Path,
    workspace: Path,
    branch_prefix: str,
    stream: TextIO | None = None,
) -> None:
    """Remove a terminally clean automation worktree after its hook has run."""
    source = source_path.expanduser().resolve(strict=True)
    workspace = workspace.expanduser().resolve(strict=True)
    if not is_linked_to_source(workspace, source):
        raise WorktreeFailure("refusing to remove a worktree not owned by the source repository")
    tracked_status = git(
        workspace, "status", "--porcelain", "--untracked-files=no"
    ).stdout.strip()
    if tracked_status:
        raise WorktreeFailure("refusing to remove a worktree with tracked changes")
    branch = git(workspace, "branch", "--show-current", check=False).stdout.strip()
    if branch and not branch.startswith(f"{branch_prefix}/"):
        raise WorktreeFailure("refusing to remove a worktree on an unexpected branch")
    git(source, "worktree", "remove", "--force", str(workspace), stream=stream)
    git(source, "worktree", "prune", stream=stream)
    if branch:
        git(source, "branch", "-D", branch, stream=stream)
