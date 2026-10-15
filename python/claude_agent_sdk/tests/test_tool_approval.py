"""A tool call that needs approval waits in the Workflow until a caller decides it."""

import asyncio

import pytest
from claude_agent_sdk import (
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from temporalio import activity
from temporalio.claude_agent_sdk import (
    ToolApproval,
    ToolApprovalRequest,
    workflow_approval,
)
from temporalio.claude_agent_sdk._workflow import REQUEST_APPROVAL
from temporalio.client import (
    Client,
    WorkflowFailureError,
    WorkflowHandle,
    WorkflowUpdateFailedError,
    WorkflowUpdateStage,
)
from temporalio.exceptions import ApplicationError
from tests.app.workflow import AgentWorkflow
from tests.helpers.claude import StandInClaude, start_agent_workflow

LS = ToolApprovalRequest(
    tool_use_id="t1", tool_name="Bash", tool_input={"command": "ls"}
)

decisions: list[PermissionResult] = []


async def ask_for_ls() -> PermissionResult:
    context = ToolPermissionContext(tool_use_id=LS.tool_use_id)
    return await workflow_approval(LS.tool_name, LS.tool_input, context)


async def pending(
    handle: WorkflowHandle[AgentWorkflow, None],
) -> list[ToolApprovalRequest]:
    """The pending approvals, once there is at least one."""
    while not (requests := await handle.query(AgentWorkflow.pending_approvals)):
        await asyncio.sleep(0.05)
    return requests


@activity.defn(name="run_claude")
async def ask_then_fail_once() -> None:
    """Asks to run ``ls``, and fails after the decision on the first attempt."""
    decisions.append(await ask_for_ls())
    if activity.info().attempt == 1:
        raise RuntimeError("lost the Worker")
    await StandInClaude(lambda _: "done").run_conversation()


@pytest.mark.parametrize(
    ("approval", "decision"),
    [
        (ToolApproval("t1", approved=True), PermissionResultAllow()),
        (
            ToolApproval("t1", approved=False, reason="no"),
            PermissionResultDeny(message="no"),
        ),
        (
            ToolApproval("t1", approved=False),
            PermissionResultDeny(message="Tool use denied"),
        ),
    ],
)
async def test_a_retry_gets_the_decision_without_asking_again(
    client: Client, approval: ToolApproval, decision: PermissionResult
) -> None:
    decisions.clear()
    async with start_agent_workflow(client, ask_then_fail_once) as handle:
        assert await pending(handle) == [LS]
        await handle.execute_update(AgentWorkflow.approve_tool, approval)
        await handle.execute_update(AgentWorkflow.end_conversation)
        await handle.result()
        left = await handle.query(AgentWorkflow.pending_approvals)

    assert decisions == [decision, decision]
    assert left == []


@activity.defn(name="run_claude")
async def ask_then_finish() -> None:
    """Asks to run ``ls``, then answers messages."""
    decisions.append(await ask_for_ls())
    await StandInClaude(lambda _: "done").run_conversation()


async def test_a_decision_on_a_call_not_waiting_for_one_is_rejected(
    client: Client,
) -> None:
    decisions.clear()
    async with start_agent_workflow(client, ask_then_finish) as handle:
        await pending(handle)
        with pytest.raises(WorkflowUpdateFailedError) as unknown:
            await handle.execute_update(
                AgentWorkflow.approve_tool, ToolApproval("t2", approved=True)
            )
        await handle.execute_update(
            AgentWorkflow.approve_tool, ToolApproval("t1", approved=True)
        )
        with pytest.raises(WorkflowUpdateFailedError) as repeated:
            await handle.execute_update(
                AgentWorkflow.approve_tool, ToolApproval("t1", approved=False)
            )
        await handle.execute_update(AgentWorkflow.end_conversation)
        await handle.result()
        left = await handle.query(AgentWorkflow.pending_approvals)

    assert decisions == [PermissionResultAllow()]
    assert left == []
    for err in (unknown, repeated):
        assert isinstance(err.value.cause, ApplicationError)
        assert err.value.cause.type == "NotWaitingForApproval"


@activity.defn(name="run_claude")
async def ask_then_fail_for_good() -> None:
    """Leaves a request for approval waiting, and fails without retries."""
    workflow_id = activity.info().workflow_id
    assert workflow_id is not None
    await (
        activity.client()
        .get_workflow_handle(workflow_id)
        .start_update(
            REQUEST_APPROVAL,
            LS,
            id="approval-t1",
            wait_for_stage=WorkflowUpdateStage.ACCEPTED,
        )
    )
    raise ApplicationError("broken", non_retryable=True)


async def test_a_request_waiting_when_the_run_ends_is_failed(client: Client) -> None:
    async with start_agent_workflow(client, ask_then_fail_for_good) as handle:
        with pytest.raises(WorkflowFailureError):
            await handle.result()
        with pytest.raises(WorkflowUpdateFailedError) as err:
            await handle.get_update_handle("approval-t1").result()

    assert isinstance(err.value.cause, ApplicationError)
    assert err.value.cause.type == "ConversationEnded"
