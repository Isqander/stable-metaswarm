from __future__ import annotations

import copy
import json

import pytest

from metaswarm.agents import (
    AgentContractError,
    AgentSchema,
    ValidationContextError,
    validate_agent_result,
)
from metaswarm.agents.parse import RESULT_BEGIN, RESULT_END
from metaswarm.agents.validation import (
    DecisionsContext,
    DispositionsContext,
    GraphContext,
    ObservationsContext,
    ReconciliationValidationContext,
)


def _text(payload: dict[str, object]) -> str:
    return f"{RESULT_BEGIN}{json.dumps(payload)}{RESULT_END}"


def _codes(payload: dict[str, object], schema: AgentSchema, context: object) -> set[str]:
    with pytest.raises(AgentContractError) as raised:
        validate_agent_result(_text(payload), schema, context)  # type: ignore[arg-type]
    return {issue.code for issue in raised.value.issues}


def _observation(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "title": "Unsafe state transition",
        "body": "The transition skips the durable gate.",
        "file": "src/state.py",
        "line_start": 10,
        "line_end": 12,
        "evidence": "The branch writes closed directly.",
        "severity_suggested": "high",
    }
    value.update(changes)
    return value


def test_blind_observations_accept_empty_and_validate_location_and_limit() -> None:
    context = ObservationsContext(frozenset({"src/state.py"}))
    empty = {"schema": "review.observations.v1", "observations": []}
    assert (
        validate_agent_result(
            _text(empty), AgentSchema.REVIEW_OBSERVATIONS, context
        ).payload.observations
        == ()
    )  # type: ignore[union-attr]

    valid = {"schema": "review.observations.v1", "observations": [_observation()]}
    assert (
        len(
            validate_agent_result(
                _text(valid), AgentSchema.REVIEW_OBSERVATIONS, context
            ).payload.observations
        )
        == 1
    )  # type: ignore[union-attr]

    invalid_cases = (
        (_observation(file="missing.py"), "unknown_file"),
        (_observation(line_start=12, line_end=10), "invalid_value"),
        (_observation(line_start=10, line_end=None), "invalid_value"),
        (_observation(file=None), "invalid_value"),
        (_observation(title="two\nlines"), "invalid_value"),
        (_observation(title="x" * 121), "invalid_length"),
        (_observation(body="x" * 4001), "invalid_length"),
        (_observation(evidence="x" * 2001), "invalid_length"),
        (_observation(severity_suggested="urgent"), "invalid_literal"),
        (_observation(unchanged_from="O-1"), "extra_field"),
        (_observation(finding_id="F-1"), "extra_field"),
    )
    for observation, expected in invalid_cases:
        payload = {"schema": "review.observations.v1", "observations": [observation]}
        assert expected in _codes(payload, AgentSchema.REVIEW_OBSERVATIONS, context)

    too_many = {
        "schema": "review.observations.v1",
        "observations": [_observation(title=f"Observation {index}") for index in range(101)],
    }
    assert "observation_limit" in _codes(too_many, AgentSchema.REVIEW_OBSERVATIONS, context)


@pytest.mark.parametrize("severity", ("low", "medium", "high", "critical"))
def test_each_normative_severity_has_a_positive_witness(severity: str) -> None:
    payload = {
        "schema": "review.observations.v1",
        "observations": [_observation(severity_suggested=severity)],
    }
    validate_agent_result(
        _text(payload),
        AgentSchema.REVIEW_OBSERVATIONS,
        ObservationsContext(frozenset({"src/state.py"})),
    )


def _reconciliation_context(
    exposed: frozenset[str] = frozenset({"O-1", "O-2"}),
) -> ReconciliationValidationContext:
    return ReconciliationValidationContext(
        exposed,
        frozenset({"F-open"}),
        (
            ("F-accepted", "accepted_reason"),
            ("F-fixed", "verified_fixed"),
            ("F-human", "human_decision"),
        ),
    )


