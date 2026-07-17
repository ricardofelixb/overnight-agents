#!/usr/bin/env python3
"""Validate orchestrator invariants that JSON Schema cannot express."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable


class ContractError(ValueError):
    pass


SPECIALISTS = {"behavior-contracts", "security-provider", "hygiene-tests"}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ContractError(f"{path}: expected a JSON object")
    return value


def _paths(values: Iterable[Any], field: str) -> set[str]:
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value or value.startswith("/"):
            raise ContractError(f"{field}: invalid repository-relative path {value!r}")
        normalized = Path(value).as_posix()
        if normalized == ".." or normalized.startswith("../"):
            raise ContractError(f"{field}: path escapes repository: {value!r}")
        result.add(normalized)
    return result


def validate_result(
    result: dict[str, Any],
    *,
    expected_base: str,
    expected_head: str,
    changed_files: set[str],
    docs_manifest: dict[str, Any],
    skills_manifest: dict[str, Any],
) -> list[str]:
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(result.get("schema_version") == 1, "schema_version must equal 1")
    require(result.get("reviewed_base_sha") == expected_base, "base SHA mismatch")
    require(result.get("reviewed_head_sha") == expected_head, "head SHA mismatch")

    try:
        reviewed = _paths(result.get("reviewed_files", []), "reviewed_files")
        reported_changes = _paths(result.get("changed_files", []), "changed_files")
        test_changes = _paths(result.get("tests_changed", []), "tests_changed")
    except ContractError as error:
        errors.append(str(error))
        reviewed, reported_changes, test_changes = set(), set(), set()
    require(changed_files <= reviewed, "reviewed_files must include every PR changed file")
    require(test_changes <= reported_changes, "tests_changed must be a subset of changed_files")

    specialists = result.get("specialists", [])
    names = [item.get("name") for item in specialists if isinstance(item, dict)]
    require(len(names) == len(set(names)), "specialist names must be unique")
    require(set(names) == SPECIALISTS, "all required specialist sub-agents must report")

    status = result.get("status")
    repairs = result.get("repairs", [])
    blockers = result.get("blocking_reasons", [])
    verification = result.get("verification", {})
    if status == "clean":
        require(not repairs, "clean result cannot contain repairs")
        require(not reported_changes, "clean result cannot contain changed files")
        require(not blockers, "clean result cannot contain blockers")
        require(verification.get("verdict") == "not_needed", "clean result must mark verification not needed")
    elif status == "repaired":
        require(bool(repairs), "repaired result requires repairs")
        require(bool(reported_changes), "repaired result requires changed files")
        require(not blockers, "repaired result cannot contain blockers")
        require(verification.get("performed") is True, "repaired result requires fresh verification")
        require(verification.get("verdict") == "passed", "repaired result requires passed verification")
        repair_files = {
            path
            for repair in repairs
            if isinstance(repair, dict)
            for path in repair.get("files", [])
            if isinstance(path, str)
        }
        require(repair_files == reported_changes, "repair files must exactly match changed_files")
    elif status == "blocked":
        require(bool(blockers), "blocked result requires blocking reasons")
        require(not repairs, "blocked result cannot contain repairs")
        require(not reported_changes, "blocked result cannot leave changed files")
    elif status == "repaired_blocked":
        require(bool(repairs), "repaired_blocked result requires repairs")
        require(bool(reported_changes), "repaired_blocked result requires changed files")
        require(bool(blockers), "repaired_blocked result requires blocking reasons")
        require(verification.get("performed") is True, "repaired_blocked result requires fresh verification")
        require(verification.get("verdict") == "passed", "repaired_blocked result requires passed verification")
        repair_files = {
            path
            for repair in repairs
            if isinstance(repair, dict)
            for path in repair.get("files", [])
            if isinstance(path, str)
        }
        require(repair_files == reported_changes, "repair files must exactly match changed_files")
    else:
        errors.append(f"invalid status: {status!r}")

    candidate_domains = set(docs_manifest.get("domains", []))
    documentation = result.get("documentation", [])
    manifest_docs: dict[str, list[dict[str, Any]]] = {}
    for item in docs_manifest.get("documents", []):
        manifest_docs.setdefault(item.get("domain"), []).append(item)
    locked = skills_manifest.get("domains", {})
    for record in documentation:
        if not isinstance(record, dict):
            continue
        domain = record.get("provider")
        require(domain in candidate_domains, f"documentation provider is not a candidate domain: {domain}")
        if domain not in candidate_domains:
            continue
        allowed_urls = {
            url
            for item in manifest_docs.get(domain, [])
            for url in (item.get("url"), item.get("final_url"))
            if isinstance(url, str)
        }
        skill_revisions = {(item.get("name"), item.get("revision")) for item in locked.get(domain, [])}
        require(record.get("url") in allowed_urls, f"unapproved documentation URL for {domain}")
        require(
            (record.get("skill"), record.get("skill_revision")) in skill_revisions,
            f"unapproved skill name/revision pair for {domain}",
        )
        matching = [
            item
            for item in manifest_docs.get(domain, [])
            if record.get("url") in {item.get("url"), item.get("final_url")}
        ]
        require(
            any(record.get("retrieved_at") == item.get("retrieved_at") for item in matching),
            f"documentation retrieval timestamp mismatch for {domain}",
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--changed-files", type=Path, required=True)
    parser.add_argument("--docs-manifest", type=Path, required=True)
    parser.add_argument("--skills-manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        errors = validate_result(
            _load(args.result),
            expected_base=args.base,
            expected_head=args.head,
            changed_files=_paths(
                (line for line in args.changed_files.read_text().splitlines() if line),
                "changed_files",
            ),
            docs_manifest=_load(args.docs_manifest),
            skills_manifest=_load(args.skills_manifest),
        )
    except (OSError, json.JSONDecodeError, ContractError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
