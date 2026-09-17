# coopt_agent Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the three pure-logic modules of `coopt_agent` — the unified proposal schema, the RTL patch applier with its safety guards, and the SoC requirement derivation — each fully unit-tested without ModelSim, ESP or an LLM.

**Architecture:** A new Python package `coopt_agent` under `/home/pd2827/esp`, installed editable into the existing `soc_opt_agent/.venv`. This plan builds only what needs no ESP execution. The orchestrator, the LLM policy and the `goldengen` correctness gate are deliberately out of scope (see the closing section).

**Tech Stack:** Python 3.12 (`/home/pd2827/esp/soc_opt_agent/.venv`), pydantic 2.x, PyYAML, pytest 8.x — all already installed in that venv.

**Spec:** `docs/superpowers/specs/2026-09-17-esp-soc-rtl-cooptimization-design.md` (sections 7, 8 and 9 are what this plan implements)

## Global Constraints

- Branch `coopt` in `/home/pd2827/esp`. Push to remote `fork` after each task.
- Use the existing venv: `/home/pd2827/esp/soc_opt_agent/.venv/bin/python` and
  `.../bin/pytest`. Do not create a second virtualenv.
- **TDD is mandatory.** Every step pair is: write the failing test, run it and see
  it fail for the stated reason, then write the minimal code to pass. A test that
  passes the moment you write it is not a test of anything.
- Do not import from `soc_opt_agent` in this plan's modules. These three modules
  are pure logic with no ESP dependency; the coupling comes later, in the
  orchestrator.
- Do not touch any file under `accelerators/`, `socs/`, `tech/` or `soft/`. A
  simulation belonging to another plan is running against that tree.
- Type hints on every public function. `from __future__ import annotations` at the
  top of each module, matching `soc_opt_agent`'s existing style.

## Interfaces this plan establishes

Later plans consume these exact names. Do not rename them.

```python
# coopt_agent.proposal
class RtlEdit:        old_text: str; new_text: str
class RtlPatch:       file: str; edits: list[RtlEdit]
class SocRequirement: knob: str; min_value: int; why: str
class Proposal:       rationale: str; rtl_patch: RtlPatch
                      soc_requirements: list[SocRequirement]; expected_effect: str
def parse_proposal(raw: str | dict) -> Proposal          # raises ProposalError

# coopt_agent.rtl_edit
class Whitelist:      top_module: str; editable: list[str]; read_only: list[str]
def load_whitelist(path: Path) -> dict[str, Whitelist]
def check_target(wl: Whitelist, accelerator_relative_path: str) -> None  # raises PatchError
def apply_edits(source: str, edits: Sequence[RtlEdit]) -> str            # raises PatchError
def extract_port_block(source: str, module: str) -> str                  # raises PatchError
def assert_ports_unchanged(before: str, after: str, module: str) -> None # raises PatchError

# coopt_agent.soc_relax
class KnobSpec:       values: list[int]; source: str = ""
class ClampRecord:    knob: str; requested: int; applied: int
class Derivation:     config: dict[str, str]; clamps: list[ClampRecord]
def load_legality(path: Path) -> dict[str, KnobSpec]
def clamp(spec: KnobSpec, requested: int) -> tuple[int, bool]
def derive_config(baseline: dict[str, str],
                  requirements: Sequence[SocRequirement],
                  legality: dict[str, KnobSpec]) -> Derivation
```

Composite `.esp_config` keys and their field order, from `tools/socgen/soc.py:376-386`:

```
CONFIG_CPU_CACHES = <l2_sets> <l2_ways> <llc_sets> <llc_ways>
CONFIG_ACC_CACHES = <acc_l2_sets> <acc_l2_ways>
```

A requirement names a dotted path into those: `CONFIG_ACC_CACHES.acc_l2_sets`.

---

### Task 1: Package scaffolding and the legality table

**Files:**
- Create: `coopt_agent/pyproject.toml`
- Create: `coopt_agent/src/coopt_agent/__init__.py`
- Create: `coopt_agent/configs/legality.yaml`
- Create: `coopt_agent/configs/editable_files.yaml`
- Create: `coopt_agent/tests/test_legality_mirrors_socgen.py`
- Create: `coopt_agent/.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: an importable `coopt_agent` package and the two config files every
  later task reads.

- [ ] **Step 1: Create the package skeleton**

`coopt_agent/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "coopt-agent"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "pydantic>=2.7,<3",
  "PyYAML>=6,<7",
]

