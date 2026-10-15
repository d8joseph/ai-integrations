"""A durable Claude Agent SDK client for use in an Activity."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import claude_agent_sdk
from claude_agent_sdk import (
    ClaudeAgentOptions,
    Message,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    ToolPermissionContext,
)

from temporalio import activity
from temporalio.claude_agent_sdk._mcp import workflow_handle_mcp
from temporalio.claude_agent_sdk._workflow import (
    CHECKPOINT,
    NEXT_PROMPT,
    REQUEST_APPROVAL,
    Checkpoint,
    NextPrompt,
    ToolApproval,
    ToolApprovalRequest,
    answer_id,
)
from temporalio.client import WorkflowHandle
from temporalio.exceptions import ApplicationError

_PLUGIN_PATH = str(Path(__file__).parent / "temporal-mod")
# The mod's calls to the workflow_handle MCP server.
_ALLOWED_TOOLS = [
    "mcp__workflow_handle__replay",
    "mcp__workflow_handle__checkpoint",
]
# How Claude Code reports a turn it cancelled, such as one the mod aborted.
_ABORTED = ("aborted_streaming", "aborted_tools")


class ClaudeSDKClient(claude_agent_sdk.ClaudeSDKClient):
    """The SDK's ``ClaudeSDKClient``, with each model step and tool call checkpointed.

    Use it in an Activity of a Workflow that inherits :class:`ClaudeConversation`. A
    retried Activity replays the checkpoints instead of calling the model again.
    """

    def __init__(
        self,
        options: ClaudeAgentOptions | None = None,
        transport: Any | None = None,
    ) -> None:
        """Create the client. Must be called in an Activity."""
        self._handle = _workflow_handle("ClaudeSDKClient")
        self._workflow_errors: list[Exception] = []
        super().__init__(
            _with_temporal(
                options or ClaudeAgentOptions(), self._handle, self._workflow_errors
            ),
            transport,
        )

    async def __aenter__(self) -> ClaudeSDKClient:
        """Connect and return this client."""
        await super().__aenter__()
        return self

    async def run_conversation(
        self,
        *,
        poll_interval: timedelta = timedelta(seconds=1),
        on_message: Callable[[int, Message], None] | None = None,
    ) -> None:
        """Answer each message sent to the Workflow until the conversation ends.

        Args:
            poll_interval: How often to check the Workflow for the next message.
            on_message: Called with the message index and each message Claude sends.
                A retry calls it again for the turns it replays.
        """
        heartbeat = asyncio.create_task(_heartbeat())
        try:
            index = 0
            while (
                prompt := await _next_prompt(self._handle, index, poll_interval)
            ) is not None:
                await self.query(prompt)
                async for msg in self.receive_response():
                    if on_message is not None:
                        on_message(index, msg)
                    if isinstance(msg, ResultMessage):
                        await self._checkpoint_answer(index, msg)
                index += 1
        finally:
            heartbeat.cancel()

    async def _checkpoint_answer(self, index: int, result: ResultMessage) -> None:
        # A turn the mod could not checkpoint has a partial answer, so the attempt fails
        # and the retry produces the real one.
        if self._workflow_errors or result.terminal_reason in _ABORTED:
            raise ApplicationError(
                f"The turn for prompt {index} could not be checkpointed",
                type="CheckpointFailed",
            ) from (self._workflow_errors[0] if self._workflow_errors else None)
        # A turn that ended in an error, such as reaching max_turns, has no result.
        answer = result.result
        if answer is None:
            answer = "\n".join(result.errors or [])
        await self._handle.signal(CHECKPOINT, Checkpoint(answer_id(index), answer))


async def workflow_approval(
    tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
) -> PermissionResult:
    """Let the Workflow's ``approve_tool`` Update decide a tool call.

    Use it as ``ClaudeAgentOptions(can_use_tool=workflow_approval)``. The call is listed
    in ``pending_approvals`` until it is decided.
    """
    assert context.tool_use_id is not None, "the CLI always sends a tool_use_id"
    request = ToolApprovalRequest(
        tool_use_id=context.tool_use_id,
        tool_name=tool_name,
        tool_input=tool_input,
        title=context.title,
    )
    approval = await _workflow_handle("workflow_approval").execute_update(
        REQUEST_APPROVAL,
        request,
        id=f"approval-{context.tool_use_id}",
        result_type=ToolApproval,
    )
    if approval.approved:
        return PermissionResultAllow()
    return PermissionResultDeny(message=approval.reason or "Tool use denied")


def _workflow_handle(caller: str) -> WorkflowHandle[Any, Any]:
    workflow_id = activity.info().workflow_id
    if workflow_id is None:
        raise RuntimeError(f"{caller} must run in an Activity")
    return activity.client().get_workflow_handle(workflow_id)


def _with_temporal(
    options: ClaudeAgentOptions,
    handle: WorkflowHandle[Any, Any],
    errors: list[Exception],
) -> ClaudeAgentOptions:
    if not isinstance(options.mcp_servers, dict):
        raise ValueError(
            "ClaudeSDKClient needs mcp_servers as a dict, to add the server the mod uses"
        )
    return dataclasses.replace(
        options,
        mcp_servers={
            **options.mcp_servers,
            "workflow_handle": workflow_handle_mcp(handle, errors),
        },
        plugins=[*options.plugins, {"type": "local", "path": _PLUGIN_PATH}],
        allowed_tools=[*options.allowed_tools, *_ALLOWED_TOOLS],
    )


async def _heartbeat() -> None:
    timeout = activity.info().heartbeat_timeout
    if not timeout:
        return
    while True:
        activity.heartbeat()
        await asyncio.sleep(timeout.total_seconds() / 2)


async def _next_prompt(
    handle: WorkflowHandle[Any, Any], index: int, poll_interval: timedelta
) -> str | None:
    """Wait for prompt ``index``, or return ``None`` once the conversation has ended."""
    while True:
        next_prompt = await handle.query(NEXT_PROMPT, index, result_type=NextPrompt)
        if next_prompt.prompt is not None:
            return next_prompt.prompt
        if next_prompt.ended:
            return None
        await asyncio.sleep(poll_interval.total_seconds())
