"""Drift test for `configs/editable_files.yaml` against the real RTL tree.

The whitelist is the structural enforcement of decision D1 -- "the compute
core's arithmetic and the ESP interface are off limits". `configs/legality.yaml`
already has a drift test against socgen; this file gives the whitelist the same
protection, because a whitelist that has silently drifted from the sources is a
guard that no longer guards anything: a renamed file makes every patch bounce,
and a top_module that no longer matches makes the port guard compare the wrong
module.

Read-only. Nothing here writes into `accelerators/`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from coopt_agent.rtl_edit import Whitelist, blank_comments, load_whitelist

REPO = Path(__file__).resolve().parents[2]
RTL_ROOT = REPO / "accelerators" / "rtl"
WHITELIST = load_whitelist(
    Path(__file__).resolve().parents[1] / "configs" / "editable_files.yaml"
)

ACCELERATORS = sorted(WHITELIST)


def _modules(source: str) -> list[str]:
    """Every module name declared in `source`, comments excluded."""
    return re.findall(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_$]*)", blank_comments(source))


def _accelerator_root(accelerator: str) -> Path:
    return RTL_ROOT / accelerator


def test_the_whitelist_is_not_empty() -> None:
    assert ACCELERATORS


@pytest.mark.parametrize("accelerator", ACCELERATORS)
def test_accelerator_directory_exists(accelerator: str) -> None:
    assert _accelerator_root(accelerator).is_dir()


@pytest.mark.parametrize("accelerator", ACCELERATORS)
def test_every_listed_path_exists(accelerator: str) -> None:
    entry: Whitelist = WHITELIST[accelerator]
    root = _accelerator_root(accelerator)
    missing = [
        path
        for path in [*entry.editable, *entry.read_only]
        if not (root / path).is_file()
    ]
    assert not missing, f"{accelerator}: whitelisted paths that no longer exist: {missing}"


@pytest.mark.parametrize("accelerator", ACCELERATORS)
def test_editable_files_are_listed(accelerator: str) -> None:
    assert WHITELIST[accelerator].editable


@pytest.mark.parametrize("accelerator", ACCELERATORS)
def test_top_module_is_declared_in_an_editable_file(accelerator: str) -> None:
    """The port guard extracts `top_module` from the patched file. If no
    editable file declares it, guard 3 could never run on a real patch."""
    entry = WHITELIST[accelerator]
    root = _accelerator_root(accelerator)
    declaring = [
        path
        for path in entry.editable
        if entry.top_module in _modules((root / path).read_text())
    ]
    assert declaring, (
        f"{accelerator}: top_module {entry.top_module!r} is not declared in any "
        f"editable file {entry.editable}"
    )


@pytest.mark.parametrize("accelerator", ACCELERATORS)
def test_read_only_files_hold_something_other_than_the_top_module(
    accelerator: str,
) -> None:
    """Each read_only file must declare at least one module that is not the
    wrapper -- that is what makes it compute core, and what D1 protects."""
    entry = WHITELIST[accelerator]
    root = _accelerator_root(accelerator)
    for path in entry.read_only:
        modules = _modules((root / path).read_text())
        others = [name for name in modules if name != entry.top_module]
        assert others, (
            f"{accelerator}: read_only file {path} declares no module other "
            f"than the top module; modules found: {modules}"
        )


@pytest.mark.parametrize("accelerator", ACCELERATORS)
def test_editable_and_read_only_do_not_overlap(accelerator: str) -> None:
    entry = WHITELIST[accelerator]
    assert not set(entry.editable) & set(entry.read_only)
