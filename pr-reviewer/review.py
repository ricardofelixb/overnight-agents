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


class ResultContractFailure(ReviewFailure):
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
    config.setdefault(
        "simplifier_skill_path",
        "../pr-simplifier/skills/simplify-pr-implementation",
    )
    errors = validate_config(config, path)
    if errors:
        raise ReviewFailure("invalid configuration: " + "; ".join(errors))
    config_dir = path.resolve().parent
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
    github_head_sha: str | None = None,
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
        "reviewed_head_sha": pr["headRefOid"],
        "github_head_sha": github_head_sha or pr["headRefOid"],
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


def capture_ci_context(
    runner: Runner,
    config: dict[str, Any],
    repository: str,
    pr: dict[str, Any],
    run_dir: Path,
    github_head_sha: str | None = None,
) -> Path:
    fields = "bucket,completedAt,description,event,link,name,startedAt,state,workflow"
    result = runner.run(
        ["gh", "pr", "checks", str(pr["number"]), "--repo", repository, "--json", fields],
        check=False,
        log_output=False,
    )
    checks_error = ""
    try:
        checks = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        if result.returncode == 0:
            raise ReviewFailure("GitHub CI check metadata is invalid")
        checks = []
        checks_error = " ".join((result.stdout or "GitHub checks unavailable").split())[:2000]
    if not isinstance(checks, list):
        raise ReviewFailure("GitHub CI check metadata must be an array")
    failed_runs: dict[str, str] = {}
    for check in checks:
        if not isinstance(check, dict) or check.get("bucket") != "fail":
            continue
        match = re.search(r"/actions/runs/(\d+)", str(check.get("link", "")))
        if not match or match.group(1) in failed_runs:
            continue
        log = runner.run(
            ["gh", "run", "view", match.group(1), "--repo", repository, "--log-failed"],
            check=False,
            log_output=False,
        )
        failed_runs[match.group(1)] = (log.stdout or "")[:500_000]
    value = {
        "version": 1,
        "retrieved_at": utc_now().isoformat(),
        "repository": repository,
        "pr_number": pr["number"],
        "head_sha": pr["headRefOid"],
        "reviewed_head_sha": pr["headRefOid"],
        "github_head_sha": github_head_sha or pr["headRefOid"],
        "checks_command_exit_code": result.returncode,
        "checks_command_error": checks_error,
        "checks": checks,
        "failed_run_logs": failed_runs,
    }
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    if len(encoded) > int(config.get("max_ci_context_bytes", 1_000_000)):
        raise ReviewFailure("GitHub CI context exceeds the configured evidence limit")
    path = run_dir / "ci-context.json"
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


def assert_safe_changed_files(changed_files: list[str]) -> None:
    if any(
        Path(path).is_absolute()
        or Path(path).as_posix() == ".."
        or Path(path).as_posix().startswith("../")
        or any(ord(character) < 32 for character in path)
        for path in changed_files
    ):
        raise ReviewFailure("pull request contains an unsafe changed-file path")


def reject_policy_changes(changed_files: list[str], patterns: list[str]) -> None:
    protected = [path for path in changed_files if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)]
    if protected:
        raise ReviewFailure("PR changes trusted agent/review policy: " + ", ".join(sorted(protected)))


def reject_protected_agent_edits(changed_files: list[str], config: dict[str, Any]) -> None:
    patterns = [
        *config.get("protected_policy_patterns", []),
        *config.get("protected_agent_edit_patterns", []),
    ]
    protected = [path for path in changed_files if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)]
    if protected:
        raise ReviewFailure("agent changed protected files: " + ", ".join(sorted(protected)))


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


def capture_ai_files_snapshot(
    config: dict[str, Any],
    project_name: str,
    domains: list[str],
    run_dir: Path,
) -> Path | None:
    if "convex" not in domains:
        return None
    snapshot_root = (Path(config["state_root"]) / "ai-files" / project_name).resolve()
    manifest_path = snapshot_root / "manifest.json"
    manifest = load_json(manifest_path)
    if manifest.get("version") != 1 or manifest.get("project") != project_name:
        raise ReviewFailure("invalid Convex AI-files snapshot manifest")
    max_age = timedelta(days=int(config.get("ai_files_max_age_days", 8)))
    if utc_now() - parse_time(manifest.get("refreshed_at", "")) > max_age:
        raise ReviewFailure("Convex AI-files snapshot is stale")
    release = Path(manifest.get("release_path", "")).resolve()
    releases_root = (snapshot_root / "releases").resolve()
    if releases_root != release.parent or not release.is_dir():
        raise ReviewFailure("Convex AI-files snapshot release path is invalid")
    files = manifest.get("files")
    if not isinstance(files, dict) or "convex/_generated/ai/guidelines.md" not in files:
        raise ReviewFailure("Convex AI-files snapshot has no generated guidelines")
    actual_files = {
        path.relative_to(release).as_posix()
        for path in release.rglob("*")
        if path.is_file()
    }
    if actual_files != set(files):
        raise ReviewFailure("Convex AI-files snapshot file set does not match its manifest")
    for relative, metadata in files.items():
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or Path(relative).as_posix().startswith("../")
            or not isinstance(metadata, dict)
        ):
            raise ReviewFailure("Convex AI-files snapshot contains unsafe metadata")
        path = release / relative
        if path.is_symlink() or not path.is_file():
            raise ReviewFailure(f"Convex AI-files snapshot file is invalid: {relative}")
        content = path.read_bytes()
        if len(content) != metadata.get("bytes") or hashlib.sha256(content).hexdigest() != metadata.get("sha256"):
            raise ReviewFailure(f"Convex AI-files snapshot integrity mismatch: {relative}")
    captured = run_dir / "convex-ai-files-manifest.json"
    captured.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return captured


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


