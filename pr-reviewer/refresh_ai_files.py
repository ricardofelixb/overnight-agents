#!/usr/bin/env python3
"""Refresh Convex-managed AI files in isolated clones and publish audited snapshots."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from policy import validate_config


MAX_SNAPSHOT_FILES = 1_000
MAX_SNAPSHOT_BYTES = 20_000_000
SAFE_ENV_NAMES = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LOGNAME",
    "PATH",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TERM",
    "TMPDIR",
    "USER",
}


class AiFilesRefreshFailure(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def command_environment() -> dict[str, str]:
    environment = {name: os.environ[name] for name in SAFE_ENV_NAMES if name in os.environ}
    environment["CI"] = "true"
    return environment


def run(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=command_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise AiFilesRefreshFailure(
            f"command failed ({result.returncode}): {command[0]}\n{result.stdout}"
        )
    return result.stdout


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def is_managed_ai_file(path: str) -> bool:
    normalized = Path(path).as_posix()
    if normalized in {"AGENTS.md", "CLAUDE.md"}:
        return True
    if normalized.startswith("convex/_generated/ai/"):
        return True
    parts = Path(normalized).parts
    return (
        len(parts) >= 3
        and parts[0] in {".agents", ".claude"}
        and parts[1] == "skills"
        and parts[2].startswith("convex")
    )


def github_repository_from_origin(origin: str) -> str:
    value = origin.strip()
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        parsed = urlparse(value)
        if parsed.hostname != "github.com" or parsed.scheme not in {"https", "ssh"}:
            raise AiFilesRefreshFailure("configured source origin is not a GitHub repository")
        path = parsed.path.lstrip("/")
    repository = path.removesuffix(".git")
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
        raise AiFilesRefreshFailure("configured source origin has an invalid repository path")
    return repository


def changed_paths(workspace: Path) -> list[str]:
    output = run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=workspace,
    )
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            raise AiFilesRefreshFailure(f"cannot parse git status line: {line!r}")
        value = line[3:]
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        paths.append(value)
    return paths


def managed_snapshot_files(workspace: Path) -> list[Path]:
    candidates: list[Path] = []
    for relative in (Path("AGENTS.md"), Path("CLAUDE.md"), Path("convex/_generated/ai")):
        path = workspace / relative
        if path.is_file():
            candidates.append(path)
        elif path.is_dir():
            candidates.extend(item for item in path.rglob("*") if item.is_file())
    for root_name in (".agents", ".claude"):
        skills = workspace / root_name / "skills"
        if not skills.is_dir():
            continue
        for child in skills.iterdir():
            if child.name.startswith("convex") and child.is_dir():
                candidates.extend(item for item in child.rglob("*") if item.is_file())
    return sorted(set(candidates), key=lambda item: item.relative_to(workspace).as_posix())


def audit_and_hash(workspace: Path, files: list[Path]) -> tuple[str, dict[str, dict[str, Any]]]:
    if not files:
        raise AiFilesRefreshFailure("Convex AI update produced no managed AI files")
    digest = hashlib.sha256()
    manifest: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    for index, path in enumerate(files, start=1):
        if index > MAX_SNAPSHOT_FILES:
            raise AiFilesRefreshFailure("Convex AI snapshot exceeds the file-count limit")
        if path.is_symlink():
            raise AiFilesRefreshFailure(f"Convex AI snapshot contains a symlink: {path}")
        relative = path.relative_to(workspace).as_posix()
        if not is_managed_ai_file(relative):
            raise AiFilesRefreshFailure(f"unexpected file in Convex AI snapshot: {relative}")
        content = path.read_bytes()
        total_bytes += len(content)
        if total_bytes > MAX_SNAPSHOT_BYTES:
            raise AiFilesRefreshFailure("Convex AI snapshot exceeds the byte limit")
        file_hash = hashlib.sha256(content).hexdigest()
        manifest[relative] = {"bytes": len(content), "sha256": file_hash}
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    if "convex/_generated/ai/guidelines.md" not in manifest:
        raise AiFilesRefreshFailure("Convex AI snapshot is missing generated guidelines")
    return digest.hexdigest(), manifest


def publish_snapshot(
    workspace: Path,
    state_root: Path,
    project: dict[str, Any],
    base_sha: str,
) -> Path:
    files = managed_snapshot_files(workspace)
    snapshot_hash, file_manifest = audit_and_hash(workspace, files)
    project_root = state_root / "ai-files" / project["name"]
    releases = project_root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    release = releases / snapshot_hash
    if not release.exists():
        staging = Path(tempfile.mkdtemp(prefix=f".{snapshot_hash}.staging-", dir=releases))
        try:
            for source in files:
                relative = source.relative_to(workspace)
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            staged_hash, staged_manifest = audit_and_hash(staging, managed_snapshot_files(staging))
            if staged_hash != snapshot_hash or staged_manifest != file_manifest:
                raise AiFilesRefreshFailure("staged Convex AI snapshot failed integrity verification")
            staging.rename(release)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    manifest = {
        "version": 1,
        "project": project["name"],
        "repository": project["repository"],
        "base_branch": project["base_branch"],
        "base_sha": base_sha,
        "refreshed_at": now_iso(),
        "command": ["npx", "convex", "ai-files", "update"],
        "snapshot_sha256": snapshot_hash,
        "release_path": str(release.resolve()),
        "files": file_manifest,
    }
    atomic_json(project_root / "manifest.json", manifest)
    return project_root / "manifest.json"


def refresh_project(project: dict[str, Any], state_root: Path) -> Path:
    source = Path(project["source_path"])
    if not (source / ".git").exists():
        raise AiFilesRefreshFailure(f"source checkout is not a Git repository: {source}")
    if not (source / "convex.json").is_file():
        raise AiFilesRefreshFailure(f"enabled project has no convex.json: {project['name']}")
    origin = run(["git", "remote", "get-url", "origin"], cwd=source).strip()
    if github_repository_from_origin(origin).lower() != project["repository"].lower():
        raise AiFilesRefreshFailure(
            f"configured source origin does not match repository for {project['name']}"
        )
    locks = state_root / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    with (locks / f"ai-files-{project['name']}.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AiFilesRefreshFailure(
                f"another Convex AI refresh is running for {project['name']}"
            ) from error
        staging_root = state_root / "ai-files-staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f"{project['name']}-", dir=staging_root
        ) as temporary:
            workspace = Path(temporary) / "repo"
            run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    project["base_branch"],
                    "--single-branch",
                    origin,
                    str(workspace),
                ]
            )
            base_sha = run(["git", "rev-parse", "HEAD"], cwd=workspace).strip()
            if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
                raise AiFilesRefreshFailure("invalid base SHA in isolated Convex AI refresh")
            for command in project.get("ai_files_setup_commands", project.get("setup_commands", [])):
                run(command, cwd=workspace)
            run(["npx", "convex", "ai-files", "update"], cwd=workspace)
            unexpected = [path for path in changed_paths(workspace) if not is_managed_ai_file(path)]
            if unexpected:
                raise AiFilesRefreshFailure(
                    "Convex AI update changed unexpected files: " + ", ".join(sorted(unexpected))
                )
            return publish_snapshot(workspace, state_root, project, base_sha)


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise AiFilesRefreshFailure(f"cannot load reviewer config: {error}") from error
    errors = validate_config(config, path)
    if errors:
        raise AiFilesRefreshFailure("invalid reviewer config: " + "; ".join(errors))
    return config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--project")
    args = parser.parse_args()
    try:
        config_path = args.config.resolve()
        config = load_config(config_path)
        state_value = Path(config["state_root"]).expanduser()
        state_root = state_value if state_value.is_absolute() else (config_path.parent / state_value).resolve()
        projects = [project for project in config["projects"] if project.get("enabled", False)]
        if args.project:
            projects = [project for project in projects if project["name"] == args.project]
            if not projects:
                raise AiFilesRefreshFailure(f"unknown or disabled project: {args.project}")
        for project in projects:
            manifest = refresh_project(project, state_root)
            print(f"refreshed Convex AI files for {project['name']}: {manifest}")
        return 0
    except (OSError, AiFilesRefreshFailure) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
