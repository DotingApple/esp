from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class PatchError(ValueError):
    """A proposed RTL edit is unsafe or unapplicable."""


@dataclass(frozen=True, slots=True)
class Whitelist:
    top_module: str
    editable: list[str] = field(default_factory=list)
    read_only: list[str] = field(default_factory=list)


def load_whitelist(path: Path) -> dict[str, Whitelist]:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return {
        accelerator: Whitelist(
            top_module=entry["top_module"],
            editable=list(entry.get("editable", [])),
            read_only=list(entry.get("read_only", [])),
        )
        for accelerator, entry in raw.items()
    }


def check_target(whitelist: Whitelist, accelerator_relative_path: str) -> None:
    """Enforce spec decision D1 structurally.

    Anything not explicitly editable is refused, including paths nobody listed:
    an allowlist, not a denylist, so a file added upstream later does not become
    editable by default.
    """
    if accelerator_relative_path in whitelist.editable:
        return
    raise PatchError(
        f"{accelerator_relative_path} is not editable. "
        f"Editable files for this accelerator: {whitelist.editable}"
    )
