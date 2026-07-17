#!/usr/bin/env python3
"""Project-agnostic, fail-closed autonomous pull request reviewer."""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from policy import detect_domains, evaluate_pr_eligibility, validate_config
from telegram_notify import NotificationFailure, deliver_notification, enqueue_notification


PR_FIELDS = ",".join(
    [
        "author",
        "baseRefName",
        "baseRefOid",
        "body",
        "comments",
        "commits",
        "headRefName",
        "headRefOid",
        "headRepositoryOwner",
        "id",
        "isCrossRepository",
        "isDraft",
        "number",
        "reviews",
        "state",
        "title",
        "updatedAt",
        "url",
    ]
)
SAFE_CODEX_ENV = {
    "CODEX_HOME",
    "HOME",
    "LANG",
    "LC_ALL",
    "LOGNAME",
    "PATH",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TERM",
    "TMPDIR",
    "USER",
}
SAFE_VALIDATION_ENV = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LOGNAME",
    "PATH",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TERM",
    "TMPDIR",
    "USER",
}
REVIEW_COMMENT_MARKER = "<!-- overnight-agents:pr-reviewer -->"


class ReviewFailure(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ReviewFailure(f"invalid timestamp: {value!r}") from error
    if parsed.tzinfo is None:
        raise ReviewFailure(f"timestamp has no timezone: {value!r}")
    return parsed


def resolve_path(config_dir: Path, value: str) -> Path:
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    return path if path.is_absolute() else (config_dir / path).resolve()


class Runner:
    def __init__(self, log_path: Path, timeout_seconds: int = 7200):
        self.log_path = log_path
        self.timeout_seconds = timeout_seconds
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, message: str) -> None:
        line = f"[{utc_now().isoformat()}] {message}"
        print(line, flush=True)
        with self.log_path.open("a") as stream:
            stream.write(line + "\n")

    def run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        capture: bool = True,
        check: bool = True,
        log_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        def display(argument: str) -> str:
            if "\n" in argument or len(argument) > 300:
                return f"<inline-argument:{len(argument)}-chars>"
            if re.search(r"(?i)(token|secret|password|authorization)=", argument):
                return "<redacted-credential-argument>"
            return re.sub(r"(https?://)[^/@\s]+@", r"\1<redacted>@", argument)

        self.log("RUN " + " ".join(display(argument) for argument in command))
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                text=True,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.STDOUT if capture else None,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise ReviewFailure(f"command timed out after {self.timeout_seconds}s: {command[0]}") from error
        if capture and log_output and result.stdout:
            with self.log_path.open("a") as stream:
                stream.write(result.stdout)
                if not result.stdout.endswith("\n"):
                    stream.write("\n")
        if check and result.returncode != 0:
            if capture and not log_output and result.stdout:
                with self.log_path.open("a") as stream:
                    stream.write(f"COMMAND FAILURE OUTPUT ({command[0]}):\n")
                    stream.write(result.stdout)
                    if not result.stdout.endswith("\n"):
                        stream.write("\n")
            raise ReviewFailure(f"command failed ({result.returncode}): {command[0]}")
        return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewFailure(f"cannot load {path}: {error}") from error
    if not isinstance(value, dict):
        raise ReviewFailure(f"{path} must contain a JSON object")
    return value


def load_configuration(path: Path) -> tuple[dict[str, Any], Path]:
    config = load_json(path)
    errors = validate_config(config, path)
    if errors:
        raise ReviewFailure("invalid configuration: " + "; ".join(errors))
    config_dir = path.resolve().parent
    for field in ("skill_path", "workspace_root", "state_root", "docs_catalog", "skills_lock", "telegram_env"):
        config[field] = str(resolve_path(config_dir, config[field]))
    defaults = config.get("defaults", {})
    if "environment_file" in defaults:
        defaults["environment_file"] = str(resolve_path(config_dir, defaults["environment_file"]))
    for project in config["projects"]:
        if "environment_file" in project:
            project["environment_file"] = str(resolve_path(config_dir, project["environment_file"]))
    return config, config_dir


def select_project(config: dict[str, Any], name: str) -> dict[str, Any]:
    for project in config["projects"]:
        if project["name"] == name:
            if not project.get("enabled", False):
                raise ReviewFailure(f"project {name} is disabled")
            merged = dict(config.get("defaults", {}))
            merged.update(project)
            return merged
    raise ReviewFailure(f"unknown project: {name}")


def require_commands() -> None:
    missing = [name for name in ("codex", "gh", "git") if shutil.which(name) is None]
    if missing:
        raise ReviewFailure(f"missing required commands: {', '.join(missing)}")


def fetch_pr(runner: Runner, repository: str, number: int) -> dict[str, Any]:
    result = runner.run(["gh", "pr", "view", str(number), "--repo", repository, "--json", PR_FIELDS])
    return json.loads(result.stdout)


def fetch_pr_at_head(
    runner: Runner,
    repository: str,
    number: int,
    expected_head: str,
    *,
    attempts: int = 6,
) -> dict[str, Any]:
    """Wait briefly for GitHub's PR head projection to reflect a successful branch push."""
    for attempt in range(1, attempts + 1):
        pr = fetch_pr(runner, repository, number)
        if pr.get("headRefOid") == expected_head:
            return pr
        if attempt < attempts:
            delay = min(2 ** (attempt - 1), 4)
            runner.log(
                f"GitHub PR head has not reached {expected_head[:12]}; "
                f"polling again in {delay}s ({attempt}/{attempts})"
            )
            time.sleep(delay)
    raise ReviewFailure("GitHub did not advance to the controller-pushed repair commit")


