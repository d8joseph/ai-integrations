"""The base class a Workflow inherits to hold a durable, multi-turn Claude conversation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from temporalio import workflow
from temporalio.contrib.workflow_streams import WorkflowStream
from temporalio.exceptions import ApplicationError

CHECKPOINT = "__temporal_claude_checkpoint"
REPLAY = "__temporal_claude_replay"
NEXT_PROMPT = "__temporal_claude_next_prompt"
REQUEST_APPROVAL = "__temporal_claude_request_approval"


@dataclass
class Checkpoint:
    """A model step's or tool call's result, sent by the mod under a deterministic ID."""

    id: str
    value: Any


def answer_id(index: int) -> str:
    """The checkpoint ID of the main loop's answer to prompt ``index``."""
    return f"answer-{index}"


@dataclass
class NextPrompt:
    """Prompt ``index`` if it has arrived, and whether the conversation has ended."""

    prompt: str | None
    ended: bool


@dataclass
class ToolApprovalRequest:
    """A tool call Claude is waiting to have approved, listed by ``pending_approvals``."""

    tool_use_id: str
    tool_name: str
    tool_input: dict[str, Any]
    title: str | None = None
    """Claude Code's prompt for the call, such as "Claude wants to read foo.txt"."""


@dataclass
class ToolApproval:
    """A decision on a tool call, sent with the ``approve_tool`` Update."""

    tool_use_id: str
    approved: bool
    reason: str = ""
    """Why the call was denied. Claude reads it as the tool's error."""


class ClaudeConversation:
    """Inherit to make a Workflow hold a durable, multi-turn Claude conversation.

    Callers send messages with the ``send_message`` Update, which returns Claude's reply,
    and finish with ``end_conversation``. The Activity answers them with
    :meth:`ClaudeSDKClient.run_conversation`.

    When the Activity uses :func:`workflow_approval`, a tool call that needs approval waits
    in ``pending_approvals`` until a caller decides it with ``approve_tool``.

    Run the Activity inside :meth:`conversation`. A Workflow with its own ``__init__``
    calls ``super().__init__()``.
    """

    def __init__(self) -> None:
        """Start with no messages and no checkpoints."""
        self._checkpoints: dict[str, Any] = {}
        self._prompts: list[str] = []
        self._ended = False
        self._run_finished = False
        self._approval_requests: dict[str, ToolApprovalRequest] = {}
        self._approvals: dict[str, ToolApproval] = {}

    @asynccontextmanager
    async def conversation(
        self, stream: WorkflowStream | None = None
    ) -> AsyncIterator[None]:
        """Wrap the Workflow's run, so the handlers it leaves behind finish however it ends.

        Pass the Workflow's ``WorkflowStream``, if it hosts one, so its subscribers are
        released too.
        """
        try:
            yield
        finally:
            self._run_finished = True
            # A subscriber's poll is an Update held open, so release it before waiting.
            if stream is not None:
                stream.detach_pollers()
            await workflow.wait_condition(workflow.all_handlers_finished)

    @workflow.signal(name=CHECKPOINT)
    def _checkpoint(self, checkpoint: Checkpoint) -> None:
        self._checkpoints.setdefault(checkpoint.id, checkpoint.value)

    @workflow.query(name=REPLAY)
    def _replay(self, id: str) -> Any:
        return self._checkpoints.get(id)

    @workflow.update
    async def send_message(self, message: str) -> str:
        """Send Claude a message and return its reply."""
        checkpoint_id = answer_id(len(self._prompts))
        self._prompts.append(message)
        await workflow.wait_condition(
            lambda: checkpoint_id in self._checkpoints or self._run_finished
        )
        if checkpoint_id not in self._checkpoints:
            raise ApplicationError(
                "The conversation ended before this message was answered",
                type="ConversationEnded",
            )
        return self._checkpoints[checkpoint_id]

    @send_message.validator
    def _validate_send_message(self, message: str) -> None:
        del message
        if self._is_closed:
            raise ApplicationError(
                "The conversation has ended", type="ConversationEnded"
            )

    @workflow.update
    def end_conversation(self) -> None:
        """Reject further messages. Messages already sent are still answered."""
        self._ended = True

    @workflow.update(name=REQUEST_APPROVAL)
    async def _request_approval(self, request: ToolApprovalRequest) -> ToolApproval:
        tool_use_id = request.tool_use_id
        self._approval_requests.setdefault(tool_use_id, request)
        await workflow.wait_condition(
            lambda: tool_use_id in self._approvals or self._run_finished
        )
        if tool_use_id not in self._approvals:
            raise ApplicationError(
                "The conversation ended before this tool call was approved",
                type="ConversationEnded",
            )
        return self._approvals[tool_use_id]

    @workflow.update
    def approve_tool(self, approval: ToolApproval) -> None:
        """Approve or deny a tool call listed by ``pending_approvals``."""
        self._approvals[approval.tool_use_id] = approval

    @approve_tool.validator
    def _validate_approve_tool(self, approval: ToolApproval) -> None:
        tool_use_id = approval.tool_use_id
        if tool_use_id not in self._approval_requests or tool_use_id in self._approvals:
            raise ApplicationError(
                f"Tool call {tool_use_id!r} is not waiting for approval",
                type="NotWaitingForApproval",
            )

    @workflow.query
    def pending_approvals(self) -> list[ToolApprovalRequest]:
        """The tool calls waiting for ``approve_tool``."""
        return [
            request
            for id, request in self._approval_requests.items()
            if id not in self._approvals
        ]

    @workflow.query(name=NEXT_PROMPT)
    def _next_prompt(self, index: int) -> NextPrompt:
        prompt = self._prompts[index] if index < len(self._prompts) else None
        return NextPrompt(prompt, self._is_closed)

    @property
    def _is_closed(self) -> bool:
        return self._ended or self._run_finished
