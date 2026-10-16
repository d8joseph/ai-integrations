# Inspection before implementation

Inspected 2026-10-15. PR #33 is open/draft, branch
`osamastro7-droid/ai-integrations:claude-agent-sdk`, head
`4f791d03c2a03a80b109939d8425ec34e1989783` (not the synthetic merge SHA).
Source: https://github.com/temporalio/ai-integrations/pull/33

1. The head above is the integration target; do not follow the moving branch.
2. `python/claude_agent_sdk/src/temporalio/claude_agent_sdk/_activity.py`,
   `make_tool_step_activity`, defines `run_claude_tool_step`.
3. It takes one `ToolStepInput` dataclass, defined in `_models.py`: session_id,
   checkpoint, call, tools, builtin_tools, conversation, transcript, attempt, retry.
4. `step.call` is `DeferredCall(id, name, input, kind)`; name is a string.
5. `step.call.input` is the exact `dict[str, Any]` reported by the segment.
6. The body calls `runner.run_tool_step(step, activity.info().attempt)`. The real
   runner either releases a matching warm engine call or replays that exact call
   through Claude Code. This is after Activity inbound interception. A segment
   pauses engine tools matching `tool_activities`; do not enable other effectful
   tools inside model segments. Ordinary `activity_as_tool` tools instead run as
   their own named Activities, not through this envelope.
7. Temporal `Interceptor.intercept_activity` builds an
   `ActivityInboundInterceptor`; its `execute_activity` runs before dispatch to
   the body. Tenuo uses that hook, including local-activity protection.
8. Tenuo derives names from `@tool`, `tool_mappings`, or the Activity type. Args
   come from signing headers/signature mapping, a dict, or positional fallback.
   Outbound workflow interception signs the matching name/args with holder PoP.
9. It can protect the envelope as one operation today, but cannot express the
   requested per-logical-tool constraints with its current static mapping alone.
10. Needed mapping: Activity type selects a pure resolver; `step.call.name` is
    the warrant tool, `step.call.input` the warrant arguments. Both signing and
    verification must resolve identically, without trusting a header to choose
    what the Worker authorizes. Prefer a generic resolver over Claude core code.
11. Tenuo wraps authorization errors as non-retryable Temporal ApplicationError;
    its stable type preserves the authorization error code. Temporal delivers
    ActivityError with that ApplicationError as cause to the workflow.
12. `_workflow._failed_step` recognizes only `ToolCallNotRun` as definitely not
    run. `ToolCallInterrupted`, timeouts, Worker loss, and all other types are
    interrupted/may-have-run. `_may_try_again` requires `ToolCallNotRun` plus
    first detail `{"try_again": true}`. The Activity body normally adds this
    retry decision, so interceptor failures bypass that enrichment.
13. Existing abstractions: DeferredCall, ToolOutcome, ToolStepRetry,
    ToolCallNotRun/ToolCallInterrupted, and workflow approvals. There is no
    external policy-result abstraction. The PR lists Tool Policy API as open.

Existing Tenuo sources inspected: `tenuo/temporal/{_config,_interceptors,
_decorators,_pop,_headers,_dedup}.py`, `tenuo/temporal_plugin.py`, Temporal docs,
and existing adapter/e2e tests. Reuse the warrant builder, Range constraint,
chain verification, PoP, audit, deduplication, and plugin registration.
