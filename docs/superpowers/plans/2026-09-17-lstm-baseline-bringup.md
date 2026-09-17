# LSTM Baseline Bring-Up and Repair — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a trustworthy, measured `lstm_rtl` baseline on this server — the ESP flow running end to end, the software/hardware buffer contract repaired on the software side, the harness instrumented, and the baseline output captured and proven non-degenerate.

**Architecture:** No new packages. This plan touches ESP configuration and one C file, `accelerators/rtl/lstm_rtl/sw/baremetal/lstm.c`. The wrapper `lstm_rtl_basic_dma64.v` must end this plan byte-for-byte identical to its current state — that is the point of the repair being software-side (spec §15).

**Tech Stack:** ESP 2026.1.0, ModelSim DE 2023.2, riscv64-unknown-elf-gcc 8.3.0 (`/home/pd2827/riscv`), Ariane CPU, `xilinx-vc707-xc7vx485t` SoC.

**Spec:** `docs/superpowers/specs/2026-09-17-esp-soc-rtl-cooptimization-design.md`

## Global Constraints

- Branch: `coopt` in `/home/pd2827/esp`. Push to remote `fork` after each task.
- Every ESP command runs through a login shell that sources the CAD env:
  `bash -lc 'source /opt/cad/scripts/tools_env.sh && <command>'`. Both
  `riscv64-unknown-elf-gcc` and `vsim` resolve there; verified 2026-09-17.
- SoC directory for all `make` invocations:
  `/home/pd2827/esp/socs/xilinx-vc707-xc7vx485t`.
- Coherence mode for every measured run: `ACC_COH_LLC` (spec §11).
- Three accelerator invocations per run (spec §11).
- `accelerators/rtl/lstm_rtl/hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v`
  is **read-only for the whole of this plan**. Task 6 enforces this.
- Simulations are slow. Always run them with `run_in_background` and a generous
  timeout; never poll with short sleeps.

## Hardware facts this plan depends on

Established by reading the sources; do not re-derive, but do re-check if a step
behaves unexpectedly.

| Fact | Value | Source |
|---|---|---|
| DMA read, one request from index 0 | `TOTAL_BEATS` = 2713 beats = 21,704 bytes | `lstm_rtl_basic_dma64.v:45-51,176` |
| Read layout | 64 rows of (16 beats `buf_u`, 25 beats `buf_v`, 1 beat `buf_b`), then 25 beats `buf_x` | FSM `S_LOAD_U/V/B`, lines 185-217 |
| DMA write index | `32'd0` — output lands at the start of the buffer | line 286 |
| DMA write length field | `32'd4096`, with `ARRAY_DEPTH` (64) commented out beside it | lines 287-288 |
| Output actually produced | 64 values, one 16-bit `lstm_ht_out` per beat, zero-padded to 64 bits | `S_WR_STREAM` |
| Config registers | `conf_info_in_dim/hidden_dim/num_timesteps` are declared and never read | ports only |
| Weights | arrive over DMA, not `$readmemh` (that is inside a disabled `ifdef`) | spec §4.3 |
| Baremetal exe name | `soft-build/ariane/baremetal/lstm_rtl.exe` (from `APPNAME := lstm`) | `sw/baremetal/Makefile`, `accelerators.mk:499` |

**Known risk, expect it in Task 3.** The write length field says 4096 beats while
the accelerator sends 64. If ESP's DMA engine waits for the full count, the write
transaction never completes and the simulation hangs after `Start...`. If that
happens, **stop and report** — it means the software-only constraint of spec §15
cannot hold and the decision must go back to the project owner. Do not "fix" it
by editing the wrapper.

---

### Task 1: Install lstm_rtl into tech/ and build a baseline SoC config

**Files:**
- Create: `coopt_agent/configs/baseline_lstm_rtl.esp_config` (tracked canonical copy)
- Generated (gitignored, installed from the above): `socs/xilinx-vc707-xc7vx485t/socgen/esp/.esp_config`
- Create: `docs/superpowers/evidence/task1-socmap-check.txt`
- Generated (gitignored): `tech/virtex7/acc/lstm_rtl/`

**Interfaces:**
- Consumes: nothing.
- Produces: a `.esp_config` whose accelerator tile is `lstm_rtl`, with
  `CONFIG_CACHE_EN = y` and `CONFIG_MON_ACCELERATORS = y`. Every later task and
  the agent's baseline depend on this file.

- [ ] **Step 1: Install the RTL accelerator into the tech library**

`tools/socgen/soc.py` discovers accelerators by scanning `tech/<tech>/acc`.
`lstm_rtl` is not there yet — only `lstm_v2_rtl` is.

```bash
bash -lc 'source /opt/cad/scripts/tools_env.sh && cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t && make lstm_rtl-hls'
```

