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
- repairing that accelerator's software/hardware buffer contract (§4.2), which
  must precede everything else
- a new `coopt_agent` package implementing the unified loop
- a new standalone `goldengen` package implementing the correctness gate
- rebuilding the baremetal harness for `lstm_rtl`
- a fresh baseline measured on the current server, after the repair

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

## 4. The baseline cannot be trusted as restored

Two independent problems, both found by reading the restored sources. Together
they mean the first task of this project is repairing the baseline, not
optimizing it.

### 4.1 The correctness check is vacuous

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

### 4.2 The software and hardware disagree about the buffer

Reading `hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v` against `lstm.c`
turns up three concrete defects:

1. **The configuration registers are ignored.** `conf_info_in_dim`,
   `conf_info_hidden_dim` and `conf_info_num_timesteps` appear only in the module
   port list; the wrapper body never reads them. The accelerator's dimensions are
   fixed by the `ARRAY_DEPTH 64` / `INPUT_DEPTH 100` defines in `lstm.v`, so the
   `in_dim = 6000` the software writes has no effect on the hardware.
2. **The output is written where the software does not look.** The wrapper sets
   `dma_write_ctrl_data_index <= 32'd0`, writing the hidden state to the start of
   the buffer, while `lstm.c` reads its results from `&mem[out_offset]` with
   `out_offset = in_len` (about 6000 words in). The software therefore reads
   leftover input, never the accelerator's output.
3. **The input read overruns the allocation.** `TOTAL_BEATS` is
   `BEATS_ALL + BEATS_X` = `42 * 64 + 25` = 2713 beats of 8 bytes = 21,704 bytes,
   issued as one DMA read from index 0. `lstm.c` allocates
   `mem_size = 6000 * 2 + 64 * 2` = 12,128 bytes. The accelerator reads roughly
   9.5 KB past the buffer.

A fourth observation bounds what "baseline" can mean here. The wrapper carries
`dma_write_ctrl_data_length <= 32'd4096;` with the original `ARRAY_DEPTH` line
commented out beside it and a `// 64 beats` comment that contradicts the value,
plus `// << CHG` markers on two FSM transitions. These are not artifacts of the
previous project: the backup repository's later commit only added the
`dma_*_ctrl_data_user` ports and commented out a `SELECT_REG` write, so the edits
arrived with the original SLDB import. **There is no pristine version to revert
to.** The restored state is the baseline, and the spec records its provenance
rather than pretending it is clean.

Defect 2 alone makes a golden meaningless — it would capture leftover input. §15
therefore gates the whole project on repairing the software/hardware contract
before any golden is generated.

Note also that the weights never move through DMA: `lstm_rest.v` loads all twelve
`.mem` files into on-chip SPRAMs with `$readmemh` at lines 25, 70 and 119. The
only DMA traffic is one 21,704-byte read and one write. This is why §9 can
exclude the accelerator cache knob at no cost, and why the report's "reuse
persistent local weight buffers across invocations" optimization target does not
apply to this accelerator at all.

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
legality checking**.

`coopt_agent` adds that check, and **mirrors ESP's own constraints rather than
inventing them**. ESP's configuration GUI enumerates the legal values, and those
lists are the authority:

| Knob | Legal values | Source |
|---|---|---|
| `CONFIG_QUEUE_SIZE` | 2 – 17 | `tools/socgen/NoCConfiguration.py:802` |
| `CONFIG_COH_NOC_WIDTH` | 32, 64, 128, 256, 512, 1024 | `tools/socgen/NoCConfiguration.py:681` |
| `CONFIG_DMA_NOC_WIDTH` | 32, 64, 128, 256, 512, 1024 | same |
| `CONFIG_MEM_LINK_WIDTH` | 32, 64, 128, 256, 512 — **no 1024** | `tools/socgen/esp_creator.py:306` |
| `CONFIG_SLM_KBYTES` | 64, 128, 256, 512, 1024, 2048, 4096 | `tools/socgen/esp_creator.py:224` |

A unit test asserts that this table still matches those source lists, so upstream
drift is caught rather than silently tolerated.

Mirroring pays off immediately: the report records a proposal setting
`CONFIG_QUEUE_SIZE` to 64, which is **above the legal maximum of 17**. Some of the
old SoC loop's unexplained failures were very likely illegal values it had no way
to recognize. The asymmetry between NoC width (up to 1024) and memory link width
(up to 512) is the kind of detail a hand-written table gets wrong.

