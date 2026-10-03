#!/usr/bin/env python3
"""Minimal standalone client for the pad's USB serial protocol (see docs/PROTOCOL.md). Needs only  pip install pyserial.

    from deskcompanion_client import Pad
    with Pad() as pad:                # finds the pad by its USB id, or Pad("/dev/ttyACM0")
        print(pad.hello()["fw"])
        pad.request({"cmd": "led", "mode": "solid", "hex": "ff8800"})
        for evt in pad.events(seconds=10):    # key presses, dial turns, host requests ...
            print(evt)
Do not run this while the Desk Companion app is connected to the same port: only one program can own it.
If the app's local API is on, prefer that (deskcompanion_cli.py)."""
import json
import queue
import threading
import time

VID_PID = ((0x303A, 0x1001), (0x303A, 0x4001), (0x303A, 0x0002))      # Espressif native USB / the pad's own CDC id


class PadError(Exception):
    pass


def find_port():
    from serial.tools import list_ports                                  # noqa: PLC0415
    for p in list_ports.comports():
        if (p.vid, p.pid) in VID_PID or "desk companion" in (p.description or "").lower():
            return p.device
    raise PadError("no Desk Companion pad found (is it plugged in, and is the app closed?)")


class Pad:
    def __init__(self, port=None, baud=115200, serial_factory=None):
        import serial                                                    # noqa: PLC0415
        self.ser = (serial_factory or serial.Serial)(port or find_port(), baud, timeout=0.1)
        self._resp, self._evt, self._rid, self._stop = queue.Queue(), queue.Queue(), 0, threading.Event()
        self._lock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        buf = b""
        while not self._stop.is_set():
            try:
                buf += self.ser.read(256)
            except Exception:                                            # noqa: BLE001
                return
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue                                             # boot noise / log lines
                if isinstance(msg, dict):
                    (self._evt if "evt" in msg and "id" not in msg else self._resp).put(msg)

    def request(self, msg, timeout=2.0):
        with self._lock:
            self._rid += 1
            rid = self._rid
            self.ser.write((json.dumps(dict(msg, id=rid)) + "\n").encode())
            end = time.monotonic() + timeout
            while True:
                try:
                    r = self._resp.get(timeout=max(0.0, end - time.monotonic()))
                except queue.Empty:
                    raise PadError("the pad did not answer") from None
                if r.get("id", rid) == rid:
                    break
        if not r.get("ok"):
            raise PadError(str(r.get("err", "error")))
        return r

    def hello(self):
        return self.request({"cmd": "hello"})

    def events(self, seconds=None):
        end = None if seconds is None else time.monotonic() + seconds
        while end is None or time.monotonic() < end:
            try:
                yield self._evt.get(timeout=0.2)
            except queue.Empty:
                continue

    def close(self):
        self._stop.set()
        try:
            self.ser.close()
        except Exception:                                                # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
