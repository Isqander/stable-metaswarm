from __future__ import annotations

from .model import (
    AfterCheckDecision,
    AskHuman,
    CampaignEvent,
    CampaignState,
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
)

_TRANSITIONS: tuple[tuple[CampaignState, CampaignEvent, CampaignState], ...] = (
    ("discovery", "discovery_completed", "reconciliation"),
    ("discovery", "cancelled", "closed_cancelled"),
    ("reconciliation", "reconciliation_has_findings", "fix_cycle"),
    ("reconciliation", "reconciliation_clean", "closed_clean"),
    ("reconciliation", "cancelled", "closed_cancelled"),
    ("fix_cycle", "check_needs_revision", "fix_cycle"),
    ("fix_cycle", "human_gate_opened", "fix_cycle"),
    ("fix_cycle", "human_extra_revision", "fix_cycle"),
    ("fix_cycle", "check_clean", "closed_clean"),
    ("fix_cycle", "human_finalized", "closed_escalated"),
    ("fix_cycle", "cancelled", "closed_cancelled"),
)


def next_campaign_state(current: CampaignState, event: CampaignEvent) -> CampaignState:
    for from_state, candidate_event, to_state in _TRANSITIONS:
        if current == from_state and event == candidate_event:
            return to_state
    raise InvalidCampaignTransition(current, event)


def _require_int(value: object, field_name: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise CycleInvariantError(f"{field_name} must be an int >= {minimum}")
    return value


def _validate_open_findings(
    open_findings: object,
    current_round_id: int,
) -> frozenset[int]:
    if type(open_findings) is not tuple:
        raise CycleInvariantError("open_findings must be a tuple")
    finding_ids: list[int] = []
    for item in open_findings:
        if not isinstance(item, OpenFinding):
            raise CycleInvariantError("open_findings contain an invalid item")
        if item.first_round_id > current_round_id:
            raise CycleInvariantError("an open finding cannot first appear after the current round")
        finding_ids.append(item.finding_id)
    if len(finding_ids) != len(set(finding_ids)):
        raise CycleInvariantError("open_findings contain duplicate findings")
    return frozenset(finding_ids)


def _validate_disputes(
    disputes: object,
    open_finding_ids: frozenset[int],
) -> EscalatingDisputes | None:
    if disputes is None:
        return None
    if not isinstance(disputes, EscalatingDisputes):
        raise CycleInvariantError("escalating_disputes has an invalid type")
    if type(disputes.items) is not tuple or not disputes.items:
        raise CycleInvariantError("escalating disputes must be a non-empty tuple")
    finding_ids: list[int] = []
    for item in disputes.items:
        if not isinstance(item, EscalatingDispute):
            raise CycleInvariantError("escalating disputes contain an invalid item")
        finding_ids.append(item.finding_id)
    if len(finding_ids) != len(set(finding_ids)):
        raise CycleInvariantError("escalating disputes contain duplicate findings")
    if not set(finding_ids).issubset(open_finding_ids):
        raise CycleInvariantError("an escalating dispute must refer to an open finding")
    return disputes


def _validate_check_facts(facts: CheckFacts) -> EscalatingDisputes | None:
    if facts.campaign_state != "fix_cycle":
        raise InvalidCampaignTransition(facts.campaign_state, "decide_after_check")

    current_round_id = _require_int(facts.current_round_id, "current_round_id", minimum=1)
    max_author_revisions = _require_int(
        facts.max_author_revisions,
        "max_author_revisions",
        minimum=1,
    )
    if not isinstance(facts.counters, ReviewCounters):
        raise CycleInvariantError("counters have an invalid type")
    author_revision_count = _require_int(
        facts.counters.author_revision_count,
        "author_revision_count",
        minimum=0,
    )
    review_check_count_before = _require_int(
        facts.counters.review_check_count_before,
        "review_check_count_before",
        minimum=0,
    )
    if author_revision_count > max_author_revisions:
        raise CycleInvariantError("author revision count exceeds the configured cap")
    if review_check_count_before != author_revision_count:
        raise CycleInvariantError("review and author counters are out of sync before the check")

    open_finding_ids = _validate_open_findings(facts.open_findings, current_round_id)
    return _validate_disputes(facts.escalating_disputes, open_finding_ids)


def _validate_post_decision_count(facts: CheckFacts) -> None:
    expected_count = facts.counters.review_check_count_before + 1
    if expected_count > facts.max_author_revisions + 1:
        raise CycleInvariantError("the completed check would exceed the derived review-check cap")


def decide_after_check(facts: CheckFacts) -> AfterCheckDecision:
    disputes = _validate_check_facts(facts)

    if not facts.open_findings:
        decision: AfterCheckDecision = CloseClean()
    elif disputes is not None:
        decision = AskHuman(reason="dispute", snapshot=disputes)
    elif facts.counters.author_revision_count < facts.max_author_revisions:
        decision = StartAuthorRevision()
    else:
        reason = (
            "cap_exhausted_new"
            if any(
                finding.first_round_id == facts.current_round_id
                for finding in facts.open_findings
            )
            else "cap_exhausted_same"
        )
        decision = AskHuman(reason=reason)

    next_campaign_state(facts.campaign_state, decision.campaign_event)
    _validate_post_decision_count(facts)
    return decision


def decide_after_revision(campaign_state: CampaignState) -> StartReviewCheck:
    if campaign_state != "fix_cycle":
        raise InvalidCampaignTransition(campaign_state, "start_review_check")
    return StartReviewCheck()
