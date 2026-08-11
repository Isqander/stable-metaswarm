from __future__ import annotations

from .reconcile_model import (
    AuthorWorkTarget,
    AwaitHumanReopen,
    ClosedFindingRef,
    FindingRoundIntent,
    FindingTarget,
    FixCheckContribution,
    HumanReopenAnswer,
    HumanReopenItem,
    HumanReopenRequest,
    HumanReopenResolutionError,
    NewFindingIntent,
    ObservationLinkIntent,
    OpenFindingRef,
    PrimaryRoundOutcome,
    ProposedGroup,
    ReadyReconciliation,
    ReconcileFailedQuestion,
    ReconciliationContractError,
    ReconciliationInput,
    ReconciliationInputError,
    ReconciliationIssue,
    RoundObservation,
)

_OUTCOMES = (
    "new",
    "existing_open",
    "reaffirmed_closed",
    "reopen_closed",
)


def _nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _observation_key(observation: RoundObservation) -> tuple[int, str, int]:
    return observation.seq, observation.public_id, observation.id


def _target_key(target: FindingTarget) -> tuple[int, int]:
    if target.finding_id is not None:
        return 0, target.finding_id
    assert target.new_finding_index is not None
    return 1, target.new_finding_index


def _owner(observations: tuple[RoundObservation, ...]) -> RoundObservation:
    return min(
        observations,
        key=lambda item: (item.lane_index, item.lane_id, *_observation_key(item)),
    )


def _validate_input(
    value: ReconciliationInput,
) -> tuple[
    tuple[RoundObservation, ...],
    tuple[OpenFindingRef, ...],
    tuple[ClosedFindingRef, ...],
]:
    if not isinstance(value, ReconciliationInput):
        raise ReconciliationInputError("reconciliation input has an invalid type")
    if value.context not in {"discovery", "fix_check_new"}:
        raise ReconciliationInputError("unknown reconciliation context")
    if type(value.current_round_id) is not int or value.current_round_id <= 0:
        raise ReconciliationInputError("current_round_id must be a positive int")
    if type(value.observations) is not tuple:
        raise ReconciliationInputError("observations must be a tuple")
    if type(value.open_findings) is not tuple or type(value.closed_findings) is not tuple:
        raise ReconciliationInputError("scoped ledger collections must be tuples")
    if type(value.current_round_finding_ids) is not frozenset:
        raise ReconciliationInputError("current-round finding IDs must be a frozenset")

    observations: list[RoundObservation] = []
    internal_observation_ids: set[int] = set()
    public_observation_ids: set[str] = set()
    for observation in value.observations:
        if not isinstance(observation, RoundObservation):
            raise ReconciliationInputError("observations contain an invalid item")
        if observation.round_id != value.current_round_id:
            raise ReconciliationInputError("observations from different rounds cannot be mixed")
        if type(observation.id) is not int or observation.id <= 0:
            raise ReconciliationInputError("observation ID must be a positive int")
        if not _nonempty_string(observation.public_id):
            raise ReconciliationInputError("observation public ID must be non-empty")
        if observation.id in internal_observation_ids:
            raise ReconciliationInputError("duplicate internal observation ID")
        if observation.public_id in public_observation_ids:
            raise ReconciliationInputError("duplicate public observation ID")
        if type(observation.seq) is not int or observation.seq < 0:
            raise ReconciliationInputError("observation seq must be a non-negative int")
        if type(observation.lane_id) is not int or observation.lane_id <= 0:
            raise ReconciliationInputError("observation lane ID must be a positive int")
        if type(observation.lane_index) is not int or observation.lane_index < 0:
            raise ReconciliationInputError("observation lane index must be non-negative")
        internal_observation_ids.add(observation.id)
        public_observation_ids.add(observation.public_id)
        observations.append(observation)

    open_findings: list[OpenFindingRef] = []
    closed_findings: list[ClosedFindingRef] = []
    internal_finding_ids: set[int] = set()
    public_finding_ids: set[str] = set()
    for finding in value.open_findings:
        if not isinstance(finding, OpenFindingRef):
            raise ReconciliationInputError("open ledger contains an invalid item")
        _register_finding(finding.id, finding.public_id, internal_finding_ids, public_finding_ids)
        open_findings.append(finding)
    for finding in value.closed_findings:
        if not isinstance(finding, ClosedFindingRef):
            raise ReconciliationInputError("closed ledger contains an invalid item")
        _register_finding(finding.id, finding.public_id, internal_finding_ids, public_finding_ids)
        if finding.resolution_authority == "human":
            if type(finding.human_answer_id) is not int or finding.human_answer_id <= 0:
                raise ReconciliationInputError("human closure requires its answer ID")
            if not _nonempty_string(finding.question_reason):
                raise ReconciliationInputError("human closure requires its question reason")
            if not _nonempty_string(finding.closing_snapshot):
                raise ReconciliationInputError("human closure requires its closing snapshot")
            if not _nonempty_string(finding.escalation_severity):
                raise ReconciliationInputError("human closure requires its severity snapshot")
        closed_findings.append(finding)

    if any(type(finding_id) is not int for finding_id in value.current_round_finding_ids):
        raise ReconciliationInputError("current-round finding IDs must be integers")
    unknown_participants = value.current_round_finding_ids - internal_finding_ids
    if unknown_participants:
        raise ReconciliationInputError(
            "current-round participant is absent from the scoped ledger"
        )

    return (
        tuple(sorted(observations, key=_observation_key)),
        tuple(sorted(open_findings, key=lambda item: (item.id, item.public_id))),
        tuple(sorted(closed_findings, key=lambda item: (item.id, item.public_id))),
    )


