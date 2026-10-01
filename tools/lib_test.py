"""Unit tests for desk_lib (no GUI, no hardware):  python3 tools/lib_test.py"""
import datetime as dt
import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from desk_lib import activewin, backup, espota, feeds, hostactions, recorder   # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="dclib_"))

# ------------------------------------------------------------------ active window / profile rules
def fake_linux(args):
    if args[:3] == ["xdotool", "getactivewindow", "getwindowname"]:
        return "main.py - ClaudeTest - Visual Studio Code\n"
    if args[:3] == ["xdotool", "getactivewindow", "getwindowpid"]:
        return str(os.getpid()) + "\n"
    return None
aw = activewin.ActiveWindow(runner=fake_linux, system="Linux")
proc, title = aw.get()
assert "Visual Studio Code" in title and proc, (proc, title)
aw2 = activewin.ActiveWindow(runner=lambda a: None, system="Linux")
assert aw2.get() == ("", "")
def fake_xprop(a):
    if a[0] != "xprop":
        return None                                                  # no xdotool installed
    if a[1] == "-root":
        return "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x4a00007\n"
    return 'WM_CLASS(STRING) = "firefox", "Firefox"\n_NET_WM_NAME(UTF8_STRING) = "Docs - Mozilla Firefox"\n'
aw3 = activewin.ActiveWindow(runner=fake_xprop, system="Linux")
assert aw3.get() == ("firefox", "Docs - Mozilla Firefox"), aw3.get()
awm = activewin.ActiveWindow(runner=lambda a: "Code|main.py\n", system="Darwin")
assert awm.get() == ("code", "main.py")
rules = [{"match": "zoom", "kind": "process", "layer": 1}, {"match": "figma", "kind": "title", "layer": 2},
         {"match": "code", "layer": 2, "enabled": False}, {"match": "", "layer": 1}]
assert activewin.pick_layer(rules, "zoom.exe", "Meeting", default=0) == 1
assert activewin.pick_layer(rules, "chrome", "Figma - Design", default=0) == 2
assert activewin.pick_layer(rules, "code", "main.py", default=0) == 0          # disabled rule + empty rule never match
assert activewin.pick_layer(rules, "x", "y") is None
print("active window OK")

# ------------------------------------------------------------------ host actions: whitelist + safety
def resolve(cat, action):
    return {"Net": {"Docs": ("host", {"op": "url", "arg": "https://example.com"}),
                    "Both": ("macro", [{"combo": ["CTRL", "c"]}, {"host": {"op": "shell", "arg": "echo hi"}}])}}[cat][action]
maps = [{"1": {"cat": "Net", "action": "Docs"}}, {"2": {"cat": "Net", "action": "Both"}}]
customs = {"Mine": {"type": "host", "val": {"op": "app", "arg": "calc"}}, "Clip": {"type": "host", "val": {"op": "clipboard"}}}
allowed = hostactions.collect_allowed(maps, customs, resolve)
assert allowed == {("url", "https://example.com"), ("shell", "echo hi"), ("app", "calc"), ("clipboard", "")}, allowed
log = {"opened": [], "popen": [], "typed": 0, "notes": []}
shell_ok = {"v": False}
ha = hostactions.HostActions(lambda: allowed, lambda: shell_ok["v"], type_clipboard=lambda: log.__setitem__("typed", log["typed"] + 1),
                             notify=lambda t, m: log["notes"].append(m), opener=log["opened"].append, runner=lambda *a, **k: log["popen"].append((a, k)),
                             system="Linux")
assert ha.run("url", "https://example.com") == (True, "opened https://example.com") and log["opened"] == ["https://example.com"]
ok, msg = ha.run("url", "https://evil.example"); assert not ok and "not part of your key configuration" in msg and len(log["opened"]) == 1
allowed.add(("url", "file:///etc/passwd")); allowed.add(("url", "javascript:alert(1)"))
assert not ha.run("url", "file:///etc/passwd")[0] and not ha.run("url", "javascript:alert(1)")[0] and len(log["opened"]) == 1, "only http(s)/mailto links"
ok, msg = ha.run("shell", "echo hi"); assert not ok and "switched off" in msg and not log["popen"]
shell_ok["v"] = True
assert ha.run("shell", "echo hi")[0] and log["popen"][-1][1].get("shell") is True
assert ha.run("app", "calc")[0] and log["popen"][-1][0][0] == ["calc"]
assert ha.run("clipboard", "")[0] and log["typed"] == 1
allowed.add(("notify", "done")); assert ha.run("notify", "done")[0] and log["notes"] == ["done"]
assert not ha.run("rm", "-rf")[0]
print("host actions OK")

