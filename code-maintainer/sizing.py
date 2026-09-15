#!/usr/bin/env python3
"""Measure a staged maintenance diff and decide whether it may grow the source.

The controller and the maintenance agent share this module so both measure the
same thing: production source lines are counted separately from tests and
documentation, and a tree is publishable only when the source shrinks, or grows
within a small budget because a correctness or security fix was adopted.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from automation import runtime

DEFAULT_GROWTH_BUDGET = 30
FIX_ROLES = frozenset({"correctness-reliability", "security-hardening"})
CATEGORIES = ("source", "test", "docs")
_TEST_PATH = re.compile(
    r"(^|/)(?:tests?|__tests__)/"
    r"|(^|/)(?:test_[^/]+\.py|[^/]+_test\.py|conftest\.py)$"
    r"|\.(?:test|spec)\.[^/]+$"
)
_DOCS_PATH = re.compile(r"\.(?:md|mdx|rst|txt)$")


def classify(path: str) -> str:
    if _TEST_PATH.search(path):
        return "test"
    if _DOCS_PATH.search(path):
        return "docs"
    return "source"


@dataclass(frozen=True)
class Delta:
    added: int = 0
    deleted: int = 0

    @property
    def net(self) -> int:
        return self.added - self.deleted


@dataclass(frozen=True)
class DiffSize:
    source: Delta
    test: Delta
    docs: Delta

    @classmethod
    def from_numstat(cls, numstat: str) -> DiffSize:
        totals = {name: [0, 0] for name in CATEGORIES}
        for line in numstat.splitlines():
            parts = line.split("\t")
            if len(parts) != 3 or "-" in parts[:2]:
                continue
            bucket = totals[classify(parts[2])]
            bucket[0] += int(parts[0])
            bucket[1] += int(parts[1])
        return cls(**{name: Delta(*totals[name]) for name in CATEGORIES})


def measure_staged(workspace: Path, stream: TextIO | None = None) -> DiffSize:
    """Measure the staged diff; callers stage the tree with `git add -A` first."""

    numstat = runtime.git(workspace, "diff", "--cached", "--numstat", stream=stream)
    return DiffSize.from_numstat(numstat.stdout)


def growth_violation(
    size: DiffSize, *, fix_adopted: bool, budget: int
) -> str | None:
    net = size.source.net
    if net <= 0:
        return None
    if not fix_adopted:
        return (
            f"production source grew by {net} lines without an adopted "
            "correctness or security fix; simplification must not grow the source"
        )
    if net > budget:
        return (
            f"production source grew by {net} lines, above the {budget}-line "
            "budget for a correctness or security fix"
        )
    return None


def _describe(delta: Delta) -> str:
    return f"+{delta.added} / −{delta.deleted} lines (net {delta.net:+d})"


def brief(size: DiffSize) -> str:
    return (
        f"source {size.source.net:+d}, tests {size.test.net:+d}, "
        f"docs {size.docs.net:+d}"
    )


def size_section(size: DiffSize) -> str:
    return "\n".join(
        (
            "## Size",
            "",
            f"- **Source:** {_describe(size.source)}",
            f"- **Tests:** {_describe(size.test)}",
            f"- **Docs:** {_describe(size.docs)}",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure the working tree and report whether its size is publishable."
    )
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--budget", type=int, default=DEFAULT_GROWTH_BUDGET)
    parser.add_argument(
        "--fix-adopted",
        action="store_true",
        help="an adopted change is a correctness-reliability or security-hardening fix",
    )
    args = parser.parse_args()
    runtime.git(args.workspace, "add", "-A")
    size = measure_staged(args.workspace)
    runtime.git(args.workspace, "reset", "-q")
    for name in CATEGORIES:
        print(f"{name}: {_describe(getattr(size, name))}")
    violation = growth_violation(
        size, fix_adopted=args.fix_adopted, budget=args.budget
    )
    if violation:
        print(f"REJECT: {violation}")
        return 1
    print("OK: the controller will publish a tree of this size")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
