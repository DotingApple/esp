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