def _group(
    observation_ids: list[str],
    outcome: str,
    **fields: object,
) -> dict[str, object]:
    return {"observation_ids": observation_ids, "outcome": outcome, **fields}


def test_reconciliation_is_an_exact_partition_of_raw_ids_without_alias_dedup() -> None:
    payload = {
        "schema": "review.reconciliation.v1",
        "groups": [
            _group(["O-1"], "new", title="Same six fields"),
            _group(["O-2"], "new", title="Same six fields"),
        ],
    }
    result = validate_agent_result(
        _text(payload), AgentSchema.REVIEW_RECONCILIATION, _reconciliation_context()
    )
    assert tuple(group.observation_ids for group in result.payload.groups) == (("O-1",), ("O-2",))  # type: ignore[union-attr]

    cases = (
        ([_group(["O-1"], "new", title="New")], "missing_observation"),
        ([_group(["O-1", "O-2", "O-X"], "new", title="New")], "unknown_observation"),
        (
            [_group(["O-1", "O-2"], "new", title="New"), _group(["O-1"], "new", title="Again")],
            "duplicate_observation",
        ),
    )
    for groups, expected in cases:
        assert expected in _codes(
            {"schema": "review.reconciliation.v1", "groups": groups},
            AgentSchema.REVIEW_RECONCILIATION,
            _reconciliation_context(),
        )


@pytest.mark.parametrize(
    ("group", "expected"),
    (
        (_group(["O-1"], "new"), "missing_title"),
        (_group(["O-1"], "new", title="New", finding_id="F-open"), "unexpected_finding"),
        (_group(["O-1"], "existing_open", finding_id="F-open", title="Rename"), "unexpected_title"),
        (_group(["O-1"], "existing_open", finding_id="F-fixed"), "finding_scope"),
        (_group(["O-1"], "reaffirmed_closed", finding_id="F-fixed"), "finding_scope"),
        (_group(["O-1"], "reopen_closed", finding_id="F-human"), "missing_reason"),
        (_group(["O-1"], "reopen_closed", finding_id="F-open", reason="Returned"), "finding_scope"),
    ),
)
def test_reconciliation_outcome_rules_are_individually_enforced(
    group: dict[str, object], expected: str
) -> None:
    context = _reconciliation_context(frozenset({"O-1"}))
    payload = {"schema": "review.reconciliation.v1", "groups": [group]}
    assert expected in _codes(payload, AgentSchema.REVIEW_RECONCILIATION, context)


@pytest.mark.parametrize(
    "group",
    (
        _group(["O-1"], "new", title="New"),
        _group(["O-1"], "existing_open", finding_id="F-open"),
        _group(["O-1"], "reaffirmed_closed", finding_id="F-accepted"),
        _group(["O-1"], "reopen_closed", finding_id="F-fixed", reason="Returned"),
        _group(["O-1"], "reopen_closed", finding_id="F-human", reason="Returned"),
    ),
)
def test_each_reconciliation_outcome_has_a_positive_witness(group: dict[str, object]) -> None:
    payload = {"schema": "review.reconciliation.v1", "groups": [group]}
    validate_agent_result(
        _text(payload),
        AgentSchema.REVIEW_RECONCILIATION,
        _reconciliation_context(frozenset({"O-1"})),
    )


