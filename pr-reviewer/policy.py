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
        if not project.get("allowed_head_patterns"):
            errors.append(f"{name}: allowed_head_patterns cannot be empty")
        elif any(pattern in {"*", "**"} for pattern in project["allowed_head_patterns"]):
            errors.append(f"{name}: catch-all head patterns are forbidden")
        if not project.get("allowed_authors"):
            errors.append(f"{name}: allowed_authors cannot be empty")
        elif "*" in project["allowed_authors"]:
            errors.append(f"{name}: wildcard authors are forbidden")
        if not isinstance(project.get("base_branch"), str) or not project["base_branch"]:
            errors.append(f"{name}: base_branch is required")
        mode = merged.get("mode", "observe")
        if mode not in {"observe", "repair", "merge"}:
            errors.append(f"{name}: mode must be observe, repair, or merge")
        if not isinstance(merged.get("approve_before_merge", False), bool):
            errors.append(f"{name}: approve_before_merge must be boolean")
        if not isinstance(merged.get("telegram_notifications_enabled", False), bool):
            errors.append(f"{name}: telegram_notifications_enabled must be boolean")
        severities = merged.get("auto_fix_severities", ["P2", "P3"])
        if not isinstance(severities, list) or not severities or not set(severities) <= {"P0", "P1", "P2", "P3"}:
            errors.append(f"{name}: auto_fix_severities must contain valid severities")
        passes = merged.get("review_passes")
        if (
            not isinstance(passes, list)
            or len(passes) < 2
            or any(not isinstance(item, dict) or not item.get("name") or not item.get("lens") for item in passes)
            or len({item.get("name") for item in passes if isinstance(item, dict)}) != len(passes)
        ):
            errors.append(f"{name}: at least two uniquely named review passes are required")
        numeric_ranges = {
            "max_repair_iterations": (0, 5),
            "command_timeout_seconds": (60, 21600),
            "max_changed_files": (1, 1000),
            "max_diff_bytes": (1000, 10_000_000),
            "max_document_bytes": (1000, 20_000_000),
            "docs_max_age_hours": (1, 168),
            "skill_max_age_days": (1, 31),
            "check_timeout_seconds": (1, 7200),
            "check_poll_seconds": (1, 300),
        }
        for field, (minimum, maximum) in numeric_ranges.items():
            value = merged.get(field)
            if not isinstance(value, int) or not minimum <= value <= maximum:
                errors.append(f"{name}: {field} must be between {minimum} and {maximum}")
    for field in ("skill_path", "workspace_root", "state_root", "docs_catalog", "skills_lock", "telegram_env"):
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
    if author not in project.get("allowed_authors", []):
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


def evaluate_merge_gate(gate: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if gate.get("mode") != "merge":
        errors.append("project is not in merge mode")
    if not gate.get("eligible"):
        errors.append("pull request eligibility failed")
    if not gate.get("consensus_clean"):
        errors.append("independent clean-review consensus is missing")
    if not gate.get("documentation_current"):
        errors.append("required documentation is missing or stale")
    if not gate.get("validation_passed"):
        errors.append("project validation did not pass")
    if not gate.get("required_checks_passed"):
        errors.append("required GitHub checks did not pass")
    if not gate.get("mergeable"):
        errors.append("pull request is not mergeable")
    if not gate.get("merge_state_clean"):
        errors.append("pull request merge state is not clean")
    if gate.get("reviewed_head_sha") != gate.get("current_head_sha"):
        errors.append("pull request head changed after review")
    if gate.get("reviewed_base_sha") != gate.get("current_base_sha"):
        errors.append("pull request base changed after review")
    if gate.get("unresolved_blockers"):
        errors.append("unresolved blockers remain")
    return errors
