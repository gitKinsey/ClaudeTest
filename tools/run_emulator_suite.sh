#!/usr/bin/env bash
# Build the firmware for the emulator (normal + forced-safe-mode image) and run the whole QEMU suite.
#   needs: arduino-cli with the esp32 core + the four libraries, Espressif's QEMU, python3 with pillow
#   usage: QEMU=/path/to/qemu-system-xtensa tools/run_emulator_suite.sh [output dir for screenshots]
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-emulator_out}"
WORK="$(mktemp -d)"
FQBN="esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=default,PartitionScheme=default,FlashMode=dio"   # Serial = UART0, which QEMU exposes
mkdir -p "$WORK/DeskCompanion"
cp DeskCompanion/DeskCompanion.ino "$WORK/DeskCompanion/"
arduino-cli compile --fqbn "$FQBN" --build-property "compiler.cpp.extra_flags=-DDC_SIM" --output-dir "$WORK/out_sim" "$WORK/DeskCompanion"
arduino-cli compile --fqbn "$FQBN" --build-property "compiler.cpp.extra_flags=-DDC_SIM -DDC_FORCE_SAFE_MODE" --output-dir "$WORK/out_safe" "$WORK/DeskCompanion"
CORE_DIR="$(arduino-cli core list --format json | python3 -c 'import json,sys,glob,os; print(sorted(glob.glob(os.path.expanduser("~/.arduino15/packages/esp32/hardware/esp32/*")))[-1])')"
python3 tools/mkflash.py "$WORK/out_sim" DeskCompanion.ino "$CORE_DIR" "$WORK/sim.bin"
python3 tools/mkflash.py "$WORK/out_safe" DeskCompanion.ino "$CORE_DIR" "$WORK/safe.bin"
python3 -u tools/emulator_test.py "$WORK/sim.bin" --safe-image "$WORK/safe.bin" --out "$OUT"
