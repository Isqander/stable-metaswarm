from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from metaswarm.domain.review.reconcile_model import (
    ClosedFindingRef,
    FindingTarget,
    OpenFindingRef,
    ProposedGroup,
    ReconciliationContractError,
    ReconciliationInput,
    RoundObservation,
)
from metaswarm.domain.review.reconciliation import (
    derive_fix_check_contribution,
    reconcile,
)


def _observation(
    observation_id: int,
    *,
    seq: int | None = None,
    lane_id: int | None = None,
    lane_index: int | None = None,
) -> RoundObservation:
    return RoundObservation(
        id=observation_id,
        public_id=f"O-{observation_id}",
        seq=observation_id if seq is None else seq,
        round_id=10,
        lane_id=observation_id if lane_id is None else lane_id,
        lane_index=observation_id if lane_index is None else lane_index,
        severity="high",
        title=f"Observation {observation_id}",
        body=f"Body {observation_id}",
        file_path="src/module.py",
        line_start=observation_id,
        line_end=observation_id,
        evidence=f"evidence {observation_id}",
    )


OPEN = (
    OpenFindingRef(101, "F-101", "Open one"),
    OpenFindingRef(102, "F-102", "Open two"),
)
CLOSED = (
    ClosedFindingRef(201, "F-201", "Accepted", "accepted_reason", "reviewer"),
    ClosedFindingRef(202, "F-202", "Fixed", "verified_fixed", "reviewer"),
    ClosedFindingRef(203, "F-203", "Policy", "policy_closed", "policy"),
    ClosedFindingRef(
        204,
        "F-204",
        "Human",
        "human_decision",
        "human",
        human_answer_id=501,
        question_reason="dispute",
        closing_snapshot='{"decision":"keep_closed"}',
        escalation_severity="critical",
    ),
)


def _input(
    *observations: RoundObservation,
    context: str = "discovery",
    current: frozenset[int] = frozenset(),
    open_findings: tuple[OpenFindingRef, ...] = OPEN,
    closed_findings: tuple[ClosedFindingRef, ...] = CLOSED,
) -> ReconciliationInput:
    return ReconciliationInput(
        context=context,  # type: ignore[arg-type]
        current_round_id=10,
        observations=tuple(observations),
        open_findings=open_findings,
        closed_findings=closed_findings,
        current_round_finding_ids=current,
    )


@pytest.mark.parametrize("context", ("discovery", "fix_check_new"))
def test_four_outcomes_map_to_exact_links_and_round_participation(context: str) -> None:
    result = reconcile(
        _input(*(_observation(index) for index in range(1, 5)), context=context),
        (
            ProposedGroup(("O-1",), "new", title="New ground truth"),
            ProposedGroup(("O-2",), "existing_open", finding_id="F-101"),
            ProposedGroup(("O-3",), "reaffirmed_closed", finding_id="F-201"),
            ProposedGroup(
                ("O-4",),
                "reopen_closed",
                finding_id="F-202",
                reason="Regression returned",
            ),
        ),
    )

    assert result.context == context
    assert len(result.new_findings) == 1
    assert result.new_findings[0].title == "New ground truth"
    assert tuple(link.link_type for link in result.links) == (
        "first_seen",
        "recurrence",
        "reaffirmation",
        "reopening",
    )
    assert tuple(link.target for link in result.links) == (
        FindingTarget(new_finding_index=0),
        FindingTarget(finding_id=101),
        FindingTarget(finding_id=201),
        FindingTarget(finding_id=202),
    )
    assert tuple(round_.target for round_ in result.finding_rounds) == (
        FindingTarget(finding_id=101),
        FindingTarget(finding_id=202),
        FindingTarget(new_finding_index=0),
    )
    assert all(round_.entry_kind == "post_check" for round_ in result.finding_rounds)
    with pytest.raises(FrozenInstanceError):
        result.links[0].link_type = "recurrence"  # type: ignore[misc]


def test_new_group_preserves_title_and_selects_first_observation_and_owner() -> None:
    late_owner = _observation(1, seq=20, lane_id=11, lane_index=0)
    first_seen = _observation(2, seq=10, lane_id=22, lane_index=2)
    result = reconcile(
        _input(late_owner, first_seen),
        (ProposedGroup(("O-1", "O-2"), "new", title="  Exact title  "),),
    )
    intent = result.new_findings[0]
    assert intent.observation_ids == (2, 1)
    assert intent.observation_public_ids == ("O-2", "O-1")
    assert intent.title == "  Exact title  "
    assert (intent.first_observation_id, intent.first_observation_public_id) == (2, "O-2")
    assert (intent.owner_lane_id, intent.owner_lane_index) == (11, 0)


def test_repeated_existing_target_gets_one_round_with_minimum_lane_owner() -> None:
    result = reconcile(
        _input(
            _observation(1, lane_id=20, lane_index=2),
            _observation(2, lane_id=10, lane_index=0),
        ),
        (
            ProposedGroup(("O-1",), "existing_open", finding_id="F-101"),
            ProposedGroup(("O-2",), "existing_open", finding_id="F-101"),
        ),
    )
    assert len(result.links) == 2
    assert len(result.finding_rounds) == 1
    assert result.finding_rounds[0].target == FindingTarget(finding_id=101)
    assert (
        result.finding_rounds[0].owner_lane_id,
        result.finding_rounds[0].owner_lane_index,
    ) == (10, 0)


def test_current_round_target_does_not_get_a_second_participation() -> None:
    result = reconcile(
        _input(_observation(1), current=frozenset({101})),
        (ProposedGroup(("O-1",), "existing_open", finding_id="F-101"),),
    )
    assert len(result.links) == 1
    assert result.finding_rounds == ()


