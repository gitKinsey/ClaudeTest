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
| 5 display modes: clock + date (analog and digital, RTC, optional Wi-Fi NTP), Pomodoro (25 min; K1 start/pause, K2 reset, encoder sets minutes), media dashboard, telemetry, GIF player from LittleFS | `sceneClock`, `scenePomo`, `sceneMedia`, `sceneTelemetry`, GIF engine on `/anim.gif` | Emulator `t_modes_render` renders modes 1-4 and checks each screen is not blank; `t_gif` plays mode 5; `t_virtual_input` drives the Pomodoro keys. Screenshots were checked by eye. Wi-Fi/NTP is not testable in QEMU. |
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

## Known limits (honest list)

* Text macros type **US-layout ASCII** unless you pick another layout in the Device tab (needs core >= 3.1).
* `fr_CH` / `ja_JP` keyboard layouts need core >= 3.3.8 / 3.3.9 and are not offered.
* The pad cannot read the PC's volume by itself (HID has no read-back); without the app it keeps a local estimate.
* Windows media sync needs `pip install pycaw`; without it the switch is greyed out and the pad keeps its estimate.
* Tenor / GIPHY need a free API key you create yourself (links in the *Online* tab). The key is stored in
  `~/.desk_companion.json` in plain text.
* GIF size is bounded by the free flash (about 1.4 MB with the default partition scheme); the app reduces colours / frames
  automatically until it fits and tells you what it did.
