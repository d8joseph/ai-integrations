"""Runs a Claude conversation in an Activity. ClaudeConversation adds the handlers."""

from datetime import timedelta

from temporalio import workflow
from temporalio.claude_agent_sdk import ClaudeConversation
from temporalio.contrib.workflow_streams import WorkflowStream

with workflow.unsafe.imports_passed_through():
    from tests.app.activities import run_claude


@workflow.defn
class AgentWorkflow(ClaudeConversation):
    @workflow.init
    def __init__(self) -> None:
        super().__init__()
        self.stream = WorkflowStream()

    @workflow.run
    async def run(self) -> None:
        """Run the conversation."""
        async with self.conversation(self.stream):
            await workflow.execute_activity(
                run_claude,
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(seconds=30),
            )
