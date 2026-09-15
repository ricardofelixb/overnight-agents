"""Structured maintenance-agent reports and pull-request rendering."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable


MAINTENANCE_REPORT_PROMPT = """
In your final response, include exactly one single-line field using valid JSON:
MAINTENANCE_REPORT_JSON: <one compact JSON object matching the required schema>

Required schema: summary string; role_outcomes array of role/status/summary
objects; changes, deferred, and rejected arrays of role/summary objects. Status
must be changed, no-change, deferred, or mixed.

Include every selected role exactly once in role_outcomes, even when it found no
actionable change. Use empty arrays when there are no deferred or rejected
findings. Because a pull request is published only for a changed tree, changes
must contain at least one adopted change. Keep every summary concise and
evidence-based. Do not include secrets, credentials, raw logs, or speculative
claims. This structured report is the source of truth for the pull-request
description.
""".strip()

REPORT_FIELD = "MAINTENANCE_REPORT_JSON"
_REPORT_PATTERN = re.compile(
    rf"^{REPORT_FIELD}:\s*(\{{.*\}})\s*$", re.MULTILINE
)
_ALLOWED_STATUSES = {"changed", "no-change", "deferred", "mixed"}
_MAX_SUMMARY_LENGTH = 600
_MAX_ITEMS = 20


class ReportFailure(ValueError):
    """Raised when an agent's publication report is missing or invalid."""


@dataclass(frozen=True)
class RoleOutcome:
    role: str
    status: str
    summary: str


@dataclass(frozen=True)
class ReportItem:
    role: str
    summary: str


@dataclass(frozen=True)
class MaintenanceReport:
    summary: str
    role_outcomes: tuple[RoleOutcome, ...]
    changes: tuple[ReportItem, ...]
    deferred: tuple[ReportItem, ...]
    rejected: tuple[ReportItem, ...]


def _clean_text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ReportFailure(f"{field} must be a string")
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        raise ReportFailure(f"{field} must not be empty")
    if len(cleaned) > _MAX_SUMMARY_LENGTH:
        raise ReportFailure(
            f"{field} exceeds the {_MAX_SUMMARY_LENGTH}-character limit"
        )
    return cleaned


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ReportFailure(f"{field} must be an array")
    if len(value) > _MAX_ITEMS:
        raise ReportFailure(f"{field} exceeds the {_MAX_ITEMS}-item limit")
    return value


def _report_items(
    value: object,
    field: str,
    expected_roles: set[str],
) -> tuple[ReportItem, ...]:
    items: list[ReportItem] = []
    for index, raw_item in enumerate(_list(value, field)):
        if not isinstance(raw_item, dict):
            raise ReportFailure(f"{field}[{index}] must be an object")
        role = _clean_text(raw_item.get("role"), f"{field}[{index}].role")
        if role not in expected_roles:
            raise ReportFailure(f"{field}[{index}].role is not a selected role")
        summary = _clean_text(
            raw_item.get("summary"), f"{field}[{index}].summary"
        )
        items.append(ReportItem(role=role, summary=summary))
    return tuple(items)


def parse_maintenance_report(
    output: str,
    selected_roles: Iterable[str],
) -> MaintenanceReport:
    """Parse and validate the agent report required before publication."""

    expected_role_order = tuple(selected_roles)
    expected_roles = set(expected_role_order)
    if not expected_role_order or len(expected_roles) != len(expected_role_order):
        raise ReportFailure("selected roles must be unique and non-empty")

    matches = list(_REPORT_PATTERN.finditer(output))
    if not matches:
        raise ReportFailure("maintenance agent must emit a MAINTENANCE_REPORT_JSON field")
    decoded: list[object] = []
    for match in matches:
        try:
            candidate = json.loads(match.group(1))
        except json.JSONDecodeError as error:
            raise ReportFailure(
                f"MAINTENANCE_REPORT_JSON is not valid JSON: {error.msg}"
            ) from error
        if (
            isinstance(candidate, dict)
            and candidate.get("summary") == "one-sentence outcome"
            and any(
                isinstance(item, dict)
                and item.get("role") == "selected-role-id"
                for item in candidate.get("role_outcomes", [])
            )
        ):
            continue
        decoded.append(candidate)
    if not decoded:
        raise ReportFailure("maintenance agent must emit a final MAINTENANCE_REPORT_JSON field")
    raw = decoded[0]
    if not isinstance(raw, dict):
        raise ReportFailure("MAINTENANCE_REPORT_JSON must be an object")
    for duplicate in decoded[1:]:
        if duplicate != raw:
            raise ReportFailure(
                "maintenance agent emitted conflicting MAINTENANCE_REPORT_JSON fields"
            )

    outcomes_by_role: dict[str, RoleOutcome] = {}
    raw_outcomes = _list(raw.get("role_outcomes"), "role_outcomes")
    for index, raw_outcome in enumerate(raw_outcomes):
        if not isinstance(raw_outcome, dict):
            raise ReportFailure(f"role_outcomes[{index}] must be an object")
        role = _clean_text(raw_outcome.get("role"), f"role_outcomes[{index}].role")
        if role not in expected_roles:
            raise ReportFailure(
                f"role_outcomes[{index}].role is not a selected role"
            )
        if role in outcomes_by_role:
            raise ReportFailure(f"role_outcomes contains duplicate role {role}")
        status = _clean_text(
            raw_outcome.get("status"), f"role_outcomes[{index}].status"
        )
        if status not in _ALLOWED_STATUSES:
            raise ReportFailure(
                f"role_outcomes[{index}].status must be one of "
                + ", ".join(sorted(_ALLOWED_STATUSES))
            )
        outcomes_by_role[role] = RoleOutcome(
            role=role,
            status=status,
            summary=_clean_text(
                raw_outcome.get("summary"), f"role_outcomes[{index}].summary"
            ),
        )
    if set(outcomes_by_role) != expected_roles:
        missing = sorted(expected_roles - set(outcomes_by_role))
        raise ReportFailure(
            "role_outcomes must cover every selected role; missing: "
            + ", ".join(missing)
        )

    changes = _report_items(raw.get("changes"), "changes", expected_roles)
    if not changes:
        raise ReportFailure("changes must describe at least one adopted change")
    return MaintenanceReport(
        summary=_clean_text(raw.get("summary"), "summary"),
        role_outcomes=tuple(
            outcomes_by_role[role] for role in expected_role_order
        ),
        changes=changes,
        deferred=_report_items(raw.get("deferred"), "deferred", expected_roles),
        rejected=_report_items(raw.get("rejected"), "rejected", expected_roles),
    )


def _label(value: str) -> str:
    return value.replace("-", " ").capitalize()


def _findings_section(title: str, items: tuple[ReportItem, ...]) -> str:
    lines = [f"## {title}"]
    if items:
        lines.extend(
            f"- **{_label(item.role)}:** {item.summary}" for item in items
        )
    else:
        lines.append("- None.")
    return "\n".join(lines)


def maintenance_report_sections(report: MaintenanceReport) -> str:
    """Render the evidence-backed agent report as deterministic Markdown."""

    outcomes = "\n".join(
        f"- **{_label(outcome.role)} — {_label(outcome.status)}:** "
        f"{outcome.summary}"
        for outcome in report.role_outcomes
    )
    return "\n\n".join(
        (
            f"## Summary\n\n{report.summary}",
            f"## Specialist outcomes\n\n{outcomes}",
            _findings_section("Changes made", report.changes),
            _findings_section("Deferred findings", report.deferred),
            _findings_section("Rejected findings", report.rejected),
        )
    )