def simplification_prompt(
    *,
    skill_path: Path,
    pr: dict[str, Any],
    changed_files_path: Path,
    edits_allowed: bool,
) -> str:
    return f"""Use the PR implementation simplification skill at {skill_path / 'SKILL.md'}.

Run one complete simplification lifecycle for this exact human-authored pull request before its independent correctness/security review.

Immutable controller inputs:
- PR: {pr['url']}
- PR number: {pr['number']}
- base SHA: {pr['baseRefOid']}
- head SHA: {pr['headRefOid']}
- exact PR changed-files list: {changed_files_path}
- edits allowed: {str(edits_allowed).lower()}

Read the skill and its complete simplification protocol. Spawn the three named read-only specialist sub-agents concurrently and reconcile their evidence yourself. Inspect the exact PR diff and only its bounded implementation slice. Apply only high-confidence improvements that preserve behavior and public contracts; do not perform a broad repository audit or final correctness/security review.

If edits are allowed, edit the working tree directly but never commit or push. After edits, spawn a fresh read-only verifier with the raw original and final diffs. If edits are not allowed, leave the working tree clean and report a blocker when a worthwhile simplification would require an edit.

The controller will run the complete project validation gate and owns commits, pushes, exact-head state, and the downstream reviewer. Never modify Git configuration, history, remotes, hooks, credentials, protected policy, dependency manifests/locks, migrations, CI configuration, or generated files. Return only schema-conforming JSON.
"""


def simplification_correction_prompt(
    *,
    skill_path: Path,
    pr: dict[str, Any],
    changed_files_path: Path,
    prior_result: Path,
    validation_evidence: Path,
) -> str:
    return f"""Use the PR implementation simplification skill at {skill_path / 'SKILL.md'}.

Resume the existing simplification for this exact pull request. This is a bounded validation-correction cycle, not a new simplification pass.

Immutable controller inputs:
- PR: {pr['url']}
- PR number: {pr['number']}
- base SHA: {pr['baseRefOid']}
- head SHA: {pr['headRefOid']}
- exact original PR changed-files list: {changed_files_path}
- previous contract-valid simplification result: {prior_result}
- trusted controller validation failure evidence: {validation_evidence}

The working tree contains the exact uncommitted simplification described by the previous result. Do not rerun the three specialist reviews or broaden scope. Repair the validation failure at its cause, or completely revert the improvement disproven by the evidence. Never weaken tests, types, lint, validation, authorization, error handling, dependency policy, or CI configuration.

Run focused checks and spawn one fresh read-only verifier with the raw original PR diff, current working-tree diff, and validation evidence. Return a complete updated result describing the final working tree exactly. Never commit, push, comment, or change Git configuration or history.
"""


