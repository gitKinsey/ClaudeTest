"""Tests for the extra data sources: quotes, birthdays, ping / http checks, lyrics, cover art, QR codes, sound-reactive LED, mood, LED alerts - and their app glue.
xvfb-run -a python3 tools/data_test.py"""
import datetime as dt
import io
import math
import os
import sys
import tempfile
import time
from pathlib import Path

tmp = tempfile.mkdtemp(prefix="dcdata_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from PIL import Image   # noqa: E402

from desk_lib import audio, extras, ledfx, lyrics, padimage   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


def raises(fn, text=""):
    try:
        fn()
    except ValueError as e:
        return text in str(e)
    return False


# ---------------------------------------------------------------- quote of the day
d1, d2 = dt.date(2026, 10, 3), dt.date(2026, 10, 4)
c1, c1b, c2 = extras.quote_card("", "", d1), extras.quote_card("", "", d1), extras.quote_card("", "", d2)
check("quote is stable within a day and changes next day", c1 == c1b and c1 != c2)
check("quote fits the card", all(len(c1[k]) <= 40 for k in "tab") and c1["label"] == "QUOTE", c1)
c = extras.quote_card("Wisdom", "/x/quotes.txt", d1, read=lambda p: "First one | Me\n\nSecond | You\nno author\n")
check("quote from a file", c["label"] == "WISDOM" and ("Me" in c["b"] or "You" in c["b"] or "author" in c["t"]), c)
check("empty quote file", raises(lambda: extras.quote_card("", "/x", d1, read=lambda p: "\n\n"), "empty"))
check("missing quote file", raises(lambda: extras.quote_card("", "/definitely/not/here.txt", d1), "cannot read"))

# ---------------------------------------------------------------- birthdays
today = dt.date(2026, 3, 10)
c = extras.birthday_card("", "Anna 03-14, Max 1990-03-12", today)
check("nearest birthday first", c["t"] == "Max" and c["a"] == "in 2 days" and "turns 36" in c["b"], c)
c = extras.birthday_card("", "Anna 03-10", today)
check("today", c["a"] == "TODAY!" and c["t"] == "Anna", c)
c = extras.birthday_card("", "Anna 03-09", today)
check("next year", c["a"] == "in 364 days", c)
c = extras.birthday_card("", "Leap 2000-02-29", dt.date(2026, 2, 27))
check("29 Feb in a non-leap year", c["a"] == "in 1 days" or c["a"] == "tomorrow", c)
check("tomorrow wording", extras.birthday_card("", "A 03-11", today)["a"] == "tomorrow")
check("parse errors", raises(lambda: extras.parse_dates("nonsense"), "should look like") and raises(lambda: extras.parse_dates("A 13-45"), "real date") and raises(lambda: extras.parse_dates(""), "1 to 12"))
v = extras.validate({"type": "birthday", "label": "bd", "arg": "Anna  03-14 ,Max 1990-03-12"})
check("validate normalises", v["arg"] == "Anna 03-14, Max 1990-03-12", v)

# ---------------------------------------------------------------- ping / http
ticks = iter([0.0, 0.0234])
c = extras.ping_card("", "example.com", connect=lambda h, p: None, clock=lambda: next(ticks))
check("ping up", c["t"] == "23 ms" and c["a"] == "example.com:443" and c["b"] == "reachable", c)


def refuse(h, p):
    raise ConnectionRefusedError(111, "Connection refused")


c = extras.ping_card("NAS", "192.168.1.5:22", connect=refuse)
check("ping down", c["t"] == "DOWN" and c["a"] == "192.168.1.5:22" and "refused" in c["b"].lower() and c["label"] == "NAS", c)
check("host:port validation", raises(lambda: extras.parse_hostport("bad host!"), "enter a server") and raises(lambda: extras.parse_hostport("a:99999"), "enter a server") and extras.parse_hostport("a.b:22") == ("a.b", 22))
ticks = iter([0.0, 0.2])
c = extras.http_card("", "https://example.com/x", fetch=lambda u: 200, clock=lambda: next(ticks))
check("http up", c["t"] == "UP" and "HTTP 200" in c["b"] and "200 ms" in c["b"] and c["a"] == "example.com", c)
c = extras.http_card("", "https://example.com", fetch=lambda u: 503)
check("http error status", c["t"] == "ERROR" and "503" in c["b"], c)


def down(u):
    raise OSError("name not resolved")


c = extras.http_card("", "https://nope.invalid", fetch=down)
check("http down", c["t"] == "DOWN", c)
check("http validation", raises(lambda: extras.validate({"type": "http", "arg": "ftp://x"}), "http") and extras.validate({"type": "http", "arg": "https://x.y"})["arg"] == "https://x.y")
check("new kinds are all registered", all(k in extras.TTL for k in extras.KINDS) and len(extras.KINDS) == 14)
b = extras.build({"type": "birthday", "label": "", "arg": "Z 06-01"}, today=today)
check("build dispatches", b["t"] == "Z")

# ---------------------------------------------------------------- ring / progress cards (firmware 1.5 card kinds)
c = extras.progress_card("", "year", dt.datetime(2026, 7, 2, 12, 0))
check("year progress", c["k"] == "p" and c["t"] == "50" and c["a"] == "of 2026" and c["b"] == "day 183 of 365", c)
c = extras.progress_card("", "month", dt.datetime(2026, 2, 15, 0, 0))
check("month progress", c["t"] == "50" and "of 28" in c["b"], c)
c = extras.progress_card("", "week", dt.datetime(2026, 10, 7, 12, 0))                  # a Wednesday
check("week progress", c["t"] == "35" and c["b"] == "Wednesday", c)
check("day progress", extras.progress_card("", "day", dt.datetime(2026, 1, 1, 6, 0))["t"] == "25" and extras.progress_card("", "day", dt.datetime(2026, 1, 1, 6, 0))["b"] == "06:00")
check("working day progress", extras.progress_card("", "work", dt.datetime(2026, 1, 1, 13, 0))["t"] == "50" and extras.progress_card("", "work", dt.datetime(2026, 1, 1, 7, 0))["t"] == "0"
      and extras.progress_card("", "work", dt.datetime(2026, 1, 1, 20, 0))["t"] == "100")
check("december month length", "of 31" in extras.progress_card("", "month", dt.datetime(2026, 12, 31, 0, 0))["b"])
check("progress validation", extras.validate({"type": "progress", "arg": ""})["arg"] == "year" and raises(lambda: extras.validate({"type": "progress", "arg": "decade"}), "year, month"))


class Batt:
    def __init__(self, pct, plugged, secs):
        self.percent, self.power_plugged, self.secsleft = pct, plugged, secs


c = extras.battery_card("", lambda: Batt(81.6, False, 7500))
check("battery ring", c["k"] == "r" and c["t"] == "82" and c["a"] == "on battery" and c["b"] == "2h 05m left", c)
c = extras.battery_card("", lambda: Batt(40, True, -2))
check("battery charging", c["a"] == "charging" and c["b"] == "", c)
check("no battery", raises(lambda: extras.battery_card("", lambda: None), "no battery"))


class Usage:
    percent, used, total = 63.4, 300 * 1024 ** 3, 500 * 1024 ** 3


c = extras.disk_card("", "/data", lambda p: Usage())
check("disk ring", c["k"] == "r" and c["t"] == "63" and c["a"] == "300 of 500 GB" and c["b"] == "/data", c)
check("disk error", raises(lambda: extras.disk_card("", "/nope", lambda p: (_ for _ in ()).throw(OSError(2, "No such file"))), "cannot read"))
c = extras.load_card("", 37.4, 55.2)
check("load ring", c["k"] == "r" and c["t"] == "37" and c["b"] == "RAM 55%", c)
check("real psutil cards work", extras.load_card("")["k"] == "r" and extras.disk_card("", "/")["k"] == "r")

# ---------------------------------------------------------------- lyrics
LRC = "[00:05.00] First line\n[00:10.50] Second line\n[00:20.00]\n[00:25.00][01:00.00] Chorus\n[bad] x\nno stamp"
lines = lyrics.parse_lrc(LRC)
check("parse lrc", lines[0] == (5.0, "First line") and len(lines) == 5 and lines[-1] == (60.0, "Chorus"), lines)
check("line_at", lyrics.line_at(lines, 0) == ("", "First line") and lyrics.line_at(lines, 12) == ("Second line", "") and lyrics.line_at(lines, 26) == ("Chorus", "Chorus")
      and lyrics.line_at(lines, 100) == ("Chorus", "") and lyrics.line_at([], 5) == ("", ""), [lyrics.line_at(lines, p) for p in (0, 12, 26, 100)])
fetched = []
ly = lyrics.Lyrics(fetch=lambda url: fetched.append(url) or {"syncedLyrics": LRC}, runner=lambda a: "7.5\n", system="Linux")
np = {"playing": True, "title": "Song", "artist": "Band", "album": ""}
c = ly.card(np)
check("long lyric lines use the scrolling card kind", lyrics.Lyrics(fetch=lambda u: {"syncedLyrics": "[00:01.00] A line that is far too long for the card"}).card(np, 2)["k"] == "s"
      and lyrics.Lyrics(fetch=lambda u: {"syncedLyrics": "[00:01.00] Hi"}).card(np, 2)["k"] == "c", c)
check("lyrics card", c["t"] == "First line" and c["a"] == "Second line" and c["label"] == "LYRICS" and "artist_name=Band" in fetched[0], c)
ly.card(np, pos=15)
check("lyrics fetched once per song", len(fetched) == 1)
check("explicit position", ly.card(np, pos=22)["t"] == "..." or ly.card(np, pos=22)["t"] == "...", ly.card(np, pos=22))
check("plain lyrics fallback", lyrics.Lyrics(fetch=lambda u: {"plainLyrics": "a\n\nb\nc"}).card(np)["a"] == "b")


def notfound(u):
    raise OSError("404")


check("no lyrics", raises(lambda: lyrics.Lyrics(fetch=notfound).card(np), "no lyrics"))
check("nothing playing", raises(lambda: ly.card(None), "nothing is playing"))
check("position parsing", lyrics.position(lambda a: "12.25\n", "Linux") == 12.25 and lyrics.position(lambda a: "x", "Linux") is None and lyrics.position(lambda a: None, "Linux") is None
      and lyrics.position(lambda a: "3,5", "Darwin") == 3.5 and lyrics.position(lambda a: "1", "Windows") is None)
nosync = lyrics.Lyrics(fetch=lambda u: {"syncedLyrics": LRC}, runner=lambda a: None, system="Linux")
check("unknown position shows the first line", nosync.card(np)["t"] == "First line")

# ---------------------------------------------------------------- cover art / QR
check("art url", padimage.art_url(lambda a: "file:///x.png\n", "Linux") == "file:///x.png" and raises(lambda: padimage.art_url(lambda a: "", "Linux"), "no cover") and raises(lambda: padimage.art_url(None, "Windows"), "Linux"))
src = Image.new("RGB", (300, 200), (200, 30, 30))
buf = io.BytesIO()
src.save(buf, "PNG")
img = padimage.fetch_image("https://x/y.png", opener=lambda u: buf.getvalue())
sq = padimage.square(img)
check("square crop", sq.size == (240, 240) and sq.getpixel((120, 120))[0] > 150)
check("bad scheme / bad data", raises(lambda: padimage.fetch_image("ftp://x"), "unsupported") and raises(lambda: padimage.fetch_image("https://x", opener=lambda u: b"junk"), "could not be read"))
gif = padimage.to_gif(sq)
check("gif bytes", gif[:6] in (b"GIF87a", b"GIF89a") and Image.open(io.BytesIO(gif)).size == (240, 240))
q = padimage.qr_image("https://example.com/hello")
check("qr image", q.size == (240, 240) and q.getpixel((0, 0)) == (255, 255, 255) and any(q.getpixel((x, 120)) == (0, 0, 0) for x in range(240)))
check("qr errors", raises(lambda: padimage.qr_image("  "), "nothing to encode") and raises(lambda: padimage.qr_image("x" * 400), "too long"))
try:                                                                  # decode it back, when a decoder happens to be installed
    import cv2
    import numpy as np_
    det = cv2.QRCodeDetector().detectAndDecode(np_.array(q))[0]
    check("qr decodes", det == "https://example.com/hello", det)
except ImportError:
    print("  skip qr decode (opencv not installed)")

# ---------------------------------------------------------------- audio spectrum
RATE = 16000


def tone(freq, n=1024, amp=0.5):
    return [amp * math.sin(2 * math.pi * freq * i / RATE) for i in range(n)]


lo, mid, hi = (audio.band_levels(tone(f), RATE) for f in (120, 800, 5000))
check("band separation", lo[0] > 0.5 > lo[1] + lo[2] and mid[1] > 0.5 > mid[0] + mid[2] and hi[2] > 0.5 > hi[0] + hi[1], (lo, mid, hi))
check("silence is dark", audio.band_levels([0.0] * 1024, RATE) == [0.0, 0.0, 0.0] and audio.color([0.0, 0.0, 0.0]) == (0, 0, 0) and audio.color([0.02, 0.02, 0.01]) == (0, 0, 0))
check("colour mapping", audio.color([1.0, 0.0, 0.5]) == (255, 0, 127) and audio.color([2, -1, 0.2]) == (255, 0, 51))
check("empty block", audio.goertzel([], RATE, 100) == 0.0)


class FakeStream:
    def __init__(self, cb):
        self.cb, self.running = cb, False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def close(self):
        pass


made = []
sp = audio.Spectrum(stream_factory=lambda cb: made.append(FakeStream(cb)) or made[-1], rate=RATE)
sp.start()
check("spectrum starts", made[0].running)
made[0].cb([[x] for x in tone(800)], 1024, None, None)
check("spectrum levels follow the input", sp.current()[1] > 0.5)
made[0].cb([[0.0]] * 1024, 1024, None, None)
check("spectrum decays smoothly", 0.0 < sp.current()[1] < 0.5)
sp.stop()
check("spectrum stops", not made[0].running and sp.current() == [0.0, 0.0, 0.0])


def nodev(cb):
    raise OSError("no input device")


check("no audio device", raises(lambda: audio.Spectrum(stream_factory=nodev).start(), "no usable audio input"))

# ---------------------------------------------------------------- mood + alerts
check("mood: night violet, noon white, evening orange", ledfx.mood_color(dt.datetime(2026, 1, 1, 0, 0)) == (10, 0, 40) and ledfx.mood_color(dt.datetime(2026, 1, 1, 13, 0)) == (255, 255, 235)
      and ledfx.mood_color(dt.datetime(2026, 1, 1, 18, 0)) == (255, 150, 60))
m1, m2 = ledfx.mood_color(dt.datetime(2026, 1, 1, 7, 30)), ledfx.mood_color(dt.datetime(2026, 1, 1, 7, 31))
check("mood is smooth", max(abs(a - b) for a, b in zip(m1, m2)) <= 3)
at = ledfx.AlertTracker()
check("baseline gives no alert", at.update({"badge:mail": 3, "ci0": "failure"}) == [])
check("no change no alert", at.update({"badge:mail": 3, "ci0": "failure"}) == [])
check("mail grew", at.update({"badge:mail": 4, "ci0": "failure"}) == [("badge:mail", "20a0ff", 2)])
check("mail shrank", at.update({"badge:mail": 1, "ci0": "failure"}) == [])
check("ci green", at.update({"badge:mail": 1, "ci0": "success"}) == [("ci0", "20ff60", 1)])
check("ci red", at.update({"badge:mail": 1, "ci0": "failure"}) == [("ci0", "ff2030", 3)])
check("ci still running is silent", at.update({"badge:mail": 1, "ci0": "in progress"}) == [])
check("new badge appears", at.update({"badge:mail": 1, "badge:chat": 2, "ci0": "in progress"}) == [("badge:chat", "20a0ff", 2)])
check("event soon", at.update({"badge:mail": 1, "badge:chat": 2, "ci0": "in progress", "event": "09:00 Standup"}) == [("event", "ffb020", 2)])

# ---------------------------------------------------------------- app glue
import companion_app as m   # noqa: E402

app = m.App()
app.update()


def pump(n=20, cond=None):
    for _ in range(n * (3 if cond else 1)):
        app.update()
        time.sleep(0.05)
        if cond and cond():
            return True
    return cond is None


app.toggle_simulate()
assert pump(60, lambda: app.dev.connected)
# QR to a spare slot through the host op and the pad shows it
ok, msg = app.hostact.run_trusted("qr", "https://example.com")
check("qr op", ok and "slot 4" in msg, msg)
pump(20)
r = app.dev.request({"cmd": "gif_list"})
check("qr stored in slot 4 and selected", any(s["s"] == 3 for s in r["slots"]) and r["cur"] == 3, r)
ok, msg = app.hostact.run_trusted("qr", "")
check("empty qr uses the clipboard", ok or "nothing to encode" in msg or "clipboard" in msg.lower(), msg)
app._clipboard_text = lambda: "from the clipboard"
ok, msg = app.hostact.run_trusted("qr", "")
check("qr from clipboard", ok, msg)
srcp = Path(tmp) / "art.png"
sq.save(srcp)
padimage_art = padimage.art_url
padimage.art_url = lambda: srcp.as_uri()
ok, msg = app.hostact.run_trusted("art", "")
padimage.art_url = padimage_art
check("art op", ok and "cover art" in msg, msg)
# pad that is not connected / without slots
app.dev.disconnect()
pump(10)
ok, msg = app.hostact.run_trusted("qr", "x")
check("qr needs the pad", not ok and "not connected" in msg, msg)

# LED: mood follows the clock, CPU wins over mood, audio switches both off
app.toggle_simulate()
assert pump(60, lambda: app.dev.connected)
sent = []
real = app.dev.request
app.dev.request = lambda msg, timeout=2.0: sent.append(msg) or real(msg, timeout)
app.cfg.update(led_mood=True, led_cpu=False, led_audio=False)
app._led_cpu_last = None
app._led_follow_cpu(10)
check("mood LED sent", sent and sent[-1]["cmd"] == "led" and "r" in sent[-1], sent[-1:])
app.cfg["led_cpu"] = True
app._led_cpu_last = None
sent.clear()
app._led_follow_cpu(95)
check("cpu has priority over mood", sent and sent[-1]["r"] > sent[-1]["g"], sent)
app.cfg.update(led_cpu=False, led_mood=False)
sent.clear()
app._led_follow_cpu(10)
check("LED back to auto", sent and sent[-1] == {"cmd": "led", "mode": "auto"}, sent)

# the audio thread drives the LED from the spectrum
made.clear()
app.spectrum = audio.Spectrum(stream_factory=lambda cb: made.append(FakeStream(cb)) or made[-1], rate=RATE)
frames = []
app.dev.send = lambda msg: frames.append(msg)
app.cfg["led_audio"] = True
pump(30, lambda: made)
made[0].cb([[x] for x in tone(120, amp=0.9)], 1024, None, None)
pump(20, lambda: any(f.get("cmd") == "led" and f.get("r", 0) > 100 for f in frames))
check("audio drives the LED", any(f.get("cmd") == "led" and f.get("r", 0) > 100 and f.get("b", 0) < 60 for f in frames), frames[-3:])
app.cfg["led_audio"] = False
pump(30, lambda: not made[0].running)
check("audio stops when switched off", not made[0].running)
app.dev.send = type(app.dev).send.__get__(app.dev)
# no sounddevice here: the thread must switch the setting off with a reason instead of crashing
app.spectrum = audio.Spectrum()
real_start = app.spectrum.start
app.spectrum.start = lambda: (_ for _ in ()).throw(ValueError("the sound-reactive LED needs:  pip install sounddevice numpy"))
app.cfg["led_audio"] = True
pump(40, lambda: not app.cfg["led_audio"])
pump(10)
check("missing sound library is reported", app.cfg["led_audio"] is False and "sounddevice" in app.status.cget("text"), app.status.cget("text"))
app.spectrum.start = real_start

# LED alerts: sent only when the switch is on and the pad has the LED effects
alerts = []
app.dev.request = lambda msg, timeout=2.0: alerts.append(msg) or {"ok": True}
app.dev.info["caps"] = list(app.dev.info.get("caps") or []) + ["ledfx"]
app._alert_snap = {"badge:mail": 1}
app.alerts = ledfx.AlertTracker()
app._led_alerts()
app._alert_snap = {"badge:mail": 2}
app.cfg["led_alerts"] = False
found = app._led_alerts()
check("alert detected but not sent while switched off", found and not alerts)
app._alert_snap = {"badge:mail": 3}
app.cfg["led_alerts"] = True
app._led_alerts()
check("alert sent to the pad", alerts and alerts[-1]["cmd"] == "led" and alerts[-1]["alert"] == "20a0ff", alerts)
app.dev.request = real

# the Info page offers the new card kinds and the app config keeps them
app.cfg["info"]["extras"] = [{"type": "ping", "label": "NAS", "arg": "192.168.1.5:22"}, {"type": "birthday", "label": "", "arg": "A 01-01"}, {"type": "bogus", "arg": "x"}]
n = m.normalize_config(dict(app.cfg))
check("config keeps valid new extras, drops bogus", [e["type"] for e in n["info"]["extras"]] == ["ping", "birthday"], n["info"]["extras"])
for kind in extras.KINDS:
    check(f"hint for {kind}", kind in m.App.EXTRA_HINTS)

print("\nFAILED: " + ", ".join(FAILS) if FAILS else "\nALL DATA TESTS PASSED")
os._exit(1 if FAILS else 0)
