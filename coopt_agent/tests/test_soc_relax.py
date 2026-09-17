from __future__ import annotations

import json
from pathlib import Path

import pytest

from coopt_agent.proposal import SocRequirement
from coopt_agent.soc_relax import (
    KnobSpec,
    LegalityError,
    clamp,
    derive_config,
    load_legality,
)

LEGALITY_PATH = Path(__file__).resolve().parents[1] / "configs" / "legality.yaml"
LEGALITY = load_legality(LEGALITY_PATH)

BASELINE = {
    "CONFIG_QUEUE_SIZE": "4",
    "CONFIG_COH_NOC_WIDTH": "64",
    "CONFIG_DMA_NOC_WIDTH": "64",
    "CONFIG_MEM_LINK_WIDTH": "64",
    "CONFIG_SLM_KBYTES": "256",
    "CONFIG_ACC_CACHES": "512 4",
    "CONFIG_CPU_CACHES": "512 4 1024 16",
    "CONFIG_CACHE_EN": "y",
}


def _req(knob: str, value: int) -> SocRequirement:
    return SocRequirement(knob=knob, min_value=value, why="test")


def test_no_requirements_leaves_baseline_identical() -> None:
    result = derive_config(BASELINE, [], LEGALITY)
    assert result.config == BASELINE
    assert result.clamps == []


def test_scalar_knob_is_raised() -> None:
    result = derive_config(BASELINE, [_req("CONFIG_QUEUE_SIZE", 16)], LEGALITY)
    assert result.config["CONFIG_QUEUE_SIZE"] == "16"


def test_request_below_baseline_does_not_lower_it() -> None:
    result = derive_config(BASELINE, [_req("CONFIG_QUEUE_SIZE", 2)], LEGALITY)
    assert result.config["CONFIG_QUEUE_SIZE"] == "4"


def test_request_above_legal_maximum_is_clamped_and_recorded() -> None:
    # The report records a proposal asking for 64; the legal maximum is 17.
    result = derive_config(BASELINE, [_req("CONFIG_QUEUE_SIZE", 64)], LEGALITY)
    assert result.config["CONFIG_QUEUE_SIZE"] == "17"
    assert len(result.clamps) == 1
    assert result.clamps[0].knob == "CONFIG_QUEUE_SIZE"
    assert result.clamps[0].requested == 64
    assert result.clamps[0].applied == 17


def test_request_between_legal_values_rounds_up() -> None:
    result = derive_config(BASELINE, [_req("CONFIG_SLM_KBYTES", 300)], LEGALITY)
    assert result.config["CONFIG_SLM_KBYTES"] == "512"


def test_composite_field_is_raised_in_place() -> None:
    result = derive_config(
        BASELINE, [_req("CONFIG_ACC_CACHES.acc_l2_sets", 2048)], LEGALITY
    )
    assert result.config["CONFIG_ACC_CACHES"] == "2048 4"


def test_composite_middle_field_is_raised_without_disturbing_neighbours() -> None:
    result = derive_config(
        BASELINE, [_req("CONFIG_CPU_CACHES.llc_sets", 2048)], LEGALITY
    )
    # only the third field moves; 512, 4 and 16 are untouched
    assert result.config["CONFIG_CPU_CACHES"] == "512 4 2048 16"


def test_two_requirements_on_one_knob_take_the_maximum() -> None:
    result = derive_config(
        BASELINE,
        [_req("CONFIG_QUEUE_SIZE", 8), _req("CONFIG_QUEUE_SIZE", 16)],
        LEGALITY,
    )
    assert result.config["CONFIG_QUEUE_SIZE"] == "16"


def test_derivation_is_from_baseline_not_from_previous_result() -> None:
    """Spec section 9: derived every time, never ratcheted."""
    raised = derive_config(BASELINE, [_req("CONFIG_QUEUE_SIZE", 16)], LEGALITY)
    assert raised.config["CONFIG_QUEUE_SIZE"] == "16"
    released = derive_config(BASELINE, [], LEGALITY)
    assert released.config["CONFIG_QUEUE_SIZE"] == "4"


def test_unknown_knob_is_rejected() -> None:
    with pytest.raises(LegalityError, match="not a known knob"):
        derive_config(BASELINE, [_req("CONFIG_MADE_UP", 4)], LEGALITY)


