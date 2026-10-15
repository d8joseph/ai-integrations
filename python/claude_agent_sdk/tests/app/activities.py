"""Runs a multi-turn Claude conversation in an Activity, made durable by the Temporal mod."""

from datetime import timedelta

from claude_agent_sdk import Message, StreamEvent

from temporalio import activity
from temporalio.claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    workflow_approval,
)
from temporalio.contrib.workflow_streams import WorkflowStreamClient

STREAMING_TOPIC = "claude"


@activity.defn
async def run_claude() -> None:
    """Answer each message sent to the Workflow until the conversation ends."""
    # Reading is allowed outright. Bash waits for a person to approve it.
    options = ClaudeAgentOptions(
        tools=["Bash", "Read", "Glob", "Grep"],
        allowed_tools=["Read", "Glob", "Grep"],
        can_use_tool=workflow_approval,
        setting_sources=[],
        model="claude-sonnet-5-5",
        max_turns=10,
        include_partial_messages=True,
    )
    # The default batches for 2s; a shorter interval keeps the printed text live.
    stream = WorkflowStreamClient.from_within_activity(
        batch_interval=timedelta(milliseconds=100)
    )
    topic = stream.topic(STREAMING_TOPIC, type=dict)
    # A retry streams its turns again, so readers use the attempt to replace the earlier one's.
    attempt = activity.info().attempt

    def publish(index: int, message: Message) -> None:
        # Only the main loop's output, not a subagent's.
        if isinstance(message, StreamEvent) and message.parent_tool_use_id is None:
            topic.publish({"index": index, "attempt": attempt, "event": message.event})

    async with stream, ClaudeSDKClient(options=options) as client:
        await client.run_conversation(on_message=publish)
