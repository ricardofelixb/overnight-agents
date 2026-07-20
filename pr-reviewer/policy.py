#!/usr/bin/env python3
"""Pure policy functions for the autonomous PR reviewer."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any


SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def detect_domains(changed_files: list[str], diff_text: str) -> list[str]:
    lowered_paths = "\n".join(changed_files).lower()
    lowered_diff = diff_text.lower()
    domains: set[str] = set()
    if "convex/" in lowered_paths or re.search(r"\bconvex\b", lowered_diff):
        domains.add("convex")
    react_path = any(
        path.endswith((".tsx", ".jsx"))
        or path.startswith(("src/app/", "app/", "pages/", "src/components/", "src/hooks/", "src/contexts/"))
        for path in changed_files
    )
    if react_path or re.search(r"\b(next|react|vercel)\b", lowered_diff):
        domains.add("react")
    if "workos" in lowered_paths or re.search(r"\b(workos|authkit)\b", lowered_diff):
        domains.add("workos")
    return sorted(domains)


def validate_config(config: dict[str, Any], config_path: Path) -> list[str]:
    del config_path
    errors: list[str] = []
    if config.get("version") != 1:
        errors.append("config version must equal 1")
    projects = config.get("projects")
    if not isinstance(projects, list) or not projects:
        errors.append("config must contain projects")
        return errors
    names: set[str] = set()
    defaults = config.get("defaults", {})
    if not isinstance(defaults, dict):
        errors.append("defaults must be an object")
        defaults = {}

    def valid_commands(value: Any) -> bool:
        return isinstance(value, list) and all(
            isinstance(command, list)
            and command
            and all(isinstance(part, str) and part and "\0" not in part for part in command)
            for command in value
        )

    def valid_validation_environment(value: Any) -> bool:
        forbidden = {"HOME", "PATH", "SHELL", "USER", "LOGNAME", "TMPDIR", "CODEX_HOME"}
        sensitive = re.compile(r"(TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|PRIVATE|API_KEY)", re.IGNORECASE)
        return isinstance(value, dict) and all(
            isinstance(name, str)
            and re.fullmatch(r"[A-Z_][A-Z0-9_]*", name)
            and name not in forbidden
            and not sensitive.search(name)
            and isinstance(value, str)
            and "\0" not in value
            and "\n" not in value
            for name, value in value.items()
        )

    for project in projects:
        if not isinstance(project, dict):
            errors.append("project entries must be objects")
            continue
        name = project.get("name")
        if not isinstance(name, str) or not name:
            errors.append("every project requires a name")
            continue
        if name in names:
            errors.append(f"duplicate project name: {name}")
        names.add(name)
        merged = defaults | project
        if not Path(project.get("source_path", "")).is_absolute():
            errors.append(f"{name}: source_path must be absolute")
        repository = project.get("repository", "")
        if not isinstance(repository, str) or not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
            errors.append(f"{name}: repository must be owner/name")
        commands = merged.get("validation_commands", [])
        if not valid_commands(commands) or not commands:
            errors.append(f"{name}: validation_commands must be non-empty argv arrays")
        if not valid_commands(merged.get("setup_commands", [])):
            errors.append(f"{name}: setup_commands must be argv arrays")
        if not valid_validation_environment(merged.get("validation_environment", {})):
            errors.append(f"{name}: validation_environment contains an unsafe name or value")
        environment_file = merged.get("environment_file")
        if environment_file is not None and (not isinstance(environment_file, str) or not environment_file):
            errors.append(f"{name}: environment_file must be a non-empty path")
        if not project.get("allowed_head_patterns"):
            errors.append(f"{name}: allowed_head_patterns cannot be empty")
        if "*" in project.get("allowed_authors", []):
            errors.append(f"{name}: wildcard authors are forbidden")
        excluded_authors = project.get("excluded_authors", [])
        if not isinstance(excluded_authors, list) or not all(
            isinstance(author, str) and author for author in excluded_authors
        ):
            errors.append(f"{name}: excluded_authors must be login strings")
        if not isinstance(project.get("base_branch"), str) or not project["base_branch"]:
            errors.append(f"{name}: base_branch is required")
        mode = merged.get("mode", "repair")
        if mode not in {"observe", "repair"}:
            errors.append(f"{name}: mode must be observe or repair")
        if not isinstance(merged.get("telegram_notifications_enabled", False), bool):
            errors.append(f"{name}: telegram_notifications_enabled must be boolean")
        if not isinstance(merged.get("simplify_human_prs", True), bool):
            errors.append(f"{name}: simplify_human_prs must be boolean")
        skip_patterns = merged.get(
            "simplification_skip_head_patterns",
            ["code-simplify/*", "code-organize/*"],
        )
        if not isinstance(skip_patterns, list) or not skip_patterns or not all(
            isinstance(pattern, str) and pattern and "\0" not in pattern for pattern in skip_patterns
        ):
            errors.append(f"{name}: simplification_skip_head_patterns must be non-empty strings")
        for field in ("protected_policy_patterns", "protected_agent_edit_patterns"):
            patterns = merged.get(field, [])
            if not isinstance(patterns, list) or not all(
                isinstance(pattern, str) and pattern and "\0" not in pattern for pattern in patterns
            ):
                errors.append(f"{name}: {field} must contain non-empty strings")
        numeric_ranges = {
            "command_timeout_seconds": (60, 21600),
            "max_changed_files": (1, 1000),
            "max_diff_bytes": (1000, 10_000_000),
            "max_review_context_bytes": (10_000, 5_000_000),
            "max_ci_context_bytes": (10_000, 5_000_000),
            "max_document_bytes": (1000, 20_000_000),
            "docs_max_age_hours": (1, 168),
            "skill_max_age_days": (1, 31),
            "ai_files_max_age_days": (1, 31),
            "validation_attempts": (1, 3),
            "validation_correction_cycles": (1, 3),
            "result_contract_correction_cycles": (0, 2),
        }
        for field, (minimum, maximum) in numeric_ranges.items():
            defaults = {
                "result_contract_correction_cycles": 1,
            }
            value = merged.get(field, defaults.get(field))
            if not isinstance(value, int) or not minimum <= value <= maximum:
                errors.append(f"{name}: {field} must be between {minimum} and {maximum}")
    for field in (
        "skill_path",
        "simplifier_skill_path",
        "workspace_root",
        "state_root",
        "docs_catalog",
        "skills_lock",
        "telegram_env",
        "webhook_env",
    ):
        if not isinstance(config.get(field), str) or not config[field]:
            errors.append(f"missing {field}")
    return errors

def evaluate_pr_eligibility(pr: dict[str, Any], project: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if pr.get("state") != "OPEN":
        errors.append("pull request is not open")
    if pr.get("isDraft") is True:
        errors.append("pull request is a draft")
    if pr.get("baseRefName") != project.get("base_branch"):
        errors.append("base branch does not match policy")
    head = pr.get("headRefName", "")
    if not any(fnmatch.fnmatchcase(head, pattern) for pattern in project.get("allowed_head_patterns", [])):
        errors.append("head branch does not match an allowed pattern")
    author = (pr.get("author") or {}).get("login")
    if author in project.get("excluded_authors", []):
        errors.append("pull request author is excluded")
    allowed_authors = project.get("allowed_authors", [])
    if allowed_authors and author not in allowed_authors:
        errors.append("pull request author is not allowlisted")
    if pr.get("isCrossRepository") and not project.get("allow_forks", False):
        errors.append("cross-repository pull requests are not allowed")
    expected_owner = str(project.get("repository", "")).split("/", 1)[0].lower()
    head_owner = (pr.get("headRepositoryOwner") or {}).get("login", "").lower()
    if head_owner != expected_owner:
        errors.append("head repository owner does not match configured repository")
    if not SHA_RE.fullmatch(pr.get("baseRefOid", "")):
        errors.append("invalid base SHA")
    if not SHA_RE.fullmatch(pr.get("headRefOid", "")):
        errors.append("invalid head SHA")
    return errors
