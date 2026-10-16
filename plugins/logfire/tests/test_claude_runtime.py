"""Connection setup must not turn a status check into an install or credential probe."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "claude_runtime.py"
SPEC = importlib.util.spec_from_file_location("claude_runtime", SCRIPT)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def version_result(version="0.1.14", returncode=0):
    return subprocess.CompletedProcess(
        [], returncode, json.dumps({"version": 1, "data": {"installed": version}}), ""
    )


class RuntimeTests(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("uv"), "uv is needed for the offline installer smoke test"
    )
    def test_real_uv_install_survives_cache_removal_without_project_or_claude_writes(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheels = root / "wheels"
            wheels.mkdir()
            # A synthetic offline wheel tests uv's actual installation layout, not
            # Logfire authentication/capture. No real package or account is used.
            files = {
                "runtime_fixture.py": 'import json\ndef main():\n print(json.dumps({"version":1,"data":{"installed":"0.1.14"}}))\n',
                "logfire_cli-0.1.14.dist-info/METADATA": "Metadata-Version: 2.1\nName: logfire-cli\nVersion: 0.1.14\n",
                "logfire_cli-0.1.14.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
                "logfire_cli-0.1.14.dist-info/entry_points.txt": "[console_scripts]\nlogfire-cli = runtime_fixture:main\n",
            }
            files["logfire_cli-0.1.14.dist-info/RECORD"] = "".join(
                f"{name},,\n" for name in files
            )
            with zipfile.ZipFile(
                wheels / "logfire_cli-0.1.14-py3-none-any.whl", "w"
            ) as wheel:
                for name, content in files.items():
                    wheel.writestr(name, content)
            environment = {
                **os.environ,
                "HOME": str(root / "home"),
                "UV_NO_INDEX": "true",
                "UV_NO_CONFIG": "true",
                "UV_FIND_LINKS": str(wheels),
                "UV_CACHE_DIR": str(root / "cache"),
                "UV_PYTHON_DOWNLOADS": "never",
            }
            command = [
                sys.executable,
                str(SCRIPT),
                "--data-root",
                str(root / "persistent runtime"),
            ]
            installed = subprocess.run(
                [*command, "--install"],
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(installed.returncode, 0, installed.stdout)
            first = json.loads(installed.stdout)
            self.assertEqual(first["status"], "ready")
            self.assertTrue(Path(first["executable"]).is_file())
            shutil.rmtree(root / "cache")
            environment["PATH"] = ""
            reused = subprocess.run(
                command,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(reused.returncode, 0, reused.stdout)
            self.assertEqual(json.loads(reused.stdout), first)
            self.assertFalse(Path(first["data_dir"]).exists())
            self.assertFalse((root / "home" / ".claude").exists())

    def test_uses_machine_version_contract_and_rejects_incompatible_releases(self):
        for version, accepted in [
            ("0.1.13", False),
            ("0.1.14", True),
            ("0.1.99", True),
            ("0.2.0", False),
            ("0.1.14rc1", False),
            ("dev", False),
        ]:
            with (
                self.subTest(version=version),
                patch.object(
                    runtime.subprocess, "run", return_value=version_result(version)
                ) as run,
            ):
                self.assertEqual(
                    runtime.compatible("/path with spaces/logfire"), accepted
                )
                self.assertEqual(
                    run.call_args.args[0],
                    ["/path with spaces/logfire", "--output", "json", "version"],
                )

    def test_invalid_version_results_are_not_trusted_or_printed(self):
        for value in [
            "not JSON",
            "[]",
            "{}",
            '{"version":2,"data":{"installed":"0.1.14"}}',
            '{"version":1,"data":{"installed":14}}',
        ]:
            with patch.object(
                runtime.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, value, "secret"),
            ):
                self.assertFalse(runtime.compatible("logfire"))
        with patch.object(
            runtime.subprocess, "run", return_value=version_result(returncode=1)
        ):
            self.assertFalse(runtime.compatible("logfire"))
        for error in [OSError("secret"), subprocess.TimeoutExpired("logfire", 15)]:
            with patch.object(runtime.subprocess, "run", side_effect=error):
                self.assertFalse(runtime.compatible("logfire"))

    def test_prefers_existing_managed_runtime_without_installing(self):
        root = Path("/private/runtime with spaces")
        with (
            patch.object(runtime, "compatible", return_value=True),
            patch.object(runtime.subprocess, "run") as run,
        ):
            result = runtime.prepare(root)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(Path(result["data_dir"]).parent, root / "projects")
        self.assertEqual(
            result["executable"],
            str(
                (
                    root
                    / "bin"
                    / ("logfire-cli.exe" if os.name == "nt" else "logfire-cli")
                ).resolve()
            ),
        )
        run.assert_not_called()

    def test_independent_claude_configurations_do_not_share_project_credentials(self):
        with patch.object(runtime, "compatible", return_value=True):
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/first/claude"}):
                first = runtime.prepare(Path("/private/runtime"))
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/second/claude"}):
                second = runtime.prepare(Path("/private/runtime"))
        self.assertEqual(first["executable"], second["executable"])
        self.assertNotEqual(first["data_dir"], second["data_dir"])

    def test_relative_claude_configuration_fails_before_install_or_probe(self):
        with (
            patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "relative"}),
            patch.object(runtime, "compatible") as probe,
        ):
            self.assertEqual(
                runtime.prepare(Path("/private/runtime"), True)["status"],
                "invalid_config_dir",
            )
        probe.assert_not_called()

    def test_does_not_bind_native_helper_to_an_ambient_cli_or_uvx_cache(self):
        with (
            patch.object(runtime, "compatible", return_value=False),
            patch.object(runtime.shutil, "which") as which,
        ):
            result = runtime.prepare(Path("/private/runtime"))
        self.assertEqual(result["status"], "runtime_required")
        which.assert_not_called()

    def test_status_never_installs_or_creates_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "missing"
            with (
                patch.object(runtime, "compatible", return_value=False),
                patch.object(runtime.shutil, "which", return_value=None),
                patch.object(runtime.subprocess, "run") as run,
            ):
                self.assertEqual(runtime.prepare(root)["status"], "runtime_required")
                self.assertEqual(
                    runtime.prepare(root, install=True)["status"], "uv_required"
                )
            run.assert_not_called()
            self.assertFalse(root.exists())

    def test_install_is_private_persistent_and_uses_no_shell(self):
        root = Path("/private/runtime with spaces")
        with (
            patch.object(runtime, "compatible", side_effect=[False, True]),
            patch.object(runtime.shutil, "which", return_value="/bin/uv"),
            patch.object(
                runtime.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as run,
        ):
            result = runtime.prepare(root, install=True)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(
            run.call_args.args[0], ["/bin/uv", "tool", "install", runtime.REQUIREMENT]
        )
        self.assertEqual(
            run.call_args.kwargs["env"]["UV_TOOL_DIR"], str(root / "tools")
        )
        self.assertEqual(
            run.call_args.kwargs["env"]["UV_TOOL_BIN_DIR"], str(root / "bin")
        )
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_failed_installs_do_not_leak_output_or_fall_back_to_old_cli(self):
        for code in [0, 1]:
            with (
                patch.object(runtime, "compatible", return_value=False),
                patch.object(runtime.shutil, "which", return_value="/bin/uv"),
                patch.object(
                    runtime.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        [], code, b"secret", b"secret"
                    ),
                ),
            ):
                result = runtime.prepare(Path("/private/runtime"), install=True)
            self.assertEqual(result["status"], "install_failed")
            self.assertNotIn("secret", json.dumps(result))
        for error in [OSError("secret"), subprocess.TimeoutExpired("uv", 180)]:
            with (
                patch.object(runtime, "compatible", return_value=False),
                patch.object(runtime.shutil, "which", return_value="/bin/uv"),
                patch.object(runtime.subprocess, "run", side_effect=error),
            ):
                self.assertEqual(
                    runtime.prepare(Path("/private/runtime"), True)["status"],
                    "install_failed",
                )

    def test_real_entrypoint_is_read_only_and_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "missing"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--data-root", str(root)],
                env={"PATH": "", "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["status"], "runtime_required")
            self.assertFalse(root.exists())
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--data-root", "relative"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("must be absolute", result.stderr)


if __name__ == "__main__":
    unittest.main()
