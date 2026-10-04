# Desk Companion - ESP32-S3 macro pad (Waveshare ESP32-S3-Zero)

A round-screen macro pad: 5 keys + a rotary encoder, GC9A01 1.28" display, USB keyboard / mouse / media keys, and a desktop
companion app. The firmware works standalone; the app is only needed to remap keys, build macros, pick / upload a GIF
(built-in library, your own folder, or Tenor / GIPHY search), mirror your PC's volume on the pad, and for **Pad & App → Diagnostics**
(bring-up and diagnostics, shown with the Advanced switch).

**Companion app 2.0 (Qt) and the firmware 1.6 sketch (branch `overkill`)** - the app is rewritten in PySide6 (see *The app, page by page*); `DeskCompanion.ino` gains optional helpers for it, each announced in `caps`: the pad reports its own screen / brightness / layer changes, key maps upload in a few batched requests, the pad shows the program name, your layer names and app messages, follows the app's accent colour, and reports loop-time counters (`docs/FIRMWARE_1_6_CHECKLIST.md`, protocol in `docs/PROTOCOL.md`). **Status:** the 1.6 C++ has not been compiled or run by me (no ESP32 toolchain where it was written) - CI compiles it, `tools/fw_snippet_check.py` type-checks the new functions against ArduinoJson, and the simulator / app side is covered by tests; the prebuilt images in `firmware/` are still **1.5.0** until `.github/workflows/firmware-images.yml` has built new ones and you have tried them on a pad.

**Version 1.5 / firmware 1.5 (branch `overkill`)** adds, on top of everything in 1.4: computer actions (per-program volume, Do-Not-Disturb, audio output, microphone, screenshots, translate, AI, web calls, window layouts, clipboard history, plugins), scripts with more commands, smarter profiles, new Info cards (quotes, birthdays, ping / website checks, lyrics, progress bars, rings), a sound-reactive LED, accent colours / languages / update check in the app, and on the pad: colour themes, display rotation, key chords, triple tap, key repeat, pad functions (sticky modifiers, popup menu, window switcher on the dial), eight more screens (Pong, Breakout, Flappy, Life, pixel pet, Simon, diagnostics, sound bars), more clock faces and screensavers (see `docs/FEATURES.md`; protocol in `docs/PROTOCOL.md`).

**Version 1.3 / firmware 1.2** adds: **3 key layers**, **per-program profiles** (the layer follows the program you use), a 6th screen
(**Info**: now playing, weather, next calendar event, notification badges), **mouse and computer actions** (click, scroll, open a
website / program, type the clipboard), **4 GIF slots with rotation**, a key-press ring effect, one-click **firmware update**,
**backup / restore**, safe-mode **recovery** tools, a **guided hardware test** with a printable report, a **setup wizard**, a keystroke
**recorder**, a **command palette** (Ctrl+K), usage statistics, and a new black-and-white interface with a light theme.

```
CoreBringup/CoreBringup.ino    STEP 1  tiny sketch, no libraries: LED + USB serial link + wiring test
DeskCompanion/DeskCompanion.ino STEP 2  the full firmware (display, keys, macros, GIFs, ...)
companion_qt.py                 desktop app, PySide6 / Qt (pages: Overview, Keys, Display, Rules, Scripts, Pad & App); ui_qt/ = widgets, core/ = the toolkit-free engine
desk_lib/                       the app's logic split into testable modules (feeds, backup, OTA client, profiles, recorder, scripting, i18n)
packaging/build.py              builds a double-click app (PyInstaller);  .github/workflows: CI (tests, compile matrix, QEMU) + release
firmware/                       prebuilt images (CoreBringup.bin, DeskCompanion.bin) + flash.py - no Arduino IDE needed
User_Setup.h, platformio.ini    TFT_eSPI pin setup / PlatformIO alternative
docs/FEATURES.md                every item of the original spec -> where it is implemented -> what proves it
tools/                          test suites (firmware in QEMU, app headless); developers only, no hardware needed
```

## The bring-up ladder - do it in this order

The idea: prove each layer on its own, so when something is wrong you know *which* layer.

