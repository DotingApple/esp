from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from coopt_agent.proposal import SocRequirement
from coopt_agent.soc_relax.legality import KnobSpec, LegalityError, clamp

# Composite .esp_config values and their field order.
# Written by tools/socgen/soc.py:376-386.
COMPOSITE_FIELDS: dict[str, list[str]] = {
    "CONFIG_CPU_CACHES": ["l2_sets", "l2_ways", "llc_sets", "llc_ways"],
    "CONFIG_ACC_CACHES": ["acc_l2_sets", "acc_l2_ways"],
}


@dataclass(frozen=True, slots=True)
class ClampRecord:
    knob: str
    requested: int
    applied: int


@dataclass(frozen=True, slots=True)
class Derivation:
    config: dict[str, str]
    clamps: list[ClampRecord] = field(default_factory=list)


def _split_path(knob: str) -> tuple[str, str | None]:
    key, _, subfield = knob.partition(".")
    return key, subfield or None


def derive_config(
    baseline: dict[str, str],
    requirements: Sequence[SocRequirement],
    legality: dict[str, KnobSpec],
) -> Derivation:
    """Derive a candidate configuration from the baseline.

    Always from the baseline, never from the previous candidate: a monotonic
    ratchet drifts one way toward the maximum and cannot release a knob that a
    later RTL change no longer needs (spec section 9).
    """
    # Collapse several requirements on one path to their maximum.
    wanted: dict[str, int] = {}
    for requirement in requirements:
        if requirement.knob not in legality:
            raise LegalityError(
                f"{requirement.knob} is not a known knob; legal knobs are "
                f"{sorted(legality)}"
            )
        previous = wanted.get(requirement.knob)
        if previous is None or requirement.min_value > previous:
            wanted[requirement.knob] = requirement.min_value

    config = dict(baseline)
    clamps: list[ClampRecord] = []

    for knob, requested in sorted(wanted.items()):
        key, subfield = _split_path(knob)
        if key not in config:
            raise LegalityError(f"{key} is not present in the baseline config")

        applied, was_clamped = clamp(legality[knob], requested)
        if was_clamped:
            clamps.append(
                ClampRecord(knob=knob, requested=requested, applied=applied)
            )

        if subfield is None:
            current = int(config[key])
            config[key] = str(max(current, applied))
            continue

        fields = COMPOSITE_FIELDS.get(key)
        if fields is None:
            raise LegalityError(f"{key} has no known composite field layout")
        if subfield not in fields:
            raise LegalityError(f"{key} has no field {subfield!r}")

        parts = config[key].split()
        if len(parts) != len(fields):
            raise LegalityError(
                f"{key} has {len(parts)} fields, expected {len(fields)} "
                f"fields: {config[key]!r}"
            )
        index = fields.index(subfield)
        parts[index] = str(max(int(parts[index]), applied))
        config[key] = " ".join(parts)

    return Derivation(config=config, clamps=clamps)
