#!/usr/bin/env python3
"""Stage, audit, atomically promote, and roll back official global skills."""

from __future__ import annotations

import argparse
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


ALLOWED_GITHUB_OWNERS = {"get-convex", "vercel-labs", "workos"}
FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL)
MAX_SKILL_FILES = 500
MAX_SKILL_BYTES = 5_000_000


class RefreshFailure(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(command: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0:
        raise RefreshFailure(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}")
    return result.stdout


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_repository_url(url: str) -> None:
    parsed = urlparse(url)
    pieces = parsed.path.strip("/").removesuffix(".git").split("/")
    if parsed.scheme != "https" or parsed.hostname != "github.com" or len(pieces) != 2 or pieces[0] not in ALLOWED_GITHUB_OWNERS:
        raise RefreshFailure(f"provider repository is not allowlisted: {url}")


def sync_source(provider: dict[str, Any], sources_root: Path) -> tuple[Path, str]:
    validate_repository_url(provider["repository"])
    source = sources_root / provider["name"]
    if not source.exists():
        run(
            [
                "git",
                "clone",
                "--filter=blob:none",
                "--no-tags",
                "--branch",
                provider["ref"],
                provider["repository"],
                str(source),
            ]
        )
    else:
        actual = run(["git", "remote", "get-url", "origin"], source).strip()
        if actual != provider["repository"]:
            raise RefreshFailure(f"source remote mismatch for {provider['name']}")
        if run(["git", "status", "--porcelain"], source).strip():
            raise RefreshFailure(f"source checkout is dirty for {provider['name']}")
        run(["git", "fetch", "--no-tags", "origin", provider["ref"]], source)
        run(["git", "checkout", "--detach", "FETCH_HEAD"], source)
    revision = run(["git", "rev-parse", "HEAD"], source).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RefreshFailure(f"invalid revision for {provider['name']}")
    return source, revision


def parse_skill(skill_file: Path) -> tuple[str, str]:
    text = skill_file.read_text()
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise RefreshFailure(f"missing YAML frontmatter: {skill_file}")
    name_match = re.search(r"^name:\s*[\"']?([^\n\"']+)", match.group("body"), re.MULTILINE)
    description_match = re.search(r"^description:\s*(.+)", match.group("body"), re.MULTILINE)
    if not name_match or not description_match:
        raise RefreshFailure(f"missing name/description: {skill_file}")
    name = name_match.group(1).strip()
    if not re.fullmatch(r"[a-z0-9-]{1,63}", name):
        raise RefreshFailure(f"invalid skill name {name!r}: {skill_file}")
    return name, description_match.group(1).strip()


def discover_skills(source: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for skill_file in source.rglob("SKILL.md"):
        if ".git" in skill_file.parts or "node_modules" in skill_file.parts:
            continue
        name, _ = parse_skill(skill_file)
        candidate = skill_file.parent
        previous = found.get(name)
        if previous is None or len(candidate.parts) < len(previous.parts):
            found[name] = candidate
    return found


def audit_tree(skill: Path) -> None:
    parse_skill(skill / "SKILL.md")
    root = skill.resolve()
    file_count = 0
    total_bytes = 0
    for path in skill.rglob("*"):
        if path.is_symlink():
            resolved = path.resolve()
            if root != resolved and root not in resolved.parents:
                raise RefreshFailure(f"skill symlink escapes its root: {path}")
        if path.is_file():
            file_count += 1
            total_bytes += path.stat().st_size
            if file_count > MAX_SKILL_FILES or total_bytes > MAX_SKILL_BYTES:
                raise RefreshFailure(f"skill exceeds audit size limits: {skill}")


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def select_skills(provider: dict[str, Any], discovered: dict[str, Path]) -> dict[str, Path]:
    requested = provider["skills"]
    if requested == ["*"]:
        return discovered
    missing = [name for name in requested if name not in discovered]
    if missing:
        raise RefreshFailure(f"{provider['name']}: missing requested skills: {missing}")
    return {name: discovered[name] for name in requested}


def stage_provider(
    provider: dict[str, Any],
    source: Path,
    revision: str,
    releases_root: Path,
) -> list[dict[str, Any]]:
    release = releases_root / provider["name"] / revision
    release.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for name, skill in sorted(select_skills(provider, discover_skills(source)).items()):
        audit_tree(skill)
        destination = release / name
        if not destination.exists():
            shutil.copytree(skill, destination, symlinks=False)
        audit_tree(destination)
        entries.append(
            {
                "name": name,
                "path": str(destination.resolve()),
                "source": provider["repository"].removesuffix(".git"),
                "revision": revision,
                "updated_at": now_iso(),
                "sha256": tree_hash(destination),
            }
        )
    return entries


def promote(entries_by_domain: dict[str, list[dict[str, Any]]], global_root: Path, old_lock: dict[str, Any] | None) -> None:
    global_root.mkdir(parents=True, exist_ok=True)
    old_entries = {
        item["name"]: item
        for values in (old_lock or {}).get("domains", {}).values()
        for item in values
    }
    all_entries = [entry for entries in entries_by_domain.values() for entry in entries]
    for entry in all_entries:
        destination = global_root / entry["name"]
        if destination.exists() and not destination.is_symlink():
            raise RefreshFailure(f"refusing to replace non-symlink global skill: {destination}")
        if not Path(entry["path"]).is_dir():
            raise RefreshFailure(f"staged skill path disappeared: {entry['path']}")
    for entry in all_entries:
        destination = global_root / entry["name"]
        previous = old_entries.get(entry["name"])
        entry["previous"] = (
            {key: value for key, value in previous.items() if key not in {"previous", "previous_path"}}
            if previous
            else None
        )
        temporary = global_root / f".{entry['name']}.next"
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        temporary.symlink_to(entry["path"], target_is_directory=True)
        os.replace(temporary, destination)


def validate_entries(entries_by_domain: dict[str, list[dict[str, Any]]]) -> None:
    owners: dict[str, str] = {}
    for domain, entries in entries_by_domain.items():
        if not entries:
            raise RefreshFailure(f"no skills staged for domain {domain}")
        for entry in entries:
            name = entry["name"]
            if name in owners:
                raise RefreshFailure(f"duplicate global skill name {name!r} in {owners[name]} and {domain}")
            owners[name] = domain


def rollback(lock: dict[str, Any], global_root: Path) -> dict[str, Any]:
    rolled_back: dict[str, list[dict[str, Any]]] = {}
    for domain, entries in lock.get("domains", {}).items():
        for entry in entries:
            previous = entry.get("previous")
            if not isinstance(previous, dict) or not Path(previous.get("path", "")).is_dir():
                continue
            destination = global_root / entry["name"]
            if destination.exists() and not destination.is_symlink():
                raise RefreshFailure(f"refusing to replace non-symlink global skill: {destination}")
            temporary = global_root / f".{entry['name']}.rollback"
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink()
            temporary.symlink_to(previous["path"], target_is_directory=True)
            os.replace(temporary, destination)
            rolled_back.setdefault(domain, []).append(previous)
    if not rolled_back:
        raise RefreshFailure("lock contains no complete previous release to restore")
    return {"version": 1, "generated_at": now_iso(), "domains": rolled_back}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--global-root", type=Path, default=Path.home() / ".codex" / "skills")
    parser.add_argument("--lock", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--stage", action="store_true")
    action.add_argument("--promote", action="store_true")
    action.add_argument("--rollback", action="store_true")
    args = parser.parse_args()

    try:
        if args.rollback:
            lock = json.loads(args.lock.read_text())
            rolled_back = rollback(lock, args.global_root.expanduser())
            atomic_json(args.lock, rolled_back)
            print("rolled back")
            return 0

        manifest = json.loads(args.manifest.read_text())
        if manifest.get("version") != 1:
            raise RefreshFailure("unsupported provider manifest version")
        sources_root = args.state_root / "skill-sources"
        releases_root = args.state_root / "skill-releases"
        sources_root.mkdir(parents=True, exist_ok=True)
        entries_by_domain: dict[str, list[dict[str, Any]]] = {}
        for provider in manifest["providers"]:
            source, revision = sync_source(provider, sources_root)
            entries_by_domain.setdefault(provider["domain"], []).extend(
                stage_provider(provider, source, revision, releases_root)
            )

        validate_entries(entries_by_domain)

        old_lock = json.loads(args.lock.read_text()) if args.lock.exists() else None
        new_lock = {"version": 1, "generated_at": now_iso(), "domains": entries_by_domain}
        if args.promote:
            promote(entries_by_domain, args.global_root.expanduser(), old_lock)
            atomic_json(args.lock, new_lock)
            print(f"promoted {sum(map(len, entries_by_domain.values()))} skills")
        else:
            staged_lock = args.lock.with_suffix(args.lock.suffix + ".staged")
            atomic_json(staged_lock, new_lock)
            print(staged_lock)
        return 0
    except (OSError, json.JSONDecodeError, RefreshFailure) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
