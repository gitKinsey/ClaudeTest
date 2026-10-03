"""Per-program profiles: the pad's layer follows the focused program.   xvfb-run -a python3 tools/profiles_test.py"""
import os
import sys
import tempfile
import time

tmp = tempfile.mkdtemp(prefix="dcprof_")
os.environ["HOME"] = tmp
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m   # noqa: E402

app = m.App()
app.update()


def pump(n=20, cond=None):
    for _ in range(n * (3 if cond else 1)):                       # generous: CI machines can be slow
        app.update()
        time.sleep(0.05)
        if cond and cond():
            return True
    return cond is None


class FakeWin:
    cur = ("", "")

    def get(self):
        return self.cur


fw = FakeWin()
app.active_win, app.profile_poll = fw, 0.1
app.toggle_simulate()
assert pump(80, lambda: app.dev.connected)
sim = app.dev.ser.sim
app.tabs.set("Profiles")

# rules via the UI methods
app.profile_add("VS Code", "code", "process", 2)
app.profile_add("Meetings", "zoom meeting", "title", 1)
app.profile_add("Old", "chrome", "process", 2)
app._profile_edit(2, enabled=False)
app.profile_add("nothing", "", "either", 0)                                     # empty match is refused
assert len(app.cfg["profiles"]) == 3 and "Enter the program" in app.status.cget("text")
assert len(app.prof_list.winfo_children()) == 3
app._profile_move(1, -1); assert app.cfg["profiles"][0]["name"] == "Meetings"; app._profile_move(0, 1)
app.prof_on.set(True); app._profiles_toggled()
app._profile_default_changed("Layer 1")
assert app.cfg["profile_default"] == 0

def expect_layer(win, layer, why):
    fw.cur = win
    assert pump(80, lambda: sim.layer == layer), f"{why}: pad is on layer {sim.layer + 1}, wanted {layer + 1} ({app._profile_state})"

expect_layer(("code", "main.py - Visual Studio Code"), 2, "VS Code rule")
assert pump(40, lambda: "layer 3" in app.prof_live.cget("text")), app.prof_live.cget("text")
expect_layer(("firefox", "Zoom Meeting - Firefox"), 1, "title rule")
expect_layer(("chrome", "Docs"), 0, "disabled rule is ignored -> default layer 1")
expect_layer(("code", "x"), 2, "back to VS Code")
fw.cur = ("python3", "Desk Companion")                                          # focus on the app itself: no change
time.sleep(0.5); pump(10)
assert sim.layer == 2, "focusing this app must not switch layers"
app._profile_default_changed("keep the current layer")
expect_layer(("notepad", "untitled"), 2, "no rule + keep = unchanged")
fw.cur = ("", "")
assert pump(40, lambda: "cannot read" in app.prof_live.cget("text"))
# capture helper
fw.cur = ("slack", "general - Slack"); app.profile_capture()
assert pump(100, lambda: app.pr_match.get() == "slack"), app.pr_match.get()
assert app.pr_kind.get() == "process"
# switching profiles off stops the loop from acting
app.prof_on.set(False); app._profiles_toggled(); app._profile_state["win"] = None
fw.cur = ("zoom", "Zoom Meeting"); time.sleep(0.6); pump(10)
assert sim.layer == 2, "profiles are off"
app._profile_remove(0); assert len(app.cfg["profiles"]) == 2
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
app.preset_var.set("Video meeting: Zoom"); app.preset_layer.set("Layer 3"); app.preset_rule.set(True)
app.apply_preset()
lm = app.cfg["layers"][2]
assert lm["1"] == {"cat": "Productivity & Dev", "action": "Toggle Zoom Mute"} and lm["5"]["action"] == "Mute" and len(app.cfg["profiles"]) == n_rules + 1
assert app.cfg["profiles"][-1] == {"name": "Zoom", "match": "zoom", "kind": "process", "layer": 2, "enabled": True}
app.apply_preset(); assert len(app.cfg["profiles"]) == n_rules + 1, "applying twice does not duplicate the rule"
assert "applied to layer 3" in app.status.cget("text")
other = app.cfg["layers"][0]["1"]
app.preset_var.set("Coding: VS Code"); app.preset_layer.set("Layer 2"); app.preset_rule.set(False); app.apply_preset()
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
app.destroy()
print("ALL PROFILE TESTS PASSED")
