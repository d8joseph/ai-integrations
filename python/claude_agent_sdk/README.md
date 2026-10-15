# Claude Agent SDK on Temporal

Makes a Claude Agent SDK run durable. Every model step and tool call is checkpointed in a Temporal
Workflow. When the Activity retries, checkpointed steps are replayed from the Workflow instead of
calling the model or the tool again.

`temporalio.claude_agent_sdk` exports:

- `ClaudeSDKClient`: the Claude Agent SDK's client, made durable. Use it in an Activity as you would
  the SDK's own, with the same `ClaudeAgentOptions` (re-exported here). Its `run_conversation()`
  answers each message sent to the Workflow.
- `ClaudeConversation`: the base class a Workflow inherits to hold a conversation. Callers send
  messages with its `send_message` Update and finish with `end_conversation`.
- `ClaudeAgentSDKPlugin`: the Worker plugin, which lets the Workflow import this package inside the
  sandbox.

## Use it

The Activity is plain Claude Agent SDK code:

```python
from temporalio.claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient


@activity.defn
async def run_claude() -> None:
    options = ClaudeAgentOptions(model="claude-sonnet-5-5", tools=["Bash"], allowed_tools=["Bash"])
    async with ClaudeSDKClient(options=options) as client:
        await client.run_conversation()
```

The Workflow inherits `ClaudeConversation` and runs the Activity inside `self.conversation()`. When
the run ends, however it ends, that block fails any message still waiting for an answer and waits for
the handlers to finish. A Workflow that hosts a `WorkflowStream` passes it in, so its subscribers are
released too. A Workflow with its own `__init__` calls `super().__init__()`.

```python
from temporalio.claude_agent_sdk import ClaudeConversation


@workflow.defn
class AgentWorkflow(ClaudeConversation):
    @workflow.run
    async def run(self) -> None:
        async with self.conversation():
            await workflow.execute_activity(
                run_claude,
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(seconds=30),
            )
```

Callers talk to the Workflow with two Updates. `send_message` returns Claude's reply, and
`end_conversation` lets the Activity finish. Once the conversation has ended, `send_message` is
rejected:

```python
answer = await handle.execute_update(
    AgentWorkflow.send_message, "Review the files in the directory."
)
await handle.execute_update(AgentWorkflow.end_conversation)
```

Add the plugin to the Worker:

```python
worker = Worker(client, task_queue=..., workflows=[AgentWorkflow], activities=[run_claude], plugins=[ClaudeAgentSDKPlugin()])
```

## How it works

`ClaudeSDKClient` adds a Claude Code mod to the options, plus the MCP server the mod uses to reach
the Workflow. Before each model step and tool call, the mod asks the Workflow to replay a
checkpointed result; after each one, it checkpoints the result. When a turn ends, `run_conversation`
checkpoints Claude's final result as the reply to the prompt.

`run_conversation` polls the Workflow for the next message every `poll_interval` (default 1
second) and heartbeats while it runs, so set a heartbeat timeout to notice a lost Worker. When the
Activity retries, it starts again at message 0. The messages it already answered come back straight away and the mod replays those
turns from their checkpoints, so the conversation is rebuilt on any Worker without calling the
model. If the mod cannot reach the Workflow, it stops the turn and `run_conversation` fails the
attempt, so Temporal retries it.

## Streaming

`send_message` returns Claude's whole reply. To let a caller, such as a chat UI, watch the reply as
it streams, host a `WorkflowStream` in the Workflow, set `include_partial_messages=True` in
`ClaudeAgentOptions`, and publish from the `on_message` callback of `run_conversation`. It is
called with each message Claude sends and the index of the message it answers, so the application
chooses what to publish and in what shape.

```python
from claude_agent_sdk import Message, StreamEvent
from temporalio.contrib.workflow_streams import WorkflowStream, WorkflowStreamClient


@workflow.defn
class AgentWorkflow(ClaudeConversation):
    @workflow.init
    def __init__(self) -> None:
        super().__init__()
        self.stream = WorkflowStream()

    @workflow.run
    async def run(self) -> None:
        async with self.conversation(self.stream):
            ...


# In the Activity
options = ClaudeAgentOptions(model="claude-sonnet-5-5", include_partial_messages=True)
stream = WorkflowStreamClient.from_within_activity(batch_interval=timedelta(milliseconds=100))
topic = stream.topic("claude", type=dict)


def publish(index: int, message: Message) -> None:
    if isinstance(message, StreamEvent):
        topic.publish({"index": index, "event": message.event})


async with stream, ClaudeSDKClient(options=options) as client:
    await client.run_conversation(on_message=publish)

# In the caller
stream = WorkflowStreamClient.create(client, workflow_id)
async for item in stream.topic("claude", type=dict).subscribe():
    print(item.data["event"])
```

A retried Activity publishes its events again, so treat the stream as a live view; the reply from
`send_message` is the durable one. `self.conversation(self.stream)` releases subscribers when the Workflow's
run ends, so the Workflow can complete.

See `tests/app/` for an example application.
