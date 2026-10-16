"""Real published CLI and Codex; synthetic Platform API and fully isolated settings."""

import json
import shutil
import unittest

from test_claude_cli import CLI, LocalApiFixture


@unittest.skipUnless(CLI and shutil.which("codex"), "Published CLI and Codex required")
class CodexCliTests(LocalApiFixture):
    def setUp(self):
        super().setUp()
        self.allow_write_tokens = True
        self.env["CODEX_HOME"] = str(self.root / "codex")
        self.env["XDG_CONFIG_HOME"] = str(self.root / "xdg")
        self.native = self.root / "codex" / "config.toml"
        self.native.parent.mkdir()
        self.unrelated = 'model = "unchanged-model"\n# Keep my settings\n'
        self.native.write_text(self.unrelated)

    def telemetry(self, *arguments, base=None):
        return self.run_cli(
            "agent", "codex", "--data-dir", str(self.data), *arguments, base=base
        )

    def test_capture_modes_reconnect_status_and_removal(self):
        credential = self.credential.read_bytes()
        for mode, expected in [
            ("metadata-only", "metadata_only"),
            ("no-tool-content", "no_tool_content"),
            ("full", "full"),
        ]:
            with self.subTest(mode=mode):
                configured = self.telemetry("configure", "--capture", mode)
                self.assertEqual(configured.returncode, 0, configured.stderr)
                self.assertNotIn("synthetic-exporter-token", configured.stdout)
                status = self.telemetry("status")
                self.assertEqual(status.returncode, 0, status.stderr)
                self.assertEqual(json.loads(status.stdout)["data"]["capture"], expected)
                self.assertNotIn("synthetic-exporter-token", status.stdout)
        # Each reconfiguration checks the active token rather than minting another.
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)
        before = self.native.read_bytes()
        checked = self.telemetry("status")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(self.native.read_bytes(), before)
        removed = self.telemetry("remove")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn('model = "unchanged-model"', self.native.read_text())
        self.assertIn("# Keep my settings", self.native.read_text())
        self.assertNotIn("synthetic-exporter-token", self.native.read_text())
        self.assertEqual(self.credential.read_bytes(), credential)
        final = self.telemetry("status")
        self.assertEqual(final.returncode, 0, final.stderr)
        self.assertEqual(json.loads(final.stdout)["data"]["native_traces"], "disabled")

    def test_independent_exporter_is_preserved_before_install_or_token_creation(self):
        original = (
            self.unrelated
            + '[otel]\nexporter = {otlp-http = {endpoint = "https://independent.example/v1/logs"}}\n'
        )
        self.native.write_text(original)
        configured = self.telemetry("configure", "--capture", "metadata-only")
        self.assertNotEqual(configured.returncode, 0)
        self.assertEqual(self.native.read_text(), original)
        self.assertFalse(any(method == "POST" for method, _ in self.calls))


if __name__ == "__main__":
    unittest.main()
