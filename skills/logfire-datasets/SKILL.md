---
name: logfire-datasets
description: Create, populate, or update hosted Logfire datasets (Datasets & Experiments) from production traces, spans, logs, query results, or existing case files. Use when the user asks to "create a dataset in Logfire", "build an eval dataset from traces", "turn production runs into test cases", "add cases to a Logfire dataset", "push a dataset to Logfire", "sync cases from code", or "list/fetch hosted datasets". The Logfire MCP server has no dataset tools; this skill uses an API key and the Python SDK. To run evaluations or experiments against a dataset, use logfire-evals.
---

# Create Logfire Datasets

## How This Works

A hosted dataset is a named collection of cases in one Logfire project. Each case has
`inputs` (required), and optional `expected_output`, `metadata`, `name`, `tags`, and a
link to the trace it came from. Experiments (run with `pydantic_evals`) read these cases.

The Logfire MCP server can query telemetry but **cannot read or write datasets**. Dataset
work goes through `logfire.experimental.api_client` (install `logfire[datasets]`),
authenticated with a Logfire **API key**. Neither the MCP OAuth session nor a
`LOGFIRE_TOKEN` write token can be used for it.

The workflow is: configure a key, agree on the case shape, discover the source data, write
a retrieval and upload script for the project, dry-run it, push, verify.

## Step 1: Configure the API Key

| Operation | Scope on the key |
|-----------|------------------|
| Query traces with the SDK (`LogfireQueryClient`) | `project:read` on the project holding the traces |
| List / fetch datasets and cases | `project:read_datasets` |
| Create / update datasets, add / update / delete cases | `project:write_datasets` |

The dataset is created in **the project the key belongs to**. A key never spans projects.
When the traces and the dataset are in one project, one key with all three scopes is
enough. When they differ, use a second key with `project:read` for the query side.

1. Check what is already set without printing secrets:
   `python3 -c "import os; print({k: bool(os.environ.get(k)) for k in ['LOGFIRE_API_KEY', 'LOGFIRE_SOURCE_API_KEY']})"`.
   Also check the project's `.env` for an existing `LOGFIRE_API_KEY=` line.
2. If no key exists, ask the user to create one in the target project under
   **Settings > API Keys** with the scopes above, then export it as `LOGFIRE_API_KEY` or add it to a
   gitignored `.env`. Never ask the user to paste the key into chat, never echo it, and never
   write it into a tracked file.
3. The region (US/EU) is inferred from the key. Set `LOGFIRE_BASE_URL` only for self-hosted
   Logfire.
4. Add `logfire[datasets]` to the project's dependencies (for example
   `uv add 'logfire[datasets]'`).
5. Verify the key before writing anything else. Run a trivial query with `LogfireQueryClient`
   (proves `project:read`) and call `LogfireAPIClient.list_datasets()` (proves
   `project:read_datasets`). Both are read-only. A `401` means the key is wrong or for another
   region; a `403` names the missing scope. `project:write_datasets` is only proven by the
   first push. Build this into the script as a check stage (Step 4) so it can be re-run.

## Step 2: Agree on the Case Shape

Before querying, settle with the user what the dataset is for, because that decides what goes
where:

- **`inputs`**: exactly what the task under test will receive (for an agent, usually the user
  prompt plus any context it depends on).
- **`expected_output`**: a reference answer, or omit it. A production output is only a correct
  reference if someone has reviewed it. Unreviewed outputs are better kept in `metadata`
  (for example `metadata.observed_output`) for regression checks.
- **`metadata`**: anything for slicing results later (agent name, environment, model, customer
  tier).

Use **JSON objects** for `inputs`, `expected_output`, and `metadata`. The hosted API rejects a
bare scalar `expected_output` (`dict_type: Input should be a valid dictionary`). Wrap scalars,
for example `{"label": "positive"}`, and make the eval task compare against that field.

Pick a stable dataset name: it starts with a letter or digit and uses only letters, digits,
`.`, `_` and `-`. Names are unique per project. Reuse the name the eval code will use, because a
hosted and a code-defined dataset with the same name merge into one entry.

## Step 3: Discover the Source Data