def test_dispositions_require_an_exact_set_and_reasons_only_for_non_fixed() -> None:
    context = DispositionsContext(frozenset({"F-1", "F-2", "F-3"}))
    valid = {
        "schema": "review.dispositions.v1",
        "dispositions": [
            {"finding_id": "F-1", "disposition": "fixed"},
            {"finding_id": "F-2", "disposition": "rejected", "reason": "Not reproducible"},
            {"finding_id": "F-3", "disposition": "wont_fix", "reason": "Accepted cost"},
        ],
    }
    validate_agent_result(_text(valid), AgentSchema.REVIEW_DISPOSITIONS, context)

    cases = (
        ([valid["dispositions"][0]], "missing_finding"),  # type: ignore[index]
        ([*valid["dispositions"], {"finding_id": "F-X", "disposition": "fixed"}], "finding_scope"),  # type: ignore[misc]
        ([*valid["dispositions"], valid["dispositions"][0]], "duplicate_finding"),  # type: ignore[index,misc]
        (
            [
                {"finding_id": "F-1", "disposition": "fixed"},
                {"finding_id": "F-2", "disposition": "rejected"},
                {"finding_id": "F-3", "disposition": "wont_fix", "reason": "Accepted cost"},
            ],
            "missing_reason",
        ),
    )
    for dispositions, expected in cases:
        payload = {"schema": "review.dispositions.v1", "dispositions": dispositions}
        assert expected in _codes(payload, AgentSchema.REVIEW_DISPOSITIONS, context)


def _decisions_context() -> DecisionsContext:
    return DecisionsContext(
        frozenset({"F-fixed", "F-rejected"}),
        (("F-fixed", "fixed"), ("F-rejected", "rejected")),
        (("F-fixed", "O-fixed"), ("F-rejected", "O-rejected")),
        frozenset({"src/state.py"}),
    )


def _valid_decisions() -> dict[str, object]:
    return {
        "schema": "review.decisions.v1",
        "decisions": [
            {"finding_id": "F-fixed", "decision": "still_present"},
            {
                "finding_id": "F-rejected",
                "decision": "insists",
                "observation": {
                    "title": "Still present",
                    "body": "The same behavior remains.",
                    "unchanged_from": "O-rejected",
                },
            },
        ],
        "new_observations": [],
    }


def test_decisions_cover_owner_set_and_validate_pairs_and_followups() -> None:
    context = _decisions_context()
    valid = _valid_decisions()
    result = validate_agent_result(_text(valid), AgentSchema.REVIEW_DECISIONS, context)
    assert result.payload.new_observations == ()  # type: ignore[union-attr]

    mutations = (
        (lambda value: value["decisions"].pop(), "missing_finding"),
        (
            lambda value: value["decisions"].append(
                {"finding_id": "F-X", "decision": "verified_fixed"}
            ),
            "finding_owner",
        ),
        (
            lambda value: value["decisions"].append(copy.deepcopy(value["decisions"][0])),
            "duplicate_finding",
        ),
        (lambda value: value["decisions"][0].update(decision="accepted_reason"), "decision_pair"),
        (
            lambda value: value["decisions"][0].update(
                decision="verified_fixed",
                observation={"title": "Closed", "body": "No", "severity_suggested": "high"},
            ),
            "closing_followup",
        ),
        (
            lambda value: value["decisions"][1]["observation"].update(unchanged_from="O-X"),
            "unknown_parent",
        ),
    )
    for mutate, expected in mutations:
        payload = copy.deepcopy(valid)
        mutate(payload)  # type: ignore[arg-type]
        assert expected in _codes(payload, AgentSchema.REVIEW_DECISIONS, context)


@pytest.mark.parametrize(
    ("disposition", "decision"),
    (
        ("fixed", "verified_fixed"),
        ("fixed", "still_present"),
        ("rejected", "accepted_reason"),
        ("rejected", "insists"),
        ("wont_fix", "accepted_reason"),
        ("wont_fix", "insists"),
    ),
)
def test_each_allowed_disposition_decision_pair_has_a_positive_witness(
    disposition: str, decision: str
) -> None:
    context = DecisionsContext(
        frozenset({"F-1"}),
        (("F-1", disposition),),
        (("F-1", "O-1"),),
        frozenset({"src/state.py"}),
    )
    payload = {
        "schema": "review.decisions.v1",
        "decisions": [{"finding_id": "F-1", "decision": decision}],
        "new_observations": [],
    }
    validate_agent_result(_text(payload), AgentSchema.REVIEW_DECISIONS, context)


