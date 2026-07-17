#!/usr/bin/env python3
"""Validate semantic invariants that JSON Schema cannot express."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable


class ContractError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ContractError(f"{path}: expected a JSON object")
    return value


def _normalized_paths(values: Iterable[Any], field: str) -> set[str]:
    result: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value or value.startswith("/"):
            raise ContractError(f"{field}: invalid repository-relative path {value!r}")
        normalized = Path(value).as_posix()
        if normalized == ".." or normalized.startswith("../"):
            raise ContractError(f"{field}: path escapes repository: {value!r}")
        result.add(normalized)
    return result


def validate_review_result(
    result: dict[str, Any],
    *,
    expected_base: str,
    expected_head: str,
    expected_phase: str,
    changed_files: set[str],
    docs_manifest: dict[str, Any] | None = None,
    skills_manifest: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(result.get("schema_version") == 1, "schema_version must equal 1")
    require(result.get("reviewed_base_sha") == expected_base, "base SHA mismatch")
    require(result.get("reviewed_head_sha") == expected_head, "head SHA mismatch")
    require(result.get("phase") == expected_phase, "phase mismatch")

    coverage = result.get("coverage")
    if not isinstance(coverage, dict):
        return errors + ["coverage must be an object"]

    try:
        reviewed = _normalized_paths(coverage.get("changed_files_reviewed", []), "changed_files_reviewed")
        unreviewed = _normalized_paths(coverage.get("changed_files_unreviewed", []), "changed_files_unreviewed")
    except ContractError as error:
        errors.append(str(error))
        reviewed, unreviewed = set(), set()

    require(not reviewed.intersection(unreviewed), "reviewed and unreviewed files overlap")
    require(reviewed.union(unreviewed) == changed_files, "coverage does not exactly match changed files")

    findings = result.get("findings")
    contracts = result.get("behavioral_contracts")
    blockers = result.get("blocking_reasons")
    verdict = result.get("verdict")
    require(isinstance(findings, list), "findings must be an array")
    require(isinstance(contracts, list), "behavioral_contracts must be an array")
    require(isinstance(blockers, list), "blocking_reasons must be an array")
    if not isinstance(findings, list) or not isinstance(contracts, list) or not isinstance(blockers, list):
        return errors

    finding_ids: set[str] = set()
    for finding in findings:
        if not isinstance(finding, dict):
            errors.append("every finding must be an object")
            continue
        finding_id = finding.get("id")
        require(isinstance(finding_id, str) and finding_id not in finding_ids, f"duplicate/invalid finding id: {finding_id!r}")
        if isinstance(finding_id, str):
            finding_ids.add(finding_id)
        start = finding.get("start_line")
        end = finding.get("end_line")
        require(isinstance(start, int) and isinstance(end, int) and end >= start, f"finding {finding_id}: invalid line range")
        require(finding.get("file") in changed_files, f"finding {finding_id}: file is not changed")

    statuses = {contract.get("status") for contract in contracts if isinstance(contract, dict)}
    if verdict == "clean":
        require(not findings, "clean verdict cannot contain findings")
        require(not blockers, "clean verdict cannot contain blockers")
        require(not unreviewed, "clean verdict cannot contain unreviewed files")
        require(statuses <= {"preserved"}, "clean verdict requires all contracts preserved")
        require(bool(contracts) or not changed_files, "clean verdict requires at least one behavioral contract")
    elif verdict == "fixable":
        require(bool(findings), "fixable verdict requires findings")
        require(any(isinstance(item, dict) and item.get("auto_fix_safe") is True for item in findings), "fixable verdict requires an auto-fix-safe finding")
        require("uncertain" not in statuses, "fixable verdict cannot hide an uncertain contract")
    elif verdict == "blocked":
        require(bool(blockers), "blocked verdict requires a blocking reason")
    else:
        errors.append(f"invalid verdict: {verdict!r}")

    if docs_manifest is not None and skills_manifest is not None:
        required_domains = set(docs_manifest.get("domains", []))
        documentation = result.get("documentation", [])
        if not isinstance(documentation, list):
            errors.append("documentation must be an array")
            documentation = []
        manifest_docs: dict[str, list[dict[str, Any]]] = {}
        for item in docs_manifest.get("documents", []):
            manifest_docs.setdefault(item.get("domain"), []).append(item)
        locked = skills_manifest.get("domains", {})
        for domain in required_domains:
            records = [item for item in documentation if isinstance(item, dict) and item.get("provider") == domain]
            require(bool(records), f"missing documentation evidence for {domain}")
            allowed_urls = {
                url
                for item in manifest_docs.get(domain, [])
                for url in (item.get("url"), item.get("final_url"))
                if isinstance(url, str)
            }
            skill_revisions = {(item.get("name"), item.get("revision")) for item in locked.get(domain, [])}
            expected_coverage = set(coverage.get("domains", [])) if isinstance(coverage.get("domains"), list) else set()
            require(domain in expected_coverage, f"coverage is missing detected domain {domain}")
            for record in records:
                require(record.get("url") in allowed_urls, f"unapproved documentation URL for {domain}")
                require(
                    (record.get("skill"), record.get("skill_revision")) in skill_revisions,
                    f"unapproved skill name/revision pair for {domain}",
                )
                matching_docs = [
                    item
                    for item in manifest_docs.get(domain, [])
                    if record.get("url") in {item.get("url"), item.get("final_url")}
                ]
                require(
                    any(record.get("retrieved_at") == item.get("retrieved_at") for item in matching_docs),
                    f"documentation retrieval timestamp mismatch for {domain}",
                )

    return errors


def validate_consensus(results: list[dict[str, Any]], required_lenses: set[str]) -> list[str]:
    errors: list[str] = []
    if len(results) < 2:
        errors.append("at least two independent review results are required")
        return errors
    bases = {item.get("reviewed_base_sha") for item in results}
    heads = {item.get("reviewed_head_sha") for item in results}
    lenses = {item.get("lens") for item in results}
    if len(bases) != 1:
        errors.append("review passes disagree on base SHA")
    if len(heads) != 1:
        errors.append("review passes disagree on head SHA")
    if any(item.get("verdict") != "clean" for item in results):
        errors.append("every review pass must be clean")
    if len(lenses) != len(results):
        errors.append("review passes must use distinct lenses")
    if lenses != required_lenses:
        errors.append(f"missing required lenses: {sorted(required_lenses - lenses)}")
    return errors


def _read_changed_files(path: Path) -> set[str]:
    return _normalized_paths((line for line in path.read_text().splitlines() if line), "changed_files")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--result", type=Path, required=True)
    validate.add_argument("--base", required=True)
    validate.add_argument("--head", required=True)
    validate.add_argument("--phase", choices=["analysis", "verification"], required=True)
    validate.add_argument("--changed-files", type=Path, required=True)
    validate.add_argument("--docs-manifest", type=Path)
    validate.add_argument("--skills-manifest", type=Path)

    consensus = subparsers.add_parser("consensus")
    consensus.add_argument("--result", type=Path, action="append", required=True)
    consensus.add_argument("--required-lens", action="append", default=[])

    args = parser.parse_args()
    try:
        if args.command == "validate":
            errors = validate_review_result(
                _load(args.result),
                expected_base=args.base,
                expected_head=args.head,
                expected_phase=args.phase,
                changed_files=_read_changed_files(args.changed_files),
                docs_manifest=_load(args.docs_manifest) if args.docs_manifest else None,
                skills_manifest=_load(args.skills_manifest) if args.skills_manifest else None,
            )
        else:
            errors = validate_consensus([_load(path) for path in args.result], set(args.required_lens))
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
