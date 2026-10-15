"""ClaudeSDKClient wires in the mod, and its MCP server checkpoints into and replays from the Workflow."""

import json
from pathlib import Path
from typing import Any, cast

from claude_agent_sdk import McpSdkServerConfig
from mcp import Client as McpClient

from temporalio import activity
from temporalio.claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from temporalio.client import Client
from tests.helpers.claude import start_agent_workflow

recorded: dict[str, Any] = {}


@activity.defn(name="run_claude")
async def call_like_the_mod() -> None:
    """Builds the client, then makes the mod's calls through its server, with no model."""
    options = ClaudeSDKClient(ClaudeAgentOptions(allowed_tools=["Bash"])).options
    assert isinstance(options.mcp_servers, dict)
    server = cast(McpSdkServerConfig, options.mcp_servers["workflow_handle"])
    calls: list[tuple[str, dict[str, Any]]] = [
        ("replay", {"id": "step-1"}),
        ("checkpoint", {"id": "step-1", "value": {"n": 1}}),
        ("checkpoint", {"id": "step-1", "value": {"n": 2}}),
        ("replay", {"id": "step-1"}),
    ]
    async with McpClient(server["instance"]) as mcp:
        recorded["tools"] = [t.name for t in (await mcp.list_tools()).tools]
        recorded["results"] = [
            (await mcp.call_tool(name, args)).content[0].text  # type: ignore[union-attr]
            for name, args in calls
        ]
    recorded["options"] = options


async def test_client_wires_in_the_mod_and_its_server(client: Client) -> None:
    async with start_agent_workflow(client, call_like_the_mod) as handle:
        await handle.result()

    options = recorded["options"]
    assert sorted(recorded["tools"]) == ["checkpoint", "replay"]
    assert options.allowed_tools == [
        "Bash",
        "mcp__workflow_handle__replay",
        "mcp__workflow_handle__checkpoint",
    ]
    [plugin] = options.plugins
    mod = Path(plugin["path"])
    assert (
        json.loads((mod / ".claude-plugin" / "plugin.json").read_text())["name"]
        == "temporal-mod"
    )
    assert (mod / "hooks" / "register.ts").is_file()
    # Nothing checkpointed yet, then the first checkpoint wins and is replayed.
    assert recorded["results"] == ["null", "null", "null", '{"n": 1}']
