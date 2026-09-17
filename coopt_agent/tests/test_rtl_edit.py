from __future__ import annotations

from pathlib import Path

import pytest

from coopt_agent.proposal import RtlEdit, RtlPatch
from coopt_agent.rtl_edit import (
    PatchError,
    apply_edits,
    apply_patch,
    assert_ports_unchanged,
    check_target,
    extract_port_block,
    load_whitelist,
)

WHITELIST = Path(__file__).resolve().parents[1] / "configs" / "editable_files.yaml"

WRAPPER = """\
module lstm_rtl_basic_dma64
(
    input  wire         clk,
    input  wire         rst,
    output reg  [31:0]  dma_write_ctrl_data_length,
    output reg          acc_done
);

localparam BEATS_U = 16;
localparam BEATS_V = 25;

always @(posedge clk) begin
    dma_write_ctrl_data_length <= 32'd4096;
end

endmodule
"""


def test_whitelist_loads_lstm() -> None:
    wl = load_whitelist(WHITELIST)["lstm_rtl"]
    assert wl.top_module == "lstm_rtl_basic_dma64"
    assert "hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v" in wl.editable
    assert "hw/src/lstm_rtl_basic_dma64/lstm.v" in wl.read_only


def test_editable_file_accepted() -> None:
    wl = load_whitelist(WHITELIST)["lstm_rtl"]
    check_target(wl, "hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v")


@pytest.mark.parametrize(
    "path",
    [
        "hw/src/lstm_rtl_basic_dma64/lstm.v",
        "hw/src/lstm_rtl_basic_dma64/lstm_rest.v",
    ],
)
def test_compute_core_rejected(path: str) -> None:
    wl = load_whitelist(WHITELIST)["lstm_rtl"]
    with pytest.raises(PatchError, match="not editable"):
        check_target(wl, path)


def test_unknown_file_rejected() -> None:
    wl = load_whitelist(WHITELIST)["lstm_rtl"]
    with pytest.raises(PatchError, match="not editable"):
        check_target(wl, "hw/src/lstm_rtl_basic_dma64/something_else.v")


def test_unique_match_is_applied() -> None:
    out = apply_edits(WRAPPER, [RtlEdit(old_text="32'd4096", new_text="32'd64")])
    assert "32'd64" in out
    assert "32'd4096" not in out


def test_two_edits_apply_in_order() -> None:
    out = apply_edits(
        WRAPPER,
        [
            RtlEdit(old_text="BEATS_U = 16", new_text="BEATS_U = 8"),
            RtlEdit(old_text="32'd4096", new_text="32'd64"),
        ],
    )
    assert "BEATS_U = 8" in out
    assert "32'd64" in out


def test_zero_matches_rejected() -> None:
    with pytest.raises(PatchError, match="0 times"):
        apply_edits(WRAPPER, [RtlEdit(old_text="nowhere_in_file", new_text="x")])


def test_multiple_matches_rejected() -> None:
    with pytest.raises(PatchError, match="2 times"):
        apply_edits(WRAPPER, [RtlEdit(old_text="localparam", new_text="parameter")])


def test_source_is_not_mutated() -> None:
    before = WRAPPER
    apply_edits(WRAPPER, [RtlEdit(old_text="32'd4096", new_text="32'd64")])
    assert WRAPPER == before


def test_extract_port_block() -> None:
    block = extract_port_block(WRAPPER, "lstm_rtl_basic_dma64")
    assert "dma_write_ctrl_data_length" in block
    assert "acc_done" in block
    assert "localparam" not in block


def test_extract_port_block_unknown_module() -> None:
    with pytest.raises(PatchError, match="module 'nope' not found"):
        extract_port_block(WRAPPER, "nope")


def test_ports_unchanged_passes_for_a_body_edit() -> None:
    after = apply_edits(WRAPPER, [RtlEdit(old_text="32'd4096", new_text="32'd64")])
    assert_ports_unchanged(WRAPPER, after, "lstm_rtl_basic_dma64")


def test_ports_changed_is_rejected() -> None:
    after = apply_edits(
        WRAPPER,
        [RtlEdit(old_text="    output reg          acc_done\n", new_text="")],
    )
    with pytest.raises(PatchError, match="port"):
        assert_ports_unchanged(WRAPPER, after, "lstm_rtl_basic_dma64")