def run_simplifier_codex(
    runner: Runner,
    config: dict[str, Any],
    workspace: Path,
    pr: dict[str, Any],
    output: Path,
    prompt: str,
    *,
    edits_allowed: bool,
) -> None:
    skill_path = Path(config["simplifier_skill_path"])
    schema = skill_path / "references" / "orchestrator-result.schema.json"
    sandbox = "workspace-write" if edits_allowed else "read-only"
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
    if edits_allowed:
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
            prompt,
        ]
    )
    git_config_before = (workspace / ".git" / "config").read_bytes()
    runner.run(command, env=codex_environment(), log_output=False)
    if (workspace / ".git" / "config").read_bytes() != git_config_before:
        raise ReviewFailure("simplifier changed local Git configuration")
    if runner.run(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip() != pr["headRefOid"]:
        raise ReviewFailure("simplifier changed commit history; only the controller may commit")


def validate_simplifier_result(
    runner: Runner,
    config: dict[str, Any],
    workspace: Path,
    pr: dict[str, Any],
    output: Path,
    changed_files_path: Path,
    run_dir: Path,
) -> dict[str, Any]:
    actual_changes = sorted(workspace_changes(runner, workspace))
    actual_changes_path = run_dir / f"actual-changes-{output.stem}.txt"
    actual_changes_path.write_text("".join(f"{path}\n" for path in actual_changes))
    runner.run(
        [
            sys.executable,
            str(Path(config["simplifier_skill_path"]) / "scripts" / "result_contract.py"),
            str(output),
            "--expected-base",
            pr["baseRefOid"],
            "--expected-head",
            pr["headRefOid"],
            "--pr-changed-files",
            str(changed_files_path),
            "--actual-changed-files",
            str(actual_changes_path),
        ]
    )
    result = load_json(output)
    if actual_changes:
        reject_protected_agent_edits(actual_changes, config)
    return result


def run_simplifier_orchestrator(
    runner: Runner,
    config: dict[str, Any],
    workspace: Path,
    pr: dict[str, Any],
    run_dir: Path,
    changed_files_path: Path,
    edits_allowed: bool,
) -> tuple[dict[str, Any], Path]:
    output = run_dir / "simplifier-result.json"
    skill_path = Path(config["simplifier_skill_path"])
    run_simplifier_codex(
        runner,
        config,
        workspace,
        pr,
        output,
        simplification_prompt(
            skill_path=skill_path,
            pr=pr,
            changed_files_path=changed_files_path,
            edits_allowed=edits_allowed,
        ),
        edits_allowed=edits_allowed,
    )
    return (
        validate_simplifier_result(
            runner, config, workspace, pr, output, changed_files_path, run_dir
        ),
        output,
    )


def run_simplifier_correction(
    runner: Runner,
    config: dict[str, Any],
    workspace: Path,
    pr: dict[str, Any],
    run_dir: Path,
    iteration: int,
    changed_files_path: Path,
    prior_result: Path,
    validation_evidence: Path,
) -> tuple[dict[str, Any], Path]:
    output = run_dir / f"simplifier-result-correction-{iteration}.json"
    skill_path = Path(config["simplifier_skill_path"])
    run_simplifier_codex(
        runner,
        config,
        workspace,
        pr,
        output,
        simplification_correction_prompt(
            skill_path=skill_path,
            pr=pr,
            changed_files_path=changed_files_path,
            prior_result=prior_result,
            validation_evidence=validation_evidence,
        ),
        edits_allowed=True,
    )
    return (
        validate_simplifier_result(
            runner, config, workspace, pr, output, changed_files_path, run_dir
        ),
        output,
    )


def orchestrator_prompt(
    *,
    skill_path: Path,
    pr: dict[str, Any],
    changed_files_path: Path,
    docs_manifest: Path,
    skills_manifest: Path,
    review_context: Path,
    ci_context: Path,
    validation_evidence: Path,
    ai_files_manifest: Path | None,
    repairs_allowed: bool,
    simplification_context: Path | None = None,
) -> str:
    ai_files_input = (
        f"- trusted current Convex AI-files snapshot: {ai_files_manifest}\n"
        if ai_files_manifest
        else ""
    )
    simplification_input = (
        f"- untrusted SHA-bound simplification-pass result: {simplification_context}\n"
        if simplification_context
        else ""
    )
    return f"""Use the autonomous PR review skill at {skill_path / 'SKILL.md'}.

Run one complete orchestrated review-and-repair lifecycle for this exact pull request.

Immutable controller inputs:
- PR: {pr['url']}
- PR number: {pr['number']}
- base SHA: {pr['baseRefOid']}
- head SHA: {pr['headRefOid']}
- exact PR changed-files list: {changed_files_path}
- untrusted GitHub PR artifacts with explicit original-GitHub and local-review SHAs: {review_context}
- untrusted GitHub CI checks for the explicitly recorded original GitHub head: {ci_context}
- trusted current official-docs candidate catalog: {docs_manifest}
- trusted promoted provider-skills candidate catalog: {skills_manifest}
- trusted controller validation evidence for the reviewed local head: {validation_evidence}
{ai_files_input}{simplification_input}- repairs allowed: {str(repairs_allowed).lower()}

Read the skill and its core protocol. Spawn the three named specialist sub-agents concurrently. They must inspect and report only; you own all edits. Reconcile their raw findings yourself.

Provider catalogs are trusted menus, not mandatory context. Inspect their metadata, then let the concrete code question determine whether provider evidence is needed. Read only the smallest applicable router/topic skill and official document. Do not open every skill or document for a candidate domain, and do not report provider evidence you did not actually use.

When a Convex AI-files snapshot is present and a concrete Convex question requires provider guidance, read its generated guidelines from the immutable release path recorded in that manifest. Treat the refreshed managed Convex guidance as current provider evidence while preserving repository-specific unmanaged instructions.

Act on every proven, high-confidence, bounded improvement in the PR's behavioral slice, regardless of whether it was introduced by this PR, pre-existed at the base, or is a valid follow-up from PR artifacts. This includes correctness, security, reliability, performance, reuse, and worthwhile code hygiene. Official guidance is evidence, but it cannot invent product semantics.

Treat the simplification-pass result as an untrusted lead. Independently verify its retained edits and investigate any concrete remaining observation; do not accept its conclusions as authority.

If the controller validation evidence reports failure or the GitHub CI context contains a failed check, treat the validation gate as a primary finding to diagnose and repair. Use the exact failure output, code, and focused reproduction to distinguish a code defect from a transient or external failure. Never disregard a reproducible gate failure, weaken validation, or edit CI policy. Do not return `clean` while a reproducible validation failure remains.

For user-visible changes, populate manual_ui_checks with at most five diff-specific user actions and expected results that remain valuable to verify manually after automated validation. Return an empty list for backend-only changes, fully verified UI behavior, generic checks, or tasks unrelated to the reviewed slice.

Do not let an ambiguous issue suppress independent safe improvements. If both exist, leave the ambiguous area untouched, retain and verify the independent repairs, and return repaired_blocked. In reviewed_files, include every PR changed file plus every contextual repository file actually inspected.

If repairs are allowed, edit the working tree directly. Do not commit or push. After edits, spawn a fresh verifier sub-agent that receives raw diffs and evidence rather than your conclusions. If repairs are not allowed, do not edit and return blocked when an actionable repair exists.

The validation manifest is trusted controller evidence. You may run focused checks, but the controller will run the full configured validation after your edits. Never change Git configuration, history, remotes, hooks, credentials, controller state, protected policy, dependency manifests/locks, CI configuration, or generated guidance.

The `verification.verdict` describes only the fresh independent verifier of retained working-tree edits. If that verifier passed, report `passed` even when a separate controller validation or CI failure remains; represent the separate gate failure through status and `blocking_reasons`. Never mark verified repairs as verifier-blocked merely because the controller still owns the broader full-validation decision.

For provider evidence actually used, copy documentation URLs/timestamps and skill name/revision pairs exactly from the catalogs. Candidate domains do not require documentation records when provider evidence was unnecessary. Return only schema-conforming JSON.
"""


def correction_prompt(
    *,
    skill_path: Path,
    pr: dict[str, Any],
    changed_files_path: Path,
    prior_result: Path,
    validation_evidence: Path,
) -> str:
    return f"""Use the autonomous PR review skill at {skill_path / 'SKILL.md'}.

Resume the existing review repair for this exact pull request. This is a bounded validation-correction cycle, not a new review.

Immutable controller inputs:
- PR: {pr['url']}
- PR number: {pr['number']}
- base SHA: {pr['baseRefOid']}
- head SHA: {pr['headRefOid']}
- exact original PR changed-files list: {changed_files_path}
- previous contract-valid orchestrator result: {prior_result}
- trusted controller validation failure evidence for the current working-tree repair: {validation_evidence}

The working tree contains the exact uncommitted repair described by the previous result. Read the correction protocol in the skill and fix the validation failure at its cause. Do not restart the three specialist reviews and do not broaden the review.

Preserve the proven repair unless the validation evidence disproves it. Never weaken tests, types, lint, validation, authorization, error handling, dependency policy, or CI configuration. Run the smallest focused reproduction and relevant checks after editing.

After correcting the repair, spawn one fresh read-only verifier sub-agent with the raw original PR diff, current working-tree diff, and validation evidence. Reconcile any proven verifier finding yourself.

Return a complete updated JSON result using the original result schema. It must describe the final working tree exactly and retain still-valid review evidence. If the proposed repair is unnecessary or unsafe, revert it completely and return `clean` only when the original PR has no actionable issue; otherwise return `blocked` with a precise reason. Never commit, push, comment, or change Git configuration or history.
"""


def result_contract_correction_prompt(
    *,
    skill_path: Path,
    pr: dict[str, Any],
    changed_files_path: Path,
    invalid_result: Path,
    contract_errors: Path,
) -> str:
    return f"""Use the autonomous PR review skill at {skill_path / 'SKILL.md'}.

Correct the structured result for this exact completed review. This is a bounded result-contract correction, not a new review.

Immutable controller inputs:
- PR: {pr['url']}
- PR number: {pr['number']}
- base SHA: {pr['baseRefOid']}
- head SHA: {pr['headRefOid']}
- exact original PR changed-files list: {changed_files_path}
- previous schema-valid but contract-invalid result: {invalid_result}
- deterministic contract errors: {contract_errors}

Do not restart specialist reviews or broaden the investigation. Reconcile the prior JSON with the actual working tree and the contract errors. Preserve already proven repairs and evidence. The `verification.verdict` reports only the fresh verifier's verdict on retained edits; controller validation and CI are separate gates represented through status and blocking reasons.

Do not edit code unless the contract errors prove an unverified repair cannot be retained safely; in that case, revert only that unverified repair and report the resulting working tree exactly. Never commit, push, comment, or change Git configuration or history.

Return one complete schema-conforming JSON object that accurately describes the final working tree.
"""


def run_codex_result(
    runner: Runner,
    config: dict[str, Any],
    workspace: Path,
    pr: dict[str, Any],
    output: Path,
    prompt: str,
    *,
    repairs_allowed: bool,
) -> None:
    skill_path = Path(config["skill_path"])
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
            prompt,
        ]
    )
    git_config_before = (workspace / ".git" / "config").read_bytes()
    runner.run(command, env=codex_environment(), log_output=False)
    if (workspace / ".git" / "config").read_bytes() != git_config_before:
        raise ReviewFailure("orchestrator changed local Git configuration")
    if runner.run(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip() != pr["headRefOid"]:
        raise ReviewFailure("orchestrator changed commit history; only the controller may commit")


def validate_orchestrator_result(
    runner: Runner,
    config: dict[str, Any],
    workspace: Path,
    pr: dict[str, Any],
    output: Path,
    changed_files_path: Path,
    docs_manifest: Path,
    skills_manifest: Path,
) -> dict[str, Any]:
    skill_path = Path(config["skill_path"])
    contract = runner.run(
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
        ],
        check=False,
    )
    if contract.returncode != 0:
        detail = (contract.stdout or "result contract validation failed").strip()
        raise ResultContractFailure(detail[:20_000])
    result = load_json(output)
    actual = workspace_changes(runner, workspace)
    reported = set(result.get("changed_files", []))
    if actual != reported:
        raise ReviewFailure("orchestrator changed-file report does not match the working tree")
    if actual:
        reject_protected_agent_edits(sorted(actual), config)
    return result


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
    ci_context: Path,
    validation_evidence: Path,
    ai_files_manifest: Path | None,
    simplification_context: Path | None,
    repairs_allowed: bool,
) -> dict[str, Any]:
    skill_path = Path(config["skill_path"])
    canonical_output = run_dir / "orchestrator-result.json"
    output = canonical_output
    run_codex_result(
        runner,
        config,
        workspace,
        pr,
        output,
        orchestrator_prompt(
            skill_path=skill_path,
            pr=pr,
            changed_files_path=changed_files_path,
            docs_manifest=docs_manifest,
            skills_manifest=skills_manifest,
            review_context=review_context,
            ci_context=ci_context,
            validation_evidence=validation_evidence,
            ai_files_manifest=ai_files_manifest,
            repairs_allowed=repairs_allowed,
            simplification_context=simplification_context,
        ),
        repairs_allowed=repairs_allowed,
    )
    correction_cycles = int(config.get("result_contract_correction_cycles", 1))
    for iteration in range(correction_cycles + 1):
        try:
            result = validate_orchestrator_result(
                runner,
                config,
                workspace,
                pr,
                output,
                changed_files_path,
                docs_manifest,
                skills_manifest,
            )
            if output != canonical_output:
                canonical_output.write_bytes(output.read_bytes())
            return result
        except ResultContractFailure as error:
            if iteration >= correction_cycles:
                raise
            runner.log(
                "Orchestrator result failed its semantic contract; starting bounded "
                f"result correction {iteration + 1}/{correction_cycles}"
            )
            errors_path = run_dir / f"orchestrator-result-contract-errors-{iteration + 1}.txt"
            errors_path.write_text(str(error) + "\n")
            prior_output = output
            output = run_dir / f"orchestrator-result-contract-correction-{iteration + 1}.json"
            run_codex_result(
                runner,
                config,
                workspace,
                pr,
                output,
                result_contract_correction_prompt(
                    skill_path=skill_path,
                    pr=pr,
                    changed_files_path=changed_files_path,
                    invalid_result=prior_output,
                    contract_errors=errors_path,
                ),
                repairs_allowed=repairs_allowed,
            )
    raise AssertionError("unreachable result contract correction loop")


