# Baseline sweep — all RTL accelerators

Stock SLDB baremetal software, unmodified RTL, default coherence.

**A `pass` here means the flow completed, not that the accelerator is correct.** These programs' validation is unreliable — `lstm_rtl`'s comparison is commented out and always prints PASS — so correctness is decided later, off-chip, against a golden vector.

| accelerator | status | failed stage | detail | sim time (ns) | wall |
|---|---|---|---|---|---|
| `aesdecipher_v2_rtl` | **sim_compile_failed** | sim | no transcript produced | — | 0m50s |
| `sha256_rtl` | **sim_compile_failed** | sim | no transcript produced | — | 0m25s |
| `sobel_v2_rtl` | **sim_compile_failed** | sim | no transcript produced | — | 0m50s |
| `conv_new_rtl` | **timeout** | sim | killed after 2700s; reached Start... then stalled | — | 45m35s |
| `simple_dnn_rtl` | **timeout** | sim | killed after 2700s; reached Start... then stalled | — | 45m35s |
| `aescipher_rtl` | **pass** | — | software reported PASS (may be vacuous) | 13,565,800 | 23m30s |
| `fcdnn_acc_rtl` | **pass** | — | software reported PASS (may be vacuous) | 14,322,860 | 19m10s |
| `fft_64_rtl` | **pass** | — | software reported PASS (may be vacuous) | 15,571,840 | 20m06s |
| `lstm_rtl` | **pass** | — | software reported PASS (may be vacuous) | 12,723,520 | 21m55s |
| `spmv_rtl` | **pass** | — | software reported PASS (may be vacuous) | 16,735,160 | 23m30s |

**5 of 10 completed a run.**

Did not complete:

- `aesdecipher_v2_rtl` — sim_compile_failed: no transcript produced
- `sha256_rtl` — sim_compile_failed: no transcript produced
- `sobel_v2_rtl` — sim_compile_failed: no transcript produced
- `conv_new_rtl` — timeout: killed after 2700s; reached Start... then stalled
- `simple_dnn_rtl` — timeout: killed after 2700s; reached Start... then stalled

Each of these needs accelerator-specific work before it can be optimized: the loop cannot score a candidate it cannot run.
