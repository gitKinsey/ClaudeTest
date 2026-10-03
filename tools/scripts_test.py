"""Headless test of the Scripts page and running scripts from a pad key.   xvfb-run -a python3 tools/scripts_test.py"""
import os
import sys
import tempfile
import threading
import time

tmp = tempfile.mkdtemp(prefix="dcscr_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
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


class Rec:
    def __init__(self):
        self.calls = []

    def run(self, spec, stop=None):
        self.calls.append(tuple(spec) if isinstance(spec, list) else spec)


rec = Rec()
app.host = m.HostInput(backend=rec)
app.hostact.open_url = lambda u: rec.calls.append(("OPEN", u))
app.toggle_simulate()
assert pump(80, lambda: app.dev.connected)
sim = app.dev.ser.sim
app.tabs.set("Scripts")
page = app.scripts
assert "Scripts" in [n for _g, pages in m.PAGES for n, _l, _s in pages] and app.cfg["scripts"] == {}

# ---- editor: invalid scripts are refused with the line, valid ones saved
page.name.insert(0, "demo")
page.editor.delete("1.0", "end"); page.editor.insert("1.0", "repeat 3\nkey ctrl+c\n")
page.save()
assert app.cfg["scripts"] == {} and "never closed" in app.status.cget("text") and "line 1" in app.status.cget("text"), app.status.cget("text")
page.check(); assert "Problem" in page.log.get("1.0", "end")
page.editor.delete("1.0", "end"); page.editor.insert("1.0", 'repeat 3\nkey ctrl+c\nwait 10\nend\nif clipboard "go"\nopen https://example.org/{counter:runs}\nend\n')
page.check(); assert "OK - 3 commands" in page.log.get("1.0", "end"), page.log.get("1.0", "end")
page.save()
assert list(app.cfg["scripts"]) == ["demo"] and page.pick.get() == "demo"
page.name.delete(0, "end"); page.name.insert(0, "bad/name"); page.save()
assert list(app.cfg["scripts"]) == ["demo"] and "Give the script a name" in app.status.cget("text")
page.name.delete(0, "end"); page.name.insert(0, "demo")
page.pretend.insert(0, "whatever"); page.dry()
out = page.log.get("1.0", "end")
assert "Dry run - nothing was sent" in out and out.count("key CTRL+c") == 3 and "(clipboard)" not in out and not rec.calls, out

# ---- assigned to a key: the pad asks, the app runs it (whitelisted because it is in the key map)
page.assign()
assert app.cfg["custom"]["Script: demo"]["val"] == {"op": "script", "arg": "demo"} and ("script", "demo") in app._allowed_host()
slot = int(app._target_slot())
app.upload_slots([slot])
assert pump(60, lambda: sim.slots.get(slot, {}).get("type") == "host")
app.clipboard_clear(); app.clipboard_append("let's go")
app.update()
rec.calls.clear()
assert app.dev.request({"cmd": "input", "k": slot})["ok"]
assert pump(100, lambda: ("OPEN", "https://example.org/1") in rec.calls), rec.calls
combos = [c for c in rec.calls if c[0] == "combo"]
assert len(combos) == 3 and combos[0] == ("combo", ["CTRL", "c"]), rec.calls
# the counter persisted; running again increments it
app.clipboard_clear(); app.clipboard_append("go again")
rec.calls.clear()
assert pump(40, lambda: not app._script_lock.locked())
app.dev.request({"cmd": "input", "k": slot})
assert pump(100, lambda: ("OPEN", "https://example.org/2") in rec.calls) and app.cfg["counters"]["runs"] == 2
# a script that is not in any key map is refused when the pad asks for it
ok, msg = app.hostact.run("script", "demo2"); assert not ok and "not part of your key configuration" in msg

def run_bg(name):
    """Run a script the way the pad does (worker thread) while the UI keeps pumping, like the real app."""
    box = {}

    def go():
        try:
            box["r"] = app.run_script(name)
        except RuntimeError as e:
            box["e"] = e
    th = threading.Thread(target=go, daemon=True)
    th.start()
    assert pump(150, lambda: not th.is_alive()), "script did not finish"
    if "e" in box:
        raise box["e"]
    return box["r"]


# ---- conditions on the clipboard, variables, sub-scripts, errors
app.cfg["scripts"]["inner"] = "text inner {who}"
app.cfg["scripts"]["outer"] = "set who = {clipboard}\nrun inner\nif not clipboard \"zzz\"\ntext done\nend"
app.clipboard_clear(); app.clipboard_append("Kim"); app.update()
rec.calls.clear()
assert run_bg("outer") == "script 'outer' ran 4 commands"
assert rec.calls == [("text", "inner Kim"), ("text", "done")], rec.calls
for bad, needle in (("nothing", "no script called"), ):
    try:
        run_bg(bad); raise SystemExit("ran a missing script")
    except RuntimeError as e:
        assert needle in str(e)
app.cfg["scripts"]["sh"] = "shell echo hi"
try:
    run_bg("sh"); raise SystemExit("shell ran while switched off")
except RuntimeError as e:
    assert "switched off" in str(e)
popen = []
app.hostact.popen = lambda *a, **k: popen.append((a, k))
app.cfg["allow_shell"] = True
run_bg("sh"); assert popen and popen[0][1].get("shell") is True
app.cfg["allow_shell"] = False

# ---- one script at a time, and Stop
app.cfg["scripts"]["slow"] = "text start\nwait 8000\ntext never"
rec.calls.clear()
res = {}
th = threading.Thread(target=lambda: res.setdefault("r", app.run_script("slow")), daemon=True)
t0 = time.time(); th.start()
assert pump(60, lambda: ("text", "start") in rec.calls)
try:
    app.run_script("outer"); raise SystemExit("two scripts ran at once")
except RuntimeError as e:
    assert "still running" in str(e)
page.stop()
th.join(5)
assert not th.is_alive() and time.time() - t0 < 6 and ("text", "never") not in rec.calls, "Stop ends a long wait at once"
assert not app._script_lock.locked()

# ---- delete, config sanity, scheduler can run scripts
page.pick.set("inner"); page.delete(); assert "inner" not in app.cfg["scripts"]
app.cfg["scripts"]["x" * 40] = "key a"; app.cfg["scripts"][""] = "key b"; app.save_cfg()
cfg2 = m.load_config()
assert all(len(k) <= 32 and k for k in cfg2["scripts"]) and "" not in cfg2["scripts"]
from desk_lib import scheduler as sch   # noqa: E402
e = sch.validate({"when": {"kind": "every", "minutes": 5}, "do": {"kind": "host", "op": "script", "arg": "demo"}})
assert e["do"] == {"kind": "host", "op": "script", "arg": "demo"}
assert m.describe_spec(("host", {"op": "script", "arg": "demo"})) == "run script: demo"
app.destroy()
print("ALL SCRIPTS TESTS PASSED")
