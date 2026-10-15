"""Durable Claude Agent SDK runs on Temporal.

Use :class:`ClaudeSDKClient` in an Activity as you would the Claude Agent SDK's own client.
It checkpoints every model step and tool call in the Workflow that started the Activity,
and replays them when the Activity retries. The Workflow inherits
:class:`ClaudeConversation`, which callers talk to with the ``send_message`` and
``end_conversation`` Updates. Add :class:`ClaudeAgentSDKPlugin` to the Worker.
"""

from __future__ import annotations

from claude_agent_sdk import ClaudeAgentOptions

from temporalio.claude_agent_sdk._client import ClaudeSDKClient, workflow_approval
from temporalio.claude_agent_sdk._plugin import ClaudeAgentSDKPlugin
from temporalio.claude_agent_sdk._workflow import (
    ClaudeConversation,
    ToolApproval,
    ToolApprovalRequest,
)

__all__ = [
    "ClaudeAgentOptions",
    "ClaudeAgentSDKPlugin",
    "ClaudeConversation",
    "ClaudeSDKClient",
    "ToolApproval",
    "ToolApprovalRequest",
    "workflow_approval",
]
