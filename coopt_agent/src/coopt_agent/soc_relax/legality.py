from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class LegalityError(ValueError):
    """A requirement names something the SoC configuration cannot express."""


@dataclass(frozen=True, slots=True)
class KnobSpec:
    values: list[int]
    source: str = ""


def load_legality(path: Path) -> dict[str, KnobSpec]:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return {
        knob: KnobSpec(
            values=sorted(int(v) for v in entry["values"]),
            source=str(entry.get("source", "")),
        )
        for knob, entry in raw.items()
    }


def clamp(spec: KnobSpec, requested: int) -> tuple[int, bool]:
    """Return (value, was_clamped).

    Rounds up to the nearest legal value at or above the request. A request
    above the legal maximum is clamped to that maximum and flagged, so the
    caller can record that the requirement was not fully met rather than
    pretending it was.
    """
    for value in spec.values:
        if value >= requested:
            return value, False
    return spec.values[-1], True
