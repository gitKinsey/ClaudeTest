#!/usr/bin/env python3
"""
Runs the REAL DeskCompanion firmware (compiled with -DDC_SIM) inside Espressif's QEMU ESP32-S3 emulator and
exercises the whole JSON protocol, the screens, the filesystem / GIF path, persistence, a fuzz run and the
crash-loop safe mode.  No hardware needed.  See tools/README.md for how to build the image.

  python3 tools/emulator_test.py <flash_image.bin> [--out DIR] [--only NAME,...]

Exit code 0 = every test passed.
"""
import argparse
import base64
import io
import json
import os
import random
import socket
import struct
import subprocess
import sys
import time
import zlib

QEMU = os.environ.get("QEMU", "qemu-system-xtensa")


class Emu:
    """QEMU process with UART0 on a TCP socket; parses JSON lines from the firmware, keeps the rest as log."""

    def __init__(self, flash, port=5599):
        self.flash, self.port = flash, port
        self.p = subprocess.Popen(
            [QEMU, "-nographic", "-machine", "esp32s3", "-drive", f"file={flash},if=mtd,format=raw",
             "-serial", f"tcp:127.0.0.1:{port},server=on,wait=off", "-monitor", "none"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.sock = None
        for _ in range(100):
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=0.2)
                break
            except OSError:
                time.sleep(0.1)
        if not self.sock:
            raise RuntimeError("cannot connect to the emulator UART")
        self.sock.settimeout(0.05)
        self.buf = b""
        self.msgs = []          # parsed JSON objects not yet consumed
        self.log = []           # non-JSON console lines (boot log, errors, panics)

    def pump(self, secs=0.0):
        end = time.time() + secs
        while True:
            try:
                d = self.sock.recv(65536)
                if d:
                    self.buf += d
            except socket.timeout:
                pass
            except OSError:
                break
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                t = line.decode("utf-8", "replace").strip()
                if not t:
                    continue
                if t.startswith("{"):
                    try:
                        self.msgs.append(json.loads(t))
                        continue
                    except ValueError:
                        pass
                self.log.append(t)
            if time.time() >= end:
                break

    def send(self, obj):
        raw = obj if isinstance(obj, (bytes, bytearray)) else (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        self.sock.sendall(raw)

    def request(self, obj, timeout=6.0, want=None):
        """Send a command, return the reply with the same "id" (raw byte lines: the first reply carrying "ok")."""
        self.msgs.clear()
        self.rid = getattr(self, "rid", 0) + 1
        raw = isinstance(obj, (bytes, bytearray))
        self.send(obj if raw else dict(obj, id=self.rid))
        end = time.time() + timeout
        while time.time() < end:
            self.pump(0.05)
            for i, m in enumerate(self.msgs):
                if "ok" in m and (raw or m.get("id") == self.rid):
                    del self.msgs[: i + 1]
                    return m
        raise TimeoutError(f"no reply to {obj if not raw else '<raw>'}")

    def power_cycle(self):
        """Kill the emulator and start it again on the same flash file = unplug / re-plug (NVS + LittleFS persist)."""
        self.close()
        time.sleep(0.5)
        self.__init__(self.flash, self.port)

    def wait_boot(self, timeout=60, need_fs=True):
        """Wait for the firmware to answer hello; with need_fs also until the background filesystem job has finished."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                h = self.request({"cmd": "hello"}, timeout=1.5)
            except TimeoutError:
                continue
            if h.get("fs") or not need_fs or h.get("fs_state") == "failed":
                return h
            time.sleep(0.3)
        raise TimeoutError("firmware did not answer hello" + (" with the filesystem ready" if need_fs else ""))

    def panicked(self):
        return any("Guru Meditation" in l or "abort()" in l for l in self.log)

    def close(self):
        try:
            self.p.kill()
        except Exception:
            pass


def rgb565be_to_image(raw, w=240, h=240):
    from PIL import Image
    px = struct.unpack(f">{w * h}H", raw)
    img = Image.new("RGB", (w, h))
    img.putdata([(((p >> 11) & 31) * 255 // 31, ((p >> 5) & 63) * 255 // 63, (p & 31) * 255 // 31) for p in px])
    return img


def snapshot(e, timeout=40):
    e.msgs.clear()
    e.send({"cmd": "snapshot"})
    data, end = {}, time.time() + timeout
    begun = False
    while time.time() < end:
        e.pump(0.1)
        for m in e.msgs:
            if m.get("evt") == "snap_begin":
                begun = True
            elif m.get("evt") == "snap":
                data[m["o"]] = base64.b64decode(m["d"])
            elif m.get("evt") == "snap_end":
                e.msgs.clear()
                raw = b"".join(data[k] for k in sorted(data))
                assert begun and len(raw) == 240 * 240 * 2, f"snapshot incomplete: {len(raw)} bytes"
                return raw
    raise TimeoutError("snapshot timed out")


# ----------------------------------------------------------------------------------------------- tests
class Ctx:
    def __init__(self, e, outdir, safe_image=None):
        self.e, self.out, self.safe_image = e, outdir, safe_image
        self.first_hello, self.first_hello_s = {}, 0.0

    def save(self, img, name):
        os.makedirs(self.out, exist_ok=True)
        img.save(os.path.join(self.out, name))


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def t_first_boot_responsive(c):
    """The USB link must answer while the (slow) first-boot filesystem format / demo-GIF build still runs."""
    h = c.first_hello
    expect(h["fs_state"] in ("mounting", "formatting", "preparing", "ready"), f"fs_state {h['fs_state']}")
    print(f"        first hello after {c.first_hello_s:.1f}s with fs_state={h['fs_state']} fs={h['fs']}")
    expect(c.first_hello_s < 20, "link should answer within seconds even on a blank flash")
    if not h["fs"]:
        # the storage job may finish between hello and this request, so both outcomes are legal - but never a hang or a crash
        r = c.e.request({"cmd": "gif_begin", "size": 100, "crc": 0}, timeout=30)
        expect(r.get("err") in ("fs_busy", None) and (r["ok"] or r["err"] == "fs_busy"), f"gif_begin during storage start-up: {r}")
        c.e.request({"cmd": "gif_abort"})


def t_hello(c):
    h = c.e.request({"cmd": "hello"})
    expect(h["ok"] and h["dev"] == "desk-companion" and h["fw"] == "1.3.0", f"bad hello {h}")
    for k in ("mode", "bright", "os", "fs_free", "fs_total", "layout", "hid", "disp", "fs", "safe", "led_pin"):
        expect(k in h, f"hello lacks {k}")
    expect(h["led_pin"] == 21, "LED pin should default to GPIO21 (Waveshare ESP32-S3-Zero)")
    expect(h["fs"] and h["disp"] and not h["safe"], f"subsystems not healthy: {h}")


def t_ping_echo_id(c):
    p = c.e.request({"cmd": "ping", "t": 12345})                   # request() adds an "id" and only accepts the reply that echoes it
    expect(p["evt"] == "pong" and p["t"] == 12345 and p["id"] == c.e.rid and p["up"] > 0, f"ping {p}")
    e2 = c.e.request({"cmd": "echo", "data": {"a": [1, 2, "xé"]}})
    expect(e2["data"] == {"a": [1, 2, "xé"]}, f"echo {e2}")


def t_info(c):
    i = c.e.request({"cmd": "info"})
    expect("ESP32-S3" in i["chip"], f"chip {i['chip']}")
    expect(i["heap"] > 100000 and i["heap_blk"] > 60000, f"low heap {i['heap']} / {i['heap_blk']}")
    expect(i["ok_prefs"] and i["ok_fs"] and i["ok_sprite"], f"init flags {i}")
    expect("ready=ok" in i["boot"], f"boot log has no ready: {i['boot']}")
    expect(i["sim"] is True and i["hid"] is False, "sim build flags")
    expect(i["reset"] in ("power-on", "software", "external", "unknown", "usb"), f"reset {i['reset']}")
    expect(i["fs_total"] > 500000, "fs_total")


def t_led(c):
    r = c.e.request({"cmd": "led", "r": 10, "g": 20, "b": 30})
    expect(r["ok"] and (r["r"], r["g"], r["b"]) == (10, 20, 30) and r["mode"] == 2 and r["pin"] == 21, f"led {r}")
    r = c.e.request({"cmd": "led", "hex": "#FF8000"})
    expect((r["r"], r["g"], r["b"]) == (255, 128, 0), f"hex {r}")
    for m in ("blink", "rainbow", "off", "auto", "solid"):
        expect(c.e.request({"cmd": "led", "mode": m})["ok"], f"led mode {m}")
    expect(c.e.request({"cmd": "led", "mode": "disco"})["err"] == "led_mode", "bad led mode must nack")
    expect(c.e.request({"cmd": "led", "r": 999, "g": -5, "b": 0})["g"] == 0, "clamp")
    c.e.request({"cmd": "led", "mode": "auto", "save": True})


def t_gpio(c):
    r = c.e.request({"cmd": "gpio", "pin": 7})
    expect(r["ok"] and r["use"] == "TFT BLK" and r["val"] in (0, 1), f"gpio read {r}")
    s = c.e.request({"cmd": "gpio", "op": "scan"})
    expect(len(s["pins"]) >= 30 and all(p not in (19, 20) for p, _ in s["pins"]), f"scan {len(s['pins'])}")
    for bad in (19, 20, 26, 30, 49, -1, 100):
        expect(c.e.request({"cmd": "gpio", "pin": bad})["err"] == "pin", f"pin {bad} must be refused")
    expect(c.e.request({"cmd": "gpio", "pin": 0, "op": "high"})["err"] == "pin_protected", "GPIO0 must not be driven")
    expect(c.e.request({"cmd": "gpio", "pin": 21, "op": "low"})["err"] == "pin_protected", "LED pin must not be driven")
    expect(c.e.request({"cmd": "gpio", "pin": 3, "op": "bogus"})["err"] == "op", "bad op")
    r = c.e.request({"cmd": "gpio", "pin": 16, "op": "pullup"})
    expect(r["ok"] and r["val"] in (0, 1), f"pullup {r}")                     # (QEMU does not model pull resistors)
    r = c.e.request({"cmd": "gpio", "pin": 16, "op": "low"})
    expect(r["ok"] and r["val"] == 0, f"drive low {r}")
    expect(c.e.request({"cmd": "info"})["gpio_touched"] is True, "gpio_touched flag")


def t_inputs(c):
    r = c.e.request({"cmd": "inputs"})
    expect(len(r["keys"]) == 5 and "enc_pos" in r and "enc_sw" in r, f"inputs {r}")


def t_display_and_snapshot(c):
    e = c.e
    expect(e.request({"cmd": "display", "test": "fill", "r": 255, "g": 0, "b": 0, "hold": 20000})["ok"], "fill")
    img = rgb565be_to_image(snapshot(e))
    c.save(img, "display_fill_red.png")
    px = set(img.getdata())
    expect(px == {(255, 0, 0)}, f"fill red snapshot wrong: {list(px)[:3]}")
    try:
        for t in ("bars", "grid", "text"):
            expect(e.request({"cmd": "display", "test": t, "hold": 20000})["ok"], t)
            img = rgb565be_to_image(snapshot(e))
            c.save(img, f"display_{t}.png")
            expect(len(set(img.getdata())) >= 3, f"{t} pattern looks empty")
        expect(e.request({"cmd": "display", "test": "nope"})["err"] == "pattern", "bad pattern")
    finally:
        e.request({"cmd": "display", "test": "off"})


def t_modes_render(c):
    e = c.e
    names = {1: "clock", 2: "focus", 3: "media", 4: "system"}
    for m, n in names.items():
        expect(e.request({"cmd": "mode", "val": m})["ok"], f"mode {m}")
        e.pump(0.8)
        img = rgb565be_to_image(snapshot(e))
        c.save(img, f"mode_{m}_{n}.png")
        expect(len(set(img.getdata())) >= 4, f"mode {m} ({n}) renders an almost empty screen")
        expect(e.request({"cmd": "ping"})["ok"], "alive")
    expect(e.request({"cmd": "mode", "val": 9})["ok"] is True, "out-of-range mode is ignored, not fatal")
    expect(e.request({"cmd": "hello"})["mode"] in (1, 2, 3, 4), "mode unchanged by invalid value")


def t_virtual_input(c):
    e = c.e
    e.request({"cmd": "mode", "val": 2})
    e.pump(0.5)
    before = rgb565be_to_image(snapshot(e))
    expect(e.request({"cmd": "input", "k": 1})["ok"], "K1 starts the focus timer")
    e.pump(1.2)
    after = rgb565be_to_image(snapshot(e))
    c.save(after, "focus_running.png")
    expect(list(before.getdata()) != list(after.getdata()), "focus screen did not change after K1")
    expect(e.request({"cmd": "input", "k": 2})["ok"], "K2 resets")
    expect(e.request({"cmd": "input", "turn": 5})["ok"], "dial sets minutes")
    # radial menu: click opens, turn selects, click enters, auto-close
    expect(e.request({"cmd": "input", "click": True})["ok"], "open menu")
    e.pump(0.5)
    menu = rgb565be_to_image(snapshot(e))
    c.save(menu, "menu_open.png")
    expect(list(menu.getdata()) != list(after.getdata()), "menu did not draw")
    expect(e.request({"cmd": "input", "turn": 1})["ok"], "menu turn")
    expect(e.request({"cmd": "input", "click": True})["ok"], "menu enter")
    e.pump(0.5)
    c.save(rgb565be_to_image(snapshot(e)), "menu_edit_volume.png")
    expect(e.request({"cmd": "input", "turn": 3})["ok"], "volume up in menu")
    expect(e.request({"cmd": "input", "hold": True})["ok"], "long press closes the menu")
    for bad in ({"k": 0}, {"k": 6}, {"turn": 0}, {"turn": 99}, {}):
        expect(e.request({"cmd": "input", **bad})["ok"] is False, f"input {bad} must nack")
    expect(e.request({"cmd": "ping"})["ok"], "alive after UI exercise")


def t_events(c):
    e = c.e
    expect(e.request({"cmd": "events", "val": True})["ok"], "events on")
    e.msgs.clear()
    e.send({"cmd": "input", "k": 3})
    e.send({"cmd": "input", "turn": -2})
    e.pump(1.0)
    kinds = [(m.get("evt"), m.get("k", m.get("d"))) for m in e.msgs if m.get("evt") in ("key", "enc")]
    expect(("key", 3) in kinds and ("enc", -1) in kinds, f"missing events: {kinds}")
    expect(e.request({"cmd": "events", "val": False})["ok"], "events off")


def t_remap_persistence(c):
    e = c.e
    expect(e.request({"cmd": "reset_keys"})["ok"], "reset")
    for slot in e.request({"cmd": "getkeys"})["slots"]:
        expect(slot["def"] is True, "after reset every slot is default")
    spec = {"type": "text", "val": "hello@example.com"}
    expect(e.request({"cmd": "remap", "key": 1, **spec})["ok"], "remap text")
    expect(e.request({"cmd": "remap", "key": 6, "type": "combo", "val": ["CTRL", "SHIFT", "ALT", "t"]})["ok"], "remap combo")
    expect(e.request({"cmd": "remap", "key": 2, "type": "macro", "val": [{"combo": ["GUI", "r"]}, {"delay": 200}, {"text": "cmd"}, {"media": "MUTE"}]})["ok"], "remap macro")
    expect(e.request({"cmd": "remap", "key": 3, "type": "media", "val": "NEXT"})["ok"], "remap media")
    expect(e.request({"cmd": "remap", "key": 8, **spec})["err"] == "key", "key 8 invalid")
    expect(e.request({"cmd": "remap", "key": 1, "type": "combo", "val": ["NOPE"]})["err"] == "spec", "bad key name")
    expect(e.request({"cmd": "remap", "key": 1, "type": "text", "val": "x" * 4000})["err"] == "too_long", "too long")
    j = json.dumps(spec, separators=(",", ":"))
    k = e.request({"cmd": "getkeys"})["slots"]
    expect(k[0]["def"] is False and k[0]["len"] == len(j) and k[0]["crc"] == (zlib.crc32(j.encode()) & 0xFFFFFFFF), f"slot 1 readback {k[0]}")
    expect(e.request({"cmd": "remap", "key": 3, "layer": 1, "type": "text", "val": "layer two"})["ok"], "remap on layer 2")
    e.request({"cmd": "brightness", "val": 77})
    e.request({"cmd": "mode", "val": 3})
    e.request({"cmd": "os", "val": "mac"})
    e.power_cycle()                                               # unplug / re-plug: everything must come back from flash
    h = e.wait_boot()
    expect(h["bright"] == 77 and h["mode"] == 3 and h["os"] == "mac", f"settings did not survive a power cycle: {h}")
    k2 = e.request({"cmd": "getkeys"})["slots"]
    expect(k2[0]["crc"] == k[0]["crc"] and k2[5]["def"] is False, "key mappings did not survive a power cycle")
    l1 = e.request({"cmd": "getkeys", "layer": 1})["slots"]
    expect(l1[2]["def"] is False and l1[0]["def"] is True, "layer 2 key mapping did not survive a power cycle")
    expect(e.request({"cmd": "info"})["reset"] in ("power-on", "unknown", "external"), "reset reason after power cycle")
    e.request({"cmd": "reset_keys"})
    e.request({"cmd": "mode", "val": 1})
    e.request({"cmd": "os", "val": "win"})


def make_gif(frames=8, size=240):
    from PIL import Image, ImageDraw
    ims = []
    for i in range(frames):
        im = Image.new("P", (size, size))
        pal = []
        for k in range(16):
            pal += [k * 16, 255 - k * 16, (k * 40) % 256]
        im.putpalette(pal + [0] * (768 - len(pal)))
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, size, size), fill=0)
        d.ellipse((20 + i * 10, 20 + i * 8, 120 + i * 10, 120 + i * 8), fill=(i % 15) + 1)
        d.rectangle((150, 150, 220, 220), fill=((i + 5) % 15) + 1)
        ims.append(im)
    b = io.BytesIO()
    ims[0].save(b, format="GIF", save_all=True, append_images=ims[1:], duration=80, loop=0)
    return b.getvalue()


def upload(e, data, crc=None, chunk_override=None, abort_at=None, slot=None):
    req = {"cmd": "gif_begin", "size": len(data), "crc": zlib.crc32(data) & 0xFFFFFFFF if crc is None else crc}
    if slot is not None:
        req["slot"] = slot
    r = e.request(req, timeout=20)
    if not r.get("ok"):
        return r
    chunk = chunk_override or r["chunk"]
    seq = 0
    for off in range(0, len(data), chunk):
        if abort_at is not None and seq == abort_at:
            return e.request({"cmd": "gif_abort"})
        a = e.request({"cmd": "gif_chunk", "seq": seq, "data": base64.b64encode(data[off:off + chunk]).decode()}, timeout=10)
        if not a.get("ok"):
            return a
        seq += 1
    return e.request({"cmd": "gif_end"}, timeout=20)


def t_gif(c):
    e = c.e
    data = make_gif()
    r = upload(e, data)
    expect(r["ok"] and r["evt"] == "gif_done", f"upload {r}")
    h = e.request({"cmd": "hello"})
    expect(h["gif"] is True and h["mode"] == 5, f"hello after upload {h}")
    e.pump(3.0)                                                   # the real decoder runs frames in the background
    expect(e.request({"cmd": "ping"})["ok"] and not e.panicked(), "firmware died while playing the GIF")
    # failure paths
    r = upload(e, data, crc=123)
    expect(r.get("err") == "crc", f"bad crc must be rejected: {r}")
    r = e.request({"cmd": "gif_begin", "size": 50_000_000, "crc": 0}, timeout=20)
    expect(r.get("err") == "no_space", f"oversize: {r}")
    expect(e.request({"cmd": "gif_chunk", "seq": 0, "data": "AAAA"})["err"] == "no_upload", "chunk without begin")
    expect(e.request({"cmd": "gif_end"})["err"] == "no_upload", "end without begin")
    r = upload(e, data, abort_at=1)
    expect(r["ok"] and r["evt"] == "gif_abort", f"abort {r}")
    e.request({"cmd": "gif_begin", "size": 100, "crc": 0}, timeout=20)
    expect(e.request({"cmd": "gif_chunk", "seq": 5, "data": "AAAA"})["err"] == "seq", "wrong sequence number")
    expect(e.request({"cmd": "gif_chunk", "seq": 0, "data": "!!!!"})["err"] == "b64", "bad base64")
    r = upload(e, data)                                           # still works after all of that
    expect(r["ok"], f"re-upload {r}")
    expect(e.request({"cmd": "gif_delete"})["ok"], "delete")
    e.request({"cmd": "mode", "val": 5})
    t0 = time.time()
    while time.time() - t0 < 120:                                 # demo animation is generated again (slow in an emulator)
        e.pump(1.0)
        if e.request({"cmd": "hello"})["gif"]:
            break
    expect(e.request({"cmd": "hello"})["gif"] is True, "built-in demo GIF was not regenerated")
    expect(e.request({"cmd": "ping"})["ok"] and not e.panicked(), "firmware died generating / playing the demo GIF")
    e.request({"cmd": "mode", "val": 1})


def t_selftest_misc(c):
    e = c.e
    r = e.request({"cmd": "selftest"}, timeout=20)
    expect(r["ok"] and r["nvs"] and r["fs"] and r["heap_ok"] and r["display"] == "drawn" and len(r["keys"]) == 5, f"selftest {r}")
    expect(e.request({"cmd": "run", "type": "text", "val": "x"})["err"] == "no_hid", "run needs HID (sim build has none)")
    expect(e.request({"cmd": "frobnicate"})["err"] == "unknown_cmd", "unknown cmd")
    expect(e.request(b"this is not json\n")["err"] == "json", "malformed json")
    expect(e.request({"cmd": "brightness", "val": 0})["ok"], "brightness low clamp")
    expect(e.request({"cmd": "hello"})["bright"] == 5, "brightness clamps to >= 5")
    expect(e.request({"cmd": "brightness", "val": 9999})["ok"], "brightness high clamp")
    expect(e.request({"cmd": "hello"})["bright"] == 255, "brightness clamps to <= 255")
    expect(e.request({"cmd": "time", "epoch": 1_800_000_000, "tz": 3600})["ok"], "time")
    expect(e.request({"cmd": "hello"})["synced"] is True, "time sync flag")
    expect(e.request({"cmd": "media", "vol": 33, "playing": True, "muted": False})["ok"], "media")
    # telemetry: `stats` is fire-and-forget (no reply), and the system screen actually shows the numbers
    e.request({"cmd": "mode", "val": 4})
    shots = []
    for cpu, ram in ((5, 10), (95, 90)):
        e.msgs.clear()
        e.send({"cmd": "stats", "cpu": cpu, "ram": ram})
        e.pump(0.8)
        expect(not [m for m in e.msgs if m.get("evt") == "stats" or m.get("ok") is False], f"stats must be silent: {e.msgs}")
        shots.append(list(rgb565be_to_image(snapshot(e)).getdata()))
    expect(shots[0] != shots[1], "system screen did not change with the telemetry values")
    e.request({"cmd": "mode", "val": 1})
    expect(e.request({"cmd": "wifi", "ssid": "", "pass": ""}).get("err") == "wifi_disabled", "wifi clear is refused by the cable-only build")
    expect(e.request({"cmd": "os", "val": "linux"})["ok"], "os")
    r = e.request({"cmd": "layout", "val": "en_US"})
    expect(r.get("ok") or r.get("err") == "layout_unsupported_core", f"layout en_US: {r}")
    r = e.request({"cmd": "layout", "val": "klingon"})
    expect(r.get("ok") is False and r.get("err") in ("layout", "layout_unsupported_core"), f"unknown layout must nack: {r}")


def t_fuzz(c):
    e = c.e
    rnd = random.Random(1234)
    for i in range(400):
        kind = rnd.randrange(6)
        if kind == 0:
            junk = bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 300))).replace(b"\n", b"") + b"\n"
        elif kind == 1:
            junk = ("{" * rnd.randrange(1, 50) + "\n").encode()
        elif kind == 2:
            junk = json.dumps({"cmd": rnd.choice(["remap", "gif_chunk", "led", "gpio", "display", "input", "mode", "brightness", "time", "wifi", "run", "events"]),
                               "key": rnd.randrange(-5, 12), "type": rnd.choice(["combo", "text", "macro", "media", "none", 5, None]),
                               "val": rnd.choice([None, 5, "abc", [], ["CTRL"], [{"x": 1}], {"a": 1}, "A" * 50]), "pin": rnd.randrange(-3, 60),
                               "r": rnd.randrange(-300, 600), "seq": rnd.randrange(-3, 5), "data": rnd.choice(["", "AAAA", "A" * 2000, "====", None]),
                               "test": rnd.choice(["fill", "bars", "off", None, 3]), "k": rnd.randrange(-2, 9), "turn": rnd.randrange(-30, 30),
                               "epoch": rnd.choice([0, -1, 2 ** 40, "x"]), "size": rnd.choice([0, -5, 10, 2 ** 33]),
                               "op": rnd.choice(["read", "low", "high", "x", None])}).encode() + b"\n"
        elif kind == 3:
            junk = b'{"cmd":"' + bytes(rnd.randrange(32, 127) for _ in range(rnd.randrange(0, 40))).replace(b'"', b"") + b'"}\n'
        elif kind == 4:
            junk = b"\r\r\n\n\n"
        else:
            junk = (json.dumps({"cmd": "ping"}) + "\n").encode()
        e.send(junk)
        if i % 40 == 0:
            e.pump(0.3)
    e.send(b"A" * 7000 + b"\n")                                    # longer than the RX limit
    e.send(b'{"cmd":"echo","data":"' + b"B" * 5500 + b'"}\n')        # just under the limit
    e.pump(2.0)
    e.send({"cmd": "led", "mode": "auto"})
    e.send({"cmd": "display", "test": "off"})
    e.send({"cmd": "gif_abort"})
    e.send({"cmd": "gpio", "pin": 16, "op": "pullup"})
    e.pump(1.0)
    expect(not e.panicked(), "PANIC during fuzzing: " + " | ".join(l for l in e.log if "Guru" in l or "abort" in l or "Backtrace" in l)[:300])
    h = e.wait_boot(30)
    expect(h["ok"], "no hello after fuzz")
    expect(e.request({"cmd": "info"})["heap"] > 100000, "heap leak after fuzz")


def t_safe_mode(c):
    """Needs --safe-image (a build with -DDC_FORCE_SAFE_MODE): the emulator cannot survive a soft reset, so the
    crash-loop counter is exercised by the forced build instead of by real panics."""
    if not c.safe_image:
        print("        (skipped: no --safe-image given)")
        return
    import shutil
    work = c.safe_image + ".run"
    shutil.copyfile(c.safe_image, work)
    e = Emu(work, 5598)
    try:
        h = e.wait_boot(90)
        expect(h["safe"] is True and h["disp"] is False, f"forced safe mode: {h}")
        expect(e.request({"cmd": "display", "test": "fill"})["err"] == "no_display", "display commands refuse in safe mode")
        expect(e.request({"cmd": "snapshot"})["err"] == "no_display", "snapshot refuses in safe mode")
        expect(e.request({"cmd": "ping"})["ok"], "core still answers in safe mode")
        expect(e.request({"cmd": "led", "r": 5})["ok"], "LED still works in safe mode")
        i = e.request({"cmd": "info"})
        expect(i["safe"] is True and i["ok_disp"] is False and "display-skipped" in i["boot"], f"info in safe mode {i}")
        expect(i["safe_why"] == "forced", f"safe_why {i['safe_why']}")
        expect(e.request({"cmd": "safe_retry", "reboot": False})["ok"], "safe_retry acknowledged")
        expect(e.request({"cmd": "remap", "key": 1, "type": "text", "val": "x"})["ok"], "key remap works in safe mode")
        expect(e.request({"cmd": "mode", "val": 5})["ok"], "switching to GIF mode in safe mode must not touch the display")
        e.pump(1.5)
        expect(e.request({"cmd": "ping"})["ok"] and not e.panicked(), "firmware died in GIF mode while in safe mode")
        expect(e.request({"cmd": "selftest"}, timeout=20)["display"] == "unavailable", "selftest reports the display as unavailable")
    finally:
        e.close()


def t_layers(c):
    e = c.e
    e.request({"cmd": "reset_keys"})
    h = e.request({"cmd": "hello"})
    expect(h["layers"] == 3 and h["layer"] == 0 and h["modes"] == 6, f"hello layers/modes: {h}")
    for cap in ("layers", "mouse", "host", "info", "gifslots", "factory"):
        expect(cap in h["caps"], f"capability {cap} missing: {h['caps']}")
    expect("wifi" not in h["caps"] and "ota" not in h["caps"], f"the default build is cable-only: {h['caps']}")
    # layers are separate key tables; layer 0 keeps the old NVS names
    expect(e.request({"cmd": "remap", "key": 1, "layer": 1, "type": "text", "val": "L2K1"})["ok"], "remap layer 2")
    expect(e.request({"cmd": "remap", "key": 1, "layer": 2, "type": "text", "val": "L3K1"})["ok"], "remap layer 3")
    expect(e.request({"cmd": "remap", "key": 1, "layer": 3, "type": "text", "val": "x"})["err"] == "layer", "layer 4 invalid")
    expect(e.request({"cmd": "remap", "key": 1, "layer": -1, "type": "text", "val": "x"})["err"] == "layer", "layer -1 invalid")
    g0, g1, g2 = (e.request({"cmd": "getkeys", "layer": n}) for n in range(3))
    expect(g0["slots"][0]["def"] and not g1["slots"][0]["def"] and not g2["slots"][0]["def"], "layers must not share slots")
    expect(g1["layer"] == 1 and g1["cur"] == 0 and g1["layers"] == 3, f"getkeys layer info {g1}")
    expect(e.request({"cmd": "getkeys", "layer": 3})["err"] == "layer", "getkeys layer 4")
    # full spec of a single slot: stored value or the layer's default
    sp = e.request({"cmd": "getkeys", "layer": 1, "slot": 1})
    expect(sp["spec"] == {"type": "text", "val": "L2K1"} and sp["def"] is False, f"slot spec {sp}")
    sp = e.request({"cmd": "getkeys", "layer": 1, "slot": 2})
    expect(sp["def"] is True and sp["spec"] == {"type": "media", "val": "PLAY_PAUSE"}, f"default of layer 2 K2: {sp}")
    sp = e.request({"cmd": "getkeys", "layer": 0, "slot": 1})
    expect(sp["spec"] == {"type": "combo", "val": ["PRIMARY", "c"]}, f"default of layer 1 K1: {sp}")
    expect(e.request({"cmd": "getkeys", "layer": 0, "slot": 9})["err"] == "key", "slot 9")
    # switching
    e.msgs.clear()
    r = e.request({"cmd": "layer", "val": 1})
    expect(r["ok"] and r["n"] == 1, f"layer 1 {r}")
    expect(e.request({"cmd": "hello"})["layer"] == 1, "hello reports the active layer")
    expect(e.request({"cmd": "layer", "val": "next"})["n"] == 2, "next -> 2")
    expect(e.request({"cmd": "layer", "val": "next"})["n"] == 0, "next wraps")
    expect(e.request({"cmd": "layer", "val": "prev"})["n"] == 2, "prev wraps")
    expect(e.request({"cmd": "layer", "val": 7})["err"] == "layer" and e.request({"cmd": "layer", "val": "sideways"})["err"] == "layer", "bad layer")
    e.request({"cmd": "layer", "val": 0})
    # a key can switch layers (and says so through an event the app listens to)
    expect(e.request({"cmd": "remap", "key": 5, "type": "layer", "val": "next"})["ok"], "remap layer action")
    expect(e.request({"cmd": "remap", "key": 5, "type": "layer", "val": 9})["err"] == "spec", "layer action 9 invalid")
    e.msgs.clear()
    expect(e.request({"cmd": "input", "k": 5})["ok"], "press K5")
    e.pump(0.6)
    evs = list(e.msgs)                                                    # (the next request clears e.msgs)
    expect(e.request({"cmd": "hello"})["layer"] == 1, "K5 switched to layer 2")
    expect(any(m.get("evt") == "layer" and m.get("n") == 1 and "id" not in m for m in evs), f"layer event missing: {evs}")
    # the radial menu has a LAYER entry: turn to it, enter, turn, confirm
    e.request({"cmd": "layer", "val": 0})
    for step in ({"click": True}, {"turn": 3}, {"click": True}, {"turn": 1}, {"click": True}):
        expect(e.request({"cmd": "input", **step})["ok"], f"menu step {step}")
    e.pump(0.4)
    expect(e.request({"cmd": "hello"})["layer"] == 1, "menu LAYER entry switched the layer")
    e.request({"cmd": "layer", "val": 0})
    # selective reset
    expect(e.request({"cmd": "reset_keys", "layer": 1})["ok"], "reset layer 2")
    g1, g2 = e.request({"cmd": "getkeys", "layer": 1}), e.request({"cmd": "getkeys", "layer": 2})
    expect(g1["slots"][0]["def"] and not g2["slots"][0]["def"], "reset of one layer leaves the others")
    e.request({"cmd": "reset_keys"})
    expect(all(s["def"] for n in range(3) for s in e.request({"cmd": "getkeys", "layer": n})["slots"]), "reset_keys without layer clears all")
    img = rgb565be_to_image(snapshot(e))                              # layer badge: L2 pill at the bottom
    e.request({"cmd": "layer", "val": 1})
    e.pump(0.6)
    img2 = rgb565be_to_image(snapshot(e))
    c.save(img2, "layer_badge.png")
    expect(list(img.getdata()) != list(img2.getdata()), "no layer badge on the screen")
    e.request({"cmd": "layer", "val": 0})


def t_new_actions(c):
    e = c.e
    e.request({"cmd": "reset_keys"})
    e.request({"cmd": "mode", "val": 1})
    url = {"op": "url", "arg": "https://example.com/a?b=1"}
    expect(e.request({"cmd": "remap", "key": 1, "type": "host", "val": url})["ok"], "host url")
    for op, arg in (("app", "calc.exe"), ("shell", "echo hi"), ("file", "/tmp/x.txt"), ("notify", "done")):
        expect(e.request({"cmd": "remap", "key": 2, "type": "host", "val": {"op": op, "arg": arg}})["ok"], f"host {op}")
    expect(e.request({"cmd": "remap", "key": 2, "type": "host", "val": {"op": "clipboard"}})["ok"], "host clipboard needs no argument")
    expect(e.request({"cmd": "remap", "key": 2, "type": "host", "val": {"op": "url"}})["err"] == "spec", "url needs an argument")
    expect(e.request({"cmd": "remap", "key": 2, "type": "host", "val": {"op": "format-c", "arg": "x"}})["err"] == "spec", "unknown host op")
    expect(e.request({"cmd": "remap", "key": 2, "type": "host", "val": {"op": "url", "arg": "x" * 401}})["err"] == "spec", "host argument too long")
    for m in ({"btn": "left"}, {"btn": "right", "act": "double"}, {"btn": "middle", "act": "down"}, {"btn": "back", "act": "up"},
              {"wheel": -3}, {"wheel": 99}, {"move": [10, -10]}):
        expect(e.request({"cmd": "remap", "key": 3, "type": "mouse", "val": m})["ok"], f"mouse {m}")
    for m in ({"btn": "banana"}, {"btn": "left", "act": "tickle"}, {"move": [1]}, 7):
        expect(e.request({"cmd": "remap", "key": 3, "type": "mouse", "val": m})["err"] == "spec", f"bad mouse {m}")
    macro = [{"combo": ["CTRL", "c"]}, {"host": url}, {"mouse": {"btn": "left"}}, {"layer": "next"}, {"delay": 10}, {"layer": 0}]
    expect(e.request({"cmd": "remap", "key": 4, "type": "macro", "val": macro})["ok"], "macro mixing every step type")
    expect(e.request({"cmd": "remap", "key": 4, "type": "macro", "val": [{"host": {"op": "nope", "arg": "x"}}]})["err"] == "spec", "bad host step")
    expect(e.request({"cmd": "remap", "key": 4, "type": "macro", "val": [{"layer": 5}]})["err"] == "spec", "bad layer step")
    # a real key press makes the pad ask the app to open the URL
    expect(e.request({"cmd": "remap", "key": 1, "type": "host", "val": url})["ok"], "host url again")
    e.msgs.clear()
    expect(e.request({"cmd": "input", "k": 1})["ok"], "press K1")
    e.pump(0.6)
    ev = [m for m in e.msgs if m.get("evt") == "host"]
    expect(len(ev) == 1 and ev[0]["op"] == "url" and ev[0]["arg"] == url["arg"] and "id" not in ev[0], f"host event: {ev}")
    # the macro with host + mouse + layer steps runs to the end without crashing the firmware, ending back on layer 1
    e.request({"cmd": "remap", "key": 4, "type": "macro", "val": macro})
    e.msgs.clear()
    expect(e.request({"cmd": "input", "k": 4})["ok"], "press K4")
    e.pump(1.2)
    macro_evs = list(e.msgs)
    expect(e.request({"cmd": "hello"})["layer"] == 0 and not e.panicked(), "macro with layer steps")
    expect(any(m.get("evt") == "host" for m in macro_evs), "host step inside a macro did not fire")
    e.request({"cmd": "reset_keys"})


def t_info_screen(c):
    e = c.e
    expect(e.request({"cmd": "mode", "val": 6})["ok"], "mode 6")
    expect(e.request({"cmd": "hello"})["mode"] == 6, "mode 6 accepted")
    e.pump(0.8)
    empty = rgb565be_to_image(snapshot(e))
    c.save(empty, "info_nodata.png")
    expect(len(set(empty.getdata())) >= 4, "info screen without data is blank")
    cards = [{"k": "m", "label": "NOW PLAYING", "t": "Midnight City", "a": "M83", "b": "Hurry Up, We're Dreaming"},
             {"k": "w", "label": "WEATHER  Zurich", "t": "21 C", "a": "Partly cloudy", "b": "wind 9 km/h"},
             {"k": "e", "label": "NEXT EVENT", "t": "14:30", "a": "Design review with a very long title", "b": "in 25 min"}]
    r = e.request({"cmd": "info_cards", "cards": cards, "badges": [{"name": "mail", "n": 3}, {"name": "chat", "n": 120}], "rot": 2})
    expect(r["ok"] and r["cards"] == 3 and r["badges"] == 2, f"info_cards reply {r}")
    e.pump(0.8)
    full = rgb565be_to_image(snapshot(e))
    c.save(full, "info_cards.png")
    expect(list(full.getdata()) != list(empty.getdata()), "info cards did not change the screen")
    e.pump(2.6)                                                            # rotation: the next card is drawn
    nxt = rgb565be_to_image(snapshot(e))
    c.save(nxt, "info_cards_next.png")
    expect(list(nxt.getdata()) != list(full.getdata()), "cards do not rotate")
    expect(e.request({"cmd": "info_cards", "cards": [{}] * 9, "badges": [{"n": 5}] * 9})["cards"] == 4, "cards are limited to 4")
    expect(e.request({"cmd": "info_cards", "cards": []})["cards"] == 0, "empty card list")
    expect(e.request({"cmd": "ping"})["ok"] and not e.panicked(), "alive after info updates")
    # the menu cycles through 6 display modes and the long press moves on from mode 6 to 1
    expect(e.request({"cmd": "input", "hold": True})["ok"], "hold = next mode")
    e.pump(0.5)
    expect(e.request({"cmd": "hello"})["mode"] == 1, "mode 6 -> 1")
    e.request({"cmd": "mode", "val": 1})


def t_gif_slots(c):
    e = c.e
    data = make_gif(4, 120)
    for sl in (1, 2):
        r = upload(e, data, slot=sl)
        expect(r["ok"] and r["evt"] == "gif_done", f"upload slot {sl}: {r}")
    expect(upload(e, data, slot=4).get("err") == "slot", "slot 4 invalid")
    expect(upload(e, data, slot=-1).get("err") == "slot", "slot -1 invalid")
    lst = e.request({"cmd": "gif_list"})
    slots = {x["s"]: x["size"] for x in lst["slots"]}
    expect(1 in slots and 2 in slots and slots[1] == len(data) and lst["cur"] == 2 and lst["max"] == 4, f"gif_list {lst}")
    h = e.request({"cmd": "hello"})
    expect(h["gifs"] >= 2 and h["mode"] == 5, f"hello after slot uploads {h}")
    e.pump(1.5)
    expect(e.request({"cmd": "ping"})["ok"] and not e.panicked(), "firmware died playing a slot")
    # explicit selection and rotation
    expect(e.request({"cmd": "gif_cfg", "slot": 1})["cur"] == 1, "select slot 1")
    expect(e.request({"cmd": "gif_cfg", "slot": 3})["err"] == "slot", "slot 3 is empty")
    r = e.request({"cmd": "gif_cfg", "rot": 1})
    expect(r["rot"] == 1, f"rotation set {r}")
    seen, t0 = set(), time.time()
    while time.time() - t0 < 30 and len(seen) < 2:
        e.pump(0.7)
        seen.add(e.request({"cmd": "gif_list"})["cur"])
    expect(len(seen) >= 2, f"rotation never changed slot: {seen}")
    expect(e.request({"cmd": "gif_cfg", "rot": 0})["rot"] == 0, "rotation off")
    # delete: slot 1 goes away, others stay; deleting everything regenerates the demo
    expect(e.request({"cmd": "gif_delete", "slot": 1})["ok"], "delete slot 1")
    expect(1 not in {x["s"] for x in e.request({"cmd": "gif_list"})["slots"]}, "slot 1 still listed")
    expect(e.request({"cmd": "gif_delete", "slot": 9})["err"] == "slot", "bad slot delete")
    e.request({"cmd": "gif_delete", "slot": 2})
    e.request({"cmd": "gif_delete", "slot": 0})
    t0 = time.time()
    while time.time() - t0 < 120:
        e.pump(1.0)
        if e.request({"cmd": "hello"})["gif"]:
            break
    expect(e.request({"cmd": "hello"})["gif"] is True and not e.panicked(), "demo animation not regenerated after deleting every slot")
    e.request({"cmd": "mode", "val": 1})


def t_gestures(c):
    """Hold / double-tap actions: stored per key and layer, and the gesture state machine with synthetic timing."""
    e = c.e
    e.request({"cmd": "reset_keys"})

    def gt(seq, hold, dbl):
        r = e.request({"cmd": "gesture_test", "hold": hold, "dbl": dbl, "seq": seq})
        expect(r["ok"], f"gesture_test {r}")
        return r["fired"]
    # a plain key acts the moment it is pressed (nothing changes for keys without gestures)
    expect(gt([[1, 0], [0, 100]], False, False) == [[0, "tap"]], "plain key: tap on press")
    expect(gt([[1, 0], [0, 900]], False, False) == [[0, "tap"]], "plain key held long: still one tap")
    # hold only: tap on release (short), hold after 450 ms (and no tap afterwards)
    expect(gt([[1, 0], [0, 200]], True, False) == [[200, "tap"]], "hold-key short press = tap on release")
    expect(gt([[1, 0], [0, 700]], True, False) == [[450, "hold"]], "hold-key long press = hold at 450 ms, no tap")
    expect(gt([[1, 0], [0, 449]], True, False) == [[449, "tap"]], "449 ms is still a tap")
    # double-tap only: single tap is delayed by the 260 ms window
    expect(gt([[1, 0], [0, 80]], False, True) == [[341, "tap"]], "single tap fires when the double-tap window closes")
    expect(gt([[1, 0], [0, 80], [1, 200], [0, 260]], False, True) == [[200, "double"]], "second press inside the window = double")
    expect(gt([[1, 0], [0, 50], [1, 100], [0, 150], [1, 200], [0, 250]], False, True) == [[100, "double"], [511, "tap"]], "triple = double + tap")
    expect(gt([[1, 0], [0, 100], [1, 400], [0, 450]], False, True) == [[361, "tap"], [711, "tap"]], "two slow taps = two taps")
    # both
    expect(gt([[1, 0], [0, 100]], True, True) == [[361, "tap"]], "both: single tap")
    expect(gt([[1, 0], [0, 600]], True, True) == [[450, "hold"]], "both: hold")
    expect(gt([[1, 0], [0, 100], [1, 300], [0, 400]], True, True) == [[300, "double"]], "both: double")
    expect(gt([[1, 0], [0, 100], [1, 300], [0, 900]], True, True) == [[300, "double"]], "both: holding the 2nd press does not add a hold after a double")
    # bad input
    for bad in ([], [[1]], [[1, 100], [0, 50]], [[1, 0]] * 30):
        expect(e.request({"cmd": "gesture_test", "seq": bad})["err"] == "seq", f"bad seq {bad[:2]}")
    # storage: per key, per layer, per gesture; defaults do not exist for gestures
    n = {"op": "notify"}
    expect(e.request({"cmd": "remap", "key": 2, "gesture": "hold", "type": "host", "val": dict(n, arg="hold2")})["ok"], "remap hold")
    expect(e.request({"cmd": "remap", "key": 2, "gesture": "double", "layer": 1, "type": "host", "val": dict(n, arg="dbl2")})["ok"], "remap double on layer 2")
    g0, g1 = e.request({"cmd": "getkeys", "layer": 0}), e.request({"cmd": "getkeys", "layer": 1})
    expect(g0["slots"][1]["h"] is True and g0["slots"][1]["d"] is False and g1["slots"][1]["d"] is True and g1["slots"][1]["h"] is False, f"flags {g0['slots'][1]} {g1['slots'][1]}")
    expect(g0["slots"][0]["h"] is False and "h" not in g0["slots"][5], "keys 1-5 only carry gesture flags")
    r = e.request({"cmd": "getkeys", "layer": 0, "slot": 2, "gesture": "hold"})
    expect(r["set"] is True and r["spec"] == {"type": "host", "val": {"op": "notify", "arg": "hold2"}}, f"gesture spec {r}")
    r = e.request({"cmd": "getkeys", "layer": 0, "slot": 2, "gesture": "double"})
    expect(r["set"] is False and "spec" not in r, f"unset gesture {r}")
    expect(e.request({"cmd": "remap", "key": 6, "gesture": "hold", "type": "text", "val": "x"})["err"] == "key", "dial has no gestures")
    expect(e.request({"cmd": "remap", "key": 1, "gesture": "wiggle", "type": "text", "val": "x"})["err"] == "gesture", "unknown gesture")
    expect(e.request({"cmd": "remap", "key": 1, "gesture": "hold", "type": "banana", "val": "x"})["err"] == "spec", "gesture spec is validated like a tap spec")
    expect(e.request({"cmd": "getkeys", "slot": 6, "gesture": "hold"})["err"] == "gesture", "getkeys gesture on the dial")
    # virtual presses: tap / hold / double run the matching action (the pad asks the app through host events)
    expect(e.request({"cmd": "remap", "key": 2, "type": "host", "val": dict(n, arg="tap2")})["ok"], "tap action")
    expect(e.request({"cmd": "remap", "key": 2, "gesture": "double", "type": "host", "val": dict(n, arg="dbl2-l1")})["ok"], "double on layer 1")
    got = {}
    for g in ("tap", "hold", "double"):
        e.msgs.clear()
        expect(e.request({"cmd": "input", "k": 2, "g": g})["ok"], f"virtual {g}")
        e.pump(0.5)
        got[g] = [m["arg"] for m in e.msgs if m.get("evt") == "host"]
    expect(got == {"tap": ["tap2"], "hold": ["hold2"], "double": ["dbl2-l1"]}, f"virtual gestures: {got}")
    expect(e.request({"cmd": "input", "k": 2, "g": "wiggle"})["err"] == "gesture", "virtual unknown gesture")
    e.msgs.clear(); e.request({"cmd": "input", "k": 3, "g": "hold"}); e.pump(0.3)
    expect(not [m for m in e.msgs if m.get("evt") == "host"], "a gesture that is not set does nothing")
    # persistence across a power cycle, clearing, reset
    e.power_cycle(); e.wait_boot(90, need_fs=False)
    g0 = e.request({"cmd": "getkeys", "layer": 0})
    expect(g0["slots"][1]["h"] is True and g0["slots"][1]["d"] is True, f"gesture flags after power cycle: {g0['slots'][1]}")
    expect(e.request({"cmd": "remap", "key": 2, "gesture": "hold", "clear": True})["ok"], "clear a gesture")
    expect(e.request({"cmd": "getkeys", "layer": 0})["slots"][1]["h"] is False, "cleared")
    expect(e.request({"cmd": "reset_keys", "layer": 0})["ok"], "reset layer 1")
    g0 = e.request({"cmd": "getkeys", "layer": 0})
    expect(all(not s.get("h") and not s.get("d") for s in g0["slots"]) and e.request({"cmd": "getkeys", "layer": 1})["slots"][1]["d"] is True, "reset clears gestures of that layer only")
    expect(e.request({"cmd": "factory", "confirm": True, "what": "keys"})["ok"], "factory keys")
    expect(not any(s.get("d") for s in e.request({"cmd": "getkeys", "layer": 1})["slots"]), "factory keys also clears gestures")
    e.request({"cmd": "reset_keys"})


def t_settings(c):
    """Dial acceleration, clock styles, screensaver and night dimming (the v1.3 behaviour settings)."""
    e = c.e
    h = e.request({"cmd": "hello"})
    for cap in ("hostx", "gestures", "dialaccel", "clockstyle", "saver", "nightdim"):
        expect(cap in h["caps"], f"capability {cap} missing: {h['caps']}")
    expect(h["fw"] == "1.3.0", f"firmware version {h['fw']}")
    st = e.request({"cmd": "settings"})
    expect(st["evt"] == "settings" and st["dial_accel"] == 0 and st["clock_style"] == 0 and st["saver_s"] == 0 and st["night_on"] is False, f"defaults {st}")
    for bad in ({"dial_accel": 3}, {"clock_style": 4}, {"saver_s": 3601}, {"saver_style": 0}, {"night_from": 24}, {"night_level": 4}, {"night_on": 1}, {"dial_accel": "x"}):
        expect(e.request({"cmd": "settings", **bad})["err"] == "settings", f"accepted {bad}")
    expect(e.request({"cmd": "settings"})["dial_accel"] == 0, "a refused settings call changes nothing")
    # ---- dial acceleration (virtual turns with a pretend interval between detents)
    expect(e.request({"cmd": "remap", "key": 6, "type": "host", "val": {"op": "notify", "arg": "cw"}})["ok"], "dial action")
    def runs(turn, dt):
        e.msgs.clear()
        r = e.request({"cmd": "input", "turn": turn, "dt": dt})
        e.pump(0.4)
        return r["runs"], len([m for m in e.msgs if m.get("evt") == "host"])
    expect(runs(1, 10) == (1, 1), "acceleration off: one run")
    e.request({"cmd": "settings", "dial_accel": 1})
    expect([runs(1, dt)[0] for dt in (10, 60, 200)] == [3, 2, 1], "level 1: fast = 3x, medium = 2x, slow = 1x")
    expect(runs(2, 40)[0] == 6, "per-detent speed counts: 2 detents in 40 ms = 20 ms each")
    e.request({"cmd": "settings", "dial_accel": 2})
    expect([runs(1, dt)[0] for dt in (10, 60, 100, 200)] == [6, 4, 2, 1], "level 2")
    expect(runs(20, 0)[0] == 24, "bounded to 24 runs")
    r = runs(-1, 10); expect(r[0] == 6, "counter-clockwise accelerates too")
    e.request({"cmd": "settings", "dial_accel": 0}); e.request({"cmd": "reset_keys"})
    # ---- clock styles render different, non-empty pictures
    import calendar
    noon = calendar.timegm((2026, 10, 3, 23, 30, 5))
    e.request({"cmd": "time", "epoch": noon, "tz": 0}); e.request({"cmd": "mode", "val": 1})
    imgs = []
    for style in range(4):
        expect(e.request({"cmd": "settings", "clock_style": style})["clock_style"] == style, f"style {style}")
        e.pump(0.6)
        img = rgb565be_to_image(snapshot(e))
        lit = sum(1 for px in img.getdata() if px != (0, 0, 0))
        expect(lit > 800, f"clock style {style} looks empty ({lit} lit pixels)")
        c.save(img, f"clock_style_{style}.png")
        imgs.append(img.tobytes())
    expect(len(set(imgs)) == 4, "the four clock styles must look different")
    e.request({"cmd": "settings", "clock_style": 0})
    # ---- night dimming
    expect(e.request({"cmd": "brightness", "val": 200})["ok"], "brightness")
    st = e.request({"cmd": "settings", "night_on": True, "night_from": 22, "night_to": 7, "night_level": 20})
    expect(st["bl"] == 20, f"23:30 is inside 22-07: backlight capped to 20, got {st['bl']}")
    expect(e.request({"cmd": "info"})["bl"] == 20 and e.request({"cmd": "info"})["bright"] == 200, "the user's brightness is kept, only the output is capped")
    e.request({"cmd": "time", "epoch": calendar.timegm((2026, 10, 3, 12, 0, 0)), "tz": 0})
    t0 = time.time()
    while time.time() - t0 < 20 and e.request({"cmd": "info"})["bl"] != 200:
        e.pump(1.0)
    expect(e.request({"cmd": "info"})["bl"] == 200, "noon is outside the night window: the loop restores the brightness on its own")
    e.request({"cmd": "time", "epoch": calendar.timegm((2026, 10, 3, 3, 0, 0)), "tz": 0})
    expect(e.request({"cmd": "settings", "night_from": 2, "night_to": 5})["bl"] == 20, "a window that does not wrap midnight")
    expect(e.request({"cmd": "settings", "night_from": 4, "night_to": 4})["bl"] == 200, "from == to = never")
    expect(e.request({"cmd": "settings", "night_from": 0, "night_to": 23, "night_level": 250})["bl"] == 200, "the cap never raises the brightness")
    e.request({"cmd": "settings", "night_on": False})
    # ---- screensaver
    e.request({"cmd": "mode", "val": 1})
    expect(e.request({"cmd": "settings", "saver_s": 1, "saver_style": 1})["saver_s"] == 1, "saver on")
    t0 = time.time()
    while time.time() - t0 < 6 and not e.request({"cmd": "info"})["saver_on"]:
        e.pump(0.5)
    expect(e.request({"cmd": "info"})["saver_on"] is True, "screensaver did not start after 1 s idle")
    for style in (1, 2, 3):
        e.request({"cmd": "settings", "saver_style": style}); e.pump(0.8)
        img = rgb565be_to_image(snapshot(e))
        lit = sum(1 for px in img.getdata() if px != (0, 0, 0))
        expect(0 < lit < 12000, f"saver style {style}: {lit} lit pixels")
        c.save(img, f"saver_{style}.png")
    e.request({"cmd": "input", "turn": 1})                       # any dial turn / key press wakes it
    expect(e.request({"cmd": "info"})["saver_on"] is False, "input wakes the screensaver")
    e.pump(1.8)
    expect(e.request({"cmd": "info"})["saver_on"] is True, "and it comes back after the idle time")
    e.request({"cmd": "mode", "val": 5}); e.pump(1.8)
    expect(e.request({"cmd": "info"})["saver_on"] is False, "the GIF screen never gets a screensaver")
    e.request({"cmd": "mode", "val": 1})
    expect(e.request({"cmd": "settings", "saver_s": 0})["saver_s"] == 0, "saver off")
    e.pump(1.2)
    expect(e.request({"cmd": "info"})["saver_on"] is False, "saver off wakes the screen")
    # ---- everything persists across a power cycle
    e.request({"cmd": "settings", "dial_accel": 2, "clock_style": 3, "saver_s": 600, "saver_style": 2, "night_on": True, "night_from": 21, "night_to": 6, "night_level": 33})
    e.power_cycle(); e.wait_boot(90, need_fs=False)
    st = e.request({"cmd": "settings"})
    expect((st["dial_accel"], st["clock_style"], st["saver_s"], st["saver_style"], st["night_on"], st["night_from"], st["night_to"], st["night_level"]) ==
           (2, 3, 600, 2, True, 21, 6, 33), f"settings after power cycle: {st}")
    e.request({"cmd": "settings", "dial_accel": 0, "clock_style": 0, "saver_s": 0, "saver_style": 1, "night_on": False, "night_from": 22, "night_to": 7, "night_level": 30})
    e.request({"cmd": "brightness", "val": 200})
    expect(not e.panicked(), "no crash")


def t_recovery(c):
    e = c.e
    expect(e.request({"cmd": "factory"})["err"] == "confirm", "factory needs confirm")
    expect(e.request({"cmd": "factory", "confirm": True, "what": "everything"})["err"] == "what", "unknown factory target")
    e.request({"cmd": "remap", "key": 1, "type": "text", "val": "keep me?"})
    e.request({"cmd": "remap", "key": 1, "layer": 2, "type": "text", "val": "and me?"})
    expect(e.request({"cmd": "factory", "confirm": True, "what": "keys"})["ok"], "factory keys")
    expect(all(s["def"] for n in range(3) for s in e.request({"cmd": "getkeys", "layer": n})["slots"]), "factory keys leaves stored keys behind")
    e.request({"cmd": "brightness", "val": 40})
    expect(e.request({"cmd": "factory", "confirm": True, "what": "settings", "reboot": False})["ok"], "factory settings")
    expect(e.request({"cmd": "ping"})["ok"], "alive after factory settings")
    r = e.request({"cmd": "boot_opt", "nodisp": True})
    expect(r["ok"] and r["nodisp"] is True, f"boot_opt {r}")
    expect(e.request({"cmd": "info"})["nodisp"] is True, "info shows nodisp")
    expect(e.request({"cmd": "boot_opt", "nodisp": False})["nodisp"] is False, "nodisp off again")
    expect(e.request({"cmd": "safe_retry", "reboot": False})["ok"], "safe_retry")
    expect(e.request({"cmd": "info"})["crashes"] == 0, "crash counter cleared")
    expect(e.request({"cmd": "ota", "val": True})["err"] in ("wifi_disabled", "no_wifi"), "OTA is off in the default (cable-only) build")
    expect(e.request({"cmd": "wifi", "ssid": "x", "pass": "y"}).get("err") == "wifi_disabled", "wifi command refused in the default build")
    expect(e.request({"cmd": "info"})["ip"] == "", "no IP without Wi-Fi")
    e.request({"cmd": "brightness", "val": 200})


TESTS = [t_first_boot_responsive, t_hello, t_ping_echo_id, t_info, t_led, t_gpio, t_inputs, t_display_and_snapshot, t_modes_render, t_virtual_input,
         t_events, t_remap_persistence, t_gif, t_layers, t_new_actions, t_info_screen, t_gif_slots, t_gestures, t_settings, t_recovery, t_selftest_misc, t_fuzz, t_safe_mode]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("flash")
    ap.add_argument("--out", default="emulator_test_out")
    ap.add_argument("--only", default="")
    ap.add_argument("--port", type=int, default=5599)
    ap.add_argument("--safe-image", default="", help="flash image built with -DDC_FORCE_SAFE_MODE (enables the safe-mode test)")
    a = ap.parse_args()
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    work = a.flash + ".run"                                       # tests mutate flash (NVS / LittleFS): use a scratch copy
    import shutil
    shutil.copyfile(a.flash, work)
    e = Emu(work, a.port)
    c = Ctx(e, a.out, a.safe_image or None)
    failed = 0
    try:
        t0 = time.time()
        c.first_hello = e.wait_boot(90, need_fs=False)
        c.first_hello_s = time.time() - t0
        print("firmware booted in the emulator")
        for t in TESTS:
            if only and t.__name__[2:] not in only and t.__name__ not in only:
                continue
            t0 = time.time()
            if t is t_hello:
                e.wait_boot(240)                                   # blank-flash first boot: wait for format + demo GIF
            try:
                t(c)
                print(f"  PASS  {t.__name__:28s} {time.time() - t0:5.1f}s")
            except Exception as ex:                               # noqa: BLE001
                failed += 1
                print(f"  FAIL  {t.__name__:28s} {ex!r}")
                for l in e.log[-8:]:
                    print("        console:", l[:160])
                try:
                    e.wait_boot(30)
                except Exception:
                    print("        (firmware no longer answers)")
        errs = sorted({l.split(") ", 1)[-1][:150] for l in e.log if l.startswith("E (") and "task_wdt" not in l})
        if errs:
            print("\nconsole error lines seen (informational):")
            for l in errs:
                print("   ", l)
        print("\nALL PASSED" if not failed else f"\n{failed} TEST(S) FAILED")
    finally:
        c.e.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