def test_knob_missing_from_baseline_is_rejected() -> None:
    thin = {k: v for k, v in BASELINE.items() if k != "CONFIG_SLM_KBYTES"}
    with pytest.raises(LegalityError, match="not present in the baseline"):
        derive_config(thin, [_req("CONFIG_SLM_KBYTES", 512)], LEGALITY)


def test_composite_with_wrong_field_count_is_rejected() -> None:
    broken = {**BASELINE, "CONFIG_ACC_CACHES": "512"}
    with pytest.raises(LegalityError, match="expected 2 fields"):
        derive_config(broken, [_req("CONFIG_ACC_CACHES.acc_l2_sets", 1024)], LEGALITY)


def test_clamp_returns_flag() -> None:
    spec = KnobSpec(values=[2, 4, 8])
    assert clamp(spec, 4) == (4, False)
    assert clamp(spec, 3) == (4, False)
    assert clamp(spec, 99) == (8, True)


def test_baseline_is_not_mutated() -> None:
    snapshot = dict(BASELINE)
    derive_config(BASELINE, [_req("CONFIG_QUEUE_SIZE", 16)], LEGALITY)
    assert BASELINE == snapshot


def test_two_requirements_on_different_fields_of_one_composite_key() -> None:
    """Both fields of CONFIG_ACC_CACHES move, and neither overwrites the other.

    The per-path merge keys on the dotted path, so two requirements naming
    different fields of the same key are two separate paths. Pinned here
    because the composite writeback reads and rewrites the whole value string:
    a future refactor that merged on the bare key, or that rebuilt the string
    from the baseline rather than from the running value, would drop one of
    these silently.
    """
    result = derive_config(
        BASELINE,
        [
            _req("CONFIG_ACC_CACHES.acc_l2_sets", 2048),
            _req("CONFIG_ACC_CACHES.acc_l2_ways", 8),
        ],
        LEGALITY,
    )
    assert result.config["CONFIG_ACC_CACHES"] == "2048 8"


def test_non_numeric_baseline_scalar_raises_legality_error() -> None:
    """A knob that IS in the legality table but has a junk baseline value used
    to leak a bare ValueError from int()."""
    broken = {**BASELINE, "CONFIG_QUEUE_SIZE": "default"}
    with pytest.raises(LegalityError, match="CONFIG_QUEUE_SIZE"):
        derive_config(broken, [_req("CONFIG_QUEUE_SIZE", 16)], LEGALITY)


def test_non_numeric_baseline_composite_field_raises_legality_error() -> None:
    broken = {**BASELINE, "CONFIG_ACC_CACHES": "auto 4"}
    with pytest.raises(LegalityError, match="CONFIG_ACC_CACHES"):
        derive_config(broken, [_req("CONFIG_ACC_CACHES.acc_l2_sets", 2048)], LEGALITY)


def test_derivation_to_dict_is_json_serialisable() -> None:
    """Spec section 9 requires a soc_derivation.json; the clamp records have to
    reach it one obvious way."""
    result = derive_config(BASELINE, [_req("CONFIG_QUEUE_SIZE", 64)], LEGALITY)
    payload = json.dumps(result.to_dict())
    round_tripped = json.loads(payload)
    assert round_tripped["config"]["CONFIG_QUEUE_SIZE"] == "17"
    assert round_tripped["clamps"] == [
        {"knob": "CONFIG_QUEUE_SIZE", "requested": 64, "applied": 17}
    ]


def test_derivation_to_dict_has_no_clamps_when_nothing_was_clamped() -> None:
    result = derive_config(BASELINE, [_req("CONFIG_QUEUE_SIZE", 16)], LEGALITY)
    assert json.loads(json.dumps(result.to_dict()))["clamps"] == []


def test_derivation_to_dict_does_not_alias_the_config() -> None:
    result = derive_config(BASELINE, [_req("CONFIG_QUEUE_SIZE", 16)], LEGALITY)
    payload = result.to_dict()
    payload["config"]["CONFIG_QUEUE_SIZE"] = "tampered"
    assert result.config["CONFIG_QUEUE_SIZE"] == "16"
