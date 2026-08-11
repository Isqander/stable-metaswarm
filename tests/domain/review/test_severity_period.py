from __future__ import annotations

import pytest

from metaswarm.domain.review.severity import derive_severity_snapshot
from metaswarm.domain.review.severity_model import (
    LinkedObservationFact,
    ResolutionFact,
    SeverityFacts,
    SeverityFactsError,
    SeverityOverrideFact,
    SeveritySnapshot,
)


def _link(
    observation_id: int,
    event_id: int,
    severity: str,
    *,
    link_type: str = "first_seen",
    finding_id: int = 1,
) -> LinkedObservationFact:
    return LinkedObservationFact(
        observation_id,
        finding_id,
        event_id,
        link_type,  # type: ignore[arg-type]
        severity,  # type: ignore[arg-type]
    )


def _resolution(
    event_id: int,
    resolution: str,
    *,
    finding_id: int = 1,
    authority: str | None = None,
    closes: bool | None = None,
) -> ResolutionFact:
    effects = {
        "verified_fixed": ("reviewer", True),
        "accepted_reason": ("reviewer", False),
        "policy_closed": ("policy", False),
        "human_decision": ("human", True),
    }
    default_authority, default_closes = effects.get(resolution, ("reviewer", False))
    return ResolutionFact(
        finding_id,
        event_id,
        resolution,  # type: ignore[arg-type]
        authority or default_authority,  # type: ignore[arg-type]
        default_closes if closes is None else closes,
    )


def _override(
    event_id: int,
    old: str,
    new: str,
    *,
    reason: str = "Human correction",
    finding_id: int = 1,
) -> SeverityOverrideFact:
    return SeverityOverrideFact(
        finding_id,
        event_id,
        old,  # type: ignore[arg-type]
        new,  # type: ignore[arg-type]
        reason,
    )


def _facts(
    *links: LinkedObservationFact,
    initial_event_id: int = 10,
    resolutions: tuple[ResolutionFact, ...] = (),
    overrides: tuple[SeverityOverrideFact, ...] = (),
    finding_id: int = 1,
) -> SeverityFacts:
    return SeverityFacts(finding_id, initial_event_id, links, resolutions, overrides)


@pytest.mark.parametrize(
    ("facts", "expected"),
    (
        (
            _facts(_link(1, 10, "low")),
            SeveritySnapshot(1, 10, "low", "low"),
        ),
        (
            _facts(
                _link(1, 10, "high"),
                _link(2, 30, "low", link_type="reopening"),
                resolutions=(_resolution(20, "verified_fixed"),),
            ),
            SeveritySnapshot(1, 30, "low", "high"),
        ),
        (
            _facts(
                _link(1, 10, "high"),
                _link(2, 30, "low", link_type="reopening"),
                resolutions=(_resolution(20, "accepted_reason"),),
            ),
            SeveritySnapshot(1, 10, "high", "high"),
        ),
        (
            _facts(
                _link(1, 10, "medium"),
                _link(2, 30, "low", link_type="reopening"),
                _link(3, 50, "high", link_type="reopening"),
                resolutions=(
                    _resolution(20, "verified_fixed"),
                    _resolution(40, "accepted_reason"),
                ),
            ),
            SeveritySnapshot(1, 30, "high", "high"),
        ),
        (
            _facts(
                _link(1, 10, "high"),
                resolutions=(_resolution(20, "verified_fixed"),),
            ),
            SeveritySnapshot(1, None, None, "high"),
        ),
        (
            _facts(
                _link(1, 10, "critical"),
                _link(2, 30, "low", link_type="recurrence"),
                overrides=(_override(20, "critical", "medium"),),
            ),
            SeveritySnapshot(1, 10, "medium", "critical"),
        ),
        (
            _facts(
                _link(1, 10, "critical"),
                _link(2, 30, "low", link_type="recurrence"),
                _link(3, 40, "high", link_type="recurrence"),
                overrides=(_override(20, "critical", "medium"),),
            ),
            SeveritySnapshot(1, 10, "high", "critical"),
        ),
    ),
    ids=(
        "first-seen",
        "verified-then-reopened",
        "accepted-then-reopened-keeps-period",
        "accepted-inside-second-period-keeps-start",
        "verified-without-reopen-closes-period",
        "override-cuts-off-old-critical",
        "post-override-high-grows-again",
    ),
)
def test_normative_period_boundary_sequences(
    facts: SeverityFacts,
    expected: SeveritySnapshot,
) -> None:
    assert derive_severity_snapshot(facts) == expected


