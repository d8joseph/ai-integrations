# Enforcement and refusal semantics

## Versions and evidence

- Temporal PR: [#33](https://github.com/temporalio/ai-integrations/pull/33), open draft.
- Head branch: `osamastro7-droid/ai-integrations:claude-agent-sdk`.
- Exact source exercised: `4f791d03c2a03a80b109939d8425ec34e1989783`.
- PoC branch: `poc/tenuo-activity-authorization`, based directly on #33 head
  `4f791d03c2a03a80b109939d8425ec34e1989783`. Baseline changes live in this example.
- Tenuo package: released 0.3.2, tested from its installed distribution, without
  loading this checkout's Python package. No plugin source/API changes remain.
- Test environment: Python 3.14.7, Temporal SDK 1.34.0, installed Tenuo 0.3.2,
  installed Claude SDK 0.2.153 and its bundled Claude Code, Temporal CLI 1.9.1 /
  dev server 1.32.0. A conventional clone fetched the pinned PR commit.
- [Validation](VALIDATION.md) records the full server/Worker/real-engine runs.
  The model endpoint is the upstream deterministic local simulator; no hosted
  model credentials are required. The upstream execution code remains unchanged.

[INSPECTION.md](INSPECTION.md) records the thirteen pre-implementation answers.

## Enforcement boundary

In the pinned upstream `python/claude_agent_sdk/src/temporalio/claude_agent_sdk/`:

- `_models.ToolStepInput.call` is `DeferredCall(id, name, input, kind)`.
- `_activity.make_tool_step_activity` defines `run_claude_tool_step(step)`.
- Its body calls `runner.run_tool_step(step, activity.info().attempt)`.
- `_runner.ClaudeAgentSdkRunner.run_tool_step` releases a matching warm call or
  runs the exact recorded call in Claude Code. It must never be reached on denial.

The example's `CallWorkerInterceptor`, a subclass of Tenuo's existing Worker
interceptor, installs an Activity adapter. Its local resolver is selected
by Activity type and reads the original decoded argument, then existing Tenuo
logic verifies the warrant chain, holder PoP and argument constraints. Only
after authorization and deduplication does it call the next interceptor/body.

The example maps `run_claude_tool_step` →
`step.call.name.removeprefix("mcp__shop__")`, `step.call.input`. The same mapping
is used for outbound proof signing. Neither a static authorization for the
dispatcher nor a constraint on the envelope itself would enforce `amount`.
The adapter creates a per-call config using the existing static `tool_mappings`
option and supplies the extracted arguments to the existing inbound interceptor.
Shared Worker config is never mutated. Its outbound interceptor signs the same
logical call with `Warrant.sign`. This small amount of bridge code is contained
in `adapter.py`; it depends on private APIs in the pinned release. There is no
new core extension point or released-plugin modification.

The step's session/checkpoint/conversation control replay, while the call name
and every logical tool argument determine authorization. In this shop, identity
and amount are entirely in the arguments. Tools whose authority depends on a
workspace, account, or other ambient state must include that context in their
authorization mapping too.

## Exact current denial path

```text
Tenuo inbound authorization fails (no next/body dispatch)
  → ApplicationError(non_retryable=True)
      type="constraint_violation"    (refund 500)
      type="tool_not_authorized"     (delete_order)
  → Temporal failure converter / ActivityError.cause
  → DurableClaudeAgent._execute_tool catches ActivityError
  → _failed_step does not recognize either type as ToolCallNotRun
  → tool_calls status="interrupted"
  → ToolOutcome(is_error=True, content=<below>)
  → next segment injects this outcome into Claude's conversation
```

The exact content produced by the unchanged classifier in the boundary tests:

```text
This call was interrupted (its step failed), so it may have run, in full or in part. Check its effects before you run it again.
```

The boundary tests serialize and restore the failure before calling the real
classifier. Actual injection into the real engine's model request is also verified
by the passing SDK test. In both denied boundary cases, the Activity-body
counter is zero, the implementation invocation list is empty, and no refund
exists. A forced attempt 2 is denied again. The existing failure is
non-retryable, and #33's `_may_try_again` returns false.

## What already works, and the smallest remaining gap

There is already a vendor-neutral public wire signal: an `ApplicationError` with
type `ToolCallNotRun`. An interceptor that knows it refused before dispatch can
raise that type with `non_retryable=True`. It does **not** need the Activity
body's retry enrichment to stop: absent `{"try_again": true}`, `_may_try_again`
returns false. A test proves this existing signal yields `status="not run"`.
So no new pre-execution hook is required just to communicate non-execution.

However, that signal currently produces:

```text
This call did not run: its step failed before the call could start. You can call it again.
```

That describes a setup failure, not a terminal policy refusal. The default
Tenuo plugin also preserves its own error codes, so it does not emit the Claude
integration's signal automatically. A composition adapter must deliberately
translate a proven pre-dispatch denial; catching arbitrary tool errors after
dispatch and relabeling them is unsafe.

The separate minimal proposal adds `TOOL_CALL_REFUSED = "ToolCallRefused"`,
another known Temporal ApplicationError type, and one `_failed_step` branch.
External policy middleware emits it with `non_retryable=True`, only before
entering the tool implementation. The workflow reports `status="refused"` and:

```text
This call was refused by policy before execution and did not run. Do not retry it.
```

The message gives a safe public reason without leaking an engine's private
exception text. Detailed reasons remain in Temporal history. No callback,
policy SDK, warrant, or vendor import is needed. `_may_try_again` already ignores
this new type; Temporal obeys its non-retryable flag even for repeatable tools.
This is a trusted Worker contract, not a way to infer that any arbitrary error
means no effects occurred. Worker loss, timeout, interruption, ordinary errors,
and existing retryable `ToolCallNotRun` behavior remain unchanged.

The `poc/tenuo-policy-refusal` candidate branch and its four wire/classification
regression tests are separate from the baseline example branch. A broader Tool Policy API could later use this same failure contract;
it is unnecessary to introduce that larger API for this example.

## Authority lifecycle and proof freshness

The baseline keeps the released schedule-time PoP semantics. The additional
lifecycle uses issued public warrant headers as Workflow state and Temporal
Updates for approval and controller replacement. No issuing key enters Workflow
code or history. A separate example-local execution-proof interceptor supports
trusted Worker-held identity with fresh, strict PoP at actual execution time;
it does not refresh arbitrary caller proofs. Expired task authority still fails.
The signed Workflow ID and exact resource constraints bind the replacement.
Tests exercise approval-only denial, replacement, old-warrant denial, expiry,
untrusted self-issuance, and replay after Worker restart. See [LIFECYCLE.md](LIFECYCLE.md)
for transport alternatives, the scheduling-delay limitation, and the trust change
required to move signing to execution time. No released Tenuo changes are needed.