Expected: a new directory `tech/virtex7/acc/lstm_rtl/` containing
`lstm_rtl.xml` and at least one `lstm_rtl_basic_dma64/` implementation
directory, and `lstm_rtl` appended to `tech/virtex7/acc/installed.log`.

- [ ] **Step 2: Verify the install**

```bash
ls /home/pd2827/esp/tech/virtex7/acc/lstm_rtl/
grep -c lstm_rtl /home/pd2827/esp/tech/virtex7/acc/installed.log
```

Expected: the xml and an implementation dir are present; the grep count is at
least 1. If the directory is missing, the generator failed — read its output
before doing anything else.

- [ ] **Step 3: Create the default configuration**

```bash
bash -lc 'source /opt/cad/scripts/tools_env.sh && cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t && make esp-defconfig'
```

Expected: `socgen/esp/.esp_config` now exists. This target copies
`socs/defconfig/esp_xilinx-vc707-xc7vx485t_defconfig` and immediately runs
`esp-config`, so it may fail at the config step — that is fine at this point,
the file is what matters.

- [ ] **Step 4: Edit the configuration for this project**

Three changes to `socs/xilinx-vc707-xc7vx485t/socgen/esp/.esp_config`. The
defconfig is a 2x2 SoC with no accelerator tile, caches off and accelerator
monitors off; all three would make the rest of this plan meaningless.

```bash
cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t/socgen/esp
cp .esp_config .esp_config.orig

# 1. Put the accelerator in the empty tile.
#    soc.py:456-478 writes an accelerator tile as
#      TILE_<y>_<x> = <n> acc <NAME> <point> <has_l2> <has_tdvfs> <vendor>
#    <point> is the implementation directory name with the "<acc>_" prefix
#    stripped, so lstm_rtl_basic_dma64 -> basic_dma64 (SoC_Config.__init__).
#    has_l2 = 0: see the controller's ruling recorded in the ledger -- at
#    ACC_COH_LLC the accelerator's private L2 is bypassed, and full coherence
#    is out of scope, so an accelerator L2 would be simulated but never used.
sed -i 's|^TILE_1_0 = 2 empty empty$|TILE_1_0 = 2 acc LSTM_RTL basic_dma64 0 0 sld|' .esp_config

# 2. Enable caches. ACC_COH_LLC needs the LLC, and CONFIG_ACC_CACHES is inert
#    without this (spec section 9).
sed -i 's|^#CONFIG_CACHE_EN is not set$|CONFIG_CACHE_EN = y|' .esp_config

# 3. Enable accelerator monitors. The whole evaluation signal comes from them.
sed -i 's|^#CONFIG_MON_ACCELERATORS is not set$|CONFIG_MON_ACCELERATORS = y|' .esp_config

diff .esp_config.orig .esp_config
```

Expected diff: exactly those three lines changed, nothing else.

**If a `sed` does not match**, the defconfig differs from what this plan
recorded. Print the relevant line and adapt — do not force it. In particular the
accelerator tile syntax must match what `soc.py` parses; check an existing SoC's
`.esp_config` under `socs/` for the exact token order before guessing.

- [ ] **Step 5: Regenerate the SoC**

```bash
bash -lc 'source /opt/cad/scripts/tools_env.sh && cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t && make esp-config' 2>&1 | tail -30
```

Expected: the run ends with the three markers
`Created global constants definition into 'esp_global.vhd'`,
`Created configuration into 'socmap.vhd'` and
`Created configuration into 'mmi64_regs.h'`.

Note: a nonzero exit accompanied by `/usr/bin/xvfb-run: line 186: kill:` while
all three markers are present is a known benign teardown failure —
`soc_opt_agent/src/soc_opt_agent/esp_flow/executor.py` already special-cases it.

- [ ] **Step 6: Verify the accelerator really landed in the SoC**

```bash
cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t
grep -i "lstm" socgen/esp/socmap.vhd | head -20 | tee /home/pd2827/esp/docs/superpowers/evidence/task1-socmap-check.txt
```

Expected: `socmap.vhd` mentions the LSTM accelerator. An empty result means the
tile edit did not take effect and every later task would silently test the wrong
SoC — the exact "retargeting hazard" the report describes. Do not continue past
an empty result.

- [ ] **Step 7: Commit**

`socs/.gitignore:44` ignores `socgen`, so the live `.esp_config` cannot be
committed where it sits. Keep the canonical copy in a tracked location instead —
the spec's §9 derivation needs the baseline config as a durable artifact anyway.

```bash
cd /home/pd2827/esp
mkdir -p docs/superpowers/evidence coopt_agent/configs
cp socs/xilinx-vc707-xc7vx485t/socgen/esp/.esp_config \
   coopt_agent/configs/baseline_lstm_rtl.esp_config
git add coopt_agent/configs/baseline_lstm_rtl.esp_config docs/superpowers/evidence/task1-socmap-check.txt
git commit -m "lstm baseline: SoC config with lstm_rtl tile, caches and monitors on

The vc707 defconfig is a 2x2 SoC with no accelerator tile, CONFIG_CACHE_EN
unset and CONFIG_MON_ACCELERATORS unset. All three had to change: without an
accelerator tile there is nothing to measure, without caches ACC_COH_LLC is
meaningless and CONFIG_ACC_CACHES is inert, and without monitors there is no
evaluation signal at all.

Co-Authored-By: <the attribution line your own session was given>"
git push fork coopt
```

