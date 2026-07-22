from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.runtime import agent_environment, prune_logs


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
        self.assertNotIn("GH_TOKEN", environment)
        self.assertNotIn("CONVEX_MANAGEMENT_TOKEN", environment)

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


if __name__ == "__main__":
    unittest.main()
