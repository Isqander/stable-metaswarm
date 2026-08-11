from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from metaswarm.domain.review.severity import (
    SEVERITY_RANK,
    resolve_effective_severity,
    severity_rank,
)
from metaswarm.domain.review.severity_model import (
    ObservationSeverityInput,
    ParentObservationContext,
    SeverityChainError,
    SeverityInputError,
    TargetObservationContext,
)

SEVERITIES = ("low", "medium", "high", "critical")


def _target(
    *,
    campaign_id: int = 10,
    finding_id: int = 20,
    seq: int = 2,
    period_start_event_id: int | None = 100,
) -> TargetObservationContext:
    return TargetObservationContext(campaign_id, finding_id, seq, period_start_event_id)


def _parent(
    *,
    observation_id: int = 1,
    campaign_id: int = 10,
    finding_id: int = 20,
    seq: int = 1,
    period_start_event_id: int | None = 100,
    link_event_id: int = 100,
    severity_effective: str = "high",
) -> ParentObservationContext:
    return ParentObservationContext(
        observation_id,
        campaign_id,
        finding_id,
        seq,
        period_start_event_id,
        link_event_id,
        severity_effective,  # type: ignore[arg-type]
    )


def test_severity_rank_table_matches_the_closed_ddl_scale() -> None:
    assert SEVERITY_RANK == (
        ("low", 10),
        ("medium", 20),
        ("high", 30),
        ("critical", 40),
    )
    assert tuple(severity_rank(value) for value in SEVERITIES) == (10, 20, 30, 40)


@pytest.mark.parametrize("severity", SEVERITIES)
def test_suggested_severity_is_the_exact_effective_value(severity: str) -> None:
    source = ObservationSeverityInput(severity, None)  # type: ignore[arg-type]
    assert resolve_effective_severity(source, _target(seq=1)) == severity
    assert resolve_effective_severity(source, _target(seq=1)) == severity


@pytest.mark.parametrize("value", ("unknown", "HIGH", "", 20, None))
def test_unknown_severity_is_rejected_without_coercion(value: object) -> None:
    with pytest.raises(SeverityInputError) as raised:
        ObservationSeverityInput(value, None)  # type: ignore[arg-type]
    assert raised.value.code in {"unknown_severity", "invalid_severity_source"}


@pytest.mark.parametrize(
    ("suggested", "parent_id"),
    ((None, None), ("high", 1), (None, 0), (None, -1), (None, True)),
)
def test_severity_source_requires_exactly_one_valid_branch(
    suggested: object,
    parent_id: object,
) -> None:
    with pytest.raises(SeverityInputError) as raised:
        ObservationSeverityInput(suggested, parent_id)  # type: ignore[arg-type]
    assert raised.value.code == "invalid_severity_source"


def test_unchanged_from_inherits_the_direct_parent_and_supports_multi_hop() -> None:
    first = resolve_effective_severity(
        ObservationSeverityInput("critical", None),
        _target(seq=1),
    )
    second = resolve_effective_severity(
        ObservationSeverityInput(None, 1),
        _target(seq=2),
        _parent(severity_effective=first),
    )
    third = resolve_effective_severity(
        ObservationSeverityInput(None, 2),
        _target(seq=3),
        _parent(
            observation_id=2,
            seq=2,
            link_event_id=110,
            severity_effective=second,
        ),
    )
    assert (first, second, third) == ("critical", "critical", "critical")


@pytest.mark.parametrize(
    ("source", "target", "parent", "code"),
    (
        (ObservationSeverityInput(None, 1), _target(), None, "invalid_parent"),
        (
            ObservationSeverityInput(None, 2),
            _target(),
            _parent(observation_id=1),
            "invalid_parent",
        ),
        (
            ObservationSeverityInput(None, 1),
            _target(seq=1),
            _parent(seq=1),
            "invalid_parent",
        ),
        (
            ObservationSeverityInput(None, 1),
            _target(),
            _parent(campaign_id=11),
            "scope_or_period_mismatch",
        ),
        (
            ObservationSeverityInput(None, 1),
            _target(),
            _parent(finding_id=21),
            "scope_or_period_mismatch",
        ),
        (
            ObservationSeverityInput(None, 1),
            _target(period_start_event_id=100),
            _parent(period_start_event_id=90),
            "scope_or_period_mismatch",
        ),
        (
            ObservationSeverityInput(None, 1),
            _target(period_start_event_id=None),
            _parent(period_start_event_id=100),
            "scope_or_period_mismatch",
        ),
        (
            ObservationSeverityInput(None, 1),
            _target(period_start_event_id=None),
            _parent(period_start_event_id=None),
            "scope_or_period_mismatch",
        ),
        (
            ObservationSeverityInput(None, 1),
            _target(period_start_event_id=100),
            _parent(link_event_id=99),
            "scope_or_period_mismatch",
        ),
    ),
    ids=(
        "missing-parent",
        "wrong-parent-id",
        "forward-parent",
        "foreign-campaign",
        "foreign-finding",
        "foreign-period",
        "closed-target-period",
        "closed-parent-and-target-period",
        "parent-before-period",
    ),
)
def test_each_invalid_parent_scope_or_period_has_its_own_case(
    source: ObservationSeverityInput,
    target: TargetObservationContext,
    parent: ParentObservationContext | None,
    code: str,
) -> None:
    with pytest.raises(SeverityChainError) as raised:
        resolve_effective_severity(source, target, parent)
    assert raised.value.code == code


def test_suggested_source_rejects_an_extraneous_parent() -> None:
    with pytest.raises(SeverityChainError) as raised:
        resolve_effective_severity(
            ObservationSeverityInput("medium", None),
            _target(),
            _parent(),
        )
    assert raised.value.code == "invalid_parent"


def test_severity_inputs_and_contexts_are_frozen() -> None:
    source = ObservationSeverityInput("high", None)
    target = _target()
    with pytest.raises(FrozenInstanceError):
        source.severity_suggested = "low"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        target.seq = 99  # type: ignore[misc]
