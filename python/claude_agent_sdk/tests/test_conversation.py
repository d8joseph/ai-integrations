"""A conversation reaches the Activity one message at a time, and survives a retry."""

import dataclasses
from collections.abc import AsyncIterator

import pytest
from claude_agent_sdk import Message, StreamEvent

from temporalio import activity
from temporalio.claude_agent_sdk._workflow import REPLAY, answer_id
from temporalio.client import Client, WorkflowUpdateFailedError
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from temporalio.exceptions import ApplicationError
from tests.app.workflow import AgentWorkflow
from tests.helpers.claude import RESULT, StandInClaude, start_agent_workflow


@activity.defn(name="run_claude")
async def answer_then_fail_once() -> None:
    """Answers with what it has seen, and fails after its first answer on the first attempt."""
    attempt = activity.info().attempt

    class FailOnce(StandInClaude):
        async def receive_response(self) -> AsyncIterator[Message]:
            async for msg in super().receive_response():
                yield msg
            if attempt == 1:
                raise RuntimeError("lost the Worker")

    await FailOnce(lambda seen: f"attempt {attempt} saw {seen}").run_conversation()


async def test_retry_gets_the_conversation_so_far(client: Client) -> None:
    async with start_agent_workflow(client, answer_then_fail_once) as handle:
        first = await handle.execute_update(AgentWorkflow.send_message, "a")
        second = await handle.execute_update(AgentWorkflow.send_message, "b")
        await handle.execute_update(AgentWorkflow.end_conversation)
        await handle.result()
        stored_first = await handle.query(REPLAY, answer_id(0))

    assert first == "attempt 1 saw ['a']"
    # The retry was handed "a" again before it waited for "b". Its reply to "a" lost to the first one.
    assert stored_first == first
    assert second == "attempt 2 saw ['a', 'b']"


@activity.defn(name="run_claude")
async def fail_to_reach_the_workflow_once() -> None:
    """Stands in for a mod that could not reach the Workflow on the first attempt."""
    attempt = activity.info().attempt
    claude = StandInClaude(lambda _: f"attempt {attempt}")
    if attempt == 1:
        claude._workflow_errors.append(RuntimeError("Workflow unreachable"))
    await claude.run_conversation()


async def test_a_failed_call_to_the_workflow_fails_the_attempt(client: Client) -> None:
    async with start_agent_workflow(client, fail_to_reach_the_workflow_once) as handle:
        answer = await handle.execute_update(AgentWorkflow.send_message, "a")
        await handle.execute_update(AgentWorkflow.end_conversation)
        await handle.result()

    assert answer == "attempt 2"


# How Claude Code ends a turn that reaches max_turns.
MAX_TURNS = dataclasses.replace(
    RESULT,
    subtype="error_max_turns",
    is_error=True,
    errors=["Reached maximum number of turns (1)"],
)


@activity.defn(name="run_claude")
async def reach_max_turns() -> None:
    """Ends each turn by reaching ``max_turns``."""
    await StandInClaude(lambda _: MAX_TURNS).run_conversation()


async def test_a_turn_that_ends_in_an_error_answers_with_the_errors(
    client: Client,
) -> None:
    async with start_agent_workflow(client, reach_max_turns) as handle:
        answer = await handle.execute_update(AgentWorkflow.send_message, "a")
        await handle.execute_update(AgentWorkflow.end_conversation)
        await handle.result()

    assert answer == "Reached maximum number of turns (1)"


async def test_send_message_after_the_end_is_rejected(client: Client) -> None:
    async with start_agent_workflow(client, answer_then_fail_once) as handle:
        await handle.execute_update(AgentWorkflow.end_conversation)
        with pytest.raises(WorkflowUpdateFailedError) as err:
            await handle.execute_update(AgentWorkflow.send_message, "a")
        await handle.result()

    assert isinstance(err.value.cause, ApplicationError)
    assert err.value.cause.type == "ConversationEnded"


@activity.defn(name="run_claude")
async def fail_for_good() -> None:
    """Fails without retries once the first message arrives."""

    def reply(_seen: list[str]) -> str:
        raise ApplicationError("broken", non_retryable=True)

    await StandInClaude(reply).run_conversation()


async def test_a_message_waiting_when_the_activity_fails_is_failed(
    client: Client,
) -> None:
    async with start_agent_workflow(client, fail_for_good) as handle:
        with pytest.raises(WorkflowUpdateFailedError) as err:
            await handle.execute_update(AgentWorkflow.send_message, "a")

    assert isinstance(err.value.cause, ApplicationError)
    assert err.value.cause.type == "ConversationEnded"


@activity.defn(name="run_claude")
async def stream_to_claude_topic() -> None:
    """Answers each message, publishing Claude's stream events to the "claude" topic."""
    stream = WorkflowStreamClient.from_within_activity()
    topic = stream.topic("claude", type=dict)

    def publish(index: int, message: Message) -> None:
        if isinstance(message, StreamEvent):
            topic.publish({"index": index, "event": message.event})

    async with stream:
        await StandInClaude(lambda seen: f"hello {seen[-1]}").run_conversation(
            on_message=publish
        )


async def test_on_message_gets_each_message_with_its_index(client: Client) -> None:
    async with start_agent_workflow(client, stream_to_claude_topic) as handle:
        first = await handle.execute_update(AgentWorkflow.send_message, "a")
        second = await handle.execute_update(AgentWorkflow.send_message, "b")
        await handle.execute_update(AgentWorkflow.end_conversation)
        # Subscribed after the run, so the release of pollers ends the subscription.
        subscription = WorkflowStreamClient.create(client, handle.id).topic(
            "claude", type=dict
        )
        items = [item.data async for item in subscription.subscribe(from_offset=0)]
        await handle.result()

    assert (first, second) == ("hello a", "hello b")
    assert [(i["index"], i["event"]["delta"]["text"]) for i in items] == [
        (0, "he"),
        (0, "llo"),
        (1, "he"),
        (1, "llo"),
    ]
