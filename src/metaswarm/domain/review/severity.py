from __future__ import annotations

from .severity_model import (
    DisputeCandidate,
    DisputePolicyResult,
    EscalatingDisputeFact,
    EscalationBatch,
    LinkedObservationFact,
    ObservationSeverityInput,
    ParentObservationContext,
    PolicyClosureIntent,
    ResolutionEffect,
    ResolutionFact,
    Severity,
    SeverityChainError,
    SeverityFacts,
    SeverityFactsError,
    SeverityInputError,
    SeverityOverrideFact,
    SeverityPolicyError,
    SeveritySnapshot,
    TargetObservationContext,
)

SEVERITY_RANK = (
    ("low", 10),
    ("medium", 20),
    ("high", 30),
    ("critical", 40),
)

_LINK_TYPES = ("first_seen", "recurrence", "reaffirmation", "reopening")
_ESCALATION_LINK_TYPES = ("first_seen", "recurrence", "reopening")
_RESOLUTION_EFFECTS = (
    ("verified_fixed", "reviewer", True),
    ("accepted_reason", "reviewer", False),
    ("policy_closed", "policy", False),
    ("human_decision", "human", True),
)


def _is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def severity_rank(value: object) -> int:
    for severity, rank in SEVERITY_RANK:
        if value == severity and type(value) is str:
            return rank
    raise SeverityInputError(
        "unknown_severity",
        f"unknown severity: {value!r}",
    )


def meets_threshold(severity: object, threshold: object) -> bool:
    return severity_rank(severity) >= severity_rank(threshold)


def _validate_context_id(value: object, field_name: str) -> None:
    if not _is_positive_int(value):
        raise SeverityChainError(
            "invalid_parent",
            f"{field_name} must be a positive int",
        )


def resolve_effective_severity(
    source: ObservationSeverityInput,
    target: TargetObservationContext,
    parent: ParentObservationContext | None = None,
) -> Severity:
    if not isinstance(source, ObservationSeverityInput) or not isinstance(
        target, TargetObservationContext
    ):
        raise SeverityInputError(
            "invalid_severity_source",
            "severity source and target context have invalid types",
        )
    _validate_context_id(target.campaign_id, "target campaign_id")
    _validate_context_id(target.finding_id, "target finding_id")
    if not _is_nonnegative_int(target.seq):
        raise SeverityChainError("invalid_parent", "target seq must be a non-negative int")

    if source.severity_suggested is not None:
        if parent is not None:
            raise SeverityChainError(
                "invalid_parent",
                "a suggested severity must not carry a parent",
                finding_id=target.finding_id,
            )
        severity_rank(source.severity_suggested)
        return source.severity_suggested

    if not isinstance(parent, ParentObservationContext):
        raise SeverityChainError(
            "invalid_parent",
            "unchanged severity requires its direct parent",
            finding_id=target.finding_id,
            observation_id=source.unchanged_from_id,
        )
    _validate_context_id(parent.observation_id, "parent observation_id")
    _validate_context_id(parent.campaign_id, "parent campaign_id")
    _validate_context_id(parent.finding_id, "parent finding_id")
    _validate_context_id(parent.link_event_id, "parent link_event_id")
    if not _is_nonnegative_int(parent.seq):
        raise SeverityChainError("invalid_parent", "parent seq must be a non-negative int")
    if parent.observation_id != source.unchanged_from_id or parent.seq >= target.seq:
        raise SeverityChainError(
            "invalid_parent",
            "unchanged_from must point to an earlier direct parent",
            finding_id=target.finding_id,
            observation_id=source.unchanged_from_id,
        )
    if parent.campaign_id != target.campaign_id or parent.finding_id != target.finding_id:
        raise SeverityChainError(
            "scope_or_period_mismatch",
            "unchanged_from parent belongs to another campaign or finding",
            finding_id=target.finding_id,
            observation_id=parent.observation_id,
        )
    if (
        parent.period_start_event_id != target.period_start_event_id
        or not _is_positive_int(target.period_start_event_id)
        or parent.link_event_id < target.period_start_event_id
    ):
        raise SeverityChainError(
            "scope_or_period_mismatch",
            "unchanged_from parent is outside the current open severity period",
            finding_id=target.finding_id,
            observation_id=parent.observation_id,
        )
    severity_rank(parent.severity_effective)
    return parent.severity_effective