def fetch_review_threads_context(runner: Runner, repository: str, pr_number: int) -> list[dict[str, Any]]:
    owner, name = repository.split("/", 1)
    query = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
      repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){
        nodes{isResolved isOutdated path line originalLine comments(first:100){
          nodes{author{login} body createdAt url}
          pageInfo{hasNextPage}
        }}
        pageInfo{hasNextPage endCursor}
      }}}
    }"""
    cursor: str | None = None
    result: list[dict[str, Any]] = []
    while True:
        command = [
            "gh", "api", "graphql", "-f", f"query={query}", "-f", f"owner={owner}", "-f", f"name={name}",
            "-F", f"number={pr_number}",
        ]
        if cursor:
            command.extend(["-f", f"cursor={cursor}"])
        payload = json.loads(runner.run(command).stdout)
        threads = payload["data"]["repository"]["pullRequest"]["reviewThreads"]
        for thread in threads["nodes"]:
            comments = thread.get("comments") or {}
            if (comments.get("pageInfo") or {}).get("hasNextPage"):
                raise ReviewFailure("a review thread exceeds the supported 100-comment evidence limit")
            result.append(
                {
                    "isResolved": thread.get("isResolved"),
                    "isOutdated": thread.get("isOutdated"),
                    "path": thread.get("path"),
                    "line": thread.get("line"),
                    "originalLine": thread.get("originalLine"),
                    "comments": comments.get("nodes", []),
                }
            )
        page = threads["pageInfo"]
        if not page["hasNextPage"]:
            return result
        cursor = page["endCursor"]


def capture_review_context(
    runner: Runner,
    config: dict[str, Any],
    repository: str,
    pr: dict[str, Any],
    run_dir: Path,
    iteration: int,
    controller_login: str | None = None,
) -> Path:
    comments = [
        comment
        for comment in pr.get("comments", [])
        if not (
            controller_login
            and (comment.get("author") or {}).get("login") == controller_login
            and REVIEW_COMMENT_MARKER in (comment.get("body") or "")
        )
    ]
    context = {
        "version": 1,
        "retrieved_at": utc_now().isoformat(),
        "repository": repository,
        "pr_number": pr["number"],
        "url": pr["url"],
        "base_sha": pr["baseRefOid"],
        "head_sha": pr["headRefOid"],
        "title": pr.get("title", ""),
        "body": pr.get("body", ""),
        "commits": pr.get("commits", []),
        "comments": comments,
        "reviews": pr.get("reviews", []),
        "review_threads": fetch_review_threads_context(runner, repository, pr["number"]),
    }
    encoded = (json.dumps(context, indent=2, sort_keys=True) + "\n").encode()
    if len(encoded) > int(config.get("max_review_context_bytes", 1_000_000)):
        raise ReviewFailure("pull-request review context exceeds the configured evidence limit")
    path = run_dir / f"review-context-{iteration}.json"
    path.write_bytes(encoded)
    return path


def safe_workspace(root: Path, project_name: str, source_path: Path) -> Path:
    root = root.resolve()
    workspace = (root / project_name).resolve()
    if root == Path("/") or workspace == root or root not in workspace.parents:
        raise ReviewFailure("unsafe workspace path")
    source = source_path.resolve()
    if workspace == source or workspace in source.parents or source in workspace.parents:
        raise ReviewFailure("review workspace overlaps the source checkout")
    return workspace


def provision_workspace_environment(runner: Runner, workspace: Path, environment_file: Path | None) -> None:
    if environment_file is None:
        return
    try:
        source = environment_file.expanduser().resolve(strict=True)
    except OSError as error:
        raise ReviewFailure(f"project environment file is unavailable: {error}") from error
    if not source.is_file():
        raise ReviewFailure("project environment file is not a regular file")
    if stat.S_IMODE(source.stat().st_mode) & 0o077:
        raise ReviewFailure("project environment file must not be accessible by group or other users")
    ignored = runner.run(
        ["git", "check-ignore", "-q", ".env.local"],
        cwd=workspace,
        check=False,
        log_output=False,
    )
    if ignored.returncode != 0:
        raise ReviewFailure(".env.local must be ignored before reviewer environment provisioning")
    destination = workspace / ".env.local"
    if destination.is_symlink():
        if destination.resolve(strict=False) == source:
            return
        raise ReviewFailure("reviewer .env.local points to an unexpected file")
    if destination.exists():
        raise ReviewFailure("reviewer .env.local exists but is not a controller-managed symlink")
    destination.symlink_to(source)


def prepare_workspace(
    runner: Runner,
    workspace: Path,
    source_path: Path,
    repository: str,
    pr_number: int,
    base_branch: str,
    expected_base: str,
    expected_head: str,
    environment_file: Path | None = None,
) -> None:
    workspace.parent.mkdir(parents=True, exist_ok=True)
    origin = runner.run(
        ["git", "-C", str(source_path), "remote", "get-url", "origin"],
        log_output=False,
    ).stdout.strip()

    def quarantine(path: Path, reason: str) -> Path:
        quarantine_root = workspace.parent / ".quarantine"
        quarantine_root.mkdir(parents=True, exist_ok=True)
        stamp = utc_now().strftime("%Y%m%dT%H%M%S.%fZ")
        destination = quarantine_root / f"{path.name}-{stamp}-{os.getpid()}"
        path.rename(destination)
        runner.log(f"Quarantined reviewer workspace ({reason}): {destination}")
        return destination

    def synchronize(path: Path) -> None:
        if not (path / ".git").is_dir():
            raise ReviewFailure(f"workspace is not a dedicated clone: {path}")
        actual_origin = runner.run(["git", "remote", "get-url", "origin"], cwd=path, log_output=False).stdout.strip()
        if actual_origin != origin:
            raise ReviewFailure("dedicated review clone origin does not match the configured source checkout")
        runner.run(["git", "fetch", "--prune", "origin", base_branch], cwd=path)
        fetched_base = runner.run(["git", "rev-parse", f"origin/{base_branch}"], cwd=path).stdout.strip()
        if fetched_base != expected_base:
            raise ReviewFailure("fetched base branch does not match GitHub PR metadata")
        runner.run(
            ["git", "fetch", "--force", "origin", f"pull/{pr_number}/head:refs/remotes/origin/reviewer-pr-{pr_number}"],
            cwd=path,
        )
        fetched_head = runner.run(
            ["git", "rev-parse", f"refs/remotes/origin/reviewer-pr-{pr_number}"], cwd=path
        ).stdout.strip()
        if fetched_head != expected_head:
            raise ReviewFailure("fetched PR head does not match GitHub metadata")
        ancestor = runner.run(
            ["git", "merge-base", "--is-ancestor", f"origin/{base_branch}", expected_head],
            cwd=path,
            check=False,
        )
        if ancestor.returncode != 0:
            raise ReviewFailure("PR head is not based on the current base branch; refresh it before autonomous review")
        runner.run(["git", "checkout", "--detach", expected_head], cwd=path)
        runner.run(["git", "branch", "-D", f"reviewer-fix-{pr_number}"], cwd=path, check=False)
        if runner.run(["git", "status", "--porcelain"], cwd=path).stdout.strip():
            raise ReviewFailure("prepared review clone is unexpectedly dirty")

    if workspace.exists():
        reason: str | None = None
        if not (workspace / ".git").is_dir():
            reason = "not-a-dedicated-clone"
        else:
            actual_origin = runner.run(
                ["git", "remote", "get-url", "origin"], cwd=workspace, log_output=False, check=False
            ).stdout.strip()
            if actual_origin != origin:
                reason = "origin-mismatch"
            elif runner.run(["git", "status", "--porcelain"], cwd=workspace).stdout.strip():
                reason = "dirty-or-incomplete"
        if reason:
            quarantine(workspace, reason)

    if workspace.exists():
        synchronize(workspace)
        provision_workspace_environment(runner, workspace, environment_file)
        return

    temporary = Path(tempfile.mkdtemp(prefix=f".{workspace.name}.provision-", dir=workspace.parent))
    try:
        runner.run(["git", "clone", "--no-checkout", origin, str(temporary)])
        synchronize(temporary)
        if workspace.exists():
            raise ReviewFailure("review workspace appeared concurrently during provisioning")
        temporary.rename(workspace)
        provision_workspace_environment(runner, workspace, environment_file)
        runner.log(f"Provisioned clean reviewer workspace for {repository}")
    except Exception:
        if temporary.exists():
            quarantine(temporary, "provision-failed")
        raise


def changed_files_and_diff(runner: Runner, workspace: Path, base: str, head: str) -> tuple[list[str], str]:
    files = runner.run(["git", "diff", "--name-only", "--diff-filter=ACDMRTUXB", f"{base}...{head}"], cwd=workspace).stdout.splitlines()
    if not files:
        raise ReviewFailure("pull request has no changed files")
    diff = runner.run(["git", "diff", "--no-ext-diff", "--unified=20", f"{base}...{head}"], cwd=workspace).stdout
    return files, diff


def reject_policy_changes(changed_files: list[str], patterns: list[str]) -> None:
    protected = [path for path in changed_files if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)]
    if protected:
        raise ReviewFailure("PR changes trusted agent/review policy: " + ", ".join(sorted(protected)))


def validate_skill_lock(config: dict[str, Any], domains: list[str]) -> dict[str, Any]:
    lock = load_json(Path(config["skills_lock"]))
    if lock.get("version") != 1:
        raise ReviewFailure("unsupported skills lock version")
    max_age = timedelta(days=int(config.get("skill_max_age_days", 8)))
    release_root = (Path(config["state_root"]) / "skill-releases").resolve()
    evidence: dict[str, Any] = {"version": 1, "domains": {}}
    for domain in domains:
        entries = (lock.get("domains") or {}).get(domain)
        if not isinstance(entries, list) or not entries:
            raise ReviewFailure(f"no promoted global skill is locked for domain {domain}")
        checked: list[dict[str, Any]] = []
        for entry in entries:
            path = Path(entry.get("path", "")).resolve()
            if path == release_root or release_root not in path.parents:
                raise ReviewFailure(f"global skill path is outside the audited release store: {entry.get('name')}")
            updated_at = parse_time(entry.get("updated_at", ""))
            if utc_now() - updated_at > max_age:
                raise ReviewFailure(f"global skill is stale: {entry.get('name')}")
            if not (path / "SKILL.md").is_file():
                raise ReviewFailure(f"global skill is unavailable: {entry.get('name')}")
            if tree_hash(path) != entry.get("sha256"):
                raise ReviewFailure(f"global skill content hash mismatch: {entry.get('name')}")
            checked.append(
                {
                    key: entry.get(key)
                    for key in ("name", "path", "source", "revision", "updated_at", "sha256")
                }
            )
        evidence["domains"][domain] = checked
    return evidence


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def refresh_docs(
    runner: Runner,
    config: dict[str, Any],
    domains: list[str],
    run_dir: Path,
    iteration: int,
) -> Path:
    manifest = run_dir / f"docs-manifest-{iteration}.json"
    command = [
        sys.executable,
        str(Path(config["skill_path"]) / "scripts" / "refresh_docs.py"),
        "--catalog",
        config["docs_catalog"],
        "--cache-dir",
        str(Path(config["state_root"]) / "docs-cache"),
        "--manifest",
        str(manifest),
        "--max-age-hours",
        str(config.get("docs_max_age_hours", 24)),
        "--max-document-bytes",
        str(config.get("max_document_bytes", 5_000_000)),
    ]
    for domain in domains:
        command.extend(["--domain", domain])
    runner.run(command)
    return manifest


def validate_docs_manifest(config: dict[str, Any], manifest_path: Path, domains: list[str]) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    if manifest.get("version") != 1 or manifest.get("errors"):
        raise ReviewFailure("documentation manifest is invalid or contains errors")
    if set(manifest.get("domains", [])) != set(domains):
        raise ReviewFailure("documentation manifest domains do not match detected domains")
    cache_root = (Path(config["state_root"]) / "docs-cache").resolve()
    seen: set[str] = set()
    max_age = timedelta(hours=int(config.get("docs_max_age_hours", 24)))
    for entry in manifest.get("documents", []):
        domain = entry.get("domain")
        path = Path(entry.get("content_path", "")).resolve()
        if domain not in domains or path == cache_root or cache_root not in path.parents:
            raise ReviewFailure("documentation manifest contains an untrusted domain or path")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256"):
            raise ReviewFailure(f"documentation content hash mismatch for {entry.get('url')}")
        if utc_now() - parse_time(entry.get("retrieved_at", "")) > max_age:
            raise ReviewFailure(f"documentation is stale for {entry.get('url')}")
        seen.add(domain)
    if seen != set(domains):
        raise ReviewFailure("documentation manifest is missing a detected domain")
    return manifest


def codex_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in SAFE_CODEX_ENV}


def validation_environment(overrides: dict[str, str] | None = None) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key in SAFE_VALIDATION_ENV}
    environment["CI"] = "true"
    environment.update(overrides or {})
    return environment


def isolated_shell_config() -> list[str]:
    controlled_path = os.environ.get("PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
    locale = os.environ.get("LANG", "C.UTF-8")
    return [
        "--config",
        'shell_environment_policy.inherit="none"',
        "--config",
        f"shell_environment_policy.set={{ PATH = {json.dumps(controlled_path)}, LANG = {json.dumps(locale)} }}",
        "--config",
        "tools.web_search=false",
    ]


def orchestrator_prompt(
    *,
    skill_path: Path,
    pr: dict[str, Any],
    changed_files_path: Path,
    docs_manifest: Path,
    skills_manifest: Path,
    review_context: Path,
    validation_evidence: Path,
    repairs_allowed: bool,
) -> str:
    return f"""Use the autonomous PR review skill at {skill_path / 'SKILL.md'}.

