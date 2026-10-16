"""Exercise plugin loading in Claude without personal settings or network authentication."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CLAUDE = shutil.which("claude")
PLUGIN = Path(__file__).resolve().parents[1]


@unittest.skipUnless(CLAUDE, "Claude CLI is required for plugin integration tests")
class ClaudePluginTests(unittest.TestCase):
    def test_mcp_health_checks_the_actual_server(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body["method"])
                if "id" not in body:
                    self.send_response(202)
                    self.end_headers()
                    return
                result = (
                    {
                        "protocolVersion": body["params"]["protocolVersion"],
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "local-test", "version": "1"},
                    }
                    if body["method"] == "initialize"
                    else {"tools": []}
                )
                response = json.dumps(
                    {"jsonrpc": "2.0", "id": body["id"], "result": result}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                environment = {
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": str(root),
                    "USERPROFILE": str(root),
                    "CLAUDE_CONFIG_DIR": str(root / "claude"),
                    "DISABLE_AUTOUPDATER": "1",
                    "DISABLE_TELEMETRY": "1",
                    "DISABLE_ERROR_REPORTING": "1",
                    "LOGFIRE_MCP_URL": f"http://127.0.0.1:{server.server_port}/mcp",
                }
                result = subprocess.run(
                    [
                        CLAUDE,
                        "--plugin-dir",
                        str(PLUGIN),
                        "mcp",
                        "get",
                        "plugin:logfire:logfire",
                    ],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Connected", result.stdout)
                self.assertIn("initialize", requests)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_mcp_endpoint_resolves_without_assuming_shell_overrides_user_settings(self):
        for saved_setting, shell_override in [
            (True, False),
            (True, True),
            (False, True),
        ]:
            with (
                self.subTest(
                    saved_setting=saved_setting, shell_override=shell_override
                ),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory).resolve()
                config = root / "claude"
                config.mkdir()
                settings_url = "http://127.0.0.1:4318/settings-endpoint/mcp"
                shell_url = "http://127.0.0.1:4318/shell-endpoint/mcp"
                (config / "settings.json").write_text(
                    json.dumps(
                        {
                            "env": {"LOGFIRE_MCP_URL": settings_url}
                            if saved_setting
                            else {}
                        }
                    ),
                    encoding="utf-8",
                )
                environment = {
                    key: value
                    for key, value in os.environ.items()
                    if key.upper()
                    in {
                        "PATH",
                        "SYSTEMROOT",
                        "WINDIR",
                        "COMSPEC",
                        "PATHEXT",
                        "TMPDIR",
                        "TEMP",
                        "TMP",
                    }
                }
                environment.update(
                    HOME=str(root),
                    USERPROFILE=str(root),
                    CLAUDE_CONFIG_DIR=str(config),
                    DISABLE_AUTOUPDATER="1",
                    DISABLE_TELEMETRY="1",
                    DISABLE_ERROR_REPORTING="1",
                )
                if shell_override:
                    environment["LOGFIRE_MCP_URL"] = shell_url
                result = subprocess.run(
                    [
                        CLAUDE,
                        "--plugin-dir",
                        str(PLUGIN),
                        "mcp",
                        "get",
                        "plugin:logfire:logfire",
                    ],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(
                    settings_url if saved_setting else shell_url, result.stdout
                )
                self.assertNotIn("logfire-us.pydantic.dev", result.stdout)


if __name__ == "__main__":
    unittest.main()
