"""Configuration validation for the scheduled code maintainer."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation.launchd import calendar_intervals
from profiles import ROLE_SET
from sizing import DEFAULT_GROWTH_BUDGET


class ConfigurationFailure(ValueError):
    pass


CONTEXT_PATH_FIELDS = (
    "skills_lock",
    "skill_release_root",
    "ai_files_root",
    "docs_catalog",
    "docs_refresh_script",
    "docs_cache",
)
CONTEXT_OPTIONAL_PATH_FIELDS = ("provider_refresh_script",)
CONTEXT_INTEGER_FIELDS = (
    ("skill_max_age_days", (1, 31), 8),
    ("ai_files_max_age_days", (1, 31), 8),
    ("docs_max_age_hours", (1, 168), 24),
    ("max_document_bytes", (1_000, 20_000_000), 5_000_000),
)
CONTEXT_KEYS = frozenset(
    CONTEXT_PATH_FIELDS
    + CONTEXT_OPTIONAL_PATH_FIELDS
    + tuple(field for field, _bounds, _default in CONTEXT_INTEGER_FIELDS)
)


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


def _schedule(value: Any, label: str) -> list[dict[str, int]]:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationFailure(f"{label} must be a non-empty daily schedule")
    try:
        return calendar_intervals(value)
    except ValueError as error:
        raise ConfigurationFailure(f"{label} is invalid: {error}") from error


def validate_config(config: dict[str, Any]) -> None:
    if config.get("version") != 3:
        raise ConfigurationFailure("config version must equal 3")
    if not isinstance(config.get("enabled"), bool):
        raise ConfigurationFailure("enabled must be a boolean")
    if "schedule" in config:
        raise ConfigurationFailure("schedule belongs on each project, not the root config")
    if config.get("provider", "codex") not in {"codex", "claude"}:
        raise ConfigurationFailure("provider must be codex or claude")
    agents = config.get("agents", {})
    if not isinstance(agents, dict):
        raise ConfigurationFailure("agents must be an object")
    unknown_agents = set(agents) - ROLE_SET
    if unknown_agents:
        raise ConfigurationFailure(
            "agents contains unknown roles: " + ", ".join(sorted(unknown_agents))
        )
    for role, enabled in agents.items():
        if not isinstance(enabled, bool):
            raise ConfigurationFailure(f"agents {role} must be a boolean")
    if not any(agents.get(role, True) for role in ROLE_SET):
        raise ConfigurationFailure("agents must enable at least one specialist role")
    _bounded_integer(config.get("max_changed_files", 80), 1, 500, "max_changed_files")
    _bounded_integer(
        config.get("max_diff_bytes", 750_000),
        1_000,
        5_000_000,
        "max_diff_bytes",
    )
    _bounded_integer(
        config.get("max_source_growth_lines", DEFAULT_GROWTH_BUDGET),
        0,
        1_000,
        "max_source_growth_lines",
    )
    _bounded_integer(
        config.get("minimum_free_bytes", 1024**3),
        1024**3,
        100 * 1024**3,
        "minimum_free_bytes",
    )
    _bounded_integer(
        config.get("ci_repair_max_attempts", 2),
        0,
        5,
        "ci_repair_max_attempts",
    )
    _bounded_integer(
        config.get("max_ci_log_bytes", 5_000_000),
        10_000,
        20_000_000,
        "max_ci_log_bytes",
    )
    _validate_slice_repair(config.get("slice_repair"))

    _validate_context(config.get("context"), "context", complete=True)

    projects = config.get("projects")
    if not isinstance(projects, list) or not projects:
        raise ConfigurationFailure("projects must be a non-empty array")
    names: set[str] = set()
    occupied: dict[tuple[int, int], str] = {}
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
        intervals = _schedule(project.get("schedule"), f"project {name} schedule")
        if project["enabled"]:
            for interval in intervals:
                key = (interval["Hour"], interval["Minute"])
                owner = occupied.get(key)
                if owner:
                    raise ConfigurationFailure(
                        f"project {name} schedule overlaps {owner}"
                    )
                occupied[key] = name
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
        overlay = project.get("context")
        if overlay is False:
            pass
        elif overlay is not None:
            _validate_context(overlay, f"project {name} context", complete=False)
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


SLICE_REPAIR_KEYS = frozenset(
    {
        "enabled",
        "provider",
        "codex_model",
        "codex_reasoning_effort",
        "timeout_seconds",
    }
)
SLICE_REPAIR_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)


def _validate_slice_repair(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ConfigurationFailure("slice_repair must be an object")
    unknown = set(value) - SLICE_REPAIR_KEYS
    if unknown:
        raise ConfigurationFailure(
            "slice_repair contains unknown fields: " + ", ".join(sorted(unknown))
        )
    if "enabled" in value and not isinstance(value["enabled"], bool):
        raise ConfigurationFailure("slice_repair enabled must be a boolean")
    if "provider" in value and value["provider"] != "codex":
        raise ConfigurationFailure("slice_repair provider must be codex")
    if "codex_model" in value and not _path(value["codex_model"]):
        raise ConfigurationFailure("slice_repair requires codex_model")
    effort = value.get("codex_reasoning_effort")
    if effort is not None and effort not in SLICE_REPAIR_EFFORTS:
        raise ConfigurationFailure("slice_repair codex_reasoning_effort is invalid")
    if "timeout_seconds" in value:
        _bounded_integer(
            value["timeout_seconds"], 60, 7200, "slice_repair timeout_seconds"
        )


def resolve_slice_repair(config: dict[str, Any]) -> dict[str, Any] | None:
    overlay = config.get("slice_repair") or {}
    if not isinstance(overlay, dict) or overlay.get("enabled", True) is False:
        return None
    return {
        "enabled": True,
        "provider": "codex",
        "codex_model": overlay.get("codex_model", "gpt-5.6-luna"),
        "codex_reasoning_effort": overlay.get("codex_reasoning_effort", "medium"),
        "timeout_seconds": overlay.get("timeout_seconds", 1200),
    }


def _validate_context(context: Any, label: str, *, complete: bool) -> None:
    if not isinstance(context, dict):
        raise ConfigurationFailure(f"{label} must be an object")
    unknown = set(context) - CONTEXT_KEYS
    if unknown:
        raise ConfigurationFailure(
            f"{label} contains unknown fields: " + ", ".join(sorted(unknown))
        )
    for field in CONTEXT_PATH_FIELDS:
        if complete or field in context:
            if not _path(context.get(field)):
                raise ConfigurationFailure(f"{label} requires {field}")
    for field in CONTEXT_OPTIONAL_PATH_FIELDS:
        if field in context and not _path(context.get(field)):
            raise ConfigurationFailure(f"{label} requires {field}")
    for field, bounds, default in CONTEXT_INTEGER_FIELDS:
        if complete:
            _bounded_integer(context.get(field, default), bounds[0], bounds[1], field)
        elif field in context:
            _bounded_integer(context[field], bounds[0], bounds[1], f"{label} {field}")


def resolve_context(
    config: dict[str, Any], project: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Return shared context defaults, a per-project overlay, or None to opt out."""

    overlay = project.get("context") if isinstance(project, dict) else None
    if overlay is False:
        return None
    context = dict(config.get("context") or {})
    if isinstance(overlay, dict):
        context.update(overlay)
    return context
