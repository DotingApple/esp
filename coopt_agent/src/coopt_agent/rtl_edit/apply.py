from __future__ import annotations

from coopt_agent.proposal import RtlPatch
from coopt_agent.rtl_edit.guard import assert_ports_unchanged
from coopt_agent.rtl_edit.patch import apply_edits
from coopt_agent.rtl_edit.whitelist import Whitelist, check_target


def apply_patch(whitelist: Whitelist, source: str, patch: RtlPatch) -> str:
    """Run spec section 8's three guards, in order, and return the patched text.

    1. editable-file whitelist  (`check_target` on `patch.file`)
    2. exact, unique match      (`apply_edits`)
    3. port-signature guard     (`assert_ports_unchanged`)

    The spec requires the guards to apply in order and any failure to reject
    the candidate *before any build is attempted*. Shipping three free
    functions and no composition left that ordering to the orchestrator, which
    could write the file to disk and only then guard it. This is the single
    entry point: it returns a string or it raises `PatchError`, and it never
    touches the filesystem, so a rejected candidate cannot leave a half-patched
    tree behind.

    `patch.file` is accelerator-relative, matching `configs/editable_files.yaml`.
    """
    check_target(whitelist, patch.file)
    patched = apply_edits(source, patch.edits)
    assert_ports_unchanged(source, patched, whitelist.top_module)
    return patched
