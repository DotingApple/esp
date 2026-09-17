from __future__ import annotations

from pathlib import Path

import pytest

from coopt_agent.proposal import RtlEdit
from coopt_agent.rtl_edit import (
    PatchError,
    apply_edits,
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
