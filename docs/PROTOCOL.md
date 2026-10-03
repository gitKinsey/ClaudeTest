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
| hostx2, dimcmd | 1.5: more host ops, `dim` command |

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
| `remap` | `key` 1..11, `type`, `val`, optional `layer` | set one key. `type`: `combo` (list of key names), `text`, `media`, `macro` (list of steps), `mouse`, `host` (`{"op","arg"}`), `toggle`, `random`, `panic`, `none` |
| `reset_keys` | optional `layer` | restore default key map |
| `run` | same `type`/`val` | perform an action now |
| `input` | `k` 1..5, or `turn` +1/-1, or `hold`/`press` | simulate a physical input |
| `led` | `mode` `auto|off|breathe|fire|solid`, `r g b` or `hex` | the RGB LED |
| `settings` | any of `dial_accel`, `clock_style`, `saver_min`, `night_dim`, `mode_mask`, ... | persistent pad settings (reply echoes all) |
| `screens` / `habits` / `reminders` | see FEATURES.md | 1.4 optional screens |
| `info_cards` | `cards` list | the Info screen content |
| `gif_list`, `gif_begin`, `gif_chunk`, `gif_end`, `gif_abort`, `gif_delete`, `gif_cfg` | | GIF slots (chunked upload, base64) |
| `snapshot` | | streams `snap_begin` + chunks of the current frame |
| `reboot` | `mode`: `normal`/`download` | |
| `factory`, `safe_retry`, `selftest`, `gpio`, `inputs`, `events`, `display`, `ota`, `wifi`, `media` | | diagnostics / recovery / optional features |

Error codes (`err`): `bad_json`, `unknown_cmd`, `bad_arg`, `busy`, `no_fs`, `too_big`, `unsupported` ... Show them as text; do not parse.

## Events

| evt | fields |
|---|---|
| `input` | `k` (key 1..5, 6/7 dial right/left, 8 dial press), `act` (`press`/`hold`/`double`) |
| `layer` | `val` - the pad switched layer itself |
| `host` | `op`, `arg` - the pad asks the PC to do something. **Never obey blindly**: the reference app only runs an `(op, arg)` pair that is in the user's own key map. |
| `reminder` | `text` |
| `pong`, `hello`, `info`, `settings`, ... | replies that are also tagged with `evt` |

## Host ops

`url`, `app`, `file`, `shell` (needs the user's opt-in), `clipboard`, `snippet`, `clip`, `notify`, `script`, and from firmware 1.5 (`hostx2`):
`appvol`, `dnd`, `audio_out`, `mic`, `shot`, `translate`, `ai`, `webhook`, `layout`, `cliphist`, `plugin`.