[project.optional-dependencies]
dev = ["pytest>=8,<9"]

[tool.pytest.ini_options]
pythonpath = ["src"]

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
```

`coopt_agent/src/coopt_agent/__init__.py`:

```python
"""Unified SoC + RTL co-optimization agent for ESP accelerators."""

__all__: list[str] = []
```

`coopt_agent/.gitignore`:

```
.venv/
*.egg-info/
__pycache__/
.pytest_cache/
runs/
baselines/
bests/
```

- [ ] **Step 2: Write the legality table**

These values are mirrored from ESP's own configuration GUI — they are the
authority, not our guesses. `coopt_agent/configs/legality.yaml`:

```yaml
# Legal values for the SoC knobs the optimizer may raise.
# Mirrored from ESP's own configuration GUI; test_legality_mirrors_socgen.py
# asserts this file still matches those sources, so upstream drift is caught.
CONFIG_QUEUE_SIZE:
  source: tools/socgen/NoCConfiguration.py:queue_size_choices
  values: [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17]
CONFIG_COH_NOC_WIDTH:
  source: tools/socgen/NoCConfiguration.py:noc_width_choices
  values: [32, 64, 128, 256, 512, 1024]
CONFIG_DMA_NOC_WIDTH:
  source: tools/socgen/NoCConfiguration.py:noc_width_choices
  values: [32, 64, 128, 256, 512, 1024]
CONFIG_MEM_LINK_WIDTH:
  # Note: stops at 512. The NoC widths go to 1024; this one does not.
  source: tools/socgen/esp_creator.py:mem_link_width_choices
  values: [32, 64, 128, 256, 512]
CONFIG_SLM_KBYTES:
  source: tools/socgen/esp_creator.py:slm_kbytes_choices
  values: [64, 128, 256, 512, 1024, 2048, 4096]
CONFIG_ACC_CACHES.acc_l2_sets:
  source: tools/socgen/esp_creator.py:sets_choices
  values: [32, 64, 128, 256, 512, 1024, 2048, 4096, 8192]
CONFIG_ACC_CACHES.acc_l2_ways:
  source: tools/socgen/esp_creator.py:l2_ways_choices
  values: [2, 4, 8]
CONFIG_CPU_CACHES.l2_sets:
  source: tools/socgen/esp_creator.py:sets_choices
  values: [32, 64, 128, 256, 512, 1024, 2048, 4096, 8192]
CONFIG_CPU_CACHES.l2_ways:
  source: tools/socgen/esp_creator.py:l2_ways_choices
  values: [2, 4, 8]
CONFIG_CPU_CACHES.llc_sets:
  source: tools/socgen/esp_creator.py:sets_choices
  values: [32, 64, 128, 256, 512, 1024, 2048, 4096, 8192]
CONFIG_CPU_CACHES.llc_ways:
  source: tools/socgen/esp_creator.py:llc_ways_choices
  values: [4, 8, 16]
```

- [ ] **Step 3: Write the editable-file whitelist**

`coopt_agent/configs/editable_files.yaml`:

```yaml
# Which files the RTL optimizer may modify, per accelerator.
# Spec section 8 guard 1: decision D1 -- "the compute core's arithmetic and the
# ESP interface are off limits" -- is enforced structurally by this list, not by
# asking the model to behave in a prompt.
lstm_rtl:
  top_module: lstm_rtl_basic_dma64
  editable:
    # The ESP wrapper: DMA control and the load/compute/store FSM.
    - hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v
  read_only:
    # `module lstm` -- the compute core top.
    - hw/src/lstm_rtl_basic_dma64/lstm.v
    # lstm_top, vecmat_mul_x, vecmat_add_h, signedmul, qadd2, spram_*.
    - hw/src/lstm_rtl_basic_dma64/lstm_rest.v
```

- [ ] **Step 4: Write the failing drift test**

This is the test that makes the legality table trustworthy: it re-derives the
values from ESP's sources and fails if they ever diverge.

`coopt_agent/tests/test_legality_mirrors_socgen.py`:

```python
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ESP_ROOT = Path(__file__).resolve().parents[2]
SOCGEN = ESP_ROOT / "tools" / "socgen"
LEGALITY = Path(__file__).resolve().parents[1] / "configs" / "legality.yaml"


