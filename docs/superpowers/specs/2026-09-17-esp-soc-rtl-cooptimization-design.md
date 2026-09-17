# Unified SoC + RTL Co-Optimization of ESP Accelerators — Design

Date: 2026-09-17
Status: approved for planning
Supersedes: the split `soc_opt_agent` / `rtl_opt_agent` design described in the
CSEE6868 final report (`System_Level_Optimization_of_LLM_Generated_RTL_Using_ESP_Monitors`)

## 1. Context

The original project ran two separate LLM agent loops: a SoC configuration loop
and, afterwards, an RTL wrapper loop. The SoC loop produced no consistent latency
improvement because its knobs were indirect relative to the active wrapper/DMA
bottleneck; the RTL loop cut LSTM end-to-end cycles by about 58%.

Only `soc_opt_agent` survived the loss of the old server. `rtl_opt_agent`, the
`goldengen` correctness gate and the modified baremetal harness are gone and are
rebuilt here.

This design replaces the sequential two-loop structure with a single loop, on the
principle stated by the project owner: **SoC parameters must not throttle the data
movement the RTL wrapper wants to perform.** SoC configuration is not an
independent search dimension; it exists to keep out of the wrapper's way.

The objective for this phase is **cycle reduction only**. Area, timing and power
are not optimized and not scored.

## 2. Scope

In scope:

- one accelerator: `lstm_rtl`
- a new `coopt_agent` package implementing the unified loop
- a new standalone `goldengen` package implementing the correctness gate
- rebuilding the baremetal harness for `lstm_rtl`
- a fresh baseline measured on the current server

Out of scope for this phase:

- the other nine SLDB accelerators (the framework stays generic; no code is
  written for accelerators that have not been exercised)
- formal verification (see §17)
- any reuse of measurements taken on the old server — every number is re-measured

## 3. Decisions

Each decision below was settled with the project owner during brainstorming.

| # | Decision | Rationale |
|---|---|---|
| D1 | The LLM may change wrapper-level data scheduling only. The compute core's arithmetic and the ESP interface are off limits. | Matches what actually produced the 58% win. Keeps the search on the contract between wrapper and memory system. |
| D2 | RTL leads; SoC widens in service of the RTL change. | The owner's stated intent. Also removes the attribution ambiguity of a joint proposal: a cycle delta is attributable to the RTL edit. |
| D3 | The LLM declares the SoC requirements its patch implies, in the same proposal. | Only the model that just wrote the patch knows its semantics. A hand-written derivation table would encode our guesses instead. |
| D4 | The golden is the baseline RTL's own output on a fixed fixture, guarded by a degeneracy check. | The goal is "must not regress from the trusted baseline", not absolute mathematical correctness. An independent reference model would require reverse-engineering fixed-point semantics from 1267 lines of Verilog — a separate sub-project. |
| D5 | `goldengen` is a standalone package, not a module inside `coopt_agent`. | It must be usable on its own, and it is the seam where formal verification plugs in later as a second backend. |

## 4. Why a correctness gate is mandatory

`accelerators/rtl/lstm_rtl/sw/baremetal/lstm.c` as shipped does this:

```c
static int validate_buf(token_t *out, token_t *gold)
{
    unsigned errors = 0;
    // for (i = 0; i < num_timesteps; i++)
    //     for (j = 0; j < hidden_dim; j++)
    //         if (gold[...] != out[...])
    //             errors++;
    return errors;          /* always 0 */
}
```

The comparison is commented out, and `init_buf` never populates `gold`. The
program prints `PASS` unconditionally, whatever the accelerator computed. The
Linux app (`sw/linux/app/lstm.c`) does compare, but against `gold[j] = (token_t) j`
— the SLDB template's placeholder ramp, not an LSTM result.

So there is no working correctness check anywhere in this accelerator, and the
"Transcript validation: Pass" rows in the report's Table 2 carry no information.
An optimizer scored on cycles alone, with a gate that always passes, will
eventually accept a patch that is fast because it is wrong.

This is also the design's cautionary example: **a gate that always passes is worse
than no gate**, because it makes everyone believe the work was validated. The
degeneracy check in §10 exists specifically to prevent rebuilding that failure
mode.

## 5. Architecture

Three Python packages under `/home/pd2827/esp`, installed editable into one venv:

```
soc_opt_agent/      existing, unchanged — reused as a library
goldengen/          new, standalone correctness gate
coopt_agent/        new, the unified loop; depends on both
```

`coopt_agent` reuses from `soc_opt_agent` rather than forking it:

| Module | Used for |
|---|---|
| `esp_flow/executor` | running ESP commands under `/opt/cad/scripts/tools_env.sh`, venv sanitation, output streaming |
| `esp_flow/commands` | `make esp-config`, `make <acc>-baremetal`, `make sim` |
| `monitor/transcript_parser` | ESP monitor metrics out of the ModelSim transcript |
| `config_edit/{parser,editor,validators}` | `.esp_config` parse / edit / allowlist enforcement |
| `scoring/compare` | improvement ratio and plateau detection |
| `paths` | ESP root discovery (no hardcoded paths) |
| `llm/client` | litellm-backed model client |

`soc_opt_agent` stays installed and its 90 tests keep running: they are the
regression net for these primitives. The cost of this choice is that a now-obsolete
SoC-only CLI remains in the tree; we accept that rather than duplicate tested code.

New in `coopt_agent`:

```
proposal/     unified proposal schema + validation
rtl_edit/     exact-string patching, editable-file whitelist, port-signature guard
soc_relax/    requirements -> .esp_config derivation
orchestrator/ baseline, iteration, campaign loop, evidence writing
harness/      transcript -> output-vector extraction helpers
```

## 6. The loop

One iteration, one build, one simulation, one accept/reject decision:

```
current best  (wrapper .v + accumulated SoC requirements)
  |
  |  prompt: wrapper source, current SoC config, previous metrics and
  |          monitor counters, recent history
  v
LLM -> unified proposal (§7)
  |
  v
apply RTL patch          -- exact string replacement; guards in §8
derive SoC config        -- §9
  |
  v
make esp-config  ->  make lstm_rtl-baremetal  ->  make sim
  |
  v
parse transcript  ->  metrics + per-invocation output vectors
  |
  v
goldengen check   ->  golden_report.json
  |
  v
accept / reject   -- §12
```

## 7. Unified proposal schema

```json
{
  "rationale": "why this change should reduce cycles",
  "rtl_patch": {
    "file": "accelerators/rtl/lstm_rtl/hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v",
    "edits": [
      {"old_text": "<exact excerpt>", "new_text": "<replacement>"}
    ]
  },
  "soc_requirements": [
    {
      "knob": "CONFIG_QUEUE_SIZE",
      "min_value": 16,
      "why": "1024-beat bursts need a deeper queue to keep requests outstanding"
    }
  ],
  "expected_effect": "what should move in the metrics, so the next reflection can check it"
}
```

`soc_requirements` may be empty — many wrapper edits imply nothing about the SoC.
`rtl_patch.edits` must be non-empty: this loop always leads with RTL.

Model settings: temperature 0.2 (RTL edits must be conservative and exact — the
report used 1.0 only for the discrete SoC search, which no longer exists here),
request timeout 300 s, proposal timeout 330 s.

## 8. RTL patch application and guards

Applied in order; any failure rejects the candidate before any build is attempted:

1. **Editable-file whitelist.** For `lstm_rtl`, only
   `hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v` is writable.
   `lstm.v` (`module lstm`, the compute core top) and `lstm_rest.v`
   (`lstm_top`, `vecmat_mul_x`, `vecmat_add_h`, `signedmul`, `qadd2`, `spram_*`)
   are read-only. D1 is enforced structurally by this list, not by asking the
   model nicely in a prompt.
2. **Exact, unique match.** Each `old_text` must occur exactly once in the target
   file. Zero occurrences or more than one is a rejection, never a guess.
3. **Port-signature guard.** After patching, the top wrapper module's port
   declaration is extracted and compared byte-for-byte against the baseline. Any
   difference rejects the candidate. This protects the ESP interface contract
   (D1) independently of the whitelist.

## 9. SoC requirement derivation

The candidate config is **derived from the baseline every time**, never ratcheted:

```
for each knob k in the allowlist:
    value(k) = max( baseline(k),
                    max(min_value of every requirement naming k, taken over
                        the accumulated requirements of the current best
                        UNION the requirements of this proposal) )
```

Each accepted proposal's `soc_requirements` are stored alongside it, so the set is
reproducible and the minimum sufficient configuration can be recomputed at any
time. A monotonic ratchet was rejected: it drifts one-way toward the maximum and
cannot release a knob that a later RTL change no longer needs.

**Requirements are restricted to scalar numeric knobs.** `.esp_config` values are
opaque strings to `soc_opt_agent`'s parser (`ConfigEntry.value: str`), and
`CONFIG_CPU_CACHES` / `CONFIG_ACC_CACHES` carry composite values — the report
records a proposal setting `CONFIG_ACC_CACHES` to `1024 8`. A scalar `min_value`
cannot express those. For this phase `soc_requirements` may therefore name only:

`CONFIG_QUEUE_SIZE`, `CONFIG_COH_NOC_WIDTH`, `CONFIG_DMA_NOC_WIDTH`,
`CONFIG_MEM_LINK_WIDTH`, `CONFIG_SLM_KBYTES`