---

### Task 2: First simulation with the software untouched

The point of this task is to prove the toolchain and simulation flow work
before changing any code. The program will print a meaningless `PASS` (spec
§4.1); that is expected and is not evidence of anything.

**Files:**
- Create: `docs/superpowers/evidence/task2-stock-transcript.txt`

**Interfaces:**
- Consumes: the `.esp_config` from Task 1.
- Produces: a known-good transcript, and confirmation that `make sim` completes.

- [ ] **Step 1: Build the baremetal program**

```bash
bash -lc 'source /opt/cad/scripts/tools_env.sh && cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t && make lstm_rtl-baremetal' 2>&1 | tail -20
```

Expected: exit 0 and `soft-build/ariane/baremetal/lstm_rtl.exe` exists. This is
the first real use of the cross compiler built on 2026-09-17; if it fails on a
missing `riscv64-unknown-elf-gcc`, the CAD env was not sourced.

- [ ] **Step 2: Verify the executable**

```bash
ls -la /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t/soft-build/ariane/baremetal/lstm_rtl.exe
file /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t/soft-build/ariane/baremetal/lstm_rtl.exe
```

Expected: an `ELF 64-bit LSB executable, UCB RISC-V`.

- [ ] **Step 3a: Reconstruct the lost `vsim.tcl`**

`utils/make/modelsim.mk:263-269` runs `vsim -c -do "do $(DESIGN_PATH)/vsim.tcl"`
only when that file exists, and falls back to a bare interactive `vsim -c`
otherwise. Only the three ASIC SoCs ship one; every FPGA SoC, this one included,
takes the fallback — which waits for a human to type `run -all` and, launched
detached, reads EOF and exits after elaboration without ever running.

The old server had this file: a surviving transcript in the backup repository
shows `-do "do /home/cz2931/esp/socs/xilinx-vc707-xc7vx485t/vsim.tcl"` on the
vsim command line. The file itself was never backed up, so reconstruct it.

Create `socs/xilinx-vc707-xc7vx485t/vsim.tcl`:

```tcl
# Drive an unattended batch simulation.
#
# utils/make/modelsim.mk sources this after `vsim` has already elaborated the
# design, so the design is loaded and only the run is missing. ESP's testbench
# ends the run itself: top.vhd asserts `Failure: Program Completed!`, which
# stops the simulation and makes `run -all` return. That assertion is why a
# successful run reports `Errors: 1` -- it is the completion signal, not a fault.
#
# `quit -f` is explicit rather than relying on vsim reaching EOF on stdin: that
# implicit exit is exactly what made the unattended run look like a hang.
run -all
quit -f
```

Verify it is picked up — the vsim command line in the transcript must contain
`-do "do .../vsim.tcl"`. If it does not, `DESIGN_PATH` is not what this step
assumes (`socs/xilinx-vc707-xc7vx485t/Makefile:10` sets it to `$(PWD)`).

- [ ] **Step 3: Run the simulation in the background**

This takes a long time. Launch it detached and wait for completion rather than
polling.

```bash
bash -lc 'source /opt/cad/scripts/tools_env.sh && cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t && make sim TEST_PROGRAM=./soft-build/ariane/baremetal/lstm_rtl.exe' > /tmp/sim_task2.log 2>&1
```

- [ ] **Step 4: Inspect the transcript**

```bash
cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t
cp modelsim/transcript /home/pd2827/esp/docs/superpowers/evidence/task2-stock-transcript.txt
grep -nE "Scanning device tree|lstm not found|Start\.\.\.|Done|PASS|FAIL|ESP MONITOR" modelsim/transcript | head -30
```

**Budget about an hour.** A surviving old-server transcript for this accelerator
reports `Elapsed time: 0:53:14` for a single invocation, and the instrumented
harness of Task 4 will make three. Do not mistake a long run for a hang.

Expected, in order: the device-tree scan, `**************** sld,lstm_rtl.0 ****************`,
`Start...`, `Done`, `... PASS`.

Three specific failures to distinguish, because they need different responses:

- `lstm not found` — the accelerator is not in the SoC. Go back to Task 1
  Step 6; the tile edit did not take.
- The transcript stops after `Start...` and the simulation never returns —
  this is the `dma_write_ctrl_data_length = 4096` risk from the header. **Stop
  and report it.** It cannot be fixed within this plan's software-only
  constraint.
- `PASS` appears — good, and remember it means nothing about correctness.

