"""The simulated pad's firmware 2.0 part: the protocol of the new commands in pure Python (validation, storage, replies) so the app can be used and tested without
hardware.  It mirrors DeskCompanion/*.h; tools/native/diff_test.py runs the same request script against this class and against the real sketch (native build) and
compares the answers.  What only a pad can do (rendering, USB classes, real timing) is not here: the app asks the pad, the pad draws.

Only the standard library is imported at module level (core.base imports this file); helpers from core.base are imported inside the methods."""
import base64
import hmac
import hashlib
import json
import math
import re
import struct
import time

PROTO20 = 20
NEW20_CAPS = ["leader", "tiers", "shiftlayer", "modtap", "repcurve", "dialcurve", "inertia", "trackball", "jog", "enccal", "swhealth", "usbwake",
              "legend", "icons", "snippets", "radial", "typer", "totp", "pin", "confirm", "macroring", "sketch", "calc",
              "timers", "calendar", "blocks", "puzzles", "space", "stream", "clock3", "saver3", "ripples", "pet2", "fold", "aa",
              "midi", "gamepad", "usbdrive", "screenbright", "ledevents", "suspenddim", "lifetime", "crumbs"]
APP_NAMES = {21: "SNIPPETS", 22: "RADIAL", 23: "TYPER", 24: "TOTP", 25: "SKETCH", 26: "CALC", 27: "TIMERS", 28: "CALENDAR", 29: "BLOCKS", 30: "PUZZLES", 31: "SPACE", 32: "STREAM"}
NUM_MODES20 = 32
SETTINGS20_DEFAULT = {"repeat_curve": 0, "wheel_inertia": False, "usb_wake": True, "enc_edges": 2, "enc_invert": False, "legend": 0, "macro_ring": True,
                      "suspend_dim": True, "ripples": False, "aa": False, "fold_text": False, "dial_curve": [0, 0, 0], "scr_bright": [0] * 32, "scr_saver": [0] * 32}
SETTINGS20_RANGE = {"repeat_curve": (0, 1), "enc_edges": (1, 4), "legend": (0, 2)}
SETTINGS20_BOOL = ("wheel_inertia", "usb_wake", "enc_invert", "macro_ring", "suspend_dim", "ripples", "aa", "fold_text")
GESTURES20 = ("tap", "hold", "double", "triple", "hold2", "hold3", "shift")

_FOLD_L1 = ["A", "A", "A", "A", "Ae", "A", "AE", "C", "E", "E", "E", "E", "I", "I", "I", "I", "D", "N", "O", "O", "O", "O", "Oe", "x", "O", "U", "U", "U", "Ue", "Y", "Th", "ss",
            "a", "a", "a", "a", "ae", "a", "ae", "c", "e", "e", "e", "e", "i", "i", "i", "i", "d", "n", "o", "o", "o", "o", "oe", "/", "o", "u", "u", "u", "ue", "y", "th", "y"]
_FOLD_GR = ["a", "b", "g", "d", "e", "z", "e", "th", "i", "k", "l", "m", "n", "x", "o", "p", "r", "s", "s", "t", "u", "f", "ch", "ps", "o"]
_FOLD_CY = ["A", "B", "V", "G", "D", "E", "Zh", "Z", "I", "J", "K", "L", "M", "N", "O", "P", "R", "S", "T", "U", "F", "Kh", "Ts", "Ch", "Sh", "Shch", "", "Y", "", "E", "Yu", "Ya",
            "a", "b", "v", "g", "d", "e", "zh", "z", "i", "j", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]
_FOLD_X = "AaAaAaCcCcCcCcDdDdEeEeEeEeEeGgGgGgGgHhHhIiIiIiIiIiJjJjKkkLlLlLlLlLlNnNnNnnNnOoOoOoOoRrRrRrSsSsSsSsTtTtTtUuUuUuUuUuUuWwYyYZzZzZzs"


def fold_text(s):
    """The same folding as foldText() in apps_sys.h: accents, Greek and Cyrillic to the nearest ASCII; unknown characters become '?'."""
    out = []
    for ch in s:
        cp = ord(ch)
        if cp < 0x80:
            out.append(ch)
            continue
        r = None
        if 0xC0 <= cp <= 0xFF:
            r = _FOLD_L1[cp - 0xC0]
        elif 0x100 <= cp <= 0x17F and cp - 0x100 < len(_FOLD_X):
            r = _FOLD_X[cp - 0x100]
        elif 0x391 <= cp <= 0x3A9:
            g = _FOLD_GR[cp - 0x391]
            r = g[0].upper() + g[1:3]
        elif 0x3B1 <= cp <= 0x3C9:
            r = _FOLD_GR[cp - 0x3B1]
        elif 0x410 <= cp <= 0x44F:
            r = _FOLD_CY[cp - 0x410]
        elif cp == 0x401:
            r = "Yo"
        elif cp == 0x451:
            r = "yo"
        elif cp == 0x20AC:
            r = "EUR"
        elif cp in (0x2013, 0x2014):
            r = "-"
        elif cp in (0x2018, 0x2019):
            r = "'"
        elif cp in (0x201C, 0x201D):
            r = '"'
        elif cp == 0x2026:
            r = "..."
        elif cp == 0xB0:
            r = "deg"
        out.append(r if r is not None else "?")
    return "".join(out)


