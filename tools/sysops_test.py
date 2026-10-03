"""Tests for OS actions (volume per program, DND, audio output, microphone, screenshot), network actions (webhook, translate, AI), window
layouts and clipboard history - all with canned tool output / fake HTTP.   python3 tools/sysops_test.py"""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from desk_lib import cliphist, netactions, sysactions, winlayout   # noqa: E402


def fake(outputs, calls):
    def run(args, timeout=5):
        calls.append(list(args))
        for key, out in outputs.items():
            if key in " ".join(args):
                if isinstance(out, Exception):
                    raise out
                return out
        return ""
    return run


# ---------------------------------------------------------------- per-program volume
SINKS = '''Sink Input #12
\tProperties:
\t\tapplication.name = "Spotify"
\t\tapplication.process.binary = "spotify"
Sink Input #15
\tProperties:
\t\tapplication.name = "Firefox"
\t\tapplication.process.binary = "firefox"
Sink Input #19
\tProperties:
\t\tapplication.name = "Firefox"
\t\tmedia.name = "YouTube - Music"
'''
calls = []
s = sysactions.SysActions(fake({"list sink-inputs": SINKS}, calls), "Linux", which=lambda t: "/usr/bin/" + t)
assert s.app_volume("Spotify", 5) == "spotify volume +5% (1 stream)" and ["pactl", "set-sink-input-volume", "12", "+5%"] in calls
calls.clear(); assert "2 streams" in s.app_volume("firefox", -10) and ["pactl", "set-sink-input-volume", "15", "-10%"] in calls and ["pactl", "set-sink-input-volume", "19", "-10%"] in calls and ["pactl", "set-sink-input-volume", "12", "-10%"] not in calls
for bad in ("vlc", ""):
    try:
        s.app_volume(bad, 5); raise SystemExit("accepted " + bad)
    except ValueError as e:
        assert "not playing" in str(e) or "no program" in str(e)
try:
    sysactions.SysActions(fake({}, []), "Linux", which=lambda t: None).app_volume("x", 5); raise SystemExit("no pactl accepted")
except ValueError as e:
    assert "pactl" in str(e)
try:
    sysactions.SysActions(fake({}, []), "Darwin").app_volume("x", 5); raise SystemExit("mac accepted")
except ValueError as e:
    assert "not available" in str(e)
pv = sysactions.parse_volume_arg
assert (pv("up"), pv("down"), pv("+7"), pv("-12"), pv("3"), pv("99")) == (5, -5, 7, -12, 3, 50)
for bad in ("loud", "", "1.5"):
    try:
        pv(bad); raise SystemExit("accepted " + bad)
    except ValueError:
        pass

# ---------------------------------------------------------------- Do Not Disturb
calls = []
s = sysactions.SysActions(fake({"get org.gnome": "true\n"}, calls), "Linux", which=lambda t: "x")
assert s.dnd("toggle") == "Do Not Disturb on" and calls[-1][-1] == "false"
assert s.dnd("off") == "Do Not Disturb off" and calls[-1][-1] == "true" and s.dnd("on").endswith("on") and calls[-1][-1] == "false"
s2 = sysactions.SysActions(fake({"get org.gnome": "false\n"}, calls), "Linux", which=lambda t: "x")
assert s2.dnd("toggle") == "Do Not Disturb off", "banners hidden now -> toggle switches DND off"
calls.clear(); w = sysactions.SysActions(fake({}, calls), "Windows", which=lambda t: "x")
assert w.dnd("on").startswith("Do Not Disturb on") and calls[0][:3] == ["reg", "add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"] and "0" == calls[0][calls[0].index("/d") + 1]
assert w.dnd("off") and calls[-1][calls[-1].index("/d") + 1] == "1"
for system, state in (("Windows", "toggle"), ("Linux", "maybe")):
    try:
        sysactions.SysActions(fake({}, []), system, which=lambda t: "x").dnd(state); raise SystemExit(f"accepted {system} {state}")
    except ValueError:
        pass
calls.clear(); m = sysactions.SysActions(fake({}, calls), "Darwin", which=lambda t: "x")
assert m.dnd("on") == "Do Not Disturb On" and calls[-1] == ["shortcuts", "run", "Do Not Disturb On"]