def _choice_list(source_file: Path, variable: str) -> list[int]:
    """Extract `self.<variable> = ["1", "2", ...]` from an ESP socgen source."""
    text = source_file.read_text()
    match = re.search(
        r"self\.%s\s*=\s*\[(.*?)\]" % re.escape(variable), text, re.S
    )
    if match is None:
        pytest.fail(f"{variable} not found in {source_file}")
    return [int(v) for v in re.findall(r'"(\d+)"', match.group(1))]


def _local_choice_list(source_file: Path, variable: str) -> list[int]:
    """Same, for a plain local variable without the `self.` prefix."""
    text = source_file.read_text()
    match = re.search(
        r"(?<!\.)\b%s\s*=\s*\[(.*?)\]" % re.escape(variable), text, re.S
    )
    if match is None:
        pytest.fail(f"{variable} not found in {source_file}")
    return [int(v) for v in re.findall(r'"(\d+)"', match.group(1))]


EXPECTED = {
    "CONFIG_QUEUE_SIZE": ("NoCConfiguration.py", "queue_size_choices", False),
    "CONFIG_COH_NOC_WIDTH": ("NoCConfiguration.py", "noc_width_choices", False),
    "CONFIG_DMA_NOC_WIDTH": ("NoCConfiguration.py", "noc_width_choices", False),
    "CONFIG_MEM_LINK_WIDTH": ("esp_creator.py", "mem_link_width_choices", True),
    "CONFIG_SLM_KBYTES": ("esp_creator.py", "slm_kbytes_choices", False),
    "CONFIG_ACC_CACHES.acc_l2_sets": ("esp_creator.py", "sets_choices", False),
    "CONFIG_ACC_CACHES.acc_l2_ways": ("esp_creator.py", "l2_ways_choices", False),
    "CONFIG_CPU_CACHES.l2_sets": ("esp_creator.py", "sets_choices", False),
    "CONFIG_CPU_CACHES.l2_ways": ("esp_creator.py", "l2_ways_choices", False),
    "CONFIG_CPU_CACHES.llc_sets": ("esp_creator.py", "sets_choices", False),
    "CONFIG_CPU_CACHES.llc_ways": ("esp_creator.py", "llc_ways_choices", False),
}


@pytest.mark.parametrize("knob", sorted(EXPECTED))
def test_legality_matches_socgen(knob: str) -> None:
    table = yaml.safe_load(LEGALITY.read_text())
    assert knob in table, f"{knob} missing from legality.yaml"

    filename, variable, is_local = EXPECTED[knob]
    source = SOCGEN / filename
    extract = _local_choice_list if is_local else _choice_list
    assert table[knob]["values"] == extract(source, variable)


def test_no_extra_knobs() -> None:
    table = yaml.safe_load(LEGALITY.read_text())
    assert sorted(table) == sorted(EXPECTED), (
        "legality.yaml and this test's EXPECTED map disagree about which knobs "
        "exist; a knob with no mirrored source is a knob nobody checked."
    )
```

- [ ] **Step 5: Run the test and watch it fail for the right reason**

Run: `cd /home/pd2827/esp/coopt_agent && ../soc_opt_agent/.venv/bin/pytest tests/test_legality_mirrors_socgen.py -v`

Expected at this point: **PASS**, because Steps 2-4 wrote the table and the test
together. That is acceptable only for this one task — the table is data
transcribed from ESP, not behaviour, and the test's job is to catch future
drift rather than to drive a design.

To prove the test actually checks something, temporarily corrupt one value:

```bash
cd /home/pd2827/esp/coopt_agent
sed -i 's/^  values: \[2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17\]/  values: [2, 3, 4]/' configs/legality.yaml
../soc_opt_agent/.venv/bin/pytest tests/test_legality_mirrors_socgen.py -k QUEUE_SIZE -v
```

Expected: **FAIL**, showing the mismatch. Then restore the correct line and
re-run to confirm PASS. Paste both outputs into your report — a drift test that
cannot fail is the same trap as the `validate_buf` this project exists to avoid.

- [ ] **Step 6: Install the package and confirm it imports**

```bash
cd /home/pd2827/esp
soc_opt_agent/.venv/bin/pip install -e "coopt_agent[dev]" 2>&1 | tail -3
soc_opt_agent/.venv/bin/python -c "import coopt_agent; print(coopt_agent.__file__)"
```

Expected: an editable install and an import that resolves under
`coopt_agent/src/coopt_agent/__init__.py`.

- [ ] **Step 7: Confirm the existing suite still passes**

```bash
cd /home/pd2827/esp/soc_opt_agent && .venv/bin/pytest tests -q 2>&1 | tail -3
```

Expected: `90 passed`. Installing a second package into the shared venv must not
disturb it.

- [ ] **Step 8: Commit**

```bash
cd /home/pd2827/esp
git add coopt_agent/
git commit -m "coopt_agent: package scaffolding and the SoC legality table

