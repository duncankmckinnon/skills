"""Exercise regional MCP selection in real Codex with isolated, synthetic servers."""

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CODEX = shutil.which("codex")
PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
PLUGIN_ID = "logfire@pydantic-skills"
SCRIPT = PLUGIN / "scripts/codex_mcp_policy.py"


@unittest.skipUnless(CODEX, "Codex is required for native MCP policy tests")
class McpPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "codex"
        self.home.mkdir()
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.root),
            "USERPROFILE": str(self.root),
            "CODEX_HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.root / "xdg"),
        }
        self.config = self.home / "config.toml"
        self.config.write_text(
            'model = "preserve-this-model"\n# Preserve this comment\n'
        )
        self.codex("plugin", "marketplace", "add", str(REPO), "--json")
        self.codex("plugin", "add", PLUGIN_ID, "--json")

    def codex(self, *arguments):
        result = subprocess.run(
            [CODEX, *arguments],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def policy(self, action, *, plugin_id=PLUGIN_ID, consent=True):
        arguments = [sys.executable, str(SCRIPT), action, "--plugin-id", plugin_id]
        if consent:
            arguments.append("--yes")
        return subprocess.run(
            arguments,
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def test_requires_consent_and_exact_installed_plugin(self):
        before = self.config.read_bytes()
        self.assertEqual(self.policy("disable", consent=False).returncode, 2)
        self.assertEqual(self.config.read_bytes(), before)
        for identifier in ["logfire@not-installed", "other@pydantic-skills"]:
            result = self.policy("disable", plugin_id=identifier)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(self.config.read_bytes(), before)

    def test_regional_switch_reconnect_and_return_to_us_preserve_other_settings(self):
        for _ in range(2):
            result = self.policy("disable")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertFalse(json.loads(result.stdout)["bundled_mcp_enabled"])

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                message = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                if "id" not in message:
                    self.send_response(202)
                    self.end_headers()
                    return
                result = (
                    {
                        "protocolVersion": message["params"]["protocolVersion"],
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": self.path, "version": "1"},
                    }
                    if message["method"] == "initialize"
                    else {"tools": []}
                )
                body = json.dumps(
                    {"jsonrpc": "2.0", "id": message["id"], "result": result}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as http:
            worker = threading.Thread(target=http.serve_forever, daemon=True)
            worker.start()
            try:
                base = f"http://127.0.0.1:{http.server_port}"
                self.codex("mcp", "add", "unrelated", "--url", base + "/other")
                # Simulate EU, then an explicitly confirmed self-hosted replacement.
                for path in ["/eu", "/self-hosted"]:
                    if path == "/self-hosted":
                        self.codex("mcp", "remove", "logfire-regional")
                    self.codex("mcp", "add", "logfire-regional", "--url", base + path)
                    # A new host represents the documented reload/restart boundary.
                    self.verify_connected_server(path)
                    self.assertEqual(self.policy("disable").returncode, 0)
                self.codex("mcp", "remove", "logfire-regional")
                enabled = self.policy("enable")
                self.assertEqual(enabled.returncode, 0, enabled.stdout)
                config = tomllib.loads(self.config.read_text())
                self.assertTrue(config["plugins"][PLUGIN_ID]["enabled"])
                self.assertTrue(
                    config["plugins"][PLUGIN_ID]["mcp_servers"]["logfire"]["enabled"]
                )
                self.assertNotIn("logfire-regional", config["mcp_servers"])
                self.assertEqual(
                    config["mcp_servers"]["unrelated"]["url"], base + "/other"
                )
                self.assertEqual(config["model"], "preserve-this-model")
                self.assertIn("# Preserve this comment", self.config.read_text())
                self.assertNotIn("otel", config)
            finally:
                http.shutdown()
                worker.join(timeout=5)

    def verify_connected_server(self, expected_path):
        with subprocess.Popen(
            [CODEX, "app-server"],
            cwd=self.root,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ) as process:
            responses = queue.Queue()

            def read():
                for line in process.stdout:
                    responses.put(json.loads(line))

            reader = threading.Thread(target=read, daemon=True)
            reader.start()

            def request(identifier, method, params):
                process.stdin.write(
                    json.dumps({"id": identifier, "method": method, "params": params})
                    + "\n"
                )
                process.stdin.flush()
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
                        "clientInfo": {"name": "regional-mcp-test", "version": "1"},
                        "capabilities": {"experimentalApi": True},
                    },
                )
                connections = request(2, "mcpServerStatus/list", {})["data"]
                regional = next(
                    item for item in connections if item["name"] == "logfire-regional"
                )
                self.assertEqual(regional["serverInfo"]["name"], expected_path)
                bundled = next(
                    item for item in connections if item["name"] == "logfire"
                )
                self.assertIsNone(bundled["serverInfo"])
                detail = request(
                    3,
                    "plugin/read",
                    {
                        "pluginName": "logfire",
                        "marketplacePath": str(
                            REPO / ".agents/plugins/marketplace.json"
                        ),
                    },
                )["plugin"]
                self.assertTrue(detail["onboardingSkill"]["enabled"])
                config = tomllib.loads(self.config.read_text())
                self.assertFalse(
                    config["plugins"][PLUGIN_ID]["mcp_servers"]["logfire"]["enabled"]
                )
            finally:
                process.terminate()
                process.wait(timeout=10)
                reader.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