def _register_finding(
    finding_id: int,
    public_id: str,
    internal_ids: set[int],
    public_ids: set[str],
) -> None:
    if type(finding_id) is not int or finding_id <= 0:
        raise ReconciliationInputError("finding ID must be a positive int")
    if not _nonempty_string(public_id):
        raise ReconciliationInputError("finding public ID must be non-empty")
    if finding_id in internal_ids or public_id in public_ids:
        raise ReconciliationInputError("open and closed ledger IDs must be unique")
    internal_ids.add(finding_id)
    public_ids.add(public_id)


def _canonical_observation_ids(
    observation_ids: tuple[str, ...],
    observation_order: dict[str, int],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            observation_ids,
            key=lambda public_id: (observation_order.get(public_id, 10**18), public_id),
        )
    )


def _issue(
    code: str,
    group_index: int | None,
    observation_ids: tuple[str, ...],
    finding_id: str | None,
    message: str,
) -> ReconciliationIssue:
    return ReconciliationIssue(code, group_index, observation_ids, finding_id, message)


def _validate_proposal(
    observations: tuple[RoundObservation, ...],
    open_findings: tuple[OpenFindingRef, ...],
    closed_findings: tuple[ClosedFindingRef, ...],
    proposed_groups: tuple[ProposedGroup, ...],
) -> None:
    if type(proposed_groups) is not tuple or any(
        not isinstance(group, ProposedGroup) for group in proposed_groups
    ):
        raise ReconciliationInputError("proposed groups must be a tuple of ProposedGroup")

    observation_order = {
        observation.public_id: index for index, observation in enumerate(observations)
    }
    input_ids = frozenset(observation_order)
    occurrences: dict[str, int] = {}
    for group in proposed_groups:
        if type(group.observation_ids) is not tuple:
            raise ReconciliationInputError("group observation IDs must be a tuple")
        if not group.observation_ids:
            raise ReconciliationInputError("a proposed group must contain an observation ID")
        for public_id in group.observation_ids:
            if type(public_id) is not str:
                raise ReconciliationInputError("group observation IDs must be strings")
            occurrences[public_id] = occurrences.get(public_id, 0) + 1

    issues: list[ReconciliationIssue] = []
    missing = tuple(
        observation.public_id
        for observation in observations
        if occurrences.get(observation.public_id, 0) == 0
    )
    if missing:
        issues.append(
            _issue(
                "missing_observation",
                None,
                missing,
                None,
                f"observations are missing from the proposal: {', '.join(missing)}",
            )
        )
    duplicates = tuple(
        observation.public_id
        for observation in observations
        if occurrences.get(observation.public_id, 0) > 1
    )
    if duplicates:
        issues.append(
            _issue(
                "duplicate_observation",
                None,
                duplicates,
                None,
                f"observations occur more than once: {', '.join(duplicates)}",
            )
        )

    open_by_public_id = {finding.public_id: finding for finding in open_findings}
    closed_by_public_id = {finding.public_id: finding for finding in closed_findings}
    for group_index, group in enumerate(proposed_groups):
        group_ids = _canonical_observation_ids(group.observation_ids, observation_order)
        unknown_ids = tuple(public_id for public_id in group_ids if public_id not in input_ids)
        if unknown_ids:
            issues.append(
                _issue(
                    "unknown_observation",
                    group_index,
                    unknown_ids,
                    group.finding_id,
                    f"group {group_index} refers to observations outside this context",
                )
            )

        if group.outcome not in _OUTCOMES:
            issues.append(
                _issue(
                    "unknown_outcome",
                    group_index,
                    group_ids,
                    group.finding_id,
                    f"group {group_index} has unknown outcome {group.outcome!r}",
                )
            )
            continue

        if group.outcome == "new":
            if group.finding_id is not None:
                issues.append(
                    _issue(
                        "unexpected_finding_reference",
                        group_index,
                        group_ids,
                        group.finding_id,
                        f"new group {group_index} cannot carry a finding ID",
                    )
                )
            if not _nonempty_string(group.title):
                issues.append(
                    _issue(
                        "missing_new_title",
                        group_index,
                        group_ids,
                        group.finding_id,
                        f"new group {group_index} requires a non-empty title",
                    )
                )
            continue

        if group.finding_id is None:
            issues.append(
                _issue(
                    "missing_finding_reference",
                    group_index,
                    group_ids,
                    None,
                    f"group {group_index} requires a finding ID",
                )
            )
        elif group.outcome == "existing_open":
            if group.finding_id not in open_by_public_id:
                issues.append(
                    _issue(
                        "invalid_open_reference",
                        group_index,
                        group_ids,
                        group.finding_id,
                        f"group {group_index} must refer to an open finding in scope",
                    )
                )
        else:
            closed = closed_by_public_id.get(group.finding_id)
            if closed is None:
                issues.append(
                    _issue(
                        "invalid_closed_reference",
                        group_index,
                        group_ids,
                        group.finding_id,
                        f"group {group_index} must refer to a closed finding in scope",
                    )
                )
            elif (
                group.outcome == "reaffirmed_closed"
                and closed.last_resolution != "accepted_reason"
            ):
                issues.append(
                    _issue(
                        "invalid_reaffirmed_resolution",
                        group_index,
                        group_ids,
                        group.finding_id,
                        "reaffirmed_closed requires last_resolution=accepted_reason",
                    )
                )

        if group.outcome == "reopen_closed" and not _nonempty_string(group.reason):
            issues.append(
                _issue(
                    "missing_reopen_reason",
                    group_index,
                    group_ids,
                    group.finding_id,
                    f"reopen_closed group {group_index} requires a non-empty reason",
                )
            )
        if group.title is not None:
            issues.append(
                _issue(
                    "unexpected_existing_title",
                    group_index,
                    group_ids,
                    group.finding_id,
                    f"existing-finding group {group_index} cannot carry a title",
                )
            )

    if issues:
        raise ReconciliationContractError(tuple(issues))