The legality table is mirrored from ESP's own configuration GUI rather than
written from our understanding: tools/socgen enumerates the legal values its
menus offer, and test_legality_mirrors_socgen.py re-derives them from those
sources so upstream drift fails a test instead of silently producing invalid
configurations. The report records a proposal that set CONFIG_QUEUE_SIZE to
64, well above the legal maximum of 17 -- exactly the class of error this
catches.

Requirements name a dotted field path, so the composite CONFIG_ACC_CACHES and
CONFIG_CPU_CACHES values that soc.py writes as space-separated numbers are
addressable per field.

Co-Authored-By: <the attribution line your own session was given>"
git push fork coopt
```

---

### Task 2: The unified proposal schema

**Files:**
- Create: `coopt_agent/src/coopt_agent/proposal/__init__.py`
- Create: `coopt_agent/src/coopt_agent/proposal/schema.py`
- Create: `coopt_agent/tests/test_proposal.py`

**Interfaces:**
- Consumes: nothing from Task 1 except the installed package.
- Produces: `RtlEdit`, `RtlPatch`, `SocRequirement`, `Proposal`, `ProposalError`,
  `parse_proposal` — exactly as named in the Interfaces section above.

- [ ] **Step 1: Write the failing tests**

`coopt_agent/tests/test_proposal.py`:

```python
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
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `cd /home/pd2827/esp/coopt_agent && ../soc_opt_agent/.venv/bin/pytest tests/test_proposal.py -v`
Expected: every test FAILS with `ModuleNotFoundError: No module named 'coopt_agent.proposal'`.

- [ ] **Step 3: Write the implementation**

`coopt_agent/src/coopt_agent/proposal/schema.py`:

```python
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
```

`coopt_agent/src/coopt_agent/proposal/__init__.py`:

```python
from __future__ import annotations

from coopt_agent.proposal.schema import (
    Proposal,
    ProposalError,
    RtlEdit,
    RtlPatch,
    SocRequirement,
    parse_proposal,
)

__all__ = [
    "Proposal",
    "ProposalError",
    "RtlEdit",
    "RtlPatch",
    "SocRequirement",
    "parse_proposal",
]
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `cd /home/pd2827/esp/coopt_agent && ../soc_opt_agent/.venv/bin/pytest tests/ -v`
Expected: all tests in `test_proposal.py` PASS, and Task 1's legality tests still PASS.

If `test_rejects_edit_whose_texts_are_identical` or `test_rejects_empty_edit_list` fails on the error
message rather than the behaviour, fix the message mapping in `parse_proposal`
rather than weakening the test.

- [ ] **Step 5: Commit**

```bash
cd /home/pd2827/esp
git add coopt_agent/
git commit -m "coopt_agent: unified proposal schema

One proposal carries both layers: the RTL patch that leads, and the SoC
requirements it implies. rtl_patch.edits must be non-empty because this loop
always leads with RTL (spec decision D2); soc_requirements may be empty
because most wrapper edits imply nothing about the memory system.

parse_proposal raises ProposalError for everything unusable, so callers do not
have to tell a JSON failure apart from a validation failure. No-op edits are
rejected: an edit whose old_text equals its new_text would consume a whole
build and simulation to measure nothing.

