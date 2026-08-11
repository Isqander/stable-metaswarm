from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type CampaignState = Literal[
    "discovery",
    "reconciliation",
    "fix_cycle",
    "closed_clean",
    "closed_escalated",
    "closed_cancelled",
]
type RoundResult = Literal["clean", "needs_revision", "escalated"]
type ReviewStopReason = Literal["dispute", "cap_exhausted_same", "cap_exhausted_new"]
type CampaignEvent = Literal[
    "discovery_completed",
    "reconciliation_has_findings",
    "reconciliation_clean",
    "check_needs_revision",
    "human_gate_opened",
    "human_extra_revision",
    "check_clean",
    "human_finalized",
    "cancelled",
]


class CycleInvariantError(ValueError):
    """Review-cycle facts or an action combination are unreachable."""


class InvalidCampaignTransition(ValueError):
    """A campaign event is not allowed from the current state."""

    def __init__(self, current: object, event: object) -> None:
        self.current = current
        self.event = event
        super().__init__(f"campaign transition is not allowed: {current!r} + {event!r}")


def _require_positive_id(value: object, field_name: str) -> None:
    if type(value) is not int or value <= 0:
        raise CycleInvariantError(f"{field_name} must be a positive int")


def _require_nonempty_string(value: object, field_name: str) -> None:
    if type(value) is not str or not value:
        raise CycleInvariantError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ReviewCounters:
    author_revision_count: int
    review_check_count_before: int


@dataclass(frozen=True, slots=True)
class OpenFinding:
    finding_id: int
    first_round_id: int

    def __post_init__(self) -> None:
        _require_positive_id(self.finding_id, "finding_id")
        _require_positive_id(self.first_round_id, "first_round_id")


@dataclass(frozen=True, slots=True)
class EscalatingDispute:
    finding_id: int
    escalation_severity: str
    severity_threshold: str
    policy_version: str

    def __post_init__(self) -> None:
        _require_positive_id(self.finding_id, "finding_id")
        _require_nonempty_string(self.escalation_severity, "escalation_severity")
        _require_nonempty_string(self.severity_threshold, "severity_threshold")
        _require_nonempty_string(self.policy_version, "policy_version")


@dataclass(frozen=True, slots=True)
class EscalatingDisputes:
    items: tuple[EscalatingDispute, ...]

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or not self.items:
            raise CycleInvariantError("escalating disputes must be a non-empty tuple")
        if any(not isinstance(item, EscalatingDispute) for item in self.items):
            raise CycleInvariantError("escalating disputes contain an invalid item")
        finding_ids = tuple(item.finding_id for item in self.items)
        if len(finding_ids) != len(set(finding_ids)):
            raise CycleInvariantError("escalating disputes contain duplicate findings")


@dataclass(frozen=True, slots=True)
class CheckFacts:
    campaign_state: CampaignState
    current_round_id: int
    open_findings: tuple[OpenFinding, ...]
    escalating_disputes: EscalatingDisputes | None
    counters: ReviewCounters
    max_author_revisions: int


@dataclass(frozen=True, slots=True)
class CloseClean:
    round_result: Literal["clean"] = "clean"
    campaign_event: Literal["check_clean"] = "check_clean"

    def __post_init__(self) -> None:
        if self.round_result != "clean" or self.campaign_event != "check_clean":
            raise CycleInvariantError("CloseClean requires clean/check_clean")


@dataclass(frozen=True, slots=True)
class StartAuthorRevision:
    round_result: Literal["needs_revision"] = "needs_revision"
    campaign_event: Literal["check_needs_revision"] = "check_needs_revision"

    def __post_init__(self) -> None:
        if (
            self.round_result != "needs_revision"
            or self.campaign_event != "check_needs_revision"
        ):
            raise CycleInvariantError(
                "StartAuthorRevision requires needs_revision/check_needs_revision"
            )


@dataclass(frozen=True, slots=True)
class AskHuman:
    reason: ReviewStopReason
    snapshot: EscalatingDisputes | None = None
    round_result: Literal["escalated"] = "escalated"
    campaign_event: Literal["human_gate_opened"] = "human_gate_opened"

    def __post_init__(self) -> None:
        if self.round_result != "escalated" or self.campaign_event != "human_gate_opened":
            raise CycleInvariantError("AskHuman requires escalated/human_gate_opened")
        if self.reason not in {"dispute", "cap_exhausted_same", "cap_exhausted_new"}:
            raise CycleInvariantError(f"unknown review stop reason: {self.reason!r}")
        if (self.reason == "dispute") != (self.snapshot is not None):
            raise CycleInvariantError("only dispute requires an escalating-disputes snapshot")
        if self.snapshot is not None and not isinstance(self.snapshot, EscalatingDisputes):
            raise CycleInvariantError("AskHuman snapshot has an invalid type")


@dataclass(frozen=True, slots=True)
class StartReviewCheck:
    """Start the mandatory check after an author revision without a state transition."""


type AfterCheckDecision = CloseClean | StartAuthorRevision | AskHuman