def resolution_effect(
    resolution: object,
    *,
    resolution_authority: object | None = None,
    closes_severity_period: object | None = None,
) -> ResolutionEffect:
    for known, authority, closes_period in _RESOLUTION_EFFECTS:
        if type(resolution) is str and resolution == known:
            if resolution_authority is not None and resolution_authority != authority:
                break
            if closes_severity_period is not None and (
                type(closes_severity_period) is not bool
                or closes_severity_period is not closes_period
            ):
                break
            return ResolutionEffect(
                known,  # type: ignore[arg-type]
                authority,  # type: ignore[arg-type]
                closes_period,
            )
    raise SeverityPolicyError(
        "invalid_resolution_effect",
        f"unknown or inconsistent resolution effect: {resolution!r}",
    )


def _facts_error(
    code: str,
    message: str,
    *,
    finding_id: int | None = None,
    observation_id: int | None = None,
) -> SeverityFactsError:
    return SeverityFactsError(
        code,
        message,
        finding_id=finding_id,
        observation_id=observation_id,
    )


def _validate_link(finding_id: int, link: object) -> LinkedObservationFact:
    if not isinstance(link, LinkedObservationFact):
        raise _facts_error("invalid_facts", "linked observations contain an invalid item")
    if not _is_positive_int(link.observation_id) or not _is_positive_int(link.event_id):
        raise _facts_error(
            "invalid_facts",
            "observation and event IDs must be positive",
            finding_id=finding_id,
            observation_id=link.observation_id if type(link.observation_id) is int else None,
        )
    if link.finding_id != finding_id:
        raise _facts_error(
            "invalid_facts",
            "linked observation belongs to another finding",
            finding_id=finding_id,
            observation_id=link.observation_id,
        )
    if link.link_type not in _LINK_TYPES:
        raise _facts_error(
            "invalid_facts",
            f"unknown observation link type: {link.link_type!r}",
            finding_id=finding_id,
            observation_id=link.observation_id,
        )
    try:
        severity_rank(link.severity_effective)
    except SeverityInputError as error:
        raise _facts_error(
            "invalid_facts",
            str(error),
            finding_id=finding_id,
            observation_id=link.observation_id,
        ) from error
    return link


def _validate_resolution(finding_id: int, value: object) -> ResolutionFact:
    if not isinstance(value, ResolutionFact) or not _is_positive_int(value.event_id):
        raise _facts_error("invalid_facts", "resolutions contain an invalid item")
    if value.finding_id != finding_id:
        raise _facts_error(
            "invalid_facts",
            "resolution belongs to another finding",
            finding_id=finding_id,
        )
    try:
        resolution_effect(
            value.resolution,
            resolution_authority=value.resolution_authority,
            closes_severity_period=value.closes_severity_period,
        )
    except SeverityPolicyError as error:
        raise _facts_error(
            "invalid_facts",
            str(error),
            finding_id=finding_id,
        ) from error
    return value


def _validate_override(finding_id: int, value: object) -> SeverityOverrideFact:
    if not isinstance(value, SeverityOverrideFact) or not _is_positive_int(value.event_id):
        raise _facts_error("invalid_override", "overrides contain an invalid item")
    if value.finding_id != finding_id or not _is_nonempty_string(value.reason):
        raise _facts_error(
            "invalid_override",
            "override must belong to the finding and have a non-empty reason",
            finding_id=finding_id,
        )
    try:
        severity_rank(value.old_severity)
        severity_rank(value.new_severity)
    except SeverityInputError as error:
        raise _facts_error(
            "invalid_override",
            str(error),
            finding_id=finding_id,
        ) from error
    return value