| Step | What | Proves |
|---|---|---|
| 0 | Flash **CoreBringup** | board, USB cable, driver, Arduino settings, the onboard LED |
| 1 | Run the app, open the **Dev** tab | the PC <-> pad link (JSON over USB serial), pins, keys, HID |
| 2 | Flash **DeskCompanion** (full firmware) | display, filesystem, key actions, GIFs |
| 3 | Pad & App → Diagnostics → *Full self-test* | everything, with a PASS/FAIL list |

### Step 0 - Arduino IDE setup (Waveshare ESP32-S3-Zero)

1. **Boards manager**: File -> Preferences -> *Additional boards manager URLs*:
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
   Install **esp32 by Espressif Systems**. **3.1.0 or newer is recommended** (keyboard-layout support);
   2.0.x also builds (the layout feature is compiled out).
2. **Board**: *Waveshare ESP32-S3-Zero* (if your core lists it) or *ESP32S3 Dev Module*.
3. **Tools menu** - these matter:

   | Setting | Value |
   |---|---|
   | USB Mode | **USB-OTG (TinyUSB)**  (needed for the keyboard; "Hardware CDC and JTAG" still gives you the serial link) |
   | USB CDC On Boot | **Enabled** |
   | Flash Size | 4MB (32Mb) |
   | Partition Scheme | **Default 4MB with spiffs** |
   | PSRAM | **Disabled**  (a wrong PSRAM type makes S3 boards boot-loop) |
   | Upload Mode | UART0 / Hardware CDC |

4. **Download mode** (needed for the first flash, and whenever the sketch is not running): hold **BOOT**, plug in USB
   (or tap RESET while holding BOOT), release BOOT. A *new* COM port appears (Espressif, `303A:1001`) - select it and upload.
   After the upload, unplug / re-plug (or press RESET): the port number usually **changes** again.
   Once a pad runs this firmware you can skip the button: Pad & App → Diagnostics → *Reboot into download mode*.

### Step 0a (alternative) - flash prebuilt images, no Arduino IDE at all

`firmware/` contains prebuilt images (`CoreBringup.bin`, `DeskCompanion.bin` = cable-only, 42 % of the app slot, `DeskCompanion-wifi.bin` = with Wi-Fi, 90 %), built from this repo's current sources with arduino-esp32 core **3.3.6** for the
*Waveshare ESP32-S3-Zero* board profile (USB-OTG/TinyUSB, USB CDC On Boot enabled, 4 MB flash, DIO 80 MHz bootloader,
default partition scheme, PSRAM off), and a flasher:

```
pip install esptool pyserial
python firmware/flash.py --image core     # CoreBringup (step 1)
python firmware/flash.py --image full     # DeskCompanion (step 2) - cable-only build, the default
python firmware/flash.py --image wifi     # same firmware built with DC_ENABLE_WIFI=1 (NTP + Wi-Fi update); only if you want those
```
Put the board in download mode first (hold BOOT while plugging in USB). If the pad already runs one of these firmwares the
script reboots it into download mode by itself. The same thing is a button in the app: **Pad & App → Diagnostics → 0. Flash firmware** (or the *Firmware* tab).
`python firmware/flash.py --list` shows the serial ports (the board in download mode is `303A:1001`).

* Flashing an image rewrites the bootloader, partition table **and the settings area**, so saved key mappings are reset
  to the defaults (re-upload them from the app). A GIF stored in the filesystem partition is left alone.
* `firmware/SHA256SUMS` lists the checksums; the images are byte-identical to what Arduino IDE 2 / arduino-cli produce
  as `*.ino.merged.bin` for the same settings, just without the 4 MB of trailing padding.
* *Verified here:* the sources compile for this exact profile, the images pass `esptool image-info`, the bootloader loads
  and starts the app in QEMU, and the same firmware logic passes the emulator suite. *Not verified here (no board):* USB
  enumeration on your PC, the real panel, the real flashing run. If the full image misbehaves, flash `core` first - it
  separates "board / USB / driver" problems from "display / filesystem" problems.

### Step 0b - flash CoreBringup (Arduino IDE)

Open `CoreBringup/CoreBringup.ino` (the folder name must stay `CoreBringup`). **No libraries needed.** Upload.

**The LED tells you what is happening** (WS2812 on GPIO21 - the tiny RGB LED next to the USB port):