- [ ] **Step 5: Commit the evidence**

```bash
cd /home/pd2827/esp
git add docs/superpowers/evidence/task2-stock-transcript.txt
git commit -m "lstm baseline: first end-to-end simulation on this server

Stock software, unmodified wrapper. Proves the rebuilt toolchain, the SoC
config and ModelSim work together. The PASS in this transcript is vacuous --
validate_buf's comparison is commented out -- and is recorded only as proof
that the flow completes.

Co-Authored-By: <the attribution line your own session was given>"
git push fork coopt
```

---

### Task 3: Repair the buffer contract and print the output vector

**Files:**
- Modify: `accelerators/rtl/lstm_rtl/sw/baremetal/lstm.c`
- Create: `docs/superpowers/evidence/task3-repaired-transcript.txt`

**Interfaces:**
- Consumes: Task 1's config, Task 2's working flow.
- Produces: the `ESP_OUT_BEGIN … ESP_OUT_END` transcript block that goldengen
  will parse in a later plan. Its exact format is fixed here:

```
ESP_OUT_BEGIN inv=<n> len=64
0x0123
...          (64 lines, one 16-bit value each, lowercase hex, zero-padded to 4)
ESP_OUT_END
```

- [ ] **Step 1: Replace the buffer geometry**

The accelerator ignores the config registers and has fixed geometry, so the
software must match the hardware rather than the other way round. Replace the
parameter block and the size computation in `lstm.c`.

Replace:

```c
/* <<--params-->> */
const int32_t in_dim = 6000;
const int32_t hidden_dim = 64;
const int32_t num_timesteps = 1;
```

with:

```c
/* <<--params-->> */
/* The wrapper ignores conf_info_in_dim / hidden_dim / num_timesteps: they are
 * declared as ports and never read. Its geometry is fixed by the defines in
 * lstm.v (ARRAY_DEPTH 64, INPUT_DEPTH 100) and the localparams in
 * lstm_rtl_basic_dma64.v. These constants mirror that hardware; they are not
 * free parameters. See spec sections 4.2 and 4.3.
 *
 *   one DMA read of TOTAL_BEATS = BEATS_ALL + BEATS_X
 *     BEATS_ALL = (BEATS_U + BEATS_V + BEATS_B) * ARRAY_DEPTH
 *               = (16 + 25 + 1) * 64 = 2688   weights and biases
 *     BEATS_X   = 25                          the input vector
 *   one DMA write of 64 values, at index 0, overwriting the start of the buffer
 */
#define LSTM_BEATS_U      16
#define LSTM_BEATS_V      25
#define LSTM_BEATS_B      1
#define LSTM_ARRAY_DEPTH  64
#define LSTM_BEATS_ROW    (LSTM_BEATS_U + LSTM_BEATS_V + LSTM_BEATS_B)
#define LSTM_BEATS_ALL    (LSTM_BEATS_ROW * LSTM_ARRAY_DEPTH)
#define LSTM_BEATS_X      LSTM_BEATS_V
#define LSTM_TOTAL_BEATS  (LSTM_BEATS_ALL + LSTM_BEATS_X)
#define LSTM_BEAT_BYTES   8
#define LSTM_OUT_VALUES   LSTM_ARRAY_DEPTH

const int32_t hidden_dim = LSTM_ARRAY_DEPTH;
const int32_t num_timesteps = 1;
```

- [ ] **Step 2: Replace the size computation in `main`**

Replace the whole block from `if (DMA_WORD_PER_BEAT(sizeof(token_t)) == 0) {`
down to and including the `mem_size = ...` line with:

```c
	/* The accelerator reads LSTM_TOTAL_BEATS beats from index 0 and writes its
	 * 64 outputs back to index 0. One buffer serves both; the read completes
	 * before the write begins, so the overlap is safe. Sizing to the read is
	 * what keeps the DMA inside the allocation. */
	in_words_adj  = (LSTM_TOTAL_BEATS * LSTM_BEAT_BYTES) / sizeof(token_t);
	out_words_adj = LSTM_OUT_VALUES;
	in_len        = in_words_adj;
	out_len       = out_words_adj;
	in_size       = in_len * sizeof(token_t);
	out_size      = out_len * sizeof(token_t);
	out_offset    = 0;                       /* the wrapper writes at index 0 */
	mem_size      = in_size;                 /* >= 21704 bytes, covers both */
```

- [ ] **Step 3: Fill the whole buffer deterministically**

Replace `init_buf` with:

```c
static void init_buf (token_t *in)
{
	unsigned i;

	/* Most of this buffer is interpreted as weights (spec 4.3), so a ramp is
	 * not a trained model -- it only has to be deterministic, which is all
	 * baseline characterization needs. If the resulting hidden state turns out
	 * degenerate, the answer is a better fixture, not a weaker check. */
	for (i = 0; i < in_len; i++)
		in[i] = (token_t) (i & 0x7fff);
}
```

