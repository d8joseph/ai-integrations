"""Ensure tests exercise the installed build of this review branch."""

import hashlib
import subprocess
from pathlib import Path

import temporalio.claude_agent_sdk


def test_installed_source_matches_this_branch():
    plugin = Path(__file__).resolve().parents[3]
    subprocess.run(
        [
            "git",
            "-C",
            str(plugin),
            "merge-base",
            "--is-ancestor",
            "4f791d03c2a03a80b109939d8425ec34e1989783",
            "HEAD",
        ],
        check=True,
    )
    source = plugin / "src/temporalio/claude_agent_sdk"
    installed = Path(temporalio.claude_agent_sdk.__file__).parent
    for path in source.glob("*.py"):
        assert (
            hashlib.sha256((installed / path.name).read_bytes()).digest()
            == hashlib.sha256(path.read_bytes()).digest()
        ), path.name