| LED | Meaning |
|---|---|
| purple | sketch started |
| blue | USB serial is up |
| red, green, blue, white (0.25 s each) | LED self-test |
| dim green blip every 3 s | alive, waiting for the app |

No LED at all? -> the sketch did not run. See *Troubleshooting*.

### Step 1 - the app

```
pip install PySide6 pyserial psutil pillow
python companion_qt.py
```
Optional extras, each switches on one feature and is greyed out with the reason when missing: `pynput` (recording, global hotkey, typing text), `pystray` (tray icon), `qrcode` (QR codes on the pad), `sounddevice numpy` (sound-reactive LED / sound bars), `pycaw` (Windows volume), `esptool` (firmware update).
Plug the pad in (before or after, any order). The app finds Espressif USB devices every second and connects by itself.
Open the **Dev** tab:

* **1. Serial ports** - every port with `VID:PID`. Your pad should show `303A:xxxx`. *Probe all ports* sends `hello`
  to each and shows what came back (useful when the app does not connect by itself).
* **2. LED** - sliders, colour buttons, blink / rainbow / auto. *This is the "light up the LED" proof.*
* **3. Keys + encoder** - switch on *Live events*, press the physical keys: the indicators light up.
  *Press K1..K5 / Dial* buttons trigger the same code as the physical keys.
* **4. Display** - fill colours, colour bars, grid, text; *Screenshot* downloads what the firmware thinks is on screen.
* **5. Keyboard / media test** - makes the pad type text / press media keys / Ctrl+A on this PC.
* **6. GPIO tester** - read / pull-up / drive any free pin, scan all pins (wiring checks).
* **7. System** - reboot, reboot into download mode, *Verify keys on pad*.
* **Top bar**: *Ping x5*, *Device info*, *Full self-test*, *Copy diagnostic report* (paste it to whoever is helping you).
* **Terminal**: every line on the wire in both directions, plus anything the pad prints that is not protocol JSON
  (boot text, panics). You can type raw JSON lines.

No hardware yet? **Overview → Simulate pad (no hardware)** connects the app to an in-process stand-in that speaks the
same protocol, so the whole app (including Diagnostics) can be tried.

### Step 2 - the full firmware

1. Libraries (Library manager): **TFT_eSPI** (Bodmer), **AnimatedGIF** (bitbank2), **Bounce2**, **ArduinoJson 7.x**.
2. Copy `User_Setup.h` over `<sketchbook>/libraries/TFT_eSPI/User_Setup.h`.
3. Open `DeskCompanion/DeskCompanion.ino`, upload.

What the full firmware does differently from a plain sketch - **the core comes first**:

1. LED purple -> blue (USB up; the PC sees the pad **before** any display / filesystem work happens) -> cyan (filesystem) -> amber (display) -> heartbeat.
2. Every subsystem reports its own init result (`info` / Diagnostics), a failed display or filesystem never takes the USB link down.
3. **Safe mode**: three crashes / watchdog resets in a row boot the pad with the display and GIF engine disabled and a **red double blink**, so it stays reachable from the app (Diagnostics shows `SAFE MODE`, the reset reason and the boot log). A power cycle or a clean reboot tries the full firmware again.
4. LED heartbeat colour: **green** = all good, **amber** = a subsystem failed, **red** = safe mode.

### Step 3 - GIFs, macros, PC volume (companion app)

* **GIF Upload tab -> GIF library.** Three views:
  * *Built-in* - 16 generated animations (heartbeat, spinner, fire, plasma, radar, ...), nothing to download.
  * *My GIFs* - a folder of your own GIFs (`~/.desk_companion_gifs`, or `DESK_COMPANION_GIFS`): *Add GIF...*, right-click a
    tile to delete it, *Open folder*.
  * *Online* - search or browse trending GIFs from **Tenor** or **GIPHY** (the sources behind WhatsApp's / most chat apps'
    GIF pickers; WhatsApp itself has no public API). Both need a **free API key** you create once
    (*Get a free key* opens the sign-up page); it is stored in `~/.desk_companion.json`. Click a result to preview it.
  * *Select GIF file...* takes any file from disk; by default a copy is kept in My GIFs (switch it off with the checkbox).
  The app crops to a square, resizes to 240x240, masks the circle, shrinks colours / frames until it fits the pad's flash,
  shows the result on the round preview, and *Upload to pad* sends it with a progress bar and CRC check.
