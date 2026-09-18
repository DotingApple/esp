# Baselines

One directory per accelerator, holding everything the optimizer is measured
against. These files are **committed on purpose**.

That is not the default a Python project would reach for, and it is deliberate:
this project exists because an earlier version of it was lost with a server. The
one artifact that made recovery possible was a ModelSim transcript that had been
committed under the old `soc_opt_agent/baselines/`. It still carried the vsim
command line, which is how the lost `socs/xilinx-vc707-xc7vx485t/vsim.tcl` was
reconstructed — without it the whole simulation flow stayed blocked. Evidence
that only exists on one machine is evidence you are one disk away from losing.

## Layout

```
baselines/<accelerator>/
  soc_params.esp_config   the SoC configuration this accelerator is measured under
  rtl/                    a pristine copy of hw/src/<accelerator>_basic_dma64/
  rtl.sha256              hashes of everything in rtl/
  sweep.json              survey result: did the stock flow run at all
  transcript.txt          the ModelSim transcript that produced it
  metrics.json            the measured baseline — cycles, monitors, output vector
```

`sweep.json`, `transcript.txt` and `metrics.json` appear as they are produced;
`soc_params.esp_config`, `rtl/` and `rtl.sha256` are the inputs and are here from
the start.

## What each file is for

**`soc_params.esp_config`** — the configuration the accelerator's baseline was
measured under, and the configuration every candidate is derived *from*. Spec §9
requires the candidate config to be re-derived from the baseline on every
iteration rather than ratcheted forward, so this file is not a historical note:
it is a live input to each run.

Each one is the shared baseline with its own accelerator tile. The tile line's
field order comes from `tools/socgen/soc.py:456-478` —
`<n> acc <NAME> <point> <has_l2> <has_tdvfs> <vendor>` — and `has_l2` is 0
because every run uses the LLC coherence path, where an accelerator's private L2
is bypassed. An accelerator L2 would be simulated on every iteration and never
used, and simulation time is what bounds how many iterations a campaign can
afford.

**`rtl/`** — the accelerator's implementation exactly as it was before any
optimization. The optimizer edits only the ESP wrapper (see
`configs/editable_files.yaml`); everything else here is the compute core, which
spec decision D1 places off limits. Keeping the whole directory rather than just
the wrapper means a candidate can be diffed against, or reverted to, its true
starting point without relying on git archaeology.

Note that "original" means *as restored*, not *as the accelerator's author wrote
it*. `lstm_rtl`'s wrapper already carried hand edits when it arrived — a
`dma_write_ctrl_data_length` of `32'd4096` with the original value commented out
beside it, and `// << CHG` markers on two FSM transitions. `sha256_rtl`'s wrapper
does not compile at all: two `assign` statements sit inside its module's port
list. These are recorded rather than repaired, so that a later measurement is
compared against what actually ran.

**`rtl.sha256`** — lets a run assert it started from the RTL it thinks it did.
Cheap, and the alternative is discovering mid-campaign that the baseline moved.
