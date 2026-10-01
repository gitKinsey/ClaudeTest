# Desk Companion - ESP32-S3 macro pad (Waveshare ESP32-S3-Zero)

A round-screen macro pad: 5 keys + a rotary encoder, GC9A01 1.28" display, USB keyboard / media keys, and a desktop
companion app. The firmware works standalone; the app is only needed to remap keys, build macros, upload a GIF and
for the **Dev tab** (bring-up and diagnostics).

```
CoreBringup/CoreBringup.ino    STEP 1  tiny sketch, no libraries: LED + USB serial link + wiring test
DeskCompanion/DeskCompanion.ino STEP 2  the full firmware (display, keys, macros, GIFs, ...)
companion_app.py                desktop app (Dev tab, virtual pad, macro creator, GIF upload)
User_Setup.h, platformio.ini    TFT_eSPI pin setup / PlatformIO alternative
tools/                          emulator test-suite for the firmware (developers; no hardware needed)
```

## The bring-up ladder - do it in this order

The idea: prove each layer on its own, so when something is wrong you know *which* layer.

| Step | What | Proves |
|---|---|---|
| 0 | Flash **CoreBringup** | board, USB cable, driver, Arduino settings, the onboard LED |
| 1 | Run the app, open the **Dev** tab | the PC <-> pad link (JSON over USB serial), pins, keys, HID |
| 2 | Flash **DeskCompanion** (full firmware) | display, filesystem, key actions, GIFs |
| 3 | Dev tab -> *Full self-test* | everything, with a PASS/FAIL list |

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
   Once a pad runs this firmware you can skip the button: Dev tab -> *Reboot into download mode*.

### Step 0a (alternative) - flash prebuilt images, no Arduino IDE at all

`firmware/` contains prebuilt images (built from this repo with arduino-esp32 core 3.2.1, USB-OTG/TinyUSB + CDC-on-boot,
4 MB flash, DIO 80 MHz, PSRAM off) and a flasher:

```
pip install esptool pyserial
python firmware/flash.py --image core     # CoreBringup (step 1)
python firmware/flash.py --image full     # DeskCompanion (step 2)
```
Put the board in download mode first (hold BOOT while plugging in USB). If the pad already runs one of these firmwares the
script reboots it into download mode by itself. The same thing is a button in the app: **Dev tab -> 0. Flash firmware**.
`python firmware/flash.py --list` shows the serial ports (the board in download mode is `303A:1001`).
*The images were built and emulator-tested here but have not been run on real hardware by the author.*

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
pip install customtkinter pyserial psutil pillow
python companion_app.py
```
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

No hardware yet? **Dashboard -> Simulate pad (no hardware)** connects the app to an in-process stand-in that speaks the
same protocol, so the whole app (including the Dev tab) can be tried.

### Step 2 - the full firmware

1. Libraries (Library manager): **TFT_eSPI** (Bodmer), **AnimatedGIF** (bitbank2), **Bounce2**, **ArduinoJson 7.x**.
2. Copy `User_Setup.h` over `<sketchbook>/libraries/TFT_eSPI/User_Setup.h`.
3. Open `DeskCompanion/DeskCompanion.ino`, upload.

What the full firmware does differently from a plain sketch - **the core comes first**:

1. LED purple -> blue (USB up; the PC sees the pad **before** any display / filesystem work happens) -> cyan (filesystem) -> amber (display) -> heartbeat.
2. Every subsystem reports its own init result (`info` / Dev tab), a failed display or filesystem never takes the USB link down.
3. **Safe mode**: three crashes / watchdog resets in a row boot the pad with the display and GIF engine disabled and a **red double blink**, so it stays reachable from the app (Dev tab shows `SAFE MODE`, the reset reason and the boot log). A power cycle or a clean reboot tries the full firmware again.
4. LED heartbeat colour: **green** = all good, **amber** = a subsystem failed, **red** = safe mode.

Default key map (works with no app): K1 copy, K2 paste, K3 undo, K4 play/pause, K5 mute, dial = volume, dial click = radial
menu (brightness / volume / mode / exit), dial hold = next display mode. Modes: clock, focus timer (K1 start/pause, K2 reset,
dial = minutes), media dashboard, system telemetry, GIF.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Upload fails / no port | not in download mode | hold BOOT while plugging in; try another cable (charge-only cables have no data lines) |
| Port appears as `303A:1001` and stays there | board is in the ROM bootloader, or the sketch uses *Hardware CDC and JTAG* | re-plug without BOOT. For the keyboard use *USB Mode = USB-OTG (TinyUSB)* |
| App says "found on COMx ... does not answer" | wrong USB settings, a different sketch, or the port is held by the Serial Monitor | close the Serial Monitor; Dev tab -> *Probe all ports*; check *USB CDC On Boot = Enabled* |
| No LED, nothing happens after upload | sketch is boot-looping (often PSRAM = OPI/QSPI selected by mistake) or the upload was not completed | set PSRAM = Disabled, re-upload; watch the port list - a port that appears / disappears every few seconds = boot loop |
| LED blue but the app does not connect | serial link fine, but another program holds the port, or Windows needs the CDC driver | Device Manager -> Ports: *USB Serial Device*; close other terminals |
| LED works, screen stays dark | display wiring / `User_Setup.h` not installed / wrong colour setup | Dev tab -> Display tests; check `User_Setup.h` was copied into the TFT_eSPI library; try `USE_HSPI_PORT` there |
| Screen colours inverted / red-blue swapped | panel variant | uncomment `TFT_INVERSION_ON/OFF` or `TFT_RGB_ORDER TFT_BGR` in `User_Setup.h` |
| Keys do nothing | wiring, or HID not enumerated | Dev tab -> Keys: indicators must light up; Keyboard test must type. HID needs *USB Mode = TinyUSB* |
| Red double-blink | safe mode (3 crashes in a row) | Dev tab -> *Device info*: look at `reset`, `boot`; usually a bad display / wiring or wrong board settings |
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
| `gif_begin` / `gif_chunk` / `gif_end` / `gif_abort` / `gif_delete` | chunked GIF upload with CRC32 |

Error codes include `json`, `unknown_cmd`, `key`, `spec`, `too_long`, `nvs_full`, `pin`, `pin_protected`, `no_display`,
`no_hid`, `no_space`, `crc`, `seq`, `b64`.

## Testing without hardware

`tools/` contains an emulator test-suite: the real firmware (compiled with `-DDC_SIM`) runs inside Espressif's QEMU
ESP32-S3 and ~15 test groups drive the protocol, all five screens (with PNG screenshots), the filesystem / GIF path,
persistence, a fuzz run and safe mode. See `tools/README.md`. The app's *Simulate pad* uses a Python model of the same protocol.

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
If a pin you wired is not exposed on your board revision the Dev tab's *Keys* / *GPIO* tools show it immediately.

## Notes

- Text snippets are typed as US-layout ASCII. `PRIMARY` in a combo = Ctrl (Windows/Linux) or Cmd (macOS), from the *Host OS* setting.
- Keyboard layouts (core 3.0+): `en_US de_DE fr_FR es_ES it_IT pt_PT pt_BR sv_SE da_DK hu_HU`
  (`fr_CH` / `ja_JP` only exist in arduino-esp32 3.3.8 / 3.3.9+, so they are not offered).
- The `.ino` files contain explicit function prototypes and keep `#if` blocks out of function bodies on purpose: the
  Arduino IDE's automatic prototype generator can mis-parse such sketches.
- PlatformIO: `platformio.ini` is provided but was **not** build-tested in this repository (only arduino-cli + the emulator were).
