"""Terminal rendering for the example app."""

from io import StringIO
from unittest.mock import patch

from rich.console import Console

from tests.app.run import StreamedReply, ask_tool_approval


def event(index: int, text: str, *, attempt: int = 1) -> dict:
    return {
        "index": index,
        "attempt": attempt,
        "event": {"type": "content_block_delta", "delta": {"text": text}},
    }


def test_streamed_reply_redraws_one_complete_answer() -> None:
    output = StringIO()
    reply = StreamedReply(Console(file=output, force_terminal=True, width=80))

    reply.start(2)
    reply.update(event(2, "Hello"))
    reply.update(event(2, " **world**"))
    reply.update(event(1, " stale"))

    assert reply.text == "Hello **world**"

    reply.finish("Hello **world**")

    assert reply.index is None
    assert "Hello" in output.getvalue()
    assert "world" in output.getvalue()


def test_streamed_reply_replaces_a_failed_attempt() -> None:
    reply = StreamedReply(Console(file=StringIO(), force_terminal=True, width=80))

    reply.start(0)
    reply.update(event(0, "old", attempt=1))
    reply.update(event(0, "new", attempt=2))
    reply.update(event(0, " ignored", attempt=1))

    assert reply.text == "new"


def test_streamed_reply_ignores_late_and_non_text_events() -> None:
    reply = StreamedReply(Console(file=StringIO(), force_terminal=True, width=80))

    reply.start(0)
    reply.update({"index": 0, "attempt": 1, "event": {"type": "message_stop"}})
    reply.stop()
    reply.update(event(0, "late"))

    assert reply.text == ""


def test_streamed_reply_keeps_streaming_while_approval_is_visible() -> None:
    reply = StreamedReply(Console(file=StringIO(), force_terminal=True, width=80))

    reply.start(0)
    reply.update(event(0, "before "))
    reply.pause()
    reply.update(event(0, "and after"))
    reply.resume()

    assert reply.text == "before and after"
    assert reply.live is not None

    reply.stop()


def test_tool_approval_renders_readable_panel() -> None:
    output = StringIO()
    console = Console(file=output, force_terminal=True, width=100)
    tool_input = {
        "command": 'curl -s --max-time 10 "wttr.in/Ottawa?format=3"',
        "description": "Fetch current Ottawa weather",
    }

    with patch("tests.app.run.Confirm.ask", return_value=True) as confirm:
        approved = ask_tool_approval(console, "Bash", tool_input)

    rendered = output.getvalue()
    assert approved is True
    assert "Tool approval" in rendered
    assert "Bash" in rendered
    assert "curl -s --max-time 10" in rendered
    assert "Fetch current Ottawa weather" in rendered
    confirm.assert_called_once_with(
        "[bold yellow]Run this tool?[/]", console=console, default=False
    )
