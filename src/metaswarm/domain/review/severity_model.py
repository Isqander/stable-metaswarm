from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type Severity = Literal["low", "medium", "high", "critical"]
type ObservationLinkType = Literal[
    "first_seen",
    "recurrence",
    "reaffirmation",
    "reopening",
]
type ResolutionKind = Literal[
    "verified_fixed",
    "accepted_reason",
    "policy_closed",
    "human_decision",
]
type ResolutionAuthority = Literal["reviewer", "policy", "human"]

_SEVERITIES = ("low", "medium", "high", "critical")


class _SeverityDomainError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        finding_id: int | None = None,
        observation_id: int | None = None,
    ) -> None:
        self.code = code
        self.finding_id = finding_id
        self.observation_id = observation_id
        super().__init__(message)


class SeverityInputError(_SeverityDomainError):
    """A severity value or source form is outside the closed input contract."""


class SeverityChainError(_SeverityDomainError):
    """An unchanged-from edge does not belong to the target finding period."""


class SeverityFactsError(_SeverityDomainError):
    """Persisted severity facts are structurally inconsistent."""


class SeverityPolicyError(_SeverityDomainError):
    """A resolution or dispute-policy value violates the closed policy contract."""


def _is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _is_nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value.strip())


@dataclass(frozen=True, slots=True)
class ObservationSeverityInput:
    severity_suggested: Severity | None
    unchanged_from_id: int | None

    def __post_init__(self) -> None:
        if (self.severity_suggested is None) == (self.unchanged_from_id is None):
            raise SeverityInputError(
                "invalid_severity_source",
                "exactly one severity source must be provided",
            )
        if self.severity_suggested is not None and self.severity_suggested not in _SEVERITIES:
            raise SeverityInputError(
                "unknown_severity",
                f"unknown severity: {self.severity_suggested!r}",
            )
        if self.unchanged_from_id is not None and not _is_positive_int(
            self.unchanged_from_id
        ):
            raise SeverityInputError(
                "invalid_severity_source",
                "unchanged_from_id must be a positive int",
                observation_id=self.unchanged_from_id
                if type(self.unchanged_from_id) is int
                else None,
            )


@dataclass(frozen=True, slots=True)
class ParentObservationContext:
    observation_id: int
    campaign_id: int
    finding_id: int
    seq: int
    period_start_event_id: int | None
    link_event_id: int
    severity_effective: Severity


@dataclass(frozen=True, slots=True)
class TargetObservationContext:
    campaign_id: int
    finding_id: int
    seq: int
    period_start_event_id: int | None


@dataclass(frozen=True, slots=True)
class LinkedObservationFact:
    observation_id: int
    finding_id: int
    event_id: int
    link_type: ObservationLinkType
    severity_effective: Severity


@dataclass(frozen=True, slots=True)
class ResolutionFact:
    finding_id: int
    event_id: int
    resolution: ResolutionKind
    resolution_authority: ResolutionAuthority
    closes_severity_period: bool


@dataclass(frozen=True, slots=True)
class SeverityOverrideFact:
    finding_id: int
    event_id: int
    old_severity: Severity
    new_severity: Severity
    reason: str


@dataclass(frozen=True, slots=True)
class SeverityFacts:
    finding_id: int
    initial_event_id: int
    linked_observations: tuple[LinkedObservationFact, ...]
    resolutions: tuple[ResolutionFact, ...]
    overrides: tuple[SeverityOverrideFact, ...]


@dataclass(frozen=True, slots=True)
class SeveritySnapshot:
    finding_id: int
    period_start_event_id: int | None
    escalation_severity: Severity | None
    historical_max: Severity | None


@dataclass(frozen=True, slots=True)
class ResolutionEffect:
    resolution: ResolutionKind
    resolution_authority: ResolutionAuthority
    closes_severity_period: bool


@dataclass(frozen=True, slots=True)
class DisputeCandidate:
    finding_id: int
    campaign_id: int
    status: str
    reviewer_decision: str
    severity: SeveritySnapshot


