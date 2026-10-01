#!/usr/bin/env python3
"""
Desk Companion - desktop companion for the ESP32-S3 macro pad.

    pip install customtkinter pyserial psutil pillow pynput      (pynput is optional: live test on this PC)
    python companion_app.py

Features: auto-detect + auto-reconnect over USB CDC, 1 Hz CPU/RAM telemetry, 53 preset actions,
custom combos / text snippets / delay macros, GIF -> 240x240 circular processor + uploader,
brightness / mode / OS / time / Wi-Fi settings, and a VIRTUAL PAD: a live digital twin of the device
(screen, 5 keys, encoder) with drag-and-drop key assignment, a test mode that can type on this PC,
and one-click upload of everything to the physical pad. The pad works fine without this app running.
"""
import base64
import ctypes
import ctypes.wintypes
import functools
import io
import json
import math
import os
import platform
import queue
import threading
import time
import traceback
import zlib
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
import psutil
import serial
from serial.tools import list_ports
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageSequence, ImageTk

APP_NAME = "Desk Companion"
BAUD = 115200
ESPRESSIF_VID = 0x303A
CONFIG_PATH = Path(os.environ.get("DESK_COMPANION_CONFIG") or Path.home() / ".desk_companion.json")
LCD = 240
MAX_GIF_FRAMES = 300

_R = getattr(Image, "Resampling", Image)
_Q = getattr(Image, "Quantize", Image)
_D = getattr(Image, "Dither", Image)
RESAMPLE, QUANT_MEDIAN, DITHER_NONE, DITHER_FS = _R.LANCZOS, _Q.MEDIANCUT, _D.NONE, _D.FLOYDSTEINBERG

# ============================================================================ action library
def C(*keys): return ("combo", list(keys))
def M(name): return ("media", name)
def MACRO(*steps): return ("macro", list(steps))
def RUN(cmd):      # Windows Run dialog launcher
    return MACRO({"combo": ["GUI", "r"]}, {"delay": 450}, {"text": cmd}, {"delay": 120}, {"combo": ["ENTER"]})
def SPOT(app):     # macOS Spotlight launcher
    return MACRO({"combo": ["GUI", "SPACE"]}, {"delay": 450}, {"text": app}, {"delay": 350}, {"combo": ["ENTER"]})

DARK_CMD = (r'''powershell -w hidden -c "$k='HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize';'''
            r'''$v=1-(gp $k).AppsUseLightTheme;sp $k AppsUseLightTheme $v;sp $k SystemUsesLightTheme $v"''')

# name, windows/linux spec, optional macOS spec.  "PRIMARY" = Ctrl on Windows/Linux, Cmd on macOS (resolved by the firmware).
ACTIONS = {
    "Editing": [
        ("Copy", C("PRIMARY", "c")), ("Cut", C("PRIMARY", "x")), ("Paste", C("PRIMARY", "v")),
        ("Undo", C("PRIMARY", "z")), ("Redo", C("CTRL", "y"), C("GUI", "SHIFT", "z")),
        ("Select All", C("PRIMARY", "a")), ("Save", C("PRIMARY", "s")), ("Duplicate", C("PRIMARY", "d")),
        ("Find", C("PRIMARY", "f")), ("Delete", C("DELETE")),
    ],
    "Media": [
        ("Play/Pause", M("PLAY_PAUSE")), ("Next Track", M("NEXT")), ("Previous Track", M("PREV")),
        ("Stop", M("STOP")), ("Mute", M("MUTE")), ("Volume Up", M("VOL_UP")),
        ("Volume Down", M("VOL_DOWN")), ("Fast Forward", M("FF")),
    ],
    "OS Controls": [
        ("Lock Workstation", C("GUI", "l"), C("CTRL", "GUI", "q")),
        ("Show Desktop", C("GUI", "d"), C("F11")),
        ("Task Manager", C("CTRL", "SHIFT", "ESC"), C("GUI", "ALT", "ESC")),
        ("Switch App (Alt+Tab)", C("ALT", "TAB"), C("GUI", "TAB")),
        ("Close Window", C("ALT", "F4"), C("GUI", "w")),
        ("Screenshot (Win+Shift+S)", C("GUI", "SHIFT", "s"), C("GUI", "SHIFT", "4")),
        ("Open Explorer / Finder", C("GUI", "e"), SPOT("Finder")),
        ("Terminal", RUN("cmd"), SPOT("Terminal")),
        ("Settings", C("GUI", "i"), SPOT("System Settings")),
        ("Calculator", RUN("calc"), SPOT("Calculator")),
        ("Toggle Dark Mode (Windows)", RUN(DARK_CMD)),
        ("Screen Snip", RUN("snippingtool"), C("GUI", "SHIFT", "5")),
    ],
    "Browser": [
        ("New Tab", C("PRIMARY", "t")), ("Close Tab", C("PRIMARY", "w")),
        ("Reopen Closed Tab", C("PRIMARY", "SHIFT", "t")), ("Refresh", C("PRIMARY", "r")),
        ("Bookmarks", C("PRIMARY", "SHIFT", "o")), ("History", C("CTRL", "h"), C("GUI", "y")),
        ("Forward", C("ALT", "RIGHT"), C("GUI", "]")), ("Back", C("ALT", "LEFT"), C("GUI", "[")),
        ("Zoom In", C("PRIMARY", "=")), ("Zoom Out", C("PRIMARY", "-")),
    ],
    "Productivity & Dev": [
        ("Toggle Zoom Mute", C("ALT", "a"), C("GUI", "SHIFT", "a")),
        ("Toggle Zoom Camera", C("ALT", "v"), C("GUI", "SHIFT", "v")),
        ("VS Code Format Document", C("SHIFT", "ALT", "f")),
        ("VS Code Line Comment", C("PRIMARY", "/")),
        ("VS Code Terminal", C("CTRL", "`")),
        ("Duplicate Line (VS Code)", C("SHIFT", "ALT", "DOWN")),
        ("Search Files (Quick Open)", C("PRIMARY", "p")),
        ("Git Status (terminal)", MACRO({"combo": ["CTRL", "`"]}, {"delay": 400}, {"text": "git status\n"})),
        ("Emoji Picker", C("GUI", "."), C("CTRL", "GUI", "SPACE")),
        ("Task View / Mission Control", C("GUI", "TAB"), C("CTRL", "UP")),
        ("Command Palette", C("PRIMARY", "SHIFT", "p")),
        ("Toggle Sidebar (VS Code)", C("PRIMARY", "b")),
    ],
    "Other": [("Unassigned", ("none", None))],
}
ACTION_INDEX = {(cat, a[0]): (a[1], a[2] if len(a) > 2 else None) for cat, lst in ACTIONS.items() for a in lst}
assert len(ACTION_INDEX) >= 50

SLOT_LABELS = {1: "K1", 2: "K2", 3: "K3", 4: "K4", 5: "K5", 6: "Encoder turn right", 7: "Encoder turn left"}
DEFAULT_MAP = {1: ("Editing", "Copy"), 2: ("Editing", "Paste"), 3: ("Editing", "Undo"), 4: ("Media", "Play/Pause"),
               5: ("Media", "Mute"), 6: ("Media", "Volume Up"), 7: ("Media", "Volume Down")}
MODIFIERS = ["-", "CTRL", "SHIFT", "ALT", "GUI", "PRIMARY"]
NAMED_KEYS = ["ENTER", "TAB", "ESC", "SPACE", "BACKSPACE", "DELETE", "INSERT", "HOME", "END", "PGUP", "PGDN",
              "UP", "DOWN", "LEFT", "RIGHT", "PRTSC", "MENU", "CAPSLOCK"]
KEY_CHOICES = (list("abcdefghijklmnopqrstuvwxyz0123456789") + [f"F{i}" for i in range(1, 13)] + NAMED_KEYS
               + list("-=[];',./`\\"))
MEDIA_CHOICES = ["PLAY_PAUSE", "NEXT", "PREV", "STOP", "MUTE", "VOL_UP", "VOL_DOWN", "FF", "REWIND"]
MODE_CHOICES = ["1 Clock", "2 Focus timer", "3 Media", "4 System", "5 GIF"]


def valid_key(k):
    if not k:
        return False
    if len(k) == 1:
        return 32 < ord(k) < 127
    u = k.upper()
    return u in NAMED_KEYS or (u[0] == "F" and u[1:].isdigit() and 1 <= int(u[1:]) <= 24)


# ============================================================================ config
def load_config():
    cfg = {"os": {"Windows": "win", "Darwin": "mac"}.get(platform.system(), "linux"), "map": {}, "custom": {}}
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    for s, (cat, name) in DEFAULT_MAP.items():
        cfg["map"].setdefault(str(s), {"cat": cat, "action": name})
    cfg.setdefault("pushed", {})              # what was last uploaded to the pad, per slot (JSON of the spec)
    cfg.setdefault("twin_mode", 1)
    cfg.setdefault("twin_bright", 200)
    cfg.setdefault("pushed_mode", None)
    cfg.setdefault("pushed_bright", None)
    cfg.setdefault("notify", True)
    cfg.setdefault("live_test", True)
    cfg.setdefault("layout", "auto")
    return cfg


def save_config(cfg):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    except OSError:
        pass


def resolve_spec(cfg, cat, name):
    if cat == "Custom":
        c = cfg["custom"].get(name)
        return (c["type"], c["val"]) if c else None
    entry = ACTION_INDEX.get((cat, name))
    if not entry:
        return None
    win, mac = entry
    return mac if (cfg["os"] == "mac" and mac) else win


def time_msg():
    lt = time.localtime()
    off = -(time.altzone if (lt.tm_isdst > 0 and time.daylight) else time.timezone)
    return {"cmd": "time", "epoch": int(time.time()), "tz": off}


# ============================================================================ GIF processing
def load_gif_frames(path, max_frames=MAX_GIF_FRAMES):
    """Return (frames, durations_ms): centre-cropped square, 240x240, black outside the circular display area."""
    im = Image.open(path)
    n = getattr(im, "n_frames", 1)
    step = max(1, -(-n // max_frames))
    mask = Image.new("L", (LCD, LCD), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, LCD - 1, LCD - 1), fill=255)
    black = Image.new("RGB", (LCD, LCD), (0, 0, 0))
    frames, durs, acc = [], [], 0
    for i, fr in enumerate(ImageSequence.Iterator(im)):
        acc += int(fr.info.get("duration", 100) or 100)
        if i % step:
            continue
        rgba = fr.convert("RGBA")
        flat = Image.alpha_composite(Image.new("RGBA", rgba.size, (0, 0, 0, 255)), rgba).convert("RGB")
        w, h = flat.size
        s = min(w, h)
        flat = flat.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)).resize((LCD, LCD), RESAMPLE)
        frames.append(Image.composite(flat, black, mask))
        durs.append(max(20, int(round(acc / 10.0)) * 10))
        acc = 0
    if not frames:
        raise ValueError("no frames found in this image")
    return frames, durs