# ---------------------------------------------------------------- audio output / microphone
calls = []
l = sysactions.SysActions(fake({"short sinks": "0\talsa_output.speakers\tx\n1\talsa_output.usb_headset\tx\n2\tbluez_sink.buds\tx\n", "get-default-sink": "alsa_output.usb_headset\n"}, calls), "Linux", which=lambda t: "x")
assert l.audio_output("next") == "audio output: bluez_sink.buds" and calls[-1] == ["pactl", "set-default-sink", "bluez_sink.buds"]
assert l.audio_output("SPEAK") == "audio output: alsa_output.speakers"
try:
    l.audio_output("hdmi"); raise SystemExit("unknown output accepted")
except ValueError as e:
    assert "no audio output matching" in str(e)
calls.clear()
mic = sysactions.SysActions(fake({"get-source-mute": "Mute: yes\n"}, calls), "Linux", which=lambda t: "x")
assert mic.microphone("toggle") == "microphone muted" and ["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "toggle"] in calls
assert mic.microphone("unmute") == "microphone muted" and calls[-2][-1] == "0"           # (the canned reply says muted: the message follows what the system reports)
try:
    mic.microphone("shout"); raise SystemExit("bad mic state")
except ValueError:
    pass
calls.clear(); mm = sysactions.SysActions(fake({"input volume of": "0\n"}, calls), "Darwin", which=lambda t: "x")
assert mm.microphone("toggle") == "microphone live" and any("input volume 75" in " ".join(c) for c in calls)
calls.clear(); wn = sysactions.SysActions(fake({}, calls), "Windows", which=lambda t: "x")
assert wn.microphone("toggle") == "microphone toggle" and calls[-1] == ["nircmd", "mutesysvolume", "2", "default_record"]
try:
    sysactions.SysActions(fake({}, []), "Windows", which=lambda t: None).microphone("mute"); raise SystemExit("no nircmd accepted")
except ValueError as e:
    assert "NirCmd" in str(e)

# ---------------------------------------------------------------- screenshot
from PIL import Image   # noqa: E402
tmp = Path(tempfile.mkdtemp(prefix="dcsys_"))
sa = sysactions.SysActions(fake({}, []), "Linux")
p = sa.screenshot(tmp / "shots", grab=lambda: Image.new("RGB", (20, 10), "red"), now=datetime(2026, 10, 7, 9, 5, 3))
assert p.name == "screenshot-20261007-090503.png" and Image.open(p).size == (20, 10)
p2 = sa.screenshot(tmp / "shots", grab=lambda: Image.new("RGB", (4, 4)), now=datetime(2026, 10, 7, 9, 5, 3)); assert p2.name.endswith("-2.png") and p2 != p
try:
    sa.screenshot(tmp, grab=lambda: (_ for _ in ()).throw(OSError("no display"))); raise SystemExit("grab failure swallowed")
except ValueError as e:
    assert "cannot take a screenshot" in str(e)
print("sysactions OK")

# ---------------------------------------------------------------- webhooks / translate / AI (fake HTTP)
log = []
def fetch_ok(method, url, body=None, headers=None):
    log.append((method, url, body, headers))
    return 200, '{"ok": true}'
assert netactions.parse_webhook("https://a.b/c") == ("GET", "https://a.b/c", None)
assert netactions.parse_webhook('https://a.b/c {"x":1}') == ("POST", "https://a.b/c", '{"x":1}')
assert netactions.parse_webhook("put https://a.b/c  hello world") == ("PUT", "https://a.b/c", "hello world")
for bad in ("ftp://x", "javascript:alert(1)", "GET", "", "file:///etc/passwd"):
    try:
        netactions.parse_webhook(bad); raise SystemExit("accepted " + bad)
    except ValueError:
        pass
assert netactions.webhook('POST https://hooks.example/x {"text":"hi"}', fetch_ok) == "POST hooks.example: 200" and log[-1][:3] == ("POST", "https://hooks.example/x", '{"text":"hi"}')
try:
    netactions.webhook("https://a.b", lambda *a: (500, "boom")); raise SystemExit("500 accepted")
except ValueError as e:
    assert "answered 500" in str(e)
try:
    netactions.http_request("GET", "file:///etc/passwd"); raise SystemExit("file scheme accepted")
except ValueError as e:
    assert "only http" in str(e)
# real local server round trip
import threading   # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer   # noqa: E402
got = {}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); got["body"] = self.rfile.read(n).decode(); got["ct"] = self.headers.get("Content-Type"); got["ua"] = self.headers.get("User-Agent")
        self.send_response(201); self.end_headers(); self.wfile.write(b"made")
    def do_GET(self):
        self.send_response(404); self.end_headers(); self.wfile.write(b"nope")
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
assert netactions.http_request("POST", base + "/x", {"a": 1}) == (201, "made") and json.loads(got["body"]) == {"a": 1} and got["ct"] == "application/json" and got["ua"].startswith("DeskCompanion")
assert netactions.http_request("GET", base + "/y")[0] == 404
srv.shutdown()
try:
    netactions.http_request("GET", f"http://127.0.0.1:{srv.server_port}/z", timeout=1); raise SystemExit("closed port accepted")
