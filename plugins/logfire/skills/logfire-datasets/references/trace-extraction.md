# Extracting Dataset Cases From Logfire Traces

Shape the SQL so that **one row is one case**. Always select `trace_id` and `span_id`:
they become `source_trace_id` / `source_span_id` (the case links back to its trace in
the UI) and give each case a stable name.

Engine rules that matter here (Apache DataFusion, not Postgres):

- `attributes->>'key'` returns text; `attributes->'key'` returns a JSON subtree. Chain
  for nesting: `attributes->'a'->0->>'b'`. Never `json_extract*`.
- Dotted attribute names are single keys: `attributes->>'gen_ai.agent.name'`.
- List an attribute's keys with `json_object_keys(attributes)`; array length with
  `json_length(attributes->'key')`.
- Filter on `span_name`, `service_name`, `otel_scope_name`, `trace_id` and the time
  window first; they are indexed.
- The window is at most 14 days per query. For more history, run several windows and
  merge (dedupe on `span_id`).
- A JSON subtree selected with `->` may arrive as a JSON-encoded string. Decode it with
  `json.loads` in the script before mapping.

## Discovery Sequence

Run these before writing the extraction query. Never guess attribute names.

```sql
-- 1. What produces spans? Pick the span that represents one unit of work.
SELECT span_name, otel_scope_name, service_name, count(*) AS n
FROM records
WHERE kind = 'span'
GROUP BY span_name, otel_scope_name, service_name
ORDER BY n DESC
LIMIT 30

-- 2. Which attributes does that span carry, and how often are they populated?
SELECT json_object_keys(attributes) AS keys
FROM records
WHERE span_name = '<chosen span>'
ORDER BY start_timestamp DESC
LIMIT 3

SELECT count(*) AS n,
       count(*) FILTER (WHERE attributes->>'<candidate key>' IS NOT NULL) AS populated
FROM records
WHERE span_name = '<chosen span>'
LIMIT 1

-- 3. Look at a few truncated values to learn the JSON shape.
SELECT trace_id, span_id, substr(attributes->>'<key>', 1, 400) AS sample
FROM records
WHERE span_name = '<chosen span>'
ORDER BY start_timestamp DESC
LIMIT 3
```

## Pydantic AI Agents (otel scope `pydantic-ai`)

| Span | `span_name` | Useful attributes |
|------|-------------|-------------------|
| Agent run (root of a run) | `invoke_agent <agent name>` (older versions: `agent run`) | `pydantic_ai.all_messages` (full conversation, JSON array), `final_result` (text of the final output), `gen_ai.system_instructions`, `gen_ai.agent.name`, `gen_ai.aggregated_usage.input_tokens` / `output_tokens` |
| Model request | `chat <model>` | `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.request.model`, `gen_ai.tool.definitions` |
| Tool call | `execute_tool <tool name>` | `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result` |

Each message is `{"role": "user" | "assistant" | ..., "parts": [{"type": "text", "content": "..."}, ...]}`.
If those attributes are missing, the app may have set `include_content=False` on
instrumentation; there is no content to extract.

```sql
-- Agent runs: user prompt -> final output
SELECT trace_id, span_id,
       attributes->'pydantic_ai.all_messages'->0->'parts'->0->>'content' AS prompt,
       attributes->>'final_result' AS final_result,
       attributes->>'gen_ai.agent.name' AS agent_name
FROM records
WHERE otel_scope_name = 'pydantic-ai'
  AND span_name LIKE 'invoke_agent%'
  AND attributes->>'final_result' IS NOT NULL
ORDER BY start_timestamp DESC
LIMIT 200
```

`->0` is the first message. For a multi-turn conversation that is the first user turn, not
the latest one: select the whole `pydantic_ai.all_messages` array and pick the last
`role = 'user'` message in Python instead.

