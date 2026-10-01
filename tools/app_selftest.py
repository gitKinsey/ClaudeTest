#!/usr/bin/env python3
"""Headless smoke test of the companion app against its built-in simulated pad (no hardware, no real serial port).

Exercises connect, the whole Dev tab (terminal, ping, info, self-test, LED, events, display snapshot, GPIO, HID),
key upload + read-back verification and the diagnostic report.   Needs a display; on a server use:

    HOME=/tmp/dc DESK_COMPANION_CONFIG=/tmp/dc/.cfg xvfb-run -a python3 tools/app_selftest.py
"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m

app = m.App(); app.update()
app.toggle_simulate()
for _ in range(80):
    app.update(); time.sleep(0.05)
    if app.dev.connected: break
assert app.dev.connected
dt = app.devtab
app.tabs.set("Dev"); app.update()

def pump(n=30, d=0.05):
    for _ in range(n): app.update(); time.sleep(d)

# raw terminal
dt.raw_var.set('{"cmd":"ping","t":5}'); dt.send_raw(); pump()
assert any('"pong"' in l for l in dt.lines), dt.lines[-5:]
# ping x5, info, selftest
dt.ping(); pump(40)
assert any("ping x5" in l for l in dt.lines)
dt.show_info(); pump(30)
assert any("ESP32-S3" in l for l in dt.lines)
dt.full_selftest(); pump(80)
res = [l for l in dt.lines if "self-test finished" in l]
assert res and "7/7" in res[-1], dt.lines[-12:]
# LED
dt.led_color(10, 20, 30); pump(20)
assert app.dev.ser.sim.led["r"] == 10 and app.dev.ser.sim.led["b"] == 30, app.dev.ser.sim.led
dt._led_mode("rainbow"); pump(20); assert app.dev.ser.sim.led["mode"] == 4
# events + virtual press
dt.events_var.set(True); dt.toggle_events(); pump(20)
app.dev.request({"cmd": "input", "k": 3}); pump(20)
# display + snapshot
app.dev.request({"cmd": "display", "test": "fill", "r": 255}); 
dt.snapshot(); pump(80)
assert dt._snap_img is not None, "no snapshot image"
# gpio
dt.gpio_pin.set("7"); dt.gpio("read"); pump(20)
assert "TFT BLK" in dt.gpio_out.cget("text"), dt.gpio_out.cget("text")
dt.gpio_scan(); pump(20)
# hid
dt.hid_later({"cmd": "run", "type": "text", "val": "x"}, n=0); pump(20)
# verify keys after remap
app.cfg["map"]["1"] = {"cat": "Editing", "action": "Cut"}
app.upload_all(); pump(120)
assert any("verified" in app.status.cget("text") or "Uploaded" in app.status.cget("text") for _ in [0]), app.status.cget("text")
print("status:", app.status.cget("text"))
app.verify_pad_keys(); pump(30); print("status2:", app.status.cget("text"))
assert "Key check OK" in app.status.cget("text")
dt.report(); pump(5)
print("report saved:", (m.Path.home()/"deskcompanion_diag.txt").exists())
dt.refresh_ports(); dt.probe_ports(); pump(20)
print("DEV TAB OK")
app._on_close()
