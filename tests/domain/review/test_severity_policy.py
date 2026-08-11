from __future__ import annotations

from dataclasses import FrozenInstanceError
from itertools import product

import pytest

from metaswarm.domain.review.model import EscalatingDispute, EscalatingDisputes
from metaswarm.domain.review.severity import (
    evaluate_disputes,
    meets_threshold,
    resolution_effect,
)
from metaswarm.domain.review.severity_model import (
    DisputeCandidate,
    DisputePolicyResult,
    EscalatingDisputeFact,
    EscalationBatch,
    PolicyClosureIntent,
    ResolutionEffect,
    SeverityInputError,
    SeverityPolicyError,
    SeveritySnapshot,
)

SEVERITIES = ("low", "medium", "high", "critical")


def _candidate(
    finding_id: int,
    severity: str | None,
    *,
    campaign_id: int = 10,
    status: str = "open",
    decision: str = "insists",
    snapshot_finding_id: int | None = None,
    period_start_event_id: int | None = 100,
) -> DisputeCandidate:
    return DisputeCandidate(
        finding_id,
        campaign_id,
        status,
        decision,
        SeveritySnapshot(
            snapshot_finding_id or finding_id,
            period_start_event_id,
            severity,  # type: ignore[arg-type]
            severity,  # type: ignore[arg-type]
        ),
    )


@pytest.mark.parametrize(
    ("resolution", "authority", "closes"),
    (
        ("verified_fixed", "reviewer", True),
        ("accepted_reason", "reviewer", False),
        ("policy_closed", "policy", False),
        ("human_decision", "human", True),
    ),
)
def test_resolution_effect_matches_the_ddl_table(
    resolution: str,
    authority: str,
    closes: bool,
) -> None:
    expected = ResolutionEffect(
        resolution,  # type: ignore[arg-type]
        authority,  # type: ignore[arg-type]
        closes,
    )
    assert resolution_effect(resolution) == expected
    assert resolution_effect(
        resolution,
        resolution_authority=authority,
        closes_severity_period=closes,
    ) == expected


@pytest.mark.parametrize(
    ("resolution", "authority", "closes"),
    (
        ("unknown", None, None),
        ("verified_fixed", "human", True),
        ("accepted_reason", "reviewer", True),
        ("policy_closed", "reviewer", False),
        ("human_decision", "human", False),
    ),
    ids=("unknown", "wrong-authority", "accepted-closes", "wrong-policy-authority", "human-keeps"),
)
def test_unknown_or_incompatible_resolution_effect_is_rejected(
    resolution: object,
    authority: object | None,
    closes: object | None,
) -> None:
    with pytest.raises(SeverityPolicyError) as raised:
        resolution_effect(
            resolution,
            resolution_authority=authority,
            closes_severity_period=closes,
        )
    assert raised.value.code == "invalid_resolution_effect"


@pytest.mark.parametrize(
    ("severity", "threshold"),
    tuple(product(SEVERITIES, repeat=2)),
)
def test_every_severity_threshold_pair_uses_greater_than_or_equal(
    severity: str,
    threshold: str,
) -> None:
    expected = SEVERITIES.index(severity) >= SEVERITIES.index(threshold)
    assert meets_threshold(severity, threshold) is expected


@pytest.mark.parametrize("field", ("severity", "threshold"))
def test_unknown_policy_severity_has_no_default(field: str) -> None:
    severity = "unknown" if field == "severity" else "high"
    threshold = "unknown" if field == "threshold" else "high"
    with pytest.raises(SeverityInputError) as raised:
        meets_threshold(severity, threshold)
    assert raised.value.code == "unknown_severity"


def test_mixed_policy_partition_is_exhaustive_and_keeps_campaign_snapshot() -> None:
    result = evaluate_disputes(
        (
            _candidate(4, "critical"),
            _candidate(2, "medium"),
            _candidate(3, "high"),
            _candidate(1, "low"),
        ),
        campaign_id=10,
        severity_threshold="high",
        policy_version="policy-v7",
    )
    assert tuple(item.finding_id for item in result.policy_closures) == (1, 2)
    assert all(
        (
            item.resolution,
            item.resolution_authority,
            item.closes_severity_period,
            item.severity_threshold,
            item.policy_version,
        )
        == ("policy_closed", "policy", False, "high", "policy-v7")
        for item in result.policy_closures
    )
    assert result.escalating is not None
    assert tuple(
        (item.finding_id, item.escalation_severity)
        for item in result.escalating.items
    ) == ((4, "critical"), (3, "high"))
    assert all(
        (item.severity_threshold, item.policy_version) == ("high", "policy-v7")
        for item in result.escalating.items
    )


