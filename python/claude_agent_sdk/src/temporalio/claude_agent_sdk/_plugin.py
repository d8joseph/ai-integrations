"""Worker plugin that lets Workflows import this package inside the sandbox."""

from __future__ import annotations

import dataclasses

from temporalio.plugin import SimplePlugin
from temporalio.worker import WorkflowRunner
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner


class ClaudeAgentSDKPlugin(SimplePlugin):
    """Pass ``claude_agent_sdk`` through the Workflow sandbox.

    A Workflow that inherits :class:`ClaudeConversation` imports this package, which
    imports ``claude_agent_sdk`` for the Activity's helpers.
    """

    def __init__(self) -> None:
        """Create the plugin."""

        def workflow_runner(runner: WorkflowRunner | None) -> WorkflowRunner:
            if not runner:
                raise ValueError("No WorkflowRunner provided to ClaudeAgentSDKPlugin.")
            if isinstance(runner, SandboxedWorkflowRunner):
                return dataclasses.replace(
                    runner,
                    restrictions=runner.restrictions.with_passthrough_modules(
                        "claude_agent_sdk"
                    ),
                )
            return runner

        super().__init__(name="ClaudeAgentSDKPlugin", workflow_runner=workflow_runner)