```sql
-- Tool calls: arguments -> result, for testing one tool in isolation
SELECT trace_id, span_id,
       attributes->>'gen_ai.tool.call.arguments' AS arguments,
       attributes->>'gen_ai.tool.call.result' AS result
FROM records
WHERE otel_scope_name = 'pydantic-ai'
  AND span_name = 'execute_tool <tool name>'
ORDER BY start_timestamp DESC
LIMIT 200
```

### Sub-agent runs

An agent that delegates work produces nested `invoke_agent` spans. To select only the
delegated runs (for example, reviews a coordinator handed off), join each run to its parent
span. Use the delegating tool's span name as it appears in the discovery inventory:

```sql
SELECT r.trace_id, r.span_id,
       r.attributes->'pydantic_ai.all_messages'->0->'parts'->0->>'content' AS task,
       r.attributes->>'final_result' AS result
FROM records r
JOIN records p ON p.span_id = r.parent_span_id AND p.trace_id = r.trace_id
WHERE r.otel_scope_name = 'pydantic-ai'
  AND r.span_name LIKE 'invoke_agent%'
  AND p.span_name = 'execute_tool delegate_task'
LIMIT 200
```

## Coding-Agent Sessions (Claude Code, Codex)

Recorded coding-agent sessions have one `invoke_agent` span per user turn under a `session`
root, with `service_name` `claude` or `codex`:

| Attribute | Content |
|-----------|---------|
| `gen_ai.input.messages` | The user turn (JSON message array) |
| `gen_ai.output.messages` | The agent's final reply for that turn |
| `gen_ai.conversation.id` | Session id, for grouping turns |
| `thirdeye.repo`, `thirdeye.cwd` | Repository name and working directory |
| `thirdeye.turn.status` | For example `completed` |

Codex turns begin with the repository's whole AGENTS.md, so a keyword filter on the full
input matches almost every turn. Filter on the end of the input instead, for example
`right(attributes->>'gen_ai.input.messages', 1500) ILIKE '%review%'`, and confirm by reading
a few prompt tails.

## Other Instrumented Code

- **`@logfire.instrument` / `logfire.span`**: function arguments are attributes named after
  the parameters (`attributes->>'question'`). The return value is in `attributes->'return'`
  only when the decorator used `record_return=True`.
- **OpenAI / Anthropic instrumentation**: request/response content is on the LLM span.
  Run the discovery sequence for that span; key names vary by integration version.
- **HTTP endpoints**: `http_route`, `http_method` and `http_response_status_code` are flat
  columns. Request bodies exist only if the app recorded them as attributes.

## Scrubbed Values

Logfire's scrubber replaces values that match sensitive patterns at ingest, leaving text like
`[Scrubbed due to 'auth']` or `[Scrubbed due to 'secret']`. Prose that only discusses auth or
secrets is often caught too, such as code reviews of auth code. The original content cannot be
recovered. During discovery, count how many candidate rows are scrubbed
(`attributes->>'final_result' LIKE '[Scrubbed%'`), drop them when mapping, and tell the user
how many were lost. Fixing it for future traces means changing the app's scrubbing options;
that is the user's call.

## Choosing Rows

| Goal | Filter |
|------|--------|
| Regression set ("golden" behaviour) | Successful runs: `NOT is_exception`, `final_result IS NOT NULL`, optionally `otel_status_code` not `'ERROR'` |
| Failure set | `is_exception` on the run span, or join to child spans that errored on `trace_id` |
| Coverage across inputs | Sample instead of `ORDER BY start_timestamp DESC`: `ORDER BY random()`, or one row per bucket (`row_number() OVER (PARTITION BY <attr> ORDER BY start_timestamp DESC) = 1` in a CTE) |
| One environment only | `deployment_environment = 'production'` |
| Dedupe repeated prompts | `GROUP BY` the prompt expression and take `max(trace_id)`, `max(span_id)`, or dedupe in Python |

Correlated subqueries, `EXISTS` and `IN (SELECT ...)` often fail to plan. Use a CTE plus
`JOIN` instead.
