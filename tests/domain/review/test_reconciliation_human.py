from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from metaswarm.domain.review.reconcile_model import (
    AwaitHumanReopen,
    ClosedFindingRef,
    HumanReopenAnswer,
    HumanReopenResolutionError,
    OpenFindingRef,
    ProposedGroup,
    ReadyReconciliation,
    ReconciliationInput,
    ReconciliationInputError,
    ReconciliationIssue,
    RoundObservation,
)
from metaswarm.domain.review.reconciliation import (
    build_reconcile_failed_question,
    derive_fix_check_contribution,
    derive_primary_round_outcome,
    reconcile,
    resolve_human_reopens,
)


def _observation(
    observation_id: int,
    *,
    seq: int | None = None,
    lane_id: int | None = None,
    lane_index: int | None = None,
    body: str | None = None,
    evidence: str | None = None,
) -> RoundObservation:
    return RoundObservation(
        id=observation_id,
        public_id=f"O-{observation_id}",
        seq=observation_id if seq is None else seq,
        round_id=10,
        lane_id=observation_id if lane_id is None else lane_id,
        lane_index=observation_id if lane_index is None else lane_index,
        severity="critical",
        title=f"Observation {observation_id}",
        body=f"Body {observation_id}" if body is None else body,
        file_path=None,
        line_start=None,
        line_end=None,
        evidence=evidence,
    )


HUMAN_CLOSED = ClosedFindingRef(
    204,
    "F-204",
    "Human decision",
    "human_decision",
    "human",
    human_answer_id=501,
    question_reason="dispute",
    closing_snapshot='{"decision":"keep_closed"}',
    escalation_severity="critical",
)
SECOND_HUMAN_CLOSED = ClosedFindingRef(
    205,
    "F-205",
    "Another human decision",
    "human_decision",
    "human",
    human_answer_id=502,
    question_reason="cap_exhausted_same",
    closing_snapshot='{"decision":"accept_as_is"}',
    escalation_severity="high",
)
REVIEWER_CLOSED = ClosedFindingRef(
    202,
    "F-202",
    "Reviewer decision",
    "verified_fixed",
    "reviewer",
)
ACCEPTED_CLOSED = ClosedFindingRef(
    201,
    "F-201",
    "Accepted reason",
    "accepted_reason",
    "reviewer",
)
OPEN = OpenFindingRef(101, "F-101", "Open finding")


def _input(
    *observations: RoundObservation,
    context: str = "discovery",
    current: frozenset[int] = frozenset(),
    closed: tuple[ClosedFindingRef, ...] = (
        ACCEPTED_CLOSED,
        REVIEWER_CLOSED,
        HUMAN_CLOSED,
        SECOND_HUMAN_CLOSED,
    ),
) -> ReconciliationInput:
    return ReconciliationInput(
        context=context,  # type: ignore[arg-type]
        current_round_id=10,
        observations=tuple(observations),
        open_findings=(OPEN,),
        closed_findings=closed,
        current_round_finding_ids=current,
    )


def test_human_closed_finding_waits_without_link_or_round_completion() -> None:
    result = reconcile(
        _input(_observation(1), _observation(2, lane_id=20, lane_index=0)),
        (
            ProposedGroup(("O-1",), "new", title="Safe non-human intent"),
            ProposedGroup(
                ("O-2",),
                "reopen_closed",
                finding_id="F-204",
                reason="The defect returned",
            ),
        ),
    )
    assert isinstance(result, AwaitHumanReopen)
    assert tuple(link.observation_public_id for link in result.links) == ("O-1",)
    assert tuple(round_.target.new_finding_index for round_ in result.finding_rounds) == (0,)
    assert not hasattr(result, "round_result")
    assert len(result.requests) == 1
    request = result.requests[0]
    assert (request.finding_id, request.finding_public_id) == (204, "F-204")
    assert tuple(item.observation_public_id for item in request.items) == ("O-2",)
    assert tuple(item.reason for item in request.items) == ("The defect returned",)
    assert request.original_human_answer_id == 501
    assert request.original_question_reason == "dispute"
    assert request.closing_snapshot == '{"decision":"keep_closed"}'
    assert request.escalation_severity == "critical"
    with pytest.raises(FrozenInstanceError):
        request.finding_id = 999  # type: ignore[misc]


