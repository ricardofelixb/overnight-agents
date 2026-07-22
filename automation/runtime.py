"""Shared process, repository-runtime, and coding-agent execution helpers."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


class RuntimeFailure(RuntimeError):
    pass


SAFE_AGENT_ENV = {
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CODEX_API_KEY",
    "CODEX_HOME",
    "HOME",
    "LANG",
    "LC_ALL",
    "LOGNAME",
    "OPENAI_API_KEY",
    "PATH",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TERM",
    "TMPDIR",
    "USER",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: int | None = None,
    stream: TextIO | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=timeout,
    )
    if stream:
        stream.write(f"[{now_iso()}] RUN {' '.join(command[:3])}\n")
        stream.write(result.stdout)
        if result.stdout and not result.stdout.endswith("\n"):
            stream.write("\n")
        stream.flush()
    if check and result.returncode != 0:
        raise RuntimeFailure(
            f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}"
        )
    return result


def git(
    cwd: Path,
    *arguments: str,
    check: bool = True,
    stream: TextIO | None = None,
) -> subprocess.CompletedProcess[str]:
    return run(["git", *arguments], cwd=cwd, check=check, stream=stream)


def repository_runtime_path(workspace: Path, inherited_path: str | None = None) -> str:
    path = inherited_path or os.environ.get(
        "PATH", "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    )
    for name in (".nvmrc", ".node-version"):
        declaration = workspace / name
        if not declaration.is_file():
            continue
        version = declaration.read_text().strip().removeprefix("v")
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            break
        binary = (
            Path.home()
            / ".local/share/fnm/node-versions"
            / f"v{version}"
            / "installation/bin"
        )
        if (binary / "node").is_file():
            return f"{binary}:{path}"
        break
    return path


def load_environment_file(
    path: Path, environment: dict[str, str], *, require_private: bool = False
) -> None:
    if not path.exists():
        return
    if require_private and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeFailure(
            f"environment file must not be accessible by group or other users: {path}"
        )
    for number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            raise RuntimeFailure(f"invalid environment assignment at {path}:{number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise RuntimeFailure(f"invalid environment name at {path}:{number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        environment[name] = value


def agent_environment(workspace: Path, environment_file: Path | None = None) -> dict[str, str]:
    source = os.environ.copy()
    if environment_file is not None:
        load_environment_file(environment_file, source, require_private=True)
    environment = {key: value for key, value in source.items() if key in SAFE_AGENT_ENV}
    environment["PATH"] = repository_runtime_path(workspace, environment.get("PATH"))
    environment["CI"] = "true"
    return environment


def agent_command(
    config: dict[str, Any], workspace: Path, prompt: str
) -> list[str]:
    provider = config.get("provider", "codex")
    if provider == "codex":
        return [
            "codex",
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--enable",
            "multi_agent",
            "--dangerously-bypass-approvals-and-sandbox",
            "--model",
            str(config.get("codex_model", "gpt-5.6-terra")),
            "--config",
            f"model_reasoning_effort={json.dumps(config.get('codex_reasoning_effort', 'medium'))}",
            "--cd",
            str(workspace),
            prompt,
        ]
    if provider == "claude":
        return [
            "claude",
            "--dangerously-skip-permissions",
            "--model",
            str(config.get("claude_model", "claude-opus-4-8")),
            "--effort",
            str(config.get("claude_effort", "medium")),
            "-p",
            prompt,
        ]
    raise RuntimeFailure("provider must be codex or claude")


def run_agent(
    config: dict[str, Any],
    workspace: Path,
    prompt: str,
    stream: TextIO,
    *,
    environment_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return run(
        agent_command(config, workspace, prompt),
        cwd=workspace,
        env=agent_environment(workspace, environment_file),
        check=False,
        timeout=int(config.get("agent_timeout_seconds", 7200)),
        stream=stream,
    )


def prune_logs(directory: Path, pattern: str, *, keep: int = 30) -> None:
    if keep < 1:
        raise RuntimeFailure("log retention must keep at least one file")
    paths = sorted(
        directory.glob(pattern),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for path in paths[keep:]:
        path.unlink()