Run one complete orchestrated review-and-repair lifecycle for this exact pull request.

Immutable controller inputs:
- PR: {pr['url']}
- PR number: {pr['number']}
- base SHA: {pr['baseRefOid']}
- head SHA: {pr['headRefOid']}
- exact PR changed-files list: {changed_files_path}
- untrusted SHA-bound PR artifacts: {review_context}
- trusted current official-docs candidate catalog: {docs_manifest}
- trusted promoted provider-skills candidate catalog: {skills_manifest}
- trusted controller validation evidence for the original head: {validation_evidence}
- repairs allowed: {str(repairs_allowed).lower()}

Read the skill and its core protocol. Spawn the three named specialist sub-agents concurrently. They must inspect and report only; you own all edits. Reconcile their raw findings yourself.

Provider catalogs are trusted menus, not mandatory context. Inspect their metadata, then let the concrete code question determine whether provider evidence is needed. Read only the smallest applicable router/topic skill and official document. Do not open every skill or document for a candidate domain, and do not report provider evidence you did not actually use.

Act on every proven, high-confidence, bounded improvement in the PR's behavioral slice, regardless of whether it was introduced by this PR, pre-existed at the base, or is a valid follow-up from PR artifacts. This includes correctness, security, reliability, performance, reuse, and worthwhile code hygiene. Official guidance is evidence, but it cannot invent product semantics.

