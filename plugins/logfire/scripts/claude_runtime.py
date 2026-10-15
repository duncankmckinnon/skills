"""Locate or explicitly install a persistent CLI for Claude's native telemetry helper."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 0.1.13 predates native trace and tool-output configuration (logfire-cli#288).
REQUIREMENT = "logfire-cli>=0.1.14,<0.2"
MINIMUM = (0, 1, 14)


def compatible(executable):
    """Use the version command's JSON contract, never human help or a credential probe."""
    try:
        result = subprocess.run(
            [str(executable), "--output", "json", "version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        report = json.loads(result.stdout)
        version = report["data"]["installed"]
        if (
            result.returncode
            or report["version"] != 1
            or not re.fullmatch(r"\d+\.\d+\.\d+", version)
        ):
            return False
        return MINIMUM <= tuple(map(int, version.split("."))) < (0, 2, 0)
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return False


def prepare(root, install=False):
    """Do not install, authenticate, or modify Claude settings during a status check."""
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    if not config.is_absolute():
        return {
            "status": "invalid_config_dir",
            "message": "CLAUDE_CONFIG_DIR must be absolute.",
        }
    # Separate SDK destinations for independent Claude installations. Retargeting
    # one configuration must not invalidate another installation's bound helper.
    identity = hashlib.sha256(os.fsencode(config)).hexdigest()
    data_dir = str(root / "projects" / identity)
    command = "logfire.exe" if os.name == "nt" else "logfire"
    managed = root / "bin" / command
    if compatible(managed):
        return {
            "status": "ready",
            "executable": str(managed.resolve()),
            "data_dir": data_dir,
        }
    if not install:
        return {"status": "runtime_required", "requirement": REQUIREMENT}
    uv = shutil.which("uv")
    if not uv:
        return {
            "status": "uv_required",
            "message": "Install uv, then retry connection setup.",
        }
    # uv tool environments survive cache cleanup; uvx environments do not. Keep this
    # outside the plugin cache/data, which Claude can delete during updates/uninstall.
    environment = {
        **os.environ,
        "UV_TOOL_DIR": str(root / "tools"),
        "UV_TOOL_BIN_DIR": str(root / "bin"),
    }
    try:
        result = subprocess.run(
            [uv, "tool", "install", REQUIREMENT],
            env=environment,
            capture_output=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "status": "install_failed",
            "message": "CLI installation did not finish; telemetry settings were not changed.",
        }
    if result.returncode or not compatible(managed):
        return {
            "status": "install_failed",
            "requirement": REQUIREMENT,
            "message": "A compatible CLI could not be installed. Check package availability and retry; do not use an older CLI.",
        }
    return {
        "status": "ready",
        "executable": str(managed.resolve()),
        "data_dir": data_dir,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install",
        action="store_true",
        help="Allow installing a compatible CLI after user consent",
    )
    parser.add_argument(
        "--data-root", type=Path, default=Path.home() / ".logfire" / "claude-plugin"
    )
    args = parser.parse_args()
    if not args.data_root.is_absolute():
        parser.error("--data-root must be absolute")
    report = prepare(args.data_root, args.install)
    print(json.dumps(report))
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
