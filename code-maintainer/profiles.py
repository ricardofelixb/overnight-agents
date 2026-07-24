"""Validated project context and semantic maintenance slices."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROLE_NAMES = (
    "reuse-simplification",
    "maintainability-organization",
    "efficiency-performance",
    "correctness-reliability",
    "security-hardening",
)
ROLE_SET = frozenset(ROLE_NAMES)
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9-]*")


class ProfileFailure(ValueError):
    pass


@dataclass(frozen=True)
class MaintenanceSlice:
    identifier: str
    title: str
    selectors: tuple[str, ...]
    search_terms: tuple[str, ...]
    roles: tuple[str, ...]
    guidance_domains: tuple[str, ...]

    def prompt_payload(self) -> dict[str, Any]:
        return {
            "id": self.identifier,
            "title": self.title,
            "selectors": list(self.selectors),
            "search_terms": list(self.search_terms),
            "roles": list(self.roles),
            "guidance_domains": list(self.guidance_domains),
        }


@dataclass(frozen=True)
class ProjectProfile:
    name: str
    root: Path
    manifest_path: Path
    shared_context: tuple[Path, ...]
    role_context: dict[str, tuple[Path, ...]]
    slices_path: Path
    slices: tuple[MaintenanceSlice, ...]


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ProfileFailure(f"invalid {label}: {error}") from error
    if not isinstance(value, dict):
        raise ProfileFailure(f"{label} must be a JSON object")
    return value


def _resource(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ProfileFailure(f"{label} must be a non-empty relative path")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ProfileFailure(f"{label} escapes the project profile")
    path = (root / candidate).resolve()
    resolved_root = root.resolve()
    if resolved_root != path and resolved_root not in path.parents:
        raise ProfileFailure(f"{label} escapes the project profile")
    if not path.is_file():
        raise ProfileFailure(f"{label} is missing: {path}")
    return path


def _string_list(value: Any, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or any(not isinstance(item, str) or not item or "\0" in item for item in value)
    ):
        raise ProfileFailure(f"{label} must be a string array")
    if len(set(value)) != len(value):
        raise ProfileFailure(f"{label} must not contain duplicates")
    return tuple(value)


def _selector(value: str) -> bool:
    path = Path(value)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and not value.startswith("-")
        and not any(ord(character) < 32 for character in value)
    )


def load_slices(path: Path) -> tuple[MaintenanceSlice, ...]:
    document = _load_object(path, "maintenance slices")
    if document.get("version") != 1:
        raise ProfileFailure("maintenance slices version must equal 1")
    defaults = document.get("defaults")
    if not isinstance(defaults, dict):
        raise ProfileFailure("maintenance slices require defaults")
    default_roles = _string_list(defaults.get("roles"), "default roles")
    if set(default_roles) - ROLE_SET:
        raise ProfileFailure("maintenance slices contain an unknown default role")
    default_domains = _string_list(
        defaults.get("guidance_domains", []),
        "default guidance domains",
        allow_empty=True,
    )
    raw_slices = document.get("slices")
    if not isinstance(raw_slices, list) or not raw_slices:
        raise ProfileFailure("maintenance slices must be a non-empty array")

    result: list[MaintenanceSlice] = []
    identifiers: set[str] = set()
    for raw in raw_slices:
        if not isinstance(raw, dict):
            raise ProfileFailure("each maintenance slice must be an object")
        identifier = raw.get("id")
        title = raw.get("title")
        if not isinstance(identifier, str) or not SAFE_ID.fullmatch(identifier):
            raise ProfileFailure("maintenance slice id is missing or unsafe")
        if identifier in identifiers:
            raise ProfileFailure(f"duplicate maintenance slice id: {identifier}")
        identifiers.add(identifier)
        if not isinstance(title, str) or not title.strip():
            raise ProfileFailure(f"maintenance slice {identifier} requires a title")
        selectors = _string_list(
            raw.get("selectors"), f"maintenance slice {identifier} selectors"
        )
        if any(not _selector(selector) for selector in selectors):
            raise ProfileFailure(
                f"maintenance slice {identifier} contains an unsafe selector"
            )
        search_terms = _string_list(
            raw.get("search_terms", []),
            f"maintenance slice {identifier} search_terms",
            allow_empty=True,
        )
        roles = _string_list(
            raw.get("roles", list(default_roles)),
            f"maintenance slice {identifier} roles",
        )
        if set(roles) - ROLE_SET:
            raise ProfileFailure(
                f"maintenance slice {identifier} contains an unknown role"
            )
        domains = _string_list(
            raw.get("guidance_domains", list(default_domains)),
            f"maintenance slice {identifier} guidance_domains",
            allow_empty=True,
        )
        result.append(
            MaintenanceSlice(
                identifier=identifier,
                title=title.strip(),
                selectors=selectors,
                search_terms=search_terms,
                roles=roles,
                guidance_domains=domains,
            )
        )
    return tuple(result)


def load_project_profile(skill_root: Path, project_name: str) -> ProjectProfile:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", project_name) or project_name in {
        ".",
        "..",
    }:
        raise ProfileFailure("project name is missing or unsafe")
    root = skill_root / "references" / "projects" / project_name
    manifest_path = root / "profile.json"
    manifest = _load_object(manifest_path, f"{project_name} profile")
    if manifest.get("version") != 1 or manifest.get("project") != project_name:
        raise ProfileFailure(f"invalid project profile identity: {project_name}")

    shared_names = _string_list(
        manifest.get("shared_context"), f"{project_name} shared_context"
    )
    shared = tuple(
        _resource(root, value, f"{project_name} shared context")
        for value in shared_names
    )
    raw_roles = manifest.get("role_context")
    if not isinstance(raw_roles, dict) or set(raw_roles) != ROLE_SET:
        raise ProfileFailure(
            f"{project_name} role_context must define every specialist role"
        )
    role_context: dict[str, tuple[Path, ...]] = {}
    for role in ROLE_NAMES:
        names = _string_list(
            raw_roles[role],
            f"{project_name} role context for {role}",
            allow_empty=True,
        )
        role_context[role] = tuple(
            _resource(root, value, f"{project_name} role context for {role}")
            for value in names
        )
    slices_path = _resource(root, manifest.get("slices"), f"{project_name} slices")
    return ProjectProfile(
        name=project_name,
        root=root,
        manifest_path=manifest_path,
        shared_context=shared,
        role_context=role_context,
        slices_path=slices_path,
        slices=load_slices(slices_path),
    )