Co-Authored-By: <the attribution line your own session was given>"
git push fork coopt
```

---

### Task 3: RTL patch application and guards

**Files:**
- Create: `coopt_agent/src/coopt_agent/rtl_edit/__init__.py`
- Create: `coopt_agent/src/coopt_agent/rtl_edit/whitelist.py`
- Create: `coopt_agent/src/coopt_agent/rtl_edit/patch.py`
- Create: `coopt_agent/src/coopt_agent/rtl_edit/guard.py`
- Create: `coopt_agent/tests/test_rtl_edit.py`

**Interfaces:**
- Consumes: `RtlEdit` from Task 2, `configs/editable_files.yaml` from Task 1.
- Produces: `Whitelist`, `PatchError`, `load_whitelist`, `check_target`,
  `apply_edits`, `extract_port_block`, `assert_ports_unchanged`.

This task implements spec §8's three guards. They exist because the optimizer is
scored on cycles, and the fastest wrong answers available to it are "write fewer
outputs", "change the interface" and "edit the compute core".

- [ ] **Step 1: Write the failing tests**

`coopt_agent/tests/test_rtl_edit.py`:

```python
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
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `cd /home/pd2827/esp/coopt_agent && ../soc_opt_agent/.venv/bin/pytest tests/test_rtl_edit.py -v`
Expected: every test FAILS with `ModuleNotFoundError: No module named 'coopt_agent.rtl_edit'`.

- [ ] **Step 3: Write the whitelist loader**

`coopt_agent/src/coopt_agent/rtl_edit/whitelist.py`:

```python
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
```

- [ ] **Step 4: Write the patch applier**

`coopt_agent/src/coopt_agent/rtl_edit/patch.py`:

```python
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
```

- [ ] **Step 5: Write the port-signature guard**

`coopt_agent/src/coopt_agent/rtl_edit/guard.py`:

```python
from __future__ import annotations

import re

from coopt_agent.rtl_edit.whitelist import PatchError


def extract_port_block(source: str, module: str) -> str:
    """Return the parenthesised port list of `module`, verbatim.

    Verilog allows nested parentheses inside port declarations (vectors use
    brackets, but parameter expressions can nest), so the closing paren is found
    by counting depth rather than by matching the first ')'.
    """
    match = re.search(rf"\bmodule\s+{re.escape(module)}\b", source)
    if match is None:
        raise PatchError(f"module {module!r} not found in source")

    open_index = source.find("(", match.end())
    if open_index == -1:
        raise PatchError(f"module {module!r} has no port list")

    depth = 0
    for index in range(open_index, len(source)):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                return source[open_index : index + 1]
    raise PatchError(f"module {module!r} has an unterminated port list")


def assert_ports_unchanged(before: str, after: str, module: str) -> None:
    """Spec section 8 guard 3: the ESP interface contract is not negotiable.

    Compared byte-for-byte. Whitespace inside the port list is not normalised:
    a patch that reformats the interface is still a patch that touched it, and
    this guard is cheap insurance against protocol changes that compile fine and
    then hang the SoC in simulation.
    """
    if extract_port_block(before, module) != extract_port_block(after, module):
        raise PatchError(
            f"the patch changed the port list of module {module!r}; the ESP "
            "interface must stay byte-for-byte identical"
        )
```

`coopt_agent/src/coopt_agent/rtl_edit/__init__.py`:

```python
from __future__ import annotations

from coopt_agent.rtl_edit.guard import assert_ports_unchanged, extract_port_block
from coopt_agent.rtl_edit.patch import apply_edits
from coopt_agent.rtl_edit.whitelist import (
    PatchError,
    Whitelist,
    check_target,
    load_whitelist,
)

__all__ = [
    "PatchError",
    "Whitelist",
    "apply_edits",
    "assert_ports_unchanged",
    "check_target",
    "extract_port_block",
    "load_whitelist",
]
```

- [ ] **Step 6: Run the tests and see them pass**

Run: `cd /home/pd2827/esp/coopt_agent && ../soc_opt_agent/.venv/bin/pytest tests/ -v`
Expected: all tests pass, including Tasks 1 and 2.

- [ ] **Step 7: Prove the guards work on the real wrapper**

The tests above use a small synthetic module. Confirm the port extractor also
handles the actual 334-line wrapper, which has a 30-line port list with comments
and mixed widths:

```bash
cd /home/pd2827/esp
soc_opt_agent/.venv/bin/python - <<'PY'
from pathlib import Path
from coopt_agent.rtl_edit import extract_port_block

src = Path("accelerators/rtl/lstm_rtl/hw/src/lstm_rtl_basic_dma64/"
           "lstm_rtl_basic_dma64.v").read_text()
block = extract_port_block(src, "lstm_rtl_basic_dma64")
print("port block lines:", block.count("\n") + 1)
for name in ("conf_info_in_dim", "dma_read_ctrl_data_length",
             "dma_write_ctrl_data_length", "acc_done", "dma_write_chnl_data"):
    assert name in block, f"missing {name}"
print("all expected ports present")
assert "localparam" not in block, "port block leaked into the module body"
print("body correctly excluded")
PY
```

