# Feature audit - original spec vs. this repository

Every line of the original specification, where it lives in the code, and what proves it. "Emulator" means
`tools/emulator_test.py` (the real firmware running in QEMU, see `tools/README.md`); "App test" means
`tools/app_selftest.py` / `tools/macro_test.py` / `tools/giflib_test.py` (the real app, headless, against the built-in
simulated pad and a mock Tenor/GIPHY server).

**What no test here can prove:** anything that needs the physical board - USB enumeration on your PC, the real GC9A01
panel, real switches/encoder bounce, the real WS2812 timing, flash wear. The firmware has been compiled for the
Waveshare ESP32-S3-Zero profile on arduino-esp32 core 3.3.6 (your version) and boots in QEMU; everything that touches the
pins goes through the same code paths the emulator exercises, but the panel and the USB link are verified only by you
(`README.md` -> bring-up ladder, Dev tab -> *Full self-test*).

## Part 1 - hardware / pins

| Spec | Where | Proof |
|---|---|---|
| Display SCLK 12, MOSI 11, RES 10, DC 9, CS 8 | `User_Setup.h`, `platformio.ini`; `#error` in the sketch if TFT_eSPI is not configured for GC9A01 | compile on cores 2.0.9 / 3.1.3 / 3.2.1 / 3.3.6 / 3.3.12 |
| Backlight PWM on GPIO 7 | `PIN_BLK`, `backlightInit/Set` (LEDC; both the 2.x and 3.x API) | compile on 2.x and 3.x; Emulator `t_selftest_misc` checks the brightness command and its clamping (5..255). The PWM signal itself cannot be observed in QEMU. |
| K1..K5 = GPIO 1, 2, 4, 5, 6, active LOW, `INPUT_PULLUP` | `PIN_KEY[]`, Bounce2, `attach(..., INPUT_PULLUP)` | Emulator `t_gpio`, `t_inputs`, `t_virtual_input`; Dev tab *Keys + encoder* shows the live level |
| Encoder A 13 (interrupt), B 14, switch 15 | `PIN_ENC_A/B/SW`, `attachInterrupt(..., CHANGE)` with an IRAM-safe ISR | Emulator `t_virtual_input` (turns + press via the `input` command) |
| Native USB HID keyboard + media + CDC serial | `USB.begin()`, `Keyboard`, `ConsumerControl`, `USBCDC` | compile only; `#error` guards for wrong *USB CDC On Boot* / *USB Mode* |
| Onboard RGB LED (GPIO 21, WS2812) - added for bring-up | LED engine (`LM_AUTO/OFF/SOLID/BLINK/RAINBOW`) | Emulator `t_led`; you confirmed it lights on real hardware (CoreBringup) |

## Part 2 - firmware

