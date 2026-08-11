from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from metaswarm.domain.review import (
    AskHuman,
    CheckFacts,
    CloseClean,
    CycleInvariantError,
    EscalatingDispute,
    EscalatingDisputes,
    InvalidCampaignTransition,
    OpenFinding,
    ReviewCounters,
    StartAuthorRevision,
    StartReviewCheck,
    decide_after_check,
    decide_after_revision,
    next_campaign_state,
)


def _finding(finding_id: int = 1, first_round_id: int = 1) -> OpenFinding:
    return OpenFinding(finding_id, first_round_id)


def _disputes(*finding_ids: int) -> EscalatingDisputes:
    return EscalatingDisputes(
        tuple(
            EscalatingDispute(
                finding_id,
                "critical" if index == 0 else "high",
                "high",
                "policy-v1",
            )
            for index, finding_id in enumerate(finding_ids)
        )
    )


def _facts(
    *,
    author_revisions: int = 1,
    checks_before: int | None = None,
    maximum: int = 3,
    current_round_id: int = 10,
    open_findings: tuple[OpenFinding, ...] = (_finding(),),
    disputes: EscalatingDisputes | None = None,
    state: str = "fix_cycle",
) -> CheckFacts:
    return CheckFacts(
        campaign_state=state,  # type: ignore[arg-type]
        current_round_id=current_round_id,
        open_findings=open_findings,
        escalating_disputes=disputes,
        counters=ReviewCounters(
            author_revisions,
            author_revisions if checks_before is None else checks_before,
        ),
        max_author_revisions=maximum,
    )


@pytest.mark.parametrize(
    ("facts", "expected_type", "expected_reason"),
    (
        (_facts(author_revisions=3, open_findings=()), CloseClean, None),
        (
            _facts(author_revisions=1, disputes=_disputes(1)),
            AskHuman,
            "dispute",
        ),
        (_facts(author_revisions=1), StartAuthorRevision, None),
        (_facts(author_revisions=3), AskHuman, "cap_exhausted_same"),
    ),
    ids=("clean", "dispute", "revision", "cap"),
)
def test_four_decision_branches_have_normative_priority(
    facts: CheckFacts,
    expected_type: type[object],
    expected_reason: str | None,
) -> None:
    decision = decide_after_check(facts)
    assert isinstance(decision, expected_type)
    assert getattr(decision, "reason", None) == expected_reason


def test_dispute_preempts_revision_and_cap_without_losing_order() -> None:
    findings = (_finding(8, 2), _finding(3, 1), _finding(5, 2))
    disputes = _disputes(8, 3, 5)

    for author_revisions in (1, 3):
        decision = decide_after_check(
            _facts(
                author_revisions=author_revisions,
                open_findings=findings,
                disputes=disputes,
            )
        )
        assert isinstance(decision, AskHuman)
        assert decision.reason == "dispute"
        assert decision.snapshot is disputes
        assert tuple(item.finding_id for item in decision.snapshot.items) == (8, 3, 5)


@pytest.mark.parametrize("maximum", (1, 3, 4))
def test_cap_uses_only_author_revision_count_and_clean_still_wins(maximum: int) -> None:
    before_cap = decide_after_check(
        _facts(author_revisions=maximum - 1, maximum=maximum)
    )
    at_cap = decide_after_check(_facts(author_revisions=maximum, maximum=maximum))
    clean_at_cap = decide_after_check(
        _facts(author_revisions=maximum, maximum=maximum, open_findings=())
    )

    assert isinstance(before_cap, StartAuthorRevision)
    assert isinstance(at_cap, AskHuman)
    assert at_cap.reason == "cap_exhausted_same"
    assert isinstance(clean_at_cap, CloseClean)


@pytest.mark.parametrize(
    ("open_findings", "expected_reason"),
    (
        ((_finding(1, 1), _finding(2, 2)), "cap_exhausted_same"),
        ((_finding(1, 1), _finding(2, 10)), "cap_exhausted_new"),
    ),
)
def test_cap_reason_uses_first_round_of_every_open_finding(
    open_findings: tuple[OpenFinding, ...],
    expected_reason: str,
) -> None:
    decision = decide_after_check(
        _facts(author_revisions=3, current_round_id=10, open_findings=open_findings)
    )
    assert isinstance(decision, AskHuman)
    assert decision.reason == expected_reason
    assert decision.snapshot is None


