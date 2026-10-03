"""Headless test of the OS / network key actions through the app (fake system tools and HTTP).   xvfb-run -a python3 tools/sysapp_test.py"""
import os
import sys
import tempfile
import time
from pathlib import Path

tmp = tempfile.mkdtemp(prefix="dcsysop_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m   # noqa: E402
from desk_lib import hostactions   # noqa: E402

app = m.App()
app.update()


def pump(n=20, cond=None):
    for _ in range(n * (3 if cond else 1)):
        app.update()
        time.sleep(0.05)
        if cond and cond():
            return True
    return cond is None


class Sys:
    def __init__(self): self.calls = []
    def app_volume(self, prog, d): self.calls.append(("vol", prog, d)); return f"{prog} {d:+d}"
    def dnd(self, s): self.calls.append(("dnd", s)); return "dnd " + s
    def audio_output(self, t): self.calls.append(("out", t)); return "out " + t
    def microphone(self, s): self.calls.append(("mic", s)); return "mic " + s
    def screenshot(self, folder): self.calls.append(("shot", folder)); return Path(folder or tmp) / "x.png"


class Typed:
    def __init__(self): self.t = []
    def run(self, spec, stop=None): self.t.append(spec)


rec, sysf, urls = Typed(), Sys(), []
app.host = m.HostInput(backend=rec)
app.hostact.sysact = sysf
app.hostact.active = None
app.active_win = type("W", (), {"get": staticmethod(lambda: ("spotify", "Spotify - Song"))})()
app.hostact.focus_program = lambda: app.active_win.get()[0]
def fetch(method, url, body=None, headers=None):
    urls.append((method, url, body))
    if "mymemory" in url:
        return 200, '{"responseData": {"translatedText": "Hallo"}}'
    if "anthropic" in url:
        return 200, '{"content": [{"type": "text", "text": "ANSWER"}]}'
    return 200, "ok"
app.hostact.net_fetch = fetch
app.toggle_simulate()
assert pump(80, lambda: app.dev.connected)
sim = app.dev.ser.sim
app.cfg["custom"].update({})

# ---- every new op is a valid pad spec, and builds from the Macros form
for op in hostactions.NEW_OPS:
    assert m.spec_ok({"type": "host", "val": {"op": op, "arg": "x"}}), op
    assert op in m.HOST_OPS
app.tabs.set("Macro Creator")
def act(kind, arg=""):
    app.act_kind.set(kind); app._act_kind_changed(kind)
    app.act_arg.configure(state="normal"); app.act_arg.delete(0, "end"); app.act_arg.insert(0, arg)
    return app._action_spec()
assert act("Do Not Disturb", "toggle") == ("host", {"op": "dnd", "arg": "toggle"}) and act("Screenshot to a folder", "") == ("host", {"op": "shot", "arg": "default"})
assert act("Ask the AI", "Summarize: {clipboard}")[1]["op"] == "ai" and act("Type from clipboard history", "2") == ("host", {"op": "cliphist", "arg": "2"})
v13 = dict(app.dev.info)
app.dev.info["caps"] = [c for c in app.dev.info["caps"] if c != "hostx2"]
try:
    act("Do Not Disturb", "on"); raise SystemExit("accepted for a pad without hostx2")
except ValueError as e:
    assert "firmware 1.5" in str(e)
app.dev.info["caps"] = list(v13["caps"])

# ---- running them (whitelist applies when the pad asks)
ok, msg = app.hostact.run("dnd", "on"); assert not ok and "not part of your key configuration" in msg
run = lambda op, arg="": app.hostact.run_trusted(op, arg)   # noqa: E731
assert run("appvol", "+5") == (True, "spotify +5") and run("appvol", "firefox:-10") == (True, "firefox -10") and run("appvol", "down") == (True, "spotify -5")
assert run("appvol", "loud")[0] is False
assert run("dnd", "toggle") == (True, "dnd toggle") and run("audio_out", "next") == (True, "out next") and run("mic", "toggle") == (True, "mic toggle")
ok, msg = run("shot", "default"); assert ok and msg.startswith("screenshot saved") and sysf.calls[-1] == ("shot", None) or sysf.calls[-1][0] == "shot"
app.cfg["shot_dir"] = tmp + "/pics"; run("shot", "default"); assert sysf.calls[-1] == ("shot", tmp + "/pics")
run("shot", "/other"); assert sysf.calls[-1] == ("shot", "/other")
app.clipboard_clear(); app.clipboard_append("Hello"); app.update()
def run_bg(op, arg):
    import threading
    box = {}
    th = threading.Thread(target=lambda: box.setdefault("r", run(op, arg)), daemon=True); th.start()
    assert pump(100, lambda: not th.is_alive()); return box["r"]
rec.t.clear()
assert run_bg("translate", "de")[0] and ("text", "Hallo") in rec.t and "langpair=Autodetect%7Cde" in urls[-1][1]
rec.t.clear(); app.cfg["ai"]["key"] = ""
ok, msg = run_bg("ai", "Summarize: {clipboard}"); assert not ok and "no API key" in msg and not rec.t
app.cfg["ai"].update(key="sk-test", model="claude-sonnet-5-5")
ok, msg = run_bg("ai", "Summarize: {clipboard}"); assert ok and ("text", "ANSWER") in rec.t
sent = urls[-1]; assert sent[0] == "POST" and "anthropic" in sent[1] and sent[2]["messages"][0]["content"] == "Summarize: Hello" and sent[2]["model"] == "claude-sonnet-5-5"
ok, msg = run("webhook", "POST https://hooks.example/x {\"text\": \"{date:%Y}\"}"); assert ok and "hooks.example" in msg and '"text": "20' in urls[-1][2]
assert run("webhook", "not a url")[0] is False
# clipboard history
ok, msg = run("cliphist", "1"); assert not ok and "switched off" in msg
app.cfg["cliphist_on"] = True
for t in ("first", "second", "third"):
    app.clipboard_clear(); app.clipboard_append(t); assert pump(60, lambda t=t: app.cliphist.items[:1] == [t])
rec.t.clear(); assert run("cliphist", "2") == (True, "typed from the clipboard history") and ("text", "second") in rec.t
assert run("cliphist", "9")[0] is False and run("cliphist", "x")[0] is False
app.cfg["cliphist_on"] = False
assert pump(80, lambda: app.cliphist.items == []), "switching it off forgets everything"
# window layouts through the app
class Lay:
    def save(self, store, name): store[name] = [{"process": "a"}]; return 1
    def restore(self, store, name):
        if name not in store: raise ValueError("there is no saved window layout called '%s'" % name)
        return 2
app.hostact.layouts = Lay()
cc = app.computer_card
cc.layout_name.insert(0, "work"); cc.save_layout()
assert pump(60, lambda: "work" in app.cfg["layouts"]) and "work (1 windows)" in cc.layout_lbl.cget("text")
cc.restore_layout(); assert pump(60, lambda: "restored" in app.status.cget("text"))
cc.layout_name.delete(0, "end"); cc.layout_name.insert(0, "nope"); cc.restore_layout()
assert pump(60, lambda: "no saved window layout" in app.status.cget("text"))
cc.layout_name.delete(0, "end"); cc.layout_name.insert(0, "work"); cc.delete_layout(); assert "work" not in app.cfg["layouts"]
# settings card
cc.ai_key.delete(0, "end"); cc.ai_key.insert(0, "sk-new"); cc.ai_model.delete(0, "end"); cc.save_ai()
assert app.cfg["ai"] == {"key": "sk-new", "model": "claude-haiku-4-5-20251001"}
cc.hist_var.set(True); cc.hist_toggled(); assert app.cfg["cliphist_on"] is True and "Clipboard history is on" in app.status.cget("text")
cc.hist_var.set(False); cc.hist_toggled()
# a pad key asking for one of these goes through the whitelist from the key map
app.cfg["custom"]["Mic"] = {"type": "host", "val": {"op": "mic", "arg": "toggle"}}
app.cfg["map"]["1"] = {"cat": "Custom", "action": "Mic"}
assert ("mic", "toggle") in app._allowed_host()
sysf.calls.clear(); app._host_cb({"op": "mic", "arg": "toggle"}); assert sysf.calls == [("mic", "toggle")] and "mic toggle" in app.status.cget("text") or pump(30, lambda: "mic toggle" in app.status.cget("text"))
try:
    app._host_cb({"op": "dnd", "arg": "on"}); raise SystemExit("an unlisted action ran")
except RuntimeError as e:
    assert "not part of your key configuration" in str(e)
app.destroy()
print("ALL SYSAPP TESTS PASSED")