_WC_HOURS = ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN", "ELEVEN", "TWELVE"]


def word_clock(h, m):
    """'IT IS HALF PAST TEN' - the sentence the word clock lights up"""
    m5 = (m + 2) // 5 * 5
    if m5 >= 60:
        m5, h = 0, h + 1
    to = m5 > 30
    hour = _WC_HOURS[((h + 1 if to else h) + 11) % 12]
    mins = {0: "", 5: "FIVE", 10: "TEN", 15: "A QUARTER", 20: "TWENTY", 25: "TWENTY FIVE", 30: "HALF", 35: "TWENTY FIVE", 40: "TWENTY", 45: "A QUARTER", 50: "TEN", 55: "FIVE"}[m5]
    return f"IT IS {hour} OCLOCK" if m5 == 0 else f"IT IS {mins} {'TO' if to else 'PAST'} {hour}"


def base32_decode(s):
    bits = acc = 0
    out = bytearray()
    for c in s:
        if c in " =-":
            continue
        if "A" <= c <= "Z":
            v = ord(c) - 65
        elif "a" <= c <= "z":
            v = ord(c) - 97
        elif "2" <= c <= "7":
            v = ord(c) - 50 + 26
        else:
            return None
        acc = (acc << 5) | v
        bits += 5
        if bits >= 8:
            if len(out) >= 32:
                return None
            out.append((acc >> (bits - 8)) & 255)
            bits -= 8
            acc &= (1 << bits) - 1
    return bytes(out)


