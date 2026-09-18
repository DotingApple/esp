#!/usr/bin/env python3
"""Add ESP monitor and cycle-count reporting to an accelerator's baremetal program.

The SoC's monitor counters are memory-mapped registers: enabling
CONFIG_MON_ACCELERATORS and friends instantiates the hardware, but nothing is
printed unless software reads it. None of the stock SLDB baremetal programs do,
which is why a run that completes successfully still reports no cycle numbers at
all. The modified harness that did this on the previous server was lost with it.

Every SLDB program shares the same invocation shape, so this patch is mechanical
and identical across accelerators:

    <declare monitors, capture start>
    esp_flush(coherence);
    printf("  Start...\n");
    iowrite32(dev, CMD_REG, CMD_MASK_START);
    while (!done) { ... }
    iowrite32(dev, CMD_REG, 0x0);
    <capture end, print diff and cycles>

No build changes are needed: soft/common/drivers/baremetal/common_bare.mk --
which every accelerator's Makefile includes -- already puts monitors.h on the
include path and links libmonitors, because ESP's own fft_monitors example is
built by exactly the same rules.

Idempotent: a file that already carries the marker is left alone.

Usage:  setup/instrument_baremetal.py <accelerator> [...]
        setup/instrument_baremetal.py --all
        setup/instrument_baremetal.py --revert <accelerator> [...]
"""

import re
import subprocess
import sys
from pathlib import Path

ESP_ROOT = Path(__file__).resolve().parents[1]
ACC_ROOT = ESP_ROOT / "accelerators" / "rtl"

MARKER = "ESP_MON_INSTRUMENTED"

HEADER = """
/* --- %s ---
 * Monitor and cycle reporting, added by setup/instrument_baremetal.py.
 * The counters are memory-mapped registers; enabling them in .esp_config
 * instantiates the hardware, but only this code makes them observable.
 */
#include <monitors.h>

static inline uint64_t esp_read_cycles(void)
{
#ifdef __riscv
\tuint64_t __c;
\t__asm__ volatile ("rdcycle %%0" : "=r" (__c));
\treturn __c;
#else
\treturn 0;
#endif
}
""" % MARKER

BEFORE = """/* {m}: capture before the accelerator starts */
{i}esp_monitor_args_t __mon_args;
{i}esp_monitor_vals_t __mon_start, __mon_end, __mon_diff;
{i}uint64_t __cyc_start, __cyc_end;
{i}__mon_args.read_mode = ESP_MON_READ_ALL;
{i}esp_monitor(__mon_args, &__mon_start);
{i}__cyc_start = esp_read_cycles();
{i}"""

AFTER = """
{i}/* {m}: capture after the accelerator reports done */
{i}__cyc_end = esp_read_cycles();
{i}esp_monitor(__mon_args, &__mon_end);
{i}__mon_diff = esp_monitor_diff(__mon_start, __mon_end);
{i}printf("ESP_CPU_CYCLES %llu\\n",
{i}       (unsigned long long) (__cyc_end - __cyc_start));
{i}esp_monitor_print(__mon_args, __mon_diff);"""

FLUSH = "esp_flush(coherence);"
STOP = "iowrite32(dev, CMD_REG, 0x0);"


def source_for(accelerator):
    candidates = sorted((ACC_ROOT / accelerator / "sw" / "baremetal").glob("*.c"))
    if len(candidates) != 1:
        raise SystemExit(
            "%s: expected exactly one baremetal .c, found %d"
            % (accelerator, len(candidates))
        )
    return candidates[0]


def indent_of(line):
    return re.match(r"[ \t]*", line).group(0)


def instrument(accelerator):
    path = source_for(accelerator)
    text = path.read_text()

    if MARKER in text:
        return "%s: already instrumented" % accelerator

    # Anchors must be unambiguous. Every SLDB program has exactly one of each;
    # anything else means this accelerator does not follow the template and
    # deserves a look rather than a guess.
    for anchor in (FLUSH, STOP):
        if text.count(anchor) != 1:
            return "%s: SKIPPED -- %r occurs %d times, expected 1" % (
                accelerator,
                anchor,
                text.count(anchor),
            )

    lines = text.splitlines(keepends=True)

    # The header goes after the last top-level #include, so it can rely on the
    # program's own includes and still precede every use.
    last_include = max(
        i for i, l in enumerate(lines) if l.lstrip().startswith("#include")
    )
    lines.insert(last_include + 1, HEADER)

    text = "".join(lines)

    flush_line = next(l for l in text.splitlines(True) if FLUSH in l)
    stop_line = next(l for l in text.splitlines(True) if STOP in l)

    text = text.replace(
        flush_line,
        BEFORE.format(i=indent_of(flush_line), m=MARKER) + flush_line.lstrip(),
        1,
    )
    text = text.replace(
        stop_line,
        stop_line.rstrip("\n")
        + AFTER.format(i=indent_of(stop_line), m=MARKER)
        + "\n",
        1,
    )

    path.write_text(text)
    return "%s: instrumented (%s)" % (accelerator, path.relative_to(ESP_ROOT))


def revert(accelerator):
    path = source_for(accelerator)
    if MARKER not in path.read_text():
        return "%s: not instrumented" % accelerator
    subprocess.run(
        ["git", "-C", str(ESP_ROOT), "checkout", "--", str(path)], check=True
    )
    return "%s: reverted to the committed version" % accelerator


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)

    reverting = args[0] == "--revert"
    if reverting:
        args = args[1:]

    if args == ["--all"]:
        args = sorted(
            d.name for d in ACC_ROOT.iterdir() if d.is_dir() and d.name != "common"
        )

    for accelerator in args:
        print(revert(accelerator) if reverting else instrument(accelerator))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