def test_port_width_change_is_rejected() -> None:
    after = apply_edits(
        WRAPPER,
        [
            RtlEdit(
                old_text="output reg  [31:0]  dma_write_ctrl_data_length",
                new_text="output reg  [63:0]  dma_write_ctrl_data_length",
            )
        ],
    )
    with pytest.raises(PatchError, match="port"):
        assert_ports_unchanged(WRAPPER, after, "lstm_rtl_basic_dma64")


# ---------------------------------------------------------------------------
# Finding 1: the port guard must not be fooled by comments naming the module.
# ---------------------------------------------------------------------------

REAL_WRAPPER = (
    Path(__file__).resolve().parents[2]
    / "accelerators"
    / "rtl"
    / "lstm_rtl"
    / "hw"
    / "src"
    / "lstm_rtl_basic_dma64"
    / "lstm_rtl_basic_dma64.v"
)

BASELINE_PORTS = """\
(
    input  wire         clk,
    input  wire         rst,
    output reg  [31:0]  dma_write_ctrl_data_length,
    output reg          acc_done
);
"""


def test_decoy_comment_cannot_smuggle_a_port_removal_past_the_guard() -> None:
    """A single well-formed proposal used to defeat all three guards at once.

    Edit 1 inserts a block comment above the real declaration holding a decoy
    `module lstm_rtl_basic_dma64` and a verbatim copy of the baseline port
    list. Edit 2 then deletes `acc_done` from the real port list, anchored on
    text (`*/`) the decoy does not contain, so the uniqueness guard is happy.
    The port guard would read the decoy, compare it against the baseline, find
    it identical, and wave through an interface without the signal the SoC
    waits on.
    """
    decoy = "/* baseline for reference\nmodule lstm_rtl_basic_dma64\n" + BASELINE_PORTS + "*/\n"
    real_head = "module lstm_rtl_basic_dma64\n" + BASELINE_PORTS
    edits = [
        RtlEdit(
            old_text="module lstm_rtl_basic_dma64\n",
            new_text=decoy + "module lstm_rtl_basic_dma64\n",
        ),
        RtlEdit(
            old_text="*/\n" + real_head,
            new_text="*/\n" + real_head.replace("    output reg          acc_done\n", "")
            .replace("dma_write_ctrl_data_length,", "dma_write_ctrl_data_length"),
        ),
    ]
    after = apply_edits(WRAPPER, edits)
    assert "acc_done" in after  # still present, but only inside the comment
    with pytest.raises(PatchError):
        assert_ports_unchanged(WRAPPER, after, "lstm_rtl_basic_dma64")


def test_line_comment_naming_the_module_is_ignored() -> None:
    source = "// module lstm_rtl_basic_dma64 (decoy, not a declaration)\n" + WRAPPER
    assert extract_port_block(source, "lstm_rtl_basic_dma64") == extract_port_block(
        WRAPPER, "lstm_rtl_basic_dma64"
    )


def test_block_comment_naming_the_module_is_ignored() -> None:
    source = (
        "/*\nmodule lstm_rtl_basic_dma64\n(\n    input wire decoy\n);\n*/\n" + WRAPPER
    )
    assert extract_port_block(source, "lstm_rtl_basic_dma64") == extract_port_block(
        WRAPPER, "lstm_rtl_basic_dma64"
    )


def test_two_real_declarations_of_the_same_module_are_rejected() -> None:
    source = WRAPPER + "\n" + WRAPPER
    with pytest.raises(PatchError, match="2 times|declared 2|more than one"):
        extract_port_block(source, "lstm_rtl_basic_dma64")


def test_paren_inside_a_port_comment_does_not_truncate_the_block() -> None:
    """Minor finding: `// active-low :-)` used to end the extracted block early."""
    source = WRAPPER.replace(
        "    input  wire         rst,\n",
        "    input  wire         rst,   // active-low :-)\n",
    )
    block = extract_port_block(source, "lstm_rtl_basic_dma64")
    assert "acc_done" in block
    assert block.endswith(")")
    assert "// active-low :-)" in block


