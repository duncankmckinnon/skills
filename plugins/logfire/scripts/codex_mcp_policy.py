"""Toggle only the installed Logfire plugin's bundled MCP server via Codex's config API."""

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time


def set_policy(plugin_id, enabled):
    codex = shutil.which("codex")
    if codex is None:
        raise RuntimeError("Install the Codex CLI before changing MCP settings.")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"LOGFIRE_API_KEY", "LOGFIRE_TOKEN", "LOGFIRE_MCP_TOKEN"}
    }
    installed = subprocess.run(
        [codex, "plugin", "list", "--json"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if installed.returncode:
        raise RuntimeError(
            "Could not inspect installed Codex plugins; settings unchanged."
        )
    if not plugin_id.startswith("logfire@") or not any(
        item["pluginId"] == plugin_id
        for item in json.loads(installed.stdout)["installed"]
    ):
        raise RuntimeError(
            "Select the exact installed Logfire plugin ID; settings unchanged."
        )
    process = subprocess.Popen(
        [codex, "app-server"],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    responses = queue.Queue()

    def read_responses():
        try:
            for line in process.stdout:
                responses.put(json.loads(line))
        except ValueError:
            pass
        finally:
            responses.put(None)

    reader = threading.Thread(target=read_responses, daemon=True)
    reader.start()

    def request(identifier, method, params):
        process.stdin.write(
            json.dumps({"id": identifier, "method": method, "params": params}) + "\n"
        )
        process.stdin.flush()
        deadline = time.monotonic() + 30
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    "Codex did not confirm the configuration operation in time."
                )
            response = responses.get(timeout=remaining)
            if response is None:
                raise RuntimeError("Codex closed the configuration connection.")
            if response.get("id") == identifier:
                if "error" in response:
                    raise RuntimeError("Codex rejected the configuration operation.")
                return

    try:
        request(
            1,
            "initialize",
            {"clientInfo": {"name": "logfire-plugin-setup", "version": "1"}},
        )
        request(
            2,
            "config/value/write",
            {
                "keyPath": f"plugins.{json.dumps(plugin_id)}.mcp_servers.logfire.enabled",
                "value": enabled,
                "mergeStrategy": "replace",
            },
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=5)
        process.stdin.close()
        process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("enable", "disable"))
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument(
        "--yes", action="store_true", help="Confirm this user-wide MCP policy change"
    )
    args = parser.parse_args()
    if not args.yes:
        parser.error("Confirm the MCP change with the user, then pass --yes.")
    try:
        set_policy(args.plugin_id, args.action == "enable")
    except (
        RuntimeError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        queue.Empty,
        subprocess.TimeoutExpired,
    ) as error:
        # Do not expose child output or configuration. A write can have succeeded
        # before a lost response; callers must inspect state before retrying.
        message = (
            str(error)
            if isinstance(error, RuntimeError)
            else "Could not complete the Codex configuration operation."
        )
        print(json.dumps({"status": "check_required", "message": message}))
        return 1
    print(
        json.dumps(
            {
                "status": "configured",
                "plugin_id": args.plugin_id,
                "bundled_mcp_enabled": args.action == "enable",
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
