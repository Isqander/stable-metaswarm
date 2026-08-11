from __future__ import annotations

import pytest

from metaswarm.agents import AgentContractError, AgentSchema, validate_agent_result
from metaswarm.agents.parse import (
    MAX_RESULT_BYTES,
    RESULT_BEGIN,
    RESULT_END,
    PayloadParseError,
    canonical_json_bytes,
    extract_marked_payload,
)
from metaswarm.agents.validation import ObservationsContext


def _marked(payload: str, *, before: str = "reasoning\n", after: str = "\ndone") -> str:
    return f"{before}{RESULT_BEGIN}{payload}{RESULT_END}{after}"


def test_noise_outside_the_single_marker_pair_does_not_change_payload() -> None:
    payload = '{"schema":"review.observations.v1","observations":[]}'
    assert extract_marked_payload(_marked(payload)) == extract_marked_payload(
        _marked(payload, before="different reasoning\n", after="\nother tail")
    )


@pytest.mark.parametrize(
    ("text", "code"),
    (
        ("no markers", "invalid_markers"),
        (f"{RESULT_BEGIN}{{}}", "invalid_markers"),
        (f"{RESULT_END}{{}}{RESULT_BEGIN}", "invalid_markers"),
        (f"{RESULT_BEGIN}{{}}{RESULT_END}{RESULT_BEGIN}{{}}{RESULT_END}", "invalid_markers"),
        (_marked(""), "invalid_json"),
        (_marked("{"), "invalid_json"),
        (_marked("{} {}"), "invalid_json"),
        (_marked("[]"), "top_level_object"),
        (_marked("1"), "top_level_object"),
        (_marked('{"a":1,"a":2}'), "duplicate_key"),
        (_marked('{"value":NaN}'), "non_finite_number"),
        (_marked('{"value":Infinity}'), "non_finite_number"),
        (_marked('{"value":-Infinity}'), "non_finite_number"),
        (_marked('{"value":1e999}'), "non_finite_number"),
    ),
)
def test_transport_defects_have_stable_codes(text: str, code: str) -> None:
    with pytest.raises(PayloadParseError) as raised:
        extract_marked_payload(text)
    assert raised.value.code == code


@pytest.mark.parametrize(
    "payload",
    (
        '"\ud800"',
        '"\udfff"',
        '{"\ud800":"value"}',
        '{"value":"\\ud800"}',
        '{"value":"\\udfff"}',
        '{"\\ud800":"value"}',
        '{"nested":[{"\\udfff":"value"}]}',
    ),
)
def test_lone_surrogate_inside_payload_is_invalid_unicode(payload: str) -> None:
    if payload.startswith('"'):
        text = _marked(f'{{"value":{payload}}}')
    else:
        text = _marked(payload)
    with pytest.raises(PayloadParseError) as raised:
        extract_marked_payload(text)
    assert raised.value.code == "invalid_unicode"


def test_surrogate_in_noise_is_ignored_and_valid_pair_becomes_one_scalar() -> None:
    result = extract_marked_payload(_marked('{"value":"\\ud83d\\ude00"}', before="ignored \ud800"))
    assert result == {"value": "😀"}


def test_result_size_uses_exact_utf8_bytes_between_markers() -> None:
    assert MAX_RESULT_BYTES == 1_048_576
    prefix = '{"pad":"'
    suffix = '"}'
    pad = "x" * (MAX_RESULT_BYTES - len(prefix.encode()) - len(suffix.encode()))
    assert extract_marked_payload(_marked(prefix + pad + suffix))["pad"] == pad
    with pytest.raises(PayloadParseError) as raised:
        extract_marked_payload(_marked(prefix + pad + "x" + suffix))
    assert raised.value.code == "result_too_large"


def test_non_ascii_size_and_canonical_bytes_are_deterministic() -> None:
    first = {"z": ["ё", 1], "a": "😀"}
    second = {"a": "😀", "z": ["ё", 1]}
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert canonical_json_bytes(first) == '{"a":"😀","z":["ё",1]}'.encode()


def test_oversized_integer_is_normalized_as_invalid_json() -> None:
    payload = '{"schema":"review.observations.v1","observations":[],"number":' + "9" * 5_000 + "}"
    with pytest.raises(AgentContractError) as raised:
        validate_agent_result(
            _marked(payload),
            AgentSchema.REVIEW_OBSERVATIONS,
            ObservationsContext(frozenset()),
        )
    assert [(issue.code, issue.path) for issue in raised.value.issues] == [("invalid_json", ())]


def test_deep_unicode_walk_is_iterative_and_still_finds_a_nested_surrogate() -> None:
    depth = 1_200
    prefix = '{"schema":"review.observations.v1","observations":[],"nested":'
    valid = prefix + "[" * depth + '"ok"' + "]" * depth + "}"
    parsed = extract_marked_payload(_marked(valid))
    assert isinstance(parsed["nested"], list)
    with pytest.raises(AgentContractError) as valid_rejected:
        validate_agent_result(
            _marked(valid),
            AgentSchema.REVIEW_OBSERVATIONS,
            ObservationsContext(frozenset()),
        )
    assert {issue.code for issue in valid_rejected.value.issues} == {"extra_field"}

    invalid = prefix + "[" * depth + '"\\ud800"' + "]" * depth + "}"
    with pytest.raises(AgentContractError) as invalid_rejected:
        validate_agent_result(
            _marked(invalid),
            AgentSchema.REVIEW_OBSERVATIONS,
            ObservationsContext(frozenset()),
        )
    assert [(issue.code, issue.path) for issue in invalid_rejected.value.issues] == [
        ("invalid_unicode", ())
    ]
