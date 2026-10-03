import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit    # noqa: E402

k = Kit("dcprof_", simulate=False)
import core.base as m    # noqa: E402
app, pump = k.e, k.pump


class FakeWin:
    cur = ("", "")

    def get(self):
        return self.cur


fw = FakeWin()
app.active_win, app.profile_poll = fw, 0.1
app.toggle_simulate()
assert pump(80, lambda: app.dev.connected)
sim = app.dev.ser.sim

# rules via the UI methods
app.profile_add("VS Code", "code", "process", 2)
app.profile_add("Meetings", "zoom meeting", "title", 1)
app.profile_add("Old", "chrome", "process", 2)
app.profile_edit(2, enabled=False)
app.profile_add("nothing", "", "either", 0)                                     # empty match is refused
assert len(app.cfg["profiles"]) == 3 and "Enter the program" in k.status
app.profile_move(1, -1); assert app.cfg["profiles"][0]["name"] == "Meetings"; app.profile_move(0, 1)
app.profiles_set(True)
app.profile_default_set("Layer 1")
assert app.cfg["profile_default"] == 0

def expect_layer(win, layer, why):
    fw.cur = win
    assert pump(80, lambda: sim.layer == layer), f"{why}: pad is on layer {sim.layer + 1}, wanted {layer + 1} ({app._profile_state})"

expect_layer(("code", "main.py - Visual Studio Code"), 2, "VS Code rule")
assert pump(40, lambda: "layer 3" in app._profile_state["text"]), app._profile_state["text"]
expect_layer(("firefox", "Zoom Meeting - Firefox"), 1, "title rule")
expect_layer(("chrome", "Docs"), 0, "disabled rule is ignored -> default layer 1")
expect_layer(("code", "x"), 2, "back to VS Code")
fw.cur = ("python3", "Desk Companion")                                          # focus on the app itself: no change
time.sleep(0.5); pump(10)
assert sim.layer == 2, "focusing this app must not switch layers"
app.profile_default_set("keep the current layer")
expect_layer(("notepad", "untitled"), 2, "no rule + keep = unchanged")
fw.cur = ("", "")
assert pump(40, lambda: "cannot read" in app._profile_state["text"])
# capture helper
got = []
app.on("profile_captured", lambda proc, title: got.append((proc, title)))
fw.cur = ("slack", "general - Slack"); app.profile_capture()
assert pump(100, lambda: got == [("slack", "general - Slack")]), got
# switching profiles off stops the loop from acting
app.profiles_set(False); app._profile_state["win"] = None
fw.cur = ("zoom", "Zoom Meeting"); time.sleep(0.6); pump(10)
assert sim.layer == 2, "profiles are off"
app.profile_remove(0); assert len(app.cfg["profiles"]) == 2
# ---- ready-made key layouts
from desk_lib import presets   # noqa: E402
assert len(presets.PRESETS) >= 10
for pname, pr in presets.PRESETS.items():                        # every preset only uses actions that exist in the library, for all 7 slots
    assert sorted(pr["keys"]) == [1, 2, 3, 4, 5, 6, 7], pname
    for slot, (cat, act) in pr["keys"].items():
        spec = m.resolve_spec({"os": "win", "custom": {}}, cat, act)
        assert spec and spec[0] != "none", (pname, cat, act)
        assert m.resolve_spec({"os": "mac", "custom": {}}, cat, act), (pname, "mac", act)
    assert pr["profile"][2] in ("process", "title") and pr["profile"][1], pname
app.cfg["profiles"].clear()
n_rules = len(app.cfg["profiles"])
app.apply_preset("Video meeting: Zoom", 2, True)
lm = app.cfg["layers"][2]
assert lm["1"] == {"cat": "Productivity & Dev", "action": "Toggle Zoom Mute"} and lm["5"]["action"] == "Mute" and len(app.cfg["profiles"]) == n_rules + 1
assert app.cfg["profiles"][-1] == {"name": "Zoom", "match": "zoom", "kind": "process", "layer": 2, "enabled": True}
app.apply_preset("Video meeting: Zoom", 2, True); assert len(app.cfg["profiles"]) == n_rules + 1, "applying twice does not duplicate the rule"
assert "applied to layer 3" in k.status
other = app.cfg["layers"][0]["1"]
app.apply_preset("Coding: VS Code", 1, False)
assert app.cfg["layers"][1]["3"]["action"] == "VS Code Terminal" and app.cfg["layers"][0]["1"] == other and len(app.cfg["profiles"]) == n_rules + 1
# the preset reaches the simulated pad through the normal upload (mac shortcuts resolve differently from Windows)
assert app.dev.connected
sim = app.dev.ser.sim
app.upload_slots([1, 2, 3], layer=1)
assert pump(80, lambda: sim.layers[1].get(1) is not None and sim.layers[1].get(3) is not None)
assert sim.layers[1][1] == {"type": "combo", "val": ["PRIMARY", "p"]} and sim.layers[1][3] == {"type": "combo", "val": ["CTRL", "`"]}, sim.layers[1]
# preset errors are reported, not raised
try:
    presets.apply(app.cfg, "nope", 0)
    raise SystemExit("unknown preset accepted")
