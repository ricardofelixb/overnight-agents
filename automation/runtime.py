"""Shared process, repository-runtime, and coding-agent execution helpers."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
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
    "VITEST_MAX_WORKERS",
}

DEFAULT_VITEST_MAX_WORKERS = "4"

AGENT_PROCESS_GUIDANCE = """

Shared process rules:
- Run validation in the foreground with a tool timeout long enough for the repository command.
- Never use nohup, `run_in_background`, a shell background operator, a pipe to `tail`, or any other detached wrapper for a validation command.
- Wait for the exact validation command to exit, read its complete output, and use that command's own exit status as the only validation verdict. A successful `ps`, `pgrep`, `tail`, monitor, or other helper command never proves validation passed.
- Treat every non-zero validation exit as a failure. Do not report success, mark a checklist item complete, or return while validation, verification, or their result is pending.
- Before retrying validation, confirm the previous validation process has exited; never run duplicate repository validations concurrently.
- Use engineering judgment for proven environmental failures: rerun the failed component in isolation and report the evidence instead of restarting an otherwise-complete monolithic pipeline solely to obtain a single green transcript.
""".rstrip()


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


def protected_repository_config(workspace: Path) -> tuple[str, ...]:
    """Return shared Git configuration excluding volatile per-branch UI metadata."""
    values = git(workspace, "config", "--local", "--list").stdout.splitlines()
    return tuple(sorted(value for value in values if not value.startswith("branch.")))


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
    # Vitest otherwise uses every available CPU except one. An agent and its
    # verifier can then starve individual tests until their normal timeouts.
    environment.setdefault("VITEST_MAX_WORKERS", DEFAULT_VITEST_MAX_WORKERS)
    return environment


HOOKS_DIR = Path(__file__).resolve().parent / "hooks"


def agent_settings(report_field: str | None) -> dict[str, Any]:
    """Enforce the headless process rules the prompt can only ask for."""
    hooks: dict[str, Any] = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{sys.executable} {HOOKS_DIR / 'deny_detached_bash.py'}",
                    }
                ],
            }
        ]
    }
    if report_field:
        hooks["Stop"] = [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": (
                            f"{sys.executable} {HOOKS_DIR / 'require_report.py'} "
                            f"{report_field}"
                        ),
                    }
                ]
            }
        ]
    return {"hooks": hooks}


def agent_command(
    config: dict[str, Any],
    workspace: Path,
    prompt: str,
    report_field: str | None = None,
    *,
    persist_session: bool = False,
) -> list[str]:
    provider = config.get("provider", "codex")
    if provider == "codex":
        command = [
            "codex",
            "exec",
            "--json" if persist_session else "--ephemeral",
        ]
        return command + [
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
            "--settings",
            json.dumps(agent_settings(report_field)),
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
    report_field: str | None = None,
    persist_session: bool = False,
    process_guidance: str = AGENT_PROCESS_GUIDANCE,
) -> subprocess.CompletedProcess[str]:
    full_prompt = prompt.rstrip()
    if process_guidance:
        full_prompt += f"\n{process_guidance}"
    return run(
        agent_command(
            config,
            workspace,
            full_prompt,
            report_field,
            persist_session=persist_session,
        ),
        cwd=workspace,
        env=agent_environment(workspace, environment_file),
        check=False,
        timeout=int(config.get("agent_timeout_seconds", 7200)),
        stream=stream,
    )


def codex_session(output: str) -> tuple[str, str]:
    """Extract a persisted Codex thread id and assistant text from JSONL."""

    thread_id = ""
    messages: list[str] = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            value = event.get("thread_id")
            if isinstance(value, str):
                thread_id = value
        item = event.get("item")
        if (
            event.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            messages.append(item["text"])
    if not thread_id:
        raise RuntimeFailure("Codex JSONL output did not contain a persisted thread id")
    if not messages:
        raise RuntimeFailure("Codex JSONL output did not contain an assistant message")
    return thread_id, "\n".join(messages)


def resume_codex_session(
    config: dict[str, Any],
    workspace: Path,
    session_id: str,
    prompt: str,
    stream: TextIO,
    *,
    environment_file: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        "codex",
        "exec",
        "resume",
        "--json",
        "--ignore-user-config",
        "--dangerously-bypass-approvals-and-sandbox",
        "--model",
        str(config.get("codex_model", "gpt-5.6-terra")),
        "--config",
        f"model_reasoning_effort={json.dumps(config.get('codex_reasoning_effort', 'medium'))}",
        session_id,
        prompt,
    ]
    return run(
        command,
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
