#!/usr/bin/env python3
"""Render the baseline sweep's per-accelerator JSON results as one table.

Regenerated after every accelerator, so the sweep's progress is readable while
it is still running.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Worst first: the whole point of the sweep is to find which accelerators cannot
# complete a run, so those sort to the top where they will be read.
ORDER = [
    "install_failed",
    "config_failed",
    "not_in_socmap",
    "socketgen_failed",
    "build_failed",
    "sim_compile_failed",
    "no_boot",
    "no_start",
    "no_invoke",
    "no_done",
    "timeout",
    "fail",
    "completed_no_verdict",
    "no_transcript",
    "pass",
]

USABLE = {"pass", "fail", "completed_no_verdict"}


def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    results = []
    for path in sorted(out_dir.glob("*.json")):
        try:
            results.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue

    if not results:
        print("# Baseline sweep\n\nNo results yet.")
        return 0

    results.sort(key=lambda r: (ORDER.index(r["status"]) if r["status"] in ORDER else 99,
                                r["accelerator"]))

    print("# Baseline sweep — all RTL accelerators\n")
    print("Stock SLDB baremetal software, unmodified RTL, default coherence.\n")
    print("**A `pass` here means the flow completed, not that the accelerator is "
          "correct.** These programs' validation is unreliable — `lstm_rtl`'s "
          "comparison is commented out and always prints PASS — so correctness "
          "is decided later, off-chip, against a golden vector.\n")

    print("| accelerator | status | failed stage | detail | sim time (ns) | wall |")
    print("|---|---|---|---|---|---|")
    for r in results:
        sim = f"{r['sim_time_ns']:,}" if r.get("sim_time_ns") else "—"
        wall = r.get("wall_seconds") or 0
        wall_s = f"{wall // 60}m{wall % 60:02d}s" if wall else "—"
        print(f"| `{r['accelerator']}` | **{r['status']}** | {r['failed_stage'] or '—'} "
              f"| {r['detail'] or '—'} | {sim} | {wall_s} |")

    done = [r for r in results if r["status"] in USABLE]
    broken = [r for r in results if r["status"] not in USABLE]

    print(f"\n**{len(done)} of {len(results)} completed a run.**\n")
    if broken:
        print("Did not complete:\n")
        for r in broken:
            print(f"- `{r['accelerator']}` — {r['status']}"
                  f"{': ' + r['detail'] if r['detail'] else ''}")
        print("\nEach of these needs accelerator-specific work before it can be "
              "optimized: the loop cannot score a candidate it cannot run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