@pytest.mark.parametrize("resolution", ("accepted_reason", "policy_closed"))
def test_nonclosing_resolutions_keep_the_original_period(resolution: str) -> None:
    facts = _facts(
        _link(1, 10, "high"),
        _link(2, 30, "low", link_type="reopening"),
        resolutions=(_resolution(20, resolution),),
    )
    assert derive_severity_snapshot(facts).period_start_event_id == 10


@pytest.mark.parametrize("resolution", ("verified_fixed", "human_decision"))
def test_closing_resolutions_start_a_new_period_only_after_reopening(
    resolution: str,
) -> None:
    closed = _facts(
        _link(1, 10, "high"),
        resolutions=(_resolution(20, resolution),),
    )
    reopened = _facts(
        _link(1, 10, "high"),
        _link(2, 30, "low", link_type="reopening"),
        resolutions=(_resolution(20, resolution),),
    )
    assert derive_severity_snapshot(closed).period_start_event_id is None
    assert derive_severity_snapshot(reopened).period_start_event_id == 30


def test_reaffirmation_affects_historical_but_not_escalation() -> None:
    snapshot = derive_severity_snapshot(
        _facts(
            _link(1, 10, "low"),
            _link(2, 20, "critical", link_type="reaffirmation"),
        )
    )
    assert snapshot == SeveritySnapshot(1, 10, "low", "critical")


def test_multiple_observations_may_share_one_event_without_synthetic_order() -> None:
    snapshot = derive_severity_snapshot(
        _facts(
            _link(1, 10, "low"),
            _link(2, 20, "medium", link_type="recurrence"),
            _link(3, 20, "high", link_type="recurrence"),
        )
    )
    assert snapshot.escalation_severity == "high"


def test_observation_in_the_override_event_is_not_ordered_after_the_override() -> None:
    snapshot = derive_severity_snapshot(
        _facts(
            _link(1, 10, "critical"),
            _link(2, 20, "high", link_type="recurrence"),
            overrides=(_override(20, "critical", "medium"),),
        )
    )
    assert snapshot == SeveritySnapshot(1, 10, "medium", "critical")


def test_old_severity_validation_excludes_observations_from_the_override_event() -> None:
    snapshot = derive_severity_snapshot(
        _facts(
            _link(1, 10, "medium"),
            _link(2, 20, "critical", link_type="recurrence"),
            overrides=(_override(20, "medium", "low"),),
        )
    )
    assert snapshot == SeveritySnapshot(1, 10, "low", "critical")


@pytest.mark.parametrize(
    ("links", "expected"),
    (
        ((_link(1, 10, "critical"),), "medium"),
        (
            (
                _link(1, 10, "critical"),
                _link(2, 30, "low", link_type="recurrence"),
            ),
            "medium",
        ),
        (
            (
                _link(1, 10, "critical"),
                _link(2, 30, "low", link_type="recurrence"),
                _link(3, 40, "high", link_type="recurrence"),
            ),
            "high",
        ),
    ),
)
def test_override_downgrade_then_automatic_growth(
    links: tuple[LinkedObservationFact, ...],
    expected: str,
) -> None:
    snapshot = derive_severity_snapshot(
        _facts(*links, overrides=(_override(20, "critical", "medium"),))
    )
    assert snapshot.escalation_severity == expected
    assert snapshot.historical_max == "critical"


def test_each_override_is_validated_inside_its_historical_period() -> None:
    valid = _facts(
        _link(1, 10, "critical"),
        _link(2, 40, "low", link_type="reopening"),
        resolutions=(_resolution(30, "verified_fixed"),),
        overrides=(_override(20, "critical", "medium"),),
    )
    assert derive_severity_snapshot(valid) == SeveritySnapshot(1, 40, "low", "critical")

    corrupted = _facts(
        _link(1, 10, "critical"),
        _link(2, 40, "low", link_type="reopening"),
        resolutions=(_resolution(30, "verified_fixed"),),
        overrides=(_override(20, "high", "medium"),),
    )
    with pytest.raises(SeverityFactsError) as raised:
        derive_severity_snapshot(corrupted)
    assert raised.value.code == "invalid_override"


def test_multiple_overrides_in_one_period_validate_as_a_chain() -> None:
    snapshot = derive_severity_snapshot(
        _facts(
            _link(1, 10, "critical"),
            _link(2, 30, "low", link_type="recurrence"),
            _link(3, 50, "high", link_type="recurrence"),
            overrides=(
                _override(20, "critical", "medium"),
                _override(40, "medium", "low"),
            ),
        )
    )
    assert snapshot == SeveritySnapshot(1, 10, "high", "critical")