def test_default_cap_sequence_always_checks_the_third_revision_then_decides() -> None:
    decisions: list[object] = []
    for revision_number in (1, 2, 3):
        decisions.append(decide_after_revision("fix_cycle"))
        decisions.append(
            decide_after_check(
                _facts(
                    author_revisions=revision_number,
                    checks_before=revision_number,
                    maximum=3,
                    current_round_id=revision_number + 1,
                    open_findings=(_finding(1, 1),),
                )
            )
        )

    assert isinstance(decisions[0], StartReviewCheck)
    assert isinstance(decisions[1], StartAuthorRevision)
    assert isinstance(decisions[2], StartReviewCheck)
    assert isinstance(decisions[3], StartAuthorRevision)
    assert isinstance(decisions[4], StartReviewCheck)
    assert isinstance(decisions[5], AskHuman)
    assert decisions[5].reason == "cap_exhausted_same"  # type: ignore[union-attr]

    clean_fourth_check = decide_after_check(
        _facts(
            author_revisions=3,
            checks_before=3,
            maximum=3,
            current_round_id=4,
            open_findings=(),
        )
    )
    assert isinstance(clean_fourth_check, CloseClean)
    assert next_campaign_state("fix_cycle", clean_fourth_check.campaign_event) == "closed_clean"


def test_policy_closed_findings_are_absent_from_facts_not_a_fifth_branch() -> None:
    all_policy_closed = decide_after_check(_facts(open_findings=()))
    another_finding_remains = decide_after_check(
        _facts(open_findings=(_finding(9, 2),), author_revisions=1)
    )

    assert isinstance(all_policy_closed, CloseClean)
    assert isinstance(another_finding_remains, StartAuthorRevision)


def test_repeated_decision_is_equal_frozen_and_does_not_mutate_facts() -> None:
    facts = _facts(disputes=_disputes(1))
    first = decide_after_check(facts)
    second = decide_after_check(facts)

    assert first == second
    assert facts == _facts(disputes=_disputes(1))
    with pytest.raises(FrozenInstanceError):
        facts.current_round_id = 11  # type: ignore[misc]


@pytest.mark.parametrize(
    "facts",
    (
        _facts(author_revisions=-1, checks_before=0),
        _facts(author_revisions=0, checks_before=-1),
        _facts(author_revisions=0, maximum=0),
        _facts(author_revisions=0, maximum=-1),
        _facts(author_revisions=4, checks_before=4, maximum=3),
        _facts(author_revisions=2, checks_before=1),
        _facts(author_revisions=True, checks_before=1),
        _facts(current_round_id=0),
    ),
    ids=(
        "negative-author",
        "negative-check",
        "zero-cap",
        "negative-cap",
        "author-over-cap",
        "counters-out-of-sync",
        "bool-counter",
        "nonpositive-round",
    ),
)
def test_unreachable_counter_or_round_snapshot_is_rejected(facts: CheckFacts) -> None:
    with pytest.raises(CycleInvariantError):
        decide_after_check(facts)


@pytest.mark.parametrize(
    "open_findings",
    (
        (_finding(1, 1), _finding(1, 2)),
        (_finding(1, 11),),
        [_finding(1, 1)],
    ),
    ids=("duplicate", "future-first-round", "mutable-container"),
)
def test_unreachable_open_finding_snapshot_is_rejected(open_findings: object) -> None:
    with pytest.raises(CycleInvariantError):
        decide_after_check(_facts(open_findings=open_findings))  # type: ignore[arg-type]


def test_dispute_must_belong_to_the_open_set() -> None:
    with pytest.raises(CycleInvariantError, match="open finding"):
        decide_after_check(
            _facts(
                open_findings=(_finding(1),),
                disputes=_disputes(2),
            )
        )


@pytest.mark.parametrize(
    "factory",
    (
        lambda: EscalatingDisputes(()),
        lambda: EscalatingDisputes(
            (
                EscalatingDispute(1, "high", "high", "v1"),
                EscalatingDispute(1, "critical", "high", "v1"),
            )
        ),
    ),
    ids=("empty", "duplicate-finding"),
)
def test_empty_or_duplicate_dispute_snapshot_is_rejected(factory: object) -> None:
    with pytest.raises(CycleInvariantError):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize(
    "state",
    (
        "discovery",
        "reconciliation",
        "closed_clean",
        "closed_escalated",
        "closed_cancelled",
        "unknown",
    ),
)
def test_after_check_decision_is_valid_only_in_fix_cycle(state: str) -> None:
    with pytest.raises(InvalidCampaignTransition) as raised:
        decide_after_check(_facts(state=state))
    assert (raised.value.current, raised.value.event) == (state, "decide_after_check")


@pytest.mark.parametrize(
    "factory",
    (
        lambda: OpenFinding(0, 1),
        lambda: OpenFinding(1, 0),
        lambda: EscalatingDispute(0, "high", "high", "v1"),
        lambda: EscalatingDispute(1, "", "high", "v1"),
        lambda: EscalatingDispute(1, "high", "", "v1"),
        lambda: EscalatingDispute(1, "high", "high", ""),
    ),
)
def test_domain_ids_and_dispute_snapshot_strings_are_well_formed(factory: object) -> None:
    with pytest.raises(CycleInvariantError):
        factory()  # type: ignore[operator]
