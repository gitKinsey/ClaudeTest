"""Engine smoke test against the built-in simulated pad (no hardware, no serial port): connect, the whole diagnostics toolset (terminal, ping, info, self-test, LED, events,
display snapshot, GPIO, HID), key upload + read-back verification and the diagnostic report."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit    # noqa: E402

k = Kit("dcself_")
import core.base as m    # noqa: E402
e, pump = k.e, k.pump
lines = lambda: e.term_lines    # noqa: E731
status = lambda: k.status    # noqa: E731
snaps, gtext, keys_seen = [], [], []
e.on("snapshot", lambda img: snaps.append(img))
e.on("gpio_text", lambda t: gtext.append(t))
e.on("inputs", lambda ks, sw, pos: keys_seen.append((ks, sw, pos)))

assert e.send_raw('{"cmd":"ping","t":5}'); pump()
assert any('"pong"' in ln for ln in lines()), lines()[-5:]
e.ping5(); pump(40)
assert any("ping x5" in ln for ln in lines())
e.show_info(); pump(30)
assert any("ESP32-S3" in ln for ln in lines())
e.full_selftest(); pump(80)
res = [ln for ln in lines() if "self-test finished" in ln]
assert res and "7/7" in res[-1], lines()[-12:]
e.led_color(10, 20, 30); pump(20)
assert k.sim.led["r"] == 10 and k.sim.led["b"] == 30, k.sim.led
e.led_mode("rainbow"); pump(20); assert k.sim.led["mode"] == 4
e.events_set(True); pump(20)
e.dev.request({"cmd": "input", "k": 3}); pump(20)
e.dev.request({"cmd": "display", "test": "fill", "r": 255})
e.snapshot(); pump(80)
assert snaps and snaps[0].size == (240, 240), "no snapshot image"
e.gpio("7", "read"); pump(20)
assert gtext and "TFT BLK" in gtext[-1], gtext
e.gpio_scan(); pump(20)
assert "HIGH" in gtext[-1]
e.read_inputs(); pump(20)
assert keys_seen and len(keys_seen[-1][0]) == 5
e.hid_later({"cmd": "run", "type": "text", "val": "x"}, n=0); pump(20)
assert "HID test sent" in status() or any('"run"' in ln for ln in lines())
e.cfg["map"]["1"] = {"cat": "Editing", "action": "Cut"}
e.upload_all(); pump(120)
assert "verified" in status() or "Uploaded" in status(), status()
print("status:", status())
e.verify_pad_keys(); pump(30)
assert "Key check OK" in status()
e.report(); pump(5)
assert (m.Path.home() / "deskcompanion_diag.txt").exists(), "diagnostic report was not written"
rows, hint = e.ports_table()
assert isinstance(rows, list) and hint
e.probe_ports(); pump(20)
e.reboot(); pump(10)
k.close()
print("ENGINE DIAGNOSTICS OK")
