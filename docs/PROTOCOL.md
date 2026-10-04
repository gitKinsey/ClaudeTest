# Desk Companion serial protocol

The pad is a USB composite device: a keyboard/mouse/consumer-control HID plus a CDC serial port. Over the serial port both sides
speak **one JSON object per line** (`\n`-terminated, UTF-8, 115200 baud is nominal - it is native USB, the rate is ignored).

* **Request** (PC -> pad): `{"cmd": "<name>", "id": <int>, ...}`.
* **Reply** (pad -> PC): `{"ok": true, "id": <same int>, ...}` or `{"ok": false, "id": ..., "err": "<code>"}`. Replies carry the request's `id`
  (older firmware may omit it - accept those).
* **Event** (pad -> PC, unsolicited): `{"evt": "<name>", ...}` and no `id`: key presses, dial turns, host requests, reminders.
* Lines that are not JSON (boot log, debug) must be ignored.

`deskcompanion_client.py` is a ~100-line reference client (needs only `pyserial`).  Only one program can own the port: close the app first,
or use the app's local HTTP API (`deskcompanion_cli.py`) instead.

## Feature detection

`{"cmd":"hello"}` returns `fw`, `dev`, `mode`, `bright`, `layer`, `layers`, `modes`, `os`, `layout`, `synced`, `fs`, `fs_state`, `safe`, ... and **`caps`**,
a list of feature names. Check `caps` before using a newer command; do not compare version numbers.

| cap | adds |
|---|---|
| layers, mouse, host, info, gifslots, factory | firmware 1.2 basics |
| hostx, gestures, dialaccel, clockstyle, saver, nightdim | 1.3: hold/double key gestures, `settings` command |
| screens, pressturn, toggle, wheelmods, ledfx, reminders, habits | 1.4: extra screens, dial press+turn, `screens` / `habits` / `reminders` commands |
| hostx2, dimcmd, themes, fx, chords, tapdance, dialclicks, keyrepeat, games, pet, diag, viz, labels, bootlog, rollback, cards2, saver2, clock2, display2, konami | 1.5: see below |
| stateevt, batch, snap2, ctx, lnames, toast, accent, perf | 1.6: `state` events, `remap_batch`, `snapshot` `scale`, `ctx`, `layer_names`, `toast`, `settings` `accent`, counters in `info` (below). `hello` also carries `proto` (protocol level, 16) |

## Commands (all answered with an `ok` reply unless noted)