def test_open_paren_inside_a_port_comment_does_not_swallow_the_body() -> None:
    """An unbalanced `(` used to run the scan into the body, so a legitimate
    body-only patch was falsely rejected as a port change."""
    source = WRAPPER.replace(
        "    input  wire         rst,\n",
        "    input  wire         rst,   // active-low :-(\n",
    )
    after = apply_edits(source, [RtlEdit(old_text="32'd4096", new_text="32'd64")])
    assert_ports_unchanged(source, after, "lstm_rtl_basic_dma64")


def test_extract_port_block_on_the_real_wrapper() -> None:
    """Every other guard test uses a synthetic fixture; this one reads the
    file the loop actually edits."""
    source = REAL_WRAPPER.read_text()
    block = extract_port_block(source, "lstm_rtl_basic_dma64")
    assert "conf_info_in_dim" in block
    assert "dma_write_ctrl_data_length" in block
    assert "acc_done" in block
    assert "localparam" not in block
    assert block.startswith("(")
    assert block.endswith(")")


# ---------------------------------------------------------------------------
# Finding 2: overlapping matches are ambiguous, not unique.
# ---------------------------------------------------------------------------


def test_overlapping_matches_are_rejected() -> None:
    """str.count counts non-overlapping occurrences, so this used to read as
    exactly 1 match and get applied at the first position -- a guess."""
    source = "            end\n            end\n            end\n"
    with pytest.raises(PatchError, match="2 times"):
        apply_edits(
            source,
            [RtlEdit(old_text="            end\n            end\n", new_text="")],
        )


# ---------------------------------------------------------------------------
# Finding 3: rtl_patch.file carries the accelerator-relative path.
# ---------------------------------------------------------------------------


def test_repo_relative_path_is_rejected() -> None:
    """Pins the convention: rtl_patch.file is accelerator-relative, so the
    repo-relative spelling is not a valid target."""
    wl = load_whitelist(WHITELIST)["lstm_rtl"]
    with pytest.raises(PatchError, match="not editable"):
        check_target(
            wl,
            "accelerators/rtl/lstm_rtl/hw/src/lstm_rtl_basic_dma64/"
            "lstm_rtl_basic_dma64.v",
        )


# ---------------------------------------------------------------------------
# Finding 4: the guards must compose, in the spec's order.
# ---------------------------------------------------------------------------


def _patch(file: str, *edits: RtlEdit) -> RtlPatch:
    return RtlPatch(file=file, edits=list(edits))


EDITABLE = "hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v"


def test_apply_patch_returns_patched_text_when_all_guards_pass() -> None:
    wl = load_whitelist(WHITELIST)["lstm_rtl"]
    out = apply_patch(
        wl, WRAPPER, _patch(EDITABLE, RtlEdit(old_text="32'd4096", new_text="32'd64"))
    )
    assert "32'd64" in out


def test_apply_patch_rejects_a_non_editable_file() -> None:
    wl = load_whitelist(WHITELIST)["lstm_rtl"]
    with pytest.raises(PatchError, match="not editable"):
        apply_patch(
            wl,
            WRAPPER,
            _patch(
                "hw/src/lstm_rtl_basic_dma64/lstm.v",
                RtlEdit(old_text="32'd4096", new_text="32'd64"),
            ),
        )


def test_apply_patch_rejects_an_ambiguous_edit() -> None:
    wl = load_whitelist(WHITELIST)["lstm_rtl"]
    with pytest.raises(PatchError, match="2 times"):
        apply_patch(
            wl,
            WRAPPER,
            _patch(EDITABLE, RtlEdit(old_text="localparam", new_text="parameter")),
        )


def test_apply_patch_rejects_a_port_change() -> None:
    wl = load_whitelist(WHITELIST)["lstm_rtl"]
    with pytest.raises(PatchError, match="port"):
        apply_patch(
            wl,
            WRAPPER,
            _patch(
                EDITABLE,
                RtlEdit(old_text="    output reg          acc_done\n", new_text=""),
            ),
        )


def test_apply_patch_returns_nothing_on_failure() -> None:
    wl = load_whitelist(WHITELIST)["lstm_rtl"]
    result = "sentinel"
    try:
        result = apply_patch(
            wl,
            WRAPPER,
            _patch(
                EDITABLE,
                RtlEdit(old_text="    output reg          acc_done\n", new_text=""),
            ),
        )
    except PatchError:
        pass
    assert result == "sentinel"