Expected: the port block contains every named port, excludes `localparam`, and
the line count is plausible for that file (roughly 25-35).

If this fails while the unit tests pass, the synthetic fixture is too simple —
fix the extractor and add a test using the real port list shape, do not weaken
the assertion.

- [ ] **Step 8: Commit**

```bash
cd /home/pd2827/esp
git add coopt_agent/
git commit -m "coopt_agent: RTL patch application with the spec's three guards

The optimizer is scored on cycles, and the fastest wrong answers available to
it are editing the compute core, changing the ESP interface, and applying an
edit somewhere other than where it meant. Each gets a guard:

- an allowlist, not a denylist: only files listed as editable may be touched,
  so lstm.v and lstm_rest.v (the compute core) are refused, and a file added
  upstream later does not become editable by default;
- exact-string edits must match exactly once -- zero means the model invented
  the text, more than one means we cannot know which it meant, and a guess
  here silently changes hardware whose only feedback is a cycle count;
- the top module's port list is compared byte-for-byte before and after, so a
  protocol change that compiles fine and then hangs the SoC is caught before
  the build starts.

Co-Authored-By: <the attribution line your own session was given>"
git push fork coopt
```

---

### Task 4: SoC requirement derivation

**Files:**
- Create: `coopt_agent/src/coopt_agent/soc_relax/__init__.py`
- Create: `coopt_agent/src/coopt_agent/soc_relax/legality.py`
- Create: `coopt_agent/src/coopt_agent/soc_relax/derive.py`
- Create: `coopt_agent/tests/test_soc_relax.py`

**Interfaces:**
- Consumes: `SocRequirement` from Task 2, `configs/legality.yaml` from Task 1.
- Produces: `KnobSpec`, `ClampRecord`, `Derivation`, `LegalityError`,
  `load_legality`, `clamp`, `derive_config`.

This implements spec §9. Two properties matter more than the rest: the config is
**derived from the baseline every time** rather than ratcheted, so a knob a later
patch no longer needs is released; and every clamp is recorded, so the reflection
step can see that a requirement was not fully met.

- [ ] **Step 1: Write the failing tests**