@dataclass(frozen=True, slots=True)
class PolicyClosureIntent:
    finding_id: int
    escalation_severity: Severity
    severity_threshold: Severity
    policy_version: str
    resolution: Literal["policy_closed"] = "policy_closed"
    resolution_authority: Literal["policy"] = "policy"
    closes_severity_period: Literal[False] = False

    def __post_init__(self) -> None:
        if not _is_positive_int(self.finding_id):
            raise SeverityPolicyError(
                "invalid_dispute_partition",
                "policy closure requires a positive finding ID",
            )
        if (
            self.escalation_severity not in _SEVERITIES
            or self.severity_threshold not in _SEVERITIES
            or not _is_nonempty_string(self.policy_version)
            or _severity_index(self.escalation_severity)
            >= _severity_index(self.severity_threshold)
            or self.resolution != "policy_closed"
            or self.resolution_authority != "policy"
            or self.closes_severity_period is not False
        ):
            raise SeverityPolicyError(
                "invalid_dispute_partition",
                "policy closure has an invalid fixed policy form",
                finding_id=self.finding_id,
            )


@dataclass(frozen=True, slots=True)
class EscalatingDisputeFact:
    finding_id: int
    escalation_severity: Severity
    severity_threshold: Severity
    policy_version: str

    def __post_init__(self) -> None:
        if (
            not _is_positive_int(self.finding_id)
            or self.escalation_severity not in _SEVERITIES
            or self.severity_threshold not in _SEVERITIES
            or not _is_nonempty_string(self.policy_version)
            or _severity_index(self.escalation_severity)
            < _severity_index(self.severity_threshold)
        ):
            raise SeverityPolicyError(
                "invalid_dispute_partition",
                "escalating dispute has an invalid policy form",
                finding_id=self.finding_id if type(self.finding_id) is int else None,
            )


def _severity_index(value: object) -> int:
    try:
        return _SEVERITIES.index(value)  # type: ignore[arg-type]
    except ValueError:
        return -1


@dataclass(frozen=True, slots=True)
class EscalationBatch:
    items: tuple[EscalatingDisputeFact, ...]

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or not self.items:
            raise SeverityPolicyError(
                "invalid_dispute_partition",
                "escalation batch must be a non-empty tuple",
            )
        if any(not isinstance(item, EscalatingDisputeFact) for item in self.items):
            raise SeverityPolicyError(
                "invalid_dispute_partition",
                "escalation batch contains an invalid item",
            )
        finding_ids = tuple(item.finding_id for item in self.items)
        if len(finding_ids) != len(set(finding_ids)):
            raise SeverityPolicyError(
                "invalid_dispute_partition",
                "escalation batch contains duplicate findings",
            )
        expected = tuple(
            sorted(
                self.items,
                key=lambda item: (-_severity_index(item.escalation_severity), item.finding_id),
            )
        )
        if self.items != expected:
            raise SeverityPolicyError(
                "invalid_dispute_partition",
                "escalation batch has a non-canonical order",
            )
        policy_snapshots = frozenset(
            (item.severity_threshold, item.policy_version) for item in self.items
        )
        if self.items and len(policy_snapshots) != 1:
            raise SeverityPolicyError(
                "invalid_dispute_partition",
                "escalation batch mixes campaign policy snapshots",
            )


@dataclass(frozen=True, slots=True)
class DisputePolicyResult:
    escalating: EscalationBatch | None
    policy_closures: tuple[PolicyClosureIntent, ...]

    def __post_init__(self) -> None:
        if self.escalating is not None and not isinstance(self.escalating, EscalationBatch):
            raise SeverityPolicyError(
                "invalid_dispute_partition",
                "escalating result has an invalid type",
            )
        if type(self.policy_closures) is not tuple or any(
            not isinstance(item, PolicyClosureIntent) for item in self.policy_closures
        ):
            raise SeverityPolicyError(
                "invalid_dispute_partition",
                "policy closures must be an immutable tuple",
            )
        closure_ids = tuple(item.finding_id for item in self.policy_closures)
        if len(closure_ids) != len(set(closure_ids)) or closure_ids != tuple(
            sorted(closure_ids)
        ):
            raise SeverityPolicyError(
                "invalid_dispute_partition",
                "policy closures must be unique and ordered by finding ID",
            )
        escalating_ids = (
            frozenset(item.finding_id for item in self.escalating.items)
            if self.escalating is not None
            else frozenset()
        )
        if escalating_ids.intersection(closure_ids):
            raise SeverityPolicyError(
                "invalid_dispute_partition",
                "a finding cannot be both escalated and policy-closed",
            )
        policy_snapshots = {
            (item.severity_threshold, item.policy_version)
            for item in self.policy_closures
        }
        if self.escalating is not None:
            policy_snapshots.update(
                (item.severity_threshold, item.policy_version)
                for item in self.escalating.items
            )
        if len(policy_snapshots) > 1:
            raise SeverityPolicyError(
                "invalid_dispute_partition",
                "dispute result mixes campaign policy snapshots",
            )
