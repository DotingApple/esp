# Baseline sweep — all RTL accelerators

Stock SLDB baremetal software, unmodified RTL, default coherence.

**A `pass` here means the flow completed, not that the accelerator is correct.** These programs' validation is unreliable — `lstm_rtl`'s comparison is commented out and always prints PASS — so correctness is decided later, off-chip, against a golden vector.

| accelerator | status | failed stage | detail | sim time (ns) | wall |
|---|---|---|---|---|---|
| `lstm_rtl` | **pass** | — | software reported PASS (may be vacuous) | 12,723,520 | 21m55s |

**1 of 1 completed a run.**

