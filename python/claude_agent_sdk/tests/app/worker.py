"""Runs the Worker for AgentWorkflow against a local Temporal server: ``python -m tests.app.worker``."""

import asyncio

from temporalio.claude_agent_sdk import ClaudeAgentSDKPlugin
from temporalio.client import Client
from temporalio.worker import Worker
from tests.app.activities import run_claude
from tests.app.workflow import AgentWorkflow

TASK_QUEUE = "claude-agent-sdk"


def create_worker(client: Client) -> Worker:
    """Create the Worker for AgentWorkflow and its Activity."""
    return Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[AgentWorkflow],
        activities=[run_claude],
        plugins=[ClaudeAgentSDKPlugin()],
    )


async def main() -> None:
    """Run the Worker until it is stopped."""
    client = await Client.connect("localhost:7233")
    await create_worker(client).run()


if __name__ == "__main__":
    asyncio.run(main())