def _palette(frames, colors):
    sel = frames[::max(1, len(frames) // 16)][:16]
    mosaic = Image.new("RGB", (96 * 4, 96 * ((len(sel) + 3) // 4)))
    for i, f in enumerate(sel):
        mosaic.paste(f.resize((96, 96), RESAMPLE), ((i % 4) * 96, (i // 4) * 96))
    return mosaic.quantize(colors=colors, method=QUANT_MEDIAN, dither=DITHER_NONE)


def encode_gif(frames, durs, colors, dither):
    pal = _palette(frames, colors)
    q = [f.quantize(palette=pal, dither=DITHER_FS if dither else DITHER_NONE) for f in frames]
    buf = io.BytesIO()
    q[0].save(buf, format="GIF", save_all=True, append_images=q[1:], duration=durs, loop=0, optimize=True)
    return buf.getvalue()


def _decimate(frames, durs, k):
    if k == 1:
        return frames, durs
    return frames[::k], [sum(durs[i:i + k]) for i in range(0, len(durs), k)]


def fit_gif(frames, durs, max_bytes, dither=False):
    """Encode, progressively reducing colours / frame count until the file fits the pad's flash."""
    for colors, k in [(256, 1), (128, 1), (64, 1), (64, 2), (32, 2), (32, 3), (16, 3), (16, 4), (16, 6), (16, 10)]:
        fr, du = _decimate(frames, durs, k)
        data = encode_gif(fr, du, colors, dither)
        if len(data) <= max_bytes:
            return data, colors, len(fr)
    raise ValueError(f"GIF still larger than {max_bytes // 1024} KB after maximum reduction")


# ============================================================================ serial device
class DeviceError(Exception):
    pass


def candidate_ports():
    out = []
    for p in list_ports.comports():
        text = f"{p.description} {p.manufacturer} {p.product}".lower()
        if p.vid == ESPRESSIF_VID or "desk companion" in text:
            out.append(p.device)
    return out


class Device:
    def __init__(self, on_drop):
        self.ser, self.port, self.info, self.busy, self._ready = None, "", {}, False, False
        self._wlock, self._rlock, self._resp, self._on_drop = threading.Lock(), threading.Lock(), queue.Queue(), on_drop

    @property
    def connected(self):
        return self.ser is not None and self._ready        # only after the hello handshake succeeded

    def connect(self, port):
        self.disconnect()
        ser = serial.Serial(port, BAUD, timeout=0.1, write_timeout=3)
        self.ser = ser
        threading.Thread(target=self._reader, args=(ser,), daemon=True).start()
        last = None
        for _ in range(3):
            try:
                self.info = self.request({"cmd": "hello"}, timeout=1.5)
                break
            except DeviceError as e:
                last = e
                time.sleep(0.3)
        else:
            self.disconnect()
            raise last
        self.port, self._ready = port, True
        return self.info

    def disconnect(self):
        ser, self.ser, self.info, self._ready = self.ser, None, {}, False
        if ser:
            try:
                ser.close()
            except (serial.SerialException, OSError):
                pass

    def _drop(self, ser):
        if self.ser is ser:
            self.disconnect()
            self._on_drop()

    def _reader(self, ser):
        buf = b""
        while self.ser is ser:
            try:
                data = ser.read(ser.in_waiting or 1)
            except (serial.SerialException, OSError, TypeError):
                break
            if not data:
                continue
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(msg, dict) and "ok" in msg:
                    self._resp.put(msg)
        self._drop(ser)

    def send(self, msg):
        ser = self.ser
        if not ser:
            raise DeviceError("not connected")
        data = (json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        try:
            with self._wlock:
                ser.write(data)
        except (serial.SerialException, OSError) as e:
            self._drop(ser)
            raise DeviceError(str(e))

    def request(self, msg, timeout=2.0):
        with self._rlock:
            while not self._resp.empty():
                self._resp.get_nowait()
            self.send(msg)
            try:
                r = self._resp.get(timeout=timeout)
            except queue.Empty:
                raise DeviceError("device did not answer (timeout)")
        if not r.get("ok"):
            raise DeviceError(r.get("err", "error"))
        return r

    def upload_gif(self, data, progress=None):
        self.busy = True
        try:
            r = self.request({"cmd": "gif_begin", "size": len(data), "crc": zlib.crc32(data) & 0xFFFFFFFF}, timeout=6)
            chunk, sent, seq = int(r.get("chunk", 768)), 0, 0
            while sent < len(data):
                part = data[sent:sent + chunk]
                self.request({"cmd": "gif_chunk", "seq": seq, "data": base64.b64encode(part).decode("ascii")}, timeout=5)
                sent += len(part)
                seq += 1
                if progress:
                    progress(sent / len(data))
            self.request({"cmd": "gif_end"}, timeout=10)
        except Exception:
            try:
                self.request({"cmd": "gif_abort"}, timeout=2)
            except DeviceError:
                pass
            raise
        finally:
            self.busy = False


# ============================================================================ virtual pad helpers
CAT_COLORS = {"Editing": "#4aa3ff", "Media": "#ff5db1", "OS Controls": "#ffb03b", "Browser": "#4cd97b",
              "Productivity & Dev": "#a98bff", "Custom": "#2dd4d4", "Other": "#6b7280"}
RISKY = {"Lock Workstation", "Close Window"}      # ask before really running these in live-test mode
HOST_OS = {"Windows": "win", "Darwin": "mac"}.get(platform.system(), "linux")


def spec_json(spec):
    return json.dumps(list(spec) if spec else None, sort_keys=True)


def describe_spec(spec):
    if not spec:
        return "nothing"
    t, v = spec
    if t == "combo":
        return "+".join(("Ctrl/Cmd" if k == "PRIMARY" else k.upper() if len(k) > 1 else k.upper()) for k in v)
    if t == "media":
        return "media key " + v.replace("_", " ").lower()
    if t == "text":
        s = v.replace("\n", "<Enter>")
        return 'type "' + (s if len(s) <= 28 else s[:25] + "...") + '"'
    if t == "macro":
        return f"macro, {len(v)} steps"
    return "nothing"


# ============================================================================ live test: real keystrokes on this PC
class KeySender:
    """Common macro runner; backends implement combo(), text() and media()."""

    def run(self, spec, stop=None):
        t, v = spec
        if t == "combo":
            self.combo(v)
        elif t == "media":
            self.media(v)
        elif t == "text":
            self.text(v)
        elif t == "macro":
            for s in v:
                if stop is not None and stop.is_set():
                    break
                if "combo" in s:
                    self.combo(s["combo"])
                elif "text" in s:
                    self.text(s["text"])
                elif "media" in s:
                    self.media(s["media"])
                elif "delay" in s:
                    (stop.wait if stop is not None else time.sleep)(s["delay"] / 1000.0)
                time.sleep(0.01)


# --- Windows: native SendInput, no extra package needed
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_uint16), ("wParamH", ctypes.c_uint16)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint32), ("u", _INPUT_UNION)]


KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4


class WinKeys(KeySender):
    VK = {"CTRL": 0x11, "CONTROL": 0x11, "PRIMARY": 0x11, "MOD": 0x11, "SHIFT": 0x10, "ALT": 0x12, "OPT": 0x12,
          "OPTION": 0x12, "GUI": 0x5B, "WIN": 0x5B, "CMD": 0x5B, "META": 0x5B, "SUPER": 0x5B, "ENTER": 0x0D,
          "RETURN": 0x0D, "ESC": 0x1B, "ESCAPE": 0x1B, "BACKSPACE": 0x08, "TAB": 0x09, "SPACE": 0x20,
          "CAPSLOCK": 0x14, "PRTSC": 0x2C, "PRINTSCREEN": 0x2C, "PAUSE": 0x13, "INSERT": 0x2D, "HOME": 0x24,
          "PGUP": 0x21, "PAGEUP": 0x21, "DELETE": 0x2E, "DEL": 0x2E, "END": 0x23, "PGDN": 0x22, "PAGEDOWN": 0x22,
          "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28, "MENU": 0x5D}
    EXTENDED = {0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x25, 0x26, 0x27, 0x28, 0x5B, 0x5D}
    MEDIA = {"PLAY_PAUSE": 0xB3, "NEXT": 0xB0, "PREV": 0xB1, "STOP": 0xB2, "MUTE": 0xAD, "VOL_DOWN": 0xAE, "VOL_UP": 0xAF}
    APPCOMMAND = {"FF": 49, "REWIND": 50}                      # no virtual key exists; sent as WM_APPCOMMAND

    def __init__(self, user32=None):
        if user32 is None:
            user32 = ctypes.windll.user32
            user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
            user32.SendInput.restype = ctypes.c_uint
            user32.GetForegroundWindow.restype = ctypes.c_void_p
            user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        self.u = user32

    def _vk(self, name):
        if len(name) == 1:
            if name.isalnum() and name.isascii():
                return ord(name.upper())
            r = self.u.VkKeyScanW(ord(name))
            if r == -1 or r == 0xFFFF:
                raise ValueError(f"key '{name}' does not exist on the current keyboard layout")
            return r & 0xFF
        n = name.upper()
        if n in self.VK:
            return self.VK[n]
        if n[0] == "F" and n[1:].isdigit() and 1 <= int(n[1:]) <= 24:
            return 0x70 + int(n[1:]) - 1
        raise ValueError(f"unknown key '{name}'")

    def _send(self, events):                                  # events: [(vk, scan, flags)]
        arr = (INPUT * len(events))()
        for i, (vk, scan, flags) in enumerate(events):
            arr[i].type = 1
            arr[i].ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        if self.u.SendInput(len(events), arr, ctypes.sizeof(INPUT)) != len(events):
            raise OSError("Windows refused the key input (a program running as administrator in front?)")

    def _key_event(self, vk, up):
        scan = self.u.MapVirtualKeyW(vk, 0) & 0xFF
        return (vk, scan, (KEYEVENTF_EXTENDEDKEY if vk in self.EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0))

    def combo(self, keys):
        vks = [self._vk(k) for k in keys]
        self._send([self._key_event(v, False) for v in vks])
        time.sleep(0.03)
        self._send([self._key_event(v, True) for v in reversed(vks)])

    def text(self, s):
        for ch in s:
            if ch == "\n" or ch == "\r":
                self.combo(["ENTER"]) if ch == "\n" else None
            elif ch == "\t":
                self.combo(["TAB"])
            else:
                units = ch.encode("utf-16-le")
                for i in range(0, len(units), 2):
                    u = int.from_bytes(units[i:i + 2], "little")
                    self._send([(0, u, KEYEVENTF_UNICODE), (0, u, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)])
            time.sleep(0.004)

    def media(self, name):
        n = name.upper()
        if n in self.MEDIA:
            vk = self.MEDIA[n]
            self._send([(vk, 0, KEYEVENTF_EXTENDEDKEY)])
            self._send([(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)])
        elif n in self.APPCOMMAND:
            hwnd = self.u.GetForegroundWindow()
            self.u.SendMessageW(hwnd, 0x0319, hwnd, self.APPCOMMAND[n] << 16)      # WM_APPCOMMAND
        else:
            raise ValueError(f"media key '{name}' is not supported")


# --- macOS / Linux: pynput (optional package)
class PynputKeys(KeySender):
    NAMES = {"CTRL": "ctrl", "CONTROL": "ctrl", "SHIFT": "shift", "ALT": "alt", "OPT": "alt", "OPTION": "alt",
             "GUI": "cmd", "WIN": "cmd", "CMD": "cmd", "META": "cmd", "SUPER": "cmd", "ENTER": "enter",
             "RETURN": "enter", "ESC": "esc", "ESCAPE": "esc", "BACKSPACE": "backspace", "TAB": "tab",
             "SPACE": "space", "CAPSLOCK": "caps_lock", "PRTSC": "print_screen", "PRINTSCREEN": "print_screen",
             "PAUSE": "pause", "INSERT": "insert", "HOME": "home", "PGUP": "page_up", "PAGEUP": "page_up",
             "DELETE": "delete", "DEL": "delete", "END": "end", "PGDN": "page_down", "PAGEDOWN": "page_down",
             "RIGHT": "right", "LEFT": "left", "DOWN": "down", "UP": "up", "MENU": "menu"}
    MEDIA = {"PLAY_PAUSE": "media_play_pause", "NEXT": "media_next", "PREV": "media_previous",
             "MUTE": "media_volume_mute", "VOL_UP": "media_volume_up", "VOL_DOWN": "media_volume_down"}

    def __init__(self):
        from pynput.keyboard import Controller, Key           # raises when missing / no X server
        self.kb, self.Key = Controller(), Key
        self.mac = platform.system() == "Darwin"

    def _key(self, name):
        if len(name) == 1:
            return name.lower() if name.isalpha() else name
        n = name.upper()
        if n in ("PRIMARY", "MOD"):
            return self.Key.cmd if self.mac else self.Key.ctrl
        if n in self.NAMES:
            attr = self.NAMES[n]
        elif n[0] == "F" and n[1:].isdigit():
            attr = n.lower()
        else:
            raise ValueError(f"unknown key '{name}'")
        k = getattr(self.Key, attr, None)
        if k is None:
            raise ValueError(f"key '{name}' is not supported on this system")
        return k

    def combo(self, keys):
        objs, down = [self._key(k) for k in keys], []
        try:
            for o in objs:
                self.kb.press(o)
                down.append(o)
                time.sleep(0.012)
            time.sleep(0.03)
        finally:
            for o in reversed(down):
                self.kb.release(o)
                time.sleep(0.008)

    def text(self, s):
        self.kb.type(s)

    def media(self, name):
        attr = self.MEDIA.get(name.upper())
        k = getattr(self.Key, attr, None) if attr else None
        if k is None:
            raise ValueError(f"media key '{name}' is not supported on this system")
        self.kb.press(k)
        time.sleep(0.01)
        self.kb.release(k)


class HostInput:
    """Picks the key sender for this OS: native SendInput on Windows (nothing to install), pynput elsewhere."""

    def __init__(self, backend=None):
        self.impl, self.available, self.error, self.kind = backend, backend is not None, "", ""
        if backend is None:
            try:
                if platform.system() == "Windows":
                    self.impl, self.kind = WinKeys(), "Windows SendInput"
                else:
                    self.impl, self.kind = PynputKeys(), "pynput"
                self.available = True
            except Exception as e:                            # pynput missing, no X server, no permission ...
                self.error = (f"{type(e).__name__}: {e}".splitlines() or [""])[0][:140]

    def run(self, spec, stop=None):
        self.impl.run(spec, stop)


def spec_needs_focus(spec):
    """Media keys are global; combos and text go to whichever window has the keyboard focus."""
    if not spec:
        return False
    t, v = spec
    return t in ("combo", "text") or (t == "macro" and any(("combo" in s or "text" in s) for s in v))


# --- Windows focus handling: a click on this app steals the keyboard focus, so hand it back before typing
class Win32Api:
    def __init__(self):
        wt = ctypes.wintypes
        self.u, self.k = ctypes.windll.user32, ctypes.windll.kernel32
        u = self.u
        u.GetForegroundWindow.restype = wt.HWND
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u.GetWindowThreadProcessId.restype = wt.DWORD
        u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        u.IsWindowVisible.argtypes = [wt.HWND]
        u.IsIconic.argtypes = [wt.HWND]
        u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        u.SetForegroundWindow.argtypes = [wt.HWND]
        u.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
        self.k.GetCurrentThreadId.restype = wt.DWORD

    def foreground(self):
        return self.u.GetForegroundWindow()

    def pid_of(self, h):
        pid = ctypes.wintypes.DWORD()
        self.u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        return pid.value

    def title(self, h):
        buf = ctypes.create_unicode_buffer(256)
        self.u.GetWindowTextW(h, buf, 256)
        return buf.value

    def cls(self, h):
        buf = ctypes.create_unicode_buffer(128)
        self.u.GetClassNameW(h, buf, 128)
        return buf.value

    def visible(self, h):
        return bool(self.u.IsWindowVisible(h))

    def activate(self, h):
        if self.u.IsIconic(h):
            self.u.ShowWindow(h, 9)                           # SW_RESTORE
        cur = self.k.GetCurrentThreadId()
        tids = {self.u.GetWindowThreadProcessId(self.u.GetForegroundWindow(), None), self.u.GetWindowThreadProcessId(h, None)}
        attached = [t for t in tids if t and t != cur and self.u.AttachThreadInput(cur, t, True)]
        try:
            self.u.SetForegroundWindow(h)
        finally:
            for t in attached:
                self.u.AttachThreadInput(cur, t, False)
        return self.u.GetForegroundWindow() == h


class WinFocus:
    SKIP = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW", "NotifyIconOverflowWindow",
            "TopLevelWindowForOverflowXamlIsland", "XamlExplorerHostIslandWindow", "Windows.UI.Core.CoreWindow"}

    def __init__(self, api, my_pid=None):
        self.api, self.pid, self.last = api, my_pid or os.getpid(), 0

    def _usable(self, h):
        a = self.api
        return bool(h) and a.pid_of(h) != self.pid and a.visible(h) and bool(a.title(h)) and a.cls(h) not in self.SKIP

    def poll(self):
        """Remember the last window of another program that had the keyboard focus."""
        h = self.api.foreground()
        if self._usable(h):
            self.last = h
        return self.last

    def in_own_window(self):
        h = self.api.foreground()
        return bool(h) and self.api.pid_of(h) == self.pid

    def refocus(self):
        """Give the focus back to that window; returns its title, or None when that is not possible."""
        h = self.last
        if not h or not self.api.visible(h):
            return None
        ok = self.api.activate(h)
        time.sleep(0.06)
        return self.api.title(h) if ok else None


LANGID_LAYOUT = {0x0407: "de_DE", 0x0807: "de_DE", 0x0C07: "de_DE", 0x1007: "de_DE", 0x1407: "de_DE",
                 0x040C: "fr_FR", 0x080C: "fr_FR", 0x0C0C: "fr_FR", 0x100C: "fr_CH", 0x0410: "it_IT",
                 0x0810: "it_IT", 0x040A: "es_ES", 0x0C0A: "es_ES", 0x0816: "pt_PT", 0x0416: "pt_BR",
                 0x041D: "sv_SE", 0x0406: "da_DK", 0x040E: "hu_HU", 0x0411: "ja_JP"}
LAYOUTS = ["en_US", "de_DE", "fr_FR", "fr_CH", "es_ES", "it_IT", "pt_PT", "pt_BR", "sv_SE", "da_DK", "hu_HU", "ja_JP"]


def detect_layout():
    """Layout of this PC's keyboard in the names the firmware knows (Windows; en_US elsewhere)."""
    try:
        if platform.system() == "Windows":
            return LANGID_LAYOUT.get(ctypes.windll.user32.GetKeyboardLayout(0) & 0xFFFF, "en_US")
    except Exception:
        pass
    return "en_US"


# ============================================================================ virtual pad: state machine + renderer
SS = 2                      # supersampling of the virtual 240x240 screen
DISP = 240                  # on-screen size of the virtual display
M_CLOCK, M_POMO, M_MEDIA, M_TELEM, M_GIF = 1, 2, 3, 4, 5
MODE_NAMES = ["", "CLOCK", "FOCUS", "MEDIA", "SYSTEM", "GIF"]
PS_IDLE, PS_RUN, PS_PAUSE, PS_DONE = range(4)
DOW = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
C_BG, C_TXT = (0, 0, 0), (255, 255, 255)
C_DIM, C_DIM2, C_GRAY = (38, 42, 52), (70, 76, 92), (140, 146, 160)
C_ACC, C_ACC2, C_OK, C_WARN, C_RED = (0, 210, 255), (255, 70, 170), (70, 220, 110), (255, 176, 0), (255, 72, 72)
DEMO_FRAMES = 24


@functools.lru_cache(maxsize=64)
def _font(px, bold=False):
    names = (["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "segoeuib.ttf", "Helvetica.ttc"] if bold else
             ["DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "segoeui.ttf", "Helvetica.ttc"])
    for n in names:
        try:
            return ImageFont.truetype(n, px)
        except (OSError, ValueError):
            continue
    try:
        return ImageFont.load_default(px)
    except TypeError:
        return ImageFont.load_default()


def _pt(deg, r, cx=120.0, cy=120.0):
    """Point on a circle; 0 deg = 12 o'clock, clockwise (same convention as the firmware)."""
    a = math.radians(deg)
    return cx + r * math.sin(a), cy - r * math.cos(a)


class Gfx:
    """Tiny supersampled 240x240 drawing surface that mirrors the TFT_eSprite calls the firmware uses."""

    def __init__(self):
        self.img = Image.new("RGB", (240 * SS, 240 * SS), C_BG)
        self.d = ImageDraw.Draw(self.img)

    def fill(self, col):
        self.d.rectangle((0, 0, 240 * SS, 240 * SS), fill=col)

    def circle(self, cx, cy, r, fill=None, outline=None, width=1):
        self.d.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS], fill=fill,
                       outline=outline, width=max(1, round(width * SS)) if outline else 0)

    def rect(self, x, y, w, h, col):
        self.d.rectangle([x * SS, y * SS, (x + w) * SS - 1, (y + h) * SS - 1], fill=col)

    def poly(self, pts, col):
        self.d.polygon([(x * SS, y * SS) for x, y in pts], fill=col)

    def line(self, p0, p1, w, col):
        (x0, y0), (x1, y1) = p0, p1
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy)
        if ln < 0.5:
            return
        nx, ny = -dy / ln * w / 2, dx / ln * w / 2
        self.poly([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)], col)

    def band(self, cx, cy, ro, ri, a0, a1, col):
        if a1 <= a0:
            return
        if a1 - a0 >= 359.5:
            self.circle(cx, cy, ro, outline=col, width=ro - ri)
            return
        n = max(2, int(math.ceil((a1 - a0) / 2.0)))
        outer = [_pt(a0 + (a1 - a0) * i / n, ro, cx, cy) for i in range(n + 1)]
        inner = [_pt(a0 + (a1 - a0) * i / n, ri, cx, cy) for i in range(n, -1, -1)]
        self.poly(outer + inner, col)

    def gauge(self, cx, cy, ro, ri, frac, col):          # 270 degree gauge with the gap at the bottom
        self.band(cx, cy, ro, ri, 225, 495, C_DIM)
        frac = max(0.0, min(1.0, frac))
        if frac > 0.005:
            self.band(cx, cy, ro, ri, 225, 225 + 270 * frac, col)

    def text(self, s, x, y, px, col, bold=False, anchor="mm"):
        f = _font(int(px * SS), bold)
        try:
            self.d.text((x * SS, y * SS), s, font=f, fill=col, anchor=anchor)
        except ValueError:
            self.d.text((x * SS, y * SS), s, font=f, fill=col)


def _hand(g, deg, ln, w, col):
    a = math.radians(deg)
    sx, sy = math.sin(a), -math.cos(a)
    g.line((120 - ln * 0.15 * sx, 120 - ln * 0.15 * sy), (120 + ln * sx, 120 + ln * sy), w, col)


class VirtualPad:
    """Python twin of the firmware UI: same modes, radial menu, focus timer and key/encoder behaviour."""

    def __init__(self, exec_slot, exec_media, on_change):
        self.exec_slot, self.exec_media, self.on_change = exec_slot, exec_media, on_change
        self.mode, self.brightness = M_CLOCK, 200
        self.vol, self.muted, self.playing = 50, False, False
        self.cpu = self.ram = 0
        self.boot = time.monotonic()
        self.menu_open, self.menu_sel, self.menu_edit, self.menu_mode, self.menu_touched = False, 0, 0, 1, 0.0
        self.pomo_minutes, self.pomo_state = 25, PS_IDLE
        self.pomo_remain, self.pomo_last, self.pomo_done_at = 25 * 60.0, 0.0, 0.0
        self.gif_frames = self.gif_durs = self._demo_cache = None
        self.upload_frac = None
        self.overlay = None
        self.dirty, self._last = True, 0.0

    # ---- model updates
    def apply_media(self, name):
        n = name.upper()
        if n == "VOL_UP":
            self.vol, self.muted = min(100, self.vol + 2), False
        elif n == "VOL_DOWN":
            self.vol, self.muted = max(0, self.vol - 2), False
        elif n == "MUTE":
            self.muted = not self.muted
        elif n == "PLAY_PAUSE":
            self.playing = not self.playing
        elif n == "STOP":
            self.playing = False
        self.dirty = True

    def apply_spec(self, spec):
        if not spec:
            return
        t, v = spec
        if t == "media":
            self.apply_media(v)
        elif t == "macro":
            for s in v:
                if "media" in s:
                    self.apply_media(s["media"])

    def set_mode(self, m, notify=True):
        if 1 <= m <= 5:
            changed = m != self.mode
            self.mode, self.dirty = m, True
            if changed and notify:
                self.on_change("mode")

    def set_brightness(self, b, notify=True):
        b = max(5, min(255, int(b)))
        changed = b != self.brightness
        self.brightness, self.dirty = b, True
        if changed and notify:
            self.on_change("bright")

    def set_gif(self, frames, durs):
        self.gif_frames, self.gif_durs, self.dirty = frames, durs, True

    def toast(self, text, secs=1.2):
        self.overlay, self.dirty = (text[:26], time.monotonic() + secs), True

    # ---- focus timer
    def pomo_toggle(self):
        if self.pomo_state in (PS_IDLE, PS_DONE):
            self.pomo_remain, self.pomo_state = self.pomo_minutes * 60.0, PS_RUN
        else:
            self.pomo_state = PS_PAUSE if self.pomo_state == PS_RUN else PS_RUN
        self.pomo_last, self.dirty = time.monotonic(), True

    def pomo_reset(self):
        self.pomo_state, self.pomo_remain, self.dirty = PS_IDLE, self.pomo_minutes * 60.0, True

    def _pomo_tick(self, now):
        if self.pomo_state == PS_RUN:
            dt, self.pomo_last = now - self.pomo_last, now
            if dt >= self.pomo_remain:
                self.pomo_remain, self.pomo_state, self.pomo_done_at, self.dirty = 0.0, PS_DONE, now, True
            else:
                self.pomo_remain -= dt
        elif self.pomo_state == PS_DONE and now - self.pomo_done_at > 15:
            self.pomo_reset()

    # ---- radial menu
    def _touch(self):
        self.menu_touched = time.monotonic()

    def _menu_close(self):
        self.menu_open, self.menu_edit, self.dirty = False, 0, True

    def _menu_turn(self, steps):
        self._touch()
        n, d = abs(steps), (1 if steps > 0 else -1)
        if not self.menu_edit:
            self.menu_sel = (self.menu_sel + d * n) % 4
        elif self.menu_edit == 1:
            self.set_brightness(self.brightness + steps * 8)
        elif self.menu_edit == 2:
            for _ in range(n):
                self.exec_media("VOL_UP" if d > 0 else "VOL_DOWN")
        elif self.menu_edit == 3:
            self.menu_mode = (self.menu_mode - 1 + steps) % 5 + 1
        self.dirty = True

    def _menu_click(self):
        self._touch()
        if not self.menu_edit:
            if self.menu_sel == 3:
                self._menu_close()
                return
            self.menu_edit = self.menu_sel + 1
            if self.menu_edit == 3:
                self.menu_mode = self.mode
        else:
            if self.menu_edit == 3 and self.menu_mode != self.mode:
                self.set_mode(self.menu_mode)
            self.menu_edit = 0
        self.dirty = True

    # ---- inputs (identical behaviour to the firmware)
    def key(self, i):                           # i = 0..4
        if self.menu_open:
            self._touch()
        if self.mode == M_POMO and i < 2:
            self.pomo_toggle() if i == 0 else self.pomo_reset()
            return
        self.exec_slot(i + 1)

    def turn(self, steps):                      # +1 = clockwise
        if not steps:
            return
        if self.menu_open:
            self._menu_turn(steps)
        elif self.mode == M_POMO and self.pomo_state == PS_IDLE:
            self.pomo_minutes = max(1, min(90, self.pomo_minutes + steps))
            self.pomo_remain, self.dirty = self.pomo_minutes * 60.0, True
        else:
            for _ in range(abs(steps)):
                self.exec_slot(6 if steps > 0 else 7)

    def click(self):
        if self.menu_open:
            self._menu_click()
        else:
            self.menu_open, self.menu_sel, self.menu_edit, self.menu_mode = True, 0, 0, self.mode
            self._touch()
            self.dirty = True

    def long_press(self):
        if self.menu_open:
            self._menu_close()
        else:
            self.set_mode(self.mode % 5 + 1)

    # ---- timing
    def tick(self):
        """Advance timers; returns True when the screen needs to be redrawn."""
        now = time.monotonic()
        self._pomo_tick(now)
        if self.menu_open and now - self.menu_touched > 4.0:
            self._menu_close()
        if self.overlay and now >= self.overlay[1]:
            self.overlay, self.dirty = None, True
        if self.dirty:
            return True
        if self.upload_frac is not None or self.menu_open or self.overlay:
            iv = 0.1
        elif self.mode == M_CLOCK:
            iv = 0.2
        elif self.mode == M_POMO:
            iv = 0.25 if self.pomo_state in (PS_RUN, PS_DONE) else 5.0
        elif self.mode == M_MEDIA:
            iv = 0.09 if self.playing else 5.0
        elif self.mode == M_TELEM:
            iv = 0.5
        else:
            iv = 0.04
        return now - self._last >= iv

    # ---- scenes
    def _scene_clock(self, g):
        t = time.localtime()
        g.circle(120, 120, 119, outline=C_DIM2)
        g.circle(120, 120, 118, outline=C_DIM)
        for i in range(60):
            big = i % 5 == 0
            p0, p1 = _pt(i * 6, 98 if big else 107), _pt(i * 6, 114)
            g.line(p0, p1, 3, C_ACC) if big else g.line(p0, p1, 1, C_DIM2)
        g.text(f"{DOW[(t.tm_wday + 1) % 7]} {t.tm_mday:02d} {MONTHS[t.tm_mon - 1]}", 120, 78, 15, C_GRAY)
        g.circle(120, 60, 3, fill=C_OK)
        g.text(f"{t.tm_hour:02d}:{t.tm_min:02d}", 120, 166, 26, C_TXT, bold=True)
        _hand(g, ((t.tm_hour % 12) + t.tm_min / 60.0) * 30.0, 52, 6, C_TXT)
        _hand(g, (t.tm_min + t.tm_sec / 60.0) * 6.0, 82, 4, C_ACC)
        _hand(g, t.tm_sec * 6.0, 94, 2, C_RED)
        g.circle(120, 120, 6, fill=C_TXT)
        g.circle(120, 120, 3, fill=C_RED)

    def _scene_pomo(self, g, now):
        total = self.pomo_minutes * 60.0
        rem = total if self.pomo_state == PS_IDLE else self.pomo_remain
        blink = self.pomo_state == PS_DONE and int(now * 1000 / 400) % 2 == 1
        col = {PS_RUN: C_ACC, PS_PAUSE: C_WARN, PS_DONE: C_RED}.get(self.pomo_state, C_OK)
        label = {PS_RUN: "FOCUS", PS_PAUSE: "PAUSED", PS_DONE: "DONE!"}.get(self.pomo_state, "READY")
        g.fill((90, 0, 0) if blink else C_BG)
        g.band(120, 120, 116, 102, 0, 360, C_DIM)
        f = rem / total if total else 0
        if f > 0.003:
            g.band(120, 120, 116, 102, 0, 360 * f, col)
        s = int(math.ceil(rem))
        g.text(label, 120, 64, 15, col)
        g.text(f"{s // 60:02d}:{s % 60:02d}", 120, 112, 50, C_TXT, bold=True)
        g.text("K1 start/pause", 120, 158, 15, C_GRAY)
        g.text("K2 reset", 120, 176, 15, C_GRAY)
        if self.pomo_state == PS_IDLE:
            g.text("turn dial: minutes", 120, 196, 13, C_GRAY)

    def _scene_media(self, g, now):
        g.gauge(120, 120, 116, 102, 0 if self.muted else self.vol / 100.0, C_ACC)
        g.text("MEDIA", 120, 50, 15, C_GRAY)
        if self.playing:
            g.rect(97, 72, 16, 46, C_TXT)
            g.rect(127, 72, 16, 46, C_TXT)
        else:
            g.poly([(100, 70), (100, 120), (146, 95)], C_TXT)
        for b in range(7):
            h = 6 + int(16 * (0.5 + 0.5 * math.sin(now * 1000 / 140.0 + b * 1.3))) if self.playing else 4
            g.rect(78 + b * 12, 154 - h, 8, h, C_ACC2 if self.playing else C_DIM2)
        g.text("MUTED" if self.muted else f"{self.vol}%", 120, 180, 26, C_RED if self.muted else C_TXT, bold=True)
        g.text("VOLUME", 120, 204, 15, C_GRAY)

    def _scene_telem(self, g):
        g.gauge(120, 120, 116, 103, self.cpu / 100.0, C_ACC)
        g.gauge(120, 120, 98, 85, self.ram / 100.0, C_ACC2)
        up = int(time.monotonic() - self.boot)
        g.text("HOST LIVE", 120, 62, 15, C_OK)
        g.text(f"CPU {self.cpu:.0f}%", 120, 98, 26, C_ACC, bold=True)
        g.text(f"RAM {self.ram:.0f}%", 120, 134, 26, C_ACC2, bold=True)
        g.text(f"app up {up // 3600:02d}:{(up // 60) % 60:02d}:{up % 60:02d}", 120, 170, 15, C_GRAY)

    def _scene_upload(self, g):
        f = self.upload_frac or 0.0
        g.gauge(120, 120, 116, 102, f, C_ACC2)
        g.text("UPLOADING GIF", 120, 84, 15, C_GRAY)
        g.text(f"{int(f * 100)}%", 120, 120, 26, C_TXT, bold=True)

    def _demo(self):
        if self._demo_cache is None:
            k, fr = 2.0, []
            for i in range(DEMO_FRAMES):
                im = Image.new("RGB", (int(240 * k), int(240 * k)), C_BG)
                d, cx, R, sw = ImageDraw.Draw(im), 119.5 * k, 118 * k, 360.0 * i / DEMO_FRAMES
                for j in range(13, -1, -1):                              # fading sweep behind the beam
                    lv = 1.0 - j / 14.0
                    d.pieslice([cx - R, cx - R, cx + R, cx + R], sw - (j + 1) * 6 - 90, sw - j * 6 - 90,
                               fill=(0, 30 + int(190 * lv), 50 + int(200 * lv)))
                for rr in (R - 2 * k, R * 0.66, R * 0.33):
                    d.ellipse([cx - rr, cx - rr, cx + rr, cx + rr], outline=(60, 66, 80), width=max(1, int(k)))
                bx, by = _pt(sw * 2.0, R * 0.55, cx, cx)
                d.ellipse([bx - 4 * k, by - 4 * k, bx + 4 * k, by + 4 * k], fill=C_ACC2)
                d.ellipse([cx - 3 * k, cx - 3 * k, cx + 3 * k, cx + 3 * k], fill=C_TXT)
                m = Image.new("L", im.size, 0)
                ImageDraw.Draw(m).ellipse([cx - R, cx - R, cx + R, cx + R], fill=255)
                fr.append(Image.composite(im, Image.new("RGB", im.size, C_BG), m))
            self._demo_cache = fr
        return self._demo_cache

    def _scene_gif(self, g, now):
        frames, durs = (self.gif_frames, self.gif_durs) if self.gif_frames else (self._demo(), [90] * DEMO_FRAMES)
        total = float(sum(durs)) or 1.0
        t, acc, f = (now * 1000.0) % total, 0.0, frames[-1]
        for fr, du in zip(frames, durs):
            acc += du
            if t < acc:
                f = fr
                break
        if f.size != g.img.size:
            f = f.resize(g.img.size, RESAMPLE)
        g.img.paste(f)
        g.d = ImageDraw.Draw(g.img)

    def _scene_menu(self, g):
        g.img = ImageEnhance.Brightness(g.img).enhance(0.5)
        g.d = ImageDraw.Draw(g.img)
        labels, full = ["BRIGHT", "VOL", "MODE", "EXIT"], ["BRIGHTNESS", "VOLUME", "DISPLAY MODE", "EXIT"]
        for i in range(4):
            c, sel = i * 90.0, i == self.menu_sel
            g.band(120, 120, 118, 72, c - 42, c + 42, (C_WARN if self.menu_edit else C_ACC) if sel else C_DIM2)
            x, y = _pt(c, 95)
            g.text(labels[i], x, y, 15, C_BG if sel else C_TXT)
        g.circle(120, 120, 66, fill=C_BG, outline=C_DIM2)
        g.text(full[self.menu_sel], 120, 82, 15, C_GRAY)
        vc = C_WARN if self.menu_edit else C_TXT
        if self.menu_sel == 0:
            g.text(f"{self.brightness * 100 // 255}%", 120, 118, 26, vc, bold=True)
        elif self.menu_sel == 1:
            g.text("MUTE" if self.muted else f"{self.vol}%", 120, 118, 26, vc, bold=True)
        elif self.menu_sel == 2:
            m = self.menu_mode if self.menu_edit else self.mode
            g.text(f"{m} / 5", 120, 112, 26, vc, bold=True)
            g.text(MODE_NAMES[m], 120, 136, 15, C_ACC)
        else:
            g.text("CLOSE", 120, 118, 26, vc, bold=True)
        g.text("CLICK = OK" if self.menu_edit else "CLICK = ENTER", 120, 158, 11, C_GRAY)

    def _draw_overlay(self, g, text):
        f = _font(int(13 * SS), True)
        w = min(150.0, g.d.textlength(text, font=f) / SS + 24)
        g.d.rounded_rectangle([(120 - w / 2) * SS, 196 * SS, (120 + w / 2) * SS, 222 * SS], radius=12 * SS,
                              fill=(18, 22, 30), outline=C_ACC, width=SS)
        g.text(text, 120, 209, 13, C_TXT, bold=True)

    def render(self):
        """Returns the current virtual screen as a DISPxDISP RGB image."""
        now = time.monotonic()
        g = Gfx()
        if self.upload_frac is not None:
            self._scene_upload(g)
        else:
            if self.menu_open and self.mode == M_GIF:
                g.fill(C_BG)
            elif self.mode == M_CLOCK:
                self._scene_clock(g)
            elif self.mode == M_POMO:
                self._scene_pomo(g, now)
            elif self.mode == M_MEDIA:
                self._scene_media(g, now)
            elif self.mode == M_TELEM:
                self._scene_telem(g)
            else:
                self._scene_gif(g, now)
            if self.menu_open:
                self._scene_menu(g)
        if self.overlay and now < self.overlay[1]:
            self._draw_overlay(g, self.overlay[0])
        img = g.img.resize((DISP, DISP), RESAMPLE)
        k = 0.1 + 0.9 * (max(5, self.brightness) / 255.0)       # backlight PWM
        if k < 0.99:
            img = ImageEnhance.Brightness(img).enhance(k)
        self.dirty, self._last = False, now
        return img


# ============================================================================ virtual pad: clickable canvas
VP_W, VP_H = 560, 640
SCR_C = (280, 200)
BOX = 280
KEY_XS, KEY_Y, KEY_W, KEY_H = [76, 178, 280, 382, 484], 440, 92, 72
ENC_C, ENC_R = (280, 552), 36
ARROW_L, ARROW_R, ARROW_RAD = (188, 552), (372, 552), 26
LONG_MS = 650
KEY_FILL, KEY_ON, KEY_EDGE = "#2e323a", "#566174", "#525966"


def make_body_image():
    k = 2
    im = Image.new("RGB", (VP_W * k, VP_H * k), (15, 17, 20))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([14 * k, 10 * k, (VP_W - 14) * k, (VP_H - 10) * k], radius=36 * k, fill=(31, 34, 40),
                        outline=(58, 63, 73), width=2 * k)
    d.rounded_rectangle([20 * k, 16 * k, (VP_W - 20) * k, (VP_H - 16) * k], radius=31 * k, outline=(40, 44, 52), width=k)
    cx, cy = SCR_C[0] * k, SCR_C[1] * k
    for r, fill, out, w in ((140, (22, 24, 29), (52, 57, 66), 2), (130, (5, 6, 8), (86, 92, 106), 3)):
        d.ellipse([cx - r * k, cy - r * k, cx + r * k, cy + r * k], fill=fill, outline=out, width=w * k)
    for x in KEY_XS:
        d.rounded_rectangle([(x - KEY_W / 2 - 5) * k, (KEY_Y - KEY_H / 2 - 5) * k, (x + KEY_W / 2 + 5) * k,
                             (KEY_Y + KEY_H / 2 + 5) * k], radius=16 * k, fill=(22, 24, 29))
    for (x, y), r in ((ENC_C, 50), (ARROW_L, ARROW_RAD + 5), (ARROW_R, ARROW_RAD + 5)):
        d.ellipse([(x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k], fill=(22, 24, 29), outline=(52, 57, 66), width=2 * k)
    ex, ey = ENC_C[0] * k, ENC_C[1] * k
    for i in range(24):
        a = math.radians(i * 15)
        d.line([ex + math.sin(a) * 43 * k, ey - math.cos(a) * 43 * k, ex + math.sin(a) * 48 * k,
                ey - math.cos(a) * 48 * k], fill=(70, 76, 90), width=k)
    return im.resize((VP_W, VP_H), RESAMPLE)


class PadView:
    """The device picture: live screen, 5 keys, encoder (knob + turn buttons). Click, scroll, drag and drop."""

    def __init__(self, parent, app):
        self.app = app
        self.c = tk.Canvas(parent, width=VP_W, height=VP_H, bg="#0f1114", highlightthickness=0)
        self.body = make_body_image()
        self._bg_tk = ImageTk.PhotoImage(self.body)
        self.c.create_image(0, 0, anchor="nw", image=self._bg_tk)
        self.box_xy = (SCR_C[0] - BOX // 2, SCR_C[1] - BOX // 2)
        self._box_bg = self.body.crop((self.box_xy[0], self.box_xy[1], self.box_xy[0] + BOX, self.box_xy[1] + BOX))
        m = Image.new("L", (DISP * 4, DISP * 4), 0)
        ImageDraw.Draw(m).ellipse([0, 0, DISP * 4 - 1, DISP * 4 - 1], fill=255)
        self._mask = m.resize((DISP, DISP), RESAMPLE)
        self._screen_item = self.c.create_image(self.box_xy[0], self.box_xy[1], anchor="nw")
        self._screen_tk = None
        self.regions, self.items = [], {}
        self._down, self._drop, self.knob_deg = None, None, 0
        self._build_items()
        self.c.bind("<ButtonPress-1>", self._press)
        self.c.bind("<B1-Motion>", self._motion)
        self.c.bind("<ButtonRelease-1>", self._release)
        self.c.bind("<MouseWheel>", self._wheel)
        self.c.bind("<Button-4>", lambda e: self._wheel(e, 1))
        self.c.bind("<Button-5>", lambda e: self._wheel(e, -1))
        for seq in ("<Button-3>", "<Control-Button-1>") + (("<Button-2>",) if platform.system() == "Darwin" else ()):
            self.c.bind(seq, self._context)

    # ---- construction
    def _rrect(self, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1,
               x0, y1 - r, x0, y0 + r, x0, y0]
        return self.c.create_polygon(pts, smooth=True, **kw)

    def _build_items(self):
        c = self.c
        for slot, x in enumerate(KEY_XS, start=1):
            x0, y0, x1, y1 = x - KEY_W / 2, KEY_Y - KEY_H / 2, x + KEY_W / 2, KEY_Y + KEY_H / 2
            self.regions.append({"kind": "key", "slot": slot, "shape": "rect", "geom": (x0, y0, x1, y1)})
            self.items[slot] = {
                "rect": self._rrect(x0, y0, x1, y1, 12, fill=KEY_FILL, outline=KEY_EDGE, width=2),
                "stripe": c.create_rectangle(x0 + 12, y0 + 7, x1 - 12, y0 + 10, fill="#6b7280", outline=""),
                "num": c.create_text(x0 + 11, y1 - 9, text=f"K{slot}", anchor="w", fill="#7d8594",
                                     font=("TkDefaultFont", 8, "bold")),
                "txt": c.create_text((x0 + x1) / 2, (y0 + y1) / 2 + 2, text="", width=KEY_W - 14, justify="center",
                                     fill="#e8ecf2", font=("TkDefaultFont", 9, "bold")),
                "dot": c.create_oval(x1 - 17, y0 + 13, x1 - 10, y0 + 20, fill="#ff9f1a", outline="", state="hidden")}
        for slot, (x, y), direction in ((7, ARROW_L, -1), (6, ARROW_R, 1)):
            self.regions.append({"kind": "turn", "slot": slot, "dir": direction, "shape": "circle",
                                 "geom": (x, y, ARROW_RAD)})
            s = 9 * direction
            self.items[slot] = {
                "rect": c.create_oval(x - ARROW_RAD, y - ARROW_RAD, x + ARROW_RAD, y + ARROW_RAD, fill=KEY_FILL,
                                      outline=KEY_EDGE, width=2),
                "arrow": c.create_polygon(x + s, y, x - s * 0.6, y - 10, x - s * 0.6, y + 10, fill="#9aa3b5",
                                          outline=""),
                "txt": c.create_text(x, y + ARROW_RAD + 28, text="", width=104, justify="center", fill="#cfd5e1",
                                     font=("TkDefaultFont", 9, "bold")),
                "num": c.create_text(x, y + ARROW_RAD + 13, text="turn right" if direction > 0 else "turn left",
                                     fill="#7d8594", font=("TkDefaultFont", 8)),
                "dot": c.create_oval(x + 14, y - 22, x + 21, y - 15, fill="#ff9f1a", outline="", state="hidden")}
        self.regions.append({"kind": "knob", "slot": 0, "shape": "circle", "geom": (ENC_C[0], ENC_C[1], ENC_R)})
        x, y = ENC_C
        self.knob = c.create_oval(x - ENC_R, y - ENC_R, x + ENC_R, y + ENC_R, fill="#3a3f49", outline="#7a8394", width=2)
        self.knob_ind = c.create_line(x, y, x, y - ENC_R + 6, fill="#00d2ff", width=4, capstyle="round")
        self.knob_cap = c.create_oval(x - 9, y - 9, x + 9, y + 9, fill="#2a2e36", outline="#566174")
        c.create_text(x, y - 60, text="click: menu     hold: next mode", fill="#7d8594", font=("TkDefaultFont", 8))

    # ---- geometry / hit testing
    def hit(self, x, y):
        for r in self.regions:
            g = r["geom"]
            if r["shape"] == "rect":
                if g[0] <= x <= g[2] and g[1] <= y <= g[3]:
                    return r
            elif (x - g[0]) ** 2 + (y - g[1]) ** 2 <= g[2] ** 2:
                return r
        return None

    def slot_at_root(self, xr, yr):
        r = self.hit(xr - self.c.winfo_rootx(), yr - self.c.winfo_rooty())
        return r["slot"] if r and r["kind"] in ("key", "turn") else None

    def region_for_slot(self, slot):
        return next(r for r in self.regions if r["kind"] in ("key", "turn") and r["slot"] == slot)

    # ---- drawing
    def set_screen(self, img):
        frame = self._box_bg.copy()
        off = (BOX - DISP) // 2
        frame.paste(img, (off, off), self._mask)
        self._screen_tk = ImageTk.PhotoImage(frame)
        self.c.itemconfig(self._screen_item, image=self._screen_tk)

    def refresh(self):
        for slot in range(1, 8):
            m = self.app.cfg["map"][str(slot)]
            unassigned = m["cat"] == "Other"
            it = self.items[slot]
            self.c.itemconfig(it["txt"], text="-" if unassigned else m["action"],
                              fill="#6b7280" if unassigned else "#e8ecf2")
            if "stripe" in it:
                self.c.itemconfig(it["stripe"], fill=CAT_COLORS.get(m["cat"], "#6b7280"))
        self.refresh_pending()

    def refresh_pending(self):
        for slot in range(1, 8):
            self.c.itemconfig(self.items[slot]["dot"], state="normal" if slot in self.app.pending_slots else "hidden")

    def flash(self, slot):
        if slot in self.items:
            it = self.items[slot]["rect"]
            self.c.itemconfig(it, fill=KEY_ON)
            self.c.after(140, lambda: self.c.itemconfig(it, fill=KEY_FILL))

    def spin(self, steps):
        self.knob_deg = (self.knob_deg + 15 * steps) % 360
        a = math.radians(self.knob_deg)
        x, y = ENC_C
        self.c.coords(self.knob_ind, x, y, x + math.sin(a) * (ENC_R - 6), y - math.cos(a) * (ENC_R - 6))

    def set_drop(self, slot):
        if slot == self._drop:
            return
        self._drop = slot
        self.c.delete("drophl")
        if slot is None:
            return
        r = self.region_for_slot(slot)
        if r["shape"] == "rect":
            x0, y0, x1, y1 = r["geom"]
            self.c.create_rectangle(x0 - 4, y0 - 4, x1 + 4, y1 + 4, outline="#00d2ff", width=3, tags="drophl")
        else:
            x, y, rad = r["geom"]
            self.c.create_oval(x - rad - 4, y - rad - 4, x + rad + 4, y + rad + 4, outline="#00d2ff", width=3, tags="drophl")

    # ---- mouse
    def _press(self, e):
        r = self.hit(e.x, e.y)
        self._down = None
        if not r:
            return
        self._down = {"r": r, "drag": False, "long": False, "job": None}
        if r["kind"] == "knob":
            self._down["job"] = self.c.after(LONG_MS, self._long)
        elif self.app.cfg["map"][str(r["slot"])]["cat"] != "Other":
            self.app.dnd.arm({"slot": r["slot"]}, self.app.cfg["map"][str(r["slot"])]["action"], e.x_root, e.y_root)

    def _long(self):
        if self._down:
            self._down["long"], self._down["job"] = True, None
            self.app.pad.long_press()

    def _motion(self, e):
        if self._down and self._down["r"]["kind"] != "knob" and self.app.dnd.motion(e.x_root, e.y_root):
            self._down["drag"] = True

    def _release(self, e):
        d, self._down = self._down, None
        if not d:
            return
        if d["job"]:
            self.c.after_cancel(d["job"])
        if d["drag"]:
            self.app.dnd.release(e.x_root, e.y_root)
            return
        self.app.dnd.cancel()
        r = self.hit(e.x, e.y)
        if r is not d["r"] or d["long"]:
            return
        if r["kind"] == "key":
            self.app.vp_key(r["slot"])
        elif r["kind"] == "turn":
            self.app.vp_turn(r["dir"])
        else:
            self.app.pad.click()

    def _wheel(self, e, direction=None):
        r = self.hit(e.x, e.y)
        if r and r["kind"] in ("knob", "turn"):
            self.app.vp_turn(direction if direction is not None else (1 if e.delta > 0 else -1))

    def _context(self, e):
        r = self.hit(e.x, e.y)
        if r and r["kind"] in ("key", "turn"):
            self.app.slot_menu(r["slot"], e.x_root, e.y_root)
        elif r:
            self.app.pad.long_press()


class DnD:
    """Minimal drag and drop: a floating ghost label follows the pointer; drop targets come from PadView."""

    def __init__(self, app):
        self.app, self.payload, self.label, self.origin, self.active, self.ghost = app, None, "", (0, 0), False, None

    def arm(self, payload, label, x, y):
        self.cancel()
        self.payload, self.label, self.origin = payload, label, (x, y)

    def motion(self, x, y):
        if not self.payload:
            return False
        if not self.active:
            if abs(x - self.origin[0]) + abs(y - self.origin[1]) < 8:
                return False
            self.active = True
            self.ghost = tk.Toplevel(self.app)
            self.ghost.overrideredirect(True)
            try:
                self.ghost.attributes("-topmost", True)
                self.ghost.attributes("-alpha", 0.92)
            except tk.TclError:
                pass
            tk.Label(self.ghost, text="  " + self.label + "  ", bg="#1f6aa5", fg="white", padx=6, pady=4,
                     font=("TkDefaultFont", 10, "bold")).pack()
        self.ghost.geometry(f"+{x + 14}+{y + 10}")
        self.app.padview.set_drop(self.app.padview.slot_at_root(x, y))
        return True

    def release(self, x, y):
        was, payload = self.active, self.payload
        target = self.app.padview.slot_at_root(x, y) if was else None
        self.cancel()
        if was and target:
            self.app.drop_assign(target, payload)
        return was

    def cancel(self):
        if self.ghost is not None:
            self.ghost.destroy()
        self.ghost, self.payload, self.active = None, None, False
        self.app.padview.set_drop(None)


# ============================================================================ plug-in notification
def play_event_sound(kind, widget=None):
    """Windows 'device connected / disconnected' sound (falls back to the bell elsewhere)."""
    try:
        import winsound
        alias = {"ok": "DeviceConnect", "off": "DeviceDisconnect"}.get(kind, "SystemExclamation")
        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        try:
            widget and widget.bell()
        except Exception:
            pass


class Toast:
    """Small borderless always-on-top popup in the bottom-right screen corner, like a system notification."""
    LIVE = []

    def __init__(self, app, title, text, kind="ok", secs=5.0):
        accent = {"ok": "#4cd97b", "warn": "#ffb454", "off": "#8a93a5"}.get(kind, "#4cd97b")
        self.win = w = tk.Toplevel(app)
        w.overrideredirect(True)
        try:
            w.attributes("-topmost", True)
        except tk.TclError:
            pass
        outer = tk.Frame(w, bg=accent)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg="#1d2026")
        inner.pack(fill="both", expand=True, padx=(5, 1), pady=1)
        tk.Label(inner, text="\u25CF", fg=accent, bg="#1d2026", font=("TkDefaultFont", 20)).grid(row=0, column=0, rowspan=2, padx=(12, 6), pady=10)
        tk.Label(inner, text=title, fg="#ffffff", bg="#1d2026", font=("TkDefaultFont", 12, "bold"), anchor="w").grid(
            row=0, column=1, sticky="w", padx=(0, 16), pady=(10, 0))
        tk.Label(inner, text=text, fg="#aab2c0", bg="#1d2026", font=("TkDefaultFont", 9), anchor="w", justify="left",
                 wraplength=250).grid(row=1, column=1, sticky="w", padx=(0, 16), pady=(0, 10))
        w.update_idletasks()
        width, height = max(330, w.winfo_reqwidth()), w.winfo_reqheight()
        sx, sy = w.winfo_screenwidth(), w.winfo_screenheight()
        self.h = height
        stacked = sum(t.h + 10 for t in Toast.LIVE)                 # toasts have different heights: stack by real size
        w.geometry(f"{width}x{height}+{sx - width - 22}+{sy - height - 70 - stacked}")
        Toast.LIVE.append(self)
        for wd in (w, outer, inner) + tuple(inner.winfo_children()):
            wd.bind("<Button-1>", lambda e: self.close())
        self._job = w.after(int(secs * 1000), self.close)

    def close(self):
        if self in Toast.LIVE:
            Toast.LIVE.remove(self)
        try:
            self.win.destroy()
        except tk.TclError:
            pass


# ============================================================================ GUI
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.title(APP_NAME)
        self.geometry("1240x820")
        self.minsize(1160, 760)
        self.cfg = load_config()
        self.ui_q, self.closing, self.auto_flag = queue.Queue(), False, True
        self.connect_lock = threading.Lock()
        self.dev = Device(lambda: self.post(self._on_disconnected))
        self.macro_steps, self.gif_frames, self.gif_durs, self.gif_data = [], None, [], None
        self._pv_idx, self._pv_job, self._pv_img, self._bright_job = 0, None, None, None
        self.pending_slots, self._warned, self._was_connected = set(), set(), False
        self._fails = {}
        self.host, self.host_q, self.host_stop = HostInput(), queue.Queue(), threading.Event()
        self.focus = None                                       # Windows: remembers the program you were working in
        if platform.system() == "Windows":
            try:
                self.focus = WinFocus(Win32Api())
            except Exception:
                self.focus = None
        self.dnd = DnD(self)
        self.pad = VirtualPad(self.exec_slot, self.exec_media, self.on_pad_change)
        self.pad.mode, self.pad.brightness = int(self.cfg["twin_mode"]), int(self.cfg["twin_bright"])
        self._build()
        self.padview.refresh()
        self.refresh_library()
        self.recompute_pending()
        self.after(50, self._pump)
        self.after(100, self._twin_loop)
        if self.focus:
            self.after(150, self._focus_loop)
        threading.Thread(target=self._host_worker, daemon=True).start()
        threading.Thread(target=self._telemetry_loop, daemon=True).start()
        threading.Thread(target=self._monitor_loop, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- thread plumbing
    def post(self, fn):
        self.ui_q.put(fn)

    def _pump(self):
        try:
            while True:
                fn = self.ui_q.get_nowait()
                try:
                    fn()
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        if not self.closing:
            self.after(50, self._pump)

    def bg(self, fn, ok=None, label="Error", fail=None):
        def run():
            try:
                res = fn()
            except Exception as e:
                self.post(lambda e=e: self.set_status(f"{label}: {e}", error=True))
                if fail:
                    self.post(fail)
            else:
                if ok:
                    self.post(lambda: ok(res))
        threading.Thread(target=run, daemon=True).start()

    def set_status(self, text, error=False):
        self.status.configure(text=text, text_color="#ff6b6b" if error else "#9aa0a6")
        self._log(text)

    def _log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", time.strftime("%H:%M:%S  ") + text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _on_close(self):
        self.closing = True
        self.host_stop.set()
        self.host_q.put(None)
        self.dnd.cancel()
        self.dev.disconnect()
        self.destroy()

    # ---------------------------------------------------------------- background loops
    def _telemetry_loop(self):
        psutil.cpu_percent(None)
        nxt, last_sync = time.monotonic() + 1.0, 0.0
        while not self.closing:
            time.sleep(max(0.0, nxt - time.monotonic()))
            nxt += 1.0
            cpu, ram = psutil.cpu_percent(None), psutil.virtual_memory().percent
            self.post(lambda c=cpu, r=ram: self._meters(c, r))
            if self.dev.connected and not self.dev.busy:
                try:
                    self.dev.send({"cmd": "stats", "cpu": round(cpu), "ram": round(ram)})
                    if time.time() - last_sync > 60:
                        self.dev.request(time_msg())
                        last_sync = time.time()
                except DeviceError:
                    pass

    def notify(self, title, text, kind="ok"):
        if not self.cfg.get("notify", True) or self.closing:
            return
        play_event_sound(kind, self)
        Toast(self, title, text, kind)

    def _monitor_loop(self):
        while not self.closing:
            ports = candidate_ports()
            gone = set(self._fails) | self._warned
            for p in gone - set(ports):                      # unplugged: forget, so the next plug-in starts fresh
                self._fails.pop(p, None)
                self._warned.discard(p)
            if self.auto_flag and not self.dev.connected:
                for port in ports:
                    if self._try_connect(port):
                        break
            time.sleep(1.0)

    def _try_connect(self, port):
        if not self.connect_lock.acquire(blocking=False):
            return False
        try:
            info = self.dev.connect(port)
        except (DeviceError, serial.SerialException, OSError) as e:
            self._fails[port] = self._fails.get(port, 0) + 1
            msg = str(e).lower()
            busy = isinstance(e, PermissionError) or any(w in msg for w in ("denied", "busy", "permission", "in use"))
            self.post(lambda e=e: self._log(f"connect {port} failed: {e}"))
            if port not in self._warned and (busy or self._fails[port] >= 3):   # 3 tries = the pad had time to boot
                self._warned.add(port)
                if busy:
                    self.post(lambda: self.notify(f"{port} is in use by another program",
                              "Close the Arduino / PlatformIO serial monitor - DeskCompanion connects by itself afterwards.", "warn"))
                else:
                    self.post(lambda: self.notify(f"USB device found on {port}",
                              "...but it does not answer as DeskCompanion. Re-flash the firmware, then replug it.", "warn"))
            return False
        finally:
            self.connect_lock.release()
        self._fails.pop(port, None)
        self.post(lambda: self._on_connected(info))
        return True

    # ---------------------------------------------------------------- UI construction
    def _build(self):
        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        for name in ("Virtual Pad", "Dashboard", "Macro Creator", "GIF Upload", "Device"):
            self.tabs.add(name)
        self.status = ctk.CTkLabel(self, text="Searching for the pad...", anchor="w", text_color="#9aa0a6")
        self.status.pack(fill="x", padx=14, pady=(4, 8))
        self._build_device_tab(self.tabs.tab("Device"))     # first: creates log_box
        self._build_virtual(self.tabs.tab("Virtual Pad"))
        self._build_dashboard(self.tabs.tab("Dashboard"))
        self._build_macro(self.tabs.tab("Macro Creator"))
        self._build_gif(self.tabs.tab("GIF Upload"))
        self.refresh_ports()

    def _title(self, parent, text, row=0):
        ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=row, column=0, columnspan=6, sticky="w", padx=12, pady=(10, 4))

    # ---- virtual pad tab
    def _build_virtual(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        bold = ctk.CTkFont(size=15, weight="bold")
        left = ctk.CTkFrame(tab, width=270)
        left.grid(row=0, column=0, sticky="ns", padx=(6, 4), pady=6)
        left.grid_propagate(False)
        center = ctk.CTkFrame(tab, fg_color="transparent")
        center.grid(row=0, column=1, sticky="n", pady=2)
        right = ctk.CTkFrame(tab, width=310)
        right.grid(row=0, column=2, sticky="ns", padx=(4, 6), pady=6)
        right.grid_propagate(False)

        # -- action library: drag from here onto the pad
        ctk.CTkLabel(left, text="Action library", font=bold).pack(anchor="w", padx=12, pady=(10, 2))
        ctk.CTkLabel(left, text="Drag an action onto a key or an encoder arrow. Double-click for a menu.",
                     text_color="#9aa0a6", wraplength=240, justify="left").pack(anchor="w", padx=12)
        self.search_entry = ctk.CTkEntry(left, placeholder_text="Search actions...")
        self.search_entry.pack(fill="x", padx=12, pady=8)
        self.search_entry.bind("<KeyRelease>", lambda e: self.refresh_library())
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Pad.Treeview", background="#1c1f24", fieldbackground="#1c1f24", foreground="#e6e9ef",
                        rowheight=26, borderwidth=0, relief="flat")
        style.map("Pad.Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "#ffffff")])
        style.layout("Pad.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        box = ctk.CTkFrame(left, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=(12, 6), pady=(0, 10))
        self.tree = ttk.Treeview(box, style="Pad.Treeview", show="tree", selectmode="browse")
        sb = ctk.CTkScrollbar(box, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree_items, self.tree_cat, self._open_cats = {}, {}, {"Editing"}
        self.tree.bind("<ButtonPress-1>", self._tree_press)
        self.tree.bind("<B1-Motion>", self._tree_motion)
        self.tree.bind("<ButtonRelease-1>", self._tree_release)
        self.tree.bind("<Double-1>", self._tree_double)

        # -- the virtual device
        self.padview = PadView(center, self)
        self.padview.c.pack(padx=6)

        # -- upload / test / screen controls
        ctk.CTkLabel(right, text="Upload to the physical pad", font=bold).pack(anchor="w", padx=12, pady=(10, 2))
        self.upload_btn2 = ctk.CTkButton(right, text="Upload to pad", height=36, command=self.upload_all)
        self.upload_btn2.pack(fill="x", padx=12, pady=4)
        self.autoup_var = tk.BooleanVar(value=False)
        ctk.CTkSwitch(right, text="Upload every change immediately", variable=self.autoup_var).pack(anchor="w", padx=12, pady=2)
        ctk.CTkLabel(right, text="Orange dots on the pad mark changes the physical device does not have yet.",
                     text_color="#9aa0a6", wraplength=280, justify="left").pack(anchor="w", padx=12)
        ctk.CTkButton(right, text="Reset all keys to defaults", fg_color="#555", command=self.reset_defaults).pack(
            fill="x", padx=12, pady=(6, 2))

        ctk.CTkLabel(right, text="Test on this PC", font=bold).pack(anchor="w", padx=12, pady=(12, 2))
        self.live_var = tk.BooleanVar(value=bool(self.cfg.get("live_test", True)) and self.host.available)
        sw = ctk.CTkSwitch(right, text="Run actions for real (live test)", variable=self.live_var, command=self._live_toggled)
        sw.pack(anchor="w", padx=12, pady=2)
        if not self.host.available:
            sw.configure(state="disabled")
        self.vp_hint = ctk.CTkLabel(right, text="", wraplength=280, justify="left")
        self.vp_hint.pack(anchor="w", padx=12)
        self._update_live_hint()
        row = ctk.CTkFrame(right, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(row, text="Delay before sending").pack(side="left")
        self.delay_var = tk.StringVar(value=f"{int(self.cfg.get('test_delay', 0))} s")
        ctk.CTkOptionMenu(row, values=["0 s", "1 s", "2 s", "3 s", "5 s"], variable=self.delay_var, width=80,
                          command=lambda v: self.cfg.__setitem__("test_delay", int(v.split()[0]))).pack(side="right")
        self.top_var = tk.BooleanVar(value=False)
        ctk.CTkSwitch(right, text="Keep this window on top", variable=self.top_var,
                      command=lambda: self.attributes("-topmost", self.top_var.get())).pack(anchor="w", padx=12, pady=2)
        ctk.CTkLabel(right, text="Sandbox: click inside, then press virtual keys", text_color="#9aa0a6").pack(
            anchor="w", padx=12, pady=(6, 0))
        self.sandbox = ctk.CTkTextbox(right, height=64)
        self.sandbox.pack(fill="x", padx=12, pady=(2, 4))
        ctk.CTkLabel(right, text="Test log", text_color="#9aa0a6").pack(anchor="w", padx=12)
        self.vp_log_box = ctk.CTkTextbox(right, height=96, state="disabled")
        self.vp_log_box.pack(fill="x", padx=12, pady=(0, 4))

        ctk.CTkLabel(right, text="Virtual screen", font=bold).pack(anchor="w", padx=12, pady=(8, 2))
        row = ctk.CTkFrame(right, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=2)
        ctk.CTkLabel(row, text="Mode").pack(side="left")
        self.vp_mode_var = tk.StringVar(value=MODE_CHOICES[self.pad.mode - 1])
        ctk.CTkOptionMenu(row, values=MODE_CHOICES, variable=self.vp_mode_var, width=150,
                          command=lambda v: self.pad.set_mode(int(v[0]))).pack(side="right")
        row = ctk.CTkFrame(right, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=2)
        ctk.CTkLabel(row, text="Brightness").pack(side="left")
        self.vp_bright = ctk.CTkSlider(row, from_=5, to=255, number_of_steps=50, width=150,
                                       command=lambda v: self.pad.set_brightness(int(v)))
        self.vp_bright.set(self.pad.brightness)
        self.vp_bright.pack(side="right")

    def refresh_library(self):
        if not hasattr(self, "tree"):
            return
        q = self.search_entry.get().strip().lower()
        if not q:                                           # remember which categories were expanded
            for iid, cat in list(self.tree_cat.items()):
                if self.tree.exists(iid):
                    (self._open_cats.add if self.tree.item(iid, "open") else self._open_cats.discard)(cat)
        self.tree.delete(*self.tree.get_children())
        self.tree_items, self.tree_cat = {}, {}
        for cat in self.categories():
            names = self.names_for(cat)
            if q:
                names = [n for n in names if q in n.lower() or q in cat.lower()]
            if not names:
                continue
            parent = self.tree.insert("", "end", text=f" {cat}  ({len(names)})", open=bool(q) or cat in self._open_cats)
            self.tree_cat[parent] = cat
            for n in names:
                self.tree_items[self.tree.insert(parent, "end", text="    " + n)] = {"cat": cat, "action": n}

    def _tree_press(self, e):
        it = self.tree_items.get(self.tree.identify_row(e.y))
        if it:
            self.dnd.arm(dict(it), it["action"], e.x_root, e.y_root)

    def _tree_motion(self, e):
        if self.dnd.payload and "slot" not in self.dnd.payload and self.dnd.motion(e.x_root, e.y_root):
            return "break"

    def _tree_release(self, e):
        self.dnd.release(e.x_root, e.y_root)

    def _tree_double(self, e):
        it = self.tree_items.get(self.tree.identify_row(e.y))
        if not it:
            return
        menu = tk.Menu(self, tearoff=0)
        for s, lbl in SLOT_LABELS.items():
            menu.add_command(label=f"Assign to {lbl}", command=lambda s=s: self.drop_assign(s, dict(it)))
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    def slot_menu(self, slot, x_root, y_root):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=f"Test {SLOT_LABELS[slot]} now", command=lambda: self.exec_slot(slot))
        menu.add_command(label="Clear", command=lambda: self.drop_assign(slot, {"cat": "Other", "action": "Unassigned"}))
        sub = tk.Menu(menu, tearoff=0)
        for cat in self.categories():
            cm = tk.Menu(sub, tearoff=0)
            for n in self.names_for(cat):
                cm.add_command(label=n, command=lambda c=cat, n=n: self.drop_assign(slot, {"cat": c, "action": n}))
            sub.add_cascade(label=cat, menu=cm)
        menu.add_cascade(label="Assign", menu=sub)
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    # ---- virtual pad: assignment, staging, upload
    def drop_assign(self, slot, payload):
        if "slot" in payload:                                # key dragged onto another key = swap
            a, b = str(payload["slot"]), str(slot)
            if a == b:
                return
            self.cfg["map"][a], self.cfg["map"][b] = self.cfg["map"][b], self.cfg["map"][a]
            self.mapping_changed([int(a), int(b)])
            self.vp_log(f"swapped {SLOT_LABELS[int(a)]} and {SLOT_LABELS[slot]}")
        else:
            self.cfg["map"][str(slot)] = {"cat": payload["cat"], "action": payload["action"]}
            self.mapping_changed([slot])
            self.vp_log(f"{SLOT_LABELS[slot]} = {payload['action']}")

    def mapping_changed(self, slots):
        save_config(self.cfg)
        self.refresh_action_lists()
        self.padview.refresh()
        self.recompute_pending()
        if self.autoup_var.get() and self.dev.connected:
            self.upload_slots(list(slots))

    def recompute_pending(self):
        slots = set()
        for s in range(1, 8):
            m = self.cfg["map"][str(s)]
            if spec_json(resolve_spec(self.cfg, m["cat"], m["action"])) != self.cfg["pushed"].get(str(s)):
                slots.add(s)
        self.pending_slots = slots
        extra = int(self.cfg.get("pushed_mode") != self.pad.mode) + int(self.cfg.get("pushed_bright") != self.pad.brightness)
        n = len(slots) + extra
        if hasattr(self, "upload_btn2"):
            self.upload_btn2.configure(text="Upload to pad" if not n else f"Upload to pad  ({n} unsent)",
                                       fg_color="#d9822b" if n else "#1f6aa5")
            self.padview.refresh_pending()

    def _mark_pushed(self, slot, j):
        self.cfg["pushed"][str(slot)] = j
        save_config(self.cfg)
        self.recompute_pending()

    def _mark_display_pushed(self, mode, bright):
        self.cfg["pushed_mode"], self.cfg["pushed_bright"] = mode, bright
        save_config(self.cfg)
        self.recompute_pending()

    def upload_slots(self, slots):
        if not self.dev.connected:
            return
        jobs = []
        for s in slots:
            m = self.cfg["map"][str(s)]
            spec = resolve_spec(self.cfg, m["cat"], m["action"])
            if spec:
                jobs.append((s, spec))
        osv = self.cfg["os"]

        def work():
            self.dev.request({"cmd": "os", "val": osv})
            for s, spec in jobs:
                self.dev.request({"cmd": "remap", "key": s, "type": spec[0], "val": spec[1]})
                self.post(lambda s=s, j=spec_json(spec): self._mark_pushed(s, j))
        self.bg(work, lambda _: self.set_status("Sent to pad: " + ", ".join(SLOT_LABELS[x[0]] for x in jobs)),
                "Upload failed")

    def upload_all(self):
        if not self.dev.connected:
            return self.set_status("Connect the pad first (Device tab / USB cable)", error=True)
        jobs = []
        for s in range(1, 8):
            m = self.cfg["map"][str(s)]
            spec = resolve_spec(self.cfg, m["cat"], m["action"])
            if spec:
                jobs.append((s, spec))
        mode, bright, osv = self.pad.mode, self.pad.brightness, self.cfg["os"]
        self.upload_btn2.configure(state="disabled")

        def work():
            self.dev.request({"cmd": "os", "val": osv})
            self.dev.request(time_msg())
            self._push_layout()
            for s, spec in jobs:
                self.dev.request({"cmd": "remap", "key": s, "type": spec[0], "val": spec[1]})
                self.post(lambda s=s, j=spec_json(spec): self._mark_pushed(s, j))
            self.dev.request({"cmd": "brightness", "val": bright})
            self.dev.request({"cmd": "mode", "val": mode})
            self.post(lambda: self._mark_display_pushed(mode, bright))

        def done(_):
            self.upload_btn2.configure(state="normal")
            self.set_status("Uploaded to the pad: all 7 key slots, brightness and mode")
            self.vp_log("uploaded everything to the physical pad")
        self.bg(work, done, "Upload failed", fail=lambda: self.upload_btn2.configure(state="normal"))

    def on_pad_change(self, kind):
        if kind == "mode":
            v = MODE_CHOICES[self.pad.mode - 1]
            self.mode_var.set(v)
            if hasattr(self, "vp_mode_var"):
                self.vp_mode_var.set(v)
            self.cfg["twin_mode"] = self.pad.mode
        elif kind == "bright":
            self.bright.set(self.pad.brightness)
            if hasattr(self, "vp_bright"):
                self.vp_bright.set(self.pad.brightness)
            self.cfg["twin_bright"] = self.pad.brightness
        save_config(self.cfg)
        self.recompute_pending()
        if self.autoup_var.get() and self.dev.connected:
            cmd = {"cmd": "mode", "val": self.pad.mode} if kind == "mode" else {"cmd": "brightness", "val": self.pad.brightness}
            self.bg(lambda: self.dev.request(cmd), None, "Display sync failed")

    # ---- virtual pad: running / testing actions
    def host_cfg(self):
        return dict(self.cfg, os=HOST_OS)             # tests act on THIS computer, so resolve variants for its OS

    def vp_log(self, text):
        b = self.vp_log_box
        b.configure(state="normal")
        b.insert("end", time.strftime("%H:%M:%S  ") + text + "\n")
        if int(b.index("end-1c").split(".")[0]) > 200:
            b.delete("1.0", "50.0")
        b.see("end")
        b.configure(state="disabled")

    def vp_key(self, slot):
        self.padview.flash(slot)
        self.pad.key(slot - 1)

    def vp_turn(self, steps):
        self.padview.spin(steps)
        self.padview.flash(6 if steps > 0 else 7)
        self.pad.turn(steps)

    def _update_live_hint(self):
        if not self.host.available:
            txt, col = "Live test is not available here: " + (self.host.error or "no key injection backend") + \
                       ("  (pip install pynput)" if platform.system() != "Windows" else ""), "#ff6b6b"
        elif self.live_var.get():
            txt = ("LIVE: virtual keys press real keys on this PC. They go to the last program you used - focus is handed "
                   "back automatically. Click into the sandbox to test inside this app."
                   if self.focus else
                   "LIVE: virtual keys press real keys on the window that has the focus. Use the delay below or the sandbox.")
            col = "#4cd97b"
        else:
            txt, col = "DRY RUN: only the virtual screen reacts. Switch on to press real keys on this PC.", "#ffb454"
        self.vp_hint.configure(text=txt, text_color=col)

    def run_spec(self, spec, name, desc, label=""):
        """Play an action on the twin and - in live mode - on this PC."""
        self.pad.apply_spec(spec)
        self.pad.toast(name)
        if self.live_var.get() and self.host.available:
            self.run_live(spec, name, desc)
        else:
            self.vp_log(f"[dry run] {label}{name}  ({desc})")

    def exec_slot(self, slot):
        m = self.cfg["map"][str(slot)]
        spec = resolve_spec(self.host_cfg(), m["cat"], m["action"])
        if not spec or spec[0] == "none":
            self.vp_log(f"{SLOT_LABELS[slot]}: nothing assigned")
            return
        self.run_spec(spec, m["action"], describe_spec(spec), f"{SLOT_LABELS[slot]}: ")

    def exec_media(self, name):
        self.pad.apply_media(name)
        if self.live_var.get() and self.host.available:
            self.host_q.put((("media", name), "menu volume", name, False))

    def run_live(self, spec, name, desc):
        if name in RISKY and not messagebox.askyesno("Run on this PC?", f"'{name}' will really run on this computer now.\nContinue?"):
            return
        delay = int(self.delay_var.get().split()[0])
        if delay:
            self._countdown(delay, spec, name, desc)
        else:
            self._enqueue(spec, name, desc)

    def _countdown(self, n, spec, name, desc):
        if self.closing:
            return
        if n <= 0:
            self._enqueue(spec, name, desc)
            return
        self.pad.toast(f"{name} in {n}s", 1.1)
        self.after(1000, lambda: self._countdown(n - 1, spec, name, desc))

    def _enqueue(self, spec, name, desc):
        use_focus = False
        if self.focus and spec_needs_focus(spec):
            try:
                in_sandbox = self.focus_get() is self.sandbox._textbox
            except Exception:
                in_sandbox = False
            use_focus = (not in_sandbox) and self.focus.in_own_window()     # we have the focus: hand it back first
        self.host_q.put((spec, name, desc, use_focus))

    def _host_worker(self):
        while True:
            item = self.host_q.get()
            if item is None:
                return
            spec, name, desc, use_focus = item
            target = ""
            try:
                if use_focus:
                    target = self.focus.refocus()
                    if not target:
                        raise RuntimeError("no other program to send to - switch to one first, or click into the sandbox")
                self.host.run(spec, self.host_stop)
            except Exception as e:
                self.post(lambda e=e, n=name: self.vp_log(f"!! {n} failed: {e}"))
            else:
                self.post(lambda n=name, d=desc, t=target: self.vp_log(f"sent to {t or 'this PC'}: {n}  ({d})"))

    def _focus_loop(self):
        if self.closing or not self.focus:
            return
        try:
            self.focus.poll()
        except Exception:
            pass
        self.after(150, self._focus_loop)

    def _live_toggled(self):
        self.cfg["live_test"] = bool(self.live_var.get())
        save_config(self.cfg)
        self._update_live_hint()
        if self.live_var.get():
            self.vp_log("LIVE TEST ON - virtual keys now act on this computer")
            self.pad.toast("LIVE TEST ON", 1.6)
        else:
            self.vp_log("live test off (dry run)")

    # ---- macro creator: test buttons
    def test_spec(self, spec, label):
        self.run_spec(spec, label, describe_spec(spec), "Test: ")

    def test_combo(self):
        def go():
            keys = self._combo_keys()
            self.test_spec(("combo", keys), describe_spec(("combo", keys)))
        self._guard(go)

    def test_text(self):
        self._guard(lambda: self.test_spec(("text", self._text_value()), "Text snippet"))

    def test_seq(self):
        self._guard(lambda: self.test_spec(self._seq_spec(), "Sequence"))

    def _twin_loop(self):
        if self.closing:
            return
        try:
            need = self.pad.tick()
            if need and self.tabs.get() == "Virtual Pad":
                self.padview.set_screen(self.pad.render())
        except Exception:
            traceback.print_exc()
        self.after(40, self._twin_loop)

    # ---- dashboard
    def _build_dashboard(self, tab):
        box = ctk.CTkFrame(tab)
        box.pack(fill="x", padx=6, pady=(6, 4))
        self._title(box, "Connection")
        self.port_var, self.port_map = tk.StringVar(), {}
        self.port_box = ctk.CTkComboBox(box, variable=self.port_var, values=[""], width=330)
        self.port_box.grid(row=1, column=0, padx=12, pady=4)
        ctk.CTkButton(box, text="Rescan", width=80, command=self.refresh_ports).grid(row=1, column=1, padx=4)
        self.conn_btn = ctk.CTkButton(box, text="Connect", width=110, command=self.toggle_connect)
        self.conn_btn.grid(row=1, column=2, padx=4)
        self.auto_var = tk.BooleanVar(value=True)
        ctk.CTkSwitch(box, text="Auto-connect", variable=self.auto_var,
                      command=lambda: setattr(self, "auto_flag", self.auto_var.get())).grid(row=1, column=3, padx=12)
        self.conn_lbl = ctk.CTkLabel(box, text="Not connected", text_color="#ffb454")
        self.conn_lbl.grid(row=2, column=0, columnspan=6, sticky="w", padx=12, pady=(2, 10))

        met = ctk.CTkFrame(tab)
        met.pack(fill="x", padx=6, pady=4)
        self._title(met, "Live telemetry (sent to the pad every second)")
        self.cpu_bar, self.ram_bar = ctk.CTkProgressBar(met, width=300), ctk.CTkProgressBar(met, width=300)
        self.cpu_lbl, self.ram_lbl = ctk.CTkLabel(met, text="CPU  0%", width=80), ctk.CTkLabel(met, text="RAM  0%", width=80)
        for r, (lbl, bar) in enumerate(((self.cpu_lbl, self.cpu_bar), (self.ram_lbl, self.ram_bar)), start=1):
            lbl.grid(row=r, column=0, padx=12, pady=3)
            bar.grid(row=r, column=1, padx=6, pady=3)
            bar.set(0)

        mp = ctk.CTkFrame(tab)
        mp.pack(fill="both", expand=True, padx=6, pady=4)
        self._title(mp, "Key mapping (changes are sent to the pad immediately and stored in its flash)")
        self.rows = {}
        for i, slot in enumerate(SLOT_LABELS, start=1):
            ctk.CTkLabel(mp, text=SLOT_LABELS[slot], width=140, anchor="w").grid(row=i, column=0, padx=(12, 4), pady=3)
            cv, av = tk.StringVar(), tk.StringVar()
            cc = ctk.CTkComboBox(mp, variable=cv, values=[""], width=180, state="readonly",
                                 command=lambda v, s=slot: self._on_cat(s, v))
            ac = ctk.CTkComboBox(mp, variable=av, values=[""], width=300, state="readonly",
                                 command=lambda v, s=slot: self._on_action(s, v))
            cc.grid(row=i, column=1, padx=4, pady=3)
            ac.grid(row=i, column=2, padx=4, pady=3)
            self.rows[slot] = (cc, ac, cv, av)
        bt = ctk.CTkFrame(mp, fg_color="transparent")
        bt.grid(row=9, column=0, columnspan=6, sticky="w", padx=12, pady=10)
        ctk.CTkButton(bt, text="Upload all to pad", command=self.upload_all).pack(side="left", padx=(0, 8))
        ctk.CTkButton(bt, text="Reset to defaults", fg_color="#555", command=self.reset_defaults).pack(side="left")
        self.refresh_action_lists()

    def categories(self):
        return list(ACTIONS) + (["Custom"] if self.cfg["custom"] else [])

    def names_for(self, cat):
        return list(self.cfg["custom"]) if cat == "Custom" else [a[0] for a in ACTIONS.get(cat, [])]

    def refresh_action_lists(self):
        for slot, (cc, ac, cv, av) in self.rows.items():
            m = self.cfg["map"][str(slot)]
            cat, name = m["cat"], m["action"]
            if cat not in self.categories() or name not in self.names_for(cat):
                cat, name = DEFAULT_MAP[slot]
            cc.configure(values=self.categories())
            cv.set(cat)
            ac.configure(values=self.names_for(cat))
            av.set(name)

    def _on_cat(self, slot, cat):
        names = self.names_for(cat)
        cc, ac, cv, av = self.rows[slot]
        ac.configure(values=names)
        av.set(names[0])
        self._on_action(slot, names[0])

    def _on_action(self, slot, name):
        self.cfg["map"][str(slot)] = {"cat": self.rows[slot][2].get(), "action": name}
        self.mapping_changed([slot])

    def push_slot(self, slot):
        self.upload_slots([slot])

    def push_all(self):
        self.upload_all()

    def reset_defaults(self):
        self.cfg["map"] = {str(s): {"cat": c, "action": n} for s, (c, n) in DEFAULT_MAP.items()}
        save_config(self.cfg)
        self.refresh_action_lists()
        self.padview.refresh()
        self.recompute_pending()
        if self.dev.connected:
            def done(_):
                for s in range(1, 8):
                    m = self.cfg["map"][str(s)]
                    self._mark_pushed(s, spec_json(resolve_spec(self.cfg, m["cat"], m["action"])))
                self.set_status("Pad key mappings reset to factory defaults")
            self.bg(lambda: self.dev.request({"cmd": "reset_keys"}), done, "Reset failed")
        else:
            self.set_status("Keys reset in the app - they reach the pad with the next upload")

    def refresh_ports(self):
        self.port_map = {f"{p.device} - {p.description}": p.device for p in list_ports.comports()}
        auto = candidate_ports()
        labels = sorted(self.port_map, key=lambda k: self.port_map[k] not in auto)
        self.port_box.configure(values=labels or [""])
        if labels and (not self.port_var.get() or self.port_var.get() not in labels):
            self.port_var.set(labels[0])

    def toggle_connect(self):
        if self.dev.connected:
            self.dev.disconnect()
            self.auto_var.set(False)
            self.auto_flag = False
            return self._on_disconnected()
        port = self.port_map.get(self.port_var.get())
        if not port:
            return self.set_status("Pick a serial port first", error=True)
        self.set_status(f"Connecting to {port}...")
        self.bg(lambda: self._connect_locked(port), self._on_connected, "Connect failed")

    def _connect_locked(self, port):
        with self.connect_lock:
            return self.dev.connect(port)

    def _on_connected(self, info):
        self.conn_lbl.configure(text=f"Connected on {self.dev.port} - firmware {info.get('fw', '?')}, "
                                     f"free flash {info.get('fs_free', 0) // 1024} KB", text_color="#4cd97b")
        self.conn_btn.configure(text="Disconnect")
        self.set_status("Pad connected")
        self._was_connected = True
        self.notify("DeskCompanion connected", f"{self.dev.port}  -  firmware {info.get('fw', '?')}", "ok")
        self.bright.set(info.get("bright", 200))
        self.mode_var.set(MODE_CHOICES[max(0, min(4, info.get("mode", 1) - 1))])
        m, b = max(1, min(5, int(info.get("mode", 1)))), int(info.get("bright", 200))
        self.pad.set_mode(m, notify=False)                     # the virtual screen adopts what the pad is showing
        self.pad.set_brightness(b, notify=False)
        self.vp_mode_var.set(MODE_CHOICES[m - 1])
        self.vp_bright.set(b)
        self.cfg["pushed_mode"], self.cfg["pushed_bright"] = m, b
        self.recompute_pending()

        def work():
            self.dev.request({"cmd": "os", "val": self.cfg["os"]})
            self.dev.request(time_msg())
            self._push_layout()
        self.bg(work, None, "Initial sync failed")

    def _on_disconnected(self):
        self.conn_lbl.configure(text="Not connected", text_color="#ffb454")
        self.conn_btn.configure(text="Connect")
        self.set_status("Pad disconnected - waiting for it to reappear" if self.auto_flag else "Disconnected")
        if self._was_connected:
            self._was_connected = False
            self.notify("DeskCompanion disconnected", "Waiting for the pad to be plugged in again...", "off")

    def _meters(self, cpu, ram):
        self.cpu_bar.set(cpu / 100)
        self.ram_bar.set(ram / 100)
        self.cpu_lbl.configure(text=f"CPU {cpu:3.0f}%")
        self.ram_lbl.configure(text=f"RAM {ram:3.0f}%")
        self.pad.cpu, self.pad.ram = cpu, ram

    # ---- macro creator
    def _build_macro(self, tab):
        top = ctk.CTkFrame(tab)
        top.pack(fill="x", padx=6, pady=(6, 4))
        ctk.CTkLabel(top, text="Target:").pack(side="left", padx=(12, 6), pady=10)
        self.target_var = tk.StringVar(value=SLOT_LABELS[1])
        ctk.CTkOptionMenu(top, values=list(SLOT_LABELS.values()), variable=self.target_var, width=180).pack(side="left")
        ctk.CTkLabel(top, text="Text is typed as US-layout ASCII (see README).", text_color="#9aa0a6").pack(side="left", padx=14)

        cb = ctk.CTkFrame(tab)
        cb.pack(fill="x", padx=6, pady=4)
        self._title(cb, "Key combination (up to 4 modifiers + main key)")
        self.mod_vars = []
        for i in range(4):
            v = tk.StringVar(value="CTRL" if i == 0 else "-")
            ctk.CTkOptionMenu(cb, values=MODIFIERS, variable=v, width=105).grid(row=1, column=i, padx=(12 if i == 0 else 4, 4), pady=6)
            self.mod_vars.append(v)
        ctk.CTkLabel(cb, text="+").grid(row=1, column=4)
        self.key_var = tk.StringVar(value="c")
        ctk.CTkComboBox(cb, values=KEY_CHOICES, variable=self.key_var, width=110).grid(row=1, column=5, padx=6)
        ctk.CTkButton(cb, text="Assign to key", width=110, command=self.assign_combo).grid(row=1, column=6, padx=4)
        ctk.CTkButton(cb, text="Add to sequence", width=120, fg_color="#555", command=self.seq_add_combo).grid(row=1, column=7, padx=4)
        ctk.CTkButton(cb, text="Test", width=70, fg_color="#2f7d4f", command=self.test_combo).grid(row=1, column=8, padx=4)

        tx = ctk.CTkFrame(tab)
        tx.pack(fill="x", padx=6, pady=4)
        self._title(tx, "Text snippet auto-typer")
        self.text_box = ctk.CTkTextbox(tx, height=64, width=520)
        self.text_box.grid(row=1, column=0, padx=12, pady=4, rowspan=2)
        self.enter_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(tx, text="Press Enter afterwards", variable=self.enter_var).grid(row=1, column=1, padx=8, sticky="w")
        bt = ctk.CTkFrame(tx, fg_color="transparent")
        bt.grid(row=2, column=1, padx=8, sticky="w")
        ctk.CTkButton(bt, text="Assign to key", width=110, command=self.assign_text).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bt, text="Add to sequence", width=120, fg_color="#555", command=self.seq_add_text).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bt, text="Test", width=70, fg_color="#2f7d4f", command=self.test_text).pack(side="left")

        sq = ctk.CTkFrame(tab)
        sq.pack(fill="both", expand=True, padx=6, pady=4)
        self._title(sq, "Macro sequence (with delays)")
        self.seq_list = tk.Listbox(sq, height=7, width=62, bg="#2b2b2b", fg="#eeeeee", selectbackground="#1f6aa5",
                                   borderwidth=0, highlightthickness=0, activestyle="none")
        self.seq_list.grid(row=1, column=0, rowspan=4, padx=12, pady=4, sticky="nsew")
        for r, (t, f) in enumerate((("Move up", lambda: self.seq_move(-1)), ("Move down", lambda: self.seq_move(1)),
                                    ("Remove", self.seq_remove), ("Clear", self.seq_clear)), start=1):
            ctk.CTkButton(sq, text=t, width=100, fg_color="#555", command=f).grid(row=r, column=1, padx=4, pady=2, sticky="w")
        adv = ctk.CTkFrame(sq, fg_color="transparent")
        adv.grid(row=5, column=0, columnspan=3, sticky="w", padx=8, pady=4)
        self.delay_var = tk.StringVar(value="200")
        ctk.CTkEntry(adv, textvariable=self.delay_var, width=70).pack(side="left", padx=4)
        ctk.CTkButton(adv, text="Add delay (ms)", width=120, command=self.seq_add_delay).pack(side="left", padx=(0, 16))
        self.media_var = tk.StringVar(value="PLAY_PAUSE")
        ctk.CTkOptionMenu(adv, values=MEDIA_CHOICES, variable=self.media_var, width=140).pack(side="left", padx=4)
        ctk.CTkButton(adv, text="Add media key", width=120, command=self.seq_add_media).pack(side="left")
        fin = ctk.CTkFrame(sq, fg_color="transparent")
        fin.grid(row=6, column=0, columnspan=3, sticky="w", padx=8, pady=(4, 10))
        self.name_var = tk.StringVar(value="My macro")
        ctk.CTkEntry(fin, textvariable=self.name_var, width=200).pack(side="left", padx=4)
        ctk.CTkButton(fin, text="Assign sequence to key", command=self.assign_seq).pack(side="left", padx=6)
        ctk.CTkButton(fin, text="Save to library only", fg_color="#555", command=self.save_seq).pack(side="left", padx=(0, 6))
        ctk.CTkButton(fin, text="Test sequence", fg_color="#2f7d4f", command=self.test_seq).pack(side="left")

    def _target_slot(self):
        return next(s for s, l in SLOT_LABELS.items() if l == self.target_var.get())

    def _combo_keys(self):
        mods = []
        for v in self.mod_vars:
            m = v.get()
            if m != "-" and m not in mods:
                mods.append(m)
        key = self.key_var.get().strip()
        key = key if len(key) == 1 else key.upper()
        if not valid_key(key):
            raise ValueError(f"'{key}' is not a valid key")
        return mods + [key]

    def _text_value(self):
        t = self.text_box.get("1.0", "end-1c")
        if not t:
            raise ValueError("Enter some text first")
        if any(ord(c) > 126 for c in t):
            raise ValueError("Only ASCII characters can be typed by the pad")
        return t + ("\n" if self.enter_var.get() else "")

    def assign_spec(self, spec, label):
        slot = self._target_slot()
        self.cfg["custom"][label] = {"type": spec[0], "val": spec[1]}
        self.cfg["map"][str(slot)] = {"cat": "Custom", "action": label}
        self.refresh_library()
        self.mapping_changed([slot])
        self.set_status(f"'{label}' assigned to {SLOT_LABELS[slot]} - test it on the Virtual Pad, then upload")

    def _guard(self, fn):
        try:
            fn()
        except ValueError as e:
            self.set_status(str(e), error=True)

    def assign_combo(self):
        def go():
            k = self._combo_keys()
            self.assign_spec(("combo", k), describe_spec(("combo", k)))
        self._guard(go)

    def assign_text(self):
        def go():
            t = self._text_value()
            self.assign_spec(("text", t), "Text: " + t.strip().replace("\n", " ")[:24])
        self._guard(go)

    def _seq_refresh(self):
        self.seq_list.delete(0, "end")
        for s in self.macro_steps:
            if "combo" in s:
                d = "COMBO   " + "+".join(s["combo"])
            elif "text" in s:
                d = "TEXT    " + repr(s["text"])[:48]
            elif "delay" in s:
                d = f"DELAY   {s['delay']} ms"
            else:
                d = "MEDIA   " + s["media"]
            self.seq_list.insert("end", d)

    def seq_add_combo(self):
        self._guard(lambda: (self.macro_steps.append({"combo": self._combo_keys()}), self._seq_refresh()))

    def seq_add_text(self):
        self._guard(lambda: (self.macro_steps.append({"text": self._text_value()}), self._seq_refresh()))

    def seq_add_delay(self):
        def go():
            try:
                ms = int(self.delay_var.get())
            except ValueError:
                raise ValueError("Delay must be a whole number of milliseconds")
            if not 0 <= ms <= 60000:
                raise ValueError("Delay must be between 0 and 60000 ms")
            self.macro_steps.append({"delay": ms})
            self._seq_refresh()
        self._guard(go)

    def seq_add_media(self):
        self.macro_steps.append({"media": self.media_var.get()})
        self._seq_refresh()

    def seq_remove(self):
        for i in reversed(self.seq_list.curselection()):
            del self.macro_steps[i]
        self._seq_refresh()

    def seq_clear(self):
        self.macro_steps.clear()
        self._seq_refresh()

    def seq_move(self, d):
        sel = self.seq_list.curselection()
        if not sel:
            return
        i, j = sel[0], sel[0] + d
        if 0 <= j < len(self.macro_steps):
            self.macro_steps[i], self.macro_steps[j] = self.macro_steps[j], self.macro_steps[i]
            self._seq_refresh()
            self.seq_list.selection_set(j)

    def _seq_spec(self):
        if not self.macro_steps:
            raise ValueError("The sequence is empty")
        if len(self.macro_steps) > 64:
            raise ValueError("Sequences are limited to 64 steps")
        return ("macro", [dict(s) for s in self.macro_steps])

    def assign_seq(self):
        self._guard(lambda: self.assign_spec(self._seq_spec(), self.name_var.get().strip() or "My macro"))

    def save_seq(self):
        def go():
            spec, name = self._seq_spec(), self.name_var.get().strip() or "My macro"
            self.cfg["custom"][name] = {"type": spec[0], "val": spec[1]}
            save_config(self.cfg)
            self.refresh_action_lists()
            self.refresh_library()
            self.set_status(f"Saved '{name}' - drag it from the 'Custom' category onto a key")
        self._guard(go)

    # ---- GIF upload
    def _build_gif(self, tab):
        left = ctk.CTkFrame(tab)
        left.pack(side="left", fill="y", padx=6, pady=6)
        self.gif_canvas = tk.Canvas(left, width=LCD, height=LCD, bg="#151515", highlightthickness=0)
        self.gif_canvas.pack(padx=14, pady=14)
        self.gif_canvas.create_oval(0, 0, LCD - 1, LCD - 1, outline="#3b82f6", width=2)
        self._pv_item = self.gif_canvas.create_image(0, 0, anchor="nw")
        self.gif_canvas.tag_lower(self._pv_item)
        ctk.CTkLabel(left, text="Round display preview", text_color="#9aa0a6").pack(pady=(0, 10))
        right = ctk.CTkFrame(tab)
        right.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        self._title(right, "Animated GIF -> pad")
        ctk.CTkButton(right, text="Select GIF...", command=self.choose_gif).grid(row=1, column=0, padx=12, pady=6, sticky="w")
        self.gif_name = ctk.CTkLabel(right, text="no file selected", anchor="w")
        self.gif_name.grid(row=1, column=1, sticky="w", padx=8)
        self.dither_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(right, text="Dithering (smoother gradients, larger file)", variable=self.dither_var).grid(
            row=2, column=0, columnspan=2, padx=12, pady=4, sticky="w")
        self.gif_info = ctk.CTkLabel(right, text="Frames are centre-cropped, resized to 240x240 and masked to a circle.",
                                     justify="left", wraplength=470, anchor="w")
        self.gif_info.grid(row=3, column=0, columnspan=2, padx=12, pady=6, sticky="w")
        self.gif_bar = ctk.CTkProgressBar(right, width=440)
        self.gif_bar.grid(row=4, column=0, columnspan=2, padx=12, pady=8, sticky="w")
        self.gif_bar.set(0)
        self.upload_btn = ctk.CTkButton(right, text="Upload to pad", state="disabled", command=self.upload_gif)
        self.upload_btn.grid(row=5, column=0, padx=12, pady=6, sticky="w")
        ctk.CTkButton(right, text="Delete GIF on pad", fg_color="#555", command=self.delete_gif).grid(row=5, column=1, sticky="w", padx=8)

    def choose_gif(self):
        path = filedialog.askopenfilename(filetypes=[("GIF images", "*.gif"), ("All files", "*.*")])
        if not path:
            return
        self.gif_name.configure(text=os.path.basename(path))
        self.gif_info.configure(text="Processing...")
        self.upload_btn.configure(state="disabled")
        self.gif_bar.set(0)
        free = self.dev.info.get("fs_free") if self.dev.connected else None
        limit = min(max((free or 1_000_000) - 16384, 50_000), 1_400_000)
        dither = self.dither_var.get()

        def work():
            frames, durs = load_gif_frames(path)
            data, colors, n = fit_gif(frames, durs, limit, dither)
            return frames, durs, data, colors, n, limit
        self.bg(work, self._gif_ready, "GIF processing failed")

    def _gif_ready(self, res):
        self.gif_frames, self.gif_durs, self.gif_data, colors, n, limit = res
        self.gif_info.configure(text=f"{len(self.gif_frames)} source frames -> {n} frames, {colors} colours, "
                                     f"{len(self.gif_data) / 1024:.0f} KB (limit {limit // 1024} KB).")
        self.upload_btn.configure(state="normal")
        self.pad.set_gif(self.gif_frames, self.gif_durs)       # the virtual screen can show it before uploading
        self._pv_idx = 0
        self._preview_tick()

    def _preview_tick(self):
        if self._pv_job:
            self.after_cancel(self._pv_job)
            self._pv_job = None
        if not self.gif_frames:
            return
        i = self._pv_idx % len(self.gif_frames)
        self._pv_img = ImageTk.PhotoImage(self.gif_frames[i])
        self.gif_canvas.itemconfig(self._pv_item, image=self._pv_img)
        self._pv_idx += 1
        self._pv_job = self.after(self.gif_durs[i], self._preview_tick)

    def upload_gif(self):
        if not self.dev.connected:
            return self.set_status("Connect the pad first", error=True)
        data = self.gif_data
        self.upload_btn.configure(state="disabled")
        self.gif_bar.set(0)
        self.set_status("Uploading GIF...")

        def progress(f):
            self.post(lambda: (self.gif_bar.set(f), setattr(self.pad, "upload_frac", f)))

        def finished():
            self.pad.upload_frac, self.pad.dirty = None, True
            self.upload_btn.configure(state="normal")

        def done(_):
            finished()
            self.gif_bar.set(1)
            self.pad.set_mode(M_GIF)
            self.set_status("GIF uploaded - the pad switched to GIF mode")
        self.bg(lambda: self.dev.upload_gif(data, progress), done, "Upload failed", fail=finished)

    def delete_gif(self):
        self.bg(lambda: self.dev.request({"cmd": "gif_delete"}),
                lambda _: self.set_status("GIF removed - the built-in demo animation will be regenerated"), "Delete failed")

    # ---- device tab
    def _build_device_tab(self, tab):
        box = ctk.CTkFrame(tab)
        box.pack(fill="x", padx=6, pady=6)
        self._title(box, "Display")
        ctk.CTkLabel(box, text="Brightness").grid(row=1, column=0, padx=12, pady=6, sticky="w")
        self.bright = ctk.CTkSlider(box, from_=5, to=255, number_of_steps=50, width=300, command=self._bright_changed)
        self.bright.set(200)
        self.bright.grid(row=1, column=1, padx=6)
        ctk.CTkLabel(box, text="Mode").grid(row=2, column=0, padx=12, pady=6, sticky="w")
        self.mode_var = tk.StringVar(value=MODE_CHOICES[0])
        ctk.CTkOptionMenu(box, values=MODE_CHOICES, variable=self.mode_var, width=180, command=self._mode_changed).grid(
            row=2, column=1, padx=6, sticky="w")
        ctk.CTkLabel(box, text="Host OS").grid(row=3, column=0, padx=12, pady=6, sticky="w")
        self.os_var = tk.StringVar(value=self.cfg["os"])
        ctk.CTkOptionMenu(box, values=["win", "mac", "linux"], variable=self.os_var, width=180, command=self._os_changed).grid(
            row=3, column=1, padx=6, sticky="w")
        self.notify_var = tk.BooleanVar(value=bool(self.cfg.get("notify", True)))
        ctk.CTkSwitch(box, text="Popup + sound when the pad connects", variable=self.notify_var,
                      command=lambda: (self.cfg.__setitem__("notify", self.notify_var.get()), save_config(self.cfg))).grid(
            row=5, column=1, padx=6, pady=(0, 8), sticky="w")
        ctk.CTkLabel(box, text="Keyboard layout").grid(row=6, column=0, padx=12, pady=6, sticky="w")
        self.layout_var = tk.StringVar(value=self.cfg.get("layout", "auto"))
        ctk.CTkOptionMenu(box, values=["auto"] + LAYOUTS, variable=self.layout_var, width=180,
                          command=self._layout_changed).grid(row=6, column=1, padx=6, sticky="w")
        ctk.CTkLabel(box, text="The pad presses keys for this layout (QWERTZ swaps Z/Y). 'auto' follows this PC.",
                     text_color="#9aa0a6").grid(row=7, column=1, padx=6, sticky="w")
        ctk.CTkButton(box, text="Sync time now", command=lambda: self.bg(lambda: self.dev.request(time_msg()),
                      lambda _: self.set_status("Clock synchronised"), "Time sync failed")).grid(row=4, column=1, padx=6, pady=8, sticky="w")

        wf = ctk.CTkFrame(tab)
        wf.pack(fill="x", padx=6, pady=4)
        self._title(wf, "Optional Wi-Fi / NTP time sync (stored on the pad; leave empty to disable)")
        self.ssid_var, self.pass_var = tk.StringVar(), tk.StringVar()
        ctk.CTkEntry(wf, textvariable=self.ssid_var, placeholder_text="SSID", width=200).grid(row=1, column=0, padx=12, pady=6)
        ctk.CTkEntry(wf, textvariable=self.pass_var, placeholder_text="Password", show="*", width=200).grid(row=1, column=1, padx=4)
        ctk.CTkButton(wf, text="Save Wi-Fi", command=lambda: self.bg(
            lambda: self.dev.request({"cmd": "wifi", "ssid": self.ssid_var.get(), "pass": self.pass_var.get()}),
            lambda _: self.set_status("Wi-Fi saved on the pad"), "Wi-Fi save failed")).grid(row=1, column=2, padx=8)

        self.log_box = ctk.CTkTextbox(tab, height=200, state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=6, pady=6)

    def _bright_changed(self, v):
        if self._bright_job:
            self.after_cancel(self._bright_job)
        val = int(v)
        self.pad.set_brightness(val)
        self._bright_job = self.after(250, lambda: self.dev.connected and self.bg(
            lambda: self.dev.request({"cmd": "brightness", "val": val}), None, "Brightness failed"))

    def _mode_changed(self, v):
        m = int(v[0])
        self.pad.set_mode(m)
        if self.dev.connected:
            self.bg(lambda: self.dev.request({"cmd": "mode", "val": m}),
                    lambda _: self._mark_display_pushed(self.pad.mode, self.pad.brightness), "Mode change failed")

    def effective_layout(self):
        v = self.cfg.get("layout", "auto")
        return detect_layout() if v == "auto" else v

    def _push_layout(self):
        try:
            self.dev.request({"cmd": "layout", "val": self.effective_layout()})
        except DeviceError:
            pass                                             # older firmware without the layout command

    def _layout_changed(self, v):
        self.cfg["layout"] = v
        save_config(self.cfg)
        if self.dev.connected:
            self.bg(self._push_layout, lambda _: self.set_status(f"Keyboard layout: {self.effective_layout()}"), "Layout change failed")

    def _os_changed(self, v):
        self.cfg["os"] = v
        save_config(self.cfg)
        self.recompute_pending()
        if self.dev.connected:
            self.upload_all()


if __name__ == "__main__":
    App().mainloop()
