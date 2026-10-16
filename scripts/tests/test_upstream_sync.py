"""Drive the real sync script, substituting only Git's remote clone boundary."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(shutil.which("rsync"), "rsync is required for upstream sync")
class UpstreamSyncTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        for filename in (
            "sync-from-upstream.sh",
            "logfire_skill_sources.py",
            "logfire-skill-sources.json",
        ):
            shutil.copy2(ROOT / "scripts" / filename, scripts / filename)
        self.sources = json.loads((scripts / "logfire-skill-sources.json").read_text())
        # The unchanged non-Logfire imports also need fixture directories, but
        # their contents are not part of this migration's assertions.
        imports = re.findall(
            r'sync_skill\s+\\\s*"([^"$]+)"\s+\\\s*"([^"$]+)"',
            (scripts / "sync-from-upstream.sh").read_text(),
        )
        imports += [
            (owner["repository"], owner["path"])
            for owner in self.sources.values()
            if owner["repository"] != "pydantic/skills"
        ]
        for repository, path in imports:
            source = self.root / "upstreams" / repository.split("/")[1] / path
            source.mkdir(parents=True, exist_ok=True)
            (source / "SKILL.md").write_text(f"source: {repository}/{path}\n")
        for name in self.sources:
            for prefix in ("plugins/logfire/skills", "skills"):
                mirror = self.root / prefix / name
                mirror.mkdir(parents=True)
                (mirror / "SKILL.md").write_text("local content\n")
                if self.sources[name]["repository"] != "pydantic/skills":
                    (mirror / "obsolete.md").write_text("stale mirror file\n")
        tools = self.root / "bin"
        tools.mkdir()
        git = tools / "git"
        git.write_text(
            f"#!{sys.executable}\n"
            "import os, pathlib, shutil, sys\n"
            "args = sys.argv[1:]\n"
            "if 'clone' in args:\n"
            "    name = args[-2].rsplit('/', 1)[-1].removesuffix('.git')\n"
            "    shutil.copytree(pathlib.Path(os.environ['FIXTURE_UPSTREAMS']) / name, args[-1])\n"
            "elif args[-2:] == ['rev-parse', 'HEAD']:\n"
            "    print('a' * 40)\n"
            "else:\n"
            "    sys.exit('unexpected Git operation')\n"
        )
        git.chmod(0o755)
        temporary = self.root / "temporary"
        temporary.mkdir()
        self.environment = {
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
            "FIXTURE_UPSTREAMS": str(self.root / "upstreams"),
            "RUNNER_TEMP": str(temporary),
            "SUMMARY_FILE": str(temporary / "summary.md"),
        }

    def run_sync(self):
        return subprocess.run(
            ["bash", "scripts/sync-from-upstream.sh"],
            cwd=self.root,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_imports_all_seven_external_owners_without_overwriting_local_mcp(self):
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name, owner in self.sources.items():
            for prefix in ("plugins/logfire/skills", "skills"):
                mirror = self.root / prefix / name
                if owner["repository"] == "pydantic/skills":
                    self.assertEqual(
                        (mirror / "SKILL.md").read_text(), "local content\n"
                    )
                else:
                    expected = f"source: {owner['repository']}/{owner['path']}\n"
                    self.assertEqual((mirror / "SKILL.md").read_text(), expected)
                    self.assertFalse((mirror / "obsolete.md").exists())
        summary = Path(self.environment["SUMMARY_FILE"]).read_text()
        self.assertIn("pydantic/logfire-cli@aaaaaaa", summary)
        self.assertNotIn("**logfire-query**", summary)
        self.assertNotIn("**logfire-ui**", summary)

    def test_invalid_owner_fails_before_changing_any_mirror(self):
        self.sources["logfire-frontend"]["path"] = "../logfire-frontend"
        (self.root / "scripts/logfire-skill-sources.json").write_text(
            json.dumps(self.sources)
        )
        result = self.run_sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid owner path", result.stderr)
        for name in self.sources:
            mirror = self.root / "plugins/logfire/skills" / name / "SKILL.md"
            self.assertEqual(mirror.read_text(), "local content\n")


if __name__ == "__main__":
    unittest.main()
