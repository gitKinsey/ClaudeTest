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

## App self-test

Run these under `xvfb-run -a` on a headless machine; all talk to the built-in simulated pad (no hardware, no internet):

* `python3 tools/app_selftest.py` - connection, the whole Dev tab, key upload and read-back verification, diagnostic report.
* `python3 tools/macro_test.py` - the 50-action library, Macro Creator (4 modifiers + key, text, delays, sequences,
  validation), upload and read-back.
* `python3 tools/giflib_test.py` - the GIF tab: 16 built-in animations, My GIFs folder (add / duplicate names / delete),
  Online search against a local mock Tenor + GIPHY server (trending, search, wrong key, no network, non-GIF download,
  stale searches), upload with byte comparison, and the PC-volume mirroring loop (parsing of `pactl` / `amixer` /
  `osascript` output from canned text, what is sent to the pad). `SHOTS=1` also saves screenshots to `/tmp`.