def _group_key(
    item: tuple[int, ProposedGroup],
    observation_order: dict[str, int],
) -> tuple[tuple[int, ...], str, str, str, str, int]:
    group_index, group = item
    positions = tuple(sorted(observation_order[public_id] for public_id in group.observation_ids))
    return (
        positions,
        group.outcome,
        group.finding_id or "",
        group.title or "",
        group.reason or "",
        group_index,
    )


def _sorted_links(
    links: list[ObservationLinkIntent],
    observation_order: dict[str, int],
) -> tuple[ObservationLinkIntent, ...]:
    return tuple(
        sorted(
            links,
            key=lambda link: (
                observation_order[link.observation_public_id],
                _target_key(link.target),
                link.link_type,
            ),
        )
    )


def _sorted_rounds(rounds: list[FindingRoundIntent]) -> tuple[FindingRoundIntent, ...]:
    return tuple(sorted(rounds, key=lambda intent: _target_key(intent.target)))


def reconcile(
    reconciliation_input: ReconciliationInput,
    proposed_groups: tuple[ProposedGroup, ...],
) -> ReadyReconciliation | AwaitHumanReopen:
    observations, open_findings, closed_findings = _validate_input(reconciliation_input)
    _validate_proposal(observations, open_findings, closed_findings, proposed_groups)

    observation_by_id = {item.public_id: item for item in observations}
    observation_order = {item.public_id: index for index, item in enumerate(observations)}
    open_by_public_id = {finding.public_id: finding for finding in open_findings}
    closed_by_public_id = {finding.public_id: finding for finding in closed_findings}
    ordered_groups = sorted(
        enumerate(proposed_groups),
        key=lambda item: _group_key(item, observation_order),
    )

    new_findings: list[NewFindingIntent] = []
    links: list[ObservationLinkIntent] = []
    rounds: list[FindingRoundIntent] = []
    existing_round_observations: dict[int, list[RoundObservation]] = {}
    pending_human: dict[int, list[HumanReopenItem]] = {}

    for _group_index, group in ordered_groups:
        group_observations = tuple(
            sorted(
                (observation_by_id[public_id] for public_id in group.observation_ids),
                key=_observation_key,
            )
        )
        if group.outcome == "new":
            new_index = len(new_findings)
            owner = _owner(group_observations)
            new_finding = NewFindingIntent(
                index=new_index,
                observation_ids=tuple(item.id for item in group_observations),
                observation_public_ids=tuple(item.public_id for item in group_observations),
                title=group.title or "",
                first_observation_id=group_observations[0].id,
                first_observation_public_id=group_observations[0].public_id,
                owner_lane_id=owner.lane_id,
                owner_lane_index=owner.lane_index,
            )
            new_findings.append(new_finding)
            target = FindingTarget(new_finding_index=new_index)
            links.extend(
                ObservationLinkIntent(item.id, item.public_id, target, "first_seen")
                for item in group_observations
            )
            rounds.append(
                FindingRoundIntent(target, owner.lane_id, owner.lane_index)
            )
            continue

        assert group.finding_id is not None
        if group.outcome == "existing_open":
            finding = open_by_public_id[group.finding_id]
            target = FindingTarget(finding_id=finding.id)
            links.extend(
                ObservationLinkIntent(item.id, item.public_id, target, "recurrence")
                for item in group_observations
            )
            existing_round_observations.setdefault(finding.id, []).extend(group_observations)
            continue

        finding = closed_by_public_id[group.finding_id]
        target = FindingTarget(finding_id=finding.id)
        if group.outcome == "reaffirmed_closed":
            links.extend(
                ObservationLinkIntent(item.id, item.public_id, target, "reaffirmation")
                for item in group_observations
            )
        elif finding.resolution_authority == "human":
            assert group.reason is not None
            pending_human.setdefault(finding.id, []).extend(
                HumanReopenItem(
                    item.id,
                    item.public_id,
                    group.reason,
                    item.lane_id,
                    item.lane_index,
                )
                for item in group_observations
            )
        else:
            assert group.reason is not None
            links.extend(
                ObservationLinkIntent(
                    item.id,
                    item.public_id,
                    target,
                    "reopening",
                    group.reason,
                )
                for item in group_observations
            )
            existing_round_observations.setdefault(finding.id, []).extend(group_observations)

    for finding_id, round_observations in existing_round_observations.items():
        if finding_id in reconciliation_input.current_round_finding_ids:
            continue
        owner = _owner(tuple(round_observations))
        rounds.append(
            FindingRoundIntent(
                FindingTarget(finding_id=finding_id),
                owner.lane_id,
                owner.lane_index,
            )
        )

    sorted_links = _sorted_links(links, observation_order)
    sorted_rounds = _sorted_rounds(rounds)
    if pending_human:
        requests: list[HumanReopenRequest] = []
        closed_by_id = {finding.id: finding for finding in closed_findings}
        for finding_id in sorted(pending_human):
            finding = closed_by_id[finding_id]
            items = tuple(
                sorted(
                    pending_human[finding_id],
                    key=lambda item: observation_order[item.observation_public_id],
                )
            )
            assert finding.human_answer_id is not None
            assert finding.question_reason is not None
            assert finding.closing_snapshot is not None
            assert finding.escalation_severity is not None
            requests.append(
                HumanReopenRequest(
                    finding.id,
                    finding.public_id,
                    items,
                    finding.human_answer_id,
                    finding.question_reason,
                    finding.closing_snapshot,
                    finding.escalation_severity,
                )
            )
        return AwaitHumanReopen(
            reconciliation_input.context,
            reconciliation_input.current_round_id,
            reconciliation_input.current_round_finding_ids,
            tuple(item.public_id for item in observations),
            tuple(new_findings),
            sorted_links,
            sorted_rounds,
            tuple(requests),
        )

    return ReadyReconciliation(
        reconciliation_input.context,
        reconciliation_input.current_round_id,
        tuple(new_findings),
        sorted_links,
        sorted_rounds,
    )


