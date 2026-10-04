"""Runs every entry of the command palette against the simulated pad and checks its visible effect or at least that nothing raises."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import QtKit    # noqa: E402

from ui_qt import dialogs    # noqa: E402

k = QtKit("dcpal_", cfg={"advanced": True})
e, sh, sim = k.e, k.shell, k.sim
k.fe.answer = False
cmds = dict(dialogs.palette_commands(sh))
assert len(cmds) >= 40, len(cmds)
expect = {}


def close_dialogs():
    for w in k.app.topLevelWidgets():
        if isinstance(w, (dialogs.SetupWizard, dialogs.HardwareTest, dialogs.AutoBackupDialog, dialogs.MiniPad, dialogs.CommandPalette)) and w.isVisible():
            w.close()


# the pages
for name, fn in cmds.items():
    if name.startswith("Go to "):
        fn(); k.spin(4)
        assert sh.current == next(p for p in sh.page_order if sh.pages[p].title in name.split("  -  ")[0][6:]), (name, sh.current)

# layers and screens on the pad
cmds["Pad: switch to layer 3"](); assert k.until(lambda: sim.layer == 2, 10), sim.layer
cmds["Pad: switch to layer 1"](); assert k.until(lambda: sim.layer == 0, 10)
scr = [n for n in cmds if n.startswith("Pad: show screen")]
assert len(scr) == 20, len(scr)
cmds[scr[8]](); assert k.until(lambda: sim.mode == 9, 10), (sim.mode, scr[8])
cmds[scr[0]](); assert k.until(lambda: sim.mode == 1, 10)

# actions with a visible effect
cmds["Toggle light / dark theme"](); k.spin(5)
dark = sh.engine.cfg["appearance"]
cmds["Toggle light / dark theme"](); k.spin(5)
assert sh.engine.cfg["appearance"] != dark
adv = e.cfg["advanced"]
cmds["Toggle Advanced mode"](); k.spin(5); assert e.cfg["advanced"] is (not adv)
cmds["Toggle Advanced mode"](); k.spin(5); assert e.cfg["advanced"] is adv
e.cfg["map"]["1"] = {"cat": "Media", "action": "Mute"}; e.mapping_changed([1])
cmds["Undo the last key assignment  (Ctrl+Z)"](); k.spin(3)
assert e.cfg["map"]["1"]["action"] != "Mute"
cmds["Redo  (Ctrl+Y)"](); k.spin(3)
assert e.cfg["map"]["1"]["action"] == "Mute"
e.edit_undo()
cmds["Upload everything to the pad"]()
assert k.until(lambda: "Uploaded to the pad" in e.status[0], 30), e.status
cmds["Verify the keys stored on the pad"]()
assert k.until(lambda: "Key check OK" in e.status[0], 15), e.status
cmds["Latency test"]()
assert k.until(lambda: "Latency" in e.status[0], 25), e.status
cmds["Check the pad's state (safe mode?)"]()
assert k.until(lambda: sh.pages["padapp"].recovery is not None and e.dev.connected, 5)
cmds["Send info cards now"](); k.spin(10)
cmds["Show the Info screen on the pad"](); assert k.until(lambda: sim.mode == 6, 10), sim.mode
cmds["Rescan serial ports"](); k.spin(3)
cmds["Check for app updates"](); k.spin(5)

# windows
for name, cls in (("Run the guided hardware test", dialogs.HardwareTest), ("Run the setup wizard", dialogs.SetupWizard), ("Open the mini pad", dialogs.MiniPad)):
    cmds[name](); k.spin(6)
    assert any(isinstance(w, cls) and w.isVisible() for w in k.app.topLevelWidgets()), name
    close_dialogs(); k.spin(3)

# file dialogs are refused (cancelled) without errors
for name in ("Back up everything...", "Restore from a backup..."):
    cmds[name](); k.spin(3)

# the simulator toggle disconnects and reconnects
cmds["Connect / disconnect the simulated pad"](); assert k.until(lambda: not e.dev.connected, 10)
cmds["Connect / disconnect the simulated pad"](); assert k.until(lambda: e.dev.connected, 20)
k.check_handlers()
k.close()
print("ALL QT PALETTE TESTS PASSED")