| cmd | fields | meaning |
|---|---|---|
| `hello` | | identity, state, `caps` |
| `ping` | `t` (echoed) | reply `evt:"pong"`, `up` (ms) |
| `echo` | `data` | returns `data` unchanged |
| `info` | | chip, heap, uptime, reset reason, crash count |
| `stats` | | counters (keys, uptime) |
| `time` | `epoch` | set the clock |
| `brightness` | `val` 5..255 | backlight |
| `mode` | `val` 1..`modes` | screen |
| `layer` | `val` 0..2 | active key layer |
| `os` | `val` `"win"`/`"mac"` | modifier mapping |
| `layout` | `val` e.g. `"us"`, `"de"` | keyboard layout for typed text |
| `getkeys` | optional `layer` | key map of a layer |
| `remap_batch` | `items`: 1-24 objects with the fields of `remap` (`key`, `type`, `val`, optional `layer`, `gesture`, `clear`) | **1.6 (`batch`).** Every item is validated first; nothing is stored unless all are fine. Answers `n`; a full NVS answers `nvs_full` with `done` (items already written). Keep a line under 6000 characters (the app sends at most ~4800) |
| `ctx` | `text` up to 16 printable ASCII characters (`""` clears) | **1.6 (`ctx`).** The program the app switched the layer for; shown for 2.5 s next to the layer badge |
| `layer_names` | `names`: three strings of up to 10 printable ASCII characters (`""` = `L1`..`L3`); no `names` = read | **1.6 (`lnames`).** Stored in NVS; used on the layer badge |
| `toast` | `text` 1-24 printable ASCII characters, `kind` `ok` / `warn` / `err`, `secs` 1-10 | **1.6 (`toast`).** A banner from the app; not drawn over a game or in GIF mode |
| `remap` | `key` 1..15, `type`, `val`, optional `layer`, optional `gesture` (`hold`, `double`, `triple`; keys 1-5 only), `clear:true` | set one key. 1-5 = K1..K5, 6 / 7 = dial right / left, 8 / 9 = dial pressed + turned, **10-13 = chords K1+K2 ... K4+K5, 14 / 15 = dial double / triple click** (1.5). `type`: `combo` (list of key names), `text`, `media`, `macro` (list of steps), `mouse`, `host` (`{"op","arg"}`), `toggle`, `random`, `panic`, **`fx`** (`val` = a pad function name, below), `none` |
| `reset_keys` | optional `layer` | restore default key map |
| `run` | same `type`/`val` | perform an action now |
| `input` | `k` 1..5 (+ optional `g`: `tap` / `hold` / `double` / `triple`), or `turn` +-1..20 (+ `press`, `dt`), or `click` / `hold`, or `chord` 1..4, or `dclick` 1..3 | simulate a physical input |
| `led` | `mode` `auto|off|breathe|fire|solid`, `r g b` or `hex` | the RGB LED |
| `settings` | any of `dial_accel`, `clock_style` 0-5, `saver_s`, `saver_style` 1-7, `night_on/from/to/level`, `mode_mask` (20 bits), and from 1.5 `theme` 0-6, **`accent` 0-5 (1.6: 0 = the theme's colour, 1-5 = cyan, pink, green, amber, violet)**, `tint`, `rotation` 0-3, `pixel_shift`, `fade`, `boot_anim`, `splash` (12 chars), `detent_led`, `key_toast`, `repeat_mask` (bit per key), `dial_lock` | persistent pad settings (the reply echoes all, plus `host_dim`, `pomo_today`). All fields are validated before any is applied |
| `dim` | `level` 0 (off) or 5..255, or `on` true/false | the host dims the pad (lock screen, fullscreen video). Not saved; ends by itself when the host is silent for 8 s |
| `labels` | `layer`, `l`: 7 strings of up to 8 printable ASCII characters (K1..K5, dial right, dial left); no `l` = read | key names for the key toast / popup menu |
| `boot_log` | optional `clear:true` | `log` (start-up notes), `reset`, `crashes`, `counts` (power-on, software, panic, watchdog, brownout, other), `usb_connects`, `usb_drops`, `heap`, `heap_min` |
| `rollback` | `confirm:true` | boot the previous firmware (other OTA slot); `no_previous` when none is stored |
| `viz` | `v`: 8 integers 0..100 | spectrum bars for screen 20 (**no reply**, send up to ~10 times a second) |
| `key_test` | `seq`: `[[keymask, ms], ...]`, optional `chords`, `hold`, `dbl`, `tri` masks | replays key states through the gesture + chord state machine and returns what it would fire (tests; runs nothing) |
| `gesture_test` | `seq`: `[[down, ms], ...]`, `hold`, `dbl`, `tri` | the same for one key |
| `screens` / `habits` / `reminders` | see FEATURES.md | 1.4 optional screens |
| `info_cards` | `cards` list | the Info screen content |
| `gif_list`, `gif_begin`, `gif_chunk`, `gif_end`, `gif_abort`, `gif_delete`, `gif_cfg` | | GIF slots (chunked upload, base64) |
| `snapshot` | optional `scale` 2 | streams `snap_begin` (`w`, `h`, `bytes`) + chunks of the current frame; `scale` 2 (**1.6**, `snap2`) sends a 120x120 frame (4x less data) |
| `reboot` | `mode`: `normal`/`download` | |
| `factory`, `safe_retry`, `selftest`, `gpio`, `inputs`, `events`, `display`, `ota`, `wifi`, `media` | | diagnostics / recovery / optional features |

`info` also reports (1.6, `perf`) `proto`, `loop_max_us`, `loop_avg_us`, `rx_overruns` (request lines longer than 6000 characters, each answered `too_long`) and `usb_drops`.

Error codes (`err`): `bad_json`, `unknown_cmd`, `bad_arg`, `busy`, `no_fs`, `too_big`, `unsupported` ... Show them as text; do not parse.

## Events

| evt | fields |
|---|---|
| `input` | `k` (key 1..5, 6/7 dial right/left, 8 dial press), `act` (`press`/`hold`/`double`) |
| `layer` | `val` - the pad switched layer itself |
| `host` | `op`, `arg` - the pad asks the PC to do something. **Never obey blindly**: the reference app only runs an `(op, arg)` pair that is in the user's own key map. |
| `reminder` | `text` |
| `state` | `mode`, `bright`, `layer` - **1.6 (`stateevt`)**: the pad changed its own screen / brightness / layer (dial menu, long press, pad function). Debounced to one event per 150 ms, only while the host is talking to the pad |
| `pong`, `hello`, `info`, `settings`, ... | replies that are also tagged with `evt` |

## Host ops

`url`, `app`, `file`, `shell` (needs the user's opt-in), `clipboard`, `snippet`, `clip`, `notify`, `script`, and from firmware 1.5 (`hostx2`):
`appvol`, `dnd`, `audio_out`, `mic`, `shot`, `translate`, `ai`, `webhook`, `layout`, `cliphist`, `plugin`.

## Pad functions (`fx`)

`dial_lock`, `theme_next`, `rot_next`, `mode_next`, `mode_prev`, `bright_up`, `bright_down`, `latch_ctrl`, `latch_shift`, `latch_alt`, `latch_gui`
(the next key combo gets that modifier), `popup` (menu of the layer's keys), `switch_next` / `switch_prev` (window switcher; put them on the dial).
`{"type":"fx","val":"popup"}`, or inside a macro `{"fx":"popup"}`.

## Screens

1 clock, 2 focus, 3 media, 4 system, 5 GIF, 6 info, 7 stopwatch, 8 breathing, 9 dice, 10 reaction, 11 snake, 12 habits, **13 pong, 14 breakout, 15 flappy, 16 life,
17 pet, 18 simon, 19 diagnostics, 20 sound bars**. `mode_mask` decides which ones the dial menu / long press visit.

## Info card kinds (`info_cards`, field `k`)

`c` plain, `m` music, `w` weather, `e` event (colours differ only); from 1.5: `r` ring (`t` = 0..100), `p` progress bar (`t` = 0..100), `s` scrolling text (`t`).