The `gold` parameter is dropped here rather than kept and ignored, because Step 5
deletes the `gold` local in `main` and Task 4 calls this function again. Update
the existing call in `main` to `init_buf(mem);` in this step.

- [ ] **Step 4: Print the output vector**

Add this function above `main`:

```c
static void dump_out(token_t *out, unsigned inv)
{
	unsigned i;

	printf("ESP_OUT_BEGIN inv=%u len=%u\n", inv, LSTM_OUT_VALUES);
	for (i = 0; i < LSTM_OUT_VALUES; i++)
		printf("0x%04x\n", (unsigned) ((unsigned short) out[i]));
	printf("ESP_OUT_END\n");
}
```

Then replace the validation block:

```c
			printf("  Done\n");
			printf("  validating...\n");

			/* Validation */
			errors = validate_buf(&mem[out_offset], gold);
			if (errors)
				printf("  ... FAIL\n");
			else
				printf("  ... PASS\n");
```

with:

```c
			printf("  Done\n");

			/* No in-program validation: validate_buf's comparison is commented
			 * out upstream and there is no reference model, so any PASS it
			 * printed was unconditional. Correctness is decided off-chip by
			 * goldengen against this dumped vector. See spec section 4.1. */
			dump_out(&mem[out_offset], 0);
```

Leave the `errors` and `gold` locals alone for now; Step 5 removes them together
with the function that used them.

- [ ] **Step 5: Delete the code that is now unused**

Steps 2 and 4 removed the only callers of `validate_buf` and
`DMA_WORD_PER_BEAT`. An unused `static` function is a warning, and a warning is a
build failure anywhere `-Werror` is in effect. Delete both definitions outright:

```c
static unsigned DMA_WORD_PER_BEAT(unsigned _st)
{
        return (sizeof(void *) / _st);
}
```

and

```c
static int validate_buf(token_t *out, token_t *gold)
{
	int i;
	int j;
	unsigned errors = 0;

	// for (i = 0; i < num_timesteps; i++)
	// 	for (j = 0; j < hidden_dim; j++)
	// 		if (gold[i * out_words_adj + j] != out[i * out_words_adj + j])
	// 			errors++;

	return errors;
}
```

Deleting `validate_buf` rather than leaving it is the point of spec §4.1: a
function that always returns success should not survive in the tree looking like
a check. Also drop the now-unused `errors` and `gold` locals in `main`, and the
`gold = aligned_malloc(out_size);` / `aligned_free(gold);` pair.

- [ ] **Step 6: Set the coherence mode**

Replace:

```c
			/* TODO: Restore full test once ESP caches are integrated */
			coherence = ACC_COH_NONE;
```

with:

```c
			/* Caches are enabled in this project's .esp_config, so the LLC path
			 * is available. Full coherence is too slow to simulate in a loop
			 * (spec section 9). */
			coherence = ACC_COH_LLC;
```

- [ ] **Step 7: Rebuild and run**

```bash
bash -lc 'source /opt/cad/scripts/tools_env.sh && cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t && make lstm_rtl-baremetal' 2>&1 | tail -20
```
Expected: exit 0. Then, in the background:
```bash
bash -lc 'source /opt/cad/scripts/tools_env.sh && cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t && make sim TEST_PROGRAM=./soft-build/ariane/baremetal/lstm_rtl.exe' > /tmp/sim_task3.log 2>&1
```

- [ ] **Step 8: Check the output for degeneracy — the real gate of this task**

```bash
cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t
sed -n '/ESP_OUT_BEGIN/,/ESP_OUT_END/p' modelsim/transcript > /tmp/out_task3.txt
wc -l /tmp/out_task3.txt                                    # expect 66
grep -c . /tmp/out_task3.txt
sed '1d;$d' /tmp/out_task3.txt | sort -u | wc -l             # distinct values
sed '1d;$d' /tmp/out_task3.txt | sort -u | head
```

Expected: 66 lines; 64 values; **more than one distinct value, and not all
`0x0000`**.

If the vector is all zeros, constant, or absent, that is spec §15 item 5 firing:
**report it as a finding and stop.** The likely cause is that a ramp makes a poor
weight matrix and the fixed-point datapath saturates or collapses. The response
is a better fixture — for example pseudo-random small-magnitude values — not a
weaker check. Bring it back to the project owner with the actual numbers.

- [ ] **Step 9: Commit**