# ------------------------------------------------------------------ feeds
assert feeds.ascii_fold("Café Über 日本  x") == "Cafe Uber x"
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/geo/search"):
            body = {"results": [{"name": "Zürich", "latitude": 47.37, "longitude": 8.54}]} if "Zurich" in self.path else {}
        elif self.path.startswith("/meteo/forecast"):
            body = {"current": {"temperature_2m": 21.4, "weather_code": 2, "wind_speed_10m": 8.6}}
        else:
            self.send_response(404); self.end_headers(); return
        d = json.dumps(body).encode(); self.send_response(200); self.end_headers(); self.wfile.write(d)
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
feeds.GEOCODE, feeds.OPEN_METEO = f"http://127.0.0.1:{srv.server_port}/geo", f"http://127.0.0.1:{srv.server_port}/meteo"
lat, lon, label = feeds.geocode("Zurich"); assert (lat, lon, label) == (47.37, 8.54, "Zurich"), (lat, lon, label)
try: feeds.geocode("Nowhereville"); raise SystemExit("unknown place accepted")
except ValueError as e: assert "Nowhereville" in str(e)
w = feeds.weather_fetch(lat, lon); c = feeds.weather_card(label, w)
assert c == {"k": "w", "label": "WEATHER  Zurich", "t": "21 C", "a": "Partly cloudy", "b": "wind 9 km/h"}, c
assert feeds.weather_card(label, w, fahrenheit=True)["t"] == "71 F"
np = feeds.now_playing(runner=lambda a: "Playing|Midnight City|M83|Hurry Up\n", system="Linux")
assert np == {"playing": True, "title": "Midnight City", "artist": "M83", "album": "Hurry Up"}
assert feeds.now_playing(runner=lambda a: None, system="Linux") is None
assert feeds.music_card(np)["label"] == "NOW PLAYING" and feeds.music_card(dict(np, playing=False))["label"] == "PAUSED"
assert feeds.now_playing(runner=lambda a: "paused|Song|Artist|Alb\n" if a[0] == "osascript" else None, system="Darwin")["playing"] is False
ICS = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
DTSTART:20300101T143000Z\r
SUMMARY:Review with a\r
  folded title\\, ok\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:20300102\r
