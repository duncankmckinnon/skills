"""Load the real plugin in an isolated Codex home, without model or account access."""

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

CODEX = shutil.which("codex")
REPO = Path(__file__).resolve().parents[3]


@unittest.skipUnless(CODEX, "Codex CLI is required for plugin installation tests")
class CodexPluginTests(unittest.TestCase):
    def test_marketplace_install_loads_packaged_onboarding_without_telemetry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            home = root / "codex"
            home.mkdir()
            environment = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(root),
                "USERPROFILE": str(root),
                "CODEX_HOME": str(home),
                "XDG_CONFIG_HOME": str(root / "xdg"),
            }

            def run(*arguments):
                result = subprocess.run(
                    [CODEX, "plugin", *arguments],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            run("marketplace", "add", str(REPO), "--json")
            run("add", "logfire@pydantic-skills", "--json")
            plugins = run("list", "--json")
            installed = next(
                item
                for item in plugins["installed"]
                if item["pluginId"] == "logfire@pydantic-skills"
            )
            self.assertTrue(installed["enabled"])
            # Validate the installed payload rather than the source checkout.
            manifests = list(
                (home / "plugins" / "cache").glob(
                    "**/logfire/*/.codex-plugin/plugin.json"
                )
            )
            self.assertEqual(len(manifests), 1)
            manifest = json.loads(manifests[0].read_text())
            plugin_root = manifests[0].parents[1]
            onboarding = (
                plugin_root / manifest["extensions"]["com.openai"]["onboardingSkill"]
            )
            self.assertTrue(onboarding.is_file())
            self.assertTrue(
                (onboarding.parent / "../../scripts/agent_runtime.py")
                .resolve()
                .is_file()
            )
            self.assertTrue((onboarding.parent / "references/connect.md").is_file())
            # Ask the host parser, not just JSON decoding, whether setup is wired.
            with subprocess.Popen(
                [CODEX, "app-server"],
                cwd=root,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            ) as server:
                responses = queue.Queue()

                def read_responses():
                    for line in server.stdout:
                        responses.put(json.loads(line))

                reader = threading.Thread(target=read_responses, daemon=True)
                reader.start()

                def request(identifier, method, params):
                    server.stdin.write(
                        json.dumps(
                            {"id": identifier, "method": method, "params": params}
                        )
                        + "\n"
                    )
                    server.stdin.flush()
                    while True:
                        response = responses.get(timeout=30)
                        if response.get("id") == identifier:
                            self.assertNotIn("error", response, response)
                            return response["result"]

                try:
                    request(
                        1,
                        "initialize",
                        {
                            "clientInfo": {
                                "name": "logfire-plugin-test",
                                "version": "1",
                            },
                            "capabilities": {"experimentalApi": True},
                        },
                    )
                    detail = request(
                        2,
                        "plugin/read",
                        {
                            "pluginName": "logfire",
                            "marketplacePath": str(
                                REPO / ".agents/plugins/marketplace.json"
                            ),
                        },
                    )["plugin"]
                    self.assertEqual(
                        detail["onboardingSkill"]["name"],
                        "logfire:logfire-codex-connection",
                    )
                    self.assertTrue(detail["onboardingSkill"]["enabled"])
                finally:
                    server.terminate()
                    server.wait(timeout=10)
                    reader.join(timeout=5)
            config = (home / "config.toml").read_text()
            self.assertNotIn("[otel", config)
            self.assertFalse((root / "xdg" / "logfire-exporter").exists())
            self.assertFalse((root / ".logfire").exists())
            run("remove", "logfire@pydantic-skills", "--json")


if __name__ == "__main__":
    unittest.main()
