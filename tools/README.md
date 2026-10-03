# tools/ - run the real firmware without hardware

`emulator_test.py` boots the actual `DeskCompanion.ino` inside Espressif's QEMU (ESP32-S3 machine) and drives it over the
emulated UART with the same JSON protocol the app uses.

## What is (and is not) emulated

The firmware is compiled with `-DDC_SIM`: no USB / HID, no real LED and no real TFT (the 240x240 frame buffer still
exists, so every screen is rendered and can be downloaded with the `snapshot` command and saved as PNG). Everything
else is the production code: command parser, NVS settings, LittleFS, demo-GIF generator, AnimatedGIF decoder,
macro engine, radial menu, focus timer, LED engine, boot log.

Not covered (QEMU limitation, needs a real pad): USB enumeration, the actual SPI display, GPIO levels / pull-ups,
the WS2812 LED, and software resets (QEMU hangs after `esp_restart()` - persistence is therefore tested by killing and
restarting the emulator on the same flash file; safe mode by a build with `-DDC_FORCE_SAFE_MODE`).

## Run it

1. Install QEMU for ESP32 (Espressif fork, 9.x), `pip install pillow psutil`, `arduino-cli` + `esp32:esp32` core 3.x + the four libraries.
2. Build (note `USBMode=hwcdc,CDCOnBoot=default` so `Serial` is UART0, which QEMU exposes):
   ```
   arduino-cli compile --fqbn "esp32:esp32:esp32s3:USBMode=hwcdc,CDCOnBoot=default,PartitionScheme=default,FlashMode=dio" \
       --build-property "compiler.cpp.extra_flags=-DDC_SIM" --output-dir out_sim DeskCompanion
   arduino-cli compile ...same... --build-property "compiler.cpp.extra_flags=-DDC_SIM -DDC_FORCE_SAFE_MODE" --output-dir out_safe DeskCompanion
   python3 tools/mkflash.py out_sim DeskCompanion.ino <core_dir> sim.bin
   python3 tools/mkflash.py out_safe DeskCompanion.ino <core_dir> safe.bin
   ```
3. `QEMU=/path/to/qemu-system-xtensa python3 tools/emulator_test.py sim.bin --safe-image safe.bin --out screenshots/`

The suite copies the image to `<image>.run` first, so your image stays pristine. Screenshots land in `--out`.

## App tests

`tools/run_app_tests.sh` runs everything below (lint first) except the QEMU suite. All of it talks to the built-in simulated pad (no hardware, no internet) and needs no display:
the Qt tests use Qt's `offscreen` platform. Needs `pip install PySide6 pyserial psutil pillow pyflakes qrcode`.

* `tools/lib_test.py`, `appextras_test.py`, `scheduler_test.py`, `scripting_test.py`, `extras_test.py`, `bridge_test.py`, `textops_test.py`, `sysops_test.py`, `system_test.py` - the
  `desk_lib` modules in isolation: OTA client against a fake device, backup zips (incl. hostile ones), feeds (Open-Meteo / ICS / badge server against a local mock),
  active-window rules, the host-action whitelist, the recorder, scripting, update check, plugins, undo history, tips, languages, hotkey (fake backend), diagnostic zip.
* `tools/engine/*_test.py`, `tools/engine/selftest.py` - the **engine** (`core/`, no widgets): connection, the whole Diagnostics tool set, key upload and read-back verification,
  the 50-action library, macros and sequences, layers, profiles, Info feeds, GIF library (against a local mock Tenor + GIPHY server), computer / mouse actions, recovery, backup /
  restore, schedules, local API, plugins, scripts, compatibility with pads on firmware 1.1 / 1.2 / 1.3 / 1.4, and the firmware 1.3 - 1.5 settings.
* `tools/qt/shell_test.py` - the Qt shell: rail, Advanced switch, status pill / bar, toasts, Overview, the twin (drag and drop, wheel), theme / accent / reduce motion, command palette,
  setup wizard, automatic backups, mini pad, guided hardware test (with its HTML report), window geometry, tray-aware close.
* `tools/qt/keys_test.py`, `display_test.py`, `rules_test.py`, `scripts_test.py`, `padapp_test.py` - every page through its real widgets: the same user actions as clicking, with the effect checked on the simulated pad.
* `tools/qt/monkey_test.py` - visits every page and tab, clicks every enabled button, changes every switch / choice / slider / field, with confirmations refused and then accepted; any exception in a slot or event handler fails it.
* `tools/qt_layout_test.py [--lang de] [--light] [--shots DIR]` - every page / tab / panel at five window sizes (1300x780, 1000x780, 800x780, 640x520, 1300x560): no clipped text, no widget outside its parent,
  no overlap, no horizontal overflow, no page-level scrolling. `tools/qt_shots.py` writes the screenshots in `docs/qt_preview/`.
* `tools/run_emulator_suite.sh` - builds both emulator images with arduino-cli and runs the whole QEMU suite (what CI does).

The 1.5 tests (they now live in `tools/engine/` unless noted):

* `tools/appextras_test.py` - update check, plugins, undo history, tips, languages, global hotkey (fake backend), latency / power, the mouse recorder,
  secret redaction and the diagnostic zip, the Python client against a fake serial port. No display needed.
* `engine/appglue_test.py` - accent colour / scale / language / system theme, undo-redo of key assignments, plugins through the host-action whitelist,
  hotkey, update check, tips card, auto-heal (a simulated pad that corrupts the first write), live mirror, share codes (including hostile ones),
  the diagnostic zip.
* `engine/data_test.py` - the info-card sources (quotes, birthdays, ping / website, lyrics, moon, sun, network, goal, rain, rings), cover art / QR code,
  the sound-reactive LED maths and thread, the time-of-day mood, LED alerts, and their app glue.
* `engine/fw15_test.py` - the firmware-1.5 features in the app against the simulated pad, plus a simulated 1.4 pad for compatibility.
* `emulator_test.py` group `t_fw15` - the real firmware 1.5 in QEMU: themes, display options, the eight new screens, saver styles, clock faces,
  card kinds, triple tap / chords / dial clicks (also with synthetic key timing through `key_test`), pad functions, labels, dim, boot log, rollback.
  It takes about 3.5 minutes; **do not run other heavy jobs on the same machine meanwhile** - starving the emulator once produced a firmware
  reset in `t_settings` (two clean runs followed).
