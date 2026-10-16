---
name: logfire-gateway
description: Configure an application's model calls to use the Pydantic AI Gateway. Use when the user asks to route application inference through Gateway, connect a Gateway key, or add a Gateway provider snippet. This does not configure or instrument coding agents.
---

# Configure an Application with Pydantic AI Gateway

For observing or routing Claude Code/Codex itself, use
[coding-agent setup](../logfire-coding-agents/SKILL.md) instead. Keep this
application workflow separate from coding-session recording and provider setup.

Use the Rust `logfire-cli` supplied by the handoff. Invoke the exact executable
or package launcher named there for every CLI operation. Examples below use
`logfire-cli`; keep the handoff's region, organization, and project flags.

Without a prepared handoff, follow [native CLI connection](../logfire-coding-agents/references/connection.md)
and prepare the confirmed project's SDK credential file before creating a new
Gateway key. An application's existing Gateway key can be used
without a new CLI login or an ordinary SDK write token.

Use this skill for model traffic, provider routing, and Gateway credentials. It
is separate from `logfire-instrumentation`: a Logfire write token sends
telemetry, while a Gateway key authorizes model requests. Do not substitute one
for the other. Do not configure coding-agent providers, telemetry, or MCP integrations.

The parent CLI may already have prepared the exact Logfire organization,
project, region, and local SDK credential. Preserve that context. Do not rerun
`auth`, `signup`, `init`, `mcp context`, or `project current` after a prepared
handoff unless the application rejects the prepared credential.

## 1. Identify the application

Before changing files, inspect the repository and identify the existing model
client, model, provider, environment loader, and runnable entrypoint. If the
request names an application or the repository contains a clear inference
client, wire that application through Gateway. Preserve its provider protocol
and model unless the user requests a change.

If there is no inference application or the target is ambiguous, ask which
application the user wants to connect. Confirm a new application's purpose
before scaffolding it.

A Gateway setup request authorizes routing the selected application's model
calls through Gateway. Ordinary Logfire instrumentation does not imply that choice.

Continue this setup in the current agent. A prepared wizard handoff already
finished account and project selection. Do not invoke the wizard again or
launch another setup agent. Preserve the existing model client, provider
abstraction, dependency versions, and environment conventions.

## 2. Handle credentials without exposing them

For an application, use an existing Gateway credential when the user has
provided one through the repository's secret mechanism. Otherwise create a
separate project key through the prepared CLI login:

```bash
logfire-cli api-key gateway --name app-gateway
```

This command prints a one-time secret. Execute it through a process API that
captures stdout in memory and accepts it only after exit zero. Never run it
bare in a recorded terminal. Store it through the repository's existing secret
mechanism as `PYDANTIC_AI_GATEWAY_API_KEY`. Keep unrelated values intact. For a
local environment file, require a gitignored regular file with user-only
permissions, replace exactly one existing assignment or insert one, and commit
the update atomically. Do not append duplicate assignments. If the existing
secret mechanism cannot be updated safely, ask the user to install the key
through their normal secret manager.

A downloaded Connect credential can use the same secret mechanism without
printing or sourcing the downloaded file. Never put a key in agent messages,
source code, logs, tests, or snapshots.

Use the environment variable expected by the selected SDK. Keep the key out of
browser code and client-side bundles. Key creation does not activate Gateway,
configure provider billing, or select a routing endpoint. If the public API
rejects creation because Gateway is inactive or permission is missing, explain
that specific prerequisite. Missing OAuth scope requires renewed consent with
the needed permission; provider activation or billing requires Gateway settings.
Do not activate paid provider access without the user's agreement. Reuse a
successfully created key; do not mint another key during a configuration retry.

## 3. Configure the detected application

Prefer the existing client and dependency versions. Make the smallest change
that preserves the application's current provider abstraction.

### Pydantic AI

Use the Gateway model form when the selected provider and model are known:

```python
from pydantic_ai import Agent

agent = Agent('gateway/openai:gpt-5.2')
```

The normal credential is `PYDANTIC_AI_GATEWAY_API_KEY`. When the application
needs an explicit key or custom route, use the installed Pydantic AI version's
`gateway_provider()` and preserve the provider type and route from the Connect
handoff. Do not guess a provider slug from a model name.

### OpenAI-compatible clients

Use the exact Gateway base URL and route supplied by Connect, or derive the
documented regional route from the handoff and existing provider protocol. Read
the provider-specific examples in the Gateway documentation when needed. Preserve the
client's existing API shape and set its API-key option to
`PYDANTIC_AI_GATEWAY_API_KEY` through the repository's normal environment
loading mechanism. Do not hard-code a regional URL when the handoff already
provides one. A self-hosted origin is not a hosted region: use its confirmed
Gateway resource and configured route, or ask for them. A documented provider
path alone does not prove that this organization has that route enabled.

### Anthropic, Google, and other native clients

Use the provider-specific Gateway snippet from Connect or the linked public
Gateway documentation for the selected region. Native
provider paths are not interchangeable: Anthropic, OpenAI Responses, Google,
Bedrock, and chat-completions routes have different URLs and request shapes.
Do not translate a native request into another protocol merely to make the
example compile.

## 4. Verify one real request

Run one representative model request through the selected route. Check the
response and the application's normal error handling. A successful import,
configuration parse, or process exit is not proof that the request used the
Gateway.

If the project has a read-capable Logfire login, query the exact project for a
fresh Gateway request or open its Gateway activity view. Do not use the
send-only SDK write token for reads. If read verification is unavailable,
report the request response as verified and mark Logfire-side arrival as
unverified.

Run the project's formatter, type checker, build, and focused tests. Report the
exact provider, route, model, endpoint class, credential variable, request
result, and any unverified Logfire-side check. Never claim that a Gateway
request was observed when this session did not check it.

After setup, return the relevant project or Gateway view link. If provider
activation or another prerequisite prevents the request, report that blocker
and mark application inference as unverified.

## References

- [Pydantic AI Gateway documentation](https://pydantic.dev/docs/ai/overview/gateway/)
