"""Engine test: the firmware 1.6 features (batched uploads, state events, program name, layer names, banners, accent, snapshot scale, protocol level, counters)
against a simulated 1.6 pad, and the same app against a simulated 1.5 pad that has none of them."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit    # noqa: E402

import core.base as m    # noqa: E402

# ======================================================================= firmware 1.6
k = Kit("dcfw16_")
e, sim, pump = k.e, k.sim, k.pump
caps = e.dev.info["caps"]
assert all(c in caps for c in m.NEW16_CAPS), caps
assert e.dev.info["proto"] == m.PROTO_LEVEL == 16 and e.proto_newer() is False

# ---- batched key uploads: 21 key actions in a handful of requests, all stored, nothing pending afterwards
e.upload_all()
assert pump(200, lambda: "Uploaded to the pad" in k.status), k.status
assert sim.cmd_count.get("remap_batch", 0) >= 1 and sim.cmd_count.get("remap", 0) == 0, sim.cmd_count
assert all(len(sim.layers[n]) == 7 for n in range(3)) and "verified" in k.status
assert pump(40, lambda: e.pending_count == 0), e.pending_count
# a huge text snippet and many macros split into several batches (a batch holds 24 items / 4800 characters)
big = [(0, i, ("text", "x" * 900)) for i in range(1, 6)] + [(1, i, ("text", "y" * 900)) for i in range(1, 6)]
before = sim.cmd_count.get("remap_batch", 0)
e._batches_sent = 0
e.bg(lambda: e._send_remaps(big), None, "batch")
assert pump(100, lambda: e._batches_sent >= 2), e._batches_sent
assert sim.cmd_count["remap_batch"] - before == e._batches_sent and sim.layers[0][5]["val"] == "x" * 900 and sim.layers[1][3]["val"] == "y" * 900
# one bad item: nothing at all is stored (all or nothing) and the error is readable
snap = [dict(l) for l in sim.layers]
r = None
try:
    e.dev.request({"cmd": "remap_batch", "items": [{"key": 1, "type": "text", "val": "ok1"}, {"key": 2, "type": "combo", "val": ["NOPE"]}]})
except m.DeviceError as ex:
    r = str(ex)
assert r is not None and [dict(l) for l in sim.layers] == snap, "a batch with a bad item changes nothing"
for bad in ([], [{"key": 99, "type": "text", "val": "a"}], [{"key": 1, "layer": 7, "type": "text", "val": "a"}], [{"key": 1, "gesture": "wiggle", "type": "text", "val": "a"}]):
    try:
        e.dev.request({"cmd": "remap_batch", "items": bad}); raise AssertionError("accepted")
    except m.DeviceError as ex:
        assert str(ex)
assert sim.cmd_count["remap_batch"] >= 3
# gestures and clears in a batch
e.dev.request({"cmd": "remap_batch", "items": [{"key": 1, "gesture": "hold", "type": "text", "val": "held"}, {"key": 9, "type": "text", "val": "pt"}]})
assert sim.gest[(0, 1, "hold")]["val"] == "held" and sim.layers[0][9]["val"] == "pt"
e.dev.request({"cmd": "remap_batch", "items": [{"key": 1, "gesture": "hold", "clear": True}, {"key": 9, "clear": True}]})
assert (0, 1, "hold") not in sim.gest and 9 not in sim.layers[0]
# the 24-item limit
try:
    e.dev.request({"cmd": "remap_batch", "items": [{"key": 1, "type": "text", "val": "a"}] * 25}); raise AssertionError("25 items accepted")
except m.DeviceError:
    pass

# ---- the pad announces its own screen / brightness / layer changes: twin, pending count and layer pill follow
k.listen("pad_state", "pad_layer")
sim.pad_side_change(mode=3, bright=90, layer=2)
assert pump(60, lambda: e.pad.mode == 3 and e.pad.brightness == 90 and e.pad_layer == 2), (e.pad.mode, e.pad.brightness, e.pad_layer)
assert ("pad_state", ("mode",)) in k.events and ("pad_state", ("bright",)) in k.events
assert e.cfg["pushed_mode"] == 3 and e.cfg["pushed_bright"] == 90, "the physical pad is what it says: not 'unsent'"
n_events = getattr(e, "state_events", 0)
sim.pad_side_change(mode=3, bright=90)
assert pump(30, lambda: getattr(e, "state_events", 0) > n_events) and e.pad.mode == 3
sim.pad_side_change(mode=1, bright=200, layer=0)
assert pump(60, lambda: e.pad.mode == 1 and e.pad.brightness == 200 and e.pad_layer == 0)
e._on_state_event({"mode": 999, "bright": -4, "layer": 77})            # nonsense is ignored, never an exception
assert e.pad.mode == 1 and e.pad.brightness == 200

# ---- the program name on the pad
assert e.send_ctx("VS Code") and sim.ctx == "VS Code"
assert e.send_ctx("Café éè a very long program name") and sim.ctx.isascii() and len(sim.ctx) <= 16, sim.ctx
e.cfg["pad_ctx"] = False
n = sim.cmd_count.get("ctx", 0)
assert not e.send_ctx("x") and sim.cmd_count.get("ctx", 0) == n, "switched off: nothing is sent"
e.cfg["pad_ctx"] = True
assert e.send_ctx("") and sim.ctx == ""
# the profile loop sends it with the layer change
e.cfg["profiles_on"] = True
e.cfg["profiles"] = [{"name": "My editor", "match": "myeditor", "kind": "process", "layer": 2, "enabled": True}]
e.active_win.get = lambda: ("myeditor", "main.py")
e.profile_poll = 0.05
assert pump(120, lambda: sim.ctx == "My editor" and sim.layer == 2), (sim.ctx, sim.layer)
e.cfg["profiles_on"] = False

# ---- layer names
assert e.layer_names() == ["", "", ""]
e.layer_names_set(["WORK", "Musíc", "a name that is much too long"])
assert pump(60, lambda: sim.lnames == ["WORK", "Music", "a name tha"]), sim.lnames
assert e.layer_names() == ["WORK", "Music", "a name tha"]
r = e.dev.request({"cmd": "layer_names"})
assert r["names"] == sim.lnames
for bad in (["a", "b"], ["a", "b", "c" * 11], ["a", 1, "c"], ["a", "b", "café"]):
    try:
        e.dev.request({"cmd": "layer_names", "names": bad}); raise AssertionError(bad)
    except m.DeviceError:
        pass
sim.lnames = ["", "", ""]                                               # a replugged pad that lost its names gets them again on connect
e.toggle_simulate(); pump(10)
e.toggle_simulate()
assert pump(100, lambda: e.dev.connected)
sim = e.dev.ser.sim
assert pump(100, lambda: sim.lnames == ["WORK", "Music", "a name tha"]), sim.lnames

# ---- banners on the pad
assert e.pad_toast("Build passed", "ok", 3) and pump(40, lambda: sim.toast_log and sim.toast_log[-1]["text"] == "Build passed")
e.notify("Reminder", "Stand up", "ok", pad=True)
assert pump(40, lambda: sim.toast_log[-1]["text"] == "Stand up")
e.notify("Connected", "no banner for this", "ok")
pump(10)
assert sim.toast_log[-1]["text"] == "Stand up", "ordinary notifications stay on the computer"
e.cfg["pad_toasts"] = False
assert not e.pad_toast("hidden") and sim.toast_log[-1]["text"] == "Stand up"
e.cfg["pad_toasts"] = True
assert e.pad_toast("x" * 40) and pump(40, lambda: len(sim.toast_log[-1]["text"]) == 24)
for bad in ({"text": ""}, {"text": "y" * 25}, {"text": "café"}):
    try:
        e.dev.request({"cmd": "toast", **bad}); raise AssertionError(bad)
    except m.DeviceError:
        pass

# ---- the pad follows the app's accent colour
assert e.accent_index() == 0
e.set_pad_accent(True)
assert pump(60, lambda: sim.settings15.get("accent") == 1), sim.settings15
e.set_pref("accent", "pink")
assert pump(60, lambda: sim.settings15["accent"] == 2) and e.accent_index() == 2
e.set_pref("accent", "violet")
assert pump(60, lambda: sim.settings15["accent"] == 5)
e.set_pad_accent(False)
assert pump(60, lambda: sim.settings15["accent"] == 0)
e.set_pref("accent", "cyan")
try:
    e.dev.request({"cmd": "settings", "accent": 6}); raise AssertionError("accent 6")
except m.DeviceError:
    pass

# ---- smaller frames for the live mirror
img = e.dev.snapshot(scale=2)
assert img.size == (120, 120) and e.dev.snapshot().size == (240, 240)
got = []
e.mirror_busy_snapshot(got.append, lambda: got.append(None))
assert pump(100, lambda: got) and got[0] is not None and got[0].size == (120, 120)

# ---- protocol level and counters
e.dev.info["proto"] = 99
assert e.proto_newer() and e.fw_status()[0] == "newer" and "protocol" in e.fw_status()[1]
e.dev.info["proto"] = 16
assert e.fw_status()[0] == "ok"
perf = []
e.on("perf", lambda text, slow: perf.append((text, slow)))
e.perf_refresh()
assert pump(60, lambda: perf) and "loop: average" in perf[-1][0] and perf[-1][1] is False, perf
assert "loop_max_us" in e.dev.info and "over-long" in e.perf_text()

# ---- an over-long request line is refused, not misread
out = []
tiny = m.SimFirmware(out.append)
tiny.feed(b'{"cmd":"ping","pad":"' + b"x" * 7000 + b'"}\n')
assert b"too_long" in b"".join(out) and tiny.rx_overruns == 1
k.close()

# ======================================================================= firmware 1.5: nothing new, nothing breaks
os.environ["DESK_COMPANION_SIM_V15"] = "1"
k5 = Kit("dcfw15b_", fresh=False)
e5, sim5, pump5 = k5.e, k5.sim, k5.pump
assert not any(c in e5.dev.info["caps"] for c in m.NEW16_CAPS) and "proto" not in e5.dev.info
assert e5.fw_status()[0] == "ok"
e5.upload_all()
assert pump5(200, lambda: "Uploaded to the pad" in k5.status), k5.status
assert sim5.cmd_count.get("remap", 0) >= 21 and sim5.cmd_count.get("remap_batch", 0) == 0 and all(len(sim5.layers[n]) == 7 for n in range(3))
assert not e5.send_ctx("x") and not e5.pad_toast("x") and not e5.push_accent() and sim5.cmd_count.get("ctx", 0) == 0 and sim5.cmd_count.get("toast", 0) == 0
e5.layer_names_set(["A", "B", "C"])
pump5(20)
assert e5.layer_names() == ["A", "B", "C"] and "layer_names" not in sim5.cmd_count and "cannot show them" in k5.status, k5.status
e5.set_pad_accent(True); pump5(20)
assert "accent" not in sim5.settings15 and "cannot follow" in k5.status, k5.status
assert e5.dev.snapshot(scale=2).size == (240, 240), "an old pad ignores the scale"
got = []
e5.mirror_busy_snapshot(got.append, lambda: got.append(None))
assert pump5(100, lambda: got) and got[0].size == (240, 240)
sim5.pad_side_change(mode=4, bright=120, layer=1)                           # a 1.5 pad sends the layer event but no state event
assert pump5(60, lambda: e5.pad_layer == 1)
assert e5.pad.mode != 4 and e5.pad.brightness != 120, "no state event on a 1.5 pad: the twin is not told"
try:
    e5.dev.request({"cmd": "toast", "text": "hi"}); raise AssertionError("1.5 pad knows toast")
except m.DeviceError as ex:
    assert "does not know" in str(ex)
e5.perf_refresh(); pump5(10)
assert e5.perf_text() == ""
k5.close()
print("ALL ENGINE FW16 TESTS PASSED")