@pytest.mark.parametrize(
    "observation",
    (
        {"title": "No source", "body": "Neither severity source is set."},
        {
            "title": "Two sources",
            "body": "Both severity sources are set.",
            "severity_suggested": "high",
            "unchanged_from": "O-rejected",
        },
    ),
)
def test_followup_observation_requires_exactly_one_severity_source(
    observation: dict[str, object],
) -> None:
    payload = _valid_decisions()
    payload["decisions"][1]["observation"] = observation  # type: ignore[index]
    assert "invalid_value" in _codes(payload, AgentSchema.REVIEW_DECISIONS, _decisions_context())


def test_decision_new_observations_use_blind_shape_file_scope_and_limit() -> None:
    context = _decisions_context()
    valid = _valid_decisions()
    valid["new_observations"] = [_observation()]
    validate_agent_result(_text(valid), AgentSchema.REVIEW_DECISIONS, context)

    for changes, expected in (
        ({"unchanged_from": "O-fixed"}, "extra_field"),
        ({"finding_id": "F-fixed"}, "extra_field"),
        ({"file": "foreign.py"}, "unknown_file"),
    ):
        payload = _valid_decisions()
        payload["new_observations"] = [_observation(**changes)]
        assert expected in _codes(payload, AgentSchema.REVIEW_DECISIONS, context)

    payload = _valid_decisions()
    payload["new_observations"] = [_observation(title=f"New {index}") for index in range(101)]
    assert "observation_limit" in _codes(payload, AgentSchema.REVIEW_DECISIONS, context)


def test_context_mismatch_and_contradictory_snapshots_are_caller_errors() -> None:
    payload = {"schema": "review.observations.v1", "observations": []}
    with pytest.raises(ValidationContextError):
        validate_agent_result(_text(payload), AgentSchema.REVIEW_OBSERVATIONS, GraphContext())
    with pytest.raises(ValidationContextError):
        ReconciliationValidationContext(
            frozenset({"O-1"}), frozenset({"F-1"}), (("F-1", "verified_fixed"),)
        )
    with pytest.raises(ValidationContextError):
        DecisionsContext(frozenset({"F-1"}), (("F-2", "fixed"),), (), frozenset({"src/state.py"}))
    with pytest.raises(ValidationContextError):
        ObservationsContext("src/state.py")  # type: ignore[arg-type]
    with pytest.raises(ValidationContextError):
        ObservationsContext(1)  # type: ignore[arg-type]
    with pytest.raises(ValidationContextError):
        ReconciliationValidationContext(
            frozenset({"O-1"}),
            frozenset(),
            (("F-1", 1),),  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationContextError):
        ReconciliationValidationContext(frozenset({"O-1"}), frozenset(), (("F-1", "unknown"),))
    with pytest.raises(ValidationContextError):
        ReconciliationValidationContext(
            frozenset({"O-1"}),
            frozenset(),
            1,  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationContextError):
        DecisionsContext(
            frozenset({"F-1"}),
            (("F-1", "unknown"),),
            (("F-1", "O-1"),),
            frozenset({"src/state.py"}),
        )
    with pytest.raises(ValidationContextError):
        DecisionsContext(
            frozenset({"F-1"}),
            (("F-1", "fixed"),),
            (),
            frozenset({"src/state.py"}),
        )


@pytest.mark.parametrize(
    "resolution", ("verified_fixed", "accepted_reason", "policy_closed", "human_decision")
)
def test_each_closed_resolution_is_valid_context_data(resolution: str) -> None:
    context = ReconciliationValidationContext(
        frozenset({"O-1"}), frozenset(), (("F-1", resolution),)
    )
    payload = {
        "schema": "review.reconciliation.v1",
        "groups": [
            _group(
                ["O-1"],
                "reaffirmed_closed" if resolution == "accepted_reason" else "reopen_closed",
                finding_id="F-1",
                **({} if resolution == "accepted_reason" else {"reason": "Returned"}),
            )
        ],
    }
    validate_agent_result(_text(payload), AgentSchema.REVIEW_RECONCILIATION, context)
