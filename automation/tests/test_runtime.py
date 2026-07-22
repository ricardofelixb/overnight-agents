from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.runtime import (
    AGENT_PROCESS_GUIDANCE,
    agent_environment,
    protected_repository_config,
    prune_logs,
    repository_runtime_path,
)


class RuntimeTests(unittest.TestCase):
    def test_agent_environment_keeps_provider_auth_but_not_controller_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            values = {
                "CLAUDE_CODE_OAUTH_TOKEN": "provider-auth",
                "GH_TOKEN": "github-controller",
                "CONVEX_MANAGEMENT_TOKEN": "cleanup-controller",
            }
            with mock.patch.dict(os.environ, values, clear=True):
                environment = agent_environment(workspace)
        self.assertEqual(environment["CLAUDE_CODE_OAUTH_TOKEN"], "provider-auth")
        self.assertEqual(environment["VITEST_MAX_WORKERS"], "4")
        self.assertNotIn("GH_TOKEN", environment)
        self.assertNotIn("CONVEX_MANAGEMENT_TOKEN", environment)

    def test_agent_environment_preserves_explicit_vitest_worker_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            with mock.patch.dict(
                os.environ, {"VITEST_MAX_WORKERS": "2"}, clear=True
            ):
                environment = agent_environment(workspace)

        self.assertEqual(environment["VITEST_MAX_WORKERS"], "2")

    def test_shared_process_guidance_prevents_duplicate_validation(self) -> None:
        self.assertIn("Never use nohup", AGENT_PROCESS_GUIDANCE)
        self.assertIn("never run duplicate repository validations", AGENT_PROCESS_GUIDANCE)
        self.assertIn("proven environmental failures", AGENT_PROCESS_GUIDANCE)

    def test_log_retention_is_shared_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(4):
                path = root / f"agent_{index}.log"
                path.write_text(str(index))
                os.utime(path, ns=(index + 1, index + 1))
            prune_logs(root, "agent_*.log", keep=2)
            self.assertEqual(
                sorted(path.name for path in root.glob("*.log")),
                ["agent_2.log", "agent_3.log"],
            )

    def test_repository_runtime_path_prefers_declared_node(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / ".nvmrc").write_text("24.16.0\n")
            binary = (
                root
                / ".local/share/fnm/node-versions/v24.16.0/installation/bin"
            )
            binary.mkdir(parents=True)
            (binary / "node").write_text("")
            with mock.patch("automation.runtime.Path.home", return_value=root):
                path = repository_runtime_path(workspace, "/usr/bin:/bin")
        self.assertEqual(path, f"{binary}:/usr/bin:/bin")

    def test_protected_repository_config_ignores_volatile_branch_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            subprocess.run(
                ["git", "init", "-b", "main"], cwd=workspace, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            subprocess.run(
                ["git", "config", "remote.origin.url", "example:test.git"],
                cwd=workspace, check=True,
            )
            before = protected_repository_config(workspace)
            subprocess.run(
                ["git", "config", "branch.main.vscode-merge-base", "origin/main"],
                cwd=workspace, check=True,
            )
            after = protected_repository_config(workspace)

        self.assertEqual(before, after)
        self.assertIn("remote.origin.url=example:test.git", after)


if __name__ == "__main__":
    unittest.main()
