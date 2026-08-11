from __future__ import annotations

import hashlib
import json
import re

import pytest

from metaswarm.agents import (
    AgentContractError,
    AgentSchema,
    ContractIssue,
    render_retry_feedback,
    validate_agent_result,
)
from metaswarm.agents.parse import RESULT_BEGIN, RESULT_END
from metaswarm.agents.validation import (
    MAX_RETRY_FEEDBACK_BYTES,
    MAX_RETRY_ISSUES,
    ObservationsContext,
)


def _issue_lines(feedback: str) -> list[str]:
    return [line for line in feedback.splitlines() if line.startswith("  - $")]


def _omitted(feedback: str) -> int:
    match = re.search(r"^  - (\d+) additional issue\(s\) omitted\.$", feedback, re.MULTILINE)
    return 0 if match is None else int(match.group(1))


def test_retry_feedback_has_stable_path_then_code_order_and_no_raw_values() -> None:
    issues = (
        ContractIssue("z_code", ("items", 10), "static message"),
        ContractIssue("b_code", ("items", 2), "another static message"),
        ContractIssue("a_code", ("items", 2), "safe catalog text"),
    )
    expected = (
        "PREVIOUS ATTEMPT REJECTED\n"
        "  - $.items[2]: a_code: safe catalog text\n"
        "  - $.items[2]: b_code: another static message\n"
        "  - $.items[10]: z_code: static message\n"
        "Fix these and resend the full result. Partial results are not accepted.\n"
    )
    assert render_retry_feedback(issues) == expected
    assert render_retry_feedback(reversed(issues)) == expected
    assert "agent-secret" not in expected


def test_issue_count_cap_keeps_whole_lines_and_reports_exact_omitted_count() -> None:
    issues = tuple(
        ContractIssue("invalid_value", ("items", index), "field is invalid")
        for index in range(MAX_RETRY_ISSUES + 1)
    )
    feedback = render_retry_feedback(issues)
    assert len(_issue_lines(feedback)) == MAX_RETRY_ISSUES
    assert _omitted(feedback) == 1
    assert feedback.endswith(
        "Fix these and resend the full result. Partial results are not accepted.\n"
    )
    assert len(feedback.encode("utf-8")) <= MAX_RETRY_FEEDBACK_BYTES


def test_byte_cap_keeps_unicode_issue_lines_whole_and_counts_every_omission() -> None:
    message = "Безопасное диагностическое сообщение. " * 40
    issues = tuple(
        ContractIssue("invalid_value", ("observations", index, "body"), message)
        for index in range(MAX_RETRY_ISSUES)
    )
    feedback = render_retry_feedback(issues)
    rendered = len(_issue_lines(feedback))
    assert 0 < rendered < MAX_RETRY_ISSUES
    assert _omitted(feedback) == MAX_RETRY_ISSUES - rendered
    assert len(feedback.encode("utf-8")) <= MAX_RETRY_FEEDBACK_BYTES
    assert feedback.encode("utf-8").decode("utf-8") == feedback
    assert not feedback.endswith("Безопасное")
    assert render_retry_feedback(issues) == feedback


def test_huge_model_controlled_extra_key_is_replaced_by_hash_and_byte_length() -> None:
    raw_key = "private-" + "x" * 900_000
    payload = {
        "schema": "review.observations.v1",
        "observations": [],
        raw_key: "must not be reflected",
    }
    text = f"{RESULT_BEGIN}{json.dumps(payload)}{RESULT_END}"
    with pytest.raises(AgentContractError) as raised:
        validate_agent_result(
            text,
            AgentSchema.REVIEW_OBSERVATIONS,
            ObservationsContext(frozenset()),
        )
    feedback = render_retry_feedback(raised.value.issues)
    encoded_key = raw_key.encode("utf-8")
    expected = f"extra@sha256:{hashlib.sha256(encoded_key).hexdigest()}:bytes={len(encoded_key)}"
    assert expected in feedback
    assert raw_key not in feedback
    assert "must not be reflected" not in feedback
    assert len(feedback.encode("utf-8")) <= MAX_RETRY_FEEDBACK_BYTES


@pytest.mark.parametrize(
    ("payload", "code"),
    (
        ({"schema": "review.observations.v1"}, "missing_field"),
        ({"schema": "review.observations.v1", "observations": 1}, "invalid_type"),
        ({"schema": "review.observations.v1", "observations": [], "extra": 1}, "extra_field"),
    ),
)
def test_model_contract_families_render_only_normalized_diagnostics(
    payload: dict[str, object], code: str
) -> None:
    text = f"{RESULT_BEGIN}{json.dumps(payload)}{RESULT_END}"
    with pytest.raises(AgentContractError) as raised:
        validate_agent_result(
            text,
            AgentSchema.REVIEW_OBSERVATIONS,
            ObservationsContext(frozenset()),
        )
    feedback = render_retry_feedback(raised.value.issues)
    assert f": {code}: " in feedback
    assert repr(payload) not in feedback
