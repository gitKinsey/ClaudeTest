# Firmware 1.6 checklist: the pad side catches up with the Qt app

Firmware 1.5 already implements every command the Qt app uses (see `docs/PROTOCOL.md`), so nothing is *missing*. What the new app
could do better with a little help from the pad is below. Every item follows the same recipe, so an older pad keeps working:

1. **Firmware** (`DeskCompanion/DeskCompanion.ino`): the change, behind a new entry in `caps` (the app never compares version numbers).
2. **Simulator** (`SimFirmware` in `core/base.py`): the same behaviour in Python, so the app tests can run it.
3. **Engine** (`core/`): uses the new command when `caps` has it, falls back to today's behaviour when not.
4. **Qt UI** (`ui_qt/`): only where the user sees or controls something.
5. **Tests**: `tools/engine/fw16_test.py` (new pad **and** a simulated 1.5 pad), a widget test where there is UI, an emulator group `t_fw16` (CI only).
6. **Docs**: `docs/PROTOCOL.md` table row, `docs/FEATURES.md`.

Legend: `[ ]` open, `[x]` done and tested here, `[~]` written and tested in the Python model + app, but the C++ has **not been compiled or run** (no compiler, no QEMU here) - CI (`firmware-compile`, `emulator`) is the first real check, then a pad.

## What cannot be done in this environment (read first)

* **No compiler.** `arduino-cli` and the ESP32 core cannot be downloaded here (the proxy blocks `espressif.github.io`). Firmware edits are therefore
  **not compiled** until CI (`firmware-compile` matrix, 4 core versions) or your machine does it. Each item is kept small and written in the style of
  the surrounding code to keep that risk low, but "it compiles" is unproven until CI is green.
* **No new `.bin`.** `firmware/DeskCompanion.bin` (what the app flashes with *Update the pad*) is a build of **1.5.0** and stays that way. The
  version string in the sketch is only bumped to 1.6.0 together with fresh images (item Z1); until then the app must not claim 1.6 features exist
  on a pad that runs the shipped image. Feature detection by `caps` makes this safe.
* **No QEMU, no hardware.** The emulator groups and everything physical (display, keys, flash wear, USB) run in CI or on your pad.

## Tier 1: make the app faster and more truthful

* [~] **F1 `state` events** - the pad announces `{"evt":"state","mode":n,"bright":n,"layer":n}` whenever the dial menu, a long press, auto-dim or a
  pad function changes them. *App:* the twin, the mini pad, Look and Keys follow the physical pad at once instead of on the next poll; the "pad is on
  layer N" pill is always right. cap `stateevt`.
* [x] **F2 `keys_crc`** - **dropped: it already exists.** `getkeys` returns the length and CRC32 of every slot (one request per layer) and the app's read-back check, auto-heal and
  *Verify keys on pad* already use it. A single all-layers reply would save two requests; not worth a new command.
* [~] **F3 `remap_batch`** - up to 24 key specs in one command, validated as a whole (all or nothing), one flash commit. *App:* *Upload to pad* takes
  3 round trips instead of up to 21 and wears the flash less; progress per layer. cap `batch`.
* [~] **F4 smaller `snapshot`** - `snapshot` accepts `scale` (1, 2) so the live mirror and the mini pad can use a 120x120 frame (4x less data) while
  the Diagnostics screenshot stays full size. cap `snap2`.

## Tier 2: the pad shows what the app knows

* [~] **F5 `ctx` line** - the app sends the current program / rule name; the pad shows it for 2.5 s next to the layer badge when the layer changes automatically. *App:* Rules -> Programs sends it from the profile loop (switch "Show the program name on the pad"). cap `ctx`.
* [~] **F6 layer names** - `layer_names` stores three names of up to 10 ASCII characters (NVS); the pad uses them in layer toasts and the popup menu.
  *App:* Keys layer selector gets an editable name per layer. cap `lnames`.
* [~] **F7 `toast`** - `{"cmd":"toast","text":"...","kind":"ok|warn|err","secs":3}` shows an overlay on the pad; ignored while a game runs. *App:* script
  `notify`, update available, CI result, backup done, reminders from schedules. cap `toast`.
* [~] **F8 accent sync** - `settings` accepts `accent` 0..5 (0 = the theme's own colour, 1..5 = the app's five accent colours). *App:* Pad & App -> This
  app switch "Pad follows the app's accent". cap `accent`.