Do not let an ambiguous issue suppress independent safe improvements. If both exist, leave the ambiguous area untouched, retain and verify the independent repairs, and return repaired_blocked. In reviewed_files, include every PR changed file plus every contextual repository file actually inspected.

If repairs are allowed, edit the working tree directly. Do not commit or push. After edits, spawn a fresh verifier sub-agent that receives raw diffs and evidence rather than your conclusions. If repairs are not allowed, do not edit and return blocked when an actionable repair exists.

The validation manifest is trusted controller evidence. You may run focused checks, but the controller will run the full configured validation after your edits. Never change Git configuration, history, remotes, hooks, credentials, controller state, protected policy, dependency manifests/locks, CI configuration, or generated guidance.

For provider evidence actually used, copy documentation URLs/timestamps and skill name/revision pairs exactly from the catalogs. Candidate domains do not require documentation records when provider evidence was unnecessary. Return only schema-conforming JSON.
"""


def run_orchestrator(
    runner: Runner,
    config: dict[str, Any],
    workspace: Path,
    pr: dict[str, Any],
    run_dir: Path,
    changed_files_path: Path,
    docs_manifest: Path,
    skills_manifest: Path,
    review_context: Path,
    validation_evidence: Path,
    repairs_allowed: bool,
) -> dict[str, Any]:
    skill_path = Path(config["skill_path"])
    output = run_dir / "orchestrator-result.json"
    schema = skill_path / "references" / "orchestrator-result.schema.json"
    sandbox = "workspace-write" if repairs_allowed else "read-only"
    command = [
        "codex",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--enable",
        "multi_agent",
        "--sandbox",
        sandbox,
        "--model",
        config["model"],
        "--config",
        f"model_reasoning_effort={json.dumps(config['reasoning_effort'])}",
        *isolated_shell_config(),
    ]
    if repairs_allowed:
        command.extend(
            [
                "--config",
                "sandbox_workspace_write.network_access=false",
                "--config",
                "sandbox_workspace_write.exclude_slash_tmp=true",
                "--config",
                "sandbox_workspace_write.exclude_tmpdir_env_var=true",
            ]
        )
    command.extend(
        [
            "--cd",
            str(workspace),
            "--output-schema",
            str(schema),
            "--output-last-message",
            str(output),
            orchestrator_prompt(
                skill_path=skill_path,
                pr=pr,
                changed_files_path=changed_files_path,
                docs_manifest=docs_manifest,
                skills_manifest=skills_manifest,
                review_context=review_context,
                validation_evidence=validation_evidence,
                repairs_allowed=repairs_allowed,
            ),
        ]
    )
    git_config_before = (workspace / ".git" / "config").read_bytes()
    runner.run(command, env=codex_environment(), log_output=False)
    if (workspace / ".git" / "config").read_bytes() != git_config_before:
        raise ReviewFailure("orchestrator changed local Git configuration")
    if runner.run(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip() != pr["headRefOid"]:
        raise ReviewFailure("orchestrator changed commit history; only the controller may commit")
    runner.run(
        [
            sys.executable,
            str(skill_path / "scripts" / "review_contract.py"),
            "--result",
            str(output),
            "--base",
            pr["baseRefOid"],
            "--head",
            pr["headRefOid"],
            "--changed-files",
            str(changed_files_path),
            "--docs-manifest",
            str(docs_manifest),
            "--skills-manifest",
            str(skills_manifest),
        ]
    )
    result = load_json(output)
    actual = workspace_changes(runner, workspace)
    reported = set(result.get("changed_files", []))
    if actual != reported:
        raise ReviewFailure("orchestrator changed-file report does not match the working tree")
    if actual:
        reject_policy_changes(sorted(actual), config.get("protected_policy_patterns", []))
    return result


def workspace_changes(runner: Runner, workspace: Path) -> set[str]:
    tracked = runner.run(["git", "diff", "--name-only", "HEAD"], cwd=workspace).stdout.splitlines()
    untracked = runner.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=workspace).stdout.splitlines()
    return {path for path in tracked + untracked if path}


def workspace_fingerprint(runner: Runner, workspace: Path) -> dict[str, str]:
    fingerprint: dict[str, str] = {}
    for relative in sorted(workspace_changes(runner, workspace)):
        path = workspace / relative
        fingerprint[relative] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "<deleted-or-non-file>"
    return fingerprint


def assert_clean_workspace(runner: Runner, workspace: Path, context: str) -> None:
    changed = workspace_changes(runner, workspace)
    if changed:
        raise ReviewFailure(f"{context} mutated the review checkout: {', '.join(sorted(changed))}")


def run_project_commands(
    runner: Runner,
    workspace: Path,
    commands: list[list[str]],
    markers: list[str],
    environment_overrides: dict[str, str] | None = None,
    attempts: int = 1,
) -> str:
    outputs: list[str] = []
    last_failure = "validation failed"
    for attempt in range(1, attempts + 1):
        combined = ""
        failed = False
        for command in commands:
            result = runner.run(
                command,
                cwd=workspace,
                env=validation_environment(environment_overrides),
                check=False,
            )
            combined += result.stdout or ""
            if result.returncode != 0:
                failed = True
                last_failure = f"command failed ({result.returncode}): {command[0]}"
                break
        missing = [marker for marker in markers if marker not in combined]
        if missing:
            failed = True
            last_failure = f"validation success marker not found: {missing[0]}"
        outputs.append(f"=== attempt {attempt}/{attempts} ===\n{combined}")
        if not failed:
            return "".join(outputs)
        if attempt < attempts:
            runner.log(f"Validation attempt {attempt}/{attempts} failed; retrying the full configured validation")
    raise ReviewFailure(last_failure)


def write_validation_evidence(
    run_dir: Path,
    iteration: int,
    pr: dict[str, Any],
    setup_commands: list[list[str]],
    validation_commands: list[list[str]],
    success_markers: list[str],
    validation_environment_values: dict[str, str],
    setup_output: str,
    validation_output: str,
) -> Path:
    setup_path = run_dir / f"setup-output-{iteration}.log"
    validation_path = run_dir / f"validation-output-{iteration}.log"
    setup_path.write_text(setup_output)
    validation_path.write_text(validation_output)
    evidence = {
        "version": 1,
        "completed_at": utc_now().isoformat(),
        "base_sha": pr["baseRefOid"],
        "head_sha": pr["headRefOid"],
        "setup_commands": setup_commands,
        "validation_commands": validation_commands,
        "validation_environment": validation_environment_values,
        "success_markers": success_markers,
        "setup_output_path": str(setup_path),
        "setup_output_sha256": hashlib.sha256(setup_path.read_bytes()).hexdigest(),
        "validation_output_path": str(validation_path),
        "validation_output_sha256": hashlib.sha256(validation_path.read_bytes()).hexdigest(),
        "status": "passed",
    }
    path = run_dir / f"validation-evidence-{iteration}.json"
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return path


def commit_and_push_repair(runner: Runner, workspace: Path, pr: dict[str, Any]) -> str:
    runner.run(["git", "diff", "--check"], cwd=workspace)
    runner.run(["git", "add", "-A"], cwd=workspace)
    runner.run(["git", "diff", "--cached", "--quiet"], cwd=workspace, check=False)
    runner.run(["git", "commit", "-m", f"fix: address autonomous review findings for PR #{pr['number']}"], cwd=workspace)
    runner.run(["git", "push", "origin", f"HEAD:{pr['headRefName']}"], cwd=workspace)
    return runner.run(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip()


def _comment_text(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())[:limit]
    return text.replace("@", "@\u200b")


def format_review_comment(
    result: dict[str, Any],
    *,
    original_head: str,
    final_head: str,
    validation_commands: list[list[str]],
) -> str:
    status = result["status"]
    if status == "repaired_blocked":
        heading = "## Autonomous review: improved, decision still required"
        outcome = "I fixed and verified independent proven issues on this PR branch, but one or more separate decisions still prevent a safe merge recommendation."
    elif status == "blocked":
        heading = "## Autonomous review: decision required"
        outcome = "I could not safely complete this PR without guessing about behavior or scope."
    elif status == "repaired":
        heading = "## Autonomous review: fixed and safe to merge"
        outcome = "I found proven issues, fixed them on this PR branch, and the controller re-ran full validation."
    else:
        heading = "## Autonomous review: safe to merge"
        outcome = "I reviewed the PR and found no actionable issue that warranted a code change."

    lines = [
        REVIEW_COMMENT_MARKER,
        heading,
        "",
        outcome,
        "",
        f"Reviewed head: `{original_head[:12]}`",
    ]
    if final_head != original_head:
        lines.append(f"Repair commit: `{final_head[:12]}`")

    repairs = [item for item in result.get("repairs", []) if isinstance(item, dict)]
    if repairs:
        lines.extend(["", "### Repairs"])
        labels = {"introduced": "introduced", "pre_existing": "pre-existing", "pr_follow_up": "PR follow-up"}
        for repair in repairs:
            label = labels.get(repair.get("provenance"), "review")
            lines.append(f"- **{_comment_text(repair.get('title'), 180)}** ({label}): {_comment_text(repair.get('evidence'), 500)}")

    if status != "blocked":
        lines.extend(["", "### Validation"])
        for command in validation_commands:
            lines.append(f"- `{' '.join(command)}` passed")
        verification = result.get("verification") or {}
        if verification.get("performed"):
            lines.append(f"- Fresh verifier sub-agent: {_comment_text(verification.get('summary'), 500)}")

    observations = [_comment_text(item, 500) for item in result.get("remaining_observations", []) if item]
    if observations:
        lines.extend(["", "### Notes"])
        lines.extend(f"- {item}" for item in observations)

    blockers = [_comment_text(item, 500) for item in result.get("blocking_reasons", []) if item]
    if blockers:
        lines.extend(["", "### Blocking reasons"])
        lines.extend(f"- {item}" for item in blockers)

    lines.extend(["", "Manual merge remains under repository controls and required GitHub checks."])
    return "\n".join(lines)[:60_000]


def upsert_review_comment(runner: Runner, repository: str, pr: dict[str, Any], body: str) -> None:
    owner, name = repository.split("/", 1)
    query = """query($owner:String!,$name:String!,$number:Int!){
      viewer{login}
      repository(owner:$owner,name:$name){pullRequest(number:$number){comments(last:100){
        nodes{id body author{login}}
      }}}
    }"""
    payload = json.loads(
        runner.run(
            [
                "gh", "api", "graphql", "-f", f"query={query}", "-f", f"owner={owner}",
                "-f", f"name={name}", "-F", f"number={pr['number']}",
            ]
        ).stdout
    )
    viewer = payload["data"]["viewer"]["login"]
    comments = payload["data"]["repository"]["pullRequest"]["comments"]["nodes"]
    existing = next(
        (
            item
            for item in reversed(comments)
            if (item.get("author") or {}).get("login") == viewer
            and REVIEW_COMMENT_MARKER in (item.get("body") or "")
        ),
        None,
    )
    if existing and existing.get("body") == body:
        return
    if existing:
        mutation = """mutation($id:ID!,$body:String!){
          updateIssueComment(input:{id:$id,body:$body}){issueComment{id}}
        }"""
        runner.run(
            [
                "gh", "api", "graphql", "-f", f"query={mutation}",
                "-f", f"id={existing['id']}", "-f", f"body={body}",
            ]
        )
        return
    mutation = """mutation($subjectId:ID!,$body:String!){
      addComment(input:{subjectId:$subjectId,body:$body}){commentEdge{node{id}}}
    }"""
    runner.run(
        [
            "gh", "api", "graphql", "-f", f"query={mutation}",
            "-f", f"subjectId={pr['id']}", "-f", f"body={body}",
        ]
    )


def review_state_path(state_root: Path, project_name: str, pr_number: int) -> Path:
    return state_root / "review-state" / project_name / f"{pr_number}.json"


def review_is_current(state_root: Path, project_name: str, pr: dict[str, Any]) -> bool:
    path = review_state_path(state_root, project_name, pr["number"])
    try:
        state = load_json(path)
    except ReviewFailure:
        return False
    return state.get("head_sha") == pr.get("headRefOid") and state.get("pr_updated_at") == pr.get("updatedAt")


def legacy_head_was_reviewed(state_root: Path, project_name: str, pr: dict[str, Any]) -> bool:
    run_root = state_root / "runs" / project_name / str(pr["number"])
    for path in sorted(run_root.glob("*/summary.json"), reverse=True):
        try:
            summary = load_json(path)
        except ReviewFailure:
            continue
        if pr.get("headRefOid") in {summary.get("head_sha"), summary.get("reviewed_head_sha")}:
            return True
    return False


def record_review_state(state_root: Path, project_name: str, pr: dict[str, Any], status: str) -> None:
    path = review_state_path(state_root, project_name, pr["number"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "version": 1,
                "project": project_name,
                "pr_number": pr["number"],
                "head_sha": pr["headRefOid"],
                "pr_updated_at": pr.get("updatedAt"),
                "status": status,
                "recorded_at": utc_now().isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    os.replace(temporary, path)


def write_summary(run_dir: Path, value: dict[str, Any]) -> None:
    (run_dir / "summary.json").write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def execute(config_path: Path, project_name: str, pr_number: int, apply: bool, force: bool = False) -> int:
    config, _ = load_configuration(config_path)
    project = select_project(config, project_name)
    require_commands()

    state_root = Path(config["state_root"])
    run_id = utc_now().strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = state_root / "runs" / project_name / str(pr_number) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    runner = Runner(
        Path(config.get("log_path", run_dir / "review.log")),
        timeout_seconds=int(project.get("command_timeout_seconds", 7200)),
    )
    lock_path = state_root / "locks" / f"{project_name}-{pr_number}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ReviewFailure("another review is already running for this PR") from error

        repository = project["repository"]
        source_repository = json.loads(
            runner.run(["gh", "repo", "view", "--json", "nameWithOwner"], cwd=Path(project["source_path"])).stdout
        )["nameWithOwner"]
        if source_repository.lower() != repository.lower():
            raise ReviewFailure("configured repository does not match source checkout")
        pr = fetch_pr(runner, repository, pr_number)
        eligibility_errors = evaluate_pr_eligibility(pr, project)
        if eligibility_errors:
            raise ReviewFailure("ineligible PR: " + "; ".join(eligibility_errors))
        if not force and review_is_current(state_root, project_name, pr):
            runner.log(f"Skipping PR #{pr_number}; head and PR context were already reviewed")
            return 0
        if not force and legacy_head_was_reviewed(state_root, project_name, pr):
            record_review_state(state_root, project_name, pr, "legacy-reviewed")
            runner.log(f"Skipping PR #{pr_number}; migrated prior review state for this head")
            return 0

        source_path = Path(project["source_path"])
        workspace = safe_workspace(Path(config["workspace_root"]), project_name, source_path)
        prepare_workspace(
            runner,
            workspace,
            source_path,
            repository,
            pr_number,
            project["base_branch"],
            pr["baseRefOid"],
            pr["headRefOid"],
            Path(project["environment_file"]) if project.get("environment_file") else None,
        )
        changed_files, diff = changed_files_and_diff(runner, workspace, pr["baseRefOid"], pr["headRefOid"])
        if len(changed_files) > int(project.get("max_changed_files", 200)):
            raise ReviewFailure("pull request exceeds the configured changed-file limit")
        if len(diff.encode()) > int(project.get("max_diff_bytes", 1_500_000)):
            raise ReviewFailure("pull request exceeds the configured diff-size limit")
        if any(
            Path(path).is_absolute()
            or Path(path).as_posix() == ".."
            or Path(path).as_posix().startswith("../")
            or any(ord(character) < 32 for character in path)
            for path in changed_files
        ):
            raise ReviewFailure("pull request contains an unsafe changed-file path")
        changed_files_path = run_dir / "changed-files.txt"
        changed_files_path.write_text("\n".join(changed_files) + "\n")
        reject_policy_changes(changed_files, project.get("protected_policy_patterns", []))

        candidate_domains = detect_domains(changed_files, diff)
        skills_manifest = run_dir / "skills-manifest.json"
        skills_manifest.write_text(
            json.dumps(validate_skill_lock(config | project, candidate_domains), indent=2, sort_keys=True) + "\n"
        )
        docs_manifest = refresh_docs(runner, config | project, candidate_domains, run_dir, 0)
        validate_docs_manifest(config | project, docs_manifest, candidate_domains)
        controller_login = runner.run(["gh", "api", "user", "--jq", ".login"]).stdout.strip()
        review_context = capture_review_context(
            runner, config | project, repository, pr, run_dir, 0, controller_login
        )

        setup_commands = project.get("setup_commands", [])
        validation_commands = project["validation_commands"]
        success_markers = project.get("validation_success_markers", [])
        validation_env = project.get("validation_environment", {})
        validation_attempts = int(project.get("validation_attempts", 1))
        setup_output = run_project_commands(runner, workspace, setup_commands, [], validation_env)
        validation_output = run_project_commands(
            runner, workspace, validation_commands, success_markers, validation_env, validation_attempts
        )
        assert_clean_workspace(runner, workspace, "initial setup/validation")
        validation_evidence = write_validation_evidence(
            run_dir,
            0,
            pr,
            setup_commands,
            validation_commands,
            success_markers,
            validation_env,
            setup_output,
            validation_output,
        )

        repairs_allowed = apply and project.get("mode", "repair") == "repair"
        result = run_orchestrator(
            runner,
            config | project,
            workspace,
            pr,
            run_dir,
            changed_files_path,
            docs_manifest,
            skills_manifest,
            review_context,
            validation_evidence,
            repairs_allowed,
        )
        validate_docs_manifest(config | project, docs_manifest, candidate_domains)

        original_head = pr["headRefOid"]
        final_head = original_head
        if result["status"] in {"repaired", "repaired_blocked"}:
            if not repairs_allowed:
                raise ReviewFailure("orchestrator repaired files without controller authorization")
            repair_fingerprint = workspace_fingerprint(runner, workspace)
            run_project_commands(runner, workspace, setup_commands, [], validation_env)
            run_project_commands(
                runner,
                workspace,
                validation_commands,
                success_markers,
                validation_env,
                validation_attempts,
            )
            if workspace_fingerprint(runner, workspace) != repair_fingerprint:
                raise ReviewFailure("setup/validation changed the orchestrator repair")
            final_head = commit_and_push_repair(runner, workspace, pr)
            pr = fetch_pr_at_head(runner, repository, pr_number, final_head)
        elif result["status"] == "clean":
            assert_clean_workspace(runner, workspace, "clean orchestrator result")
        else:
            assert_clean_workspace(runner, workspace, "blocked orchestrator result")

        comment = format_review_comment(
            result,
            original_head=original_head,
            final_head=final_head,
            validation_commands=validation_commands,
        )
        upsert_review_comment(runner, repository, pr, comment)
        current = fetch_pr(runner, repository, pr_number)
        record_review_state(state_root, project_name, current, result["status"])

        summary = {
            "status": result["status"],
            "reviewed_head_sha": original_head,
            "final_head_sha": final_head,
            "repairs": result.get("repairs", []),
            "blocking_reasons": result.get("blocking_reasons", []),
            "url": pr["url"],
            "manual_merge": True,
        }
        write_summary(run_dir, summary)

        if result["status"] in {"blocked", "repaired_blocked"}:
            if project.get("telegram_notifications_enabled", False):
                try:
                    event_path = enqueue_notification(
                        state_root,
                        {
                            "version": 1,
                            "type": "pr_blocked",
                            "created_at": utc_now().isoformat(),
                            "project": project_name,
                            "repository": repository,
                            "pr_number": pr_number,
                            "title": pr.get("title", ""),
                            "url": pr["url"],
                            "head_sha": original_head,
                            "blockers": result.get("blocking_reasons", []),
                            "repairs_applied": bool(result.get("repairs")),
                            "findings": [
                                {
                                    "severity": "fixed",
                                    "title": item.get("title", "Verified repair"),
                                }
                                for item in result.get("repairs", [])
                                if isinstance(item, dict)
                            ],
                        },
                    )
                    if event_path is not None:
                        deliver_notification(event_path, Path(config["telegram_env"]), state_root)
                except (NotificationFailure, OSError, ValueError):
                    runner.log("Telegram blocker notification was queued for retry")
            return 2
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--apply", action="store_true", help="Allow verified repairs to be committed and pushed")
    parser.add_argument("--force", action="store_true", help="Review even when this exact PR state was already handled")
    args = parser.parse_args()
    try:
        return execute(args.config, args.project, args.pr, args.apply, args.force)
    except ReviewFailure as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
