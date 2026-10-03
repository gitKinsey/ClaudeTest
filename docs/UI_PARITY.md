# UI parity checklist: Tk app -> Qt app

The Qt app (`companion_qt.py`, `ui_qt/`, `core/`) does **everything** the old Tk app did (`App`, `DevTab`, `padextras`, `appcard`, `automation`, `scripts_page`,
`wizards`, `tray`). The Tk app was removed once every line below was implemented; it is in the git history before the commit "Remove the Tk app".
Each line is covered by `tools/engine/*_test.py` (logic), `tools/qt/*_test.py` (widgets) or `tools/qt_layout_test.py` (layout) unless marked `[~]`.

Legend: `[x]` implemented and covered by a test; `[~]` implemented, but the part that touches real hardware / the OS / a real input device could not be run here (see the note after the line).
"Engine" = `core/engine.py` (no widgets). "Page" = a module in `ui_qt/pages/`.

## A. Shell
- [x] A1  Window: soft minimum 640x520, default 1300x780, free resize, remembered size/position/splitters
- [x] A2  Left rail with sliding indicator, icons, badges (pad disconnected, unsent changes), version label; top icon bar below 760 px
- [x] A3  Connection pill in the rail (Searching / Pad connected / Simulated pad / Not connected / Not answering)
- [x] A4  Light / dark / system theme (was: "Light theme" switch + segmented control), accent colour (5), UI scale (6), language (en/de/es/fr), reduce motion
- [x] A5  Status bar (last message, error colouring) + activity log (timestamped, 200 lines)
- [x] A6  Toast notifications (ok / warn / off) with sound, click to dismiss, stacking, setting "Popup + sound when the pad connects"
- [x] A7  Global shortcuts: Ctrl+K palette, Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z undo / redo (not inside text fields)
- [x] A8  Close behaviour: tray keeps running, otherwise quit (saves config, stops API, badge server, host worker, device)
- [~] A9  Command line: `--config`, `--port`, `--api-port`, `--minimized` (autostart), `--flash-helper`; start minimised to tray / taskbar - (the flags and the minimised start are tested; a real tray / taskbar start on Windows / macOS is not)
- [~] A10 System tray (Show / Layer 1-3 / Quit), `tray.Tray` re-used - (menu logic tested with a fake tray; the real `pystray` icon was not shown)
- [~] A11 Global hotkey opens the command palette (pynput), shows the window - (apply / refuse paths tested with a fake backend; the real global hotkey needs pynput on a desktop)
- [x] A12 Advanced switch (hides Diagnostics, Scripts, API & CLI, plugins, raw tools, core flashing); persisted; reachable from the palette
- [x] A13 Pop-out mini pad window (always-on-top twin)
- [x] A14 Keep this window on top (was on the Pad page)
- [x] A15 Command palette (all entries below)
- [x] A16 Setup wizard (6 pages), guided hardware test (7 steps + HTML report + copy), automatic backups dialog
- [x] A17 Responsive layout: wide = columns (splitters, remembered), medium = fewer columns, narrow = one panel with chip tabs; rail full / icons / top bar; short windows compact; no page-level scrolling; soft minimum 640x520

## B. Overview (was: Dashboard / Home)
- [x] B1  Banners: firmware old / newer / unknown / CoreBringup (+ "Update firmware" button), "Can't connect?" trouble card, SAFE MODE banner (+ "Recovery options"), tip card with "Got it"
- [x] B2  Connection card: port list (sorted, ESP first) + Rescan, Connect / Disconnect, Auto-connect switch, Simulate pad / Stop simulating, status line, explanation
- [x] B3  Pad health: Firmware, Pad layer, Free flash, Last reset, Display, USB keyboard, Storage, Chip temp (+ colours, Refresh)
- [x] B4  Live telemetry: CPU and RAM bars
- [x] B5  Quick actions: Upload everything, Setup wizard, Guided hardware test, Pick a GIF, Command palette
- [x] B6  Key map rows (7 slots: category + action combos) with Upload all / Reset this layer (lives in Keys inspector "Action" tab, linked from Overview)
- [x] B7  Usage: 7 bars (K1-K5, dial left / right), Reset counters, flush every 30 s
- [x] B8  Display-stalled warning in status once per connection
- [x] B9  Update check at start -> toast + status

