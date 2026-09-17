#!/bin/bash
# Non-interactive driver for ESP's official toolchain script
# (https://www.esp.cs.columbia.edu/docs/setup/setup-guide/ -> utils/toolchain/build_riscv_toolchain.sh)
# Answers, in prompt order:
#   y                  -> continue
#   /home/pd2827/riscv -> target folder
#   16                 -> make threads
#   n                  -> do NOT enable Python in rootfs
#   n                  -> do NOT skip baremetal newlib toolchain   <-- the only part we need
#   y                  -> SKIP Linux glibc toolchain
#   y                  -> SKIP buildroot rootfs
cd /home/pd2827/esp
printf 'y\n/home/pd2827/riscv\n16\nn\nn\ny\ny\n' | ./utils/toolchain/build_riscv_toolchain.sh
