"""Engine test of macro scripts: saving / checking / dry run, running from a pad key, conditions, sub-scripts, Stop, history, templates, import."""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit    # noqa: E402
from core.engine import NullFrontend    # noqa: E402

fe = NullFrontend()
k = Kit("dcscr_", frontend=fe, simulate=False)
import core.base as m   # noqa: E402
app, pump = k.e, k.pump
status = lambda: k.status    # noqa: E731


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
assert app.cfg["scripts"] == {}

# ---- editor: invalid scripts are refused with the line, valid ones saved
assert app.script_save("demo", "repeat 3\nkey ctrl+c\n") is None
assert app.cfg["scripts"] == {} and "never closed" in status() and "line 1" in status(), status()
ok, msg = app.script_check("repeat 3\nkey ctrl+c\n"); assert not ok and "Problem" in msg
SRC = 'repeat 3\nkey ctrl+c\nwait 10\nend\nif clipboard "go"\nopen https://example.org/{counter:runs}\nend\n'
ok, msg = app.script_check(SRC); assert ok and "OK - 3 commands" in msg, msg
assert app.script_save("demo", SRC) == "demo"
assert list(app.cfg["scripts"]) == ["demo"]
assert app.script_save("bad/name", SRC) is None
assert list(app.cfg["scripts"]) == ["demo"] and "Give the script a name" in status()
out = "\n".join(app.script_dry(SRC, "whatever"))
assert "Dry run - nothing was sent" in out and out.count("key CTRL+c") == 3 and "(clipboard)" not in out and not rec.calls, out

# ---- assigned to a key: the pad asks, the app runs it (whitelisted because it is in the key map)
app.script_assign("demo")
assert app.cfg["custom"]["Script: demo"]["val"] == {"op": "script", "arg": "demo"} and ("script", "demo") in app._allowed_host()
slot = app.target_slot
app.upload_slots([slot])
assert pump(60, lambda: sim.slots.get(slot, {}).get("type") == "host")
fe.clip = "let's go"
rec.calls.clear()
assert app.dev.request({"cmd": "input", "k": slot})["ok"]
assert pump(100, lambda: ("OPEN", "https://example.org/1") in rec.calls), rec.calls
combos = [c for c in rec.calls if c[0] == "combo"]
assert len(combos) == 3 and combos[0] == ("combo", ["CTRL", "c"]), rec.calls
# the counter persisted; running again increments it
fe.clip = "go again"
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
fe.clip = "Kim"
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
app.script_stop_request()
th.join(5)
assert not th.is_alive() and time.time() - t0 < 6 and ("text", "never") not in rec.calls, "Stop ends a long wait at once"
assert not app._script_lock.locked()

# ---- delete, config sanity, scheduler can run scripts
app.script_delete("inner"); assert "inner" not in app.cfg["scripts"]
app.cfg["scripts"]["x" * 40] = "key a"; app.cfg["scripts"][""] = "key b"; app.save_cfg()
cfg2 = m.load_config()
assert all(len(k) <= 32 and k for k in cfg2["scripts"]) and "" not in cfg2["scripts"]
from desk_lib import scheduler as sch   # noqa: E402
e = sch.validate({"when": {"kind": "every", "minutes": 5}, "do": {"kind": "host", "op": "script", "arg": "demo"}})
assert e["do"] == {"kind": "host", "op": "script", "arg": "demo"}
assert m.describe_spec(("host", {"op": "script", "arg": "demo"})) == "run script: demo"
# ---- version 2: exec / card / alert / ask / translate / pointer commands through the real app backend
rec.moves = []
rec.moveto = lambda x, y: rec.moves.append(("to", x, y))
rec.clickat = lambda x, y, b: rec.moves.append(("click", x, y, b))
app.cfg["allow_shell"] = True
app.cfg["scripts"]["build"] = ("exec echo hello && exit 0\nif var exit == 0\ncard BUILD | passed | {out} |\nalert 00ff00 2\nelse\ncard BUILD | FAILED |  |\nend\n"
                               "exec exit 3\nif var exit != 0\ncard BUILD | red | {exit} |\nend")
assert run_bg("build") == "script 'build' ran 5 commands"
assert app.cfg["info"]["custom"] and app.cfg["info"]["c_label"] == "BUILD" and app.cfg["info"]["c_t"] == "red" and app.cfg["info"]["c_a"] == "3"
assert pump(40, lambda: sim.led.get("alert") == "00ff00"), sim.led
app.cfg["allow_shell"] = False
try:
    run_bg("build"); raise SystemExit("exec ran with the shell switched off")
except RuntimeError as e:
    assert "shell commands" in str(e)
urls = []
def fetch(method, url, body=None, headers=None):
    urls.append(url)
    return (200, '{"content": [{"type": "text", "text": "the answer"}]}') if "anthropic" in url else (200, '{"responseData": {"translatedText": "hola"}}') if "mymemory" in url else (200, "ok")
app.hostact.net_fetch = fetch
app.cfg["ai"]["key"] = "sk-x"
fe.clip = "some text"
app.cfg["scripts"]["ai"] = "ask Explain: {clipboard}\ntext {answer}\ntranslate es\ntext {translated}\nhttp GET https://ok.example/ping\nmoveto 10 20\nclickat 30 40 right"
rec.calls.clear()
assert run_bg("ai").endswith("ran 7 commands")
assert [c for c in rec.calls if c[0] == "text"] == [("text", "the answer"), ("text", "hola")] and rec.moves == [("to", 10, 20), ("click", 30, 40, "right")], rec.moves
# ---- versions, templates, import from an address
app.script_save("demo", "key a\n")
app.script_save("demo", "key b\n")
hist = app.cfg["script_history"]["demo"]
assert len(hist) == 2 and hist[0]["src"] == "key a\n" and "repeat 3" in hist[1]["src"]
src = app.script_version("demo", hist[1]["t"])
assert "repeat 3" in src and app.cfg["scripts"]["demo"] == "key b\n", "restoring only loads it into the editor"
for i in range(12):
    app.script_save("demo", f"key {chr(97 + i)}\n")
assert len(app.cfg["script_history"]["demo"]) == 10, "the history keeps 10 versions"
from desk_lib import scripting as scr   # noqa: E402
for key, src in scr.TEMPLATES.items():
    nm = "".join(ch for ch in key.split(":")[0] if ch.isalnum() or ch in " _-.").strip()[:30]
    assert nm and app.script_save(nm, src) == nm and nm in app.cfg["scripts"], key
import threading   # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer   # noqa: E402
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = b"key ctrl+a\ntext shared script" if self.path.startswith("/good") else b"this is not a script at all"
        self.send_response(200); self.end_headers(); self.wfile.write(body)
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
got = []
app.script_import_url(f"http://127.0.0.1:{srv.server_port}/good/shared-macro.txt", lambda text, leaf: got.append((text, leaf)))
assert pump(60, lambda: got) and got[0][1] == "shared-macro" and "shared script" in got[0][0] and "READ IT" in status()
assert "shared-macro" not in app.cfg["scripts"], "an imported script is only loaded into the editor until the user saves it"
app.script_import_url(f"http://127.0.0.1:{srv.server_port}/bad.txt", lambda *a: got.append(a))
assert pump(60, lambda: "Import failed" in status()), status()
srv.shutdown()
k.close()
print("ALL ENGINE SCRIPTS TESTS PASSED")
