"""External pre-execution policy failures preserve the tool-step outcome contract."""

import pytest

from temporalio.api.failure.v1 import Failure
from temporalio.claude_agent_sdk import TOOL_CALL_REFUSED
from temporalio.claude_agent_sdk._workflow import _failed_step, _may_try_again
from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter
from temporalio.exceptions import ActivityError, ApplicationError, RetryState


def activity_failure(cause: ApplicationError) -> ActivityError:
    failure = Failure()
    converter, payload = DefaultFailureConverter(), DefaultPayloadConverter()
    converter.to_failure(cause, payload, failure)
    restored = converter.from_failure(failure, payload)
    error = ActivityError(
        "Activity task failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker",
        activity_type="run_claude_tool_step",
        activity_id="tool-x",
        retry_state=RetryState.NON_RETRYABLE_FAILURE,
    )
    error.__cause__ = restored
    return error


def test_external_policy_refusal_survives_wire_without_retry_or_private_reason() -> (
    None
):
    # An interceptor raises this before the Activity body: no body enrichment.
    error = activity_failure(
        ApplicationError(
            "private policy configuration",
            {"try_again": True},
            type=TOOL_CALL_REFUSED,
            non_retryable=True,
        )
    )
    record: dict[str, str] = {}
    result = _failed_step(error, record)
    assert record["status"] == "refused" and result.is_error
    assert result.content == (
        "This call was refused by policy before execution and did not run. Do not retry it."
    )
    assert "private" not in str(result.content)
    assert not _may_try_again(error)
    assert isinstance(error.cause, ApplicationError) and error.cause.non_retryable


@pytest.mark.parametrize(
    "kind,status,text,retry",
    [
        ("ToolCallNotRun", "not run", "This call did not run", True),
        ("ToolCallInterrupted", "interrupted", "so it may have run", False),
        ("OtherFailure", "interrupted", "so it may have run", False),
    ],
)
def test_existing_failure_semantics_are_preserved(
    kind: str, status: str, text: str, retry: bool
) -> None:
    error = activity_failure(
        ApplicationError("failure", {"try_again": True}, type=kind)
    )
    record: dict[str, str] = {}
    result = _failed_step(error, record)
    assert record["status"] == status and result.is_error
    assert text in str(result.content)
    assert _may_try_again(error) is retry
