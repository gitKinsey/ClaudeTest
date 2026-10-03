# UI parity checklist: Tk app -> Qt app

The Qt app (`companion_qt.py`, `ui_qt/`) must do **everything** the Tk app (`companion_app.py` `App`, `DevTab`, `desk_lib/padextras.py`,
`appcard.py`, `automation.py`, `scripts_page.py`, `wizards.py`, `tray.py`) does. The Tk app stays until every line below is ticked.

Legend: `[x]` implemented and covered by a test or by the layout test; `[~]` implemented, not testable headless (needs hardware / OS); `[ ]` open.
"Engine" = `core/engine.py` (no widgets). "Page" = a module in `ui_qt/pages/`.

## A. Shell
- [ ] A1  Window: soft minimum 640x520, default 1300x780, free resize, remembered size/position/splitters
- [ ] A2  Left rail with sliding indicator, icons, badges (pad disconnected, unsent changes), version label; top icon bar below 640 px
- [ ] A3  Connection pill in the rail (Searching / Pad connected / Simulated pad / Not connected / Not answering)
- [ ] A4  Light / dark / system theme (was: "Light theme" switch + segmented control), accent colour (5), UI scale (6), language (en/de/es/fr), reduce motion
- [ ] A5  Status bar (last message, error colouring) + activity log (timestamped, 200 lines)
- [ ] A6  Toast notifications (ok / warn / off) with sound, click to dismiss, stacking, setting "Popup + sound when the pad connects"
- [ ] A7  Global shortcuts: Ctrl+K palette, Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z undo / redo (not inside text fields)
- [ ] A8  Close behaviour: tray keeps running, otherwise quit (saves config, stops API, badge server, host worker, device)
- [ ] A9  Command line: `--config`, `--port`, `--api-port`, `--minimized` (autostart), `--flash-helper`; start minimised to tray / taskbar
- [ ] A10 System tray (Show / Layer 1-3 / Quit), `tray.Tray` re-used
- [ ] A11 Global hotkey opens the command palette (pynput), shows the window
- [ ] A12 Advanced switch (hides Diagnostics, Scripts, API & CLI, plugins, raw tools, core flashing); persisted; reachable from the palette
- [ ] A13 Pop-out mini pad window (always-on-top twin)
- [ ] A14 Keep this window on top (was on the Pad page)
- [ ] A15 Command palette (all entries below)
- [ ] A16 Setup wizard (6 pages), guided hardware test (7 steps + HTML report + copy), automatic backups dialog
- [ ] A17 Responsive layout: >=1200 3 columns, 900-1200 2 columns + icon rail, 640-900 single column + slide-over inspector + twin in the top bar, <640 top bar, short windows compact; no page-level scrolling

## B. Overview (was: Dashboard / Home)
- [ ] B1  Banners: firmware old / newer / unknown / CoreBringup (+ "Update firmware" button), "Can't connect?" trouble card, SAFE MODE banner (+ "Recovery options"), tip card with "Got it"
- [ ] B2  Connection card: port list (sorted, ESP first) + Rescan, Connect / Disconnect, Auto-connect switch, Simulate pad / Stop simulating, status line, explanation
- [ ] B3  Pad health: Firmware, Pad layer, Free flash, Last reset, Display, USB keyboard, Storage, Chip temp (+ colours, Refresh)
- [ ] B4  Live telemetry: CPU and RAM bars
- [ ] B5  Quick actions: Upload everything, Setup wizard, Guided hardware test, Pick a GIF, Command palette
- [ ] B6  Key map rows (7 slots: category + action combos) with Upload all / Reset this layer (lives in Keys inspector "Action" tab, linked from Overview)
- [ ] B7  Usage: 7 bars (K1-K5, dial left / right), Reset counters, flush every 30 s
- [ ] B8  Display-stalled warning in status once per connection
- [ ] B9  Update check at start -> toast + status