* **Keys → Build / Sequence** - up to 4 modifiers + a key, text snippets (US-layout ASCII), delays, and sequences of these.
* **Pad & App → Behaviour → "Mirror this PC's volume / playback on the pad"** - the pad's media screen follows the real volume, mute and
  play state (every change, plus a refresh every 10 s). Linux: `pactl` or `amixer` (+ `playerctl`); macOS: built in;
  Windows: `pip install pycaw` (best effort, untested by the author - without it the switch is greyed out and the pad
  keeps counting the volume keys on its own).

Default key map (works with no app): K1 copy, K2 paste, K3 undo, K4 play/pause, K5 mute, dial = volume, dial click = radial
menu (brightness / volume / mode / exit), dial hold = next display mode. Modes: clock, focus timer (K1 start/pause, K2 reset,
dial = minutes), media dashboard, system telemetry, GIF.

## The app, page by page

| Page | What it does |
|---|---|
| **Overview** | Connection (port list, Connect / Disconnect, auto-connect, *Simulate pad*), pad health (firmware, layer, flash, last reset, display, keyboard, storage, temperature), live CPU / RAM telemetry, quick actions (upload everything, setup wizard, hardware test, GIF, palette) and usage statistics. Banners appear when the firmware is older than the app, the pad is in safe mode, it cannot be reached, or there is a tip for you. |
| **Keys** | The live twin of the device (drag and drop, wheel and click the dial, right-click a key), **Layer 1 / 2 / 3**, *Upload to pad*, undo / redo. The action library (search, 9 categories) and an inspector with **Key map** (keyboard-friendly rows, share codes, macro export / import), **Build** (key combinations, text snippets, computer / mouse / layer actions), **Sequence** (with delays and a keystroke recorder), **Gestures** (hold, double / triple tap, chords, dial clicks, alternating / random keys) and **Test** (run on this PC, delay, sandbox, virtual screen). |
| **Display** | **GIFs** (built-in 16, My GIFs, Online via Tenor / GIPHY with your own free key; four pad slots, rotation), **Info** (now playing, weather, calendar, custom card, more cards, notification badges over `http://127.0.0.1`, preview), **Screens** (which of the 20 screens the dial visits, three reminders, five habits) and **Look** (brightness, mode, themes, rotation, dial, clock, screensaver, night dimming, dim while locked, LED effects). |
| **Rules** | **Programs** (the pad's layer follows the program in front; rules with time / days / game mode; ready-made layouts), **Schedules**, **Computer** (AI key, screenshots, clipboard history, window layouts) and, with *Advanced*, **API & CLI** and **Plugins**. |
| **Scripts** (Advanced) | Macro scripts with loops, conditions, variables and sub-scripts: check, dry run, run, ten saved versions, templates, import from an address, assign to a key. |
| **Pad & App** | **Behaviour** (host OS, keyboard layout, sync time, autostart, tray, shell commands), **Firmware** (version check, one-click USB update, experimental Wi-Fi update), **Recovery**, **Backup** (zip, automatic backups, macros, share codes), **This app** (light / dark / system, accent colour, size, language, reduce motion, Advanced switch, hotkey, updates, tips, latency), **Diagnostics** (Advanced: ports, LED, keys, display, keyboard test, GPIO, terminal, full self-test, report) and the activity **Log**. |

Everywhere: **Ctrl+K** opens the command palette (jump to a page, upload, switch layer / screen, toggle Advanced, open the mini pad, run the wizards). The rail on the
left is a full sidebar on wide windows, icons when narrower and a top bar on narrow ones; the window can be any size from 640 x 520 and the
pages rearrange (columns, then chip tabs) instead of scrolling. The **mini pad** is a small always-on-top twin of the device. *Overview -> Setup wizard* walks a new pad through connect / firmware / LED + display check / GIF choice, and
*Overview -> Guided hardware test* checks LED, display, the five keys, the dial, USB keyboard output and the self-test, then saves an HTML report
you can print.

### Layers, in short

Every key (K1-K5, dial right, dial left) has three assignments - one per layer. The layer is chosen from the dial menu (the new **LAYER**
entry), with a key bound to *Next / Previous / Layer N*, from the app, or automatically by a **profile**. The pad shows an `L2` / `L3`
badge, flashes its LED (cyan / magenta / amber) and tells the app. Defaults: layer 1 = copy / paste / undo / play / mute, layer 2 = media
(previous / play / next / stop / mute), layer 3 = browser (back / forward / reload / new tab / close tab, dial = page down / up).

### Computer actions and safety

The pad cannot launch programs itself, so *open website / start program / open file / type the clipboard / notify / run command* are sent to the
app as events. **The app only runs an event whose exact action is part of YOUR key maps or saved macros** (so a fake USB device cannot make it do
anything), only `http(s)` and `mailto` links open, and shell commands additionally need the switch *Device -> Allow the pad to run shell commands*
(off by default). While the guided hardware test runs, such actions are ignored. These actions need the app to be running; plain key and
mouse actions work without it.

### Wi-Fi: optional, OFF by default (the pad is a cable device)

The ESP32-S3 (and the ESP32-S3-Zero) does have Wi-Fi and Bluetooth LE, but everything in this project works over the USB cable, and the pad keeps
its clock through the app. The two Wi-Fi extras - **NTP time sync** and the **Wi-Fi firmware update (OTA)** - therefore sit behind one switch at the
top of `DeskCompanion.ino`:

```cpp
#define DC_ENABLE_WIFI 0     // 0 = no Wi-Fi code at all (default)    1 = include NTP time sync + Wi-Fi OTA
```

(or build with `-DDC_ENABLE_WIFI=1`; `-DDC_HAS_OTA=0` keeps time sync but drops the update). With 0 the firmware contains no Wi-Fi code, the
`wifi` / `ota` commands answer `wifi_disabled`, `hello` does not list the `wifi` capability, and the app greys out those settings with an
explanation. Bluetooth is not used at all. With 1: Device -> Firmware: save your Wi-Fi, set an OTA password, *Enable OTA*, wait ~30 s,
*Update over Wi-Fi*. The firmware side is the standard `ArduinoOTA` and the app contains a small client for its protocol, **tested against a fake
device only - not on real hardware**; keep the USB way as the fallback.

### Installers

`python packaging/build.py` makes a double-click app with PyInstaller (Windows `.exe`, macOS `.app`, Linux binary) including the firmware images
and the flasher; pushing a `v*` tag runs `.github/workflows/release.yml`, which builds all three and attaches them to a GitHub release. The Linux
build was built and started headless here; the Windows and macOS builds are produced by that workflow and have **not** been run by the author.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Upload fails / no port | not in download mode | hold BOOT while plugging in; try another cable (charge-only cables have no data lines) |
| Port appears as `303A:1001` and stays there | board is in the ROM bootloader, or the sketch uses *Hardware CDC and JTAG* | re-plug without BOOT. For the keyboard use *USB Mode = USB-OTG (TinyUSB)* |
| App says "found on COMx ... does not answer" | wrong USB settings, a different sketch, or the port is held by the Serial Monitor | close the Serial Monitor; Diagnostics → *Probe all ports*; check *USB CDC On Boot = Enabled* |
| No LED, nothing happens after upload | sketch is boot-looping (often PSRAM = OPI/QSPI selected by mistake) or the upload was not completed | set PSRAM = Disabled, re-upload; watch the port list - a port that appears / disappears every few seconds = boot loop |
| LED blue but the app does not connect | serial link fine, but another program holds the port, or Windows needs the CDC driver | Device Manager -> Ports: *USB Serial Device*; close other terminals |
| LED works, screen stays dark | display wiring / `User_Setup.h` not installed / wrong colour setup | Diagnostics → Display tests; check `User_Setup.h` was copied into the TFT_eSPI library; try `USE_HSPI_PORT` there |
| Screen colours inverted / red-blue swapped | panel variant | uncomment `TFT_INVERSION_ON/OFF` or `TFT_RGB_ORDER TFT_BGR` in `User_Setup.h` |
| Keys do nothing | wiring, or HID not enumerated | Diagnostics → Keys: indicators must light up; Keyboard test must type. HID needs *USB Mode = TinyUSB* |
| Red double-blink | safe mode (3 crashes in a row) | Diagnostics → *Device info*: look at `reset`, `boot`; usually a bad display / wiring or wrong board settings |
| `'File' does not name a type`, or "TFT_eSPI is not configured for the GC9A01" while compiling | the TFT_eSPI library still has its default `User_Setup.h` (it enables `SMOOTH_FONT`, which hides the global `File` type) | copy this project's `User_Setup.h` over `<sketchbook>/libraries/TFT_eSPI/User_Setup.h` (Step 2). The firmware also uses `fs::File` explicitly, so it compiles either way - but without our `User_Setup.h` the display is configured for the wrong panel |
| Online tab: "the server answered HTTP 401/403" / "enter your ... API key first" | missing or wrong Tenor / GIPHY key | create a free key with *Get a free key*, paste it, search again. A key from the other provider will not work - pick the matching provider in the menu |
| Online tab: "no connection" | no internet / a firewall or proxy blocks `tenor.googleapis.com` or `api.giphy.com` | the Built-in and My GIFs views work offline |
| `KeyboardLayout_xx not declared` while compiling | very old/new core mismatch | use core 3.1.0+; or 2.0.x (layout feature is compiled out automatically) |

Windows check: Device Manager should list, under *Ports*, a **USB Serial Device (COMn)** and, under *Keyboards*,
a **HID Keyboard Device** while the full firmware runs.

## Serial protocol (JSON lines, USB CDC, 115200 - the baud rate is ignored by native USB)

One JSON object per line. Every request may carry `"id": n`, which is echoed in the reply (the app uses it to match
replies). Replies have `"ok": true|false` (`"err"` on failure). Messages **without** `ok` are asynchronous events.

| Command | Purpose |
|---|---|
| `hello` | identity + health (`fw`, `hid`, `disp`, `fs`, `safe`, ...) |
| `ping` (`t`) | round-trip test |
| `info` | chip, heap, reset reason, crash counter, USB mode, subsystem status, boot log |
| `echo` (`data`) | returns `data` unchanged |
| `led` (`r g b` / `hex` / `mode`: auto,off,solid,blink,rainbow; `save`) | onboard RGB LED |
| `gpio` (`pin`, `op`: read,pullup,input,low,high,scan) | wiring tests; GPIO 0, 19, 20, 21 and 26-32 are protected |
| `inputs` | current key / encoder levels |
| `events` (`val`) | stream `{"evt":"key","k":1,"v":1}`, `{"evt":"enc","d":1,"pos":n}`, `{"evt":"encsw","v":1}` |
| `input` (`k` 1-5 / `turn` / `click` / `hold`) | virtual key press through the real UI/action code |
| `run` (`type`,`val`) | execute an action now (keyboard test) without saving it |
| `display` (`test`: fill,bars,grid,text,off) / `snapshot` | test patterns / frame-buffer download |
| `selftest` | NVS + filesystem + heap + LED + display self-test |
| `reboot` (`mode`: normal,download) | restart (download = ROM flasher, no BOOT button) |
| `remap` / `reset_keys` / `getkeys` | key slots 1-5 = K1-K5, 6/7 = dial right/left; `getkeys` returns length + CRC for read-back |
| `brightness`, `mode`, `os`, `layout`, `time`, `wifi`, `media`, `stats` | settings / telemetry |
| `gif_begin` (`slot` 0-3) / `gif_chunk` / `gif_end` / `gif_abort` / `gif_delete` (`slot`) | chunked GIF upload with CRC32 into one of 4 slots |
| `gif_list` / `gif_cfg` (`rot` seconds, `slot`) | what is stored, rotation interval, which slot plays |
| `layer` (`val`: 0-2, `next`, `prev`) | switch the pad's key layer; the pad also emits `{"evt":"layer","n":1}` |
| `remap` / `reset_keys` / `getkeys` accept `"layer": 0-2`; `getkeys` takes `"slot": 1-7` for one slot's full spec | per-layer key maps |
| spec types `layer`, `mouse`, `host` | `{"type":"layer","val":"next"}`, `{"type":"mouse","val":{"btn":"left","act":"click"}}` / `{"wheel":3}` / `{"move":[dx,dy]}`, `{"type":"host","val":{"op":"url\|app\|shell\|file\|clipboard\|notify","arg":"..."}}`; `host` makes the pad send `{"evt":"host","op":...,"arg":...}` to the app |
| `info_cards` (`cards`, `badges`, `rot`) | content of the Info screen (mode 6) |
| `factory` (`what`: keys / settings / gifs, `confirm`: true), `boot_opt` (`nodisp`), `safe_retry` | recovery from safe mode / bad settings |
| `wifi` (`ssid`, `pass`) / `ota` (`val`, `pass`) | only in a build with `DC_ENABLE_WIFI 1`; otherwise `wifi_disabled` |

Error codes include `json`, `unknown_cmd`, `key`, `spec`, `too_long`, `nvs_full`, `pin`, `pin_protected`, `no_display`,
`no_hid`, `no_space`, `crc`, `seq`, `b64`.

## Testing without hardware

`tools/` contains an emulator test-suite: the real firmware (compiled with `-DDC_SIM`) runs inside Espressif's QEMU
ESP32-S3 and 21 test groups drive the protocol, the screens (with PNG screenshots), the filesystem / GIF path,
persistence, a fuzz run and safe mode. See `tools/README.md`. The app's *Simulate pad* uses a Python model of the same protocol.
App tests (all headless; the Qt tests use the offscreen platform): `lib_test.py` and the other `tools/*_test.py` (the `desk_lib` modules: OTA client against a fake
device, backups incl. hostile zips, feeds, active-window rules, host-action safety, recorder, scripting), `tools/engine/*_test.py` (the whole app logic - connection, Diagnostics,
key upload + read-back, macros, GIF library, layers, profiles, Info, compatibility with a pad on firmware 1.1, computer / mouse actions, recovery, backup / restore, schedules, API, plugins - driven
without any widget against the simulated pad), `tools/qt/*_test.py` (the real Qt widgets against the simulated pad: shell, rail, palette, wizards, every page, plus a monkey test that clicks every control and fails on any exception) and `tools/qt_layout_test.py` (every page at five window sizes in four languages, light and dark:
no clipped text, no overflow, no page scrolling). The same suites run on every push in
`.github/workflows/ci.yml`, together with a firmware compile matrix and the QEMU suite (`tools/run_emulator_suite.sh`); `tools/run_app_tests.sh` runs everything but the QEMU suite. `docs/FEATURES.md` maps every requirement to the code and the test that covers it,
and lists what only real hardware can prove.

## Wiring (from the project spec)

| Function | GPIO | | Function | GPIO |
|---|---|---|---|---|
| TFT SCLK | 12 | | K1 | 1 |
| TFT MOSI (SDA) | 11 | | K2 | 2 |
| TFT RES | 10 | | K3 | 4 |
| TFT DC | 9 | | K4 | 5 |
| TFT CS | 8 | | K5 | 6 |
| TFT BLK (PWM) | 7 | | Encoder A / B / SW | 13 / 14 / 15 |
| Onboard RGB LED | 21 | | BOOT button | 0 |

Keys and the encoder switch go to GND (internal pull-ups). GPIO 19/20 are the USB pins, 0/3/45/46 are strapping pins.
If a pin you wired is not exposed on your board revision the Diagnostics's *Keys* / *GPIO* tools show it immediately.

## Notes

- Text snippets are typed as US-layout ASCII. `PRIMARY` in a combo = Ctrl (Windows/Linux) or Cmd (macOS), from the *Host OS* setting.
- Keyboard layouts (core 3.0+): `en_US de_DE fr_FR es_ES it_IT pt_PT pt_BR sv_SE da_DK hu_HU`
  (`fr_CH` / `ja_JP` only exist in arduino-esp32 3.3.8 / 3.3.9+, so they are not offered).
- The `.ino` files contain explicit function prototypes and keep `#if` blocks out of function bodies on purpose: the
  Arduino IDE's automatic prototype generator can mis-parse such sketches.
- PlatformIO: `platformio.ini` is provided but was **not** build-tested in this repository (only arduino-cli + the emulator were).