def _max_severity(values: tuple[Severity, ...]) -> Severity | None:
    if not values:
        return None
    return max(values, key=severity_rank)


def _severity_before_override(
    period_start: int,
    links: tuple[LinkedObservationFact, ...],
    previous: SeverityOverrideFact | None,
    event_id: int,
) -> Severity | None:
    lower_bound = previous.event_id if previous is not None else period_start - 1
    values = tuple(
        link.severity_effective
        for link in links
        if link.link_type in _ESCALATION_LINK_TYPES
        and lower_bound < link.event_id < event_id
    )
    if previous is not None:
        values = (previous.new_severity, *values)
    return _max_severity(values)


def _period_start_at(
    event_id: int,
    opening_events: frozenset[int],
    closing_events: frozenset[int],
) -> int | None:
    last_close = max((event for event in closing_events if event <= event_id), default=0)
    eligible_opens = tuple(
        event for event in opening_events if last_close < event <= event_id
    )
    return min(eligible_opens) if eligible_opens else None


def derive_severity_snapshot(facts: SeverityFacts) -> SeveritySnapshot:
    if not isinstance(facts, SeverityFacts):
        raise _facts_error("invalid_facts", "severity facts have an invalid type")
    if not _is_positive_int(facts.finding_id) or not _is_positive_int(
        facts.initial_event_id
    ):
        raise _facts_error("invalid_facts", "finding and initial event IDs must be positive")
    if (
        type(facts.linked_observations) is not tuple
        or type(facts.resolutions) is not tuple
        or type(facts.overrides) is not tuple
    ):
        raise _facts_error("invalid_facts", "severity fact collections must be tuples")

    links = tuple(_validate_link(facts.finding_id, item) for item in facts.linked_observations)
    observation_ids = tuple(item.observation_id for item in links)
    if len(observation_ids) != len(set(observation_ids)):
        raise _facts_error(
            "invalid_facts",
            "linked observations contain duplicate observation IDs",
            finding_id=facts.finding_id,
        )
    resolutions = tuple(
        _validate_resolution(facts.finding_id, item) for item in facts.resolutions
    )
    resolution_events = tuple(item.event_id for item in resolutions)
    if len(resolution_events) != len(set(resolution_events)):
        raise _facts_error(
            "invalid_facts",
            "resolutions contain duplicate boundary event IDs",
            finding_id=facts.finding_id,
        )
    overrides = tuple(_validate_override(facts.finding_id, item) for item in facts.overrides)
    override_events = tuple(item.event_id for item in overrides)
    if len(override_events) != len(set(override_events)):
        raise _facts_error(
            "invalid_override",
            "a finding has more than one override in the same event",
            finding_id=facts.finding_id,
        )

    opening_events = frozenset(
        (facts.initial_event_id,)
        + tuple(item.event_id for item in links if item.link_type == "reopening")
    )
    closing_events = frozenset(
        item.event_id for item in resolutions if item.closes_severity_period
    )
    if opening_events.intersection(closing_events):
        raise _facts_error(
            "invalid_facts",
            "an event cannot both open and close a severity period",
            finding_id=facts.finding_id,
        )
    ordered_overrides = tuple(sorted(overrides, key=lambda item: item.event_id))
    override_periods: list[tuple[SeverityOverrideFact, int]] = []
    for override in ordered_overrides:
        override_period_start = _period_start_at(
            override.event_id,
            opening_events,
            closing_events,
        )
        if override_period_start is None:
            continue
        previous = next(
            (
                previous_override
                for previous_override, previous_period in reversed(override_periods)
                if previous_period == override_period_start
            ),
            None,
        )
        expected_old = _severity_before_override(
            override_period_start,
            links,
            previous,
            override.event_id,
        )
        if expected_old is None or override.old_severity != expected_old:
            raise _facts_error(
                "invalid_override",
                "override old severity does not match escalation before its event",
                finding_id=facts.finding_id,
            )
        override_periods.append((override, override_period_start))

    last_close = max(closing_events, default=0)
    opens_after_close = tuple(event for event in opening_events if event > last_close)
    period_start = min(opens_after_close) if opens_after_close else None

    historical_max = _max_severity(tuple(item.severity_effective for item in links))
    if period_start is None:
        return SeveritySnapshot(facts.finding_id, None, None, historical_max)

    current_overrides = tuple(
        item
        for item, override_period_start in override_periods
        if override_period_start == period_start
    )
    previous = current_overrides[-1] if current_overrides else None

    if previous is None:
        escalation_values = tuple(
            item.severity_effective
            for item in links
            if item.link_type in _ESCALATION_LINK_TYPES and item.event_id >= period_start
        )
    else:
        escalation_values = (
            previous.new_severity,
            *(
                item.severity_effective
                for item in links
                if item.link_type in _ESCALATION_LINK_TYPES
                and item.event_id > previous.event_id
            ),
        )
    return SeveritySnapshot(
        facts.finding_id,
        period_start,
        _max_severity(escalation_values),
        historical_max,
    )