SUMMARY:Holiday\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART:20200101T100000Z\r
SUMMARY:Past\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART:garbage\r
SUMMARY:Broken\r
END:VEVENT\r
END:VCALENDAR\r
"""
utc = dt.timezone.utc
evs = feeds.parse_ics(ICS, local_tz=utc)
assert len(evs) == 3, evs
now = dt.datetime(2030, 1, 1, 14, 5, tzinfo=utc)
ev = feeds.next_event(evs, now); card = feeds.event_card(ev, now)
assert card == {"k": "e", "label": "NEXT EVENT", "t": "14:30", "a": "Review with a folded title, ok", "b": "in 25 min"}, card
ev2 = feeds.next_event(evs, dt.datetime(2030, 1, 1, 15, 0, tzinfo=utc)); assert ev2[2] == "Holiday" and feeds.event_card(ev2, dt.datetime(2030, 1, 1, 15, 0, tzinfo=utc))["t"] == "all day"
assert feeds.next_event(evs, dt.datetime(2031, 1, 1, tzinfo=utc)) is None
p = tmp / "cal.ics"; p.write_text(ICS); assert "BEGIN:VCALENDAR" in feeds.load_calendar(str(p))
# badge server: token protected, bounded, 127.0.0.1 only
bs = feeds.BadgeServer(token="s3cret")
def hit(q, method="GET"):
    try:
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{bs.port}/badge?{q}", method=method), timeout=3) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
assert hit("name=mail&n=3&token=s3cret") == 200 and bs.get() == [{"name": "mail", "n": 3}]
assert hit("name=mail&n=3&token=wrong") == 403 and hit("name=x&n=1") == 403
assert hit("name=chat&n=abc&token=s3cret") == 400 and hit("n=1&token=s3cret") == 400
assert hit("name=chat&n=5000&token=s3cret", "POST") == 200 and {"name": "chat", "n": 999} in bs.get()
assert hit("name=mail&n=0&token=s3cret") == 200 and all(b["name"] != "mail" for b in bs.get())
for i in range(8): hit(f"name=b{i}&n=1&token=s3cret")
assert len(bs.get()) == 4, "the pad shows at most 4 badges"
assert bs.httpd.server_address[0] == "127.0.0.1"
bs.close()
print("feeds OK")

# ------------------------------------------------------------------ OTA client against a fake device
class FakeDevice(threading.Thread):
    def __init__(self, password=""):
        super().__init__(daemon=True)
        self.password, self.got, self.error = password, None, None
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.udp.bind(("127.0.0.1", 0)); self.udp.settimeout(10)
        self.port = self.udp.getsockname()[1]

    def run(self):
        try:
            data, addr = self.udp.recvfrom(256)
            cmd, lport, size, md5 = data.decode().split()
            assert cmd == "0"
            if self.password:
                nonce = "abc123nonce"
                self.udp.sendto(f"AUTH {nonce}".encode(), addr)
                data, addr = self.udp.recvfrom(256)
                c, cnonce, result = data.decode().split()
                assert c == "200"
                want = hashlib.md5(f"{hashlib.md5(self.password.encode()).hexdigest()}:{nonce}:{cnonce}".encode()).hexdigest()
                if result != want:
                    self.udp.sendto(b"Authentication Failed", addr)
                    return
            self.udp.sendto(b"OK", addr)
            s = socket.create_connection((addr[0], int(lport)), timeout=10)
            buf = b""
            while len(buf) < int(size):
                part = s.recv(4096)
                if not part: break
                buf += part
                s.sendall(str(len(part)).encode())
            self.got = buf
            ok = hashlib.md5(buf).hexdigest() == md5
            s.sendall(b"OK" if ok else b"ERR")
            s.close()
        except Exception as e:                           # noqa: BLE001
            self.error = e

image = tmp / "app.bin"; image.write_bytes(bytes(range(256)) * 40 + b"tail")
for pw_dev, pw_cli, should in (("", "", True), ("hunter2", "hunter2", True), ("hunter2", "wrong", False), ("hunter2", "", False)):
    dev = FakeDevice(pw_dev); dev.start(); prog = []
    try:
        espota.ota_upload("127.0.0.1", str(image), pw_cli, port=dev.port, progress=prog.append, timeout=3)
        done = True
    except RuntimeError as e:
        done = False; msg = str(e)
    dev.join(5)
    assert done == should, (pw_dev, pw_cli, done, dev.error)
    if should:
        assert dev.got == image.read_bytes() and prog and abs(prog[-1] - 1.0) < 1e-9, "image not transferred intact"
    else:
        assert "password" in msg or "refused" in msg, msg
try: espota.ota_upload("127.0.0.1", str(image), "", port=9, timeout=0.3); raise SystemExit("no device accepted")
except RuntimeError as e: assert "did not answer" in str(e)
merged = bytearray(b"\xff" * 0x10000) + bytearray(b"\xe9" + bytes(100))
(tmp / "m.bin").write_bytes(bytes(merged))
assert espota.app_image_from_merged(tmp / "m.bin")[0] == 0xE9
try: espota.app_image_from_merged(image); raise SystemExit("non-image accepted")
except ValueError: pass
print("OTA client OK")

# ------------------------------------------------------------------ backup / restore
g = tmp / "a cat.gif"; g.write_bytes(b"GIF89a" + b"\x00" * 20)
blob = backup.make_backup({"map": {"1": {"cat": "Editing", "action": "Copy"}}}, {"layers": [[{"type": "none"}] * 7] * 3, "bright": 150}, [g], "9.9")
man, cfg, pad, gifs = backup.read_backup(blob)
assert man["gifs"] == 1 and cfg["map"]["1"]["action"] == "Copy" and pad["bright"] == 150 and gifs == {"a cat.gif": g.read_bytes()}
import io, zipfile
def evil(names):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("manifest.json", json.dumps({"format": 1})); z.writestr("config.json", "{}")
        for n in names: z.writestr(n, b"GIF89a-evil")
    return b.getvalue()
_, _, _, gifs = backup.read_backup(evil(["gifs/../../etc/x.gif", "gifs/ok.gif", "../x.gif", "gifs/sub/dir.gif", "gifs/notgif.txt"]))
assert list(gifs) == ["ok.gif"], gifs
for bad in (b"not a zip", evil([])[:30]):
    try: backup.read_backup(bad); raise SystemExit("accepted garbage")
    except Exception as e: assert not isinstance(e, SystemExit)
b = io.BytesIO()
with zipfile.ZipFile(b, "w") as z: z.writestr("other.txt", "x")
try: backup.read_backup(b.getvalue()); raise SystemExit("foreign zip accepted")
except ValueError as e: assert "not a Desk Companion backup" in str(e)
print("backup OK")
# ------------------------------------------------------------------ macro recorder
def rec(events, **kw):
    r = recorder.MacroRecorder(**kw)
    for kind, name, t in events:
        (r.key_down if kind == "d" else r.key_up)(name, t)
    return r, r.finish()
_, st = rec([("d", "h", 0.0), ("u", "h", 0.05), ("d", "i", 0.1), ("u", "i", 0.15), ("d", "space", 0.2), ("d", "a", 0.3)])
assert st == [{"text": "hi a"}], st
_, st = rec([("d", "ctrl_l", 0), ("d", "c", 0.05), ("u", "c", 0.1), ("u", "ctrl_l", 0.12), ("d", "ctrl_l", 0.5), ("d", "shift", 0.52), ("d", "T", 0.55)])
assert st == [{"combo": ["CTRL", "c"]}, {"delay": 500}, {"combo": ["CTRL", "SHIFT", "t"]}], st
_, st = rec([("d", "shift", 0), ("d", "H", 0.05), ("u", "H", 0.1), ("u", "shift", 0.12), ("d", "i", 0.2), ("d", "enter", 0.3), ("d", "f5", 0.4), ("d", "gui", 0.45), ("d", "r", 0.5)])
assert st == [{"text": "Hi"}, {"combo": ["ENTER"]}, {"combo": ["F5"]}, {"combo": ["GUI", "r"]}], st
_, st = rec([("d", "a", 0), ("d", "b", 3.0), ("d", "c", 20.0)])                                  # long pauses are capped
assert st == [{"text": "a"}, {"delay": 3000}, {"text": "b"}, {"delay": 5000}, {"text": "c"}], st
_, st = rec([("d", "x", 0), ("d", "volume_up", 0.1), ("d", "\u00e9", 0.2), ("d", "y", 0.3)])        # unsupported keys are skipped
assert st == [{"text": "xy"}], st
r, st = rec([("d", "a", 0)] + [e for i in range(100) for e in (("d", "ctrl", 1 + i), ("d", "z", 1.1 + i), ("u", "z", 1.2 + i), ("u", "ctrl", 1.3 + i))])
assert 60 <= len(st) <= 64 and r.truncated, (len(st), r.truncated)
_, st = rec([("d", "a", 0), ("d", "tab", 9.0)], gap_ms=350)
assert st[-1] == {"combo": ["TAB"]}
r = recorder.MacroRecorder(); r.key_down("a", 0); r.key_down("b", 5)
assert r.finish() == [{"text": "a"}, {"delay": 5000}, {"text": "b"}]
r = recorder.MacroRecorder(); r.key_down("a", 0); r.key_down("enter", 9)
r.steps.append({"delay": 100}); assert r.finish()[-1] == {"combo": ["ENTER"]}, "a trailing pause is dropped"
print("recorder OK")
print("ALL LIB TESTS PASSED")