@pytest.mark.parametrize(
    ("choice", "expected_link", "expected_outcome", "round_count"),
    (
        ("reopen", "reopening", "needs_revision", 1),
        ("keep_closed", "reaffirmation", "clean", 0),
    ),
)
def test_human_answer_maps_to_exact_link_and_only_then_completes(
    choice: str,
    expected_link: str,
    expected_outcome: str,
    round_count: int,
) -> None:
    awaiting = reconcile(
        _input(_observation(1, lane_id=7, lane_index=2)),
        (
            ProposedGroup(
                ("O-1",),
                "reopen_closed",
                finding_id="F-204",
                reason="Returned",
            ),
        ),
    )
    assert isinstance(awaiting, AwaitHumanReopen)
    ready = resolve_human_reopens(
        awaiting,
        (HumanReopenAnswer(204, choice),),  # type: ignore[arg-type]
    )
    assert ready.links[0].link_type == expected_link
    assert ready.links[0].reason == ("Returned" if choice == "reopen" else None)
    assert len(ready.finding_rounds) == round_count
    assert derive_primary_round_outcome(ready) == expected_outcome


def test_multiple_groups_for_one_human_finding_form_one_ordered_request() -> None:
    awaiting = reconcile(
        _input(
            _observation(1, seq=20, lane_id=20, lane_index=2),
            _observation(2, seq=10, lane_id=10, lane_index=0),
        ),
        (
            ProposedGroup(
                ("O-1",), "reopen_closed", finding_id="F-204", reason="Reason one"
            ),
            ProposedGroup(
                ("O-2",), "reopen_closed", finding_id="F-204", reason="Reason two"
            ),
        ),
    )
    assert isinstance(awaiting, AwaitHumanReopen)
    assert len(awaiting.requests) == 1
    assert tuple(item.observation_public_id for item in awaiting.requests[0].items) == (
        "O-2",
        "O-1",
    )
    ready = resolve_human_reopens(awaiting, (HumanReopenAnswer(204, "reopen"),))
    assert tuple(link.observation_public_id for link in ready.links) == ("O-2", "O-1")
    assert len(ready.finding_rounds) == 1
    assert ready.finding_rounds[0].owner_lane_id == 10


@pytest.mark.parametrize(
    "answers",
    (
        (),
        (HumanReopenAnswer(999, "reopen"),),
    ),
    ids=("partial", "foreign"),
)
def test_incomplete_or_foreign_human_answer_does_not_mutate_pending(
    answers: tuple[HumanReopenAnswer, ...],
) -> None:
    awaiting = reconcile(
        _input(_observation(1), _observation(2)),
        (
            ProposedGroup(
                ("O-1",), "reopen_closed", finding_id="F-204", reason="One"
            ),
            ProposedGroup(
                ("O-2",), "reopen_closed", finding_id="F-205", reason="Two"
            ),
        ),
    )
    assert isinstance(awaiting, AwaitHumanReopen)
    before = deepcopy(awaiting)
    with pytest.raises(HumanReopenResolutionError):
        resolve_human_reopens(awaiting, answers)
    assert awaiting == before


@pytest.mark.parametrize(
    "answers",
    (
        (HumanReopenAnswer(204, "reopen"), HumanReopenAnswer(204, "keep_closed")),
        (HumanReopenAnswer(204, "other"),),  # type: ignore[arg-type]
    ),
    ids=("duplicate", "unknown-choice"),
)
def test_duplicate_or_unknown_human_answer_is_rejected_in_isolation(
    answers: tuple[HumanReopenAnswer, ...],
) -> None:
    awaiting = reconcile(
        _input(_observation(1)),
        (
            ProposedGroup(
                ("O-1",), "reopen_closed", finding_id="F-204", reason="One"
            ),
        ),
    )
    assert isinstance(awaiting, AwaitHumanReopen)
    before = deepcopy(awaiting)
    with pytest.raises(HumanReopenResolutionError):
        resolve_human_reopens(awaiting, answers)
    assert awaiting == before


def test_complete_answer_set_resolves_multiple_human_requests_atomically() -> None:
    awaiting = reconcile(
        _input(_observation(1), _observation(2)),
        (
            ProposedGroup(
                ("O-1",), "reopen_closed", finding_id="F-204", reason="One"
            ),
            ProposedGroup(
                ("O-2",), "reopen_closed", finding_id="F-205", reason="Two"
            ),
        ),
    )
    assert isinstance(awaiting, AwaitHumanReopen)
    ready = resolve_human_reopens(
        awaiting,
        (HumanReopenAnswer(205, "keep_closed"), HumanReopenAnswer(204, "reopen")),
    )
    assert tuple(link.link_type for link in ready.links) == (
        "reopening",
        "reaffirmation",
    )
    assert len(ready.finding_rounds) == 1


def test_human_reopen_does_not_duplicate_existing_current_round_participation() -> None:
    awaiting = reconcile(
        _input(_observation(1), current=frozenset({204})),
        (
            ProposedGroup(
                ("O-1",), "reopen_closed", finding_id="F-204", reason="Returned"
            ),
        ),
    )
    assert isinstance(awaiting, AwaitHumanReopen)
    ready = resolve_human_reopens(awaiting, (HumanReopenAnswer(204, "reopen"),))
    assert tuple(link.link_type for link in ready.links) == ("reopening",)
    assert ready.finding_rounds == ()