`coopt_agent/tests/test_soc_relax.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from coopt_agent.proposal import SocRequirement
from coopt_agent.soc_relax import (
    KnobSpec,
    LegalityError,
    clamp,
    derive_config,
    load_legality,
)

LEGALITY_PATH = Path(__file__).resolve().parents[1] / "configs" / "legality.yaml"
LEGALITY = load_legality(LEGALITY_PATH)

BASELINE = {
    "CONFIG_QUEUE_SIZE": "4",
    "CONFIG_COH_NOC_WIDTH": "64",
    "CONFIG_DMA_NOC_WIDTH": "64",
    "CONFIG_MEM_LINK_WIDTH": "64",
    "CONFIG_SLM_KBYTES": "256",
    "CONFIG_ACC_CACHES": "512 4",
    "CONFIG_CPU_CACHES": "512 4 1024 16",
    "CONFIG_CACHE_EN": "y",
}


def _req(knob: str, value: int) -> SocRequirement:
    return SocRequirement(knob=knob, min_value=value, why="test")


def test_no_requirements_leaves_baseline_identical() -> None:
    result = derive_config(BASELINE, [], LEGALITY)
    assert result.config == BASELINE
    assert result.clamps == []


def test_scalar_knob_is_raised() -> None:
    result = derive_config(BASELINE, [_req("CONFIG_QUEUE_SIZE", 16)], LEGALITY)
    assert result.config["CONFIG_QUEUE_SIZE"] == "16"


def test_request_below_baseline_does_not_lower_it() -> None:
    result = derive_config(BASELINE, [_req("CONFIG_QUEUE_SIZE", 2)], LEGALITY)
    assert result.config["CONFIG_QUEUE_SIZE"] == "4"


def test_request_above_legal_maximum_is_clamped_and_recorded() -> None:
    # The report records a proposal asking for 64; the legal maximum is 17.
    result = derive_config(BASELINE, [_req("CONFIG_QUEUE_SIZE", 64)], LEGALITY)
    assert result.config["CONFIG_QUEUE_SIZE"] == "17"
    assert len(result.clamps) == 1
    assert result.clamps[0].knob == "CONFIG_QUEUE_SIZE"
    assert result.clamps[0].requested == 64
    assert result.clamps[0].applied == 17


def test_request_between_legal_values_rounds_up() -> None:
    result = derive_config(BASELINE, [_req("CONFIG_SLM_KBYTES", 300)], LEGALITY)
    assert result.config["CONFIG_SLM_KBYTES"] == "512"


def test_composite_field_is_raised_in_place() -> None:
    result = derive_config(
        BASELINE, [_req("CONFIG_ACC_CACHES.acc_l2_sets", 2048)], LEGALITY
    )
    assert result.config["CONFIG_ACC_CACHES"] == "2048 4"


def test_composite_middle_field_is_raised_without_disturbing_neighbours() -> None:
    result = derive_config(
        BASELINE, [_req("CONFIG_CPU_CACHES.llc_sets", 2048)], LEGALITY
    )
    # only the third field moves; 512, 4 and 16 are untouched
    assert result.config["CONFIG_CPU_CACHES"] == "512 4 2048 16"


def test_two_requirements_on_one_knob_take_the_maximum() -> None:
    result = derive_config(
        BASELINE,
        [_req("CONFIG_QUEUE_SIZE", 8), _req("CONFIG_QUEUE_SIZE", 16)],
        LEGALITY,
    )
    assert result.config["CONFIG_QUEUE_SIZE"] == "16"


def test_derivation_is_from_baseline_not_from_previous_result() -> None:
    """Spec section 9: derived every time, never ratcheted."""
    raised = derive_config(BASELINE, [_req("CONFIG_QUEUE_SIZE", 16)], LEGALITY)
    assert raised.config["CONFIG_QUEUE_SIZE"] == "16"
    released = derive_config(BASELINE, [], LEGALITY)
    assert released.config["CONFIG_QUEUE_SIZE"] == "4"


def test_unknown_knob_is_rejected() -> None:
    with pytest.raises(LegalityError, match="not a known knob"):
        derive_config(BASELINE, [_req("CONFIG_MADE_UP", 4)], LEGALITY)


def test_knob_missing_from_baseline_is_rejected() -> None:
    thin = {k: v for k, v in BASELINE.items() if k != "CONFIG_SLM_KBYTES"}
    with pytest.raises(LegalityError, match="not present in the baseline"):
        derive_config(thin, [_req("CONFIG_SLM_KBYTES", 512)], LEGALITY)


def test_composite_with_wrong_field_count_is_rejected() -> None:
    broken = {**BASELINE, "CONFIG_ACC_CACHES": "512"}
    with pytest.raises(LegalityError, match="expected 2 fields"):
        derive_config(broken, [_req("CONFIG_ACC_CACHES.acc_l2_sets", 1024)], LEGALITY)


def test_clamp_returns_flag() -> None:
    spec = KnobSpec(values=[2, 4, 8])
    assert clamp(spec, 4) == (4, False)
    assert clamp(spec, 3) == (4, False)
    assert clamp(spec, 99) == (8, True)


def test_baseline_is_not_mutated() -> None:
    snapshot = dict(BASELINE)
    derive_config(BASELINE, [_req("CONFIG_QUEUE_SIZE", 16)], LEGALITY)
    assert BASELINE == snapshot
```

- [ ] **Step 2: Run the tests and see them fail**

Run: `cd /home/pd2827/esp/coopt_agent && ../soc_opt_agent/.venv/bin/pytest tests/test_soc_relax.py -v`
Expected: collection FAILS with `ModuleNotFoundError: No module named 'coopt_agent.soc_relax'`.

- [ ] **Step 3: Write the legality module**

`coopt_agent/src/coopt_agent/soc_relax/legality.py`:

```python
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
```

- [ ] **Step 4: Write the derivation module**

`coopt_agent/src/coopt_agent/soc_relax/derive.py`:

```python
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
                f"{key} has {len(parts)} fields, expected {len(fields)}: "
                f"{config[key]!r}"
            )
        index = fields.index(subfield)
        parts[index] = str(max(int(parts[index]), applied))
        config[key] = " ".join(parts)

    return Derivation(config=config, clamps=clamps)
```