def run_correction_orchestrator(
    runner: Runner,
    config: dict[str, Any],
    workspace: Path,
    pr: dict[str, Any],
    run_dir: Path,
    iteration: int,
    changed_files_path: Path,
    docs_manifest: Path,
    skills_manifest: Path,
    prior_result: Path,
    validation_evidence: Path,
) -> tuple[dict[str, Any], Path]:
    skill_path = Path(config["skill_path"])
    output = run_dir / f"orchestrator-result-correction-{iteration}.json"
    run_codex_result(
        runner,
        config,
        workspace,
        pr,
        output,
        correction_prompt(
            skill_path=skill_path,
            pr=pr,
            changed_files_path=changed_files_path,
            prior_result=prior_result,
            validation_evidence=validation_evidence,
        ),
        repairs_allowed=True,
    )
    result = validate_orchestrator_result(
        runner,
        config,
        workspace,
        pr,
        output,
        changed_files_path,
        docs_manifest,
        skills_manifest,
    )
    return result, output


def workspace_changes(runner: Runner, workspace: Path) -> set[str]:
    tracked = runner.run(["git", "diff", "--name-only", "HEAD"], cwd=workspace).stdout.splitlines()
    untracked = runner.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=workspace).stdout.splitlines()
    return {path for path in tracked + untracked if path}


