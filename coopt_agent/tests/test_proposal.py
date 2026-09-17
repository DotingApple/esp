from __future__ import annotations

import json

import pytest

from coopt_agent.proposal import Proposal, ProposalError, parse_proposal

VALID = {
    "rationale": "1024-beat bursts cut per-transfer overhead",
    "rtl_patch": {
        "file": "hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v",
        "edits": [{"old_text": "32'd16", "new_text": "32'd1024"}],
    },
    "soc_requirements": [
        {
            "knob": "CONFIG_QUEUE_SIZE",
            "min_value": 16,
            "why": "deeper queue keeps requests outstanding",
        }
    ],
    "expected_effect": "fewer DMA transactions, lower end-to-end cycles",
}


def test_valid_proposal_parses() -> None:
    p = parse_proposal(VALID)
    assert isinstance(p, Proposal)
    assert p.rtl_patch.file.endswith("lstm_rtl_basic_dma64.v")
    assert p.rtl_patch.edits[0].new_text == "32'd1024"
    assert p.soc_requirements[0].knob == "CONFIG_QUEUE_SIZE"
    assert p.soc_requirements[0].min_value == 16


def test_accepts_a_json_string() -> None:
    assert parse_proposal(json.dumps(VALID)).rationale == VALID["rationale"]


def test_soc_requirements_may_be_empty() -> None:
    payload = {**VALID, "soc_requirements": []}
    assert parse_proposal(payload).soc_requirements == []


def test_soc_requirements_may_be_absent() -> None:
    payload = {k: v for k, v in VALID.items() if k != "soc_requirements"}
    assert parse_proposal(payload).soc_requirements == []


def test_rejects_empty_edit_list() -> None:
    payload = {**VALID, "rtl_patch": {**VALID["rtl_patch"], "edits": []}}
    with pytest.raises(ProposalError, match="at least one edit"):
        parse_proposal(payload)


def test_rejects_missing_rtl_patch() -> None:
    payload = {k: v for k, v in VALID.items() if k != "rtl_patch"}
    with pytest.raises(ProposalError):
        parse_proposal(payload)


def test_rejects_malformed_json() -> None:
    with pytest.raises(ProposalError, match="not valid JSON"):
        parse_proposal("{not json")


def test_rejects_edit_whose_texts_are_identical() -> None:
    payload = {
        **VALID,
        "rtl_patch": {
            **VALID["rtl_patch"],
            "edits": [{"old_text": "same", "new_text": "same"}],
        },
    }
    with pytest.raises(ProposalError, match="no-op edit"):
        parse_proposal(payload)


def test_rejects_negative_min_value() -> None:
    payload = {
        **VALID,
        "soc_requirements": [
            {"knob": "CONFIG_QUEUE_SIZE", "min_value": -1, "why": "x"}
        ],
    }
    with pytest.raises(ProposalError):
        parse_proposal(payload)