## C. Keys (was: Virtual Pad + Macro Creator + gesture / choice panels + share codes)
- [ ] C1  Layer selector (Layer 1/2/3), "pad is on layer N" pill, Show on pad
- [ ] C2  Pad twin: screen (live render, 40 ms), 5 keys, dial (click / hold / wheel / right-click menu), turn arrows, orange "unsent" dots, key flash, knob spin, drop highlight, labels with category stripes
- [ ] C3  Drag and drop: library -> key / arrow, key -> key (swap), double-click menu "Assign to ...", right-click slot menu (Test now / Clear / Assign > category > action)
- [ ] C4  Action library tree with search, categories (+ Custom), remembered expanded categories
- [ ] C5  Upload to pad (with "(N unsent)" label and warning colour), Upload every change immediately, Reset this layer, read-back verification + auto-heal, Verify keys on pad
- [ ] C6  Test on this PC: live test switch (+ availability hint and colours), delay 0/1/2/3/5 s with countdown toasts, risky-action confirmation, sandbox text box, test log (200 lines), focus hand-back (Windows)
- [ ] C7  Virtual screen: mode (20), brightness slider
- [ ] C8  Undo / redo of key assignments (history), snapshot includes layers + custom
- [ ] C9  Macro builder: target key, key combination (4 modifiers + key) -> Assign / Add to sequence / Test
- [ ] C10 Text snippet auto-typer (+ Enter afterwards), Assign / Add / Test, ASCII validation
- [ ] C11 Computer / mouse / layer actions: all 24 kinds with placeholders, transform picker for "Transform the clipboard", validation messages, firmware-capability errors, Assign / Add / Test
- [ ] C12 Sequence: list (COMBO / TEXT / DELAY / MEDIA / COMPUTER / MOUSE / LAYER), Move up / down, Remove, Clear, Add delay (ms), Add media key, name, Assign sequence to key, Save to library only, Test sequence, 64-step limit
- [ ] C13 Keystroke / mouse recorder (pynput, confirmation, 60 s / 64 steps, Stop)
- [ ] C14 Gestures: hold / double / triple / chords / dial press-turn / dial double + triple click -> list, add (layer, key, gesture, category, action), remove, push to pad
- [ ] C15 Alternating / random keys (2 or 2-6 actions) -> Assign to selected key
- [ ] C16 Share codes: copy layer code, paste + replace layer (validated, undoable)
- [ ] C17 Macro export / import (.json)
- [ ] C18 Layer presets (apply "Ready-made key layouts") -> in Rules, refreshes Keys

## D. Display
### D1 GIFs
- [ ] D1.1 Round preview of the processed GIF (animated), twin follows
- [ ] D1.2 Views: Built-in (16 presets, thumbnails), My GIFs (add, open folder, tiles, right-click delete, empty state), Online (Tenor / GIPHY, key, "Get a free key", search, trending, async thumbnails, status)
- [ ] D1.3 Select GIF file, Keep a copy, Dithering, size info line, progress bar, Upload to pad (slot 1-4), Clear this slot
- [ ] D1.4 "On the pad": refresh, slot rows (size, empty, playing, Show, Del), rotate every (off / 5 s ... 5 min), messages for old / CoreBringup firmware
### D2 Info screen
- [ ] D2.1 Cards: Now playing (+ status), Weather (city Find, Fahrenheit, place label), Next calendar event (.ics path / link, Browse, Use, status), Custom card (4 fields)
- [ ] D2.2 More cards: kinds (all `extras.KINDS`), per-kind hint, label, argument, Add (max 4), list with Remove
- [ ] D2.3 Notification badges: URL (read-only) + Copy + Test badge + label
- [ ] D2.4 Preview of the Info screen, card rotation slider, Send to the pad now, Show the Info screen on the pad, Show the album cover, Show a QR code of the clipboard, status text
### D3 Screens
- [ ] D3.1 Screens in the cycle: 20 checkboxes (13-20 need 1.5), at least one stays on
- [ ] D3.2 Reminders x3 (minutes + text, validation, Show now, Save reminders)
- [ ] D3.3 Habits: 5 names, Save names, Refresh, today / streak text
### D4 Look and behaviour (pad settings)
- [ ] D4.1 Brightness, Screen mode
- [ ] D4.2 Dial acceleration, Clock style (6), Screensaver after + style (7), Night dimming on / from / to / max brightness
- [ ] D4.3 Colour theme (7), Rotation, tint per layer, pixel shift, fade, start-up animation, start-up name, LED tick, key toast, dial lock, key repeat K1-K5
- [ ] D4.4 Availability notes per firmware capability, values loaded from the pad on connect, no echo back while loading
- [ ] D4.5 Dim while PC locked / fullscreen window + dimmed brightness slider
- [ ] D4.6 LED: follows CPU, time of day, sound, blink for alerts; sound bars (SOUND screen) switch; availability texts