The two cache knobs stay in the allowlist — the baseline may already set them —
but the LLM may not raise them. Extending the requirement shape to composite
knobs is future work.

Validation is layered, because the existing validators are not sufficient on
their own. `soc_opt_agent/config_edit/validators.py` checks allowlist membership,
duplicate keys and mixed enabled/disabled states; **it performs no numeric
legality checking**. `coopt_agent` therefore adds a legality table of its own
giving, per scalar knob, the legal values or the legal range. ESP does not expose
its own legality constraints in a queryable form, so this table is our own and
must be treated as fallible: a value it admits can still make ESP generate cache
RTL that fails to compile, which is one of the failure modes the report observed.

Order of checks on each derived value:

1. the knob is in the allowlist and is a scalar knob (above)
2. the requested value is clamped to the nearest legal value at or above the
   request, per the legality table
3. a request above the legal maximum is clamped to that maximum, and the clamp is
   recorded in `soc_derivation.json` so the reflection step can see that the
   requirement was not fully met
4. a generation or compilation failure after a clamp is reported as such, not
   silently scored as a bad RTL patch

Widening is not free: the report observed that aggressive cache/memory settings
can generate cache RTL that fails to compile, and that full coherence is far too
slow to simulate in a loop. Coherence mode stays `ACC_COH_LLC` and is not a
search dimension.

## 10. goldengen (standalone package)

CLI:

```
goldengen generate --fixture <path> --transcript <path> --out golden.json
goldengen check    --golden  <path> --transcript <path> --out golden_report.json
```

Layout:

```
goldengen/
  src/goldengen/
    fixtures.py      fixture load/validate
    extract.py       transcript -> per-invocation output vectors
    compare.py       expected vs actual, elementwise
    degeneracy.py    the health check below
    adapters/
      base.py        Adapter protocol
      lstm.py        LstmAdapter
    cli.py
  fixtures/lstm/
    fixture.json
    golden.json
```

An adapter declares: the accelerator's parameters, the expected output vector
length, and how to locate the output block in a transcript. Adding an accelerator
means adding an adapter and a fixture, not touching the engine.

**Degeneracy check — runs at `generate` time. If it fails, goldengen refuses to
write a golden and the campaign stops.** A golden that passes trivially is the
failure mode of §4 rebuilt.

1. an output block is present for every invocation
2. each vector's length is exactly `hidden_dim * num_timesteps` (64 for the
   current fixture)
3. not all elements are zero
4. not all elements are identical — a constant vector is as empty as a zero one
5. all invocations produce identical vectors

`check` compares elementwise against the golden and additionally re-asserts (5).
Rule 5 is not ceremony: "reuse persistent local weight buffers across
invocations" is one of the report's own RTL optimization targets, and a patch
that reuses weights but resets them incorrectly produces a correct invocation 0
and wrong invocations 1 and 2. A single-invocation golden cannot see that.

## 11. Harness rebuild

`accelerators/rtl/lstm_rtl/sw/baremetal/lstm.c` is currently the stock SLDB
version; every modification described in the report's §7.1 is gone. Rebuild,
following the pattern of ESP's own `soft/common/apps/baremetal/fft_monitors/fft_monitors.c`:

1. coherence `ACC_COH_LLC` (currently hardcoded `ACC_COH_NONE` under `__riscv`)
2. three accelerator invocations
3. per invocation: `esp_monitor(&start)` / run / `esp_monitor(&end)` /
   `esp_monitor_diff` / `esp_monitor_print`
4. per invocation: total cycles including memory access
5. an end-to-end cycle count spanning the first invocation to the final decision
6. per invocation, the output vector in a delimited block, as raw 16-bit hex
   patterns to avoid `int16` sign ambiguity:

```
ESP_OUT_BEGIN inv=0 len=64
0x0123
...
ESP_OUT_END
```

7. `validate_buf` is **not** faked into looking like it works. Building a real
   in-program check needs a reference model, which is D4's explicit non-goal and
   §17's future work. The program stops printing a `PASS` it cannot justify, and
   goldengen becomes the single correctness authority. One honest gate beats two
   gates where one is stuck open.

## 12. Accept / reject

A candidate is accepted only if **all** hold:

- the ESP config, baremetal build and ModelSim run all complete
- goldengen `check` passes, including the all-invocations-identical rule
- end-to-end cycles improve over the current best by at least
  `minimum_improvement` (0.02, from `soc_opt_agent/configs/scoring.yaml`)

