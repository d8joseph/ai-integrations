# External authorization under durable Claude tool execution

This review example builds directly on draft [#33](https://github.com/temporalio/ai-integrations/pull/33),
base commit **`4f791d03c2a03a80b109939d8425ec34e1989783`**. It composes released
Tenuo 0.3.2 underneath the plugin's existing `run_claude_tool_step` Activity.
Neither Tenuo's package nor Temporal's baseline execution code is changed.

```text
Claude Agent SDK
      |
      v
Temporal durable tool Activity
      |
      v
Tenuo Worker interceptor
      |
      +--> warrant permits exact tool + args --> run
      |
      +--> warrant denies --------------------> no side effect
```

Temporal owns durable execution, retries, Worker recovery, workflow state and
approval/waiting. Tenuo decides whether the agent is authorized to make this
exact call, including its argument constraints, before execution. Authorization
composes with Temporal approvals; it does not replace them.

## Review and run

Branch `poc/tenuo-activity-authorization` contains the external example and tests
on top of the pinned #33 base. The separate `poc/tenuo-policy-refusal` branch
adds only a generic refusal signal and regression tests. No PR is opened by
this example.

From a checkout of the example branch, with Python 3.10+, `uv` and the Temporal
CLI available:

```bash
cd python/claude_agent_sdk/examples/tenuo_authorization
bash run_e2e.sh
```

The script installs **this checkout's plugin**, non-editable, into a temporary
environment with the example dependencies. It starts a temporary Temporal dev
server, then runs all example tests, including the real Claude SDK and bundled
Claude Code against #33's deterministic local fake Messages API. No model
credentials are needed. It stops the server on exit and preserves server logs
and JUnit results in the printed artifacts directory. The default port is 17333;
set `TEMPORAL_DEMO_PORT` if that port is already in use.

For just the server-free boundary tests:

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt ../..
.venv/bin/python -m pytest -q tests/test_boundary.py tests/test_setup.py tests/test_pin.py
```

For the scripted demo with an already running Temporal server:

```bash
.venv/bin/python run_demo.py --address localhost:7233
```

`run_demo.py --live` uses real Claude authentication and a real model. The
fake-model test is the repeatable evidence for the four exact calls. To test
another Claude Code version, set `CLAUDE_CLI_PATH` for the SDK test; otherwise it
uses the pinned SDK's bundled executable.

## Four cases

The fake shop records every implementation invocation before doing any work,
and stores refunds in a separate ledger. A ten-minute task warrant grants:

```python
get_order: order_id=Exact("order-123")
issue_refund: order_id=Exact("order-123"), amount=Range(min=0, max=100)
```

| Case | Logical request | Implementation invocations | Current result |
|---|---|---|---|
| A | `get_order("order-123")` | 1 | normal result |
| B | `issue_refund("order-123", 25)` | 1 | normal result; one refund |
| C | `issue_refund("order-123", 500)` | 0 | denied; Claude outcome says “may have run” |
| D | `delete_order("order-123")` | 0 | denied; Claude outcome says “may have run” |

`delete_order` is available on the MCP server but absent from the warrant, so D
tests authorization rather than an unknown-tool lookup. No tool contains a
model-side policy check.

## The small composition adapter

Claude names the MCP operation `mcp__shop__issue_refund`. The example-local
[adapter](adapter.py) extracts `step.call.name` and `step.call.input`, removes
only this known transport prefix, and checks `issue_refund` with the exact
arguments. Authorizing only the shared Activity name would not enforce this.

Outbound proof signing and inbound authorization use the same pure mapping.
The adapter delegates chain verification, PoP verification, authorization,
audit and replay suppression to the existing Tenuo Activity interceptor. The
original `ToolStepInput` reaches the Activity unchanged. Each authorization
uses a per-call config, avoiding shared mutable tool mappings.

Built-in and ordinary durable tools are disabled. Every shop MCP tool matches
`tool_activities=["mcp__shop__*"]`, ensuring it pauses for its own tool Activity.
The model-segment capability rejects inputs that violate those settings.

The adapter uses private APIs from pinned Tenuo 0.3.2, isolated in one file.
This is PoC composition code, not a new supported plugin API. The demo uses
process-local replay suppression; distributed deployments need the existing
shared-store option and service-side idempotency.

## Current evidence and the upstream gap

The full suite passes with installed Tenuo 0.3.2, a real Temporal server,
and the real Claude SDK / bundled Claude Code engine against the deterministic
upstream local model simulator. Denied calls never enter the implementation.
This is an engine/Worker end-to-end test, not a paid hosted-model run.
See the exact counts, environment and artifacts in [VALIDATION.md](VALIDATION.md).

Today the Worker denial is non-retryable, but #33 classifies its error type as
an interrupted call. Existing `ToolCallNotRun` can already convey non-execution;
its result says “You can call it again,” which does not express terminal policy
refusal. The separate candidate branch introduces a vendor-neutral
`ToolCallRefused` signal. See [the technical note](TECHNICAL_NOTE.md) and
[validation record](VALIDATION.md).

## Authority lifecycle

Temporal controls when work runs and how it survives failure. Tenuo controls what authority the agent has when that work runs.

Human approval may cause new authority to be issued, but approval and authority are separate concepts.

The additional [lifecycle scenario](LIFECYCLE.md) demonstrates a controller-issued,
ten-minute warrant tied to the Workflow and exact order. It runs read/refund 25,
denies refund 250, waits for a Temporal approval Update, and proves approval alone
still cannot authorize 250. Only a newly signed controller warrant permits the
retry. Tests restart the Worker after replacement and check that private keys
never appear in serialized history. Run it with an existing server/environment:

```bash
python run_lifecycle.py --address localhost:7233 --restart
```

The lifecycle uses a separate, explicit Worker-held identity mode that creates
fresh proofs at Activity ingress. The baseline preserves schedule-time proofs
and their queue-delay limitation. [LIFECYCLE.md](LIFECYCLE.md) explains both trust
models, expiry, retries, long approval waits, and production recommendations.