```bash
cd /home/pd2827/esp
cp socs/xilinx-vc707-xc7vx485t/modelsim/transcript docs/superpowers/evidence/task3-repaired-transcript.txt
git add accelerators/rtl/lstm_rtl/sw/baremetal/lstm.c docs/superpowers/evidence/task3-repaired-transcript.txt
git commit -m "lstm baseline: repair the software/hardware buffer contract

The software allocated 12,128 bytes while the wrapper reads 21,704 from index
0, and read its results from &mem[out_offset] about 6000 words in while the
wrapper writes at index 0. The software therefore overran its buffer and never
saw the accelerator's output.

Repaired entirely on the software side, per spec section 15: lstm.c now mirrors
the hardware's fixed geometry, allocates for the full DMA read, and reads the
output where the wrapper actually puts it. lstm_rtl_basic_dma64.v is untouched,
so the baseline stays the current trusted RTL.

Also dumps the output vector for off-chip checking and drops the unconditional
PASS, and switches to ACC_COH_LLC now that caches are enabled.

Co-Authored-By: <the attribution line your own session was given>"
git push fork coopt
```

---

### Task 4: Instrument the harness

**Files:**
- Modify: `accelerators/rtl/lstm_rtl/sw/baremetal/lstm.c`
- Reference: `soft/common/apps/baremetal/fft_monitors/fft_monitors.c`
- Create: `docs/superpowers/evidence/task4-instrumented-transcript.txt`

**Interfaces:**
- Consumes: Task 3's repaired `lstm.c`.
- Produces: per-invocation monitor statistics, per-invocation cycle counts, an
  end-to-end cycle count, and three `ESP_OUT` blocks with `inv=0,1,2`.

- [ ] **Step 1: Add the include and the cycle counter helper**

`monitors.h` provides the accelerator-side counters — `acc_tot_lo`/`acc_tot_hi`
are the report's "accelerator total cycles" and `acc_mem_lo`/`acc_mem_hi` the
accelerator memory cycles. It does not provide a CPU-side cycle count, so read
`rdcycle` directly. Add near the top of `lstm.c`:

```c
#include <monitors.h>

static inline uint64_t esp_get_cycles(void)
{
#ifdef __riscv
	uint64_t c;
	__asm__ volatile ("rdcycle %0" : "=r" (c));
	return c;
#else
	return 0;
#endif
}
```

- [ ] **Step 2: Restructure the invocation into a loop of three**

Three invocations separate first-run effects from steady state, and make spec
§10 rule 5 checkable — the load-bearing check for this accelerator, because the
biggest available cycle win is skipping the redundant weight load and a wrong
skip only shows up after the first run.

The invocation sits inside the `#ifndef __riscv` / `#else` block that Task 3
Step 6 edited; under `__riscv` that is a bare `{ coherence = ACC_COH_LLC; ... }`.
Replace everything inside that block **after** the `coherence = ACC_COH_LLC;`
assignment — i.e. from `printf("  --------------------\n");` down to and
including the `dump_out(...)` call Task 3 added — with:

```c
		esp_monitor_args_t mon_args;
		esp_monitor_vals_t vals_start, vals_end, vals_diff;
		uint64_t e2e_start, e2e_end, inv_start, inv_end;
		unsigned inv;

		mon_args.read_mode = ESP_MON_READ_ALL;

		e2e_start = esp_get_cycles();

		for (inv = 0; inv < 3; inv++) {

			printf("  -------- invocation %u --------\n", inv);
			/* Re-initialize every time: the accelerator writes its output over
			 * the start of this same buffer, so invocation n+1 would otherwise
			 * see invocation n's results as input. */
			init_buf(mem);

			iowrite32(dev, COHERENCE_REG, coherence);
#ifndef __sparc
			iowrite32(dev, PT_ADDRESS_REG, (unsigned long long) ptable);
#else
			iowrite32(dev, PT_ADDRESS_REG, (unsigned) ptable);
#endif
			iowrite32(dev, PT_NCHUNK_REG, NCHUNK(mem_size));
			iowrite32(dev, PT_SHIFT_REG, CHUNK_SHIFT);
			iowrite32(dev, SRC_OFFSET_REG, 0x0);
			iowrite32(dev, DST_OFFSET_REG, 0x0);
			iowrite32(dev, LSTM_IN_DIM_REG, in_words_adj);
			iowrite32(dev, LSTM_HIDDEN_DIM_REG, hidden_dim);
			iowrite32(dev, LSTM_NUM_TIMESTEPS_REG, num_timesteps);

			esp_monitor(mon_args, &vals_start);
			esp_flush(coherence);

			inv_start = esp_get_cycles();
			printf("  Start...\n");
			iowrite32(dev, CMD_REG, CMD_MASK_START);

			done = 0;
			while (!done) {
				done = ioread32(dev, STATUS_REG);
				done &= STATUS_MASK_DONE;
			}
			iowrite32(dev, CMD_REG, 0x0);
			inv_end = esp_get_cycles();

			esp_monitor(mon_args, &vals_end);
			vals_diff = esp_monitor_diff(vals_start, vals_end);

			printf("  Done\n");
			printf("ESP_INV_CYCLES inv=%u %llu\n", inv,
			       (unsigned long long) (inv_end - inv_start));

			dump_out(&mem[out_offset], inv);
			esp_monitor_print(mon_args, vals_diff);
		}

		e2e_end = esp_get_cycles();
		printf("ESP_E2E_CYCLES %llu\n",
		       (unsigned long long) (e2e_end - e2e_start));
```