except KeyError:
    pass
try:
    presets.apply(app.cfg, "Browsing", 0, action_exists=lambda c, a: False)
    raise SystemExit("missing library action accepted")
except ValueError:
    pass
assert any(c[0] == "Spotify" for c in presets.profile_choices()) and any(c[0] == "Zoom" for c in presets.profile_choices())
# ---- time windows, game rules, remembered layers
from datetime import datetime   # noqa: E402
from desk_lib import activewin as aw   # noqa: E402
mon10, sat10, mon23 = datetime(2026, 10, 5, 10, 0), datetime(2026, 10, 10, 10, 0), datetime(2026, 10, 5, 23, 30)
rules = [{"match": "slack", "kind": "process", "layer": 1, "time": "09:00-17:00", "days": "mon-fri"}, {"match": "slack", "kind": "process", "layer": 2},
         {"match": "steam", "kind": "process", "layer": 0, "game": True, "enabled": True}, {"match": "vlc", "kind": "process", "layer": 1, "time": "22:00-06:00"}]
assert aw.pick_layer(rules, "slack", "", now=mon10) == 1 and aw.pick_layer(rules, "slack", "", now=sat10) == 2 and aw.pick_layer(rules, "slack", "", now=mon23) == 2
assert aw.pick_layer(rules, "vlc", "", default=-1, now=mon23) == 1 and aw.pick_layer(rules, "vlc", "", default=-1, now=mon10) == -1, "a rule that wraps midnight"
assert aw.pick_rule(rules, "steam", "", mon10)["game"] is True and aw.pick_rule(rules, "nothing", "", mon10) is None
for t, d in (("9am", ""), ("09:00", ""), ("25:00-26:00", ""), ("", "someday"), ("", "mon-xyz")):
    try:
        aw.validate_window(t, d); raise SystemExit(f"accepted window {t!r} {d!r}")
    except ValueError:
        pass
aw.validate_window("", ""); aw.validate_window("09:00-17:00", "mon,wed-fri")
app.cfg["profiles"].clear()
app.profile_add("", "slack", "either", 2, "09:00-17:00", "mon-fri", True)
assert app.cfg["profiles"][-1] == {"name": "slack", "match": "slack", "kind": "either", "layer": 2, "enabled": True, "time": "09:00-17:00", "days": "mon-fri", "game": True}
app.profile_add("", "x", "either", 2, "nine", "", False)
assert len(app.cfg["profiles"]) == 1 and "Rule not added" in k.status
app.cfg["profiles"].clear(); app.cfg["profiles_on"] = True
# game mode: set by the profile loop, silences pad host actions and scheduled actions
app.cfg["profiles"].append({"name": "g", "match": "steam", "kind": "process", "layer": 0, "enabled": True, "game": True})
fw.cur = ("steam", "Some Game"); app._profile_state.update(win=None)
assert pump(80, lambda: app.game_mode is True) and "game mode" in app._profile_state["text"]
sim.host_log.clear()
app._on_pad_event({"evt": "host", "op": "notify", "arg": "x"}); app.pump()
assert app.host_q.empty(), "game mode: the pad's host actions are ignored"
n_cmds = sim.cmd_count.get("layer", 0)
app.run_schedule({"id": "x", "name": "t", "do": {"kind": "layer", "n": 1}, "when": {"kind": "every", "minutes": 1}}); time.sleep(0.3); app.pump()
assert sim.cmd_count.get("layer", 0) == n_cmds, "scheduled actions pause too (a manual 'Run now' still works)"
fw.cur = ("notepad", "Untitled"); app._profile_state.update(win=None)
assert pump(80, lambda: app.game_mode is False)
# remembered layers
app.cfg["profiles"].clear(); app.cfg["remember_layers"] = True
fw.cur = ("krita", "Painting"); app._profile_state.update(win=None, layer=None)
assert pump(60, lambda: app._profile_state["win"] == ("krita", "Painting"))
app._pad_layer_changed(2)                                           # the user switches the layer by hand while Krita is in front
assert app._app_layers == {"krita": 2}
fw.cur = ("notepad", "Untitled"); app._profile_state.update(win=None)
assert pump(60, lambda: app._profile_state["win"] == ("notepad", "Untitled"))
fw.cur = ("krita", "Painting"); app._profile_state.update(win=None)
assert pump(80, lambda: "layer 3" in app._profile_state["text"]), app._profile_state["text"]
app.cfg["remember_layers"] = False; app.cfg["profiles_on"] = False