def _policy_error(message: str, *, finding_id: int | None = None) -> SeverityPolicyError:
    return SeverityPolicyError(
        "invalid_dispute_candidate",
        message,
        finding_id=finding_id,
    )


def evaluate_disputes(
    candidates: tuple[DisputeCandidate, ...],
    *,
    campaign_id: int,
    severity_threshold: Severity,
    policy_version: str,
) -> DisputePolicyResult:
    if type(candidates) is not tuple:
        raise _policy_error("dispute candidates must be an immutable tuple")
    if not _is_positive_int(campaign_id) or not _is_nonempty_string(policy_version):
        raise _policy_error("campaign ID and policy version are required")
    severity_rank(severity_threshold)

    seen: set[int] = set()
    escalating: list[EscalatingDisputeFact] = []
    closures: list[PolicyClosureIntent] = []
    for candidate in candidates:
        if not isinstance(candidate, DisputeCandidate):
            raise _policy_error("dispute candidates contain an invalid item")
        if not _is_positive_int(candidate.finding_id) or candidate.finding_id in seen:
            raise _policy_error(
                "dispute candidate IDs must be positive and unique",
                finding_id=candidate.finding_id if type(candidate.finding_id) is int else None,
            )
        seen.add(candidate.finding_id)
        if (
            candidate.campaign_id != campaign_id
            or candidate.status != "open"
            or candidate.reviewer_decision != "insists"
            or not isinstance(candidate.severity, SeveritySnapshot)
            or candidate.severity.finding_id != candidate.finding_id
            or not _is_positive_int(candidate.severity.period_start_event_id)
        ):
            raise _policy_error(
                "candidate is outside the open insists policy scope",
                finding_id=candidate.finding_id,
            )
        value = candidate.severity.escalation_severity
        try:
            severity_rank(value)
        except SeverityInputError as error:
            raise _policy_error(
                str(error),
                finding_id=candidate.finding_id,
            ) from error
        if meets_threshold(value, severity_threshold):
            escalating.append(
                EscalatingDisputeFact(
                    candidate.finding_id,
                    value,
                    severity_threshold,
                    policy_version,
                )
            )
        else:
            closures.append(
                PolicyClosureIntent(
                    candidate.finding_id,
                    value,
                    severity_threshold,
                    policy_version,
                )
            )

    ordered_escalating = tuple(
        sorted(
            escalating,
            key=lambda item: (-severity_rank(item.escalation_severity), item.finding_id),
        )
    )
    result = DisputePolicyResult(
        EscalationBatch(ordered_escalating) if ordered_escalating else None,
        tuple(sorted(closures, key=lambda item: item.finding_id)),
    )
    return result