Note the CSR writes are kept even though §4.2 established the wrapper ignores
them. They cost nothing, they keep the program honest about what it intends, and
if a later patch ever wires those ports up the software will already be
supplying sane values.

- [ ] **Step 3: Confirm the monitor API compiles as used**

The struct is `{read_mode, read_mask, tile_index, acc_index, mon_index,
noc_index}` and `ESP_MON_READ_ALL` selects everything, matching
`fft_monitors.c:197-201`. If the build complains about a missing include path,
check how `soft/common/apps/baremetal/fft_monitors/Makefile` pulls in the
monitors driver and mirror it in
`accelerators/rtl/lstm_rtl/sw/baremetal/Makefile`, which currently contains only
`APPNAME := lstm` and an include of `common_bare.mk`.

- [ ] **Step 4: Rebuild and run**

```bash
bash -lc 'source /opt/cad/scripts/tools_env.sh && cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t && make lstm_rtl-baremetal' 2>&1 | tail -20
```
then the simulation in the background as in Task 3 Step 7.

- [ ] **Step 5: Verify every field the agent will later parse**

```bash
cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t
grep -c "ESP_OUT_BEGIN" modelsim/transcript        # expect 3
grep "ESP_INV_CYCLES" modelsim/transcript          # expect 3 lines, inv=0,1,2
grep "ESP_E2E_CYCLES" modelsim/transcript          # expect 1 line
grep -c "ESP MONITOR" modelsim/transcript          # expect >= 3
```

- [ ] **Step 6: Verify the three invocations agree**

```bash
cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t
for n in 0 1 2; do
  awk "/ESP_OUT_BEGIN inv=$n /{f=1;next} /ESP_OUT_END/{f=0} f" modelsim/transcript > /tmp/inv$n.txt
done
diff /tmp/inv0.txt /tmp/inv1.txt && diff /tmp/inv1.txt /tmp/inv2.txt && echo "ALL THREE MATCH"
```

Expected: `ALL THREE MATCH`. A difference here, in the unmodified baseline, means
the accelerator is not deterministic across invocations — report it and stop, as
it would invalidate the golden before the optimizer ever runs.

- [ ] **Step 7: Commit**

```bash
cd /home/pd2827/esp
cp socs/xilinx-vc707-xc7vx485t/modelsim/transcript docs/superpowers/evidence/task4-instrumented-transcript.txt
git add accelerators/rtl/lstm_rtl/sw/baremetal/lstm.c docs/superpowers/evidence/task4-instrumented-transcript.txt
git commit -m "lstm baseline: instrument the harness

Three invocations, ESP monitor statistics per invocation, per-invocation and
end-to-end cycle counts, and an output dump per invocation. Rebuilt from the
report's description; the modified harness it described was lost with the old
server. Follows soft/common/apps/baremetal/fft_monitors as the in-tree
reference for the monitors API.

Co-Authored-By: <the attribution line your own session was given>"
git push fork coopt
```

---

### Task 5: Record the baseline

**Files:**
- Create: `docs/superpowers/evidence/baseline-lstm_rtl.json`

**Interfaces:**
- Consumes: Task 4's transcript.
- Produces: the numbers every later comparison is measured against.

- [ ] **Step 1: Extract the numbers**

```bash
cd /home/pd2827/esp/socs/xilinx-vc707-xc7vx485t
grep -E "ESP_E2E_CYCLES|ESP_INV_CYCLES" modelsim/transcript
grep -A30 "ESP MONITOR" modelsim/transcript | head -40
```

- [ ] **Step 2: Write the baseline record**

Create `docs/superpowers/evidence/baseline-lstm_rtl.json` with exactly this
shape, filling every field from the transcript. `null` is not acceptable in any
field except where noted; a counter the monitors did not report is a finding
about Task 1's `CONFIG_MON_ACCELERATORS` setting, not a blank to leave.

```json
{
  "accelerator": "lstm_rtl",
  "measured_at": "2026-09-17",
  "git_sha": "<sha of the commit whose build produced this transcript>",
  "server": "torino, riscv64-unknown-elf-gcc 8.3.0, ModelSim DE 2023.2",
  "coherence": "ACC_COH_LLC",
  "cycles": {
    "end_to_end": 0,
    "invocation_0": 0,
    "invocation_1": 0,
    "invocation_2": 0
  },
  "monitors": {
    "acc_tot": 0,
    "acc_mem": 0,
    "acc_tlb": 0,
    "acc_invocations": 0,
    "ddr_accesses": 0,
    "l2_hits": 0,
    "l2_misses": 0,
    "llc_hits": 0,
    "llc_misses": 0,
    "coh_dma_reqs": 0,
    "coh_dma_rsps": 0
  },
  "soc_config": {
    "CONFIG_QUEUE_SIZE": 0,
    "CONFIG_COH_NOC_WIDTH": 0,
    "CONFIG_DMA_NOC_WIDTH": 0,
    "CONFIG_MEM_LINK_WIDTH": 0,
    "CONFIG_SLM_KBYTES": 0,
    "CONFIG_CACHE_EN": "y",
    "CONFIG_ACC_CACHES": "<sets> <ways>",
    "CONFIG_CPU_CACHES": "<l2_sets> <l2_ways> <llc_sets> <llc_ways>"
  },
  "output_vector": ["0x0000"],
  "output_vector_identical_across_invocations": true
}
```