`coopt_agent/src/coopt_agent/soc_relax/__init__.py`:

```python
from __future__ import annotations

from coopt_agent.soc_relax.derive import (
    COMPOSITE_FIELDS,
    ClampRecord,
    Derivation,
    derive_config,
)
from coopt_agent.soc_relax.legality import (
    KnobSpec,
    LegalityError,
    clamp,
    load_legality,
)

__all__ = [
    "COMPOSITE_FIELDS",
    "ClampRecord",
    "Derivation",
    "KnobSpec",
    "LegalityError",
    "clamp",
    "derive_config",
    "load_legality",
]
```

- [ ] **Step 5: Run the tests and see them pass**

Run: `cd /home/pd2827/esp/coopt_agent && ../soc_opt_agent/.venv/bin/pytest tests/ -v`
Expected: every test across all four tasks passes.

- [ ] **Step 6: Check the derivation against the real baseline config**

```bash
cd /home/pd2827/esp
soc_opt_agent/.venv/bin/python - <<'PY'
from pathlib import Path
from coopt_agent.proposal import SocRequirement
from coopt_agent.soc_relax import derive_config, load_legality

baseline = {}
for line in Path("coopt_agent/configs/baseline_lstm_rtl.esp_config").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        key, _, value = line.partition("=")
        baseline[key.strip()] = value.strip()

legality = load_legality(Path("coopt_agent/configs/legality.yaml"))
result = derive_config(
    baseline,
    [
        SocRequirement(knob="CONFIG_QUEUE_SIZE", min_value=16, why="deeper queue"),
        SocRequirement(knob="CONFIG_ACC_CACHES.acc_l2_sets", min_value=2048, why="hold weights"),
    ],
    legality,
)
print("QUEUE_SIZE :", baseline["CONFIG_QUEUE_SIZE"], "->", result.config["CONFIG_QUEUE_SIZE"])
print("ACC_CACHES :", baseline["CONFIG_ACC_CACHES"], "->", result.config["CONFIG_ACC_CACHES"])
print("clamps     :", result.clamps)
changed = [k for k in baseline if baseline[k] != result.config[k]]
print("keys changed:", changed)
assert set(changed) == {"CONFIG_QUEUE_SIZE", "CONFIG_ACC_CACHES"}, changed
assert set(result.config) == set(baseline), "derivation added or dropped a key"
PY
```

Expected: `CONFIG_QUEUE_SIZE` 4 -> 16, `CONFIG_ACC_CACHES` "512 4" -> "2048 4",
no clamps, and exactly those two keys changed. Every other line of the real
baseline must survive untouched — this is the check that the derivation cannot
quietly disturb the rest of the SoC.

- [ ] **Step 7: Commit**

```bash
cd /home/pd2827/esp
git add coopt_agent/
git commit -m "coopt_agent: SoC requirement derivation

The candidate configuration is derived from the baseline on every iteration,
never ratcheted forward from the previous candidate: a monotonic ratchet
drifts one way toward the maximum and cannot release a knob that a later RTL
change no longer needs (spec section 9).

Requirements name a dotted field path, so the composite CONFIG_ACC_CACHES and
CONFIG_CPU_CACHES values are raised field-in-place without disturbing their
neighbours. Several requirements on one path collapse to their maximum.

Every clamp is recorded rather than silently applied. When the model asks for
more than ESP allows -- the report has a proposal asking for CONFIG_QUEUE_SIZE
64 against a legal maximum of 17 -- the run still happens, but the evidence
says the requirement was not fully met, so a disappointing result is not
mistaken for a failed RTL idea.

Co-Authored-By: <the attribution line your own session was given>"
git push fork coopt
```

---

## What this plan deliberately leaves out

- **`goldengen`** — the correctness gate. Its fixture format and degeneracy
  thresholds depend on what the first real baseline output looks like, which the
  LSTM bring-up plan is still measuring.
- **The orchestrator and the LLM policy** — these need `goldengen`, the ESP flow
  wrappers, and a real transcript to parse. They are the third plan.
- **Prompts** — writing them before there is a metrics record to put in them
  would be guessing at the reflection loop's inputs.

Everything in this plan is pure logic with no ESP dependency, which is why it can
be built and fully tested while a simulation is still running against the same
tree.
