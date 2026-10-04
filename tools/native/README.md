# Native firmware build

`DeskCompanion.ino` compiled with the host g++ against small stand-ins for the Arduino-ESP32 core (`shim/`), a software `TFT_eSprite`, NVS and LittleFS on the host file system,
and a pipe instead of USB. The *real* firmware code runs: command parser, key / gesture state machines, screens, settings. What it is not: a model of the ESP32 timing, USB, the real
panel or the real fonts (text is approximated with DejaVu glyphs), and GIF decoding is stubbed (a stored GIF "plays" a blank frame).

    python3 tools/native/build.py build/dc_native                              # full build: HID output is recorded, display + LED events are reported
    DC_NATIVE_FLAGS=-DDC_SIM python3 tools/native/build.py build/dc_native_sim  # the QEMU test configuration (no HID, no TFT)
    python3 tools/emulator_test.py --native build/dc_native_sim                 # the whole emulator suite against the native build

stdin: ordinary lines are the serial link; `#key <1..5> <0|1>`, `#enc <detents>`, `#encsw <0|1>`, `#pin <n> <0|1>`, `#frame`, `#quit` control the hardware.
stdout: serial output plus `#led r g b`, `#bl duty`, `#hid kb|cc|ms ...`, `#restart`. `esp_restart()` exits with code 75 (`native_emu.NativeEmu` relaunches on the same NVS / files).