def workspace_fingerprint(runner: Runner, workspace: Path) -> dict[str, str]:
    fingerprint: dict[str, str] = {}
    for relative in sorted(workspace_changes(runner, workspace)):
        path = workspace / relative
        if path.is_symlink():
            raise ReviewFailure(f"workspace change is a symlink: {relative}")
        fingerprint[relative] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "<deleted-or-non-file>"
    return fingerprint


def assert_clean_workspace(runner: Runner, workspace: Path, context: str) -> None:
    changed = workspace_changes(runner, workspace)
    if changed:
        raise ReviewFailure(f"{context} mutated the review checkout: {', '.join(sorted(changed))}")


def collect_project_commands(
    runner: Runner,
    workspace: Path,
    commands: list[list[str]],
    markers: list[str],
    environment_overrides: dict[str, str] | None = None,
    attempts: int = 1,
) -> tuple[str, bool, str]:
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
            return "".join(outputs), True, ""
        if attempt < attempts:
            runner.log(f"Validation attempt {attempt}/{attempts} failed; retrying the full configured validation")
    return "".join(outputs), False, last_failure


def run_project_commands(
    runner: Runner,
    workspace: Path,
    commands: list[list[str]],
    markers: list[str],
    environment_overrides: dict[str, str] | None = None,
    attempts: int = 1,
) -> str:
    output, passed, failure = collect_project_commands(
        runner,
        workspace,
        commands,
        markers,
        environment_overrides,
        attempts,
    )
    if not passed:
        raise ReviewFailure(failure)
    return output


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
    status: str = "passed",
    failure: str = "",
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
        "status": status,
        "failure": failure,
    }
    path = run_dir / f"validation-evidence-{iteration}.json"
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return path


def validate_repair_with_corrections(
    runner: Runner,
    config: dict[str, Any],
    workspace: Path,
    pr: dict[str, Any],
    run_dir: Path,
    changed_files_path: Path,
    docs_manifest: Path,
    skills_manifest: Path,
    setup_commands: list[list[str]],
    validation_commands: list[list[str]],
    success_markers: list[str],
    validation_env: dict[str, str],
    result: dict[str, Any],
) -> dict[str, Any]:
    correction_cycles = int(config.get("validation_correction_cycles", 2))
    validation_attempts = int(config.get("validation_attempts", 1))
    result_path = run_dir / "orchestrator-result.json"
    for iteration in range(correction_cycles + 1):
        repair_fingerprint = workspace_fingerprint(runner, workspace)
        setup_output = run_project_commands(runner, workspace, setup_commands, [], validation_env)
        validation_output, passed, failure = collect_project_commands(
            runner,
            workspace,
            validation_commands,
            success_markers,
            validation_env,
            attempts=validation_attempts,
        )
        if workspace_fingerprint(runner, workspace) != repair_fingerprint:
            raise ReviewFailure("setup/validation changed the orchestrator repair")
        evidence = write_validation_evidence(
            run_dir,
            iteration + 1,
            pr,
            setup_commands,
            validation_commands,
            success_markers,
            validation_env,
            setup_output,
            validation_output,
            "passed" if passed else "failed",
            failure,
        )
        if passed:
            return result
        if iteration >= correction_cycles:
            raise ReviewFailure(
                f"post-repair validation still fails after {correction_cycles} correction cycle(s): {failure}"
            )
        runner.log(
            f"Post-repair validation failed; starting focused correction cycle {iteration + 1}/{correction_cycles}"
        )
        result, result_path = run_correction_orchestrator(
            runner,
            config,
            workspace,
            pr,
            run_dir,
            iteration + 1,
            changed_files_path,
            docs_manifest,
            skills_manifest,
            result_path,
            evidence,
        )
    raise AssertionError("unreachable validation correction loop")


