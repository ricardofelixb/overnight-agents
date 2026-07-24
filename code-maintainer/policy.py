"""Configuration validation for the scheduled code maintainer."""

from __future__ import annotations

import re
from typing import Any


class ConfigurationFailure(ValueError):
    pass


def _command(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(part, str) and part and "\0" not in part for part in value)
    )


def _path(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and "\0" not in value


def _bounded_integer(
    value: Any, minimum: int, maximum: int, label: str
) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ConfigurationFailure(
            f"{label} must be an integer between {minimum} and {maximum}"
        )


def validate_config(config: dict[str, Any]) -> None:
    if config.get("version") != 2:
        raise ConfigurationFailure("config version must equal 2")
    if not isinstance(config.get("enabled"), bool):
        raise ConfigurationFailure("enabled must be a boolean")
    if not isinstance(config.get("schedule"), str) or not config["schedule"].strip():
        raise ConfigurationFailure("schedule must be a non-empty string")
    if config.get("provider", "codex") not in {"codex", "claude"}:
        raise ConfigurationFailure("provider must be codex or claude")
    _bounded_integer(config.get("max_changed_files", 80), 1, 500, "max_changed_files")
    _bounded_integer(
        config.get("max_diff_bytes", 750_000),
        1_000,
        5_000_000,
        "max_diff_bytes",
    )

    context = config.get("context")
    if not isinstance(context, dict):
        raise ConfigurationFailure("context must be an object")
    for field in (
        "skills_lock",
        "skill_release_root",
        "ai_files_root",
        "docs_catalog",
        "docs_refresh_script",
        "docs_cache",
    ):
        if not _path(context.get(field)):
            raise ConfigurationFailure(f"context requires {field}")
    for field, bounds, default in (
        ("skill_max_age_days", (1, 31), 8),
        ("ai_files_max_age_days", (1, 31), 8),
        ("docs_max_age_hours", (1, 168), 24),
        ("max_document_bytes", (1_000, 20_000_000), 5_000_000),
    ):
        _bounded_integer(context.get(field, default), bounds[0], bounds[1], field)

    projects = config.get("projects")
    if not isinstance(projects, list) or not projects:
        raise ConfigurationFailure("projects must be a non-empty array")
    names: set[str] = set()
    for project in projects:
        if not isinstance(project, dict):
            raise ConfigurationFailure("each project must be an object")
        name = project.get("name")
        if (
            not isinstance(name, str)
            or name in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9._-]+", name)
        ):
            raise ConfigurationFailure("project name is missing or unsafe")
        if name in names:
            raise ConfigurationFailure(f"duplicate project name: {name}")
        names.add(name)
        if not isinstance(project.get("enabled"), bool):
            raise ConfigurationFailure(f"project {name} enabled must be a boolean")
        for field in ("source_path", "repository", "base_branch"):
            if not _path(project.get(field)):
                raise ConfigurationFailure(f"project {name} requires {field}")
        commands = project.get("validation_commands")
        if not isinstance(commands, list) or not commands or any(
            not _command(command) for command in commands
        ):
            raise ConfigurationFailure(
                f"project {name} validation_commands must be non-empty argv arrays"
            )
        workspace = project.get("workspace")
        if workspace is None:
            if not _path(project.get("environment_file")):
                raise ConfigurationFailure(f"project {name} requires environment_file")
            continue
        if not isinstance(workspace, dict) or workspace.get("type") != "linked-worktree":
            raise ConfigurationFailure(
                f"project {name} workspace type must be linked-worktree"
            )
        for field in ("setup_command", "cleanup_command"):
            if not _command(workspace.get(field)):
                raise ConfigurationFailure(
                    f"project {name} workspace {field} must be a non-empty argv array"
                )
        token_file = workspace.get("management_token_file")
        if token_file is not None and not _path(token_file):
            raise ConfigurationFailure(
                f"project {name} workspace management_token_file must be a path"
            )