## E. Rules
- [ ] E1  Programs: switch on / off, "When no rule matches" (keep / L1-3), live status line, rules list (enable, name, match text, time / days / GAME, layer menu, up, down, Remove), add form (name, match, kind, layer, time, days, game mode), "Use the program I focus in 3 s", Quick add (VS Code, Browser, Spotify, Zoom + More programs), Ready-made layouts (preset, layer, also add rule, Apply), remember my layer per program
- [ ] E2  Schedules: list (enable, name, description, next run, Run now, Remove), add form (When kinds + argument + hint, Do kinds + placeholder, name)
- [ ] E3  Computer actions: AI key / model, Screenshots folder + Take one now, Clipboard history switch + Forget, Window layouts (save / restore / delete + list)
- [ ] E4  (Advanced) Local API and command line: enable, port, new token, copy token, state + help text
- [ ] E5  (Advanced) Plugins: allow, open folder, add example, reload, status

## F. Scripts (Advanced)
- [ ] F1  Script picker, New, name, Save (validation + history of 10), Delete, template menu, earlier versions + Restore, import from address, editor, Check, Dry run (+ pretend window), Run in 3 s, Stop, Assign to the selected key, log

## G. Pad & App
- [ ] G1  Behaviour: Host OS, Keyboard layout (auto + 10), Sync time now, popup + sound, mirror PC volume, autostart, tray, allow shell (confirm), (dim / LED switches live in Display)
- [ ] G2  Firmware: status line, hint, Update the pad to X, (Advanced) Flash CoreBringup, Flash the Wi-Fi build, Flash another .bin, Wi-Fi OTA (password, enable, disable, update, progress), Wi-Fi credentials, flashing log lines, flash busy state
- [ ] G3  Recovery: Check pad state, Retry normal boot, Boot without display, Re-enable display, Reset key maps / all settings / delete GIFs (confirm), Boot report, Roll back
- [ ] G4  Backup: Back up everything, Restore, Export one macro, Import macros, Automatic backups
- [ ] G5  This app: appearance, accent, size, language, hotkey, plugins (Advanced), Check for updates + switch, tips switch, latency test, power estimate, multi-pad note
- [ ] G6  Activity log

## H. Diagnostics (Advanced, was: Dev tab)
- [ ] H1  Header: state label + Ping x5, Device info, Full self-test, Copy diagnostic report, Export report .zip
- [ ] H2  0 Flash (core / full); 1 Serial ports table + hints + Refresh + Probe all ports; 2 LED sliders / presets / modes; 3 Keys + encoder indicators, live events, Read now, virtual presses; 4 Display tests, screenshot, live mirror; 5 Keyboard / media test; 6 GPIO tester + scan; 7 System: Reboot, Reboot into download mode, Verify keys
- [ ] H3  Terminal (tx / rx / raw / sys / err colours, 600 lines, raw JSON send, clear, copy, rx / tx byte counters)

## I. Engine (logic that must survive without a toolkit)
- [ ] I1  Threads: host worker, telemetry, monitor / auto-connect, media mirror, profile loop, info loop, schedule loop, clipboard history loop, audio / LED loop, screen / dim loop, twin loop
- [ ] I2  Connection: connect / disconnect / simulate, trouble text, `_try_connect` bookkeeping, on connect / disconnect handling
- [ ] I3  API bridge handler, badge server, host action whitelist, script backend, plugin API, clipboard helpers
- [ ] I4  Config load / save, restore config, auto backups
- [ ] I5  Pending tracking, upload, verification, gestures sync, labels, layout, os
- [ ] I6  Test hooks used by the existing tests keep their names (drop_assign, upload_all, upload_slots, _info_collect, import_layer_share_code, hostact, run_script ...)

## J. Palette entries (`open_palette`)
Go to every page, Upload everything, Connect / disconnect the simulated pad, Rescan serial ports, Verify the keys stored on the pad, Toggle light / dark theme,
Run the guided hardware test, Run the setup wizard, Back up everything, Restore from a backup, Show the Info screen on the pad, Send info cards now,
Check the pad's state, Undo, Redo, Check for app updates, Latency test, Pad: switch to layer N, Pad: show screen N (20), plus new: Toggle Advanced mode, Open the mini pad.