def test_fix_check_contribution_marks_new_identity_with_current_round() -> None:
    ready = reconcile(
        _input(_observation(1), _observation(2), context="fix_check_new"),
        (
            ProposedGroup(("O-1",), "new", title="Late finding"),
            ProposedGroup(("O-2",), "existing_open", finding_id="F-101"),
        ),
    )
    contribution = derive_fix_check_contribution(ready)
    assert len(contribution.author_work) == 2
    assert contribution.author_work[0].target == FindingTarget(finding_id=101)
    assert contribution.author_work[0].first_round_id is None
    assert contribution.author_work[1].target == FindingTarget(new_finding_index=0)
    assert contribution.author_work[1].first_round_id == 10
    assert not hasattr(contribution, "round_result")


def test_valid_result_is_independent_of_input_and_group_order() -> None:
    observations = (_observation(1), _observation(2), _observation(3))
    groups = (
        ProposedGroup(("O-1",), "new", title="First"),
        ProposedGroup(("O-2",), "existing_open", finding_id="F-101"),
        ProposedGroup(("O-3",), "reaffirmed_closed", finding_id="F-201"),
    )
    assert reconcile(_input(*observations), groups) == reconcile(
        _input(*reversed(observations)),
        tuple(reversed(groups)),
    )


def _single_issue(
    reconciliation_input: ReconciliationInput,
    groups: tuple[ProposedGroup, ...],
) -> str:
    with pytest.raises(ReconciliationContractError) as raised:
        reconcile(reconciliation_input, groups)
    assert len(raised.value.issues) == 1
    issue = raised.value.issues[0]
    assert issue.message
    return issue.code


@pytest.mark.parametrize(
    ("reconciliation_input", "groups", "expected_code"),
    (
        (_input(_observation(1)), (), "missing_observation"),
        (
            _input(_observation(1)),
            (
                ProposedGroup(("O-1",), "new", title="One"),
                ProposedGroup(("O-1",), "new", title="Two"),
            ),
            "duplicate_observation",
        ),
        (
            _input(_observation(1)),
            (ProposedGroup(("O-1",), "other"),),  # type: ignore[arg-type]
            "unknown_outcome",
        ),
        (
            _input(_observation(1)),
            (ProposedGroup(("O-1",), "existing_open", finding_id="F-999"),),
            "invalid_open_reference",
        ),
        (
            _input(_observation(1)),
            (
                ProposedGroup(
                    ("O-1",), "reopen_closed", finding_id="F-101", reason="returned"
                ),
            ),
            "invalid_closed_reference",
        ),
        (
            _input(_observation(1)),
            (ProposedGroup(("O-1",), "reaffirmed_closed", finding_id="F-202"),),
            "invalid_reaffirmed_resolution",
        ),
        (
            _input(_observation(1)),
            (ProposedGroup(("O-1",), "reopen_closed", finding_id="F-202", reason=" "),),
            "missing_reopen_reason",
        ),
        (
            _input(_observation(1)),
            (ProposedGroup(("O-1",), "new", finding_id="F-101", title="New"),),
            "unexpected_finding_reference",
        ),
        (
            _input(_observation(1)),
            (ProposedGroup(("O-1",), "existing_open"),),
            "missing_finding_reference",
        ),
        (
            _input(_observation(1)),
            (ProposedGroup(("O-1",), "new", title=" "),),
            "missing_new_title",
        ),
        (
            _input(_observation(1)),
            (
                ProposedGroup(
                    ("O-1",), "existing_open", finding_id="F-101", title="Rename"
                ),
            ),
            "unexpected_existing_title",
        ),
        (
            _input(_observation(1)),
            (ProposedGroup(("O-1", "O-999"), "new", title="Unknown input"),),
            "unknown_observation",
        ),
    ),
    ids=(
        "missing-observation",
        "duplicate-observation",
        "unknown-outcome",
        "invalid-open-reference",
        "invalid-closed-reference",
        "invalid-reaffirmed-resolution",
        "missing-reopen-reason",
        "unexpected-finding-reference",
        "missing-finding-reference",
        "missing-new-title",
        "unexpected-existing-title",
        "unknown-observation",
    ),
)
def test_each_contract_violation_has_an_isolated_issue(
    reconciliation_input: ReconciliationInput,
    groups: tuple[ProposedGroup, ...],
    expected_code: str,
) -> None:
    assert _single_issue(reconciliation_input, groups) == expected_code


@pytest.mark.parametrize("finding_id", ("F-202", "F-203", "F-204"))
def test_q44_requires_reopen_after_non_accepted_closure(finding_id: str) -> None:
    code = _single_issue(
        _input(_observation(1)),
        (ProposedGroup(("O-1",), "reaffirmed_closed", finding_id=finding_id),),
    )
    assert code == "invalid_reaffirmed_resolution"


def test_independent_contract_issues_are_collected_in_stable_order() -> None:
    groups = (
        ProposedGroup(("O-1",), "new", finding_id="F-101", title=" "),
        ProposedGroup(("O-999",), "existing_open", finding_id="F-404", title="Rename"),
    )
    with pytest.raises(ReconciliationContractError) as raised:
        reconcile(_input(_observation(1), _observation(2)), groups)
    assert tuple(issue.code for issue in raised.value.issues) == (
        "missing_observation",
        "unexpected_finding_reference",
        "missing_new_title",
        "unknown_observation",
        "invalid_open_reference",
        "unexpected_existing_title",
    )
