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

    Occurrences are counted by position, not with `str.count`, which counts
    only non-overlapping matches. Verilog old_text frequently starts and ends
    with the same token -- `"            end\n            end\n"` against a
    three-`end` block matches at two positions but counts as one -- and taking
    the first would be exactly the guess guard 2 exists to refuse.
    """
    patched = source
    for index, edit in enumerate(edits):
        occurrences = _count_positions(patched, edit.old_text)
        if occurrences != 1:
            raise PatchError(
                f"edit {index}: old_text occurs {occurrences} times, expected "
                f"exactly 1 -- {edit.old_text!r}"
            )
        patched = patched.replace(edit.old_text, edit.new_text, 1)
    return patched


def _count_positions(haystack: str, needle: str) -> int:
    """Count every position `needle` starts at, including overlapping ones."""
    count = 0
    position = haystack.find(needle)
    while position != -1:
        count += 1
        position = haystack.find(needle, position + 1)
    return count