# ---- lock / fullscreen dim and resume re-sync
from desk_lib import screenstate   # noqa: E402
class FakeScreen:
    lock, fs = False, False
    def locked(self): return self.lock
    def fullscreen(self): return self.fs
fsx = FakeScreen(); app.screen = fsx
app.dev.info["caps"] = list(app.dev.info["caps"]) + [c for c in ("dimcmd",) if c not in app.dev.info["caps"]]
app.cfg.update(dim_lock=True, dim_fullscreen=False, dim_level=20)
app._screen_step(); assert sim.dim == 0
fsx.lock = True; app._screen_step(); assert sim.dim == 20, sim.dim
app._screen_step(); n = sim.cmd_count["dim"]; app._screen_step(); assert sim.cmd_count["dim"] == n, "an unchanged state is not re-sent"
fsx.lock = False; fsx.fs = True; app._screen_step(); assert sim.dim == 0, "fullscreen dimming is off"
app.cfg["dim_fullscreen"] = True; app._screen_step(); assert sim.dim == 20
fsx.fs = False; app._screen_step(); assert sim.dim == 0
app.dev.info["caps"] = [c for c in app.dev.info["caps"] if c != "dimcmd"]        # a pad without the dim command: the brightness is changed and restored
app.pad.brightness = 150; fsx.lock = True; app._screen_step(); assert sim.bright == 20
fsx.lock = False; app._screen_step(); assert sim.bright == 150
t0 = sim.cmd_count.get("time", 0); app._dim["tick"] -= 100; app._screen_step()
assert sim.cmd_count.get("time", 0) == t0 + 1, "a long gap between two checks = the PC slept: the pad gets the clock again"
ss = screenstate.ScreenState
def fake_run(outs):
    return lambda args, timeout=3: outs.get(args[0] if args[0] not in ("loginctl", "xprop") else args[0])
assert ss(fake_run({"loginctl": "LockedHint=yes\n"}), "Linux", {"XDG_SESSION_ID": "2"}).locked() is True
assert ss(fake_run({"loginctl": "LockedHint=no\n"}), "Linux", {"XDG_SESSION_ID": "2"}).locked() is False
assert ss(fake_run({"loginctl": "x", "gnome-screensaver-command": "The screensaver is active\n"}), "Linux", {}).locked() is True
assert ss(fake_run({}), "Linux", {}).locked() is None
assert ss(fake_run({"ioreg": '"CGSSessionScreenIsLocked" = Yes'}), "Darwin").locked() is True and ss(fake_run({"ioreg": "nothing"}), "Darwin").locked() is False
assert ss(lambda a, timeout=3: "0x4a00007\n" if a[0] == "xdotool" else "_NET_WM_STATE(ATOM) = _NET_WM_STATE_FULLSCREEN\n", "Linux", {}).fullscreen() is True
assert ss(lambda a, timeout=3: "0x4a00007\n" if a[0] == "xdotool" else "_NET_WM_STATE(ATOM) =\n", "Linux", {}).fullscreen() is False and ss(fake_run({}), "Linux", {}).fullscreen() is None
assert ss(fake_run({"osascript": "true\n"}), "Darwin").fullscreen() is True
assert ss(system="Windows", win_locked=lambda: True, win_fullscreen=lambda: False).locked() is True and ss(system="Windows", win_locked=lambda: True, win_fullscreen=lambda: False).fullscreen() is False
assert ss(system="Plan9").locked() is None
k.close()
print("ALL ENGINE PROFILE TESTS PASSED")
