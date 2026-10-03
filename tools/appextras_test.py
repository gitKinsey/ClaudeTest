"""Tests for the small app modules: updates, plugins, history, tips, i18n, hotkey, diag, appearance config, client."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from desk_lib import diag, hotkey, history, i18n, plugins, recorder, tips, updates   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


# ---- updates
check("parse_version", updates.parse_version("v1.4.0") == (1, 4, 0) and updates.parse_version("1.5") == (1, 5, 0) and updates.parse_version("x") == ())
check("is_newer", updates.is_newer("1.5.0", "1.4.9") and not updates.is_newer("1.4.0", "1.4.0") and not updates.is_newer("", "1.0") and updates.is_newer("v2.0", "1.99.9"))
r = updates.check("1.4.0", fetch=lambda url: (200, json.dumps({"tag_name": "v1.5.0", "html_url": "https://x/y", "body": "notes"})))
check("update available", r["newer"] and r["latest"] == "v1.5.0" and "available" in r["message"], r)
r = updates.check("1.5.0", fetch=lambda url: (200, json.dumps({"tag_name": "1.5.0"})))
check("up to date", not r["newer"] and "up to date" in r["message"], r)
r = updates.check("1.4.0", fetch=lambda url: (404, ""))
check("no releases", not r["newer"] and "no release" in r["message"], r)
for bad, what in ((lambda u: (500, ""), "500"), (lambda u: (200, "<html>"), "unreadable")):
    try:
        updates.check("1.0", fetch=bad)
        check("error " + what, False)
    except ValueError:
        check("error " + what, True)


def boom(url):
    raise OSError("offline")


try:
    updates.check("1.0", fetch=boom)
    check("offline", False)
except ValueError as e:
    check("offline", "could not reach" in str(e))

# ---- plugins
with tempfile.TemporaryDirectory() as d:
    host = plugins.PluginHost(d, api=type("A", (), {"typed": []})())
    check("empty folder", host.load() == [])
    host.install_example()
    (Path(d) / "bad.py").write_text("raise RuntimeError('nope')")
    (Path(d) / "noname.py").write_text("NAME = 'Bad Name'\ndef run(a, api): return 1")
    (Path(d) / "echo.py").write_text("NAME = 'echo'\ndef run(arg, api): return 'got ' + arg")
    (Path(d) / "zdup.py").write_text("NAME = 'echo'\ndef run(arg, api): return 'dup'")
    typed = []
    host.api = type("A", (), {"type_text": staticmethod(typed.append)})()
    names = host.load()
    check("loads good plugins", names == ["echo", "hello"], names)
    check("bad plugins reported", set(host.errors) == {"bad.py", "noname.py", "zdup.py"} and "nope" in host.errors["bad.py"], host.errors)
    check("run with argument", host.run("echo:abc") == "got abc")
    msg = host.run("hello:short")
    check("example plugin types", typed and len(typed[0]) == 5 and msg.startswith("typed"), (typed, msg))
    try:
        host.run("ghost:1")
        check("unknown plugin refused", False)
    except ValueError as e:
        check("unknown plugin refused", "not loaded" in str(e))

# ---- history
h = history.EditHistory(limit=3)
h.record({"a": 1})
check("nothing to undo yet", not h.can_undo() and h.undo() is None)
h.record({"a": 2})
h.record({"a": 2})                                                    # duplicate ignored
h.record({"a": 3})
check("undo", h.undo() == {"a": 2} and h.can_redo())
check("redo", h.redo() == {"a": 3} and not h.can_redo())
h.undo()
h.record({"a": 9})
check("new change clears redo", not h.can_redo())
for i in range(10):
    h.record({"a": 100 + i})
n = 0
while h.undo() is not None:
    n += 1
check("history is bounded", n == 2, n)

# ---- tips
cfg = {"gestures": {}, "scripts": {}, "schedules": [], "profiles_on": False, "api": {"on": False}, "autobackup": {"on": False}, "tray": False}
t = tips.pick(cfg, caps={"gestures", "screens"})
check("a tip is suggested", t is not None and t[0] == "gestures", t)
check("dismissed tips are skipped", tips.pick(cfg, {"gestures"}, dismissed={"gestures"})[0] == "scripts")
check("capability-gated tips hidden", all(i != "gestures" for i, _ in [tips.pick(cfg, set(), seed=k) for k in range(5)]))
full = {"gestures": {"x": 1}, "scripts": {"a": "key a"}, "schedules": [1], "profiles_on": True, "api": {"on": True}, "autobackup": {"on": True},
        "tray": True, "dim_lock": True, "screens_seen": True, "palette_used": True, "cliphist_on": True}
check("no tip when everything is used", tips.pick(full, {"gestures", "screens"}) is None)
check("seed rotates", tips.pick(cfg, {"gestures"}, seed=0) != tips.pick(cfg, {"gestures"}, seed=1))

# ---- i18n
i18n.set_language("de")
check("german", i18n.tr("Save") == "Speichern" and i18n.tr("unknown text") == "unknown text" and i18n.tr("") == "")
i18n.set_language("fr")
check("french", i18n.tr("Device") == "Appareil")
i18n.set_language("xx")
check("unknown language -> english", i18n.language() == "en" and i18n.tr("Save") == "Save")
check("all languages cover the same keys", all(set(i18n.T[k]) == set(i18n.T["de"]) for k in i18n.T))

# ---- hotkey
check("normalize", hotkey.normalize("Ctrl+Alt+K") == "<ctrl>+<alt>+k" and hotkey.normalize("win + F5") == "<cmd>+<f5>" and hotkey.normalize("ctrl+space") == "<ctrl>+<space>")
for bad in ("k", "ctrl", "ctrl+", "foo+k", "ctrl+ab"):
    try:
        hotkey.normalize(bad)
        check("reject " + bad, False)
    except ValueError:
        check("reject " + bad, True)
got = []


class FakeHK:
    def __init__(self, mapping):
        self.mapping, self.started = mapping, False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


made = []
g = hotkey.GlobalHotkey("ctrl+alt+p", lambda: got.append(1), factory=lambda m: made.append(FakeHK(m)) or made[-1])
g.start()
made[0].mapping["<ctrl>+<alt>+p"]()
check("hotkey fires callback", got == [1] and made[0].started)
g.stop()
check("hotkey stop", not made[0].started)

# ---- diag
check("latency", "n=4" in diag.latency_summary([1, 2, 3, 4]) and diag.latency_summary([]) == "no samples")
check("power", diag.power_estimate(255, "auto") > diag.power_estimate(10, "auto") > diag.power_estimate(10, "off") and diag.power_estimate(200, wifi=True) > diag.power_estimate(200))
check("power text", "mA" in diag.power_text(120))

# ---- mouse recording (relative moves, clicks, wheel) mixed with typing
r = recorder.MacroRecorder()
for i, (x, y) in enumerate([(100, 100), (150, 120), (260, 120), (260, 130)]):
    r.mouse_move(x, y, i * 0.01)
r.mouse_button("Button.left", True, 0.1); r.mouse_button("Button.left", False, 0.15)
r.key_down("h", 0.2); r.key_down("i", 0.25)
r.mouse_move(255, 130, 0.3)
r.mouse_scroll(-3, 0.35)
r.mouse_button("Button.right", True, 0.4); r.mouse_button("Button.right", False, 1.2)
steps = r.finish()
check("mouse moves are relative and split at 100 per axis", steps[:2] == [{"mouse": {"move": [100, 30]}}, {"mouse": {"move": [60, 0]}}], steps[:3])
check("click, typing, small move, wheel in order", steps[2:6] == [{"mouse": {"btn": "left", "act": "click"}}, {"text": "hi"}, {"mouse": {"move": [-5, 0]}}, {"mouse": {"wheel": -3}}], steps[2:7])
check("a long press becomes down / wait / up", steps[6:] == [{"mouse": {"btn": "right", "act": "down"}}, {"delay": 800}, {"mouse": {"btn": "right", "act": "up"}}], steps[6:])
r = recorder.MacroRecorder(); r.mouse_button("Button.unknown", True, 0); r.mouse_button("Button.unknown", False, 0.1); r.mouse_scroll(0, 0.2)
check("unknown buttons and zero scrolls are ignored", r.finish() == [])
r = recorder.MacroRecorder()
for i in range(200):
    r.mouse_move(i * 3, 0, i * 0.01)
    r.mouse_button("Button.left", True, i * 0.01); r.mouse_button("Button.left", False, i * 0.01 + 0.005)
check("recording is capped at 64 steps", len(r.finish()) == 64 and r.truncated)
import companion_app as _m   # noqa: E402
check("recorded mouse steps are valid pad macros", all(_m.spec_ok({"type": "macro", "val": [st]}) for st in steps), steps)

# ---- diagnostic export: secrets never leave the app
cfg = {"ai": {"key": "sk-ant-secret", "model": "m"}, "api": {"on": True, "port": 8765, "token": "tok123"}, "online_keys": {"tenor": "TENORKEY", "giphy": ""}, "info": {"token": "badge", "city": "Zurich"},
       "scripts": {"a": "x" * 5000}, "usage": {"K1": 5}, "wifi_pass": "pw", "layers": [{"1": {"cat": "Editing", "action": "Copy"}}], "mine": [{"password": "p", "n": 1}]}
red = diag.redact(cfg)
check("secrets removed", red["ai"]["key"] == "***" and red["ai"]["model"] == "m" and red["api"]["token"] == "***" and red["api"]["port"] == 8765 and red["api"]["on"] is True
      and red["online_keys"]["tenor"] == "***" and red["online_keys"]["giphy"] == "" and red["info"]["token"] == "***" and red["info"]["city"] == "Zurich" and red["wifi_pass"] == "***"
      and red["mine"][0]["password"] == "***" and red["mine"][0]["n"] == 1, red)
check("ordinary settings and long texts survive", red["layers"] == cfg["layers"] and red["usage"] == {"K1": 5} and red["scripts"]["a"].endswith("...(cut)") and "sk-ant" not in json.dumps(red) and "TENORKEY" not in json.dumps(red))
check("the original is untouched", cfg["ai"]["key"] == "sk-ant-secret")
import io, zipfile   # noqa: E401,E402
z = zipfile.ZipFile(io.BytesIO(diag.build_zip({"report.txt": "hello", "a/b.json": "{}", "..\\evil": "x"})))
check("zip with flat names", sorted(z.namelist()) == [".._evil", "a_b.json", "report.txt"] and z.read("report.txt") == b"hello", z.namelist())

# ---- client against a fake serial port
sys.path.insert(0, str(ROOT))
import deskcompanion_client as dcc   # noqa: E402


class FakeSerial:
    def __init__(self, *a, **k):
        self.out, self.buf = [], b""
        self.inbox = [b'garbage line\n', b'{"evt":"input","k":1}\n']

    def write(self, b):
        msg = json.loads(b)
        self.inbox.append((json.dumps({"ok": msg["cmd"] != "bad", "id": msg["id"], "evt": msg["cmd"], "fw": "1.5.0", "err": "bad_arg"}) + "\n").encode())

    def read(self, n):
        import time
        time.sleep(0.01)
        return self.inbox.pop(0) if self.inbox else b""

    def close(self):
        pass


with dcc.Pad("fake", serial_factory=FakeSerial) as pad:
    check("client hello", pad.hello()["fw"] == "1.5.0")
    try:
        pad.request({"cmd": "bad"})
        check("client error", False)
    except dcc.PadError as e:
        check("client error", "bad_arg" in str(e))
    evs = list(pad.events(seconds=0.4))
    check("client events", any(e.get("evt") == "input" for e in evs), evs)

print("\nFAILED: " + ", ".join(FAILS) if FAILS else "\nall extras tests passed")
sys.exit(1 if FAILS else 0)
