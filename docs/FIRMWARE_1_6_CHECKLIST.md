# Firmware 1.6 checklist: the pad side catches up with the Qt app

Firmware 1.5 already implements every command the Qt app uses (see `docs/PROTOCOL.md`), so nothing is *missing*. What the new app
could do better with a little help from the pad is below. Every item follows the same recipe, so an older pad keeps working:

1. **Firmware** (`DeskCompanion/DeskCompanion.ino`): the change, behind a new entry in `caps` (the app never compares version numbers).
2. **Simulator** (`SimFirmware` in `core/base.py`): the same behaviour in Python, so the app tests can run it.
3. **Engine** (`core/`): uses the new command when `caps` has it, falls back to today's behaviour when not.
4. **Qt UI** (`ui_qt/`): only where the user sees or controls something.
5. **Tests**: `tools/engine/fw16_test.py` (new pad **and** a simulated 1.5 pad), a widget test where there is UI, an emulator group `t_fw16` (CI only).
6. **Docs**: `docs/PROTOCOL.md` table row, `docs/FEATURES.md`.

Legend: `[ ]` open, `[x]` done and tested here, `[~]` done, but the part that needs a compiler / QEMU / the real pad has not run.

## What cannot be done in this environment (read first)

* **No compiler.** `arduino-cli` and the ESP32 core cannot be downloaded here (the proxy blocks `espressif.github.io`). Firmware edits are therefore
  **not compiled** until CI (`firmware-compile` matrix, 4 core versions) or your machine does it. Each item is kept small and written in the style of
  the surrounding code to keep that risk low, but "it compiles" is unproven until CI is green.
* **No new `.bin`.** `firmware/DeskCompanion.bin` (what the app flashes with *Update the pad*) is a build of **1.5.0** and stays that way. The
  version string in the sketch is only bumped to 1.6.0 together with fresh images (item Z1); until then the app must not claim 1.6 features exist
  on a pad that runs the shipped image. Feature detection by `caps` makes this safe.
* **No QEMU, no hardware.** The emulator groups and everything physical (display, keys, flash wear, USB) run in CI or on your pad.

## Tier 1: make the app faster and more truthful

* [ ] **F1 `state` events** - the pad announces `{"evt":"state","mode":n,"bright":n,"layer":n}` whenever the dial menu, a long press, auto-dim or a
  pad function changes them. *App:* the twin, the mini pad, Look and Keys follow the physical pad at once instead of on the next poll; the "pad is on
  layer N" pill is always right. cap `stateevt`.
* [ ] **F2 `keys_crc`** - one command returns a CRC32 per layer of the key map + gestures + labels. *App:* on connect and in "Verify keys on pad" one
  round trip says whether pad and app agree (today: read every key of three layers); the orange "unsent" dots are exact after a reconnect; auto-heal
  gets cheaper. cap `crc`.
* [ ] **F3 `remap_batch`** - up to 24 key specs in one command, validated as a whole (all or nothing), one flash commit. *App:* *Upload to pad* takes
  3 round trips instead of up to 21 and wears the flash less; progress per layer. cap `batch`.
* [ ] **F4 smaller `snapshot`** - `snapshot` accepts `scale` (1, 2) so the live mirror and the mini pad can use a 120x120 frame (4x less data) while
  the Diagnostics screenshot stays full size. cap `snap2`.

## Tier 2: the pad shows what the app knows

* [ ] **F5 `ctx` line** - the app sends the current program / rule name; the pad shows it for ~1.5 s when the layer changes automatically and as a
  small line on the clock screen. *App:* Rules -> Programs sends it from the profile loop (switch "Show the program name on the pad"). cap `ctx`.
* [ ] **F6 layer names** - `layer_names` stores three names of up to 10 ASCII characters (NVS); the pad uses them in layer toasts and the popup menu.
  *App:* Keys layer selector gets an editable name per layer. cap `lnames`.
* [ ] **F7 `toast`** - `{"cmd":"toast","text":"...","kind":"ok|warn|err","secs":3}` shows an overlay on the pad; ignored while a game runs. *App:* script
  `notify`, update available, CI result, backup done, reminders from schedules. cap `toast`.
* [ ] **F8 accent sync** - `settings` accepts `accent` 0..5 (0 = the theme's own colour, 1..5 = the app's five accent colours). *App:* Pad & App -> This
  app switch "Pad follows the app's accent". cap `accent`.

## Tier 3: diagnostics and robustness

* [ ] **F9 `proto` number in `hello`** - an integer protocol level (plus `build_id`), so the app can say "the app is newer than the pad" exactly. `caps` stays the
  source of truth for features. (No cap needed.)
* [ ] **F10 loop / heap counters in `stats`** - longest loop time, minimum free heap, USB drop count, last serial overrun. *App:* Diagnostics -> Device info and
  the diagnostic report; a warning pill on Overview when the loop is slow. cap `perf`.
* [ ] **F11 serial overrun guard** - a request longer than the line buffer answers `too_long` instead of being cut and misparsed (found while reading the parser: check
  first whether it already does).

## Cross-cutting

* [ ] **Z1 build the images** - a CI job `firmware-images` that builds `DeskCompanion.bin`, `DeskCompanion-wifi.bin`, `CoreBringup.bin` and `SHA256SUMS` and uploads
  them as an artifact (and attaches them on release). Until it has run, the bundled images stay 1.5.0. Needs a decision: commit the images by hand, or let CI commit them.
* [ ] **Z2 version** - bump `FW_VERSION`, `FW_BUNDLED`, app version only together with Z1.
* [ ] **Z3 compatibility test** - `tools/engine/fw16_test.py` runs every item above against a simulated **1.6** pad and a simulated **1.5** pad (no new caps).
* [ ] **Z4 emulator group `t_fw16`** in `tools/emulator_test.py` (runs in CI only).
* [ ] **Z5 docs** - `docs/PROTOCOL.md` cap table + command table, `docs/FEATURES.md` rows with "what only real hardware can prove".
* [ ] **Z6 app wording** - the firmware banner on Overview and the Firmware tab list what a pad on 1.5 would gain from updating.

## Suggested order

F9, F1, F2, F3 first (small, mostly protocol, big effect on how the app feels), then F7, F5, F6, F8, F4, F10, F11, with Z1 / Z2 last.
