---
description: Check MCP access and native Claude telemetry configuration without changing them
---

# Logfire connection status

Check the two connections separately. This command is for Claude Code.

1. Inspect the plugin's resolved MCP endpoint and authentication status. A small
   authenticated read may verify access; report network/authentication failure
   without changing the server or opening a new login flow.
   MCP configuration commands may exit zero even when connection failed; inspect
   their reported status, not just the process exit code.
2. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/claude_runtime.py"` without
   `--install`. This must not install dependencies during a status check.
3. If ready, use its returned executable to run
   `--beta --no-input --output json agent claude --data-dir <data_dir> status`.
   Never open/print SDK credential files or read OAuth secrets. If the runtime
   or project credentials are missing, report telemetry status as unknown, not
   disabled: settings installed through another workflow may still be active.
4. Report ingestion only if a bounded MCP query in the confirmed telemetry
   destination finds matching recent Claude events. Otherwise say not verified.

Keep the result to three short rows: MCP access, session capture (including
destination/mode), and delivery evidence (or "Not verified"). Include one next
action only when needed. `needs_update` is not healthy delivery.
Offer `/logfire:connect` for repairs; do not repair or reinstall automatically.