Do the exploration with the MCP `query_run` tool if it is available (no API key needed).
Otherwise run each query through `LogfireQueryClient.query_json_rows()` with the API key (see
[references/python-scripts.md](references/python-scripts.md#retrieving-rows)).

Follow the discovery sequence in [references/trace-extraction.md](references/trace-extraction.md):

1. Inventory spans to find the one that represents one unit of work (one agent run, one tool
   call, one request).
2. List its attribute keys and check how often each is populated.
3. Sample a few truncated values to learn the JSON shape.

Never guess attribute names. For Pydantic AI the agent run span is `invoke_agent <name>` with
`pydantic_ai.all_messages` and `final_result`. The reference has the full table, plus patterns
for `@logfire.instrument` functions, tool calls, failure sets, and sampling.

Production traces can contain personal or customer data, and a dataset copies it into a
long-lived store. If inputs look like they hold PII or secrets, tell the user before pushing and
offer to redact or drop fields when mapping rows to cases.

## Step 4: Write and Dry-Run the Script

Write a Python script in the user's project, following its layout and tooling, that retrieves
the rows, maps them to cases, and uploads them. Read
[references/python-scripts.md](references/python-scripts.md) before writing it. It covers the
query and dataset clients, credentials, the stages the script needs, the case dict format,
local validation, and push behaviour.

The script must:

- read keys from the environment and fail clearly when one is missing;
- have a read-only check stage, a default dry run that writes a local preview, and write only
  behind an explicit flag such as `--push`;
- query a bounded window (at most 14 days per query) with SQL that returns one row per case,
  including `trace_id` and `span_id`;
- give every case a stable `name` (for example from `span_id`) so re-runs upsert instead of
  duplicating;
- create the dataset only if it is missing, then upload with `add_cases()` using dicts that
  carry `source_trace_id` / `source_span_id`. `push_dataset()` overwrites dataset-level
  evaluators and cannot set trace links;
- validate cases locally before any write, and print the hosted `case_count` at the end.

Run the check, then the dry run. Show the user the case count and two or three cases from the
preview, and **get explicit confirmation before pushing**. Pushing writes to a shared project.
Keep the preview file out of git; it contains trace content.

When the cases already exist in code (a `pydantic_evals.Dataset` or a `.yaml`/`.json` case
file) and there is no trace link to keep, `push_dataset()` on that dataset is the shorter path.
See the reference.

## Step 5: Verify

Never report a dataset as created or populated without checking it in this session.

1. Confirm the hosted `case_count` the script printed matches the preview. A lower count means
   names collided. A higher count means the dataset already held cases (neither upload path
   deletes cases).
2. Spot-check content with `list_cases(name)` or `get_dataset(name)`.
3. Point the user to **AI Evaluations > Datasets & experiments > Datasets** in the target
   project. Cases created with `source_trace_id` link back to their trace.

Close with the dataset name, project, hosted case count, the script path, and the exact command
to re-run it. To run an experiment over the dataset next, use logfire-evals.

## Limits

- 10,000 cases per dataset. Updating a named case does not consume capacity. Unnamed cases
  always count as new and duplicate on every re-run.
- An import that would exceed the limit is rejected entirely. Split large collections across
  datasets.
- Cases are validated against the dataset's JSON schemas on write. Schemas do not apply
  retroactively.
- Telemetry queries cover at most 14 days per call. For a longer history, query several
  windows and merge on `span_id`.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Trying MCP tools or `LOGFIRE_TOKEN` for dataset calls | Use an API key with the datasets scopes |
| Dataset appears in the wrong project | The key decides the project. Use a key from the target project |
| `dict_type: Input should be a valid dictionary` | Wrap scalar `expected_output` (or inputs/metadata) in an object |
| Duplicate cases after re-running | Give every case a stable `name` (for example from `span_id`) |
| Using unreviewed production output as `expected_output` | Put it in `metadata`, or have the user review it first |
| Overwriting evaluators configured in the UI | Use `add_cases()` for existing datasets, not `push_dataset()` |
| Expecting local deletions to propagate | Neither upload path deletes; call `delete_case()` explicitly |
| Guessing attribute names in SQL | Run the discovery queries first |
