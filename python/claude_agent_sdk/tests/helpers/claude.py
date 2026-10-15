"""A stand-in for Claude, and a Worker that runs the example AgentWorkflow with any Activity."""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterable, AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from claude_agent_sdk import Message, ResultMessage, StreamEvent

from temporalio.claude_agent_sdk import ClaudeAgentSDKPlugin, ClaudeSDKClient
from temporalio.client import Client, WorkflowHandle
from temporalio.worker import Worker
from tests.app.workflow import AgentWorkflow

RESULT = ResultMessage(
    subtype="success",
    duration_ms=0,
    duration_api_ms=0,
    is_error=False,
    num_turns=1,
    session_id="s",
)


class StandInClaude(ClaudeSDKClient):
    """Stands in for Claude and the mod, with no CLI.

    Streams two text deltas, then ends the turn with ``reply(messages seen)``: the
    result text, or a whole ``ResultMessage``.
    """

    def __init__(self, reply: Callable[[list[str]], str | ResultMessage]) -> None:
        super().__init__()
        self.reply = reply
        self.seen: list[str] = []

    async def query(
        self, prompt: str | AsyncIterable[dict[str, Any]], session_id: str = "default"
    ) -> None:
        assert isinstance(prompt, str)
        self.seen.append(prompt)

    async def receive_response(self) -> AsyncIterator[Message]:
        for text in ("he", "llo"):
            event = {"type": "content_block_delta", "delta": {"text": text}}
            yield StreamEvent(uuid="u", session_id="s", event=event)
        reply = self.reply(self.seen)
        if isinstance(reply, str):
            reply = dataclasses.replace(RESULT, result=reply)
        yield reply


@asynccontextmanager
async def start_agent_workflow(
    client: Client, activity_fn: Callable[..., Any]
) -> AsyncIterator[WorkflowHandle[AgentWorkflow, None]]:
    """Start an AgentWorkflow on a Worker that runs ``activity_fn`` as its Activity."""
    task_queue = f"tq-{uuid.uuid4()}"
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[AgentWorkflow],
        activities=[activity_fn],
        plugins=[ClaudeAgentSDKPlugin()],
    ):
        yield await client.start_workflow(
            AgentWorkflow.run, id=f"wf-{uuid.uuid4()}", task_queue=task_queue
        )