## Tier 3: diagnostics and robustness

* [~] **F9 `proto` number in `hello`** - an integer protocol level (plus `build_id`), so the app can say "the app is newer than the pad" exactly. `caps` stays the
  source of truth for features. (No cap needed.)
* [~] **F10 loop / heap counters in `info`** - longest loop time, minimum free heap, USB drop count, last serial overrun. *App:* Diagnostics -> Device info and
  the diagnostic report; a warning pill on Overview when the loop is slow. cap `perf`.
* [~] **F11 serial overrun guard** - a request longer than the line buffer answers `too_long` instead of being cut and misparsed (found while reading the parser: check
  first whether it already does).

## Cross-cutting

* [ ] **Z1 build the images** - a CI job `firmware-images` that builds `DeskCompanion.bin`, `DeskCompanion-wifi.bin`, `CoreBringup.bin` and `SHA256SUMS` and uploads
  them as an artifact (and attaches them on release). Until it has run, the bundled images stay 1.5.0. Needs a decision: commit the images by hand, or let CI commit them.
* [ ] **Z2 version** - bump `FW_VERSION`, `FW_BUNDLED`, app version only together with Z1.
* [x] **Z3 compatibility test** - `tools/engine/fw16_test.py` runs every item above against a simulated **1.6** pad and a simulated **1.5** pad (no new caps).
* [~] **Z4 emulator group `t_fw16`** in `tools/emulator_test.py` (written, never run: CI only).
* [x] **Z5 docs** - `docs/PROTOCOL.md` cap table + command table, `docs/FEATURES.md` rows with "what only real hardware can prove".
* [~] **Z6 app wording** (Pad & App -> Firmware lists what 1.6 adds; the Overview banner is unchanged) - the firmware banner on Overview and the Firmware tab list what a pad on 1.5 would gain from updating.

## Where it lives

| item | firmware (`DeskCompanion.ino`) | simulator (`core/base.py`) | engine | UI | tests |
|---|---|---|---|---|---|
| F1 | `evtState`, `stateDirty`, loop flush | `pad_side_change` | `Fw16Ops._on_state_event` | twin, Look, layer pill follow | `fw16_test`, `fw16_ui_test`, `t_fw16` |
| F3 | `cmdRemapBatch` | `remap_batch` | `Fw16Ops._send_remaps` (24 items / 4800 chars per batch), used by upload | - | `fw16_test`, `t_fw16` |
| F4 | `snapScale` in `cmdSnapshot` | `_snapshot(scale)` | `mirror_busy_snapshot` | live mirror | `fw16_test`, `t_fw16` |
| F5 | `cmdCtx`, overlay | `ctx` | `send_ctx`, profile loop | Rules -> Programs switch | `fw16_test`, `fw16_ui_test` |
| F6 | `cmdLayerNames`, badge text | `layer_names` | `layer_names_set`, pushed on connect | Keys -> Key map | `fw16_test`, `fw16_ui_test`, `t_fw16` |
| F7 | `cmdToast`, overlay | `toast` | `pad_toast`, `notify(pad=True)` | Pad & App -> Behaviour switch | same |
| F8 | `accentIdx` in `applyTheme` | `settings` `accent` | `set_pad_accent`, `push_accent` | Pad & App -> This app switch | same |
| F9 | `PROTO_LEVEL` in hello / info | `proto` | `proto_newer`, `fw_status` | Overview banner text | `fw16_test` |
| F10 | `loopMaxUs` ... in `cmdInfo` | info fields | `perf_text`, `perf_refresh` | Diagnostics line | `fw16_test`, `fw16_ui_test`, `t_fw16` |
| F11 | `rxOverruns`, `too_long` | `feed` | - | Diagnostics line | `fw16_test`, `t_fw16` |

The sketch still says `FW_VERSION "1.5.0"` and the app still ships the 1.5.0 image (see Z1 / Z2): a pad built from this source reports 1.5.0 with the 1.6 `caps`, and the app goes by `caps`.

## Suggested order

F9, F1, F2, F3 first (small, mostly protocol, big effect on how the app feels), then F7, F5, F6, F8, F4, F10, F11, with Z1 / Z2 last.
