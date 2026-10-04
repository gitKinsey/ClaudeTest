"""Engine test: things going wrong - the pad vanishes in the middle of an upload / GIF / snapshot, garbage and silence from the pad, a pad that errors on everything.
The app must not hang, raise out of a handler, or stay stuck in 'uploading'."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit    # noqa: E402

import core.base as m    # noqa: E402

k = Kit("dcchaos_")
e, sim, pump = k.e, k.sim, k.pump
k.listen("uploading", "disconnected")


def stuck_free():
    return not e.uploading


# ---- the pad is unplugged during 'Upload everything'
e.upload_all()
time.sleep(0.05)
e.dev.disconnect(); e._on_disconnected()
assert pump(200, stuck_free), "uploading flag must clear"
assert ("uploading", (False,)) in k.events
assert not e.dev.connected and not e.dev.busy

# ---- back again
e.toggle_simulate()
assert pump(120, lambda: e.dev.connected)
sim = e.dev.ser.sim

# ---- a GIF upload while the pad disappears
e.use_preset(e.builtin_presets()[0])
assert pump(200, lambda: e.gif_data is not None)
e.upload_gif()
time.sleep(0.1)
e.dev.disconnect(); e._on_disconnected()
assert pump(300, lambda: not e.dev.busy), "the link is not left busy"
e.toggle_simulate(); assert pump(120, lambda: e.dev.connected)
sim = e.dev.ser.sim

# ---- an unplugged pad: every action says so instead of raising
for fn in (e.upload_all, e.verify_pad_keys, e.refresh_health, e.snapshot, e.ping5, e.latency_test, e.sync_time, e.recovery_check, e.read_inputs, e.show_info):
    e.dev.disconnect(); e._on_disconnected()
    try:
        fn()
    except TypeError:
        pass
    pump(10)
k.check_handlers()
e.toggle_simulate(); assert pump(120, lambda: e.dev.connected)
sim = e.dev.ser.sim

# ---- a pad that answers every command with an error
orig = sim._handle


def grumpy(line):
    import json
    try:
        msg = json.loads(line)
    except ValueError:
        return
    sim._send({"ok": False, "err": "busy", **({"id": msg["id"]} if isinstance(msg.get("id"), int) else {})})


sim._handle = grumpy
e.upload_all()
assert pump(300, stuck_free), "an erroring pad does not leave the app uploading"
assert "failed" in k.status.lower() or "busy" in k.status.lower() or e.status[1], k.status
e.pad_settings_apply(theme=1); pump(10)
e.send_ctx("x"); e.pad_toast("hello"); e.perf_refresh(); pump(10)
sim._handle = orig

# ---- garbage between the lines is ignored
sim._send({"junk": 1})
sim.emit(b"this is not json\n")
sim.emit(b"\xff\xfe\x00 binary\n")
pump(10)
assert e.dev.connected
r = e.dev.request({"cmd": "ping"})
assert r["ok"]

# ---- a pad that goes silent: requests time out with an error, not forever
sim._handle = lambda line: None
t0 = time.time()
try:
    e.dev.request({"cmd": "ping"}, timeout=1.0); raise AssertionError("answered by a silent pad")
except m.DeviceError:
    pass
assert time.time() - t0 < 3
sim._handle = orig
assert e.dev.request({"cmd": "ping"})["ok"]
k.check_handlers()
k.close()
print("ALL ENGINE CHAOS TESTS PASSED")