Per-knob ranges are necessary but not sufficient: ESP also enforces cross-field
constraints — `NoCConfiguration.py:924` and `:995` reject a configuration where
more than one SLM tile is present and `CONFIG_SLM_KBYTES` is below 1024. A value
this table admits can still make ESP generate cache RTL that does not compile,
which is a failure mode the report observed. Treat a generation failure as
evidence that the table is incomplete, and extend it.

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
failure mode of §4.1 rebuilt.

1. an output block is present for every invocation
2. each vector's length is exactly `hidden_dim * num_timesteps` (64 for the
   current fixture)
3. not all elements are zero
4. not all elements are identical — a constant vector is as empty as a zero one
5. all invocations produce identical vectors

`check` compares elementwise against the golden and additionally re-asserts (5).

Rule 5 is not ceremony, though its justification is narrower than the report's
framing suggests. "Reuse persistent local weight buffers across invocations" is
one of the report's RTL optimization targets, but it does not apply here: LSTM's
weights are `$readmemh`-loaded into on-chip SPRAMs and never move through DMA
(§4.2). What rule 5 does protect is the wrapper's own per-run state — `buf_u`,
`buf_v` and `buf_b` are filled from DMA on every invocation, and the FSM's reset
path is directly in the edit surface, since removing bubble states is an
explicit optimization target. A patch that drops a reset or lets a buffer carry
over produces a correct invocation 0 and wrong invocations 1 and 2, which a
single-invocation golden cannot see.

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

1. **the software/hardware contract of §4.2 is repaired**, so that the software
   reads the accelerator's output and the accelerator's DMA read stays inside the
   allocation. Until this holds, nothing downstream means anything; this precedes
   all agent code.
2. all unit tests pass
3. `make lstm_rtl-baremetal` compiles with the rebuilt harness on this server
4. a ModelSim run completes and the transcript contains parseable monitor stats,
   cycle counts and three output blocks
5. `goldengen generate` produces a golden that survives the degeneracy check —
   if the baseline output turns out to be degenerate, that is a finding to report,
   not something to work around
6. a fresh `lstm_rtl` baseline is recorded on this server, with its own numbers
7. at least one full co-optimization iteration runs end to end and its
   accept/reject decision is backed by evidence on disk

No claim that the loop works is made before (7).

Item 1 changes the shape of the work: the repair touches the baseline the
optimizer is measured against, so it must be finished, committed and measured
before the first candidate is ever proposed. Whether the fix belongs in the
software (read from index 0, allocate 21,704 bytes) or in the wrapper (write at
`out_offset`, derive lengths from the configuration registers) is an
implementation decision for the plan, but it must be made deliberately and
recorded — changing the wrapper changes the thing being optimized.

## 16. Known risks and open questions

- **A repaired baseline is not the reported baseline.** The `in_dim` question is
  resolved in §4.2 — the configuration registers are dead and the buffer contract
  is broken — but resolving it means the baseline we measure will not be the one
  the report measured. The report's 2,049,922 end-to-end cycles are not a target
  to reproduce, and the spec does not treat them as one.
- **The repair could mask or create a bottleneck.** Fixing the DMA read length or
  the write index changes exactly the traffic the optimizer is meant to improve.
  The baseline must be re-measured after the repair, and the repair itself must
  not be scored as an optimization.
- **Toolchain drift.** The cross compiler here is GCC 8.3.0 built from
  riscv-gnu-toolchain `afcc8bc`. The old server's version is unknown, which is one
  more reason no old measurement is reused.
- **Golden strength.** Baseline characterization cannot catch an error already
  present in the baseline, and only covers the states the fixture exercises.
- **Clamped requirements.** When a requested knob value is clamped, the RTL change
  runs under a SoC that does not fully satisfy it. The clamp is recorded, but the
  loop still scores the result; the reflection prompt must be able to see this.
- **The legality table is mirrored, not complete.** §9's table is copied from
  ESP's own GUI choice lists and tested against them, so per-knob ranges are
  authoritative. Cross-field constraints are not covered, and a legal combination
  can still produce cache RTL that does not compile — the report saw exactly this,
  including a negative replication multiplier in `l2_localmem_asic.sv`. Treat
  generation failures as evidence the table is incomplete, and extend it rather
  than routing around it.

## 17. Future work

- **Formal verification**, introduced in the second half of the project as a
  stronger backend behind the same `goldengen` interface, replacing or
  supplementing baseline characterization.
- An independent fixed-point reference model for LSTM.
- Extending to the AES accelerators once the loop is proven on LSTM.
- Revisiting whether SoC knobs can be *narrowed* once area or timing enters the
  objective.
