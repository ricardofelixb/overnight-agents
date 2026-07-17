#!/usr/bin/env python3
"""Prepare a controller-owned clone for one simplification run."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class WorkspaceFailure(RuntimeError):
    pass


def run(command: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if check and result.returncode != 0:
        raise WorkspaceFailure(f"command failed ({result.returncode}): {command[0]}\n{result.stdout}")
    return result


def git(cwd: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *arguments], cwd=cwd, check=check)


def safe_workspace(workspace_root: Path, project_name: str, source_path: Path) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", project_name):
        raise WorkspaceFailure("project name must contain only letters, digits, dots, underscores, or hyphens")
    root = workspace_root.expanduser().resolve()
    workspace = (root / project_name).resolve()
    source = source_path.expanduser().resolve()
    if root == Path("/") or workspace == root or root not in workspace.parents:
        raise WorkspaceFailure("unsafe simplifier workspace path")
    if workspace == source or workspace in source.parents or source in workspace.parents:
        raise WorkspaceFailure("simplifier workspace overlaps the source checkout")
    return workspace


def require_private_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise WorkspaceFailure(f"{label} is not a regular file")
    permissions = stat.S_IMODE(resolved.stat().st_mode)
    if permissions & 0o077:
        raise WorkspaceFailure(f"{label} must not be accessible by group or other users")
    return resolved


def require_ignored(cwd: Path, relative_path: str) -> None:
    if git(cwd, "check-ignore", "-q", relative_path, check=False).returncode != 0:
        raise WorkspaceFailure(f"{relative_path} must be ignored before automation can provision it")


def provision_symlink(cwd: Path, relative_path: str, source: Path) -> None:
    destination = cwd / relative_path
    if destination.is_symlink():
        if destination.resolve(strict=False) == source:
            return
        raise WorkspaceFailure(f"{relative_path} points to an unexpected file")
    if destination.exists():
        raise WorkspaceFailure(f"{relative_path} exists but is not a controller-managed symlink")
    destination.symlink_to(source)


def provision_runtime_files(workspace: Path, environment_file: Path, checklist_file: Path) -> None:
    environment = require_private_file(environment_file, "project environment file")
    require_ignored(workspace, ".env.local")
    provision_symlink(workspace, ".env.local", environment)

    tracked_checklist = git(
        workspace,
        "ls-files",
        "--error-unmatch",
        "simplification.md",
        check=False,
    ).returncode == 0
    if tracked_checklist:
        return
    checklist = checklist_file.expanduser().resolve(strict=True)
    if not checklist.is_file():
        raise WorkspaceFailure("simplification checklist is not a regular file")
    require_ignored(workspace, "simplification.md")
    provision_symlink(workspace, "simplification.md", checklist)


def quarantine(workspace: Path, reason: str) -> Path:
    root = workspace.parent / ".quarantine"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = root / f"{workspace.name}-{stamp}-{os.getpid()}-{reason}"
    workspace.rename(destination)
    return destination


def checkout_base(workspace: Path, base_branch: str) -> None:
    git(workspace, "fetch", "--prune", "origin", base_branch)
    git(workspace, "checkout", "-B", base_branch, f"origin/{base_branch}")
    git(workspace, "reset", "--hard", f"origin/{base_branch}")
    if git(workspace, "status", "--porcelain").stdout.strip():
        raise WorkspaceFailure("prepared simplifier clone is unexpectedly dirty")


def prepare_workspace(
    *,
    source_path: Path,
    workspace_root: Path,
    project_name: str,
    base_branch: str,
    branch_prefix: str,
    environment_file: Path,
    checklist_file: Path,
) -> dict[str, str | bool]:
    source = source_path.expanduser().resolve(strict=True)
    workspace = safe_workspace(workspace_root, project_name, source)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    origin = git(source, "remote", "get-url", "origin").stdout.strip()
    if not origin:
        raise WorkspaceFailure("source checkout has no origin remote")

    if workspace.exists():
        reason: str | None = None
        if not (workspace / ".git").is_dir():
            reason = "not-a-dedicated-clone"
        else:
            actual_origin = git(workspace, "remote", "get-url", "origin", check=False).stdout.strip()
            if actual_origin != origin:
                reason = "origin-mismatch"
        if reason:
            quarantine(workspace, reason)

    if workspace.exists():
        status = git(workspace, "status", "--porcelain").stdout.strip()
        current_branch = git(workspace, "branch", "--show-current", check=False).stdout.strip()
        if status and current_branch.startswith(f"{branch_prefix}/"):
            provision_runtime_files(workspace, environment_file, checklist_file)
            return {"workspace": str(workspace), "resuming": True, "branch": current_branch}
        if status:
            quarantine(workspace, "dirty-unexpected-branch")

    if not workspace.exists():
        temporary = Path(tempfile.mkdtemp(prefix=f".{workspace.name}.provision-", dir=workspace.parent))
        try:
            shutil.rmtree(temporary)
            run(["git", "clone", "--no-checkout", origin, str(temporary)])
            checkout_base(temporary, base_branch)
            if workspace.exists():
                raise WorkspaceFailure("simplifier workspace appeared concurrently during provisioning")
            temporary.rename(workspace)
        except Exception:
            if temporary.exists():
                quarantine(temporary, "provision-failed")
            raise
    else:
        checkout_base(workspace, base_branch)

    provision_runtime_files(workspace, environment_file, checklist_file)
    return {"workspace": str(workspace), "resuming": False, "branch": ""}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--base-branch", required=True)
    parser.add_argument("--branch-prefix", required=True)
    parser.add_argument("--environment-file", type=Path, required=True)
    parser.add_argument("--checklist-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare_workspace(
            source_path=args.source,
            workspace_root=args.workspace_root,
            project_name=args.project,
            base_branch=args.base_branch,
            branch_prefix=args.branch_prefix,
            environment_file=args.environment_file,
            checklist_file=args.checklist_file,
        )
    except (OSError, WorkspaceFailure) as error:
        parser.exit(1, f"workspace preparation failed: {error}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
