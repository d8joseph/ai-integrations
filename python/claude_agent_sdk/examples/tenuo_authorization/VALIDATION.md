# Validation

Pinned draft #33 commit: `4f791d03c2a03a80b109939d8425ec34e1989783`.
The example branch leaves the upstream plugin implementation unchanged. Tests
compare the installed plugin Python files against the checkout being tested.

## Reproduce

```bash
cd python/claude_agent_sdk/examples/tenuo_authorization
bash run_e2e.sh
```

The runner installs released `tenuo==0.3.2`, `temporalio==1.34.0`,
`claude-agent-sdk==0.2.153`, and a non-editable build of the checked-out plugin
into an isolated environment. It starts a local Temporal server and exercises
the real Claude SDK / bundled Claude Code engine against the upstream deterministic
Messages API simulator. No hosted model credentials are required.

The last complete run used Apple Silicon macOS, Python 3.14.7 and Temporal CLI
1.9.1 / dev server 1.32.0: **32 passed, no skips or warnings**, followed by a
successful standalone lifecycle trace. The runner prints a fresh artifacts directory containing
`results.xml`, `server.log`, and the actual `lifecycle.log` trace.

## Coverage

The example tests prove exact logical-call authorization, zero implementation
invocations on denial, preserved non-retryable failure types, the result actually
sent back to the model, forced redelivery and first-attempt replay suppression.

The lifecycle runs with both ScriptedClaude and the real SDK/engine: read and
refund 25 succeed; refund 250 is denied before and after approval alone; a new
controller-issued warrant permits 250. Tests also cover old-warrant denial,
resource/task scope, expiry, untrusted self-issuance, strict fresh proofs, rejection
of caller-proof laundering and Worker restart after authority replacement.
Serialized history is checked for accidental issuer/holder private-key exposure.
Repeated approval/authority Updates cannot overwrite an earlier decision, and
execution-time proof mode rejects wider retry-verification windows.

## Upstream compatibility

The unchanged upstream implementation's locked Python 3.12.14 suite finished
with **758 passed, 9 skipped, 1 failed** (768 collected). The failure was
`test_approval_by_signal_works_when_subscribers_use_up_the_update_limit[store]`:
it expected `RPCError(RESOURCE_EXHAUSTED)` but received
`WorkflowUpdateRPCTimeoutOrCancelledError`. Both held/store variants passed in
isolation (**2 passed**). No upstream code was changed to mask the failure.

The separate refusal-signal candidate passed the full locked upstream suite:
**763 passed, 9 skipped**, including four new signal tests. `make lint`,
`make build`, and the wheel/sdist conventions check passed.

## Limits

These are deterministic engine/Worker tests, not evaluations of a hosted model's
reasoning. Continue-As-New and distributed service exactly-once effects are not
claimed. LIFECYCLE.md documents the two proof trust models, expiry, credential
storage and renewal limitations. The baseline does not translate native Tenuo
failures into the separate candidate's refusal signal.
