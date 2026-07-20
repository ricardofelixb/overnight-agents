#!/usr/bin/env python3
"""Validate invariants for a simplify-pr-implementation result."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SPECIALISTS = {
    "reuse-abstractions",
    "quality-maintainability",
    "efficiency-performance",
}
STATUSES = {"clean", "simplified", "blocked", "simplified_blocked"}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _paths(values: Any, field: str, errors: list[str]) -> set[str]:
    if not isinstance(values, list):
        errors.append(f"{field} must be an array")
        return set()
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value or "\\" in value:
            errors.append(f"{field}: invalid repository-relative path {value!r}")
            continue
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
            errors.append(f"{field}: invalid repository-relative path {value!r}")
            continue
        result.add(value)
    if len(result) != len(values):
        errors.append(f"{field} must contain unique normalized paths")
    return result


def validate_result(
    result: dict[str, Any],
    *,
    expected_base: str,
    expected_head: str,
    pr_changed_files: set[str],
    actual_changed_files: set[str],
) -> list[str]:
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(bool(SHA_RE.fullmatch(expected_base)), "expected base SHA must be 40 lowercase hex characters")
    require(bool(SHA_RE.fullmatch(expected_head)), "expected head SHA must be 40 lowercase hex characters")
    require(result.get("schema_version") == 1, "schema_version must equal 1")
    require(result.get("reviewed_base_sha") == expected_base, "base SHA mismatch")
    require(result.get("reviewed_head_sha") == expected_head, "head SHA mismatch")
    require(result.get("status") in STATUSES, f"invalid status: {result.get('status')!r}")

    reviewed = _paths(result.get("reviewed_files"), "reviewed_files", errors)
    reported_changes = _paths(result.get("changed_files"), "changed_files", errors)
    test_changes = _paths(result.get("tests_changed"), "tests_changed", errors)
    require(pr_changed_files <= reviewed, "reviewed_files must include every PR changed file")
    require(reported_changes == actual_changed_files, "changed_files must exactly match the working-tree diff")
    require(test_changes <= reported_changes, "tests_changed must be a subset of changed_files")

    specialists = result.get("specialists")
    if not isinstance(specialists, list):
        errors.append("specialists must be an array")
        specialists = []
    names = [item.get("name") for item in specialists if isinstance(item, dict)]
    require(len(names) == len(set(names)), "specialist names must be unique")
    require(set(names) == SPECIALISTS, "all required specialist sub-agents must report")
    for index, specialist in enumerate(specialists):
        if not isinstance(specialist, dict):
            continue
        accepted = specialist.get("accepted_improvement_ids", [])
        require(
            isinstance(accepted, list) and len(accepted) == len(set(accepted)),
            f"specialists[{index}].accepted_improvement_ids must be unique",
        )

    improvements = result.get("improvements")
    if not isinstance(improvements, list):
        errors.append("improvements must be an array")
        improvements = []
    improvement_ids = [item.get("id") for item in improvements if isinstance(item, dict)]
    require(len(improvement_ids) == len(set(improvement_ids)), "improvement IDs must be unique")
    accepted_ids = {
        improvement_id
        for specialist in specialists
        if isinstance(specialist, dict)
        for improvement_id in specialist.get("accepted_improvement_ids", [])
        if isinstance(improvement_id, str)
    }
    require(accepted_ids == set(improvement_ids), "specialist accepted IDs must exactly match improvements")
    improvement_files: set[str] = set()
    for index, improvement in enumerate(improvements):
        if not isinstance(improvement, dict):
            errors.append(f"improvements[{index}] must be an object")
            continue
        improvement_files |= _paths(improvement.get("files"), f"improvements[{index}].files", errors)
    require(improvement_files == reported_changes, "improvement files must exactly match changed_files")

    blockers = result.get("blocking_reasons")
    require(isinstance(blockers, list), "blocking_reasons must be an array")
    if not isinstance(blockers, list):
        blockers = []
    verification = result.get("verification")
    if not isinstance(verification, dict):
        errors.append("verification must be an object")
        verification = {}

    status = result.get("status")
    if status == "clean":
        require(not improvements, "clean result cannot contain improvements")
        require(not reported_changes, "clean result cannot contain changed files")
        require(not blockers, "clean result cannot contain blockers")
        valid_clean_verification = (
            verification.get("performed") is False and verification.get("verdict") == "not_needed"
        ) or (
            verification.get("performed") is True and verification.get("verdict") == "passed"
        )
        require(valid_clean_verification, "clean verification must be not_needed or performed and passed")
    elif status == "simplified":
        require(bool(improvements), "simplified result requires improvements")
        require(bool(reported_changes), "simplified result requires changed files")
        require(not blockers, "simplified result cannot contain blockers")
        require(verification.get("performed") is True, "simplified result requires fresh verification")
        require(verification.get("verdict") == "passed", "simplified result requires passed verification")
    elif status == "blocked":
        require(bool(blockers), "blocked result requires blocking reasons")
        require(not improvements, "blocked result cannot contain improvements")
        require(not reported_changes, "blocked result cannot leave changed files")
    elif status == "simplified_blocked":
        require(bool(improvements), "simplified_blocked result requires improvements")
        require(bool(reported_changes), "simplified_blocked result requires changed files")
        require(bool(blockers), "simplified_blocked result requires blocking reasons")
        require(verification.get("performed") is True, "simplified_blocked result requires fresh verification")
        require(verification.get("verdict") == "passed", "simplified_blocked result requires passed verification")

    return errors


def _read_paths(path: Path) -> set[str]:
    values = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    errors: list[str] = []
    paths = _paths(values, str(path), errors)
    if errors:
        raise ValueError("; ".join(errors))
    return paths


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--expected-base", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--pr-changed-files", required=True, type=Path)
    parser.add_argument("--actual-changed-files", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        errors = validate_result(
            _load(args.result),
            expected_base=args.expected_base,
            expected_head=args.expected_head,
            pr_changed_files=_read_paths(args.pr_changed_files),
            actual_changed_files=_read_paths(args.actual_changed_files),
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(error, file=sys.stderr)
        return 2
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