def test_all_simultaneous_disputes_form_one_complete_t1_4_aggregate() -> None:
    result = evaluate_disputes(
        (
            _candidate(8, "high"),
            _candidate(3, "critical"),
            _candidate(2, "high"),
            _candidate(9, "critical"),
        ),
        campaign_id=10,
        severity_threshold="high",
        policy_version="policy-v1",
    )
    assert result.policy_closures == ()
    assert result.escalating is not None
    assert tuple(item.finding_id for item in result.escalating.items) == (3, 9, 2, 8)
    mapped = EscalatingDisputes(
        tuple(
            EscalatingDispute(
                item.finding_id,
                item.escalation_severity,
                item.severity_threshold,
                item.policy_version,
            )
            for item in result.escalating.items
        )
    )
    assert tuple(item.finding_id for item in mapped.items) == (3, 9, 2, 8)


def test_empty_dispute_input_is_legal() -> None:
    assert evaluate_disputes(
        (),
        campaign_id=10,
        severity_threshold="high",
        policy_version="policy-v1",
    ) == DisputePolicyResult(None, ())


@pytest.mark.parametrize(
    "candidates",
    (
        (_candidate(1, "high"), _candidate(1, "high")),
        (_candidate(1, "high", status="closed"),),
        (_candidate(1, "high", decision="still_present"),),
        (_candidate(1, None),),
        (_candidate(1, "high", period_start_event_id=None),),
        (_candidate(1, "high", period_start_event_id=-1),),
        (_candidate(1, "unknown"),),
        (_candidate(1, "high", campaign_id=11),),
        (_candidate(1, "high", snapshot_finding_id=2),),
    ),
    ids=(
        "duplicate",
        "closed",
        "not-insists",
        "missing-severity",
        "closed-period",
        "invalid-period",
        "unknown-severity",
        "foreign-campaign",
        "snapshot-mismatch",
    ),
)
def test_each_invalid_dispute_candidate_is_rejected_in_isolation(
    candidates: tuple[DisputeCandidate, ...],
) -> None:
    with pytest.raises(SeverityPolicyError) as raised:
        evaluate_disputes(
            candidates,
            campaign_id=10,
            severity_threshold="high",
            policy_version="policy-v1",
        )
    assert raised.value.code == "invalid_dispute_candidate"


@pytest.mark.parametrize(
    "factory",
    (
        lambda: EscalationBatch(()),
        lambda: EscalationBatch(
            (
                EscalatingDisputeFact(1, "high", "high", "v1"),
                EscalatingDisputeFact(1, "high", "high", "v1"),
            )
        ),
        lambda: EscalationBatch(
            (
                EscalatingDisputeFact(1, "high", "high", "v1"),
                EscalatingDisputeFact(2, "critical", "high", "v1"),
            )
        ),
        lambda: DisputePolicyResult(
            EscalationBatch((EscalatingDisputeFact(1, "high", "high", "v1"),)),
            (PolicyClosureIntent(1, "low", "high", "v1"),),
        ),
        lambda: DisputePolicyResult("invalid", ()),  # type: ignore[arg-type]
        lambda: EscalatingDisputeFact(1, "unknown", "high", "v1"),  # type: ignore[arg-type]
        lambda: PolicyClosureIntent(
            1,
            "low",
            "high",
            "v1",
            resolution="verified_fixed",  # type: ignore[arg-type]
        ),
        lambda: PolicyClosureIntent(1, "high", "high", "v1"),
        lambda: EscalatingDisputeFact(1, "medium", "high", "v1"),
        lambda: EscalationBatch(
            (
                EscalatingDisputeFact(1, "critical", "high", "v1"),
                EscalatingDisputeFact(2, "high", "medium", "v1"),
            )
        ),
        lambda: DisputePolicyResult(
            EscalationBatch((EscalatingDisputeFact(2, "high", "high", "v1"),)),
            (PolicyClosureIntent(1, "low", "medium", "v1"),),
        ),
    ),
    ids=(
        "empty-batch",
        "duplicate-batch",
        "wrong-order",
        "overlapping-partition",
        "invalid-result-type",
        "unknown-batch-severity",
        "invalid-closure-form",
        "closure-at-threshold",
        "escalation-below-threshold",
        "mixed-batch-policy",
        "mixed-result-policy",
    ),
)
def test_malformed_dispute_partition_is_not_mappable_to_t1_4(factory: object) -> None:
    with pytest.raises(SeverityPolicyError) as raised:
        factory()  # type: ignore[operator]
    assert raised.value.code == "invalid_dispute_partition"


def test_policy_values_are_frozen() -> None:
    result = evaluate_disputes(
        (_candidate(1, "high"),),
        campaign_id=10,
        severity_threshold="high",
        policy_version="v1",
    )
    assert result.escalating is not None
    with pytest.raises(FrozenInstanceError):
        result.escalating.items = ()  # type: ignore[misc]