def resolve_human_reopens(
    awaiting: AwaitHumanReopen,
    answers: tuple[HumanReopenAnswer, ...],
) -> ReadyReconciliation:
    if not isinstance(awaiting, AwaitHumanReopen):
        raise HumanReopenResolutionError("awaiting value has an invalid type")
    if type(answers) is not tuple or any(
        not isinstance(answer, HumanReopenAnswer) for answer in answers
    ):
        raise HumanReopenResolutionError("answers must be a tuple of HumanReopenAnswer")

    answer_by_finding: dict[int, HumanReopenAnswer] = {}
    for answer in answers:
        if type(answer.finding_id) is not int or answer.finding_id <= 0:
            raise HumanReopenResolutionError("answer finding ID must be a positive int")
        if answer.choice not in {"reopen", "keep_closed"}:
            raise HumanReopenResolutionError("unknown human reopen choice")
        if answer.finding_id in answer_by_finding:
            raise HumanReopenResolutionError("a pending finding was answered more than once")
        answer_by_finding[answer.finding_id] = answer

    requested_ids = frozenset(request.finding_id for request in awaiting.requests)
    if frozenset(answer_by_finding) != requested_ids:
        raise HumanReopenResolutionError("answers must cover every pending finding exactly once")

    links = list(awaiting.links)
    rounds = list(awaiting.finding_rounds)
    observation_order = {
        observation_id: index
        for index, observation_id in enumerate(awaiting.observation_order)
    }
    for request in awaiting.requests:
        answer = answer_by_finding[request.finding_id]
        target = FindingTarget(finding_id=request.finding_id)
        if answer.choice == "reopen":
            links.extend(
                ObservationLinkIntent(
                    item.observation_id,
                    item.observation_public_id,
                    target,
                    "reopening",
                    item.reason,
                )
                for item in request.items
            )
            if request.finding_id not in awaiting.current_round_finding_ids:
                owner_item = min(
                    request.items,
                    key=lambda item: (
                        item.lane_index,
                        item.lane_id,
                        item.observation_public_id,
                    ),
                )
                rounds.append(
                    FindingRoundIntent(
                        target,
                        owner_item.lane_id,
                        owner_item.lane_index,
                    )
                )
        else:
            links.extend(
                ObservationLinkIntent(
                    item.observation_id,
                    item.observation_public_id,
                    target,
                    "reaffirmation",
                )
                for item in request.items
            )

    return ReadyReconciliation(
        awaiting.context,
        awaiting.current_round_id,
        awaiting.new_findings,
        _sorted_links(links, observation_order),
        _sorted_rounds(rounds),
    )


