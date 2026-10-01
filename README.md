# Desk Companion — setup guide

A round-screen ESP32-S3 macro pad. The firmware is fully standalone; the Python app is optional
and only needed to change key mappings, build macros, or upload a GIF.

## 1. Arduino IDE setup

1. **Board support**: File → Preferences → "Additional Boards Manager URLs" →
   `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
   Then Tools → Board → Boards Manager → install **esp32 by Espressif Systems** (3.0.0 or newer).
2. **Board**: Tools → Board → "ESP32S3 Dev Module" (or your specific "ESP32-S3 Zero" entry if your
   board package adds one).
3. **Critical USB settings** (Tools menu) — the sketch will not compile without these:
   - **USB Mode**: `USB-OTG (TinyUSB)`
   - **USB CDC On Boot**: `Enabled`
   - **Upload Mode**: `UART0 / Hardware CDC` (use this if the board isn't detected for the very
     first upload; after that, "USB-OTG CDC" also works since the sketch enumerates as a CDC device)
   - **Partition Scheme**: `Default 4MB with spiffs` (or any scheme that includes a SPIFFS/LittleFS
     data partition — the GIF and settings need it)
   - **Flash Size**: `4MB` (match your board)
4. **Libraries** — Tools → Manage Libraries, install:
   - `TFT_eSPI` by Bodmer
   - `AnimatedGIF` by bitbank2
   - `Bounce2` by Thomas O Fredericks
   - `ArduinoJson` by Benoit Blanchon — **version 7.x** (the code uses the v7 `JsonDocument` API)
5. **Configure TFT_eSPI**: copy `User_Setup.h` from this package over
   `<Arduino sketchbook>/libraries/TFT_eSPI/User_Setup.h` (back up the original first). This
   sets the GC9A01 driver, the exact pin map, and the fonts the firmware uses.
6. Open `DeskCompanion/DeskCompanion.ino`, select the correct COM/serial port, and upload.
   First boot takes a few extra seconds — it generates a small demo radar animation in LittleFS.

### PlatformIO alternative
A ready `platformio.ini` is included (uses the same pins as flags, so you can skip step 5 above).
From the folder containing both `platformio.ini` and `DeskCompanion/`: `pio run -t upload`.

### Wiring checklist
| Function | GPIO | | Function | GPIO |
|---|---|---|---|---|
| TFT SCLK | 12 | | K1 | 1 |
| TFT MOSI (SDA) | 11 | | K2 | 2 |
| TFT RES | 10 | | K3 | 4 |
| TFT DC | 9 | | K4 | 5 |
| TFT CS | 8 | | K5 | 6 |
| TFT BLK (PWM) | 7 | | Encoder A | 13 |
| | | | Encoder B | 14 |
| | | | Encoder SW | 15 |
All switches and the encoder button are wired to GND and use internal pull-ups — no external
resistors needed.

## 2. What works with zero configuration
Power it over USB and it immediately behaves as a keyboard + media-key HID device:
K1 Copy, K2 Paste, K3 Undo, K4 Play/Pause, K5 Mute, encoder turn = Volume, encoder click = radial
menu (Brightness / Volume / Mode / Exit, auto-closes after 4 s), encoder long-press = next display
mode. Five modes: Clock, Focus timer (K1 start/pause, K2 reset, turn dial to set minutes while
idle), Media dashboard, System telemetry (shows live host CPU/RAM when the app is running,
otherwise pad uptime/temperature), and a looping GIF (a small built-in demo until you upload
your own). All key mappings, the current mode, and brightness survive power loss.

## 3. Desktop companion app
```
pip install customtkinter pyserial psutil pillow
python companion_app.py
```
On Windows that is all - the live test uses the built-in Windows input API. (On macOS/Linux add
`pynput` for the live test; everything else works without it.)

**Zero setup connection:** just start the app and plug the pad in (before or after, any order).
It looks for Espressif USB devices every second, performs the `hello` handshake (retried while
the pad is still booting) and connects by itself; if the cable is pulled it reconnects when the
pad returns. A popup + the Windows device sound say **DeskCompanion connected / disconnected**
(switch under *Device*). If Windows sees the board but it does not answer - or another program
such as the Arduino serial monitor holds the COM port - an orange popup says so. The popups need
the app to be running; the pad itself works without it.

**No pad handy?** Click **Simulate pad (no hardware)** on the Dashboard tab. It connects the app
to an in-process stand-in (`SimFirmware`/`SimPort`) that speaks the exact same JSON wire protocol
as the real firmware - no serial port, driver, or OS-specific loopback trick involved, so it works
identically on Windows/macOS/Linux. Everything that talks to "the pad" (remapping keys, the Macro
Creator, brightness/mode, and the full chunked GIF upload with CRC check) runs through the same
`Device` code used for real hardware, so you can try the whole app, including uploading a GIF, before
ever touching an ESP32. Click it again (now labelled **Stop simulating**) to disconnect. The app also sends your PC's keyboard
layout to the pad (Device tab, *auto*), so Ctrl+Z is Undo on QWERTZ keyboards too instead of Redo. The **Virtual Pad** tab (see below) is the main screen. The **Dashboard** tab is the
same key mapping as a plain list: K1–K5 and both encoder directions, 52 built-in actions plus
"Unassigned"; edits are *staged* and go to the pad with **Upload**. The **Macro Creator** tab builds custom key
combos, ASCII text auto-typers, and multi-step sequences (combo + text + delay + media steps, up
to 64 steps) and assigns them to any key. The **GIF Upload** tab opens with a **preset library** of
8 procedurally-generated quick-start animations (Heartbeat, Spinner, Pulse Rings, Confetti Burst,
Fire, Rainbow Sweep, Loading Dots, Checkmark Pop) you can preview and upload with one click - no
file needed; these are plain geometric generators, not copyrighted sticker/meme art, so there is
nothing to source or license. Below that, **Select GIF...** still lets you load your own file, which
crops/resizes it to a circular 240×240 image and automatically reduces colours/frames to fit the
pad's free flash; either path previews on the round canvas and uploads with a progress bar. The
**Device** tab covers brightness, active mode, host OS
(for the Ctrl-vs-Cmd modifier), keyboard layout, and optional Wi-Fi/NTP time sync.

### Virtual Pad (digital twin)
The first tab is a live copy of the device: the round screen, the five keys and the encoder.
- **Live screen** - the twin runs the same state machine and scenes as the firmware (Clock, Focus
  timer, Media, System with your real CPU/RAM, GIF, radial menu, upload progress). The GIF mode
  plays the GIF you processed on the *GIF Upload* tab *before* you upload it. Fonts are an
  approximation of the TFT's bitmap fonts; layout and behaviour match.
- **Use it like the pad** - click a key; click the knob (menu), hold it 0.65 s or right-click it
  (next mode / close menu); scroll over the knob or click the ◀ ▶ buttons to turn it. The focus
  timer, menu, volume and play state react exactly like on the device.
- **Drag and drop** - drag any action from the library onto a key or an encoder arrow; drag a key
  onto another key to swap them; right-click a key for *Test / Clear / Assign*. Double-click a
  library entry for an "Assign to…" menu. Custom macros appear under *Custom*.
- **Test before uploading** - **Run actions for real** (on by default when available) makes the
  virtual keys press the actual shortcut / media key / text on *this* computer. On Windows the
  app remembers the last program you used and hands the keyboard focus back to it before pressing
  keys, so a click on a virtual key acts on e.g. your editor, not on the app. Click into the
  **Sandbox** box to test inside the app instead, or set a **Delay** (up to 5 s). The Macro Creator
  has **Test** buttons for combos, text and sequences. Switch the toggle off for a *dry run*
  (screen + log only). Lock Workstation and Close Window ask first.
- **Upload** - edits are staged; orange dots mark keys the physical pad doesn't have yet, and the
  button shows how many changes are unsent. **Upload to pad** sends all 7 key slots, brightness
  and mode in one go; "Upload every change immediately" sends each edit as you make it.
- **Limits, honestly:** on macOS/Linux a click gives the app the focus, so live actions land in
  the app itself unless you use the sandbox or the delay; macOS asks for *Accessibility* permission
  the first time, Linux needs X11 (Wayland blocks synthetic input), and pynput cannot send Media
  Stop / Fast-forward / Rewind. On Windows an elevated (admin) target window ignores a
  non-elevated app, and Fast-forward/Rewind work only in players that listen for them. The live
  test types text by Unicode, the pad types by keyboard layout - special characters may differ.
  The twin does not read the pad back: it adopts the pad's mode/brightness on connect, and
  remembers what it last uploaded.

## 4. Serial protocol reference (JSON, one object per line, USB CDC @ 115200)
Host → pad: `{"cmd":"stats","cpu":42,"ram":58}` (no reply) · `{"cmd":"hello"}` → device info ·
`{"cmd":"remap","key":1..7,"type":"combo|media|text|macro|none","val":...}` (keys 1-5 = K1-K5,
6/7 = encoder CW/CCW) · `{"cmd":"reset_keys"}` · `{"cmd":"brightness","val":5..255}` ·
`{"cmd":"mode","val":1..5}` · `{"cmd":"os","val":"win|mac|linux"}` ·
`{"cmd":"time","epoch":...,"tz":...}` · `{"cmd":"wifi","ssid":"...","pass":"..."}` ·
`{"cmd":"media","vol":0..100,"playing":bool,"muted":bool}` · GIF upload: `gif_begin` (size, crc32)
→ `gif_chunk` (seq, base64 data, ≤`chunk` bytes as reported by `gif_ready`: 768, or 128 on core 2.x) × N → `gif_end`, or `gif_abort`/`gif_delete`. Every
command gets `{"ok":true/false,...}` except `stats`.

## 5. Notes
- Text snippets are typed as **US-layout ASCII only** (control chars, high-Unicode and emoji are
  rejected client-side before they ever reach the pad).
- "PRIMARY" in a combo resolves to Ctrl on Windows/Linux and Cmd (GUI) on macOS, based on the
  Host OS setting.
- GIF capacity depends on your flash/partition scheme; the app always fits the file to the pad's
  actual reported free space, degrading colours and frame count as needed.
- Firmware needs **arduino-esp32 3.x** for the 8 KB USB-CDC receive buffer (fast GIF upload). On
  core 2.0.x it still works - the sketch detects the small buffer and uploads in 128-byte chunks.
- With *USB CDC On Boot* the core starts USB before `setup()`, so the USB product name cannot be
  changed from the sketch; the app recognises the pad by VID `0x303A` plus the `hello` reply.
- Fast encoder turns queue their key/media steps (bounded) instead of dropping them; text
  snippets are never stacked.
- Keyboard layout switching (the `{"cmd":"layout"}` command and the Device tab's *Keyboard layout*
  field) needs **arduino-esp32 core 3.0.0+** - `USBHIDKeyboard::begin(const uint8_t*)` and the
  `KeyboardLayout_xx_xx` tables it needs don't exist on 2.0.x. The sketch detects the core version
  at compile time (`ESP_ARDUINO_VERSION_MAJOR`) and compiles that feature out on older cores instead
  of failing to build; the pad still works standalone with plain US-ASCII key codes either way, and
  the app's `layout` request gets a clean `nack` instead of silently doing nothing. Supported
  layouts are `en_US de_DE fr_FR es_ES it_IT pt_PT pt_BR sv_SE da_DK hu_HU` - Swiss-French and
  Japanese are deliberately not offered: `KeyboardLayout_fr_CH`/`KeyboardLayout_ja_JP` were only
  added to arduino-esp32 in 3.3.8/3.3.9, so every other 3.x release (which is most installs) does
  not declare them at all - a hard compile error, not a runtime fallback - so including them would
  have broken the build for most core 3.x users exactly like the 2.0.x case above.
- A failed `prefs.putString()` in the `remap` handler (NVS, the ~20 KB settings partition, can fill
  up if several key slots hold large custom macros) used to still `ack` as if the save had worked;
  it now checks the return value and `nack`s with `nvs_full` so a remap that didn't actually persist
  is visible instead of silently reverting after the next reboot.
- Verified by compiling the `.ino` with arduino-cli, end to end, against ESP32 cores 2.0.9, 3.2.1
  (representative of the pre-3.3.8 releases most 3.x users have) and 3.3.12 (TFT_eSPI 2.5.44,
  AnimatedGIF 2.2.3, Bounce2 2.72, ArduinoJson 7.4.2): 3.3.12 builds to
  83 % of the app partition / 107 KB RAM; 2.0.9 to 64 % / 84 KB RAM (smaller, since the keyboard-layout
  tables are compiled out). All function prototypes are now written explicitly rather than left to the
  Arduino IDE's automatic (ctags-based) generator, and the two backlight functions no longer have
  `#if/#else/#endif` inside their body - both were observed, in this compile verification, to be able
  to make that generator misparse the rest of the file; writing them out is a pure, behaviour-preserving
  addition that removes the exposure regardless of which ctags build a given Arduino install bundles.
- The companion app's wire protocol (handshake, remap, brightness, and the full chunked GIF upload
  with CRC) was verified end to end against a protocol-accurate firmware stand-in, independent of the
  in-app **Simulate pad** feature described above.