| Spec | Where | Proof |
|---|---|---|
| Works 100 % standalone, no app | `DEFAULT_SLOT[]` (used whenever NVS holds nothing), own RTC clock | Emulator boots with an empty NVS: `t_first_boot_responsive`, `t_modes_render` |
| K1 copy, K2 paste, K3 undo (Ctrl or Cmd per OS), K4 play/pause, K5 mute, encoder = volume | `DEFAULT_SLOT[]`, `runSlot()`, `PRIMARY` = Ctrl or Cmd | Emulator `t_virtual_input` presses the keys and checks the firmware stays healthy; the simulator build has **no USB HID** (`HidStub`), so the actual key output is checked by you with Dev tab -> *Keyboard / media test* |
| Encoder press = circular overlay menu | `menuOpen`, the menu renderer | Emulator `t_virtual_input` (opens it, screenshot differs from the screen below) |
| 5 display modes: clock + date (analog and digital, RTC kept in sync by the app over USB; optional Wi-Fi NTP only when built with `DC_ENABLE_WIFI 1`), Pomodoro (25 min; K1 start/pause, K2 reset, encoder sets minutes), media dashboard, telemetry, GIF player from LittleFS | `sceneClock`, `scenePomo`, `sceneMedia`, `sceneTelemetry`, GIF engine on `/anim.gif` | Emulator `t_modes_render` renders modes 1-4 and checks each screen is not blank; `t_gif` plays mode 5; `t_virtual_input` drives the Pomodoro keys. Screenshots were checked by eye. Wi-Fi/NTP is not testable in QEMU. |
| Telemetry offline shows uptime / VCC | uptime always; VCC only if you wire a divider to an ADC pin (`VCC_SENSE_PIN`, default off) - **the ESP32-S3 cannot measure its own supply**, so free heap is shown instead when no divider exists | Emulator screenshot |
| Radial menu: Brightness (PWM), Master Volume (USB Consumer Control), Display Mode 1..5, Exit; auto-close after 4 s | `MENU_TIMEOUT_MS = 4000` | Emulator `t_virtual_input` (turn, enter, edit volume, long-press closes). The 4 s auto-close is a constant that no test waits for. |
| Encoder hold = next display mode | `ENC_HOLD_MS = 700`, `onEncLong()` | not separately tested (the test's `hold` closes the open menu) |
| `Preferences`: key maps, custom macros, mode, brightness persist | NVS keys `s0..s6` (slots), `mode`, `bright`, `os`, `kbl`, `pomo`, `ssid`/`wpass` | Emulator `t_remap_persistence` (writes, kills QEMU, restarts, reads back) |
| Serial JSON: `stats`, `remap` (combo / text / macro / media), file upload to `/anim.gif` | `handleCommand()`, `gif_begin/chunk/end` (base64, CRC32, atomic rename from `/anim.tmp`) | Emulator `t_gif`, `t_remap_persistence`; `t_fuzz` throws malformed lines at it |
| **Added, not in the spec:** safe mode after 3 crashes, background filesystem mount, request ids, bounded TX | see `README.md` "Step 2" | Emulator `t_safe_mode`, `t_first_boot_responsive` |

## Part 3 - desktop app (`companion_app.py`)

| Spec | Where | Proof |
|---|---|---|
| Auto-detect the ESP32-S3 CDC port on launch, auto-reconnect | `_monitor_loop`, `candidate_ports` (VID 303A, name hints, then probes every unknown port with `hello`) | `tools/app_selftest.py` (simulated port); Dev tab *Probe all ports*. Real USB detection is verified only on your machine. |
| CPU % / RAM % every 1000 ms via psutil in a background thread | `_telemetry_loop` | `tools/app_selftest.py` runs the loop against the simulator; Emulator `t_selftest_misc` sends `stats` to the real firmware |
| >= 50 actions in the 5 categories | 53 actions: Editing 10, Media 8, OS 12, Browser 10, Productivity & Dev 12, plus *Unassigned* | `tools/macro_test.py` asserts the per-category minimums |
| Macro creator: up to 4 modifiers + main key; text snippets; millisecond delays; sequences | `_build_macro`, `assign_combo/text/seq`, `seq_add_*` | `tools/macro_test.py`: Ctrl+Shift+Alt+GUI+T, text with Enter, combo/delay/text/media sequence, invalid key / non-ASCII / bad delay refused, upload, read-back |
| GIF: pick any `.gif`, crop + resize to 240x240, circular-mask preview, upload with progress bar to LittleFS | `load_gif_frames`, `fit_gif`, `upload_gif` | App test uploads and compares bytes with the simulated flash; Emulator `t_gif` plays it |

## Things you asked for later

| Request | Where | Proof |
|---|---|---|
| "should work when nothing is connected to the ESP" | The pad never needs a display, keys or app to answer the protocol; a failed display / filesystem is reported, not fatal; the app has **Dashboard -> Simulate pad** | Emulator runs with no display attached; App tests use the simulator |
| A GIF *library* to pick from, with the option to upload your own | **GIF Upload** tab: *Built-in* (16 generated animations), *My GIFs* (your folder, add / delete / open folder), *Online* (Tenor or GIPHY search + trending, your own free API key), plus *Select GIF file...* for any file | App test `tools/giflib_test.py` (mock Tenor + GIPHY server: search, trending, bad key, no network, non-GIF download, duplicate names, stale searches, upload byte-compare) |
| "Whatever is in WhatsApp" | WhatsApp's GIF picker is Tenor/GIPHY. There is **no public WhatsApp API**, so the *Online* tab uses those same sources directly. Nothing copyrighted is bundled in the repo. | same as above |
| Dev tab / light the LED / make sure it works | **Dev** tab (ports, LED, keys, display, HID, GPIO, system, terminal, full self-test, report) | `tools/app_selftest.py`; you confirmed link + LED on hardware |
| Mirror the PC's volume / playback on the pad | `HostMedia` + `_media_loop`; Linux `pactl`/`amixer`/`playerctl`, macOS `osascript`, Windows optional `pycaw`; sends `{"cmd":"media",...}` on change and every 10 s | `giflib_test.py` parses canned tool output and checks what is sent and that the virtual pad follows. **Windows (pycaw) and macOS are best-effort and untested here.** |
| Flash without the Arduino IDE | `firmware/flash.py` + `firmware/*.bin` (also Dev tab -> *0. Flash firmware*) | images boot in QEMU, byte-identical to the core's own `merged.bin`; the esptool step itself is not run here (no board) |

## Version 1.3 (app) / 1.2 (firmware): the "make it more robust and better" round

| Feature | Where | Proof |
|---|---|---|
| **3 key layers**, selectable from the dial menu / a key / the app / a profile | firmware `curLayer`, `slotKey(layer, i)`, `DEFAULT_SLOT[3][7]`, menu entry LAYER; app `edit_layer`, `cfg["layers"]`, layer selector on the Pad page | Emulator `t_layers` (separate key tables, defaults, switching, layer events, menu entry, badge on screen, selective reset, persistence in `t_remap_persistence`); `tools/layers_test.py` (editor, per-layer upload, read-back, config migration from the one-layer format) |
| **Per-program profiles** | `desk_lib/activewin.py`, `App._profile_loop`, Profiles page | `lib_test.py` (window detection with canned xdotool / xprop / osascript output, rule matching); `profiles_test.py` (rules, ordering, disabled rules, default layer, "this app focused" ignored, off switch). **Window detection on Windows / macOS is untested here.** |
| **More actions**: mouse (click / double / scroll / move / buttons), open website / program / file, type clipboard, notification, run command | firmware `ST_MOUSE`, `ST_HOST`, `ST_LAYER`; `desk_lib/hostactions.py`; Macros page | Emulator `t_new_actions` (every spec accepted, every bad one refused, a real key press emits the host event); `lib_test.py` + `layers_test.py` (the whitelist: unlisted actions, `file:` / `javascript:` links and switched-off shell commands are refused); `features_test.py` (the action builder, bad input, sequences) |
| **Info screen** (6th mode): now playing, weather, next calendar event, custom card, notification badges | firmware `sceneInfo`, `info_cards`; `desk_lib/feeds.py`; Info page | Emulator `t_info_screen` (no-data state, cards, rotation, limits); `lib_test.py` (Open-Meteo / geocoder against a mock server, ICS parsing incl. folded lines and all-day events, token-protected badge server); `info_test.py` (whole chain into the simulated pad, caching, loop, bad sources) |
| **4 GIF slots + rotation** | firmware `gifPath`, `gif_list`, `gif_cfg`; GIFs page "On the pad" | Emulator `t_gif_slots` (upload, list, select, rotation, delete, demo regeneration); `giflib_test.py` |
| **Key-press ring** on every screen | firmware `drawOverlays()`, twin `_draw_status()` | Emulator `t_layers` (badge) / screenshots; the ring itself is only checked by eye |
| **Firmware update from the app** | `App.flash_firmware`, `firmware/flash.py`, version check `fw_status()`, banner on Home | `features_test.py` (old / newer / unknown / ok states, banner). The flashing run itself needs a board. |
| **Wi-Fi OTA + NTP** (optional, **compiled out by default** via `DC_ENABLE_WIFI`) | firmware `ArduinoOTA`, `desk_lib/espota.py`; the app greys the Wi-Fi settings when the pad has no `wifi` capability | Emulator `t_recovery` / `t_layers` (the cable-only build refuses `wifi` / `ota` and does not advertise them); `features_test.py` (UI gating, firmware 1.1 compatibility); OTA client tested against a **fake** device only. **Never run with real Wi-Fi.** |
| **Backup / restore** | `desk_lib/backup.py`, Device page | `lib_test.py` (round trip, path traversal / non-GIF / foreign zips refused); `features_test.py` (real export from the simulated pad incl. read-back of all layers, restore into a broken config, damaged file refused) |
| **Macro sharing** (export / import one or all macros) | `macro_export` / `macro_import` | `features_test.py` (invalid macros skipped) |
| **Safe-mode self-recovery** | firmware `safe_retry`, `boot_opt`, `factory`, `info.safe_why`; Device page, Home banner | Emulator `t_recovery`, `t_safe_mode`; `features_test.py` |
| **Guided hardware test + printable report** | `desk_lib/wizards.py` `HardwareTest` | `features_test.py` runs every step against the simulator (LED, display, five keys, dial, keyboard, self-test, a failing step, the HTML report). Whether the LED / screen really look right is your answer in the dialog. |
| **First-run setup wizard** | `SetupWizard`, offered once on the first real connection | `features_test.py` clicks through all pages |
| **Keystroke recorder** | `desk_lib/recorder.py`, Macros page | `lib_test.py` (combos, text runs, delays, caps, unsupported keys), `features_test.py` (start / stop with a fake listener). Real key capture needs `pynput`. |
| **Command palette** (Ctrl+K), **usage statistics**, **light / dark theme**, sidebar navigation | `CommandPalette`, `_count_use`, `desk_lib/ui.py` | `features_test.py` (palette), `layers_test.py` (usage events indirectly); looks checked on screenshots of every page in both themes |
| **Installers** | `packaging/build.py`, `.github/workflows/release.yml` | Linux build produced and launched headless here (that run found and fixed a missing Pillow hidden import); Windows / macOS builds are made by the workflow and **have not been run**. |
| **CI** | `.github/workflows/ci.yml` | Written from the commands that were run locally; **the workflow itself has not been executed** (no GitHub Actions access from here). |
| A bug found on the way | Macro Creator's delay field shared a variable with the Virtual Pad's test delay -> every virtual key press waited 200 s | `macro_test.py` regression test (fails on the old code) |

## Version 1.4 (app) / 1.3 (firmware): the "overkill" branch

Everything here needs **no extra hardware**. Items marked *firmware 1.3* are ignored politely by a 1.2 pad (the app checks the `caps` list in `hello`).

| Feature | Where | Proof |
|---|---|---|
| **Snippets with variables** (`{date}` `{time}` `{datetime}` `{date:%d.%m.%Y}` `{weekday}` `{clipboard}` `{counter:name}` `{uuid}` `{random:1-6}` `{user}` `{host}`) | `desk_lib/textops.py`, host op `snippet`, Macros page "Type a snippet" | `tools/textops_test.py` (every variable, escapes, unknown variables stay visible, persisted counters) |
| **Clipboard transforms** (21: upper/lower/title/sentence, trim, one line, snake/kebab/camel/Pascal, JSON pretty/minify, URL, Base64, sort/dedupe/reverse lines, quote, count) | `textops.TRANSFORMS`, host op `clip` | `textops_test.py` (every transform; bad JSON / Base64 types nothing and says why); `features_test.py` (the picker) |
| **Scheduled actions**: daily / weekdays / weekends at a time, every N minutes, once; do: switch layer, screen, brightness, LED, open website / program / file, type a snippet, run a command, show a reminder | `desk_lib/scheduler.py` (pure), `desk_lib/automation.py` (page + execution), Automation page; a rule that was due while the app was closed is skipped, never run late | `tools/scheduler_test.py` (grace window, weekdays, once-rules retire, validation); `tools/automation_test.py` (form -> rule -> simulated pad changes layer, host rules join the whitelist, persistence, junk dropped) |
| **Local API + command line**: `deskcompanion_cli.py layer 2`, `led ff8800`, `card ...`, `badge mail 3`, `press 1`, `notify ...`; off by default, 127.0.0.1 only, secret token, browser requests (Origin header) and foreign Host headers refused | `desk_lib/bridge.py`, `deskcompanion_cli.py`, Automation page | `tools/bridge_test.py` (auth, Origin / Host protection, bad input never reaches the app, 413, CLI exit codes); `automation_test.py` (the real app + simulated pad through HTTP, new token locks the old one out) |
| **More Info cards**: countdown to a date, world clock (1-3 zones), git repository status, latest GitHub Actions run, crypto price | `desk_lib/extras.py`, Info page "More cards" | `tools/extras_test.py` (all card builders with injected git / network); `info_test.py` (real `git` in a temp repo, the loop sends extras alone, failing services are reported not fatal) |
| **Hold and double-tap key actions** (*firmware 1.3*): a key with a hold / double-tap action decides on release; keys without one still act instantly | firmware `GestureFsm`, `remap {"gesture":"hold"|"double"}`, `getkeys` flags; app `desk_lib/padextras.py` GesturePanel | Emulator `t_gestures` (the state machine with synthetic timing: tap / hold / double / triple / both / slow taps, storage per key + layer + gesture, virtual gestures run the right action, persistence, clear, reset, factory); `fw13_test.py` (assign, upload keeps the pad in step, stale gesture removed, whitelist). **The real GPIO timing path is read-through only - no board here.** |
| **Dial acceleration** (*firmware 1.3*): fast turns repeat the action 2-6x | `accelMult`, `settings {"dial_accel"}` | Emulator `t_settings` (levels, per-detent speed, 24-run cap, both directions) |
| **Clock styles**: classic, digital, binary (BCD), minimal (*firmware 1.3*) | `sceneClockAlt`, `settings {"clock_style"}` | Emulator `t_settings` (screenshots of all four must differ and not be empty; saved in `emulator_out/clock_style_*.png`). The app's virtual twin still draws the classic face. |
| **Screensaver**: starfield / matrix rain / dim drifting clock after 1 min - 1 h idle; any key or dial turn wakes it; never on the GIF screen or a running timer (*firmware 1.3*) | `sceneSaver`, `settings {"saver_s","saver_style"}` | Emulator `t_settings` (starts, three styles render, wakes, returns, GIF screen excluded, off) |
| **Night dimming**: cap the backlight between two hours (wraps midnight); the user's brightness is kept (*firmware 1.3*) | `effBright()`, `settings {"night_*"}` | Emulator `t_settings` (inside / outside the window, loop restores it by itself, non-wrapping window, from == to, cap never raises) |
| **Pad behaviour card** in the app (all the settings above, filled from the pad on connect, greyed out with a reason on older firmware) | Device page, `padextras.BehaviourCard` | `fw13_test.py` (against a 1.3 and a 1.2.0 simulated pad) |
| **Display start-up deadline**: a stalled panel can no longer silence the pad | firmware `dispinit` task, `info.disp_why` | Compile-checked and emulator suite green; **the stall itself could not be reproduced without the board** |

## Batch 2 on `overkill` (app 1.4 / firmware 1.4)

| Feature | Where | Proof |
|---|---|---|
| **Macro scripts**: loops (`repeat 3 ... end`), conditions (`if window "Excel"`, `process`, `clipboard`, `time 09:00-17:00`, `weekday mon-fri`, `not`, `else`), variables (`set x = {clipboard}`), sub-scripts (`run other`, 5 levels), `stop`, dry run; limits stop runaway scripts (100 repeats, 500 commands, 60 s of waiting) | `desk_lib/scripting.py`, Scripts page, host op `script` (the pad asks, the app runs it) | `tools/scripting_test.py` (every command / condition / error with its line number, limits); `tools/scripts_test.py` (editor, dry run, key -> pad -> app runs it with a recording key sender, one at a time, Stop ends a long wait, shell switch) |
| **Ready-made key layouts**: Zoom / Teams / Google Meet, Photoshop-Krita, Premiere, Excel, Word-Docs, Blender, VS Code, Browsing - fill a layer and add the program rule | `desk_lib/presets.py`, Profiles page; 50+ new library actions (Meetings, Creative & Video, Writing, Spreadsheet & 3D, View & Windows) | `profiles_test.py` (every preset resolves for Windows and macOS, applies to a layer, adds one rule, reaches the simulated pad). Shortcuts are the programs' defaults. |
| **Automatic backups** (a snapshot after every meaningful change, newest 15 kept) with a restore dialog | `desk_lib/autobackup.py`, Device -> Backup | `tools/system_test.py`, `tools/system_app_test.py` |
| **Start with my computer** (Windows Run key, macOS LaunchAgent, Linux autostart) and an optional **tray icon** (`pip install pystray`) | `desk_lib/autostart.py`, `desk_lib/tray.py` | `system_test.py` (all three platforms with a fake registry / temp home, fake pystray menu); the real tray / registry are **untested here** |
| **LED follows the CPU load** | `padextras.cpu_color`, telemetry loop | `system_app_test.py` |
| **Screens 7-12 on the pad** (*firmware 1.4*): stopwatch with laps, breathing guide (4-7-8, box, 5-5, 4-6), dice / coin / 8-ball, reaction test, snake (dial steers), habits (5 daily toggles, 8-day history, streaks) - choose which screens the dial menu / long press visits | firmware `modeMask`, `modeKey()`, `cmd:"screens"`, `cmd:"habits"`; Device -> Screens | Emulator `t_screens` (every screen renders and differs; stopwatch start / lap / stop / reset, other keys keep their macros; breathing patterns; every dice range; reaction test through wait / early / go / result; snake moves, turns with the dial, dies, restarts, pauses; habits toggle, streaks over day changes and a power cycle; screen mask in menu and long press) |
| **Reminders** (*firmware 1.4*): up to 3 full-screen nudges every N minutes, any key dismisses; the app also shows a notification | firmware `remTick()`, `cmd:"reminders"`; Device -> Screens | Emulator `t_screens` (validation, event, screenshot, dismiss, persistence); `fw14_test.py` (event becomes a notification) |
| **Dial pressed + turned** (*firmware 1.4*): its own two actions per layer | slots 8 / 9, `getkeys "pt"`; Automation -> Key gestures | Emulator `t_actions14`; `fw14_test.py` (assign, upload, stale one removed) |
| **Alternating and random keys**, **panic key** (*firmware 1.4*) | firmware `runSpecJson()`, types `toggle` / `random` / `panic`; Automation -> Alternating and random keys | Emulator `t_actions14` (state per key, validation incl. no nesting, random picks, panic cancels a running macro and works as a macro step); `fw14_test.py` |
| **Mouse wheel with Ctrl / Shift / Alt, and sideways scroll** (*firmware 1.4*) -> zoom, horizontal scroll actions | `parseMouse` `mods` / `h` | Emulator `t_actions14` (accepted / refused specs, runs without a crash). **Whether the PC really zooms is untested.** |
| **LED effects** breathe / fire and a notification **alert** flash (*firmware 1.4*); also via the local API and scheduled actions | `cmd:"led"` | Emulator `t_actions14`; `scheduler_test.py`, `bridge_test.py` |

**Not built yet** (still on the idea list): key chords and tap-dance, sticky modifiers, dial as window switcher / per-app volume, per-layer wallpapers, theme packs, screen transitions, boot animation, display rotation, QR code / spectrum / album-art / lyrics screens, sound-reactive LED, localisation, multi-pad, accessibility pass, firmware rollback, latency monitor.

## Known limits (honest list)

* Text macros type **US-layout ASCII** unless you pick another layout in the Device tab (needs core >= 3.1).
* `fr_CH` / `ja_JP` keyboard layouts need core >= 3.3.8 / 3.3.9 and are not offered.
* The pad cannot read the PC's volume by itself (HID has no read-back); without the app it keeps a local estimate.
* Windows media sync needs `pip install pycaw`; without it the switch is greyed out and the pad keeps its estimate.
* Tenor / GIPHY need a free API key you create yourself (links in the *Online* tab). The key is stored in
  `~/.desk_companion.json` in plain text.
* GIF size is bounded by the free flash (about 1.4 MB with the default partition scheme); the app reduces colours / frames
  automatically until it fits and tells you what it did.
