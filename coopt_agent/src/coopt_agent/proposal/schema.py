from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError, field_validator


class ProposalError(ValueError):
    """The model returned something that is not a usable proposal."""


class RtlEdit(BaseModel):
    old_text: str = Field(min_length=1)
    new_text: str

    @field_validator("new_text")
    @classmethod
    def _must_change_something(cls, new_text: str, info) -> str:
        if new_text == info.data.get("old_text"):
            raise ValueError("no-op edit: old_text and new_text are identical")
        return new_text


class RtlPatch(BaseModel):
    """A patch to one file of one accelerator.

    `file` carries the **accelerator-relative** path -- for example
    `hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v`, not the repo-relative
    `accelerators/rtl/lstm_rtl/hw/src/...`. This is the same spelling
    `configs/editable_files.yaml` uses and the same one `check_target` compares
    against, so the whitelist check is a plain exact-string membership test.

    The convention is accelerator-relative because the whitelist is
    per-accelerator: a repo-relative path would have to be converted before it
    could be checked, and the converter would need the accelerator root, which
    the schema does not carry. That normalization would land in the
    orchestrator, where the obvious implementation is fuzzy suffix matching --
    and a suffix match turns an allowlist that currently fails closed into one
    that can be talked past. The path is carried in the form the guard can
    check directly.
    """

    file: str = Field(min_length=1)
    edits: list[RtlEdit] = Field(min_length=1)


class SocRequirement(BaseModel):
    knob: str = Field(min_length=1)
    min_value: int = Field(ge=0)
    why: str = ""


class Proposal(BaseModel):
    rationale: str = ""
    rtl_patch: RtlPatch
    soc_requirements: list[SocRequirement] = Field(default_factory=list)
    expected_effect: str = ""


def parse_proposal(raw: str | dict) -> Proposal:
    """Parse and validate a proposal from the model.

    Raises ProposalError for anything unusable, so callers never have to tell
    a pydantic failure apart from a JSON failure.
    """
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProposalError(f"proposal is not valid JSON: {exc}") from exc
    else:
        payload = raw

    try:
        return Proposal.model_validate(payload)
    except ValidationError as exc:
        message = str(exc)
        if "edits" in message and "at least 1 item" in message:
            raise ProposalError(
                "rtl_patch must contain at least one edit: this loop always "
                "leads with an RTL change"
            ) from exc
        raise ProposalError(message) from exc