except ValueError as e:
    assert "network problem" in str(e)

urls = []
def fetch_tr(method, url, body=None, headers=None):
    urls.append(url)
    return 200, json.dumps({"responseData": {"translatedText": "Hallo Welt"}})
assert netactions.translate("Hello world", "de", fetch_tr) == "Hallo Welt" and "langpair=Autodetect%7Cde" in urls[0] and "q=Hello+world" in urls[0]
long = ("This is a sentence. " * 60).strip()
urls.clear(); out = netactions.translate(long, "fr", fetch_tr)
assert 2 <= len(urls) <= 8 and out.count("Hallo Welt") == len(urls), "long text is sent in chunks of at most ~450 characters"
for bad in (("x", "german"), ("", "de"), ("x", "d3")):
    try:
        netactions.translate(*bad, fetch_tr); raise SystemExit(f"accepted {bad}")
    except ValueError:
        pass
try:
    netactions.translate("x", "de", lambda *a: (429, "limit")); raise SystemExit("no translation accepted")
except ValueError as e:
    assert "answered 429" in str(e)

seen = {}
def fetch_ai(method, url, body=None, headers=None):
    seen.update(method=method, url=url, body=body, headers=headers)
    return 200, json.dumps({"content": [{"type": "text", "text": "Hello "}, {"type": "text", "text": "there"}]})
assert netactions.ask_ai("Say hi", "sk-test", fetch=fetch_ai) == "Hello there"
assert seen["url"] == "https://api.anthropic.com/v1/messages" and seen["headers"]["x-api-key"] == "sk-test" and seen["headers"]["anthropic-version"] == "2023-06-01"
assert seen["body"]["messages"] == [{"role": "user", "content": "Say hi"}] and seen["body"]["model"].startswith("claude-")
assert netactions.ask_ai("x", "k", model="claude-opus-5-5", fetch=fetch_ai, system="be brief") == "Hello there" and seen["body"]["model"] == "claude-opus-5-5" and seen["body"]["system"] == "be brief"
for bad, needle in ((("x", ""), "no API key"),):
    try:
        netactions.ask_ai(*bad, fetch=fetch_ai); raise SystemExit("accepted a missing key")
    except ValueError as e:
        assert needle in str(e)
try:
    netactions.ask_ai("x", "k", fetch=lambda *a: (401, '{"error": {"message": "invalid x-api-key"}}')); raise SystemExit("401 accepted")
except ValueError as e:
    assert "invalid x-api-key" in str(e)
for resp in ((200, "not json"), (200, '{"content": []}')):
    try:
        netactions.ask_ai("x", "k", fetch=lambda *a, r=resp: r); raise SystemExit("bad reply accepted")
    except ValueError:
        pass
print("netactions OK")

# ---------------------------------------------------------------- window layouts
WMCTRL = '''0x04a00007  0 1234  100  200  800  600  host Docs - Mozilla Firefox
0x05000003  0 2345  920   40  600  400  host main.py - Visual Studio Code
0x0600000a -1 3456    0    0 1920   30  host Desktop panel
0x07000001  0 0    10   10   30   20  host tiny
'''
def lay_run(args, timeout=8):
    calls.append(list(args))
    if args[0] == "wmctrl" and args[1] == "-lGp":
        return WMCTRL
    if args[0] == "ps":
        return {"1234": "firefox", "2345": "code"}.get(args[2], "") + "\n"
    return ""