def commit_repair(runner: Runner, workspace: Path, pr: dict[str, Any]) -> str:
    runner.run(["git", "diff", "--check"], cwd=workspace)
    runner.run(["git", "add", "-A"], cwd=workspace)
    staged = runner.run(["git", "diff", "--cached", "--quiet"], cwd=workspace, check=False)
    if staged.returncode == 0:
        raise ReviewFailure("reviewer reported repairs but produced no staged diff")
    runner.run(["git", "commit", "-m", f"fix: address autonomous review findings for PR #{pr['number']}"], cwd=workspace)
    return runner.run(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip()


def push_final_head(
    runner: Runner,
    workspace: Path,
    pr: dict[str, Any],
    *,
    expected_remote_head: str,
) -> str:
    branch = pr["headRefName"]
    remote_ref = f"refs/heads/{branch}"
    descendant = runner.run(
        ["git", "merge-base", "--is-ancestor", expected_remote_head, "HEAD"],
        cwd=workspace,
        check=False,
    )
    if descendant.returncode != 0:
        raise ReviewFailure("reviewed local head is not a descendant of the original PR head")
    remote = runner.run(
        ["git", "ls-remote", "--heads", "origin", remote_ref],
        cwd=workspace,
        log_output=False,
    ).stdout.splitlines()
    if len(remote) != 1 or remote[0].split(maxsplit=1)[0] != expected_remote_head:
        raise ReviewFailure("PR head changed during the atomic review; refusing to push")
    final_head = runner.run(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip()
    runner.run(
        [
            "git",
            "push",
            f"--force-with-lease={remote_ref}:{expected_remote_head}",
            "origin",
            f"HEAD:{remote_ref}",
        ],
        cwd=workspace,
    )
    return final_head


def _comment_text(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())[:limit]
    return text.replace("@", "@\u200b")


def format_review_comment(
    result: dict[str, Any],
    *,
    original_head: str,
    final_head: str,
    validation_commands: list[list[str]],
    simplification: dict[str, Any] | None = None,
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

    if simplification and simplification.get("reason") not in {"already_simplified_automation", "disabled"}:
        simplification_status = simplification.get("status")
        lines.extend(["", "### Implementation simplification"])
        if simplification_status in {"simplified", "simplified_blocked"}:
            count = len(simplification.get("improvements", []))
            lines.append(f"- Applied and validated {count} behavior-preserving improvement(s) before review.")
            if simplification.get("input_head_sha") != simplification.get("simplification_head_sha"):
                lines.append(
                    "- Simplification commit: "
                    f"`{str(simplification.get('simplification_head_sha', ''))[:12]}`"
                )
        elif simplification_status == "clean":
            lines.append("- The first pass found no worthwhile behavior-preserving simplification.")
        elif simplification_status == "deferred":
            lines.append("- The original validation gate was red, so simplification was deferred to the repair review.")
        elif simplification_status == "blocked":
            lines.append("- The first pass retained no edits and handed its ambiguity to the correctness review.")

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

    manual_ui_checks = [_comment_text(item, 300) for item in result.get("manual_ui_checks", []) if item]
    if manual_ui_checks:
        lines.extend(["", "### Manual UI sanity checks"])
        lines.extend(f"- [ ] {item}" for item in manual_ui_checks[:5])

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


def simplification_state_path(state_root: Path, project_name: str, pr_number: int) -> Path:
    return state_root / "simplification-state" / project_name / f"{pr_number}.json"


def current_simplification_state(
    state_root: Path,
    project_name: str,
    pr: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        state = load_json(simplification_state_path(state_root, project_name, pr["number"]))
    except ReviewFailure:
        return None
    return state if state.get("version") == 1 and state.get("head_sha") == pr.get("headRefOid") else None


def record_simplification_state(
    state_root: Path,
    project_name: str,
    pr: dict[str, Any],
    *,
    input_head_sha: str,
    result: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    state = {
        "version": 1,
        "project": project_name,
        "pr_number": pr["number"],
        "input_head_sha": input_head_sha,
        "head_sha": pr["headRefOid"],
        "simplification_head_sha": pr["headRefOid"],
        "status": result.get("status", "skipped"),
        "reason": reason,
        "summary": result.get("summary", ""),
        "improvements": result.get("improvements", []),
        "remaining_observations": result.get("remaining_observations", []),
        "blocking_reasons": result.get("blocking_reasons", []),
        "recorded_at": utc_now().isoformat(),
    }
    path = simplification_state_path(state_root, project_name, pr["number"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)
    return state


def carry_simplification_state_to_head(
    state_root: Path,
    project_name: str,
    pr: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    carried = dict(state)
    carried["head_sha"] = pr["headRefOid"]
    carried["finalized_by"] = "reviewer_output"
    carried["recorded_at"] = utc_now().isoformat()
    path = simplification_state_path(state_root, project_name, pr["number"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(carried, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)
    return carried


def simplification_is_skipped(pr: dict[str, Any], project: dict[str, Any]) -> str | None:
    if not project.get("simplify_human_prs", True):
        return "disabled"
    head = str(pr.get("headRefName", ""))
    patterns = project.get(
        "simplification_skip_head_patterns",
        ["code-simplify/*", "code-organize/*"],
    )
    if any(fnmatch.fnmatchcase(head, pattern) for pattern in patterns):
        return "already_simplified_automation"
    return None


def validate_simplification_with_corrections(
    runner: Runner,
    config: dict[str, Any],
    workspace: Path,
    pr: dict[str, Any],
    run_dir: Path,
    changed_files_path: Path,
    setup_commands: list[list[str]],
    validation_commands: list[list[str]],
    success_markers: list[str],
    validation_env: dict[str, str],
    result: dict[str, Any],
    result_path: Path,
) -> tuple[dict[str, Any], Path]:
    correction_cycles = int(config.get("simplification_correction_cycles", 2))
    validation_attempts = int(config.get("validation_attempts", 1))
    for iteration in range(correction_cycles + 1):
        fingerprint = workspace_fingerprint(runner, workspace)
        setup_output = run_project_commands(runner, workspace, setup_commands, [], validation_env)
        validation_output, passed, failure = collect_project_commands(
            runner,
            workspace,
            validation_commands,
            success_markers,
            validation_env,
            attempts=validation_attempts,
        )
        if workspace_fingerprint(runner, workspace) != fingerprint:
            raise ReviewFailure("setup/validation changed the simplifier working tree")
        evidence = write_validation_evidence(
            run_dir,
            100 + iteration,
            pr,
            setup_commands,
            validation_commands,
            success_markers,
            validation_env,
            setup_output,
            validation_output,
            "passed" if passed else "failed",
            failure,
        )
        if passed:
            return result, evidence
        if iteration >= correction_cycles:
            raise ReviewFailure(
                "post-simplification validation still fails after "
                f"{correction_cycles} correction cycle(s): {failure}"
            )
        runner.log(
            "Post-simplification validation failed; starting focused correction cycle "
            f"{iteration + 1}/{correction_cycles}"
        )
        result, result_path = run_simplifier_correction(
            runner,
            config,
            workspace,
            pr,
            run_dir,
            iteration + 1,
            changed_files_path,
            result_path,
            evidence,
        )
    raise AssertionError("unreachable simplification correction loop")


def commit_simplification(runner: Runner, workspace: Path, pr: dict[str, Any]) -> str:
    runner.run(["git", "diff", "--check"], cwd=workspace)
    runner.run(["git", "add", "-A"], cwd=workspace)
    staged = runner.run(["git", "diff", "--cached", "--quiet"], cwd=workspace, check=False)
    if staged.returncode == 0:
        raise ReviewFailure("simplifier reported changes but produced no staged diff")
    runner.run(
        ["git", "commit", "-m", f"refactor: simplify implementation for PR #{pr['number']}"],
        cwd=workspace,
    )
    return runner.run(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip()


def bind_validation_evidence_to_checkpoint(
    runner: Runner,
    workspace: Path,
    evidence_path: Path,
    *,
    original_head: str,
    checkpoint_head: str,
    validated_fingerprint: dict[str, str],
    output: Path,
) -> Path:
    evidence = load_json(evidence_path)
    if evidence.get("head_sha") != original_head or evidence.get("status") != "passed":
        raise ReviewFailure("only passed original-head validation can bind to a local checkpoint")
    for relative, expected_hash in validated_fingerprint.items():
        path = workspace / relative
        if path.is_symlink():
            raise ReviewFailure(f"local checkpoint change is a symlink: {relative}")
        actual_hash = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else "<deleted-or-non-file>"
        )
        if actual_hash != expected_hash:
            raise ReviewFailure("local checkpoint content differs from the validated simplification")
    actual_head = runner.run(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip()
    if actual_head != checkpoint_head:
        raise ReviewFailure("local checkpoint head changed before validation evidence was bound")
    evidence["source_head_sha"] = original_head
    evidence["head_sha"] = checkpoint_head
    evidence["checkpoint_tree_sha"] = runner.run(
        ["git", "rev-parse", f"{checkpoint_head}^{{tree}}"], cwd=workspace
    ).stdout.strip()
    evidence["validated_worktree_fingerprint"] = validated_fingerprint
    evidence["bound_at"] = utc_now().isoformat()
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    return output


def run_simplification_phase(
    runner: Runner,
    config: dict[str, Any],
    project_name: str,
    project: dict[str, Any],
    workspace: Path,
    pr: dict[str, Any],
    run_dir: Path,
    *,
    apply: bool,
) -> tuple[dict[str, Any], dict[str, Any], Path | None]:
    state_root = Path(config["state_root"])
    existing = current_simplification_state(state_root, project_name, pr)
    if existing is not None:
        runner.log(f"Skipping simplification for {pr['headRefOid'][:12]}; exact head already handled")
        return pr, existing, None

    skip_reason = simplification_is_skipped(pr, project)
    if skip_reason:
        state = record_simplification_state(
            state_root,
            project_name,
            pr,
            input_head_sha=pr["headRefOid"],
            result={"status": "skipped", "summary": "PR does not require a human-PR simplification pass."},
            reason=skip_reason,
        )
        runner.log(f"Skipping simplification for {pr['headRefName']}: {skip_reason}")
        return pr, state, None

    phase_dir = run_dir / "simplification"
    phase_dir.mkdir(parents=True, exist_ok=False)
    changed_files, diff = changed_files_and_diff(runner, workspace, pr["baseRefOid"], pr["headRefOid"])
    if len(changed_files) > int(project.get("max_changed_files", 200)):
        raise ReviewFailure("pull request exceeds the simplifier changed-file limit")
    if len(diff.encode()) > int(project.get("max_diff_bytes", 1_500_000)):
        raise ReviewFailure("pull request exceeds the simplifier diff-size limit")
    assert_safe_changed_files(changed_files)
    reject_policy_changes(changed_files, project.get("protected_policy_patterns", []))
    changed_files_path = phase_dir / "changed-files.txt"
    changed_files_path.write_text("".join(f"{path}\n" for path in changed_files))

    setup_commands = project.get("setup_commands", [])
    validation_commands = project["validation_commands"]
    success_markers = project.get("validation_success_markers", [])
    validation_env = project.get("validation_environment", {})
    validation_attempts = int(project.get("validation_attempts", 1))
    setup_output = run_project_commands(runner, workspace, setup_commands, [], validation_env)
    validation_output, baseline_passed, baseline_failure = collect_project_commands(
        runner,
        workspace,
        validation_commands,
        success_markers,
        validation_env,
        validation_attempts,
    )
    assert_clean_workspace(runner, workspace, "simplifier baseline validation")
    phase_validation_evidence = write_validation_evidence(
        phase_dir,
        0,
        pr,
        setup_commands,
        validation_commands,
        success_markers,
        validation_env,
        setup_output,
        validation_output,
        "passed" if baseline_passed else "failed",
        baseline_failure,
    )
    if not baseline_passed:
        state = record_simplification_state(
            state_root,
            project_name,
            pr,
            input_head_sha=pr["headRefOid"],
            result={
                "status": "deferred",
                "summary": "The initial validation gate was red, so simplification was deferred to preserve diagnostic clarity.",
                "remaining_observations": [baseline_failure],
            },
            reason="baseline_validation_failed",
        )
        runner.log("Deferring simplification because the original PR validation gate is red")
        return pr, state, phase_validation_evidence

    edits_allowed = apply and project.get("mode", "repair") == "repair"
    result, result_path = run_simplifier_orchestrator(
        runner,
        config | project,
        workspace,
        pr,
        phase_dir,
        changed_files_path,
        edits_allowed,
    )
    original_head = pr["headRefOid"]
    if result["status"] in {"simplified", "simplified_blocked"}:
        if not edits_allowed:
            raise ReviewFailure("simplifier edited files without controller authorization")
        result, phase_validation_evidence = validate_simplification_with_corrections(
            runner,
            config | project,
            workspace,
            pr,
            phase_dir,
            changed_files_path,
            setup_commands,
            validation_commands,
            success_markers,
            validation_env,
            result,
            result_path,
        )
    else:
        assert_clean_workspace(runner, workspace, "non-editing simplifier result")

    if result["status"] in {"simplified", "simplified_blocked"}:
        validated_fingerprint = workspace_fingerprint(runner, workspace)
        local_head = commit_simplification(runner, workspace, pr)
        phase_validation_evidence = bind_validation_evidence_to_checkpoint(
            runner,
            workspace,
            phase_validation_evidence,
            original_head=original_head,
            checkpoint_head=local_head,
            validated_fingerprint=validated_fingerprint,
            output=phase_dir / "validation-evidence-local-checkpoint.json",
        )
        pr = dict(pr)
        pr["headRefOid"] = local_head
        reason = "local_simplification_checkpoint"
    else:
        reason = "simplification_complete"
    state = record_simplification_state(
        state_root,
        project_name,
        pr,
        input_head_sha=original_head,
        result=result,
        reason=reason,
    )
    return pr, state, phase_validation_evidence


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
        remote_input_head = pr["headRefOid"]
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
        pr, simplification_state, phase_validation_evidence = run_simplification_phase(
            runner,
            config,
            project_name,
            project,
            workspace,
            pr,
            run_dir,
            apply=apply,
        )
        local_descendant = runner.run(
            ["git", "merge-base", "--is-ancestor", remote_input_head, pr["headRefOid"]],
            cwd=workspace,
            check=False,
        )
        if local_descendant.returncode != 0:
            raise ReviewFailure("simplification checkpoint is not based on the original PR head")
        simplification_context = run_dir / "simplification-context.json"
        simplification_context.write_text(
            json.dumps(simplification_state, indent=2, sort_keys=True) + "\n"
        )
        changed_files, diff = changed_files_and_diff(runner, workspace, pr["baseRefOid"], pr["headRefOid"])
        if len(changed_files) > int(project.get("max_changed_files", 200)):
            raise ReviewFailure("pull request exceeds the configured changed-file limit")
        if len(diff.encode()) > int(project.get("max_diff_bytes", 1_500_000)):
            raise ReviewFailure("pull request exceeds the configured diff-size limit")
        assert_safe_changed_files(changed_files)
        changed_files_path = run_dir / "changed-files.txt"
        changed_files_path.write_text("\n".join(changed_files) + "\n")
        reject_policy_changes(changed_files, project.get("protected_policy_patterns", []))

        candidate_domains = detect_domains(changed_files, diff)
        ai_files_manifest = capture_ai_files_snapshot(
            config | project,
            project_name,
            candidate_domains,
            run_dir,
        )
        skills_manifest = run_dir / "skills-manifest.json"
        skills_manifest.write_text(
            json.dumps(validate_skill_lock(config | project, candidate_domains), indent=2, sort_keys=True) + "\n"
        )
        docs_manifest = refresh_docs(runner, config | project, candidate_domains, run_dir, 0)
        validate_docs_manifest(config | project, docs_manifest, candidate_domains)
        controller_login = runner.run(["gh", "api", "user", "--jq", ".login"]).stdout.strip()
        review_context = capture_review_context(
            runner,
            config | project,
            repository,
            pr,
            run_dir,
            0,
            controller_login,
            github_head_sha=remote_input_head,
        )
        ci_context = capture_ci_context(
            runner,
            config | project,
            repository,
            pr,
            run_dir,
            github_head_sha=remote_input_head,
        )

        setup_commands = project.get("setup_commands", [])
        validation_commands = project["validation_commands"]
        success_markers = project.get("validation_success_markers", [])
        validation_env = project.get("validation_environment", {})
        validation_attempts = int(project.get("validation_attempts", 1))
        if phase_validation_evidence is not None:
            phase_evidence = load_json(phase_validation_evidence)
            if (
                phase_evidence.get("base_sha") != pr["baseRefOid"]
                or phase_evidence.get("head_sha") != pr["headRefOid"]
                or phase_evidence.get("validation_commands") != validation_commands
                or phase_evidence.get("status") not in {"passed", "failed"}
            ):
                raise ReviewFailure("simplifier validation evidence does not match the reviewed head")
            validation_evidence = phase_validation_evidence
            validation_passed = phase_evidence["status"] == "passed"
            validation_failure = str(phase_evidence.get("failure", ""))
            runner.log("Reusing exact-head validation evidence from the simplification phase")
        else:
            setup_output = run_project_commands(runner, workspace, setup_commands, [], validation_env)
            validation_output, validation_passed, validation_failure = collect_project_commands(
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
                "passed" if validation_passed else "failed",
                validation_failure,
            )
        if not validation_passed:
            runner.log(
                "Initial validation failed; supplying the complete gate evidence to the repair orchestrator"
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
            ci_context,
            validation_evidence,
            ai_files_manifest,
            simplification_context,
            repairs_allowed,
        )
        validate_docs_manifest(config | project, docs_manifest, candidate_domains)

        original_head = pr["headRefOid"]
        final_head = original_head
        validated_after_orchestrator = False
        if result["status"] in {"repaired", "repaired_blocked"}:
            if not repairs_allowed:
                raise ReviewFailure("orchestrator repaired files without controller authorization")
            result = validate_repair_with_corrections(
                runner,
                config | project,
                workspace,
                pr,
                run_dir,
                changed_files_path,
                docs_manifest,
                skills_manifest,
                setup_commands,
                validation_commands,
                success_markers,
                validation_env,
                result,
            )
            validated_after_orchestrator = True

        if result["status"] in {"repaired", "repaired_blocked"}:
            final_head = commit_repair(runner, workspace, pr)
        elif result["status"] == "clean":
            assert_clean_workspace(runner, workspace, "clean orchestrator result")
            if not validation_passed and not validated_after_orchestrator:
                run_project_commands(
                    runner,
                    workspace,
                    validation_commands,
                    success_markers,
                    validation_env,
                    validation_attempts,
                )
        else:
            assert_clean_workspace(runner, workspace, "blocked orchestrator result")

        local_final_head = runner.run(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip()
        if local_final_head != final_head:
            raise ReviewFailure("final local head does not match the reviewed result")
        if final_head != remote_input_head:
            pushed_head = push_final_head(
                runner,
                workspace,
                pr,
                expected_remote_head=remote_input_head,
            )
            if pushed_head != final_head:
                raise ReviewFailure("atomic push did not use the reviewed local head")
            pr = fetch_pr_at_head(runner, repository, pr_number, final_head)
        else:
            pr = fetch_pr(runner, repository, pr_number)
            if pr.get("headRefOid") != remote_input_head:
                raise ReviewFailure("PR head changed during the atomic review")
        simplification_state = carry_simplification_state_to_head(
            state_root,
            project_name,
            pr,
            simplification_state,
        )

        comment = format_review_comment(
            result,
            original_head=original_head,
            final_head=final_head,
            validation_commands=validation_commands,
            simplification=simplification_state,
        )
        current_before_comment = fetch_pr(runner, repository, pr_number)
        if current_before_comment.get("headRefOid") != final_head:
            raise ReviewFailure("PR head changed before the review comment could be published")
        pr = current_before_comment
        upsert_review_comment(runner, repository, pr, comment)
        current = fetch_pr(runner, repository, pr_number)
        if current.get("headRefOid") != final_head:
            raise ReviewFailure("PR head changed before reviewed state could be recorded")
        record_review_state(state_root, project_name, current, result["status"])

        summary = {
            "status": result["status"],
            "github_input_head_sha": remote_input_head,
            "reviewed_head_sha": original_head,
            "final_head_sha": final_head,
            "repairs": result.get("repairs", []),
            "manual_ui_checks": result.get("manual_ui_checks", []),
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
