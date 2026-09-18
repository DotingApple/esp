#!/bin/bash
# Baseline sweep: run every RTL accelerator through the full ESP flow and record
# what happens, including the ones that never finish.
#
# The point is a survey, not a measurement. The accelerators' baremetal programs
# are the stock SLDB ones, whose validation is unreliable (lstm_rtl's is
# commented out entirely and always prints PASS), so a `pass` result here means
# "the flow completed", not "the accelerator is correct".
#
# Resumable: an accelerator whose result JSON already exists is skipped, so an
# interrupted sweep can be restarted without redoing hours of work.
#
# Usage:  setup/baseline_sweep.sh [accelerator ...]
#         setup/baseline_sweep.sh            # all of them
#
# Output: docs/superpowers/evidence/baseline-sweep/
#           <acc>.json          structured result
#           <acc>.transcript    ModelSim transcript, when one was produced
#           <acc>.log           build/sim output (tail on failure)
#           SUMMARY.md          regenerated after every accelerator

set -u

ESP_ROOT=/home/pd2827/esp
SOC_DIR="$ESP_ROOT/socs/xilinx-vc707-xc7vx485t"
OUT_DIR="$ESP_ROOT/docs/superpowers/evidence/baseline-sweep"
BASELINE_CFG="$ESP_ROOT/coopt_agent/configs/baseline_lstm_rtl.esp_config"
LIVE_CFG="$SOC_DIR/socgen/esp/.esp_config"
CAD_ENV=/opt/cad/scripts/tools_env.sh

# lstm_rtl took 20m33s. 45 minutes leaves room for slower accelerators while
# still bounding the sweep; anything past it is reported as a timeout, which is
# itself the finding we are after.
SIM_TIMEOUT=${SIM_TIMEOUT:-2700}
BUILD_TIMEOUT=${BUILD_TIMEOUT:-1800}

mkdir -p "$OUT_DIR"