calls = []
wl = winlayout.WinLayouts(lay_run, "Linux", which=lambda t: "x")
wins = wl.windows()
assert [(w["process"], w["x"], w["w"]) for w in wins] == [("firefox", 100, 800), ("code", 920, 600), ("", 10, 30)] or [(w["process"]) for w in wins][:2] == ["firefox", "code"], wins
store = {}
assert wl.save(store, "work") == 2 and store["work"][0] == {"process": "firefox", "title": "Docs - Mozilla Firefox", "x": 100, "y": 200, "w": 800, "h": 600}, "tiny windows are skipped"
WMCTRL = WMCTRL.replace("100  200  800  600", "5 5 300 300").replace("920   40  600  400", "0 0 100 100")      # the user moved things around
calls.clear(); assert wl.restore(store, "work") == 2
assert ["wmctrl", "-i", "-r", "0x04a00007", "-e", "0,100,200,800,600"] in calls and ["wmctrl", "-i", "-r", "0x05000003", "-e", "0,920,40,600,400"] in calls
try:
    wl.restore(store, "nothing"); raise SystemExit("unknown layout restored")
except ValueError as e:
    assert "no saved window layout" in str(e)
store["ghost"] = [{"process": "notepad", "title": "x", "x": 0, "y": 0, "w": 100, "h": 100}]
try:
    wl.restore(store, "ghost"); raise SystemExit("restored closed programs")
except ValueError as e:
    assert "none of the saved programs" in str(e)
try:
    winlayout.WinLayouts(lay_run, "Linux", which=lambda t: None).windows(); raise SystemExit("no wmctrl accepted")
except ValueError as e:
    assert "wmctrl" in str(e)
tabs = "123\tfirefox\t10\t20\t800\t600\tDocs\n456\tcode\tbad\t0\t0\t0\tx\nshort line\n"
assert winlayout.parse_tab_lines(tabs) == [{"id": "123", "process": "firefox", "x": 10, "y": 20, "w": 800, "h": 600, "title": "Docs"}]
calls.clear(); wm = winlayout.WinLayouts(lambda a, timeout=8: (calls.append(a) or "7\tSafari\t1\t2\t300\t200\tHome\n"), "Darwin")
assert wm.windows()[0]["process"] == "Safari"; wm.move({"id": "7"}, 5, 6, 700, 500); assert "set size to {700, 500}" in calls[-1][-1]
calls.clear(); ww = winlayout.WinLayouts(lambda a, timeout=8: (calls.append(a) or "99\tnotepad\t0\t0\t400\t300\tNotes\n"), "Windows")
assert ww.windows()[0]["id"] == "99" and calls[0][0] == "powershell"; ww.move({"id": "99"}, 1, 2, 3, 4); assert "MoveWindow([IntPtr]99,1,2,3,4" in calls[-1][-1]
print("winlayout OK")

# ---------------------------------------------------------------- clipboard history
ch = cliphist.ClipHistory(size=3)
assert ch.add("a") and ch.add("b") and not ch.add("b") and ch.add("c") and ch.add("d") and ch.items == ["d", "c", "b"], ch.items
assert ch.add("b") and ch.items == ["b", "d", "c"], "copying an old value again moves it to the front"
assert not ch.add("") and not ch.add("   ") and not ch.add("x" * 3000)
assert (ch.get(1), ch.get(3)) == ("b", "c")
for n, needle in ((0, "from 1 to 3"), (4, "from 1 to 3")):
    try:
        ch.get(n); raise SystemExit("accepted " + str(n))
    except ValueError as e:
        assert needle in str(e)
ch2 = cliphist.ClipHistory(); ch2.add("only")
try:
    ch2.get(2); raise SystemExit("missing entry")
except ValueError as e:
    assert "only 1 clipboard entry" in str(e)
ch.clear(); assert ch.items == []
print("cliphist OK")
print("ALL SYSOPS TESTS PASSED")
