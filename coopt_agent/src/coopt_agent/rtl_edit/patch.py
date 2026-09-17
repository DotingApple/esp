from __future__ import annotations

from collections.abc import Sequence

from coopt_agent.proposal import RtlEdit
from coopt_agent.rtl_edit.whitelist import PatchError


def apply_edits(source: str, edits: Sequence[RtlEdit]) -> str:
    """Apply exact-string edits, refusing anything ambiguous.

    Each old_text must occur exactly once. Zero occurrences means the model
    invented the text; more than one means we cannot know which it meant. Both
    are rejected rather than guessed, because a guess here silently changes
    hardware and the only feedback is a cycle count.
    """
    patched = source
    for index, edit in enumerate(edits):
        occurrences = patched.count(edit.old_text)
        if occurrences != 1:
            raise PatchError(
                f"edit {index}: old_text occurs {occurrences} times, expected "
                f"exactly 1 -- {edit.old_text!r}"
            )
        patched = patched.replace(edit.old_text, edit.new_text, 1)
    return patched
