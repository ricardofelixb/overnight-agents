"""Configuration validation for the scheduled code simplifier."""

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


def validate_config(config: dict[str, Any]) -> None:
    if config.get("version") != 1:
        raise ConfigurationFailure("config version must equal 1")
    if not isinstance(config.get("enabled"), bool):
        raise ConfigurationFailure("enabled must be a boolean")
    if not isinstance(config.get("schedule"), str) or not config["schedule"].strip():
        raise ConfigurationFailure("schedule must be a non-empty string")
    if config.get("provider", "codex") not in {"codex", "claude"}:
        raise ConfigurationFailure("provider must be codex or claude")
    projects = config.get("projects")
    if not isinstance(projects, list) or not projects:
        raise ConfigurationFailure("projects must be a non-empty array")
    names: set[str] = set()
    for project in projects:
        if not isinstance(project, dict):
            raise ConfigurationFailure("each project must be an object")
        name = project.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise ConfigurationFailure("project name is missing or unsafe")
        if name in names:
            raise ConfigurationFailure(f"duplicate project name: {name}")
        names.add(name)
        if not isinstance(project.get("enabled"), bool):
            raise ConfigurationFailure(f"project {name} enabled must be a boolean")
        for field in ("source_path", "repository", "base_branch", "checklist_file"):
            if not isinstance(project.get(field), str) or not project[field]:
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
            if not isinstance(project.get("environment_file"), str) or not project[
                "environment_file"
            ]:
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
        if token_file is not None and (
            not isinstance(token_file, str) or not token_file
        ):
            raise ConfigurationFailure(
                f"project {name} workspace management_token_file must be a path"
            )
