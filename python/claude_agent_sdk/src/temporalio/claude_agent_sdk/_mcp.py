"""The MCP server the Temporal mod uses to reach the Workflow."""

from __future__ import annotations

import json
from typing import Any, TypedDict

from claude_agent_sdk import (
    McpSdkServerConfig,
    create_sdk_mcp_server,
    tool,
)

from temporalio.claude_agent_sdk._workflow import (
    CHECKPOINT,
    REPLAY,
    Checkpoint,
)
from temporalio.client import WorkflowHandle


class _ModelCall(TypedDict):
    chunks: list[dict[str, Any]]
    result: dict[str, Any]


_ToolCallResult = dict[str, Any]

_CheckpointedCall = _ToolCallResult | _ModelCall


class _CheckpointArgs(TypedDict):
    id: str
    value: _CheckpointedCall


class _ReplayArgs(TypedDict):
    id: str


def _text(value: _CheckpointedCall | None) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value)}]}


def workflow_handle_mcp(
    handle: WorkflowHandle[Any, Any], errors: list[Exception]
) -> McpSdkServerConfig:
    """Build the server the mod calls the Workflow through.

    A failed call is added to ``errors``, so the Activity can fail the attempt.
    """

    @tool("replay", "Internal.", _ReplayArgs)
    async def replay(args: _ReplayArgs) -> dict[str, Any]:
        try:
            return _text(await handle.query(REPLAY, args["id"]))
        except Exception as err:
            errors.append(err)
            raise

    @tool("checkpoint", "Internal.", _CheckpointArgs)
    async def checkpoint(args: _CheckpointArgs) -> dict[str, Any]:
        try:
            await handle.signal(CHECKPOINT, Checkpoint(**args))
        except Exception as err:
            errors.append(err)
            raise
        return _text(None)

    return create_sdk_mcp_server("workflow_handle", tools=[replay, checkpoint])
