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

Matches the API as documented at
https://www.esp.cs.columbia.edu/docs/monitors_api/monitors_api-guide/ :
esp_monitor is called twice with the same args, ESP_MON_READ_ALL needs no
further fields, esp_monitor_diff handles counter overflow, and
esp_monitor_print goes to the console under bare metal. esp_monitor_vals_alloc
and esp_monitor_free are deliberately absent -- they exist only under Linux.

Two caveats from that guide, worth knowing before trusting a small delta:

  * The counters are approximate. Reading a monitor takes time during which it
    keeps incrementing, and monitors in different tiles are not sampled at the
    same instant. The guide says the error is small and shrinks with longer
    sampling periods -- but it is not zero, so an improvement smaller than the
    noise is not an improvement.
  * esp_monitor_diff is only correct if a counter increments fewer than 2^32
    times between the two calls (2^64 for the two 64-bit ones). One invocation
    of ~12.7M ns of simulated time is far below that; a long multi-invocation
    measurement would need checking.

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
BEGIN = "/* " + MARKER + " BEGIN */"
END = "/* " + MARKER + " END */"

HEADER = """@@BEGIN@@
/* --- @@MARKER@@ ---
 * Monitor and cycle reporting, added by setup/instrument_baremetal.py.
 * The counters are memory-mapped registers; enabling them in .esp_config
 * instantiates the hardware, but only this code makes them observable.
 */
#include <monitors.h>
/* Tile coordinates, generated per SoC. libmonitors.c includes this inside
 * esp_monitor() so the arrays are locals there; including it here gives this
 * file its own copies at file scope, which is what the tile-index computations
 * below need. */
#include "soc_locs.h"

/* Read one monitor. READ_SINGLE returns the value directly and ignores the
 * vals pointer, so NULL is what the guide's Example 1 passes. */
#define __MON_RD(tile, idx, dst)          \\
\tdo {                                     \\
\t\t__mon_args.tile_index = (tile);        \\
\t\t__mon_args.mon_index  = (idx);         \\
\t\t(dst) = esp_monitor(__mon_args, NULL); \\
\t} while (0)

/* sub_monitor_vals accounts for counter overflow between the two reads. */
#define __MON_PR(name, s, e) \\
\tprintf("ESP_MON %s %u\\n", (name), (unsigned) sub_monitor_vals((s), (e)))

/* Reads mcycle, the machine-mode cycle counter, NOT rdcycle.
 *
 * rdcycle is the pseudo-instruction for the user-mode `cycle` CSR (0xC00), and
 * this Ariane configuration traps on it: an instrumented run died on that one
 * instruction while printing nothing further, which the testbench reported as
 * "Program Completed!" because top.vhd asserts on cpuerr. It cost three
 * full simulations to find, because the fault looked like an esp_monitor
 * problem -- esp_monitor sits next to it and is entirely innocent.
 *
 * mcycle (0xB00) is implemented and readable here; the program runs in machine
 * mode, which the CPU trace confirms.
 */
static inline uint64_t esp_read_cycles(void)
{
#ifdef __riscv
\tuint64_t __c;
\t__asm__ volatile ("csrr %0, mcycle" : "=r" (__c));
\treturn __c;
#else
\treturn 0;
#endif
}
@@END@@
""".replace("@@MARKER@@", MARKER).replace("@@BEGIN@@", BEGIN).replace("@@END@@", END)

# Uses ESP_MON_READ_SINGLE -- the mode of Example 1 in ESP's monitors guide --
# rather than READ_ALL. READ_SINGLE returns one counter directly as an unsigned
# int and takes NULL for the vals pointer, so nothing here copies the 740-byte
# esp_monitor_vals_t, and esp_monitor_print is not called at all.
#
# That matters because READ_ALL does not work here. With an otherwise identical
# SoC the un-instrumented program completes in 12,723,520 ns, while the
# READ_ALL-instrumented one dies inside esp_monitor before esp_flush is reached,
# at 5,294,260 ns. The guide says all 59 monitors are always instantiated and
# that reading an inapplicable one merely returns 0, so the cause is not a
# missing register; it has not been identified, and READ_SINGLE sidesteps the
# whole path rather than waiting for an explanation.
#
# Tile indices are computed as row * SOC_COLS + col from the generated
# socgen/esp/soc_locs.h, so they follow the SoC layout rather than being
# hardcoded. Monitor indices come from monitors.h.
BEFORE = """{b}\n{i}/* {m}: capture before the accelerator starts */
{i}esp_monitor_args_t __mon_args;
{i}unsigned int __acc_tot_s, __acc_tot_e, __acc_mem_s, __acc_mem_e;
{i}unsigned int __acc_tlb_s, __acc_tlb_e, __acc_inv_s, __acc_inv_e;
{i}unsigned int __ddr_s, __ddr_e, __llc_h_s, __llc_h_e, __llc_m_s, __llc_m_e;
{i}unsigned int __l2_h_s, __l2_h_e, __l2_m_s, __l2_m_e;
{i}const int __acc_tile = acc_locs[0].row * SOC_COLS + acc_locs[0].col;
{i}const int __mem_tile = mem_locs[0].row * SOC_COLS + mem_locs[0].col;
{i}const int __cpu_tile = cpu_locs[0].row * SOC_COLS + cpu_locs[0].col;
{i}uint64_t __cyc_start, __cyc_end;
{i}__mon_args.read_mode = ESP_MON_READ_SINGLE;
{i}__MON_RD(__acc_tile, MON_ACC_TOT_LO_INDEX, __acc_tot_s);
{i}__MON_RD(__acc_tile, MON_ACC_MEM_LO_INDEX, __acc_mem_s);
{i}__MON_RD(__acc_tile, MON_ACC_TLB_INDEX, __acc_tlb_s);
{i}__MON_RD(__acc_tile, MON_ACC_INVOCATIONS, __acc_inv_s);
{i}__MON_RD(__mem_tile, MON_DDR_WORD_TRANSFER_INDEX, __ddr_s);
{i}__MON_RD(__mem_tile, MON_LLC_HIT_INDEX, __llc_h_s);
{i}__MON_RD(__mem_tile, MON_LLC_MISS_INDEX, __llc_m_s);
{i}__MON_RD(__cpu_tile, MON_L2_HIT_INDEX, __l2_h_s);
{i}__MON_RD(__cpu_tile, MON_L2_MISS_INDEX, __l2_m_s);
{i}__cyc_start = esp_read_cycles();
{i}{e}
{i}"""

