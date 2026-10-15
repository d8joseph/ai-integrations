"""Starts an AgentWorkflow conversation and sends it each line you type: ``python -m tests.app.run``.

Start the Worker first with ``python -m tests.app.worker``.
"""

import asyncio
import uuid
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.pretty import Pretty
from rich.prompt import Confirm

from temporalio.claude_agent_sdk import ToolApproval
from temporalio.client import Client, WorkflowHandle
from temporalio.contrib.workflow_streams import WorkflowStreamClient
from tests.app.activities import STREAMING_TOPIC
from tests.app.worker import TASK_QUEUE
from tests.app.workflow import AgentWorkflow


class StreamedReply:
    """Render one streamed reply in place, then leave one clean final answer."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.index: int | None = None
        self.attempt = 0
        self.text = ""
        self.live: Live | None = None

    def start(self, index: int) -> None:
        self.stop()
        self.index = index
        self.attempt = 0
        self.text = ""
        self.resume()

    def resume(self) -> None:
        """Resume rendering after another terminal interaction."""
        if self.index is None or self.live is not None:
            return
        content = Markdown(self.text) if self.text else "Waiting for Claude…"
        self.live = Live(
            Panel(content, title="Claude", border_style="cyan"),
            console=self.console,
            refresh_per_second=12,
            transient=True,
        )
        self.live.start()

    def update(self, item: dict[str, Any]) -> None:
        if item["index"] != self.index:
            return
        attempt = item["attempt"]
        if attempt < self.attempt:
            return
        if attempt > self.attempt:
            self.attempt = attempt
            self.text = ""
        event = item["event"]
        if event["type"] != "content_block_delta":
            return
        self.text += event["delta"].get("text", "")
        if self.live is not None:
            self.live.update(
                Panel(Markdown(self.text), title="Claude", border_style="cyan")
            )

    def finish(self, answer: str) -> None:
        self.stop()
        self.console.print(Panel(Markdown(answer), title="Claude", border_style="cyan"))

    def stop(self) -> None:
        self.pause()
        self.index = None

    def pause(self) -> None:
        """Pause rendering without discarding the reply in progress."""
        if self.live is not None:
            self.live.stop()
            self.live = None


def ask_tool_approval(
    console: Console, tool_name: str, tool_input: dict[str, Any]
) -> bool:
    """Render a readable tool request and ask for confirmation."""
    console.print(
        Panel(
            Pretty(tool_input, expand_all=True),
            title=f"[bold]Tool approval · {tool_name}[/]",
            subtitle="Review before running",
            border_style="yellow",
            padding=(1, 2),
        )
    )
    return Confirm.ask("[bold yellow]Run this tool?[/]", console=console, default=False)


async def render_stream(client: Client, workflow_id: str, reply: StreamedReply) -> None:
    stream = WorkflowStreamClient.create(client, workflow_id)
    async for item in stream.topic(STREAMING_TOPIC, type=dict).subscribe():
        reply.update(item.data)


async def approve_tools(
    handle: WorkflowHandle[AgentWorkflow, None],
    console: Console,
    reply: StreamedReply,
) -> None:
    """Ask whether to run each tool call that waits for approval."""
    while True:
        for request in await handle.query(AgentWorkflow.pending_approvals):
            reply.pause()
            try:
                approved = await asyncio.to_thread(
                    ask_tool_approval,
                    console,
                    request.tool_name,
                    request.tool_input,
                )
                approval = ToolApproval(request.tool_use_id, approved=approved)
                await handle.execute_update(AgentWorkflow.approve_tool, approval)
            finally:
                reply.resume()
        await asyncio.sleep(0.5)


async def main() -> None:
    console = Console()
    client = await Client.connect("localhost:7233")
    handle = await client.start_workflow(
        AgentWorkflow.run, id=f"agent-{uuid.uuid4().hex[:8]}", task_queue=TASK_QUEUE
    )
    reply = StreamedReply(console)
    streaming = asyncio.create_task(render_stream(client, handle.id, reply))
    approving = asyncio.create_task(approve_tools(handle, console, reply))
    index = 0
    while message := await asyncio.to_thread(console.input, "[bold green]You[/] "):
        reply.start(index)
        try:
            answer = await handle.execute_update(AgentWorkflow.send_message, message)
        except BaseException:
            reply.stop()
            raise
        reply.finish(answer)
        index += 1
    await handle.execute_update(AgentWorkflow.end_conversation)
    await handle.result()
    streaming.cancel()
    approving.cancel()


if __name__ == "__main__":
    asyncio.run(main())