## C. Keys (was: Virtual Pad + Macro Creator + gesture / choice panels + share codes)
- [x] C1  Layer selector (Layer 1/2/3), "pad is on layer N" pill, Show on pad
- [x] C2  Pad twin: screen (live render, 40 ms), 5 keys, dial (click / hold / wheel / right-click menu), turn arrows, orange "unsent" dots, key flash, knob spin, drop highlight, labels with category stripes
- [x] C3  Drag and drop: library -> key / arrow, key -> key (swap), double-click menu "Assign to ...", right-click slot menu (Test now / Clear / Assign > category > action)
- [x] C4  Action library tree with search, categories (+ Custom), remembered expanded categories
- [x] C5  Upload to pad (with "(N unsent)" label and warning colour), Upload every change immediately, Reset this layer, read-back verification + auto-heal, Verify keys on pad
- [~] C6  Test on this PC: live test switch (+ availability hint and colours), delay 0/1/2/3/5 s with countdown toasts, risky-action confirmation, sandbox text box, test log (200 lines), focus hand-back (Windows) - (everything but the Windows focus hand-back, which needs Windows)
- [x] C7  Virtual screen: mode (20), brightness slider
- [x] C8  Undo / redo of key assignments (history), snapshot includes layers + custom
- [x] C9  Macro builder: target key, key combination (4 modifiers + key) -> Assign / Add to sequence / Test
- [x] C10 Text snippet auto-typer (+ Enter afterwards), Assign / Add / Test, ASCII validation
- [x] C11 Computer / mouse / layer actions: all 24 kinds with placeholders, transform picker for "Transform the clipboard", validation messages, firmware-capability errors, Assign / Add / Test
- [x] C12 Sequence: list (COMBO / TEXT / DELAY / MEDIA / COMPUTER / MOUSE / LAYER), Move up / down, Remove, Clear, Add delay (ms), Add media key, name, Assign sequence to key, Save to library only, Test sequence, 64-step limit
- [~] C13 Keystroke / mouse recorder (pynput, confirmation, 60 s / 64 steps, Stop) - (the recording logic is tested with synthetic events; real pynput capture needs a desktop)
- [x] C14 Gestures: hold / double / triple / chords / dial press-turn / dial double + triple click -> list, add (layer, key, gesture, category, action), remove, push to pad
- [x] C15 Alternating / random keys (2 or 2-6 actions) -> Assign to selected key
- [x] C16 Share codes: copy layer code, paste + replace layer (validated, undoable)
- [x] C17 Macro export / import (.json)
- [x] C18 Layer presets (apply "Ready-made key layouts") -> in Rules, refreshes Keys

## D. Display
### D1 GIFs
- [x] D1.1 Round preview of the processed GIF (animated), twin follows
- [x] D1.2 Views: Built-in (16 presets, thumbnails), My GIFs (add, open folder, tiles, right-click delete, empty state), Online (Tenor / GIPHY, key, "Get a free key", search, trending, async thumbnails, status)
- [x] D1.3 Select GIF file, Keep a copy, Dithering, size info line, progress bar, Upload to pad (slot 1-4), Clear this slot
- [x] D1.4 "On the pad": refresh, slot rows (size, empty, playing, Show, Del), rotate every (off / 5 s ... 5 min), messages for old / CoreBringup firmware
### D2 Info screen
- [x] D2.1 Cards: Now playing (+ status), Weather (city Find, Fahrenheit, place label), Next calendar event (.ics path / link, Browse, Use, status), Custom card (4 fields)
- [x] D2.2 More cards: kinds (all `extras.KINDS`), per-kind hint, label, argument, Add (max 4), list with Remove
- [x] D2.3 Notification badges: URL (read-only) + Copy + Test badge + label
- [x] D2.4 Preview of the Info screen, card rotation slider, Send to the pad now, Show the Info screen on the pad, Show the album cover, Show a QR code of the clipboard, status text
### D3 Screens
- [x] D3.1 Screens in the cycle: 20 checkboxes (13-20 need 1.5), at least one stays on
- [x] D3.2 Reminders x3 (minutes + text, validation, Show now, Save reminders)
- [x] D3.3 Habits: 5 names, Save names, Refresh, today / streak text
### D4 Look and behaviour (pad settings)
- [x] D4.1 Brightness, Screen mode
- [x] D4.2 Dial acceleration, Clock style (6), Screensaver after + style (7), Night dimming on / from / to / max brightness
- [x] D4.3 Colour theme (7), Rotation, tint per layer, pixel shift, fade, start-up animation, start-up name, LED tick, key toast, dial lock, key repeat K1-K5
- [x] D4.4 Availability notes per firmware capability, values loaded from the pad on connect, no echo back while loading
- [x] D4.5 Dim while PC locked / fullscreen window + dimmed brightness slider
- [x] D4.6 LED: follows CPU, time of day, sound, blink for alerts; sound bars (SOUND screen) switch; availability texts

