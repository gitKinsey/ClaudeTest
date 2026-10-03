"""Engine test: info screen feeds - now playing, weather, calendar, custom card, badges -> pad cards."""
import datetime as dt
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit, isolate    # noqa: E402
tmp = isolate("dcinfo_")


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
import core.base as m   # noqa: E402
from desk_lib import feeds   # noqa: E402

soon = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=40)).strftime("%Y%m%dT%H%M%SZ")
ics = tmp + "/cal.ics"
open(ics, "w").write(f"BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:{soon}\nSUMMARY:Sprint planning\nEND:VEVENT\nEND:VCALENDAR\n")
playing = {"v": {"playing": True, "title": "Midnight City", "artist": "M83", "album": "Hurry Up"}}
feeds.now_playing = lambda *a, **k: playing["v"]

k = Kit("dcinfo2_", fresh=False)
app, pump = k.e, k.pump
sim = k.sim
done = []
app.on("info_done", lambda d: done.append(d))

# ---- city lookup (mock geocoder) turns weather on
app.info_find_city("Zurich")
assert pump(60, lambda: app.cfg["info"].get("lat") == 47.37), app.cfg["info"]
assert app.cfg["info"]["label"] == "Zurich" and app.cfg["info"]["weather"]
# ---- calendar + custom card
app.info_update(event=True, ics=ics, custom=True, c_label="MOTTO", c_t="Ship it", c_a="Café later")
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
assert pump(40, lambda: done and "sent to the pad" in done[-1]["status"]), done
assert app.pad.cards == cards and app.info_preview.cards == cards
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
# ---- extra cards: countdown / world clock / git / CI / crypto
from desk_lib import extras   # noqa: E402
import subprocess   # noqa: E402
app.info_update(music=False, weather=False, event=False, custom=False); app.cfg["info"]["extras"].clear()
def add_extra(kind, label, arg):
    app.extra_add(extras.KINDS[kind], label, arg)
add_extra("countdown", "Trip", "not a date")
assert app.cfg["info"]["extras"] == [] and "Cannot add the card" in k.status
target = (dt.date.today() + dt.timedelta(days=9)).isoformat()
add_extra("countdown", "Trip", target)
assert [c["t"] for c in app._info_collect()[0]] == ["9 days"], app._info_collect()[0]
repo = tmp + "/repo"; os.makedirs(repo)
subprocess.run(["git", "init", "-q", repo], check=True); open(repo + "/f.txt", "w").write("x")
add_extra("git", "", repo)
gc = app._info_collect()[0][1]
assert gc["k"] == "c" and gc["label"] == "GIT" and gc["a"] == "1 change", gc
add_extra("worldclock", "", "UTC")
def offline(url, timeout=8):
    raise OSError("offline (test)")
extras._fetch_json = offline                                    # the test must not depend on the internet
add_extra("crypto", "", "bitcoin")
assert len(app.cfg["info"]["extras"]) == 4
add_extra("ci", "", "a/b"); assert len(app.cfg["info"]["extras"]) == 4 and "at most 4" in k.status
cards, _b, errs = app._info_collect()
assert [c["k"] for c in cards[:3]] == ["e", "c", "c"] and errs["x3"].startswith("Crypto price:"), (cards, errs)   # a failing service is reported, not fatal
app.extra_remove(3); app.extra_remove(2)
app.info_poll = 0.15; sim.cards = []; app._info_sent = (None, 0.0)
assert pump(80, lambda: len(sim.cards) == 2), "extras alone are sent by the loop"
app.cfg["info"]["extras"][1]["arg"] = "/definitely/not/a/repo"; app._info_cache.clear()
assert app._info_collect()[2]["x1"].startswith("Git repository status:")
app.cfg["info"]["extras"].append({"type": "nope"}); app.save_cfg()
assert len(m.load_config()["info"]["extras"]) == 2, "junk card definitions are dropped on load"
k.close()
print("ALL ENGINE INFO TESTS PASSED")