def derive_primary_round_outcome(ready: ReadyReconciliation) -> PrimaryRoundOutcome:
    if not isinstance(ready, ReadyReconciliation) or ready.context != "discovery":
        raise ReconciliationInputError("primary outcome requires ready discovery reconciliation")
    if any(
        link.link_type in {"first_seen", "recurrence", "reopening"}
        for link in ready.links
    ):
        return "needs_revision"
    return "clean"


def derive_fix_check_contribution(ready: ReadyReconciliation) -> FixCheckContribution:
    if not isinstance(ready, ReadyReconciliation) or ready.context != "fix_check_new":
        raise ReconciliationInputError("fix-check contribution requires ready fix_check_new")
    targets = {
        link.target
        for link in ready.links
        if link.link_type in {"first_seen", "recurrence", "reopening"}
    }
    return FixCheckContribution(
        tuple(
            AuthorWorkTarget(
                target,
                ready.current_round_id if target.new_finding_index is not None else None,
            )
            for target in sorted(targets, key=_target_key)
        )
    )


def build_reconcile_failed_question(
    reconciliation_input: ReconciliationInput,
    validation_issues: tuple[ReconciliationIssue, ...],
) -> ReconcileFailedQuestion:
    observations, open_findings, closed_findings = _validate_input(reconciliation_input)
    if type(validation_issues) is not tuple or any(
        not isinstance(issue, ReconciliationIssue) for issue in validation_issues
    ):
        raise ReconciliationInputError("validation issues must be an immutable tuple")
    return ReconcileFailedQuestion(
        "reconcile_failed",
        reconciliation_input.context,
        reconciliation_input.current_round_id,
        observations,
        open_findings,
        closed_findings,
        validation_issues,
    )


__all__ = (
    "build_reconcile_failed_question",
    "derive_fix_check_contribution",
    "derive_primary_round_outcome",
    "reconcile",
    "resolve_human_reopens",
)