AFTER = """
{i}{b}
{i}/* {m}: capture after the accelerator reports done */
{i}__cyc_end = esp_read_cycles();
{i}__MON_RD(__acc_tile, MON_ACC_TOT_LO_INDEX, __acc_tot_e);
{i}__MON_RD(__acc_tile, MON_ACC_MEM_LO_INDEX, __acc_mem_e);
{i}__MON_RD(__acc_tile, MON_ACC_TLB_INDEX, __acc_tlb_e);
{i}__MON_RD(__acc_tile, MON_ACC_INVOCATIONS, __acc_inv_e);
{i}__MON_RD(__mem_tile, MON_DDR_WORD_TRANSFER_INDEX, __ddr_e);
{i}__MON_RD(__mem_tile, MON_LLC_HIT_INDEX, __llc_h_e);
{i}__MON_RD(__mem_tile, MON_LLC_MISS_INDEX, __llc_m_e);
{i}__MON_RD(__cpu_tile, MON_L2_HIT_INDEX, __l2_h_e);
{i}__MON_RD(__cpu_tile, MON_L2_MISS_INDEX, __l2_m_e);
{i}printf("ESP_MON_BEGIN\\n");
{i}printf("ESP_CPU_CYCLES %llu\\n", (unsigned long long)(__cyc_end - __cyc_start));
{i}__MON_PR("acc_total_cycles", __acc_tot_s, __acc_tot_e);
{i}__MON_PR("acc_mem_cycles",   __acc_mem_s, __acc_mem_e);
{i}__MON_PR("acc_tlb_cycles",   __acc_tlb_s, __acc_tlb_e);
{i}__MON_PR("acc_invocations",  __acc_inv_s, __acc_inv_e);
{i}__MON_PR("ddr_accesses",     __ddr_s,     __ddr_e);
{i}__MON_PR("llc_hits",         __llc_h_s,   __llc_h_e);
{i}__MON_PR("llc_misses",       __llc_m_s,   __llc_m_e);
{i}__MON_PR("l2_hits",          __l2_h_s,    __l2_h_e);
{i}__MON_PR("l2_misses",        __l2_m_s,    __l2_m_e);
{i}printf("ESP_MON_END\\n");
{i}{e}"""

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
        BEFORE.format(i=indent_of(flush_line), m=MARKER, b=BEGIN, e=END) + flush_line.lstrip(),
        1,
    )
    text = text.replace(
        stop_line,
        stop_line.rstrip("\n")
        + AFTER.format(i=indent_of(stop_line), m=MARKER, b=BEGIN, e=END)
        + "\n",
        1,
    )

    path.write_text(text)
    return "%s: instrumented (%s)" % (accelerator, path.relative_to(ESP_ROOT))


def revert(accelerator):
    """Strip the instrumentation back out.

    Not `git checkout`: the instrumented version is itself committed, so
    restoring from git is a no-op that silently reports success. Removing the
    inserted text is the only thing that actually reverts.
    """
    path = source_for(accelerator)
    text = path.read_text()
    if MARKER not in text:
        return "%s: not instrumented" % accelerator

    # Delete everything between the BEGIN and END markers, inclusive. This is
    # deliberately independent of what the current templates contain: an earlier
    # version of this script reverted by matching the templates, and when the
    # templates were edited between instrumenting and reverting it silently ate
    # ~100 lines of the program instead.
    lines = text.splitlines(keepends=True)
    kept, skipping = [], False
    for line in lines:
        if BEGIN in line:
            skipping = True
            continue
        if skipping:
            if END in line:
                skipping = False
            continue
        kept.append(line)

    path.write_text("".join(kept))
    remaining = path.read_text()
    if MARKER in remaining or "esp_monitor" in remaining:
        return "%s: INCOMPLETE revert -- traces remain, check by hand" % accelerator
    if skipping:
        return "%s: BROKEN revert -- a BEGIN marker had no END; file may be truncated" % accelerator
    return "%s: instrumentation removed" % accelerator


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