@pytest.mark.parametrize(
    ("groups", "expected"),
    (
        ((), "clean"),
        ((ProposedGroup(("O-1",), "reaffirmed_closed", finding_id="F-201"),), "clean"),
        ((ProposedGroup(("O-1",), "new", title="New"),), "needs_revision"),
        ((ProposedGroup(("O-1",), "existing_open", finding_id="F-101"),), "needs_revision"),
        (
            (
                ProposedGroup(
                    ("O-1",),
                    "reopen_closed",
                    finding_id="F-202",
                    reason="Returned",
                ),
            ),
            "needs_revision",
        ),
    ),
    ids=("no-observations", "reaffirmation-only", "new", "recurrence", "reopening"),
)
def test_primary_round_outcome_depends_only_on_author_work(
    groups: tuple[ProposedGroup, ...],
    expected: str,
) -> None:
    observations = () if not groups else (_observation(1),)
    ready = reconcile(_input(*observations), groups)
    assert isinstance(ready, ReadyReconciliation)
    assert derive_primary_round_outcome(ready) == expected


def test_context_specific_completion_rejects_the_other_context() -> None:
    discovery = reconcile(_input(), ())
    fix_check = reconcile(_input(context="fix_check_new"), ())
    assert isinstance(discovery, ReadyReconciliation)
    assert isinstance(fix_check, ReadyReconciliation)
    with pytest.raises(ReconciliationInputError):
        derive_fix_check_contribution(discovery)
    with pytest.raises(ReconciliationInputError):
        derive_primary_round_outcome(fix_check)


def test_pending_human_result_is_rejected_by_both_completion_functions() -> None:
    group = ProposedGroup(
        ("O-1",), "reopen_closed", finding_id="F-204", reason="Returned"
    )
    discovery = reconcile(_input(_observation(1)), (group,))
    fix_check = reconcile(_input(_observation(1), context="fix_check_new"), (group,))
    assert isinstance(discovery, AwaitHumanReopen)
    assert isinstance(fix_check, AwaitHumanReopen)
    with pytest.raises(ReconciliationInputError):
        derive_primary_round_outcome(discovery)  # type: ignore[arg-type]
    with pytest.raises(ReconciliationInputError):
        derive_fix_check_contribution(fix_check)  # type: ignore[arg-type]


def test_reconcile_failed_question_preserves_raw_snapshot_without_fallback_intents() -> None:
    first = _observation(1, seq=20, body="Body\r\nraw", evidence="  exact  ")
    second = _observation(2, seq=10, body="Другой текст", evidence=None)
    source = ReconciliationInput(
        "discovery",
        10,
        (first, second),
        (OPEN,),
        (HUMAN_CLOSED, ACCEPTED_CLOSED),
        frozenset(),
    )
    issues = (ReconciliationIssue("missing_observation", None, ("O-1",), None, "missing"),)
    question = build_reconcile_failed_question(source, issues)

    assert question.reason == "reconcile_failed"
    assert question.observations == (second, first)
    assert question.observations[1].body == "Body\r\nraw"
    assert question.observations[1].evidence == "  exact  "
    assert question.open_findings == (OPEN,)
    assert question.closed_findings == (ACCEPTED_CLOSED, HUMAN_CLOSED)
    assert question.validation_issues == issues
    assert not hasattr(question, "new_findings")
    assert not hasattr(question, "links")


@pytest.mark.parametrize(
    "closed",
    (
        ClosedFindingRef(
            204,
            "F-204",
            "Human",
            "human_decision",
            "human",
            question_reason="dispute",
            closing_snapshot="snapshot",
            escalation_severity="critical",
        ),
        ClosedFindingRef(
            204,
            "F-204",
            "Human",
            "human_decision",
            "human",
            human_answer_id=1,
            closing_snapshot="snapshot",
            escalation_severity="critical",
        ),
        ClosedFindingRef(
            204,
            "F-204",
            "Human",
            "human_decision",
            "human",
            human_answer_id=1,
            question_reason="dispute",
            escalation_severity="critical",
        ),
        ClosedFindingRef(
            204,
            "F-204",
            "Human",
            "human_decision",
            "human",
            human_answer_id=1,
            question_reason="dispute",
            closing_snapshot="snapshot",
        ),
    ),
    ids=("answer", "reason", "snapshot", "severity"),
)
def test_human_closed_ledger_requires_complete_reopen_context(
    closed: ClosedFindingRef,
) -> None:
    with pytest.raises(ReconciliationInputError):
        reconcile(_input(closed=(closed,)), ())