if [ $# -gt 0 ]; then
    ACCS=("$@")
else
    # lstm_rtl first: it is the accelerator the rest of the project depends on,
    # so its result should be available early even if the sweep is interrupted.
    mapfile -t ACCS < <(ls -d "$ESP_ROOT"/accelerators/rtl/*/ |
                        awk -F/ '{print $(NF-1)}' | grep -v '^common$' |
                        awk '{ if ($0 == "lstm_rtl") first = $0; else rest = rest $0 "\n" }
                             END { print first; printf "%s", rest }')
fi

say() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

# Run a command under the CAD environment with a timeout, in its own process
# group so that a timeout kills vsim too rather than orphaning it to burn a core
# for the rest of the sweep.
esp_run() {
    local secs=$1 logfile=$2; shift 2
    local cmd="$*"
    # setsid makes the inner bash a session AND process-group leader, so its $$
    # is the PGID of everything it spawns. Recording it lets a timeout kill the
    # whole group -- vsim included -- instead of orphaning a simulator that
    # would burn a core for the rest of the sweep.
    #
    # Neither `timeout` nor a bare `setsid` does this correctly here, and both
    # were tried: plain `setsid` forks and returns immediately when the caller
    # is already a process-group leader, so the caller sees every stage
    # "succeed" in milliseconds, and `timeout` (with or without --foreground)
    # leaves descendants running.
    local pgidfile
    pgidfile=$(mktemp)
    setsid --wait bash -lc "echo \$\$ > '$pgidfile'; source $CAD_ENV >/dev/null 2>&1 && cd '$SOC_DIR' && $cmd" \
        >>"$logfile" 2>&1 &
    local waiter=$!

    local elapsed=0
    while kill -0 "$waiter" 2>/dev/null; do
        sleep 5
        elapsed=$((elapsed + 5))
        if [ "$elapsed" -ge "$secs" ]; then
            local pg
            pg=$(cat "$pgidfile" 2>/dev/null)
            if [ -n "$pg" ]; then
                kill -TERM -"$pg" 2>/dev/null
                sleep 15
                kill -KILL -"$pg" 2>/dev/null
            fi
            wait "$waiter" 2>/dev/null
            rm -f "$pgidfile"
            return 124
        fi
    done
    wait "$waiter"
    local rc=$?
    rm -f "$pgidfile"
    return $rc
}

# Write one accelerator's result. Fields are flat on purpose: this file is read
# by a human deciding which accelerators are worth optimizing.
emit() {
    local acc=$1 status=$2 stage=$3 detail=$4 sim_ns=$5 wall=$6
    python3 - "$OUT_DIR/$acc.json" "$acc" "$status" "$stage" "$detail" "$sim_ns" "$wall" <<'PY'
import json, sys
path, acc, status, stage, detail, sim_ns, wall = sys.argv[1:8]
json.dump({
    "accelerator": acc,
    "status": status,          # pass | fail | timeout | no_start | build_failed | ...
    "failed_stage": stage,     # "" when it completed
    "detail": detail,
    "sim_time_ns": int(sim_ns) if sim_ns.isdigit() else None,
    "wall_seconds": int(wall),
}, open(path, "w"), indent=2)
PY
    say "  -> $status ${stage:+(at $stage)} ${detail:+- $detail}"
}

classify() {
    # Reads the transcript and decides what happened. Order matters: the
    # earliest failure in the run's own sequence is the one worth reporting.
    local acc=$1 t=$2
    if [ ! -s "$t" ]; then echo "no_transcript|"; return; fi
    if grep -q "not found" "$t"; then echo "no_start|accelerator not in device tree"; return; fi
    if ! grep -q "Scanning device tree" "$t"; then echo "no_boot|program never reached device scan"; return; fi
    if ! grep -q "sld,$acc" "$t"; then echo "no_start|probe did not register sld,$acc"; return; fi
    if ! grep -q "Start\.\.\." "$t"; then echo "no_invoke|accelerator found but never started"; return; fi
    if ! grep -q "Program Completed" "$t"; then echo "no_done|started but never completed"; return; fi
    if grep -q "\.\.\. FAIL" "$t"; then echo "fail|software validation reported FAIL"; return; fi
    if grep -q "\.\.\. PASS" "$t"; then echo "pass|software reported PASS (may be vacuous)"; return; fi
    echo "completed_no_verdict|ran to completion, printed no PASS/FAIL"
}

############################  per-accelerator sweep  ###########################
for acc in "${ACCS[@]}"; do
    if [ -f "$OUT_DIR/$acc.json" ]; then
        say "$acc: result exists, skipping"; continue
    fi

    say "=== $acc ==="
    started=$(date +%s)
    log="$OUT_DIR/$acc.log"; : >"$log"
    upper=$(echo "$acc" | tr '[:lower:]' '[:upper:]')

    # Install ONLY the accelerator under test. `make sim` builds a ModelSim
    # library for every accelerator in tech/<tech>/acc/, so one accelerator
    # whose Verilog does not compile blocks the simulation of all the others --
    # sha256_rtl is exactly that case, with two `assign` statements sitting
    # inside its module's port list. Isolating each accelerator means a failure
    # is attributed to the accelerator that caused it instead of stopping the
    # sweep, which is the whole point of the survey.
    say "  isolating tech/ and installing $acc"
    find "$ESP_ROOT/tech/virtex7/acc" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
    : > "$ESP_ROOT/tech/virtex7/acc/installed.log"
    if ! esp_run "$BUILD_TIMEOUT" "$log" "make $acc-hls"; then
        emit "$acc" install_failed hls "make $acc-hls failed" "" $(( $(date +%s) - started )); continue
    fi
    if [ ! -d "$ESP_ROOT/tech/virtex7/acc/$acc" ]; then
        emit "$acc" install_failed hls "no tech/virtex7/acc/$acc produced" "" $(( $(date +%s) - started )); continue
    fi

    # Retarget the accelerator tile. Field order comes from soc.py:456-478:
    #   <n> acc <NAME> <point> <has_l2> <has_tdvfs> <vendor>
    # has_l2=0 because every run here uses the default coherence path; an
    # accelerator L2 would be simulated and never used.
    cp "$BASELINE_CFG" "$LIVE_CFG"
    sed -i "s|^TILE_1_0 = .*|TILE_1_0 = 2 acc $upper basic_dma64 0 0 sld|" "$LIVE_CFG"
    grep -q "^TILE_1_0 = 2 acc $upper " "$LIVE_CFG" || {
        emit "$acc" config_failed retarget "tile line did not take" "" $(( $(date +%s) - started )); continue; }

    say "  esp-config"
    esp_run "$BUILD_TIMEOUT" "$log" "make esp-config" || true   # xvfb teardown often exits nonzero
    if ! grep -q "Created configuration into 'socmap.vhd'" "$log"; then
        emit "$acc" config_failed esp-config "socmap.vhd not generated" "" $(( $(date +%s) - started )); continue
    fi
    if ! grep -qi "$acc" "$SOC_DIR/socgen/esp/socmap.vhd"; then
        emit "$acc" not_in_socmap esp-config "retarget did not reach socmap.vhd" "" $(( $(date +%s) - started )); continue
    fi

    # socketgen depends on socmap.vhd, so esp-config above should have
    # invalidated it -- but its other dependency is tech/.../installed.log
    # rather than the directory contents, so force it rather than trust it.
    say "  socketgen"
    esp_run "$BUILD_TIMEOUT" "$log" "make socketgen-distclean && make socketgen" || {
        emit "$acc" socketgen_failed socketgen "wrapper generation failed" "" $(( $(date +%s) - started )); continue; }

    say "  baremetal"
    if ! esp_run "$BUILD_TIMEOUT" "$log" "make $acc-baremetal"; then
        emit "$acc" build_failed baremetal "make $acc-baremetal failed" "" $(( $(date +%s) - started )); continue
    fi
    exe="$SOC_DIR/soft-build/ariane/baremetal/$acc.exe"
    [ -f "$exe" ] || { emit "$acc" build_failed baremetal "no $acc.exe produced" "" $(( $(date +%s) - started )); continue; }

    say "  sim (timeout ${SIM_TIMEOUT}s)"
    rm -f "$SOC_DIR/modelsim/transcript"
    esp_run "$SIM_TIMEOUT" "$log" "make sim TEST_PROGRAM=./soft-build/ariane/baremetal/$acc.exe"
    simrc=$?
    wall=$(( $(date +%s) - started ))

    t="$SOC_DIR/modelsim/transcript"
    [ -f "$t" ] && cp "$t" "$OUT_DIR/$acc.transcript"

    if [ $simrc -eq 124 ] || [ $simrc -eq 137 ]; then
        # A timeout is a real result, and the transcript says where it stalled.
        det="killed after ${SIM_TIMEOUT}s"
        if [ -f "$t" ] && grep -q "Start\.\.\." "$t"; then det="$det; reached Start... then stalled"; fi
        emit "$acc" timeout sim "$det" "" "$wall"; continue
    fi

    if [ ! -s "$OUT_DIR/$acc.transcript" ]; then
        emit "$acc" sim_compile_failed sim "no transcript produced" "" "$wall"; continue
    fi

    IFS='|' read -r status detail < <(classify "$acc" "$OUT_DIR/$acc.transcript")
    # "Time: <ns>" sits on the line AFTER "Program Completed", not on it.
    sim_ns=$(grep -A1 "Program Completed" "$OUT_DIR/$acc.transcript" |
             grep -oP 'Time:\s*\K[0-9]+' | head -1)
    emit "$acc" "$status" "" "$detail" "${sim_ns:-}" "$wall"

    # Keep only the tail of successful logs; full logs matter only for failures.
    if [ "$status" = "pass" ]; then tail -200 "$log" > "$log.tmp" && mv "$log.tmp" "$log"; fi

    # Regenerate the summary after each accelerator so progress is visible
    # without waiting for the whole sweep.
    "$ESP_ROOT/setup/baseline_sweep_summary.py" "$OUT_DIR" > "$OUT_DIR/SUMMARY.md" 2>/dev/null || true
done

############################  restore  ########################################
say "restoring: lstm_rtl alone in tech/, baseline configuration"
find "$ESP_ROOT/tech/virtex7/acc" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
: > "$ESP_ROOT/tech/virtex7/acc/installed.log"
esp_run "$BUILD_TIMEOUT" "$OUT_DIR/restore.log" "make lstm_rtl-hls" || true
cp "$BASELINE_CFG" "$LIVE_CFG"
esp_run "$BUILD_TIMEOUT" "$OUT_DIR/restore.log" "make esp-config" || true
esp_run "$BUILD_TIMEOUT" "$OUT_DIR/restore.log" "make socketgen-distclean && make socketgen" || true

"$ESP_ROOT/setup/baseline_sweep_summary.py" "$OUT_DIR" > "$OUT_DIR/SUMMARY.md" 2>/dev/null || true
say "sweep complete. summary: $OUT_DIR/SUMMARY.md"
