"""Headless test of the Automation page: building rules from the form, running them, the schedule loop, persistence.
xvfb-run -a python3 tools/automation_test.py"""
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta

tmp = tempfile.mkdtemp(prefix="dcauto_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m   # noqa: E402
from desk_lib import automation as au   # noqa: E402

# ---- build_entry: form fields -> validated rule
be = au.build_entry
e = be("Weekdays", "09:00", "Switch layer", "2", name="work")
assert e["when"] == {"kind": "daily", "time": "09:00", "days": [0, 1, 2, 3, 4]} and e["do"] == {"kind": "layer", "n": 1}
assert be("Every day", "21:30", "Set brightness", "40")["do"] == {"kind": "brightness", "n": 40}
assert be("Every N minutes", "30", "LED", "#FF8800")["do"] == {"kind": "led", "mode": "solid", "hex": "ff8800"}
assert be("Every N minutes", "30", "LED", "off")["do"] == {"kind": "led", "mode": "off"}
assert be("Once", "2026-10-03 15:00", "Show a reminder", "tea")["when"]["at"] == "2026-10-03T15:00"
assert be("Every day", "08:00", "Open website", "https://example.org")["do"] == {"kind": "host", "op": "url", "arg": "https://example.org"}
assert be("Every day", "08:00", "Switch layer", "next")["do"]["n"] == "next"
for bad in (("Every day", "9am", "Switch layer", "1"), ("Every N minutes", "x", "Switch layer", "1"), ("Every day", "09:00", "Show screen", "x"),
            ("Every day", "09:00", "Switch layer", "9"), ("Every day", "09:00", "Show a reminder", " "), ("Once", "soon", "Switch layer", "1")):
    try:
        be(*bad); raise SystemExit(f"accepted {bad}")
    except ValueError:
        pass

# ---- run_entry talks to the pad / host / notifier
sent, ran, notes = [], [], []
req, hrun, note = sent.append, lambda op, arg: (ran.append((op, arg)) or (True, "ok")), notes.append
au.run_entry(be("Every day", "09:00", "Switch layer", "3"), req, hrun, note)
au.run_entry(be("Every day", "09:00", "Show screen", "6"), req, hrun, note)
au.run_entry(be("Every day", "09:00", "Set brightness", "60"), req, hrun, note)
au.run_entry(be("Every day", "09:00", "LED", "ff0000"), req, hrun, note)
au.run_entry(be("Every day", "09:00", "LED", "auto"), req, hrun, note)
au.run_entry(be("Every day", "09:00", "Open website", "https://a.b"), req, hrun, note)
au.run_entry(be("Every day", "09:00", "Show a reminder", "stretch"), req, hrun, note)
assert sent == [{"cmd": "layer", "val": 2}, {"cmd": "mode", "val": 6}, {"cmd": "brightness", "val": 60}, {"cmd": "led", "hex": "ff0000"}, {"cmd": "led", "mode": "auto"}], sent
assert ran == [("url", "https://a.b")] and notes == ["stretch"]
try:
    au.run_entry(be("Every day", "09:00", "Open website", "https://a.b"), req, lambda o, a: (False, "refused: nope"), note); raise SystemExit("ignored a refusal")
except RuntimeError as ex:
    assert "refused" in str(ex)

# ---- the app
app = m.App()
app.update()


def pump(n=20, cond=None):
    for _ in range(n * (3 if cond else 1)):
        app.update()
        time.sleep(0.05)
        if cond and cond():
            return True
    return cond is None


assert "Automation" in [n for _g, pages in m.PAGES for n, _l, _s in pages] and app.cfg["schedules"] == []
app.toggle_simulate()
assert pump(80, lambda: app.dev.connected)
sim = app.dev.ser.sim
app.tabs.set("Automation")
page = app.automation
page.when.set("Every day"); page._when_changed(); page.when_arg.insert(0, "10:00")
page.do.set("Switch layer"); page._do_changed(); page.do_arg.insert(0, "3"); page.name.insert(0, "evening")
page.add()
assert len(app.cfg["schedules"]) == 1 and app.cfg["schedules"][0]["name"] == "evening", app.status.cget("text")
page.do_arg.delete(0, "end"); page.do_arg.insert(0, "9"); page.add()
assert len(app.cfg["schedules"]) == 1 and "Cannot add the rule" in app.status.cget("text"), "a bad rule is refused with a reason"
page.do.set("Run shell command"); page._do_changed(); page.do_arg.insert(0, "echo hi"); page.add()
assert len(app.cfg["schedules"]) == 1 and "switched off" in app.status.cget("text")

# run now -> the simulated pad really changes layer
app.run_schedule(app.cfg["schedules"][0], manual=True)
assert pump(60, lambda: sim.layer == 2), "Run now switched the pad to layer 3"
assert "evening" in app.status.cget("text") or pump(20, lambda: "evening" in app.status.cget("text"))

# the loop: a due daily rule fires on the pad
sim.layer = 0
due = (datetime.now() + timedelta(seconds=0)).replace(second=0, microsecond=0)
app.cfg["schedules"][0]["when"] = {"kind": "daily", "time": due.strftime("%H:%M"), "days": list(range(7))}
fired = app.scheduler.tick(datetime.now().replace(second=5))
assert fired and fired[0]["id"] == app.cfg["schedules"][0]["id"]
app.run_schedule(fired[0])
assert pump(60, lambda: sim.layer == 2)

# host rules join the whitelist, shell rules need the switch, once rules retire and the page refreshes
app.cfg["schedules"].append(be("Every day", "08:00", "Open website", "https://example.org/daily"))
assert ("url", "https://example.org/daily") in app._allowed_host()
opened = []
app.hostact.open_url = opened.append
app.run_schedule(app.cfg["schedules"][-1], manual=True)
assert pump(60, lambda: opened == ["https://example.org/daily"])
once = be("Once", (datetime.now() - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M"), "Show a reminder", "tea")
app.cfg["schedules"].append(once)
page.refresh()
app.scheduler.tick(datetime.now())
assert once["enabled"] is False
page._enable(0, False)
assert app.cfg["schedules"][0]["enabled"] is False
page._remove(0)
assert len(app.cfg["schedules"]) == 2

# persistence: rules survive a restart, junk in the config file is dropped
app.save_cfg()
cfg2 = m.load_config()
assert [x["do"]["kind"] for x in cfg2["schedules"]] == ["host", "notify"]
cfg2["schedules"].append({"when": {"kind": "daily", "time": "99:99"}, "do": {}}); cfg2["schedules"].append("junk")
m.CONFIG_PATH.write_text(__import__("json").dumps(cfg2)); assert len(m.load_config()["schedules"]) == 2
# ---- local API through the real app + simulated pad
import http.client, json   # noqa: E401,E402
assert not app.api and app.cfg["api"]["on"] is False and len(app.cfg["api"]["token"]) >= 16
page.api_port.delete(0, "end"); page.api_port.insert(0, "80")
page.api_on.set(True); page._api_toggled()
assert not app.api and page.api_on.get() is False and "1024" in app.status.cget("text"), "privileged port refused"
page.api_port.delete(0, "end"); page.api_port.insert(0, "0")
app.cfg["api"]["port"] = 0
assert app.api_start() and app.api.port
app.cfg["api"]["port"] = app.api.port


def api(method, path, body=None, token=None):
    c = http.client.HTTPConnection("127.0.0.1", app.api.port, timeout=10)
    c.request(method, path, body=None if body is None else json.dumps(body), headers={"Authorization": "Bearer " + (token or app.cfg["api"]["token"])})
    r = c.getresponse(); out = json.loads(r.read()); c.close()
    return r.status, out
st, out = api("GET", "/v1/status"); assert st == 200 and out["connected"] is True and out["app"] == m.APP_VERSION, out
assert api("POST", "/v1/layer", {"n": 2})[0] == 200 and sim.layer == 1
assert api("POST", "/v1/layer", {"n": "next"})[0] == 200 and sim.layer == 2
assert api("POST", "/v1/brightness", {"n": 77})[0] == 200 and sim.bright == 77
assert api("POST", "/v1/mode", {"n": 4})[0] == 200 and sim.mode == 4
assert api("POST", "/v1/led", {"hex": "ff8800"})[0] == 200 and (sim.led["r"], sim.led["g"], sim.led["b"]) == (255, 136, 0)
assert api("POST", "/v1/led", {"mode": "off"})[0] == 200 and sim.led["mode"] == 1
sim.host_log.clear()
assert api("POST", "/v1/press", {"key": 1})[0] == 200
assert api("POST", "/v1/card", {"label": "BUILD", "title": "green", "a": "main", "b": "42 tests"})[0] == 200
assert app.cfg["info"]["custom"] and app.cfg["info"]["c_t"] == "green"
assert api("POST", "/v1/card", {})[0] == 200 and not app.cfg["info"]["custom"]
assert api("POST", "/v1/card", {"label": "DISK", "title": "63", "a": "of 500 GB", "kind": "ring"})[0] == 200 and app.cfg["info"]["c_k"] == "r"
assert [c for c in app._info_collect()[0] if c["label"] == "DISK"][0]["k"] in ("r", "c")      # a pad without the 1.5 card kinds shows it as a plain card
assert api("POST", "/v1/card", {"label": "X", "title": "oops", "kind": "ring"})[0] == 400
api("POST", "/v1/card", {})
assert api("POST", "/v1/badge", {"name": "mail", "n": 4})[0] == 200 and {"name": "mail", "n": 4} in app.badges.get()
assert api("POST", "/v1/layer", {"n": 7})[0] == 400 and api("GET", "/v1/status", token="x" * 20)[0] == 401
app.dev.disconnect(); app._on_disconnected()
st, out = api("POST", "/v1/layer", {"n": 1}); assert st == 409 and "not connected" in out["error"], (st, out)
assert api("GET", "/v1/status")[1]["connected"] is False
old = app.cfg["api"]["token"]
page._api_newtoken()
assert app.cfg["api"]["token"] != old and api("GET", "/v1/status", token=old)[0] == 401 and api("GET", "/v1/status")[0] == 200, "a new token locks out the old one"
page.api_on.set(False); page._api_toggled()
assert app.api is None and app.cfg["api"]["on"] is False
app.destroy()
print("ALL AUTOMATION TESTS PASSED")