## E. Rules
- [x] E1  Programs: switch on / off, "When no rule matches" (keep / L1-3), live status line, rules list (enable, name, match text, time / days / GAME, layer menu, up, down, Remove), add form (name, match, kind, layer, time, days, game mode), "Use the program I focus in 3 s", Quick add (VS Code, Browser, Spotify, Zoom + More programs), Ready-made layouts (preset, layer, also add rule, Apply), remember my layer per program
- [x] E2  Schedules: list (enable, name, description, next run, Run now, Remove), add form (When kinds + argument + hint, Do kinds + placeholder, name)
- [x] E3  Computer actions: AI key / model, Screenshots folder + Take one now, Clipboard history switch + Forget, Window layouts (save / restore / delete + list)
- [x] E4  (Advanced) Local API and command line: enable, port, new token, copy token, state + help text
- [x] E5  (Advanced) Plugins: allow, open folder, add example, reload, status

## F. Scripts (Advanced)
- [x] F1  Script picker, New, name, Save (validation + history of 10), Delete, template menu, earlier versions + Restore, import from address, editor, Check, Dry run (+ pretend window), Run in 3 s, Stop, Assign to the selected key, log

## G. Pad & App
- [~] G1  Behaviour: Host OS, Keyboard layout (auto + 10), Sync time now, popup + sound, mirror PC volume, autostart, tray, allow shell (confirm), (dim / LED switches live in Display) - (autostart files are tested for Linux / macOS / Windows paths, not by logging in again)
- [~] G2  Firmware: status line, hint, Update the pad to X, (Advanced) Flash CoreBringup, Flash the Wi-Fi build, Flash another .bin, Wi-Fi OTA (password, enable, disable, update, progress), Wi-Fi credentials, flashing log lines, flash busy state - (the flash command line, progress parsing and the OTA client are tested; no board was flashed from this app)
- [x] G3  Recovery: Check pad state, Retry normal boot, Boot without display, Re-enable display, Reset key maps / all settings / delete GIFs (confirm), Boot report, Roll back
- [x] G4  Backup: Back up everything, Restore, Export one macro, Import macros, Automatic backups
- [x] G5  This app: appearance, accent, size, language, hotkey, plugins (Advanced), Check for updates + switch, tips switch, latency test, power estimate, multi-pad note
- [x] G6  Activity log

## H. Diagnostics (Advanced, was: Dev tab)
- [x] H1  Header: state label + Ping x5, Device info, Full self-test, Copy diagnostic report, Export report .zip
- [x] H2  0 Flash (core / full); 1 Serial ports table + hints + Refresh + Probe all ports; 2 LED sliders / presets / modes; 3 Keys + encoder indicators, live events, Read now, virtual presses; 4 Display tests, screenshot, live mirror; 5 Keyboard / media test; 6 GPIO tester + scan; 7 System: Reboot, Reboot into download mode, Verify keys
- [x] H3  Terminal (tx / rx / raw / sys / err colours, 600 lines, raw JSON send, clear, copy, rx / tx byte counters)

## I. Engine (logic that must survive without a toolkit)
- [x] I1  Threads: host worker, telemetry, monitor / auto-connect, media mirror, profile loop, info loop, schedule loop, clipboard history loop, audio / LED loop, screen / dim loop, twin loop
- [x] I2  Connection: connect / disconnect / simulate, trouble text, `_try_connect` bookkeeping, on connect / disconnect handling
- [x] I3  API bridge handler, badge server, host action whitelist, script backend, plugin API, clipboard helpers
- [x] I4  Config load / save, restore config, auto backups
- [x] I5  Pending tracking, upload, verification, gestures sync, labels, layout, os
- [x] I6  Test hooks used by the existing tests keep their names (drop_assign, upload_all, upload_slots, _info_collect, import_layer_share_code, hostact, run_script ...)

## J. Palette entries (`open_palette`)
Go to every page, Upload everything, Connect / disconnect the simulated pad, Rescan serial ports, Verify the keys stored on the pad, Toggle light / dark theme,
Run the guided hardware test, Run the setup wizard, Back up everything, Restore from a backup, Show the Info screen on the pad, Send info cards now,
Check the pad's state, Undo, Redo, Check for app updates, Latency test, Pad: switch to layer N, Pad: show screen N (20), plus new: Toggle Advanced mode, Open the mini pad.
