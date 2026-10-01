"""Info screen feeds in the app: now playing, weather, calendar, custom card, badges -> pad cards.   xvfb-run -a python3 tools/info_test.py"""
import datetime as dt
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

tmp = tempfile.mkdtemp(prefix="dcinfo_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")


class H(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        H.hits.append(self.path)
        if self.path.startswith("/geo/search"):
            body = {"results": [{"name": "Zürich", "latitude": 47.37, "longitude": 8.54}]}
        elif self.path.startswith("/meteo/forecast"):
            body = {"current": {"temperature_2m": 18.2, "weather_code": 61, "wind_speed_10m": 12.0}}
        else:
            self.send_response(404); self.end_headers(); return
        d = json.dumps(body).encode(); self.send_response(200); self.end_headers(); self.wfile.write(d)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["DESK_COMPANION_METEO"] = f"http://127.0.0.1:{srv.server_port}/meteo"
os.environ["DESK_COMPANION_GEOCODE"] = f"http://127.0.0.1:{srv.server_port}/geo"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m   # noqa: E402
from desk_lib import feeds   # noqa: E402

soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=40)).strftime("%Y%m%dT%H%M%SZ")
ics = tmp + "/cal.ics"
open(ics, "w").write(f"BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:{soon}\nSUMMARY:Sprint planning\nEND:VEVENT\nEND:VCALENDAR\n")
playing = {"v": {"playing": True, "title": "Midnight City", "artist": "M83", "album": "Hurry Up"}}
feeds.now_playing = lambda *a, **k: playing["v"]

app = m.App()
app.update()


def pump(n=20, cond=None):
    for _ in range(n):
        app.update()
        time.sleep(0.05)
        if cond and cond():
            return True
    return cond is None


app.toggle_simulate()
assert pump(80, lambda: app.dev.connected)
sim = app.dev.ser.sim
app.tabs.set("Info Screen")

# ---- city lookup (mock geocoder) turns weather on
app.info_city.insert(0, "Zurich"); app.info_find_city()
assert pump(60, lambda: app.cfg["info"].get("lat") == 47.37), app.cfg["info"]
assert app.cfg["info"]["label"] == "Zurich" and app.info_vars["weather"].get()
# ---- calendar + custom card
app.info_ics.insert(0, ics); app.info_vars["event"].set(True)
app.info_vars["custom"].set(True)
app.info_custom["c_label"].insert(0, "MOTTO"); app.info_custom["c_t"].insert(0, "Ship it"); app.info_custom["c_a"].insert(0, "Café later")
app._info_changed()
cards, badges, errs = app._info_collect()
kinds = [c["k"] for c in cards]
assert kinds == ["m", "e", "w", "c"], kinds
assert cards[0]["t"] == "Midnight City" and cards[1]["a"] == "Sprint planning" and cards[1]["b"].startswith("in ") and cards[1]["b"].endswith(" min")
assert cards[2] == {"k": "w", "label": "WEATHER  Zurich", "t": "18 C", "a": "Light rain", "b": "wind 12 km/h"}, cards[2]
assert cards[3]["a"] == "Cafe later" and cards[3]["label"] == "MOTTO", cards[3]           # ASCII-folded for the pad's fonts
assert all(v == "" for v in errs.values()), errs
n_hits = len(H.hits)
app._info_collect(); assert len(H.hits) == n_hits, "weather / calendar must be cached between updates"

# ---- send to the pad (sim) + badge over HTTP with the token
app.badges.set("mail", 3)
app.info_send_now(force=True)
assert pump(60, lambda: len(sim.cards) == 4), sim.cards
assert sim.badges == [{"name": "mail", "n": 3}], sim.badges
assert app.pad.cards == cards and app.info_preview.cards == cards and "sent to the pad" in app.info_status.cget("text")
base = f"http://127.0.0.1:{app.badges.port}/badge"
urllib.request.urlopen(f"{base}?name=chat&n=7&token={app.badges.token}", timeout=3).read()
try:
    urllib.request.urlopen(f"{base}?name=chat&n=9&token=nope", timeout=3)
    raise SystemExit("badge without the token accepted")
except urllib.error.HTTPError as e:
    assert e.code == 403
assert {"name": "chat", "n": 7} in app.badges.get()

# ---- the background loop pushes changes on its own, and skips unchanged data
app.info_poll = 0.15
sim.cards = []
assert pump(80, lambda: len(sim.cards) == 4), "loop did not send"
playing["v"] = {"playing": False, "title": "Another Song", "artist": "Someone", "album": ""}
assert pump(80, lambda: sim.cards and sim.cards[0]["t"] == "Another Song"), sim.cards
assert sim.cards[0]["label"] == "PAUSED"
# ---- nothing playing -> the card disappears, a message explains
playing["v"] = None
assert pump(80, lambda: sim.cards and sim.cards[0]["k"] == "e"), sim.cards
# ---- firmware without the Info screen is left alone
app.dev.info["modes"] = 5
sim.cards = []
playing["v"] = {"playing": True, "title": "x", "artist": "y", "album": ""}
time.sleep(0.8); pump(10)
assert sim.cards == [], "must not send info cards to a pad that has no Info screen"
app.dev.info["modes"] = 6
# ---- bad calendar / weather sources are reported, not fatal
app.cfg["info"]["ics"] = "/no/such/file.ics"; app._info_cache.clear()
c2, b2, e2 = app._info_collect()
assert "calendar problem" in e2["event"] and [c["k"] for c in c2] == ["m", "w", "c"], (e2, c2)
app.destroy()
print("ALL INFO TESTS PASSED")