End-to-end cycles including memory access is the primary metric — it is what a
user observes. Accelerator total cycles, monitor counters and any SoC clamps are
recorded for the reflection prompt but do not decide acceptance. The report's LSTM
result is precisely a case where accelerator total cycles barely moved while the
CPU-observed window collapsed; scoring on the accelerator window would have
rejected the best candidate found.

Plateau: stop after `plateau_window` (10) iterations without a qualifying
improvement.

## 13. Evidence layout

Per run, mirroring the structure that worked before:

```
coopt_agent/runs/run_<timestamp>_<acc>/
  proposal.json          the LLM's exact proposal
  rtl_before.v           wrapper before the patch
  rtl_after.v            wrapper after the patch
  config_before.esp_config
  config_after.esp_config
  soc_derivation.json    requirements in, clamps applied, values out
  commands.log           build and simulation output
  transcript.txt         ModelSim transcript
  metrics.json           parsed metrics
  golden_actual.json     extracted output vectors
  golden_report.json     comparison result
  summary.json           accept/reject and why
```

`baselines/<acc>/` and `bests/<acc>/` follow the same shape.

## 14. Testing strategy

TDD throughout: a failing test before each behaviour. Everything below runs
without ModelSim, against recorded transcript fixtures.

| Area | Cases |
|---|---|
| proposal schema | well-formed accepted; empty `rtl_patch.edits` rejected; unknown knob rejected; malformed JSON rejected |
| rtl_edit | unique match applied; zero matches rejected; multiple matches rejected; write to `lstm.v` or `lstm_rest.v` rejected; port-signature change rejected |
| soc_relax | derivation from baseline; union with the best's accumulated requirements; clamp at legal maximum recorded in `soc_derivation.json`; non-allowlisted knob rejected; composite knob (`CONFIG_ACC_CACHES`) rejected as a requirement; empty requirements leave the config byte-identical to baseline; a later best that drops a requirement releases that knob |
| extract | well-formed block parsed; missing block; truncated block; wrong length; multiple invocations |
| degeneracy | all-zero rejected; constant rejected; wrong length rejected; invocations differing rejected; healthy vector accepted |
| compare | identical passes; single-element difference fails and reports the index |
| accept/reject | improvement accepted; below threshold rejected; golden failure rejected even when faster; build failure rejected |
| campaign | plateau stops; best updates only on acceptance |

Fixtures: real transcripts captured from the first baseline run, plus
hand-constructed degenerate variants.

## 15. Definition of done for this phase

Unit tests passing is not sufficient. The phase is done when:

1. all unit tests pass
2. `make lstm_rtl-baremetal` compiles with the rebuilt harness on this server
3. a ModelSim run completes and the transcript contains parseable monitor stats,
   cycle counts and three output blocks
4. `goldengen generate` produces a golden that survives the degeneracy check —
   if the baseline output turns out to be degenerate, that is a finding to report,
   not something to work around
5. a fresh `lstm_rtl` baseline is recorded on this server, with its own numbers
6. at least one full co-optimization iteration runs end to end and its
   accept/reject decision is backed by evidence on disk

No claim that the loop works is made before (6).

## 16. Known risks and open questions

- **`in_dim` mismatch.** The baremetal program passes `in_dim = 6000`, while
  `lstm.v` declares `INPUT_DEPTH 100` and `INWEIGHT_DEPTH 6400 (100x64)`. Whether
  6000 is 60 timesteps of 100, or a software/hardware mismatch, is unresolved. It
  must be settled before the golden is trusted — a degenerate baseline output
  would likely be the first symptom.
- **Toolchain drift.** The cross compiler here is GCC 8.3.0 built from
  riscv-gnu-toolchain `afcc8bc`. The old server's version is unknown, which is one
  more reason no old measurement is reused.
- **Golden strength.** Baseline characterization cannot catch an error already
  present in the baseline, and only covers the states the fixture exercises.
- **Clamped requirements.** When a requested knob value is clamped, the RTL change
  runs under a SoC that does not fully satisfy it. The clamp is recorded, but the
  loop still scores the result; the reflection prompt must be able to see this.
- **The legality table is ours, not ESP's.** ESP exposes no queryable legality
  constraints for `.esp_config`, so §9's table encodes our understanding. A value
  it admits can still produce cache RTL that does not compile — the report saw
  exactly this, including a negative replication multiplier in
  `l2_localmem_asic.sv`. Treat generation failures as evidence that the table is
  wrong, and correct it rather than routing around it.

## 17. Future work

- **Formal verification**, introduced in the second half of the project as a
  stronger backend behind the same `goldengen` interface, replacing or
  supplementing baseline characterization.
- An independent fixed-point reference model for LSTM.
- Extending to the AES accelerators once the loop is proven on LSTM.
- Revisiting whether SoC knobs can be *narrowed* once area or timing enters the
  objective.