def totp_code(key, t, period=30, digits=6):
    h = hmac.new(key, struct.pack(">Q", int(t) // period), hashlib.sha1).digest()
    o = h[19] & 15
    return str(((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits)).zfill(digits)


def _days_from_civil(y, m, d):
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    return era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy - 719468


def sun_times(y, m, d, lat, lon):
    """sunrise / sunset (unix seconds, UTC date) with the NOAA sunrise equation; None in polar day / night"""
    n = math.floor(_days_from_civil(y, m, d) + 2440587.5 + 0.5 - 2451545.0 + 0.0008)
    js = n - lon / 360.0
    M = (357.5291 + 0.98560028 * js) % 360.0
    Mr = math.radians(M)
    C = 1.9148 * math.sin(Mr) + 0.02 * math.sin(2 * Mr) + 0.0003 * math.sin(3 * Mr)
    lam = math.radians((M + C + 180.0 + 102.9372) % 360.0)
    jt = 2451545.0 + js + 0.0053 * math.sin(Mr) - 0.0069 * math.sin(2 * lam)
    sd = math.sin(lam) * math.sin(math.radians(23.4397))
    cd = math.cos(math.asin(sd))
    cw = (math.sin(math.radians(-0.833)) - math.sin(math.radians(lat)) * sd) / (math.cos(math.radians(lat)) * cd)
    if cw < -1 or cw > 1:
        return None
    w = math.degrees(math.acos(cw))
    return (jt - w / 360.0 - 2440587.5) * 86400.0, (jt + w / 360.0 - 2440587.5) * 86400.0


def moon_age(unix):
    jd = unix / 86400.0 + 2440587.5
    a = (jd - 2451550.1) % 29.530588853
    return a


def _ascii_text(t, maxlen, nl=False):
    return isinstance(t, str) and len(t) <= maxlen and all((32 <= ord(c) <= 126) or (nl and c == "\n") for c in t)


def _hexn(s, n):
    return isinstance(s, str) and len(s) == n and re.fullmatch(r"[0-9a-fA-F]+", s) is not None


def fat12_image(files):
    """A 128 KB FAT12 image (the drive of cmd usbdrive): files = [(8.3 name, bytes)] in the order README, SETTINGS, KEYS, LIFETIME"""
    SEC, TOTAL = 512, 256
    img = bytearray(SEC * TOTAL)
    img[0:3] = b"\xEB\x3C\x90"
    img[3:11] = b"DESKCOMP"
    struct.pack_into("<HBHBHHBHHH", img, 11, SEC, 1, 1, 2, 32, TOTAL, 0xF8, 1, 32, 1)
    img[36] = 0x80
    img[38] = 0x29
    struct.pack_into("<I", img, 39, 0xDC200001)
    img[43:54] = b"DESKCOMPAN "
    img[54:62] = b"FAT12   "
    img[510:512] = b"\x55\xAA"
    fat = bytearray(SEC)
    fat[0:3] = b"\xF8\xFF\xFF"
    root = bytearray(SEC * 2)
    root[0:11] = b"DESKCOMPAN "
    root[11] = 0x08
    data0 = 1 + 2 + 2
    cluster, maxc = 2, TOTAL - data0 + 1

    def setfat(n, v):
        o = n * 3 // 2
        if n & 1:
            fat[o] = (fat[o] & 0x0F) | ((v << 4) & 0xF0)
            fat[o + 1] = (v >> 4) & 0xFF
        else:
            fat[o] = v & 0xFF
            fat[o + 1] = (fat[o + 1] & 0xF0) | ((v >> 8) & 0x0F)
    for f, (name, content) in enumerate(files):
        e = 32 * (f + 1)
        root[e:e + 11] = name.encode().ljust(11)[:11]
        root[e + 11] = 0x21
        need = max(1, (len(content) + SEC - 1) // SEC)
        ln = len(content)
        if cluster + need - 1 > maxc:
            need = maxc - cluster + 1
            ln = max(0, need) * SEC
            need = max(0, need)
        struct.pack_into("<HI", root, e + 26, cluster if need else 0, ln)
        for k in range(need):
            setfat(cluster + k, 0xFFF if k + 1 == need else cluster + k + 1)
            chunk = content[k * SEC:(k + 1) * SEC]
            img[SEC * (data0 + cluster - 2 + k):SEC * (data0 + cluster - 2 + k) + len(chunk)] = chunk
        cluster += need
    img[SEC:SEC * 2] = fat
    img[SEC * 2:SEC * 3] = fat
    img[SEC * 3:SEC * 5] = root
    return bytes(img)


class Fw20Sim:
    """Mixin of SimFirmware: state and command handlers of firmware 2.0."""

    def init20(self):
        self.s20 = json.loads(json.dumps(SETTINGS20_DEFAULT))
        self.leader_seqs = []
        self.sw_press, self.sw_chatter = [0] * 5, [0] * 5
        self.enc = {"cal": 0, "edges": 2, "invert": False}
        self.snippets, self.radial, self.icons_key, self.icons_radial = [], [{} for _ in range(8)], {}, {}
        self.totp_accs, self.pin = [None] * 10, {"pin": None, "locked": False, "all": False, "idle": 0, "fails": 0}
        self.cal_months, self.zones, self.geo = [], [], None
        self.sketch = bytearray(200)
        self.sketch_saved = False
        self.sim_time = None                                      # epoch the app set with {"cmd":"time"} (None = the PC's clock)
        self.crumbs, self.crumbs_prev, self.boots20 = [], [], 1
        self.life = {"dial_cw": 0, "dial_ccw": 0, "dial_click": 0, "minutes": 0, "boots": 1}
        self.led_events = []
        self.lev = {"active": None, "queue": [], "played": 0, "until": 0.0, "step": 0, "reps": 0}
        self.pet = {"cheer": 0.0, "sad": 0.0, "fun": 80, "cpu": 0, "cpu_at": 0.0}
        self.usbx, self.usbx_boot, self.midi_on, self.pad_on = 0, 0, False, False
        self.usb_cfg = {"base": 36, "channel": 1, "cc": 20, "velocity": 100, "axis_step": 8}
        self.stream = {"active": False, "buf": None, "frames": 0, "tiles": 0, "bytes": 0}
        self.crumb(8, "BOOT r1 c0")

    def _fw20(self):
        return self._fw16() and not self.fw16only

    # ------------------------------------------------------------------ helpers
    def crumb(self, kind, text):
        self.crumbs.append({"ms": self._up_ms(), "k": kind, "s": text})
        del self.crumbs[:-48]

    def _now(self):
        return self.sim_time if self.sim_time is not None else int(time.time())

    def _unlocked(self, secret=True):
        return not (self.pin["pin"] and self.pin["locked"] and (secret or self.pin["all"]))

    def lev_tick(self):
        """advance the LED event player to 'now' (the state machine of ledEventActive())"""
        now = time.monotonic()
        a = self.lev["active"]
        while a is not None and now >= self.lev["until"]:
            ev = self.led_events[a]
            self.lev["step"] += 1
            if self.lev["step"] >= len(ev["steps"]):
                self.lev["reps"] -= 1
                if self.lev["reps"] > 0:
                    self.lev["step"] = 0
                else:
                    a = None
                    self.lev["active"] = None
                    if self.lev["queue"]:
                        best = max(range(len(self.lev["queue"])), key=lambda i: (self.led_events[self.lev["queue"][i]]["p"], -i))
                        a = self.lev["queue"].pop(best)
                        self._lev_start(a, self.lev["until"])
                    break
            self.lev["until"] += ev["steps"][self.lev["step"]]["ms"] / 1000.0
        return self.lev["active"]

    def _lev_start(self, idx, at=None):
        ev = self.led_events[idx]
        self.lev.update(active=idx, step=0, reps=ev["rep"], until=(at if at is not None else time.monotonic()) + ev["steps"][0]["ms"] / 1000.0)
        self.lev["played"] += 1

    # ------------------------------------------------------------------ settings (the new fields)
    def settings20_validate(self, msg):
        """returns (new values, error?) for the 2.0 fields of a settings request"""
        new = {}
        for k, v in msg.items():
            if k in SETTINGS20_BOOL:
                if not isinstance(v, bool):
                    return None, True
                new[k] = v
            elif k in SETTINGS20_RANGE:
                lo, hi = SETTINGS20_RANGE[k]
                if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                    return None, True
                new[k] = v
            elif k == "dial_curve":
                if not isinstance(v, list) or len(v) != 3 or any(isinstance(x, bool) or not isinstance(x, int) or not 0 <= x <= 3 for x in v):
                    return None, True
                new[k] = list(v)
            elif k == "scr_bright":
                if not isinstance(v, list) or len(v) != 32 or any(isinstance(x, bool) or not isinstance(x, int) or not 0 <= x <= 200 or 0 < x < 5 for x in v):
                    return None, True
                new[k] = list(v)
            elif k == "scr_saver":
                if not isinstance(v, list) or len(v) != 32 or any(isinstance(x, bool) or not isinstance(x, int) or not -1 <= x <= 3600 for x in v):
                    return None, True
                new[k] = list(v)
        return new, False

    # ------------------------------------------------------------------ commands
    def cmd20(self, cmd, msg, reply):
        """returns True when the command was handled here"""
        from core import base as b
        if cmd == "leader":
            if "seqs" in msg:
                seqs = msg["seqs"]
                if not isinstance(seqs, list) or len(seqs) > 24:
                    reply({"ok": False, "err": "seqs"})
                    return True
                for s in seqs:
                    k = s.get("k") if isinstance(s, dict) else None
                    if not isinstance(k, list) or not 1 <= len(k) <= 3 or any(isinstance(x, bool) or not isinstance(x, int) or not 1 <= x <= 5 for x in k):
                        reply({"ok": False, "err": "seqs"})
                        return True
                    a = s.get("a") or {}
                    if not b.spec_ok({"type": a.get("type"), "val": a.get("val")}):
                        reply({"ok": False, "err": "spec"})
                        return True
                if len(b.compact_json(seqs)) > 3800:
                    reply({"ok": False, "err": "too_long"})
                    return True
                self.leader_seqs = [{"k": list(s["k"]), "a": {"type": s["a"]["type"], "val": s["a"]["val"]}} for s in seqs]
            reply({"ok": True, "evt": "leader", "on": False, "n": len(self.leader_seqs), "seqs": json.loads(json.dumps(self.leader_seqs))})
        elif cmd == "switches":
            op = msg.get("op", "")
            if op == "reset":
                self.sw_press, self.sw_chatter = [0] * 5, [0] * 5
            elif op and op != "save":
                reply({"ok": False, "err": "op"})
                return True
            reply({"ok": True, "evt": "switches", "presses": list(self.sw_press), "chatter": list(self.sw_chatter)})
        elif cmd == "enccal":
            op = msg.get("op", "get")
            if op == "start":
                self.enc["cal"] = 1
            elif op == "cancel":
                self.enc["cal"] = 0
            elif op == "set":
                e = msg.get("edges", self.enc["edges"])
                if isinstance(e, bool) or not isinstance(e, int) or not 1 <= e <= 4:
                    reply({"ok": False, "err": "edges"})
                    return True
                self.enc["edges"] = e
                if "invert" in msg:
                    self.enc["invert"] = bool(msg["invert"])
                self.s20["enc_edges"], self.s20["enc_invert"] = self.enc["edges"], self.enc["invert"]
            elif op != "get":
                reply({"ok": False, "err": "op"})
                return True
            reply({"ok": True, "evt": "enccal_state", "cal": self.enc["cal"], "edges": self.enc["edges"], "invert": self.enc["invert"]})
        elif cmd == "snippets":
            op = msg.get("op", "list")
            if op == "set":
                items, at = msg.get("items"), msg.get("at", 0)
                if not isinstance(items, list) or not 1 <= len(items) <= 12 or not isinstance(at, int) or at < 0 or at + len(items) > 100:
                    reply({"ok": False, "err": "items"})
                    return True
                for it in items:
                    if not isinstance(it, dict) or not it.get("l") or not _ascii_text(it["l"], 14) or not it.get("t") or not _ascii_text(it["t"], 120, True):
                        reply({"ok": False, "err": "items"})
                        return True
                total = msg.get("total", max(len(self.snippets), at + len(items)))
                if not isinstance(total, int) or total < at + len(items) or total > 100:
                    reply({"ok": False, "err": "total"})
                    return True
                while len(self.snippets) < total:
                    self.snippets.append(None)
                for i, it in enumerate(items):
                    self.snippets[at + i] = {"l": it["l"], "t": it["t"]}
                del self.snippets[total:]
            elif op == "clear":
                self.snippets = []
            elif op != "list":
                reply({"ok": False, "err": "op"})
                return True
            frm, cnt = msg.get("from", 0), min(20, msg.get("n", 20))
            reply({"ok": True, "evt": "snippets", "n": len(self.snippets), "labels": [(s or {"l": ""})["l"] for s in self.snippets[frm:frm + cnt]]})
        elif cmd == "radial":
            if "slots" in msg:
                sl = msg["slots"]
                if not isinstance(sl, list) or len(sl) > 8:
                    reply({"ok": False, "err": "slots"})
                    return True
                new = [{} for _ in range(8)]
                for i, v in enumerate(sl):
                    if not v:
                        continue
                    if not isinstance(v, dict) or not v.get("l") or not _ascii_text(v["l"], 8):
                        reply({"ok": False, "err": "slots"})
                        return True
                    a = v.get("a") or {}
                    if not b.spec_ok({"type": a.get("type"), "val": a.get("val")}):
                        reply({"ok": False, "err": "spec"})
                        return True
                    new[i] = {"l": v["l"], "a": {"type": a["type"], "val": a["val"]}}
                self.radial = new
            while self.radial and not self.radial[-1] and len(self.radial) > 0 and False:
                self.radial.pop()
            reply({"ok": True, "evt": "radial", "slots": json.loads(json.dumps(self.radial))})
        elif cmd == "icon":
            op, tg = msg.get("op", "list"), msg.get("target", "key")
            if op != "list":
                if tg == "key":
                    lay, k = msg.get("layer", 0), msg.get("key", 0)
                    if not isinstance(lay, int) or not 0 <= lay < 3 or not isinstance(k, int) or not 1 <= k <= 5:
                        reply({"ok": False, "err": "key"})
                        return True
                    key = ("k", lay, k)
                elif tg == "radial":
                    s = msg.get("slot", -1)
                    if not isinstance(s, int) or not 0 <= s <= 7:
                        reply({"ok": False, "err": "slot"})
                        return True
                    key = ("r", s)
                else:
                    reply({"ok": False, "err": "target"})
                    return True
                if op == "set":
                    if not _hexn(msg.get("bits", ""), 144):
                        reply({"ok": False, "err": "bits"})
                        return True
                    (self.icons_key if tg == "key" else self.icons_radial)[key] = msg["bits"].lower()
                elif op == "clear":
                    (self.icons_key if tg == "key" else self.icons_radial).pop(key, None)
                else:
                    reply({"ok": False, "err": "op"})
                    return True
            reply({"ok": True, "evt": "icons", "keys": [[("k", l, k) in self.icons_key for k in range(1, 6)] for l in range(3)], "radial": [("r", i) in self.icons_radial for i in range(8)]})
        elif cmd == "totp":
            op = msg.get("op", "list")
            if op in ("set", "clear", "code") and not self._unlocked():
                reply({"ok": False, "err": "locked"})
                return True
            if op == "set":
                items, at = msg.get("items"), msg.get("at", 0)
                if not isinstance(items, list) or not items or not isinstance(at, int) or at < 0 or at + len(items) > 10:
                    reply({"ok": False, "err": "items"})
                    return True
                for it in items:
                    key = base32_decode(it.get("s", "")) if isinstance(it, dict) and isinstance(it.get("s", ""), str) else None
                    d, p = it.get("d", 6) if isinstance(it, dict) else 0, it.get("p", 30) if isinstance(it, dict) else 0
                    if (not isinstance(it, dict) or not it.get("n") or not _ascii_text(it["n"], 10) or key is None or len(key) < 10
                            or isinstance(d, bool) or not isinstance(d, int) or not 6 <= d <= 8 or isinstance(p, bool) or not isinstance(p, int) or not 15 <= p <= 120):
                        reply({"ok": False, "err": "items"})
                        return True
                for i, it in enumerate(items):
                    self.totp_accs[at + i] = {"n": it["n"], "s": it["s"], "d": it.get("d", 6), "p": it.get("p", 30)}
            elif op == "clear":
                self.totp_accs = [None] * 10
            elif op == "code":
                i = msg.get("i", 0)
                if not isinstance(i, int) or not 0 <= i <= 9 or not self.totp_accs[i]:
                    reply({"ok": False, "err": "i"})
                    return True
                a = self.totp_accs[i]
                t = msg.get("t", self._now())
                reply({"ok": True, "evt": "totp_code", "code": totp_code(base32_decode(a["s"]), t, a["p"], a["d"]), "rem": a["p"] - int(t) % a["p"]})
                return True
            elif op != "list":
                reply({"ok": False, "err": "op"})
                return True
            accs = [{"i": i, "n": a["n"], "d": a["d"], "p": a["p"]} for i, a in enumerate(self.totp_accs) if a]
            reply({"ok": True, "evt": "totp", "n": len(accs), "names": accs})
        elif cmd == "pin":
            op = msg.get("op", "state")
            p = self.pin

            def digits(x):
                return isinstance(x, str) and 4 <= len(x) <= 8 and x.isdigit() and x.isascii()
            if op == "set":
                if not digits(msg.get("pin", "")):
                    reply({"ok": False, "err": "pin"})
                    return True
                if p["pin"] and (p["locked"] or msg.get("old", "") != p["pin"]):
                    reply({"ok": False, "err": "old_pin"})
                    return True
                p["pin"] = msg["pin"]
            elif op == "clear":
                if not p["pin"]:
                    reply({"ok": False, "err": "no_pin"})
                    return True
                if p["locked"] or msg.get("pin", "") != p["pin"]:
                    reply({"ok": False, "err": "old_pin"})
                    return True
                p["pin"], p["locked"] = None, False
            elif op == "lock":
                if not p["pin"]:
                    reply({"ok": False, "err": "no_pin"})
                    return True
                p["locked"] = True
            elif op == "config":
                if p["pin"] and (p["locked"] or msg.get("pin", "") != p["pin"]):
                    reply({"ok": False, "err": "old_pin"})
                    return True
                if "all" in msg:
                    p["all"] = bool(msg["all"])
                if "idle" in msg:
                    v = msg["idle"]
                    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 3600:
                        reply({"ok": False, "err": "idle"})
                        return True
                    p["idle"] = v
            elif op != "state":
                reply({"ok": False, "err": "op"})
                return True
            reply({"ok": True, "evt": "pin", "set": p["pin"] is not None, "locked": p["locked"], "all": p["all"], "idle": p["idle"], "fails": p["fails"]})
        elif cmd == "cal":
            ms = msg.get("months")
            if not isinstance(ms, list) or len(ms) > 3:
                reply({"ok": False, "err": "months"})
                return True
            out = []
            for m in ms:
                y, mo, dd = m.get("y", 0), m.get("m", 0), m.get("days")
                if not isinstance(y, int) or not 2000 <= y <= 2100 or not isinstance(mo, int) or not 1 <= mo <= 12 or not isinstance(dd, list):
                    reply({"ok": False, "err": "months"})
                    return True
                dim = [31, 29 if (y % 4 == 0 and y % 100 != 0) or y % 400 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1]
                if any(isinstance(x, bool) or not isinstance(x, int) or not 1 <= x <= dim for x in dd):
                    reply({"ok": False, "err": "days"})
                    return True
                out.append({"y": y, "m": mo, "days": sorted(set(dd))})
            self.cal_months = out
            reply({"ok": True, "evt": "cal"})
        elif cmd == "worldclock":
            zs = msg.get("zones")
            if not isinstance(zs, list) or len(zs) > 4 or any(not isinstance(z, dict) or not z.get("n") or not _ascii_text(z["n"], 6) or isinstance(z.get("o", 0), bool)
                                                              or not isinstance(z.get("o", 0), int) or not -720 <= z.get("o", 0) <= 840 for z in zs):
                reply({"ok": False, "err": "zones"})
                return True
            self.zones = [{"n": z["n"], "o": z.get("o", 0)} for z in zs]
            reply({"ok": True, "evt": "worldclock"})
        elif cmd == "geo":
            la, lo = msg.get("lat"), msg.get("lon")
            if not isinstance(la, (int, float)) or not isinstance(lo, (int, float)) or isinstance(la, bool) or isinstance(lo, bool) or not -90 <= la <= 90 or not -180 <= lo <= 180:
                reply({"ok": False, "err": "geo"})
                return True
            self.geo = (round(la * 100) / 100.0, round(lo * 100) / 100.0)
            t = time.gmtime(self._now())
            st = sun_times(t.tm_year, t.tm_mon, t.tm_mday, self.geo[0], self.geo[1])
            r = {"ok": True, "evt": "geo", "sun": st is not None, "moon_age": moon_age(self._now())}
            if st:
                r["rise"], r["set"] = int(st[0]), int(st[1])
            reply(r)
        elif cmd == "sketch":
            op = msg.get("op", "get")
            if op == "set":
                if not _hexn(msg.get("bits", ""), 400):
                    reply({"ok": False, "err": "bits"})
                    return True
                self.sketch = bytearray(bytes.fromhex(msg["bits"]))
                if msg.get("save"):
                    self.sketch_saved = True
            elif op == "clear":
                self.sketch = bytearray(200)
            elif op != "get":
                reply({"ok": False, "err": "op"})
                return True
            reply({"ok": True, "evt": "sketch", "bits": bytes(self.sketch).hex(), "saved": self.sketch_saved})
        elif cmd == "app":
            op = msg.get("op", "state")
            if op != "state":
                reply({"ok": False, "err": "op"})
                return True
            r = {"ok": True, "evt": "app", "mode": self.mode, "name": APP_NAMES.get(self.mode, ""), "locked": bool(self.pin["pin"] and self.pin["locked"])}
            if self.mode == 21:
                r.update(n=len(self.snippets), sel=0)
            elif self.mode == 24:
                r.update(n=sum(1 for a in self.totp_accs if a), sel=0)
            elif self.mode == 25:
                r.update(count=sum(bin(x).count("1") for x in self.sketch))
            elif self.mode == 32:
                r.update(active=self.stream["active"], frames=self.stream["frames"], tiles=self.stream["tiles"], bytes=self.stream["bytes"])
            reply(r)
        elif cmd == "stream":
            op = msg.get("op", "stats")
            st = self.stream
            if op == "start":
                st.update(active=True, buf=bytearray(240 * 240 * 2), frames=0, tiles=0, bytes=0)
                self.mode = 32
            elif op == "stop":
                st.update(active=False, buf=None)
            elif op != "stats":
                reply({"ok": False, "err": "op"})
                return True
            reply({"ok": True, "evt": "stream", "active": st["active"], "frames": st["frames"], "tiles": st["tiles"], "bytes": st["bytes"], "ms": 0, "heap": 210_000})
        elif cmd == "tiles":
            st = self.stream
            if not st["active"] or self.mode != 32:
                reply({"ok": False, "err": "not_streaming"})
                return True
            t = msg.get("t")
            if not isinstance(t, list) or not 1 <= len(t) <= 12:
                reply({"ok": False, "err": "tiles"})
                return True
            dec = []
            for v in t:
                if not isinstance(v, list) or len(v) != 3 or not isinstance(v[0], int) or not isinstance(v[1], int) or not isinstance(v[2], str):
                    reply({"ok": False, "err": "tiles"})
                    return True
                try:
                    raw = base64.b64decode(v[2], validate=True)
                except ValueError:
                    reply({"ok": False, "err": "tile"})
                    return True
                px = self._tile_pixels(raw)
                if px is None or not 0 <= v[0] <= 14 or not 0 <= v[1] <= 14:
                    reply({"ok": False, "err": "tile"})
                    return True
                dec.append((v[0], v[1], px, len(raw)))
            for tx, ty, px, n in dec:
                for y in range(16):
                    o = ((ty * 16 + y) * 240 + tx * 16) * 2
                    st["buf"][o:o + 32] = px[y * 32:(y + 1) * 32]
                st["bytes"] += n
            st["tiles"] += len(dec)
            if msg.get("end"):
                st["frames"] += 1
            reply({"ok": True, "evt": "tiles", "n": len(dec)})
        elif cmd == "crumbs":
            if msg.get("op", "get") == "clear":
                self.crumbs_prev, self.crumbs = [], []
            tail = ", ".join(c["s"] for c in self.crumbs_prev[-6:])
            reply({"ok": True, "evt": "crumbs", "boots": self.boots20, "prev_boots": self.boots20 - 1 if self.crumbs_prev else 0, "prev": list(self.crumbs_prev), "now": list(self.crumbs), "tail": tail})
        elif cmd == "lifetime":
            op = msg.get("op", "get")
            if op == "reset":
                boots = self.life["boots"]
                self.life = {"dial_cw": 0, "dial_ccw": 0, "dial_click": 0, "minutes": 0, "boots": boots}
                self.sw_press, self.sw_chatter = [0] * 5, [0] * 5
            elif op not in ("get", "save"):
                reply({"ok": False, "err": "op"})
                return True
            reply(dict({"ok": True, "evt": "lifetime", "presses": list(self.sw_press), "chatter": list(self.sw_chatter), "up_s": self._up_ms() // 1000}, **self.life))
        elif cmd == "led_events":
            if "events" in msg:
                ev = msg["events"]
                if not isinstance(ev, list) or len(ev) > 8:
                    reply({"ok": False, "err": "events"})
                    return True
                out = []
                for v in ev:
                    ok = isinstance(v, dict) and isinstance(v.get("n"), str) and v["n"] and _ascii_text(v["n"], 10)
                    p, rep, steps = (v.get("p", 1), v.get("rep", 1), v.get("steps")) if isinstance(v, dict) else (0, 0, None)
                    ok = ok and isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 9 and isinstance(rep, int) and not isinstance(rep, bool) and 1 <= rep <= 20 and isinstance(steps, list) and 1 <= len(steps) <= 6
                    if ok:
                        for s in steps:
                            c, ms = (s.get("c", ""), s.get("ms", 0)) if isinstance(s, dict) else ("", 0)
                            if not isinstance(c, str) or not re.fullmatch(r"#?[0-9a-fA-F]{6}", c) or isinstance(ms, bool) or not isinstance(ms, int) or not 20 <= ms <= 5000:
                                ok = False
                    if not ok:
                        reply({"ok": False, "err": "events"})
                        return True
                    out.append({"n": v["n"], "p": p, "rep": rep, "steps": [{"c": s["c"].lstrip("#"), "ms": s["ms"]} for s in steps]})
                if len(json.dumps(out)) > 1800:
                    reply({"ok": False, "err": "too_long"})
                    return True
                self.led_events = out
                self.lev.update(active=None, queue=[])
            reply({"ok": True, "evt": "led_events", "names": [{"n": e["n"], "p": e["p"]} for e in self.led_events]})
        elif cmd == "led_event":
            if msg.get("cancel"):
                self.lev.update(active=None, queue=[])
            else:
                idx = next((i for i, e in enumerate(self.led_events) if e["n"] == msg.get("name")), -1)
                if idx < 0:
                    reply({"ok": False, "err": "event"})
                    return True
                self.lev_tick()
                act = self.lev["active"]
                if act is None:
                    self._lev_start(idx)
                elif self.led_events[idx]["p"] > self.led_events[act]["p"]:
                    self._lev_start(idx)
                    self.lev["queue"] = []
                elif idx not in self.lev["queue"] and len(self.lev["queue"]) < 4:
                    self.lev["queue"].append(idx)
            act = self.lev_tick()
            reply({"ok": True, "evt": "led_event", "active": self.led_events[act]["n"] if act is not None else "", "queue": [self.led_events[i]["n"] for i in self.lev["queue"]], "played": self.lev["played"]})
        elif cmd == "pet_event":
            k = msg.get("kind", "")
            now = time.monotonic()
            if k == "cheer":
                self.pet["cheer"] = now + 4
                self.pet["fun"] = min(100, self.pet["fun"] + 10)
            elif k == "sad":
                self.pet["sad"] = now + 5
                self.pet["fun"] = max(0, self.pet["fun"] - 10)
            elif k:
                reply({"ok": False, "err": "kind"})
                return True
            reply({"ok": True, "evt": "pet_event", "cheer": self.pet["cheer"] > now, "sad": self.pet["sad"] > now, "sweat": self.pet["cpu"] >= 85 and now - self.pet["cpu_at"] < 5,
                   "sleep": False, "fun": self.pet["fun"]})
        elif cmd == "clockwords":
            t = time.localtime(self._now())
            h, m = msg.get("h", t.tm_hour), msg.get("m", t.tm_min)
            if isinstance(h, bool) or isinstance(m, bool) or not isinstance(h, int) or not isinstance(m, int) or not 0 <= h <= 23 or not 0 <= m <= 59:
                reply({"ok": False, "err": "time"})
                return True
            reply({"ok": True, "evt": "clockwords", "text": word_clock(h, m)})
        elif cmd == "fold":
            reply({"ok": True, "evt": "fold", "text": fold_text(str(msg.get("text", "")))})
        elif cmd == "usbmode":
            want = self.usbx
            keys = ("midi", "gamepad", "drive")
            for i, k in enumerate(keys):
                if k in msg and not isinstance(msg[k], bool):
                    reply({"ok": False, "err": k})
                    return True
            mo, po = msg.get("midi_on"), msg.get("pad_on")
            for v in (mo, po):
                if v is not None and not isinstance(v, bool):
                    reply({"ok": False, "err": "bool"})
                    return True
            if (mo and not self.usbx_boot & 1) or (po and not self.usbx_boot & 2):
                reply({"ok": False, "err": "not_active"})
                return True
            if (mo and (po or (po is None and self.pad_on))) or (po and mo is None and self.midi_on):
                reply({"ok": False, "err": "exclusive"})
                return True
            rng = {"base": (0, 120), "channel": (1, 16), "cc": (0, 115), "velocity": (1, 127), "axis_step": (1, 64)}
            for k, (lo, hi) in rng.items():
                if k in msg and (isinstance(msg[k], bool) or not isinstance(msg[k], int) or not lo <= msg[k] <= hi):
                    reply({"ok": False, "err": k})
                    return True
            for i, k in enumerate(keys):
                if k in msg:
                    want = (want | (1 << i)) if msg[k] else (want & ~(1 << i))
            self.usbx = want
            if mo is not None:
                self.midi_on = mo
                if mo:
                    self.pad_on = False
            if po is not None:
                self.pad_on = po
                if po:
                    self.midi_on = False
            for k in rng:
                if k in msg:
                    self.usb_cfg[k] = msg[k]
            reply(dict({"ok": True, "evt": "usbmode", "midi": bool(want & 1), "gamepad": bool(want & 2), "drive": bool(want & 4), "active": self.usbx_boot, "reboot_needed": want != self.usbx_boot,
                        "midi_on": self.midi_on, "pad_on": self.pad_on, "axis": 0, "available": ["midi", "gamepad", "drive"]}, **self.usb_cfg))
        elif cmd == "usbdrive":
            op = msg.get("op", "state")
            if not self.usbx_boot & 4 and op != "state":
                reply({"ok": False, "err": "not_active"})
                return True
            if op == "image":
                off, n = msg.get("off", 0), msg.get("n", 1024)
                img = self.drive_image()
                if not isinstance(off, int) or not isinstance(n, int) or off < 0 or n < 1 or n > 1500 or off >= len(img):
                    reply({"ok": False, "err": "range"})
                    return True
                n = min(n, len(img) - off)
                reply({"ok": True, "evt": "usbdrive", "off": off, "n": n, "total": len(img), "d": base64.b64encode(img[off:off + n]).decode()})
                return True
            if op not in ("state", "refresh"):
                reply({"ok": False, "err": "op"})
                return True
            reply({"ok": True, "evt": "usbdrive", "active": bool(self.usbx_boot & 4), "size": 131072 if self.usbx_boot & 4 else 0, "readonly": True})
        else:
            return False
        return True

    def drive_image(self):
        from core import base as b
        keys = {"layers": [{"slots": {str(i): self._spec_for(l, i) for i in range(1, 8) if i in self.layers[l] or True}, "gestures": {}} for l in range(3)]}
        for (l, k, g), sp in self.gest.items():
            keys["layers"][l]["gestures"][f"{k}{ {'hold': 'h', 'double': 'd', 'triple': 't', 'hold2': 'j', 'hold3': 'k', 'shift': 's'}[g]}"] = sp
        for l in range(3):
            for k, sp in self.layers[l].items():
                if k > 7:
                    keys["layers"][l]["slots"][str(k)] = sp
        st = {"fw": self.fw_text(), "proto": PROTO20, "mode": self.mode, "brightness": self.bright, "usb_extra": self.usbx_boot}
        readme = ("DeskCompanion backup drive (read-only)\r\nSETTINGS.JSON - the main settings\r\nKEYS.JSON     - every key action of the three layers\r\n"
                  "LIFETIME.JSON - presses, dial turns, hours of use\r\nThe files are a snapshot taken when the pad started; eject and re-plug to refresh.\r\n")
        return fat12_image([("README  TXT", readme.encode()), ("SETTINGS" + "JSN", b.compact_json(st).encode()), ("KEYS    JSN", b.compact_json(keys).encode()),
                            ("LIFETIMEJSN", b.compact_json(dict(self.life, presses=self.sw_press)).encode())])

    @staticmethod
    def _tile_pixels(raw):
        """decode one stream tile (solid / raw / RLE) into 512 bytes of little-endian RGB565, or None"""
        if not raw:
            return None
        f = raw[0]
        if f == 0:
            return raw[1:3] * 256 if len(raw) == 3 else None
        if f == 1:
            return bytes(raw[1:]) if len(raw) == 513 else None
        if f == 2:
            out, i = bytearray(), 1
            while i + 2 < len(raw) and len(out) < 512:
                run = raw[i]
                if not run or len(out) + run * 2 > 512:
                    return None
                out += raw[i + 1:i + 3] * run
                i += 3
            return bytes(out) if len(out) == 512 and i == len(raw) else None
        return None
