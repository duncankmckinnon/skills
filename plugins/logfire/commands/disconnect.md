---
description: Stop native Claude session telemetry while keeping Logfire MCP and skills available
---

# Stop Logfire session capture

This command removes native Claude telemetry configuration, not the remote
project, project token, account login, MCP connection, or application
instrumentation. Explain that distinction and confirm the user's intent.
If the user's request is ambiguous, ask whether they want to stop session
capture, disconnect MCP, or both. If MCP-only, use Claude's native controls and
skip the telemetry runtime entirely. Do not make users stop capture just to
disconnect MCP, or clear their MCP login when they only asked to stop capture.

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/agent_runtime.py"` without
`--install`. Using its returned executable and data directory:

1. Preview `--beta --no-input --output json agent claude --data-dir <data_dir> remove --dry-run`.
2. With confirmation, run the same operation with `--yes` instead of `--dry-run`.
3. Recheck `agent claude ... status`. Report the actual result and ask the user
   to restart Claude to stop exports from already-running sessions.

Do not delete the managed runtime or credential directory while the saved
header helper may still reference them. The CLI refuses removal if managed
fields were edited; surface that conflict rather than deleting fields by hand.
If runtime or credentials are missing, do not claim capture has stopped or
install software without asking. Help locate the original configuration.

Disabling/uninstalling the plugin is not a substitute for this operation.
If the user also wants to disconnect MCP, use Claude's native `/mcp` controls
to clear that connection separately. Do not revoke remote tokens unless asked.