`acc_tot` and `acc_mem` are each assembled from their `_lo` and `_hi` halves
(`MON_ACC_TOT_LO_INDEX` / `MON_ACC_TOT_HI_INDEX` in `monitors.h`) as
`(hi << 32) | lo`. `output_vector` holds all 64 values from invocation 0.

Do not copy any number from the report. These are this server's numbers, on a
repaired baseline, and spec §16 records that the two are not comparable.

- [ ] **Step 3: Confirm the wrapper was never touched**

```bash
cd /home/pd2827/esp
git diff 2c4234c5..HEAD -- accelerators/rtl/lstm_rtl/hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v
```

Expected: **empty output**. The base is `2c4234c5` ("Restore SLDB baseline
accelerators from backup"), the commit that introduced this file — not the
branch point `a45f2bb8`, where the file does not exist yet and the diff would
show all 340 lines as an addition. This is the plan's central constraint. A non-empty
diff means the baseline is no longer the restored RTL and the measurement's
meaning has changed — report it rather than committing.

- [ ] **Step 4: Commit**

```bash
cd /home/pd2827/esp
git add docs/superpowers/evidence/baseline-lstm_rtl.json
git commit -m "lstm baseline: record the measured baseline

First trustworthy lstm_rtl measurement on this server: repaired software
contract, instrumented harness, wrapper untouched. Not comparable to the
report's 2,049,922 cycles, which were taken on a different server with an
unknown toolchain against software that could not see the accelerator's output.

Co-Authored-By: <the attribution line your own session was given>"
git push fork coopt
```

---

### Task 6: Lock the wrapper against accidental edits

**Files:**
- Create: `coopt_agent/configs/editable_files.yaml`
- Create: `docs/superpowers/evidence/baseline-wrapper.sha256`

**Interfaces:**
- Produces: `editable_files.yaml`, consumed by the `rtl_edit` module in the next
  plan as its editable-file whitelist (spec §8 guard 1).

- [ ] **Step 1: Record the baseline wrapper hash**

```bash
cd /home/pd2827/esp
sha256sum accelerators/rtl/lstm_rtl/hw/src/lstm_rtl_basic_dma64/*.v \
  > docs/superpowers/evidence/baseline-wrapper.sha256
cat docs/superpowers/evidence/baseline-wrapper.sha256
```

- [ ] **Step 2: Write the whitelist**

Create `coopt_agent/configs/editable_files.yaml`:

```yaml
# Which files the RTL optimizer may modify, per accelerator.
# Spec section 8 guard 1: decision D1 is enforced structurally here, not by
# asking the model to behave in a prompt.
lstm_rtl:
  editable:
    # The ESP wrapper: DMA control and the load/compute/store FSM.
    - hw/src/lstm_rtl_basic_dma64/lstm_rtl_basic_dma64.v
  read_only:
    # The compute core. `module lstm` and its arithmetic.
    - hw/src/lstm_rtl_basic_dma64/lstm.v
    # vecmat_mul_x, vecmat_add_h, signedmul, qadd2, spram_*, lstm_top.
    - hw/src/lstm_rtl_basic_dma64/lstm_rest.v
  top_module: lstm_rtl_basic_dma64
```

- [ ] **Step 3: Commit**

```bash
cd /home/pd2827/esp
git add coopt_agent/configs/editable_files.yaml docs/superpowers/evidence/baseline-wrapper.sha256
git commit -m "lstm baseline: record the wrapper whitelist and baseline hashes

Only the ESP wrapper is editable; lstm.v and lstm_rest.v hold the compute core
and are read-only. Spec decision D1 is enforced by this list rather than by
prompt wording. The hashes pin what the optimizer starts from.

Co-Authored-By: <the attribution line your own session was given>"
git push fork coopt
```

---

## What this plan deliberately leaves out

`goldengen` and `coopt_agent` are the next two plans. They are not written yet
because Task 3 Step 7 and Task 4 Step 6 can change their content: a degenerate
baseline output means the fixture must be redesigned before a golden format is
worth specifying, and a hang in Task 2 or 3 would send the software-only repair
decision back to the project owner. Writing them now would be guessing.

Write the goldengen plan once this one has produced a non-degenerate,
reproducible baseline vector.
