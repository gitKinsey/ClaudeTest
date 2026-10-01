#!/usr/bin/env python3
"""Assemble a 4 MB QEMU flash image from arduino-cli build output.

  python3 tools/mkflash.py <build_output_dir> <sketch_name.ino> <esp32_core_dir> <out.bin>

  build_output_dir   what `arduino-cli compile --output-dir` wrote (contains <name>.bootloader.bin, .partitions.bin, .bin)
  sketch_name.ino    e.g. DeskCompanion.ino
  esp32_core_dir     .../packages/esp32/hardware/esp32/<version>   (for boot_app0.bin)
"""
import os
import sys

out_dir, name, core_dir, dst = sys.argv[1:5]
flash = bytearray(b"\xff" * (4 * 1024 * 1024))


def put(off, path):
    data = open(path, "rb").read()
    flash[off:off + len(data)] = data


put(0x0, os.path.join(out_dir, name + ".bootloader.bin"))
put(0x8000, os.path.join(out_dir, name + ".partitions.bin"))
put(0xE000, os.path.join(core_dir, "tools", "partitions", "boot_app0.bin"))
put(0x10000, os.path.join(out_dir, name + ".bin"))
open(dst, "wb").write(flash)
print("flash image", dst, len(flash))
