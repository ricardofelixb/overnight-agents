"""Validate and capture fresh, audited skills and official documentation."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

from automation import runtime


class ContextFailure(RuntimeError):
    pass


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ContextFailure(f"invalid {label}: {error}") from error
    if not isinstance(value, dict):
        raise ContextFailure(f"{label} must be a JSON object")
    return value


def _time(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ContextFailure(f"{label} timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContextFailure(f"{label} timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise ContextFailure(f"{label} timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _fresh(value: Any, maximum: timedelta, label: str) -> None:
    age = datetime.now(timezone.utc) - _time(value, label)
    if age < timedelta(0) or age > maximum:
        raise ContextFailure(f"{label} is stale")


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _path(config: dict[str, Any], value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ContextFailure(f"maintenance context requires {label}")
    path = Path(value)
    if not path.is_absolute():
        path = Path(config["_config_dir"]) / path
    return path.resolve()


def validate_skill_lock(
    config: dict[str, Any], domains: tuple[str, ...]
) -> dict[str, list[dict[str, Any]]]:
    context = config["context"]
    lock = _load_object(
        _path(config, context.get("skills_lock"), "skills_lock"), "skills lock"
    )
    if lock.get("version") != 1:
        raise ContextFailure("unsupported skills lock version")
    release_root = _path(
        config, context.get("skill_release_root"), "skill_release_root"
    )
    maximum = timedelta(days=int(context.get("skill_max_age_days", 8)))
    result: dict[str, list[dict[str, Any]]] = {}
    for domain in domains:
        entries = (lock.get("domains") or {}).get(domain)
        if not isinstance(entries, list) or not entries:
            raise ContextFailure(f"no audited skill release exists for {domain}")
        checked: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ContextFailure(f"invalid audited skill entry for {domain}")
            path = Path(str(entry.get("path", ""))).resolve()
            if path == release_root or release_root not in path.parents:
                raise ContextFailure(f"audited skill path escapes release root: {domain}")
            _fresh(entry.get("updated_at"), maximum, f"audited skill {entry.get('name')}")
            if not (path / "SKILL.md").is_file():
                raise ContextFailure(f"audited skill is unavailable: {entry.get('name')}")
            if _tree_hash(path) != entry.get("sha256"):
                raise ContextFailure(
                    f"audited skill hash mismatch: {entry.get('name')}"
                )
            checked.append(
                {
                    key: entry.get(key)
                    for key in (
                        "name",
                        "path",
                        "source",
                        "revision",
                        "updated_at",
                        "sha256",
                    )
                }
            )
        result[domain] = checked
    return result


def validate_ai_files(
    config: dict[str, Any], project_name: str, workspace: Path
) -> dict[str, Any]:
    context = config["context"]
    manifest_path = (
        _path(config, context.get("ai_files_root"), "ai_files_root")
        / project_name
        / "manifest.json"
    )
    manifest = _load_object(manifest_path, "Convex AI-files manifest")
    if manifest.get("version") != 1 or manifest.get("project") != project_name:
        raise ContextFailure("invalid Convex AI-files project identity")
    _fresh(
        manifest.get("refreshed_at"),
        timedelta(days=int(context.get("ai_files_max_age_days", 8))),
        "Convex AI-files snapshot",
    )
    metadata = (manifest.get("files") or {}).get(
        "convex/_generated/ai/guidelines.md"
    )
    workspace_guidelines = workspace / "convex/_generated/ai/guidelines.md"
    if not isinstance(metadata, dict) or not workspace_guidelines.is_file():
        raise ContextFailure("Convex guidance is unavailable")
    content = workspace_guidelines.read_bytes()
    if (
        len(content) != metadata.get("bytes")
        or hashlib.sha256(content).hexdigest() != metadata.get("sha256")
    ):
        raise ContextFailure(
            "workspace Convex guidance differs from the latest audited AI-files snapshot"
        )
    return {
        key: manifest.get(key)
        for key in (
            "project",
            "refreshed_at",
            "release_path",
            "base_sha",
        )
    }


def refresh_official_docs(
    config: dict[str, Any],
    domains: tuple[str, ...],
    project_name: str,
    stream: TextIO,
) -> Path:
    context = config["context"]
    manifest = (
        Path(config["_config_dir"])
        / "state"
        / "context"
        / project_name
        / "official-docs.json"
    ).resolve()
    command = [
        sys.executable,
        str(_path(config, context.get("docs_refresh_script"), "docs_refresh_script")),
        "--catalog",
        str(_path(config, context.get("docs_catalog"), "docs_catalog")),
        "--cache-dir",
        str(_path(config, context.get("docs_cache"), "docs_cache")),
        "--manifest",
        str(manifest),
        "--max-age-hours",
        str(context.get("docs_max_age_hours", 24)),
        "--max-document-bytes",
        str(context.get("max_document_bytes", 5_000_000)),
    ]
    for domain in domains:
        command.extend(["--domain", domain])
    runtime.run(command, stream=stream)
    validate_official_docs_manifest(config, manifest, domains)
    return manifest


def validate_official_docs_manifest(
    config: dict[str, Any],
    manifest: Path,
    domains: tuple[str, ...],
) -> dict[str, Any]:
    document = _load_object(manifest, "official documentation manifest")
    if document.get("version") != 1 or document.get("errors"):
        raise ContextFailure("official documentation refresh did not produce evidence")
    if set(document.get("domains", [])) != set(domains):
        raise ContextFailure("official documentation domains do not match the slice")
    documents = document.get("documents")
    if not isinstance(documents, list):
        raise ContextFailure("official documentation manifest has no documents")
    cache_root = _path(
        config, config["context"].get("docs_cache"), "docs_cache"
    )
    maximum = timedelta(
        hours=int(config["context"].get("docs_max_age_hours", 24))
    )
    seen: set[str] = set()
    for entry in documents:
        if not isinstance(entry, dict):
            raise ContextFailure("official documentation entry is invalid")
        domain = entry.get("domain")
        path = Path(str(entry.get("content_path", ""))).resolve()
        if (
            domain not in domains
            or path == cache_root
            or cache_root not in path.parents
        ):
            raise ContextFailure(
                "official documentation contains an untrusted domain or path"
            )
        if not path.is_file():
            raise ContextFailure("official documentation content is unavailable")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256"):
            raise ContextFailure("official documentation content hash mismatch")
        _fresh(entry.get("retrieved_at"), maximum, "official documentation")
        seen.add(domain)
    if seen != set(domains):
        raise ContextFailure(
            "official documentation is missing a requested domain"
        )
    return document


def prepare_context_evidence(
    config: dict[str, Any],
    project_name: str,
    domains: tuple[str, ...],
    workspace: Path,
    stream: TextIO,
) -> Path:
    evidence: dict[str, Any] = {
        "version": 1,
        "project": project_name,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "domains": list(domains),
        "skills": validate_skill_lock(config, domains),
    }
    if "convex" in domains:
        evidence["convex_ai_files"] = validate_ai_files(
            config, project_name, workspace
        )
    docs_manifest = refresh_official_docs(
        config, domains, project_name, stream
    )
    evidence["official_docs_manifest"] = str(docs_manifest)
    path = (
        Path(config["_config_dir"])
        / "state"
        / "context"
        / project_name
        / "evidence.json"
    ).resolve()
    from cycles import atomic_json

    atomic_json(path, evidence)
    return path