def test_last_override_wins_when_no_later_observation_exists() -> None:
    snapshot = derive_severity_snapshot(
        _facts(
            _link(1, 10, "critical"),
            overrides=(
                _override(20, "critical", "high"),
                _override(30, "high", "low"),
            ),
        )
    )
    assert snapshot == SeveritySnapshot(1, 10, "low", "critical")


def test_last_closing_resolution_defines_the_current_period() -> None:
    snapshot = derive_severity_snapshot(
        _facts(
            _link(1, 10, "low"),
            _link(2, 30, "high", link_type="reopening"),
            _link(3, 50, "medium", link_type="reopening"),
            resolutions=(
                _resolution(20, "verified_fixed"),
                _resolution(40, "human_decision"),
            ),
        )
    )
    assert snapshot == SeveritySnapshot(1, 50, "medium", "high")


def test_override_outside_an_open_period_is_ignored_like_the_sql_view() -> None:
    snapshot = derive_severity_snapshot(
        _facts(
            _link(1, 10, "high"),
            resolutions=(_resolution(20, "verified_fixed"),),
            overrides=(_override(30, "low", "critical"),),
        )
    )
    assert snapshot == SeveritySnapshot(1, None, None, "high")


def test_historical_max_never_includes_even_an_increasing_override() -> None:
    snapshot = derive_severity_snapshot(
        _facts(
            _link(1, 10, "low"),
            overrides=(_override(20, "low", "critical"),),
        )
    )
    assert snapshot == SeveritySnapshot(1, 10, "critical", "low")


@pytest.mark.parametrize(
    ("facts", "code"),
    (
        (_facts(_link(1, 10, "low", finding_id=2)), "invalid_facts"),
        (_facts(_link(1, 10, "low", link_type="unknown")), "invalid_facts"),
        (
            _facts(_link(1, 10, "low"), resolutions=(_resolution(20, "unknown"),)),
            "invalid_facts",
        ),
        (_facts(_link(1, 10, "low"), _link(1, 20, "high")), "invalid_facts"),
        (_facts(_link(1, 0, "low")), "invalid_facts"),
        (
            _facts(
                _link(1, 10, "low"),
                resolutions=(_resolution(10, "verified_fixed"),),
            ),
            "invalid_facts",
        ),
        (
            _facts(
                _link(1, 10, "low"),
                resolutions=(
                    _resolution(20, "verified_fixed", finding_id=2),
                ),
            ),
            "invalid_facts",
        ),
        (
            _facts(
                _link(1, 10, "low"),
                resolutions=(
                    _resolution(20, "verified_fixed", authority="human"),
                ),
            ),
            "invalid_facts",
        ),
    ),
    ids=(
        "foreign-finding",
        "unknown-link",
        "unknown-resolution",
        "duplicate-observation",
        "nonpositive-event",
        "conflicting-boundary",
        "foreign-resolution-finding",
        "resolution-effect-mismatch",
    ),
)
def test_structurally_invalid_facts_never_return_a_partial_snapshot(
    facts: SeverityFacts,
    code: str,
) -> None:
    with pytest.raises(SeverityFactsError) as raised:
        derive_severity_snapshot(facts)
    assert raised.value.code == code


@pytest.mark.parametrize(
    "overrides",
    (
        (_override(20, "critical", "medium", reason=" "),),
        (_override(20, "high", "medium"),),
        (_override(20, "critical", "unknown"),),
        (
            _override(20, "critical", "medium"),
            _override(20, "medium", "low"),
        ),
    ),
    ids=("empty-reason", "wrong-old", "unknown-new", "duplicate-event"),
)
def test_invalid_override_is_rejected_by_its_own_rule(
    overrides: tuple[SeverityOverrideFact, ...],
) -> None:
    facts = _facts(
        _link(1, 10, "critical"),
        overrides=overrides,
    )
    with pytest.raises(SeverityFactsError) as raised:
        derive_severity_snapshot(facts)
    assert raised.value.code == "invalid_override"


def test_fact_order_does_not_change_the_snapshot() -> None:
    links = (
        _link(1, 10, "critical"),
        _link(2, 30, "low", link_type="recurrence"),
        _link(3, 40, "high", link_type="recurrence"),
    )
    resolutions = (_resolution(50, "accepted_reason"),)
    overrides = (_override(20, "critical", "medium"),)
    forward = _facts(*links, resolutions=resolutions, overrides=overrides)
    reversed_facts = _facts(
        *reversed(links),
        resolutions=tuple(reversed(resolutions)),
        overrides=tuple(reversed(overrides)),
    )
    assert derive_severity_snapshot(forward) == derive_severity_snapshot(reversed_facts)
