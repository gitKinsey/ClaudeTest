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
import colorsys
import ctypes
import ctypes.wintypes
from datetime import datetime
import functools
import io
import json
import math
import os
import platform
import subprocess
import sys
import queue
import random
import re
import secrets
import shutil
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

APP_DIR = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)    # next to this file, or the PyInstaller bundle folder
sys.path.insert(0, str(APP_DIR))                                                          # desk_lib/ lives there
from desk_lib import ui                                              # noqa: E402
from desk_lib import activewin, automation, autobackup, autostart, backup, bridge, cliphist, espota, extras, feeds, hostactions, padextras, presets, recorder, scheduler, scripting, scripts_page, sysactions, textops, tray, winlayout, wizards     # noqa: E402
from desk_lib.ui import (ACCENT, CARD2, CARD3, ERR, FAINT, MUTED, OK, PINK, TEXT, WARN, SideTabs, Pill)   # noqa: E402

APP_NAME = "Desk Companion"
APP_VERSION = "1.4.0"
PAGES = [("Control", [("Dashboard", "Home", "Connection, health and quick actions"),
                      ("Virtual Pad", "Pad", "Your keys on three layers, with a live twin of the device"),
                      ("Macro Creator", "Macros", "Key combinations, text, delays, mouse, computer actions"),
                      ("Profiles", "Profiles", "Switch the pad's layer automatically for each program"),
                      ("Scripts", "Scripts", "Loops, conditions and variables for your keys"),
                      ("Automation", "Automation", "Scheduled actions and the local API")]),
         ("Display", [("GIF Upload", "GIFs", "Pick or upload animations for the round screen"),
                      ("Info Screen", "Info", "Now playing, weather, calendar and notification badges")]),
         ("System", [("Device", "Device", "Settings, firmware, backup and recovery"),
                     ("Dev", "Diagnostics", "Bring-up tests, terminal and reports")])]
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
    "View & Windows": [
        ("Fullscreen", C("F11"), C("CTRL", "GUI", "f")), ("Reset Zoom", C("PRIMARY", "0")), ("Previous App (Alt+Shift+Tab)", C("ALT", "SHIFT", "TAB"), C("GUI", "SHIFT", "TAB")),
        ("Snap Window Left", C("GUI", "LEFT")), ("Snap Window Right", C("GUI", "RIGHT")), ("Maximize Window", C("GUI", "UP")),
        ("Minimize Window", C("GUI", "DOWN"), C("GUI", "m")), ("Next Virtual Desktop", C("CTRL", "GUI", "RIGHT"), C("CTRL", "RIGHT")),
        ("Previous Virtual Desktop", C("CTRL", "GUI", "LEFT"), C("CTRL", "LEFT")),
    ],
    "Meetings": [
        ("Teams: Mute", C("PRIMARY", "SHIFT", "m")), ("Teams: Camera", C("PRIMARY", "SHIFT", "o")), ("Teams: Share Screen", C("PRIMARY", "SHIFT", "e")),
        ("Teams: Raise Hand", C("PRIMARY", "SHIFT", "k")), ("Meet: Mute", C("PRIMARY", "d")), ("Meet: Camera", C("PRIMARY", "e")),
        ("Meet: Raise Hand", C("CTRL", "ALT", "h")), ("Meet: Chat", C("CTRL", "ALT", "c")),
        ("Zoom: Share Screen", C("ALT", "s"), C("GUI", "SHIFT", "s")), ("Zoom: Raise Hand", C("ALT", "y"), C("ALT", "y")),
    ],
    "Creative & Video": [
        ("Brush Tool", C("b")), ("Eraser Tool", C("e")), ("Brush Size Up", C("]")), ("Brush Size Down", C("[")),
        ("Step Backward (Photoshop)", C("CTRL", "ALT", "z"), C("GUI", "ALT", "z")), ("Frame Forward", C("RIGHT")), ("Frame Back", C("LEFT")),
        ("Timeline Play (Space)", C("SPACE")), ("Shuttle Reverse (J)", C("j")), ("Shuttle Stop (K)", C("k")), ("Shuttle Forward (L)", C("l")),
        ("Add Edit / Split (Premiere)", C("PRIMARY", "k")), ("Mark In (I)", C("i")), ("Mark Out (O)", C("o")),
    ],
    "Writing": [
        ("Bold", C("PRIMARY", "b")), ("Italic", C("PRIMARY", "i")), ("Underline", C("PRIMARY", "u")), ("Insert Link", C("PRIMARY", "k")),
        ("Find Next", C("F3"), C("GUI", "g")), ("Select Line", C("HOME"), C("GUI", "LEFT")),
    ],
    "Spreadsheet & 3D": [
        ("Edit Cell (F2)", C("F2")), ("Autosum", C("ALT", "=")), ("Toggle Filter", C("PRIMARY", "SHIFT", "l")), ("Absolute Reference (F4)", C("F4")),
        ("Next Sheet", C("CTRL", "PGDN")), ("Previous Sheet", C("CTRL", "PGUP")),
        ("Blender: Grab", C("g")), ("Blender: Rotate", C("r")), ("Blender: Scale", C("s")), ("Blender: Edit Mode", C("TAB")),
    ],
    "Layers & Pad": [
        ("Panic: stop all macros", ("panic", None)),
        ("Next Layer", ("layer", "next")), ("Previous Layer", ("layer", "prev")),
        ("Layer 1", ("layer", 0)), ("Layer 2", ("layer", 1)), ("Layer 3", ("layer", 2)),
    ],
    "Mouse": [
        ("Left Click", ("mouse", {"btn": "left"})), ("Right Click", ("mouse", {"btn": "right"})),
        ("Middle Click", ("mouse", {"btn": "middle"})), ("Double Click", ("mouse", {"btn": "left", "act": "double"})),
        ("Scroll Up", ("mouse", {"wheel": 3})), ("Scroll Down", ("mouse", {"wheel": -3})),
        ("Mouse Back Button", ("mouse", {"btn": "back"})), ("Mouse Forward Button", ("mouse", {"btn": "forward"})),
        ("Zoom In (Ctrl+Scroll)", ("mouse", {"wheel": 2, "mods": ["CTRL"]})), ("Zoom Out (Ctrl+Scroll)", ("mouse", {"wheel": -2, "mods": ["CTRL"]})),
        ("Scroll Left (sideways)", ("mouse", {"wheel": 3, "h": True})), ("Scroll Right (sideways)", ("mouse", {"wheel": -3, "h": True})),
        ("Scroll Faster Up (Shift)", ("mouse", {"wheel": 3, "mods": ["SHIFT"]})),
    ],
    "Navigation": [
        ("Page Down", C("PGDN")), ("Page Up", C("PGUP")), ("Home", C("HOME")), ("End", C("END")),
        ("Arrow Up", C("UP")), ("Arrow Down", C("DOWN")), ("Arrow Left", C("LEFT")), ("Arrow Right", C("RIGHT")),
    ],
    "Computer": [("Type Clipboard", ("host", {"op": "clipboard"})), ("Show Notification (test)", ("host", {"op": "notify", "arg": "Hello from your pad"}))],
    "Other": [("Unassigned", ("none", None))],
}
ACTION_INDEX = {(cat, a[0]): (a[1], a[2] if len(a) > 2 else None) for cat, lst in ACTIONS.items() for a in lst}
assert len(ACTION_INDEX) >= 50

SLOT_LABELS = {1: "K1", 2: "K2", 3: "K3", 4: "K4", 5: "K5", 6: "Encoder turn right", 7: "Encoder turn left"}
DEFAULT_MAP = {1: ("Editing", "Copy"), 2: ("Editing", "Paste"), 3: ("Editing", "Undo"), 4: ("Media", "Play/Pause"),
               5: ("Media", "Mute"), 6: ("Media", "Volume Up"), 7: ("Media", "Volume Down")}
DEFAULT_LAYER_MAPS = [
    DEFAULT_MAP,
    {1: ("Media", "Previous Track"), 2: ("Media", "Play/Pause"), 3: ("Media", "Next Track"), 4: ("Media", "Stop"),
     5: ("Media", "Mute"), 6: ("Media", "Volume Up"), 7: ("Media", "Volume Down")},
    {1: ("Browser", "Back"), 2: ("Browser", "Forward"), 3: ("Browser", "Refresh"), 4: ("Browser", "New Tab"),
     5: ("Browser", "Close Tab"), 6: ("Navigation", "Page Down"), 7: ("Navigation", "Page Up")},
]
# (label, op, placeholder, needs an argument)
ACTION_KINDS = [("Open website", "url", "https://example.com", True), ("Start program", "app", "program name or path", True),
                ("Run shell command", "shell", "command line (needs the shell switch)", True), ("Open file or folder", "file", "path", True),
                ("Show notification", "notify", "message", True), ("Type the clipboard", "clipboard", "", False),
                ("Type a snippet", "snippet", "text with {date} {time} {clipboard} {counter:name} {uuid} {random:1-6}", True),
                ("Transform the clipboard", "clip", "", False),
                ("Change this program's volume", "appvol", "up, down, +5, -10  or  spotify:+5", True), ("Do Not Disturb", "dnd", "on, off or toggle", True),
                ("Switch audio output", "audio_out", "next, or part of a device name", True), ("Microphone mute", "mic", "toggle, mute or unmute", True),
                ("Screenshot to a folder", "shot", "folder (empty = Device page setting / Pictures)", False),
                ("Translate the clipboard", "translate", "language code: de, es, fr, ja ...", True),
                ("Ask the AI", "ai", "prompt, e.g.  Summarize: {clipboard}", True), ("Call a web address", "webhook", "[GET|POST] https://address [body]", True),
                ("Window layout", "layout", "save work   (or just: work  to restore it)", True), ("Type from clipboard history", "cliphist", "1 = latest copy ... 9", True),
                ("Mouse click", "click", "left / right / middle / back / forward  (default: left)", False),
                ("Mouse double click", "click", "left / right / middle  (default: left)", False),
                ("Mouse scroll", "scroll", "amount: 1..20 up, -1..-20 down", True), ("Switch layer", "layer", "1, 2, 3, next or prev (default: next)", False)]
LAYER_NAMES = ["Layer 1  General", "Layer 2  Media", "Layer 3  Browser"]
MODIFIERS = ["-", "CTRL", "SHIFT", "ALT", "GUI", "PRIMARY"]
NAMED_KEYS = ["ENTER", "TAB", "ESC", "SPACE", "BACKSPACE", "DELETE", "INSERT", "HOME", "END", "PGUP", "PGDN",
              "UP", "DOWN", "LEFT", "RIGHT", "PRTSC", "MENU", "CAPSLOCK"]
KEY_CHOICES = (list("abcdefghijklmnopqrstuvwxyz0123456789") + [f"F{i}" for i in range(1, 13)] + NAMED_KEYS
               + list("-=[];',./`\\"))
MEDIA_CHOICES = ["PLAY_PAUSE", "NEXT", "PREV", "STOP", "MUTE", "VOL_UP", "VOL_DOWN", "FF", "REWIND"]
MODE_CHOICES = ["1 Clock", "2 Focus timer", "3 Media", "4 System", "5 GIF", "6 Info", "7 Stopwatch", "8 Breathing", "9 Dice & coin", "10 Reaction test", "11 Snake", "12 Habits"]


def valid_key(k):
    if not k:
        return False
    if len(k) == 1:
        return 32 < ord(k) < 127
    u = k.upper()
    return u in NAMED_KEYS or (u[0] == "F" and u[1:].isdigit() and 1 <= int(u[1:]) <= 24)


# ============================================================================ config
INFO_DEFAULTS = {"music": True, "weather": False, "event": False, "custom": False, "city": "", "lat": None, "lon": None, "label": "",
                 "fahrenheit": False, "ics": "", "rot": 6, "c_label": "", "c_t": "", "c_a": "", "c_b": ""}


def load_config():
    cfg = {"os": {"Windows": "win", "Darwin": "mac"}.get(platform.system(), "linux"), "map": {}, "custom": {}}
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return normalize_config(cfg)


def normalize_config(cfg):
    """Fill in everything a config needs (also used when restoring a backup). Makes cfg["map"] / ["pushed"] aliases of layer 1."""
    cfg.setdefault("os", {"Windows": "win", "Darwin": "mac"}.get(platform.system(), "linux"))
    cfg.setdefault("map", {})
    cfg.setdefault("custom", {})
    if not isinstance(cfg.get("layers"), list) or len(cfg["layers"]) != LAYERS or not all(isinstance(x, dict) for x in cfg["layers"]):
        cfg["layers"] = [cfg.get("map") if isinstance(cfg.get("map"), dict) else {}, {}, {}]      # configs from before layers existed: "map" = layer 1
    if not isinstance(cfg.get("pushed_layers"), list) or len(cfg["pushed_layers"]) != LAYERS:
        cfg["pushed_layers"] = [cfg.get("pushed") if isinstance(cfg.get("pushed"), dict) else {}, {}, {}]
    for n in range(LAYERS):
        for s, (cat, name) in DEFAULT_LAYER_MAPS[n].items():
            cfg["layers"][n].setdefault(str(s), {"cat": cat, "action": name})
    cfg["edit_layer"] = 0
    cfg["map"], cfg["pushed"] = cfg["layers"][0], cfg["pushed_layers"][0]     # the app edits cfg["map"] = the selected layer (see App.set_edit_layer)
    cfg.setdefault("twin_mode", 1)
    cfg.setdefault("twin_bright", 200)
    cfg.setdefault("pushed_mode", None)
    cfg.setdefault("pushed_bright", None)
    cfg.setdefault("notify", True)
    cfg.setdefault("live_test", True)
    cfg.setdefault("layout", "auto")
    cfg.setdefault("online_keys", {})
    cfg.setdefault("host_media_sync", True)
    cfg.setdefault("appearance", "dark")
    cfg.setdefault("profiles", [])               # per-program layer rules: {"name","match","kind","layer","enabled"}
    cfg.setdefault("profiles_on", False)
    cfg.setdefault("profile_default", 0)
    cfg.setdefault("allow_shell", False)         # may the pad run shell commands on this PC?  (off unless you switch it on)
    info = cfg.setdefault("info", {})            # info-screen feed settings
    for k, v in INFO_DEFAULTS.items():
        info.setdefault(k, v)
    cfg.setdefault("usage", {})                  # key press counters
    cfg.setdefault("cliphist_on", False)         # remember the last copied texts (memory only)
    cfg.setdefault("shot_dir", "")               # screenshot action folder ("" = Pictures)
    ai = cfg.setdefault("ai", {})                # AI action: the user's own Anthropic API key (kept in this file in plain text) and model
    ai.setdefault("key", "")
    ai.setdefault("model", "claude-haiku-4-5-20251001")
    if not isinstance(cfg.get("layouts"), dict):
        cfg["layouts"] = {}                      # saved window layouts {name: [{process,title,x,y,w,h}]}
    cfg.setdefault("led_cpu", False)             # the pad's LED follows this computer's CPU load
    cfg.setdefault("tray", False)                # keep running in the system tray when the window is closed
    cfg.setdefault("counters", {})               # {counter:name} values used by snippets
    sc = cfg.get("scripts")                      # macro scripts: {name: source}
    cfg["scripts"] = {str(k)[:32]: str(v)[:scripting.MAX_SCRIPT_CHARS] for k, v in sc.items() if str(k).strip()} if isinstance(sc, dict) else {}
    good = []                                    # scheduled actions: drop anything that no longer validates
    for e in cfg.get("schedules") or []:
        try:
            good.append(scheduler.validate(e))
        except (ValueError, TypeError):
            pass
    cfg["schedules"] = good
    good = []                                    # extra info cards (countdown, world clock, git, CI, crypto)
    for x in info.get("extras") or []:
        try:
            good.append(extras.validate(x))
        except (ValueError, TypeError):
            pass
    info["extras"] = good[:4]
    gs = cfg.get("gestures")                     # hold / double-tap actions: {"layer:slot:hold": {"cat","action"}}
    cfg["gestures"] = {k: v for k, v in gs.items() if isinstance(v, dict) and "cat" in v and "action" in v and re.fullmatch(r"[0-2]:([1-5]:(hold|double)|[89]:press)", str(k))} \
        if isinstance(gs, dict) else {}
    api = cfg.setdefault("api", {})              # local API for scripts (off unless switched on)
    api.setdefault("on", False)
    api.setdefault("port", 47651)
    if not isinstance(api.get("token"), str) or len(api["token"]) < 16:
        api["token"] = secrets.token_urlsafe(18)
    cfg.setdefault("wizard_done", False)
    return cfg


_AUTOBACKUP = {}


def autobackup_for(path=None):
    """The rolling-backup helper that belongs to the config file (a '<name>.backups' folder next to it)."""
    path = Path(path or CONFIG_PATH)
    if path not in _AUTOBACKUP:
        _AUTOBACKUP[path] = autobackup.AutoBackup(path.with_name(path.name + ".backups"))
    return _AUTOBACKUP[path]


def save_config(cfg):
    out = dict(cfg)
    if isinstance(out.get("layers"), list):          # "map" / "pushed" are aliases of the selected layer: store layer 1 there
        out["map"], out["pushed"] = out["layers"][0], out["pushed_layers"][0]
    out.pop("edit_layer", None)
    try:
        CONFIG_PATH.write_text(json.dumps(out, indent=1), encoding="utf-8")
    except OSError:
        return
    autobackup_for().maybe(out)                      # throttled + only when something meaningful changed


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


# ============================================================================ built-in preset animation library
# Procedurally generated quick-start animations (no copyrighted sticker/meme art bundled - these are
# plain geometric generators) so there is always something to try on the GIF tab before hunting for a file.
@functools.lru_cache(maxsize=1)
def _preset_mask():
    m = Image.new("L", (LCD, LCD), 0)
    ImageDraw.Draw(m).ellipse((0, 0, LCD - 1, LCD - 1), fill=255)
    return m


def _preset_frames(n_frames, duration_ms, draw_fn):
    black = Image.new("RGB", (LCD, LCD), (0, 0, 0))
    mask = _preset_mask()
    frames = []
    for i in range(n_frames):
        im = Image.new("RGB", (LCD, LCD), (10, 10, 14))
        draw_fn(ImageDraw.Draw(im), i, n_frames)
        frames.append(Image.composite(im, black, mask))
    return frames, [duration_ms] * n_frames


def _heart_points(cx, cy, s):
    pts = []
    for deg in range(0, 360, 6):
        t = math.radians(deg)
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x * s, cy - y * s))
    return pts


def _draw_heartbeat(d, i, n):
    beat = abs(math.sin(math.pi * i / n)) ** 0.5
    d.polygon(_heart_points(120, 128, 5.6 + 1.1 * beat), fill=(230, 45, 95))


def _draw_spinner(d, i, n):
    d.ellipse((38, 38, 202, 202), outline=(40, 44, 54), width=10)
    for k in range(12):
        a = math.radians(k * 30 - i * 360 / n)
        x, y = 120 + 70 * math.sin(a), 120 - 70 * math.cos(a)
        fade = k / 12
        col = (int(10 + 200 * (1 - fade)), int(90 + 120 * (1 - fade)), 255)
        r = 5 + 6 * (1 - fade)
        d.ellipse((x - r, y - r, x + r, y + r), fill=col)


def _draw_rings(d, i, n):
    for ring in range(3):
        t = ((i / n) + ring / 3.0) % 1.0
        r = 8 + t * 108
        fade = 1 - t
        col = (int(0 * fade), int(210 * fade), int(255 * fade))
        d.ellipse((120 - r, 120 - r, 120 + r, 120 + r), outline=col, width=max(1, int(7 * fade)))


def _draw_confetti(d, i, n):
    colors = [(255, 70, 170), (0, 210, 255), (255, 176, 0), (70, 220, 110), (170, 110, 255)]
    t = i / n
    for k in range(40):
        ang = math.radians((k * 29) % 360)
        r = (30 + (k % 7) * 13) * t
        x, y = 120 + r * math.sin(ang), 120 - r * math.cos(ang) * 0.9 + 60 * t * t
        rad = 5 - 3 * t
        if rad > 0.5:
            d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=colors[k % len(colors)])


def _draw_fire(d, i, n):
    t = i / n
    for layer, h, col in ((0, 95, (255, 205, 60)), (1, 75, (255, 120, 30)), (2, 55, (230, 40, 20))):
        wob = 10 * math.sin(2 * math.pi * (t * 3 + layer * 0.3))
        pts = [(120 - 42 + layer * 7, 205), (120 + wob, 205 - h), (120 + 42 - layer * 7, 205)]
        d.polygon(pts, fill=col)


def _draw_rainbow(d, i, n):
    for a in range(0, 360, 3):
        hue = ((a + i * 360 / n) % 360) / 360.0
        r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(hue, 1, 1))
        rad = math.radians(a)
        x0, y0 = 120 + 30 * math.sin(rad), 120 - 30 * math.cos(rad)
        x1, y1 = 120 + 116 * math.sin(rad), 120 - 116 * math.cos(rad)
        d.line((x0, y0, x1, y1), fill=(r, g, b), width=5)


def _draw_dots(d, i, n):
    for k in range(3):
        phase = ((i / n) - k * 0.15) % 1.0
        bounce = abs(math.sin(phase * math.pi))
        x, y = 90 + k * 30, 150 - 50 * bounce
        d.ellipse((x - 11, y - 11, x + 11, y + 11), fill=(0, 210, 255))


def _draw_check(d, i, n):
    t = min(1.0, (i / n) / 0.4) if n else 1.0
    cx, cy = 120, 120
    pts = [(-42, 4), (-10, 36), (52, -36)]
    scaled = [(cx + x * t, cy + y * t) for x, y in pts]
    if t > 0.02:
        d.line(scaled, fill=(70, 220, 110), width=int(14 * t) + 2, joint="curve")
    d.ellipse((cx - 92, cy - 92, cx + 92, cy + 92), outline=(70, 220, 110), width=4)


def _draw_bounce(d, i, n):
    t = i / n
    for k, col in enumerate(((255, 70, 170), (0, 210, 255), (255, 176, 0))):
        ph = (t + k / 3.0) % 1.0
        x = 60 + 60 * k
        y = 190 - abs(math.sin(ph * math.pi)) * 120
        sq = 1 - 0.25 * (1 - abs(math.sin(ph * math.pi))) ** 6
        d.ellipse((x - 20, y - 20 * sq, x + 20, y + 20 * sq), fill=col)
    d.line((30, 212, 210, 212), fill=(70, 76, 92), width=3)


def _draw_equalizer(d, i, n):
    t = i / n
    for b in range(11):
        h = 20 + 80 * (0.5 + 0.5 * math.sin(2 * math.pi * (t * (1 + b % 3) + b * 0.13)))
        hue = b / 11.0
        r, g, bl = (int(c * 255) for c in colorsys.hsv_to_rgb(hue * 0.7, 0.9, 1))
        d.rectangle((38 + b * 15, 190 - h, 38 + b * 15 + 10, 190), fill=(r, g, bl))


def _draw_wave(d, i, n):
    t = i / n
    for layer, col in enumerate(((0, 120, 255), (0, 210, 255), (120, 255, 240))):
        pts = [(x, 120 + (18 + layer * 6) * math.sin(2 * math.pi * (x / 90.0 + t * (1 + layer)) + layer))
               for x in range(0, 241, 6)]
        d.line(pts, fill=col, width=5)


def _draw_fireworks(d, i, n):
    t = i / n
    for k, (cx, cy, hue) in enumerate(((80, 90, 0.0), (160, 110, 0.55), (120, 150, 0.3))):
        ph = (t * 1.5 + k * 0.33) % 1.0
        for s in range(14):
            a = 2 * math.pi * s / 14
            r = 8 + 55 * ph
            x, y = cx + r * math.cos(a), cy + r * math.sin(a) + 18 * ph * ph
            fade = 1 - ph
            rgb = tuple(int(c * 255 * fade) for c in colorsys.hsv_to_rgb(hue, 0.8, 1))
            d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=rgb)


def _draw_starfield(d, i, n):
    t = i / n
    for k in range(60):
        ang = (k * 137.5) % 360
        base = ((k * 17) % 100) / 100.0
        r = ((base + t) % 1.0) ** 2 * 125
        x, y = 120 + r * math.cos(math.radians(ang)), 120 + r * math.sin(math.radians(ang))
        v = int(80 + 175 * (r / 125))
        sz = 1 + 2 * (r / 125)
        d.ellipse((x - sz, y - sz, x + sz, y + sz), fill=(v, v, 255))


def _draw_plasma(d, i, n):
    t = 2 * math.pi * i / n
    for y in range(0, 240, 8):
        for x in range(0, 240, 8):
            v = math.sin(x / 31.0 + t) + math.sin(y / 23.0 - t) + math.sin((x + y) / 41.0 + t)
            h = (v + 3) / 6.0
            r, g, b = (int(c * 255) for c in colorsys.hsv_to_rgb(h, 0.9, 0.95))
            d.rectangle((x, y, x + 7, y + 7), fill=(r, g, b))


def _draw_radar(d, i, n):
    for r in (30, 60, 90, 118):
        d.ellipse((120 - r, 120 - r, 120 + r, 120 + r), outline=(0, 90, 60), width=2)
    d.line((2, 120, 238, 120), fill=(0, 70, 50), width=1)
    d.line((120, 2, 120, 238), fill=(0, 70, 50), width=1)
    a0 = 360.0 * i / n
    for j in range(24):
        a = math.radians(a0 - j * 3 - 90)
        g = int(255 * (1 - j / 24.0))
        d.line((120, 120, 120 + 116 * math.cos(a), 120 + 116 * math.sin(a)), fill=(0, g, int(g * 0.4)), width=3)
    b = math.radians(a0 * 2 - 90)
    d.ellipse((120 + 70 * math.cos(b) - 5, 120 + 70 * math.sin(b) - 5, 120 + 70 * math.cos(b) + 5, 120 + 70 * math.sin(b) + 5), fill=(255, 70, 170))


def _draw_spiral(d, i, n):
    t = 2 * math.pi * i / n
    for k in range(150):
        a = k * 0.28 + t
        r = 3 + k * 0.78
        x, y = 120 + r * math.cos(a), 120 + r * math.sin(a)
        rr = 2 + k / 40.0
        rgb = tuple(int(c * 255) for c in colorsys.hsv_to_rgb((k / 150.0 + i / n) % 1.0, 0.85, 1))
        d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=rgb)


GIF_PRESETS = {
    "Heartbeat": (24, 70, _draw_heartbeat),
    "Spinner": (24, 55, _draw_spinner),
    "Pulse Rings": (30, 55, _draw_rings),
    "Confetti Burst": (20, 70, _draw_confetti),
    "Fire": (16, 85, _draw_fire),
    "Rainbow Sweep": (36, 45, _draw_rainbow),
    "Loading Dots": (24, 55, _draw_dots),
    "Checkmark Pop": (18, 70, _draw_check),
    "Bouncing Balls": (24, 55, _draw_bounce),
    "Equalizer": (24, 60, _draw_equalizer),
    "Ocean Wave": (24, 60, _draw_wave),
    "Fireworks": (24, 70, _draw_fireworks),
    "Starfield": (24, 55, _draw_starfield),
    "Plasma": (20, 80, _draw_plasma),
    "Radar": (30, 60, _draw_radar),
    "Hypno Spiral": (24, 60, _draw_spiral),
}


def build_preset(name):
    n_frames, duration_ms, draw_fn = GIF_PRESETS[name]
    return _preset_frames(n_frames, duration_ms, draw_fn)


def preset_thumb(name, size=56):
    """One representative frame only - the library grid must not render every frame of every preset at startup."""
    n_frames, _d, draw_fn = GIF_PRESETS[name]
    im = Image.new("RGB", (LCD, LCD), (10, 10, 14))
    draw_fn(ImageDraw.Draw(im), n_frames // 3, n_frames)
    return round_thumb(im, size)


def round_thumb(im, size=56):
    """Centre-crop to a square, shrink, and mask to a circle - how the picture will look on the round screen."""
    w, h = im.size
    s = min(w, h)
    sq = im.convert("RGB").crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)).resize((size, size), RESAMPLE)
    m = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    m = m.resize((size, size), RESAMPLE)
    return Image.composite(sq, Image.new("RGB", (size, size), (30, 33, 40)), m)


# ============================================================================ "My GIFs" library (local folder)
def gif_lib_dir():
    d = Path(os.environ.get("DESK_COMPANION_GIFS") or Path.home() / ".desk_companion_gifs")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def _safe_name(name):
    keep = "".join(c if (c.isalnum() or c in " -_.") else "_" for c in name).strip(" ._") or "gif"
    return keep[:60]


def lib_list():
    d = gif_lib_dir()
    try:
        files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".gif"]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def lib_add(src, name=None):
    """Copy a .gif file (path) or raw bytes into the library; returns the stored path (never overwrites)."""
    d = gif_lib_dir()
    base = _safe_name(name or (Path(src).stem if not isinstance(src, (bytes, bytearray)) else "gif"))
    dst, k = d / f"{base}.gif", 2
    while dst.exists():
        dst = d / f"{base}-{k}.gif"
        k += 1
    if isinstance(src, (bytes, bytearray)):
        dst.write_bytes(bytes(src))
    else:
        dst.write_bytes(Path(src).read_bytes())
    return dst


def lib_delete(path):
    try:
        Path(path).unlink()
        return True
    except OSError:
        return False


def gif_file_thumb(path, size=56):
    with Image.open(path) as im:
        im.seek(0)
        return round_thumb(im.copy(), size)


# ============================================================================ online GIF search (Tenor / GIPHY, bring your own key)
TENOR_BASE = os.environ.get("DESK_COMPANION_TENOR_BASE", "https://tenor.googleapis.com/v2")
GIPHY_BASE = os.environ.get("DESK_COMPANION_GIPHY_BASE", "https://api.giphy.com/v1")
ONLINE_PROVIDERS = {
    "Tenor": "https://developers.google.com/tenor/guides/quickstart",
    "GIPHY": "https://developers.giphy.com/",
}
MAX_ONLINE_BYTES = 6_000_000


def _http_get(url, timeout=12, limit=MAX_ONLINE_BYTES):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "DeskCompanion/1.2"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        hint = " - is the API key right?" if e.code in (400, 401, 403) else " - rate limit reached, try again later" if e.code == 429 else ""
        raise ValueError(f"the server answered HTTP {e.code}{hint}") from None
    except urllib.error.URLError as e:
        raise ValueError(f"no connection ({e.reason})") from None
    except OSError as e:                                           # timeouts, resets
        raise ValueError(f"no connection ({e})") from None
    if len(data) > limit:
        raise ValueError("file is too large")
    return data


def online_search(provider, key, query="", limit=24):
    """Returns [{"title", "thumb", "url"}]. Empty query = trending / featured."""
    import urllib.parse
    q = (query or "").strip()
    if not key:
        raise ValueError(f"enter your {provider} API key first (free, see the link below)")
    if provider == "Tenor":
        ep = "search" if q else "featured"
        qs = {"key": key, "client_key": "desk_companion", "limit": limit, "media_filter": "tinygif,gif,mediumgif", "contentfilter": "medium"}
        if q:
            qs["q"] = q
        data = json.loads(_http_get(f"{TENOR_BASE}/{ep}?{urllib.parse.urlencode(qs)}").decode("utf-8", "replace"))
        out = []
        for r in data.get("results", []):
            mf = r.get("media_formats", {})
            full = mf.get("mediumgif") or mf.get("gif") or mf.get("tinygif")
            th = mf.get("tinygif") or mf.get("gif") or full
            if full and th:
                out.append({"title": r.get("content_description") or r.get("title") or "", "thumb": th["url"], "url": full["url"]})
        return out
    if provider == "GIPHY":
        ep = "search" if q else "trending"
        qs = {"api_key": key, "limit": limit, "rating": "g"}
        if q:
            qs["q"] = q
        data = json.loads(_http_get(f"{GIPHY_BASE}/gifs/{ep}?{urllib.parse.urlencode(qs)}").decode("utf-8", "replace"))
        out = []
        for r in data.get("data", []):
            im = r.get("images", {})
            full = im.get("fixed_height") or im.get("downsized") or im.get("original")
            th = im.get("fixed_height_small") or im.get("fixed_width_small") or full
            if full and th:
                out.append({"title": r.get("title", ""), "thumb": th["url"], "url": full["url"]})
        return out
    raise ValueError(f"unknown provider {provider}")


def online_download(url):
    data = _http_get(url, timeout=25)
    if data[:3] != b"GIF":
        raise ValueError("the download is not a GIF file")
    return data


# ============================================================================ serial device
class DeviceError(Exception):
    pass


ESP_PID_HINTS = {
    0x1001: "ESP32-S3 built-in USB (ROM bootloader / 'Hardware CDC and JTAG'). The board is in download mode (BOOT held / "
            "blank chip) or the sketch was built with USB Mode = Hardware CDC and JTAG.",
    0x4001: "ESP32-S3 running an Arduino sketch in USB-OTG (TinyUSB) mode - this is what DeskCompanion should look like.",
    0x822B: "Waveshare ESP32-S3-Zero running an Arduino sketch in USB-OTG (TinyUSB) mode - this is what DeskCompanion should look like.",
    0x0002: "ESP32-S3 running an Arduino sketch in USB-OTG (TinyUSB) mode - this is what DeskCompanion should look like.",
}


def is_espressif(p):
    text = f"{p.description} {p.manufacturer} {p.product}".lower()
    return p.vid == ESPRESSIF_VID or any(w in text for w in ("desk companion", "deskcompanion", "espressif", "esp32", "usb jtag"))


def list_serial_ports():
    """Every serial port with VID:PID and a hint - used by the Dev tab and the connection diagnostics."""
    out = []
    for p in list_ports.comports():
        vid, pid = p.vid, p.pid
        out.append({"device": p.device, "vid": vid, "pid": pid, "desc": p.description or "", "mfr": p.manufacturer or "",
                    "product": p.product or "", "serial": p.serial_number or "", "esp": is_espressif(p),
                    "hint": ESP_PID_HINTS.get(pid, "Espressif USB device") if vid == ESPRESSIF_VID else ""})
    return out


def candidate_ports():
    return [p["device"] for p in list_serial_ports() if p["esp"]]


def fmt_vidpid(p):
    return "----:----" if p["vid"] is None else f"{p['vid']:04X}:{p['pid'] or 0:04X}"


ERR_TEXT = {
    "fs_busy": "the pad is still preparing its storage (first boot after flashing can take up to ~30 s) - try again in a moment",
    "nvs_full": "the pad's settings memory is full - remove some large macros / text snippets",
    "no_space": "not enough free flash on the pad for this GIF",
    "crc": "the data arrived damaged (CRC mismatch) - try again",
    "no_display": "the pad's display is not available (safe mode or init failed)",
    "no_hid": "this firmware build has no USB keyboard (USB Mode must be 'USB-OTG (TinyUSB)')",
    "pin": "that GPIO number is not available", "pin_protected": "that GPIO is protected (USB / flash / boot / LED pin)",
    "unknown_cmd": "the firmware does not know this command (older firmware?)", "json": "the pad could not parse the message",
    "layout_unsupported_core": "keyboard layouts need arduino-esp32 core 3.0+ when the firmware is built",
    "wifi_disabled": "this firmware is cable-only: its Wi-Fi code is switched off (DC_ENABLE_WIFI = 0 in the sketch)",
    "no_wifi": "save your Wi-Fi name and password on the pad first (Device -> Optional Wi-Fi)",
    "ota_unsupported": "this firmware build has no Wi-Fi update",
    "layer": "this layer number does not exist", "slot": "that GIF slot does not exist", "confirm": "the pad wants an explicit confirmation",
    "what": "the pad does not know that reset target",
}

SIM_PORT = "SIMULATED (no hardware)"
SIM_FS_TOTAL = 1_500_000
SIM_FS_RESERVED = 400_000           # firmware + always-on demo gif, matching the real pad's flash budget roughly
QUIET_CMDS = {"stats"}              # sent once a second - not worth a line in the Dev terminal


def compact_json(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def rgb565be_image(raw, w=240, h=240):
    """Raw big-endian RGB565 frame buffer (as sent by the firmware's snapshot command) -> PIL image."""
    import struct
    px = struct.unpack(f">{w * h}H", raw)
    img = Image.new("RGB", (w, h))
    img.putdata([(((p >> 11) & 31) * 255 // 31, ((p >> 5) & 63) * 255 // 63, (p & 31) * 255 // 31) for p in px])
    return img


FW_BUNDLED = "1.5.0"                     # version of firmware/DeskCompanion.bin shipped with this app
LAYERS, GIF_SLOTS = 3, 4
HOST_OPS = ("url", "app", "shell", "clipboard", "file", "notify", "snippet", "clip", "script",
            "appvol", "dnd", "audio_out", "mic", "shot", "translate", "ai", "webhook", "layout", "cliphist")
DEFAULT_LAYERS = [
    [{"type": "combo", "val": ["PRIMARY", "c"]}, {"type": "combo", "val": ["PRIMARY", "v"]}, {"type": "combo", "val": ["PRIMARY", "z"]},
     {"type": "media", "val": "PLAY_PAUSE"}, {"type": "media", "val": "MUTE"}, {"type": "media", "val": "VOL_UP"}, {"type": "media", "val": "VOL_DOWN"}],
    [{"type": "media", "val": "PREV"}, {"type": "media", "val": "PLAY_PAUSE"}, {"type": "media", "val": "NEXT"},
     {"type": "media", "val": "STOP"}, {"type": "media", "val": "MUTE"}, {"type": "media", "val": "VOL_UP"}, {"type": "media", "val": "VOL_DOWN"}],
    [{"type": "combo", "val": ["ALT", "LEFT"]}, {"type": "combo", "val": ["ALT", "RIGHT"]}, {"type": "combo", "val": ["F5"]},
     {"type": "combo", "val": ["PRIMARY", "t"]}, {"type": "combo", "val": ["PRIMARY", "w"]}, {"type": "combo", "val": ["PGDN"]}, {"type": "combo", "val": ["PGUP"]}],
]


def spec_ok(spec):
    """Same acceptance rules as the firmware's parseSpec() - used by the simulated pad (and by the UI before it uploads)."""
    t, v = spec.get("type", "none"), spec.get("val")
    def layer_ok(x):
        return x in ("next", "prev") or (isinstance(x, int) and not isinstance(x, bool) and 0 <= x < LAYERS)
    def host_ok(o):
        return (isinstance(o, dict) and o.get("op") in HOST_OPS and len(str(o.get("arg", ""))) <= 400
                and (o.get("op") == "clipboard" or bool(o.get("arg"))))
    def mouse_ok(o):
        if not isinstance(o, dict):
            return False
        if "wheel" in o:
            mods = o.get("mods", [])
            return (isinstance(o["wheel"], int) and isinstance(mods, list) and all(isinstance(x, str) and x.upper() in ("CTRL", "SHIFT", "ALT", "GUI") for x in mods)
                    and isinstance(o.get("h", False), bool))
        if "move" in o:
            return isinstance(o["move"], list) and len(o["move"]) == 2
        return o.get("btn", "left") in ("left", "right", "middle", "back", "forward") and o.get("act", "click") in ("click", "double", "down", "up")
    if t == "none":
        return True
    if t == "combo":
        return isinstance(v, list) and 0 < len(v) <= 6 and all(isinstance(k, str) and (valid_key(k) or k.upper() in ("PRIMARY", "MOD", "CTRL", "SHIFT", "ALT", "GUI", "WIN", "CMD", "META", "OPT", "OPTION", "CONTROL", "SUPER")) for k in v)
    if t == "media":
        return v in ("PLAY_PAUSE", "NEXT", "PREV", "STOP", "MUTE", "VOL_UP", "VOL_DOWN", "FF", "REWIND")
    if t == "text":
        return isinstance(v, str)
    if t == "layer":
        return layer_ok(v)
    if t == "host":
        return host_ok(v)
    if t == "mouse":
        return mouse_ok(v)
    if t == "panic":
        return True
    if t in ("toggle", "random"):
        lo, hi = (2, 2) if t == "toggle" else (2, 6)
        return (isinstance(v, list) and lo <= len(v) <= hi and all(isinstance(x, dict) and x.get("type") not in ("toggle", "random") and spec_ok(x) for x in v))
    if t == "macro":
        if not isinstance(v, list) or len(v) > 64:
            return False
        for o in v:
            if not isinstance(o, dict):
                return False
            if "combo" in o:
                if not spec_ok({"type": "combo", "val": o["combo"]}):
                    return False
            elif "text" in o or "delay" in o:
                pass
            elif "media" in o:
                if not spec_ok({"type": "media", "val": o["media"]}):
                    return False
            elif "host" in o:
                if not host_ok(o["host"]):
                    return False
            elif "mouse" in o:
                if not mouse_ok(o["mouse"]):
                    return False
            elif "panic" in o:
                pass
            elif "layer" in o:
                if not layer_ok(o["layer"]):
                    return False
            else:
                return False
        return True
    return False


NEW13_CAPS = ["hostx", "gestures", "dialaccel", "clockstyle", "saver", "nightdim"]       # firmware 1.3 additions (see hello "caps")
NEW14_CAPS = ["screens", "pressturn", "toggle", "wheelmods", "ledfx", "reminders", "habits"]     # firmware 1.4 additions
NEW15_CAPS = ["hostx2"]                                                                           # firmware 1.5 additions (more host ops; see hello "caps")
SETTINGS_DEFAULT = {"dial_accel": 0, "clock_style": 0, "saver_s": 0, "saver_style": 1, "night_on": False, "night_from": 22, "night_to": 7, "night_level": 30, "mode_mask": 0x3F}
SETTINGS_RANGE = {"dial_accel": (0, 2), "clock_style": (0, 3), "saver_s": (0, 3600), "saver_style": (1, 3), "night_from": (0, 23), "night_to": (0, 23), "night_level": (5, 255), "mode_mask": (1, 4095)}
LEGACY_CMDS = {"layer", "info_cards", "gif_list", "gif_cfg", "factory", "boot_opt", "safe_retry", "ota"}   # unknown to firmware 1.1


CORE_CMDS = {"stats", "hello", "ping", "echo", "info", "led", "gpio", "inputs", "events", "run", "reboot", "display", "snapshot"}   # all CoreBringup knows


class SimFirmware:
    """Implements the DeskCompanion wire protocol in pure Python, standing in for real hardware so the
    app's connect / remap / brightness / GIF-upload / Dev-tab code paths can be exercised with nothing plugged in."""

    def __init__(self, emit, legacy=None):
        self.emit = emit                      # callable(bytes) -> pushes firmware->app bytes
        self.legacy = bool(os.environ.get("DESK_COMPANION_SIM_LEGACY")) if legacy is None else legacy   # behave like firmware 1.1.0 (single layer, 5 modes)
        self.wifi = bool(os.environ.get("DESK_COMPANION_SIM_WIFI"))        # firmware built with DC_ENABLE_WIFI=1 (default build: cable only)
        self.fw12 = bool(os.environ.get("DESK_COMPANION_SIM_V12"))          # behave like firmware 1.2.0 (layers, no gestures / settings / new host ops)
        self.fw13 = bool(os.environ.get("DESK_COMPANION_SIM_V13"))          # behave like firmware 1.3.0 (everything but the 1.4 screens / actions)
        self.fw14 = bool(os.environ.get("DESK_COMPANION_SIM_V14"))          # behave like firmware 1.4.0 (everything but the 1.5 additions)
        self.reminders = [{"m": 0, "t": ""} for _ in range(3)]
        self.habits = {"names": ["WATER", "MOVE", "READ", "SLEEP", "FOCUS"], "today": [0] * 5}
        self._tgl = {}
        self.gest = {}                        # (layer, key, "hold"|"double") -> spec
        self.settings = dict(SETTINGS_DEFAULT)
        self.core = bool(os.environ.get("DESK_COMPANION_SIM_CORE"))        # behave like the CoreBringup diagnostic sketch (diagnostic commands only)
        self.cmd_count = {}                   # cmd -> times received (tests: no command spam)
        self._buf = b""
        self.mode, self.bright, self.osv, self.layout = 1, 200, "win", "en_US"
        self.slots = {}                       # layer 0 (kept under this name: tests and tools read it)
        self.layers = [self.slots, {}, {}]
        self.layer = 0
        self.gifs = {0: 60_000}              # slot -> bytes; slot 0 starts as the built-in demo animation, like the real pad
        self.gif_cur, self.gif_rot, self._up_slot = 0, 0, 0
        self.cards, self.badges, self.card_rot = [], [], 6
        self.nodisp, self.ota, self.crashes = False, False, 0
        self.disp_why = ""                   # "init_hang" / "skipped_after_hang" when the real pad's display start-up stalled
        self.host_log = []                   # host actions the pad asked for (tests / Dev tab)
        self._up = None
        self.led = {"mode": 0, "r": 0, "g": 0, "b": 0}
        self.events = False
        self.pins = {}
        self.t0 = time.monotonic()
        self.display = None                   # (r, g, b) of an active "fill" test pattern, or a pattern name

    def fw_text(self):
        return "1.2.0" if self.fw12 else "1.3.0" if self.fw13 else "1.4.0" if self.fw14 else FW_BUNDLED

    def feed(self, data):
        self._buf += data
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            self._handle(line)

    def _send(self, obj):
        self.emit((compact_json(obj) + "\n").encode())

    @property
    def gif_present(self):
        return bool(self.gifs)

    @property
    def gif_bytes_used(self):
        return sum(self.gifs.values())

    def _fs_free(self):
        return max(0, SIM_FS_TOTAL - SIM_FS_RESERVED - self.gif_bytes_used)

    def _gif_list(self, reply):
        reply({"ok": True, "evt": "gif_list", "slots": [{"s": k, "size": v} for k, v in sorted(self.gifs.items())],
               "cur": self.gif_cur, "rot": self.gif_rot, "max": GIF_SLOTS, "fs_free": self._fs_free()})

    def _spec_for(self, lay, i):
        return self.layers[lay].get(i) or DEFAULT_LAYERS[lay][i - 1]

    def _run_spec(self, spec):
        """What pressing a key does on the real pad that is visible to the app: layer switches and host actions."""
        t, v = spec.get("type"), spec.get("val")
        if t == "toggle" and isinstance(v, list) and len(v) == 2:          # alternates between its two halves, like the firmware
            key = compact_json(v)
            half = self._tgl.get(key, 0)
            self._tgl[key] = 1 - half
            return self._run_spec(v[half])
        if t == "random" and isinstance(v, list) and v:
            return self._run_spec(random.choice(v))
        if t == "panic":
            return
        steps = v if t == "macro" else [{t: v}]
        for st in steps:
            if "layer" in st:
                x = st["layer"]
                self.layer = (self.layer + 1) % LAYERS if x == "next" else (self.layer - 1) % LAYERS if x == "prev" else int(x)
                self._send({"evt": "layer", "n": self.layer})
            elif "host" in st:
                self.host_log.append(st["host"])
                self._send({"evt": "host", "op": st["host"]["op"], "arg": st["host"].get("arg", "")})

    def _up_ms(self):
        return int((time.monotonic() - self.t0) * 1000)

    @staticmethod
    def _readable(p):
        return isinstance(p, int) and (0 <= p <= 18 or p == 21 or 33 <= p <= 48)

    @staticmethod
    def _drivable(p):
        return isinstance(p, int) and (1 <= p <= 18 or 33 <= p <= 42)

    PIN_USE = {7: "TFT BLK", 8: "TFT CS", 9: "TFT DC", 10: "TFT RES", 11: "TFT SDA", 12: "TFT SCL", 13: "ENC A", 14: "ENC B",
               15: "ENC SW", 1: "K1", 2: "K2", 4: "K3", 5: "K4", 6: "K5", 21: "RGB LED", 0: "BOOT button"}

    def _handle(self, line):
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            self._send({"ok": False, "err": "json"})
            return
        if not isinstance(msg, dict):
            self._send({"ok": False, "err": "json"})
            return
        cmd = msg.get("cmd")
        rid = msg.get("id")

        def reply(obj):
            if isinstance(rid, int):
                obj["id"] = rid
            if self.legacy:
                if obj.get("evt") in ("hello", "info"):
                    for k in ("layer", "layers", "modes", "gifs", "gif_rot", "caps", "safe_why", "disp_why", "nodisp", "ota", "ip"):
                        obj.pop(k, None)
                    obj["fw"] = "1.1.0"
                if obj.get("evt") == "keys":
                    for k in ("layer", "cur", "layers"):
                        obj.pop(k, None)
            self._send(obj)

        self.cmd_count[cmd] = self.cmd_count.get(cmd, 0) + 1
        if (self.fw12 and cmd in ("settings", "gesture_test")) or ((self.fw12 or self.fw13) and cmd in ("screens", "habits", "reminders")):
            reply({"ok": False, "err": "unknown_cmd"})
            return
        if self.core:
            if cmd not in CORE_CMDS:
                reply({"ok": False, "err": "unknown_cmd"})
                return
            if cmd in ("hello", "info"):
                orig = reply

                def reply(obj, _orig=orig):
                    obj.update(fw="1.1.0-core", core_only=True)
                    for k in ("layer", "layers", "modes", "gifs", "gif_rot", "caps"):
                        obj.pop(k, None)
                    _orig(obj)
        if self.legacy:
            if cmd in LEGACY_CMDS:
                reply({"ok": False, "err": "unknown_cmd"})
                return
            if cmd == "mode" and not 1 <= int(msg.get("val", 1)) <= 5:
                reply({"ok": True, "evt": "mode"})
                return
            if cmd in ("remap", "getkeys", "reset_keys"):
                msg.pop("layer", None)              # the old firmware has one key table and ignores the field
                if cmd == "getkeys":
                    msg.pop("slot", None)

        if cmd == "stats":
            return                             # 1 Hz telemetry, no reply - matches the real firmware
        if cmd == "hello":
            reply({"ok": True, "evt": "hello", "dev": "desk-companion", "fw": self.fw_text(),
                   "layer": self.layer, "layers": LAYERS, "modes": 6 if (self.fw12 or self.fw13) else NUM_MODES, "gifs": len(self.gifs), "gif_rot": self.gif_rot,
                   "caps": ["layers", "mouse", "host", "info", "gifslots", "factory"] + ([] if self.fw12 else NEW13_CAPS) + ([] if self.fw12 or self.fw13 else NEW14_CAPS)
                           + ([] if self.fw12 or self.fw13 or self.fw14 else NEW15_CAPS) + (["wifi", "ota"] if self.wifi else []),
                   "mode": self.mode, "bright": self.bright, "os": self.osv, "gif": self.gif_present,
                   "fs_free": self._fs_free(), "fs_total": SIM_FS_TOTAL, "synced": False, "layout": self.layout,
                   "hid": True, "disp": True, "fs": True, "fs_state": "ready", "safe": False, "led_pin": 21})
        elif cmd == "ping":
            r = {"ok": True, "evt": "pong", "up": self._up_ms()}
            if "t" in msg:
                r["t"] = msg["t"]
            reply(r)
        elif cmd == "echo":
            reply({"ok": True, "evt": "echo", "data": msg.get("data")})
        elif cmd == "info":
            reply({"ok": True, "evt": "info", "fw": self.fw_text(), "build": "simulated", "safe_why": "", "disp_why": self.disp_why, "bl": self.bright, "saver_on": False, "nodisp": self.nodisp, "ota": self.ota, "ip": "", "layer": self.layer, "chip": "ESP32-S3 (simulated)", "rev": 0,
                   "cores": 2, "cpu_mhz": 240, "flash": 4194304,
                   "heap": 210_000, "heap_min": 190_000, "heap_blk": 110_000, "psram": 0, "temp": 31.5, "up_ms": self._up_ms(),
                   "reset": "power-on", "crashes": self.crashes, "safe": False, "core": "sim", "usb_mode": 0, "cdc_boot": 1, "hid": True,
                   "tft": True, "sim": True, "ok_prefs": True, "ok_fs": True, "fs_state": "ready", "ok_sprite": True, "ok_disp": True,
                   "fs_free": self._fs_free(), "fs_total": SIM_FS_TOTAL, "rx_ms_ago": 0, "events": self.events,
                   "gpio_touched": False, "led_pin": 21, "led_mode": self.led["mode"], "mode": self.mode, "bright": self.bright,
                   "boot": "0:power-on=ok;1:prefs=ok;2:usb=ok;3:fs=ok;4:display=ok;5:ready=ok;"})
        elif cmd == "led":
            m = msg.get("mode", "")
            modes = {"auto": 0, "off": 1, "solid": 2, "blink": 3, "rainbow": 4, "breathe": 5, "fire": 6}
            if "alert" in msg and not (self.fw12 or self.fw13):
                self.led["alert"] = str(msg["alert"])
            if "hex" in msg:
                try:
                    v = int(str(msg["hex"]).lstrip("#"), 16)
                    self.led.update(r=(v >> 16) & 255, g=(v >> 8) & 255, b=v & 255, mode=2)
                except ValueError:
                    pass
            if any(k in msg for k in ("r", "g", "b")):
                for k in "rgb":
                    self.led[k] = max(0, min(255, int(msg.get(k, 0) or 0)))
                self.led["mode"] = 2
            if m:
                if m not in modes:
                    reply({"ok": False, "err": "led_mode"})
                    return
                self.led["mode"] = modes[m]
            reply({"ok": True, "evt": "led", "pin": 21, **self.led})
        elif cmd == "gpio":
            op = msg.get("op", "read")
            if op == "scan":
                reply({"ok": True, "evt": "gpio_scan", "pins": [[p, self.pins.get(p, 1)] for p in range(49) if self._readable(p)]})
                return
            p = msg.get("pin", -1)
            if not self._readable(p):
                reply({"ok": False, "err": "pin"})
                return
            if op != "read":
                if not self._drivable(p):
                    reply({"ok": False, "err": "pin_protected"})
                    return
                if op in ("high", "low", "pullup", "input"):
                    self.pins[p] = 0 if op == "low" else 1
                else:
                    reply({"ok": False, "err": "op"})
                    return
            reply({"ok": True, "evt": "gpio", "pin": p, "val": self.pins.get(p, 1), "use": self.PIN_USE.get(p, "")})
        elif cmd == "inputs":
            reply({"ok": True, "evt": "inputs", "keys": [0, 0, 0, 0, 0], "enc_sw": 0, "enc_a": 1, "enc_b": 1, "enc_pos": 0})
        elif cmd == "events":
            self.events = bool(msg.get("val", True))
            reply({"ok": True, "evt": "events"})
        elif cmd == "display":
            t = msg.get("test", "fill")
            if t == "off":
                self.display = None
            elif t == "fill":
                self.display = (int(msg.get("r", 0)), int(msg.get("g", 0)), int(msg.get("b", 0)))
            elif t in ("bars", "grid", "text"):
                self.display = t
            else:
                reply({"ok": False, "err": "pattern"})
                return
            reply({"ok": True, "evt": "display"})
        elif cmd == "snapshot":
            self._snapshot(rid)
        elif cmd == "run":
            reply({"ok": True, "evt": "run", "steps": 1})
        elif cmd == "input":
            if "k" in msg:
                k = msg["k"]
                if not isinstance(k, int) or not 1 <= k <= 5:
                    reply({"ok": False, "err": "key"})
                    return
                if self.events:
                    self._send({"evt": "key", "k": k, "v": 1})
                    self._send({"evt": "key", "k": k, "v": 0})
                g = msg.get("g", "tap") if not self.fw12 else "tap"
                if g == "tap":
                    self._run_spec(self._spec_for(self.layer, k))
                elif g in ("hold", "double"):
                    sp = self.gest.get((self.layer, k, g))
                    if sp:
                        self._run_spec(sp)
                else:
                    reply({"ok": False, "err": "gesture"})
                    return
            elif "turn" in msg:
                t = msg["turn"]
                if not isinstance(t, int) or t == 0 or abs(t) > 20:
                    reply({"ok": False, "err": "turn"})
                    return
                if self.events:
                    self._send({"evt": "enc", "d": 1 if t > 0 else -1, "pos": t})
                runs = 1
                pt = self.layers[self.layer].get(8 if t > 0 else 9) if (msg.get("press") and not (self.fw12 or self.fw13)) else None
                if pt:
                    self._run_spec(pt)
                    reply({"ok": True, "evt": "input", "runs": 1})
                    return
                if not self.fw12:
                    dt = msg.get("dt", 1000)
                    per = dt / abs(t)
                    lvl = self.settings["dial_accel"]
                    mult = 1 if lvl == 0 else (3 if lvl == 1 else 6) if per < 35 else (2 if lvl == 1 else 4) if per < 80 else 2 if (lvl == 2 and per < 140) else 1
                    runs = min(24, abs(t) * mult)
                for _ in range(runs):
                    self._run_spec(self._spec_for(self.layer, 6 if t > 0 else 7))
                reply({"ok": True, "evt": "input", "runs": runs})
                return
            elif msg.get("click") or msg.get("hold"):
                if self.events:
                    self._send({"evt": "encsw", "v": 1})
                    self._send({"evt": "encsw", "v": 0})
            else:
                reply({"ok": False, "err": "input"})
                return
            reply({"ok": True, "evt": "input"})
        elif cmd == "getkeys":
            lay = msg.get("layer", 0)
            if not isinstance(lay, int) or not 0 <= lay < LAYERS:
                reply({"ok": False, "err": "layer"})
                return
            base = {"ok": True, "evt": "keys", "layer": lay, "cur": self.layer, "layers": LAYERS}
            if "slot" in msg:
                sl = msg["slot"]
                if not isinstance(sl, int) or not 1 <= sl <= (7 if (self.fw12 or self.fw13) else 9):
                    reply({"ok": False, "err": "key"})
                    return
                if sl > 7:
                    sp = self.layers[lay].get(sl)
                    reply(dict(base, s=sl, **{"def": sp is None}, **({"spec": sp} if sp else {})))
                    return
                g = msg.get("gesture", "tap")
                if g != "tap" and not self.fw12:
                    if g not in ("hold", "double") or sl > 5:
                        reply({"ok": False, "err": "gesture"})
                        return
                    sp = self.gest.get((lay, sl, g))
                    reply(dict(base, s=sl, g=g, set=sp is not None, **({"spec": sp} if sp else {})))
                    return
                reply(dict(base, s=sl, spec=self._spec_for(lay, sl), **{"def": sl not in self.layers[lay]}))
                return
            slots = []
            for i in range(1, 8):
                s = self.layers[lay].get(i)
                j = compact_json({"type": s["type"], "val": s["val"]}) if s else ""
                slots.append({"s": i, "def": not s, "len": len(j.encode()), "crc": zlib.crc32(j.encode()) & 0xFFFFFFFF if s else 0})
                if i <= 5 and not self.fw12:
                    slots[-1].update(h=(lay, i, "hold") in self.gest, d=(lay, i, "double") in self.gest)
            if not (self.fw12 or self.fw13):
                base["pt"] = [8 in self.layers[lay], 9 in self.layers[lay]]
            reply(dict(base, slots=slots))
        elif cmd == "layer":
            v = msg.get("val")
            n = (self.layer + 1) % LAYERS if v == "next" else (self.layer - 1) % LAYERS if v == "prev" else v
            if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n < LAYERS:
                reply({"ok": False, "err": "layer"})
                return
            if n != self.layer:
                self.layer = n
                self._send({"evt": "layer", "n": n})
            reply({"ok": True, "evt": "layer", "n": n, "layers": LAYERS})
        elif cmd == "info_cards":
            self.cards = [c for c in (msg.get("cards") or []) if isinstance(c, dict)][:4]
            self.badges = [b for b in (msg.get("badges") or []) if isinstance(b, dict)][:4]
            self.card_rot = msg.get("rot", self.card_rot)
            reply({"ok": True, "evt": "info_cards", "cards": len(self.cards), "badges": len(self.badges)})
        elif cmd == "gif_list":
            self._gif_list(reply)
        elif cmd == "gif_cfg":
            if "rot" in msg:
                self.gif_rot = max(0, min(3600, int(msg["rot"])))
            if "slot" in msg:
                if msg["slot"] not in self.gifs:
                    reply({"ok": False, "err": "slot"})
                    return
                self.gif_cur = msg["slot"]
            self._gif_list(reply)
        elif cmd == "factory":
            if not msg.get("confirm"):
                reply({"ok": False, "err": "confirm"})
                return
            what = msg.get("what", "settings")
            if what == "keys":
                for lay in self.layers:
                    lay.clear()
            elif what == "settings":
                for lay in self.layers:
                    lay.clear()
                self.bright, self.mode, self.osv = 200, 1, "win"
            elif what == "gifs":
                self.gifs = {0: 60_000}
            else:
                reply({"ok": False, "err": "what"})
                return
            reply({"ok": True, "evt": "factory"})
        elif cmd == "boot_opt":
            if "nodisp" in msg:
                self.nodisp = bool(msg["nodisp"])
            reply({"ok": True, "evt": "boot_opt", "nodisp": self.nodisp, "safe": False})
        elif cmd == "safe_retry":
            self.crashes = 0
            reply({"ok": True, "evt": "safe_retry"})
        elif cmd == "ota":
            if not self.wifi:
                reply({"ok": False, "err": "wifi_disabled"})
                return
            self.ota = bool(msg.get("val"))
            reply({"ok": True, "evt": "ota", "on": self.ota, "has_pw": bool(msg.get("pass"))})
        elif cmd == "selftest":
            reply({"ok": True, "evt": "selftest", "nvs": True, "fs": True, "heap_ok": True, "heap": 210_000, "led": "cycled",
                   "display": "drawn", "hid": True, "keys": [0, 0, 0, 0, 0]})
        elif cmd == "reboot":
            reply({"ok": True, "evt": "reboot"})
        elif cmd == "remap":
            key, lay = msg.get("key"), msg.get("layer", 0)
            if not isinstance(key, int) or not 1 <= key <= (7 if (self.fw12 or self.fw13) else 9):
                reply({"ok": False, "err": "key"})
            elif not isinstance(lay, int) or not 0 <= lay < LAYERS:
                reply({"ok": False, "err": "layer"})
            elif not self.fw12 and msg.get("gesture", "tap") not in ("tap", "hold", "double"):
                reply({"ok": False, "err": "gesture"})
            elif not self.fw12 and msg.get("gesture", "tap") != "tap" and key > 5:
                reply({"ok": False, "err": "key"})
            elif key >= 8 and msg.get("clear"):
                self.layers[lay].pop(key, None)
                reply({"ok": True, "evt": "remap"})
            elif not self.fw12 and msg.get("gesture", "tap") != "tap" and msg.get("clear"):
                self.gest.pop((lay, key, msg["gesture"]), None)
                reply({"ok": True, "evt": "remap"})
            elif not spec_ok({"type": msg.get("type"), "val": msg.get("val")}):
                reply({"ok": False, "err": "spec"})
            else:
                spec = {"type": msg.get("type"), "val": msg.get("val")}
                g = msg.get("gesture", "tap")
                if g != "tap" and not self.fw12:
                    self.gest[(lay, key, g)] = spec
                else:
                    self.layers[lay][key] = spec                  # firmware 1.2 ignores the unknown "gesture" field and stores a tap action
                reply({"ok": True, "evt": "remap"})
        elif cmd == "reset_keys":
            only = msg.get("layer")
            for n, lay in enumerate(self.layers):
                if only is None or only == n:
                    lay.clear()
                    for k in [k for k in self.gest if k[0] == n]:
                        del self.gest[k]
            reply({"ok": True, "evt": "reset_keys"})
        elif cmd == "screens":
            reply({"ok": True, "evt": "screens", "mask": self.settings["mode_mask"], "reminder_active": False})
        elif cmd == "habits":
            if "names" in msg:
                nm = msg["names"]
                if not (isinstance(nm, list) and len(nm) == 5 and all(isinstance(x, str) and 0 < len(x.strip()) <= 10 and "|" not in x for x in nm)):
                    reply({"ok": False, "err": "names"})
                    return
                self.habits["names"] = [x.strip() for x in nm]
            if "toggle" in msg:
                i = msg["toggle"]
                if not isinstance(i, int) or not 0 <= i <= 4:
                    reply({"ok": False, "err": "habit"})
                    return
                self.habits["today"][i] ^= 1
            reply({"ok": True, "evt": "habits", "synced": True, "names": self.habits["names"], "today": self.habits["today"], "streak": list(self.habits["today"])})
        elif cmd == "reminders":
            if "list" in msg:
                lst = msg["list"]
                ok = isinstance(lst, list) and len(lst) <= 3 and all(isinstance(x, dict) and isinstance(x.get("m", 0), int) and 0 <= x.get("m", 0) <= 1440
                                                                      and len(str(x.get("t", ""))) <= 16 and not any(c in str(x.get("t", "")) for c in "|;")
                                                                      and (x.get("m", 0) == 0 or str(x.get("t", "")).strip()) for x in lst)
                if not ok:
                    reply({"ok": False, "err": "list"})
                    return
                self.reminders = [{"m": x.get("m", 0), "t": str(x.get("t", "")).strip()} for x in lst] + [{"m": 0, "t": ""}] * (3 - len(lst))
            if "test" in msg:
                i = msg["test"]
                if not isinstance(i, int) or not 0 <= i <= 2 or not self.reminders[i]["t"]:
                    reply({"ok": False, "err": "test"})
                    return
                self._send({"evt": "reminder", "i": i, "text": self.reminders[i]["t"]})
            reply({"ok": True, "evt": "reminders", "list": self.reminders, "active": False})
        elif cmd == "settings":
            new = {}
            for k, v in msg.items():
                if k in ("cmd", "id") or (k == "mode_mask" and (self.fw12 or self.fw13)):
                    continue
                if k == "night_on":
                    if not isinstance(v, bool):
                        reply({"ok": False, "err": "settings"})
                        return
                    new[k] = v
                elif k in SETTINGS_RANGE:
                    lo, hi = SETTINGS_RANGE[k]
                    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                        reply({"ok": False, "err": "settings"})
                        return
                    new[k] = v
            self.settings.update(new)
            reply(dict({"ok": True, "evt": "settings", "bl": self.bright, "saver_on": False}, **self.settings))
        elif cmd == "brightness":
            self.bright = max(5, min(255, int(msg.get("val", 200))))
            reply({"ok": True, "evt": "brightness"})
        elif cmd == "mode":
            v = int(msg.get("val", 1))
            if 1 <= v <= (6 if (self.fw12 or self.fw13) else NUM_MODES):
                self.mode = v
            reply({"ok": True, "evt": "mode"})
        elif cmd == "os":
            self.osv = msg.get("val", "win")
            reply({"ok": True, "evt": "os"})
        elif cmd == "layout":
            self.layout = msg.get("val", "en_US")
            reply({"ok": True, "evt": "layout"})
        elif cmd == "wifi" and not self.wifi and not self.legacy:
            reply({"ok": False, "err": "wifi_disabled"})
        elif cmd in ("time", "wifi", "media"):
            reply({"ok": True, "evt": cmd})
        elif cmd == "gif_begin":
            size, slot = int(msg.get("size", 0)), msg.get("slot", 0)
            if not isinstance(slot, int) or not 0 <= slot < GIF_SLOTS:
                reply({"ok": False, "err": "slot"})
                return
            room = SIM_FS_TOTAL - SIM_FS_RESERVED - sum(v for k, v in self.gifs.items() if k != slot)
            if size <= 0 or size + 8192 > room:
                reply({"ok": False, "err": "no_space"})
                return
            self._up = {"size": size, "crc": msg.get("crc", 0), "rx": 0, "seq": 0, "buf": bytearray()}
            self._up_slot = slot
            reply({"ok": True, "evt": "gif_ready", "chunk": 768})
        elif cmd == "gif_chunk":
            up = self._up
            if not up:
                reply({"ok": False, "err": "no_upload"})
                return
            if msg.get("seq") != up["seq"]:
                reply({"ok": False, "err": "seq"})
                return
            try:
                part = base64.b64decode(msg.get("data", ""))
            except Exception:
                self._up = None
                reply({"ok": False, "err": "b64"})
                return
            if up["rx"] + len(part) > up["size"]:
                self._up = None
                reply({"ok": False, "err": "overflow"})
                return
            up["buf"] += part
            up["rx"] += len(part)
            up["seq"] += 1
            reply({"ok": True, "evt": "gif_ack", "seq": msg.get("seq"), "rx": up["rx"]})
        elif cmd == "gif_end":
            up = self._up
            self._up = None
            if not up:
                reply({"ok": False, "err": "no_upload"})
                return
            if up["rx"] != up["size"] or (zlib.crc32(bytes(up["buf"])) & 0xFFFFFFFF) != up["crc"]:
                reply({"ok": False, "err": "crc"})
                return
            self.gifs[self._up_slot] = up["rx"]
            self.gif_cur, self.mode = self._up_slot, M_GIF
            reply({"ok": True, "evt": "gif_done"})
        elif cmd == "gif_abort":
            self._up = None
            reply({"ok": True, "evt": "gif_abort"})
        elif cmd == "gif_delete":
            slot = msg.get("slot", 0)
            if not isinstance(slot, int) or not 0 <= slot < GIF_SLOTS:
                reply({"ok": False, "err": "slot"})
                return
            self._up = None
            self.gifs.pop(slot, None)
            if self.gif_cur == slot:
                self.gif_cur = 0
            if slot == 0 or not self.gifs:
                self.gifs.setdefault(0, 60_000)          # the demo animation is regenerated, as on the real pad
            reply({"ok": True, "evt": "gif_delete"})
        else:
            reply({"ok": False, "err": "unknown_cmd"})

    def _snapshot(self, rid):
        img = Image.new("RGB", (240, 240), (0, 0, 0))
        d = ImageDraw.Draw(img)
        disp = self.display
        if isinstance(disp, tuple):
            d.rectangle((0, 0, 240, 240), fill=disp)
        elif disp == "bars":
            for i, c in enumerate([(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255), (0, 0, 0)]):
                d.rectangle((i * 30, 0, i * 30 + 29, 240), fill=c)
        else:
            d.ellipse((1, 1, 238, 238), outline=(70, 76, 92), width=2)
            d.text((120, 100), "SIMULATED PAD", fill=(255, 255, 255), anchor="mm")
            d.text((120, 125), f"mode {self.mode}  bright {self.bright}", fill=(0, 210, 255), anchor="mm")
            led = self.led
            d.ellipse((105, 150, 135, 180), fill=(led["r"], led["g"], led["b"]), outline=(140, 146, 160))
        raw = b"".join(((r & 0xF8) << 8 | (g & 0xFC) << 3 | b >> 3).to_bytes(2, "big") for r, g, b in img.getdata())
        first = {"ok": True, "evt": "snap_begin", "w": 240, "h": 240, "fmt": "rgb565be", "bytes": len(raw), "chunk": 360}
        if isinstance(rid, int):
            first["id"] = rid
        self._send(first)
        for off in range(0, len(raw), 360):
            self._send({"evt": "snap", "o": off, "d": base64.b64encode(raw[off:off + 360]).decode()})
        self._send({"evt": "snap_end"})


class SimPort:
    """Stands in for serial.Serial: an in-process, OS-independent loopback so Device can talk to a
    SimFirmware without any real COM port, pty, or socket (works identically on Windows/macOS/Linux)."""

    def __init__(self):
        self._out = bytearray()
        self._cv = threading.Condition()
        self._closed = False
        self.sim = SimFirmware(self._feed)

    def _feed(self, data):
        with self._cv:
            if self._closed:
                return
            self._out += data
            self._cv.notify_all()

    @property
    def in_waiting(self):
        with self._cv:
            return len(self._out)

    def read(self, n=1):
        with self._cv:
            if not self._out:
                self._cv.wait(timeout=0.1)       # mirrors pyserial's timeout=0.1 read behaviour
            n = min(n, len(self._out))
            data = bytes(self._out[:n])
            del self._out[:n]
            return data

    def write(self, data):
        if self._closed:
            raise OSError("simulated port closed")
        self.sim.feed(bytes(data))
        return len(data)

    def close(self):
        with self._cv:
            self._closed = True
            self._cv.notify_all()


class Device:
    def __init__(self, on_drop):
        self.ser, self.port, self.info, self.busy, self._ready = None, "", {}, False, False
        self._wlock, self._rlock, self._resp, self._on_drop = threading.Lock(), threading.Lock(), queue.Queue(), on_drop
        self.on_event = None                  # callable(dict): messages without "ok" (key events, ...)
        self.on_line = None                   # callable(direction, text): every line moving over the wire (Dev terminal)
        self._taps = []                       # extra event listeners (snapshot download)
        self.rx_bytes = self.tx_bytes = 0
        self.last_rx = 0.0

    @property
    def connected(self):
        return self.ser is not None and self._ready        # only after the hello handshake succeeded

    @staticmethod
    def _open(port):
        ser = serial.Serial()
        ser.port, ser.baudrate, ser.timeout, ser.write_timeout = port, BAUD, 0.1, 3
        ser.dtr = True                                      # TinyUSB CDC only talks to a host that raised DTR (and RTS)
        ser.rts = True
        ser.open()
        return ser

    def connect(self, port, hello_wait=8.0):
        self.disconnect()
        ser = SimPort() if port == SIM_PORT else self._open(port)
        self.ser = ser
        self.rx_bytes = self.tx_bytes = 0
        threading.Thread(target=self._reader, args=(ser,), daemon=True).start()
        end, last = time.monotonic() + hello_wait, None
        while time.monotonic() < end:                       # the pad may still be booting for a moment after the port opens
            try:
                self.info = self.request({"cmd": "hello"}, timeout=1.5)
                break
            except DeviceError as e:
                last = e
                if self.ser is not ser:
                    break
                time.sleep(0.25)
        else:
            self.disconnect()
            raise last or DeviceError("device did not answer")
        if self.ser is not ser:
            raise last or DeviceError("port closed while connecting")
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

    def _log(self, direction, text):
        cb = self.on_line
        if cb:
            try:
                cb(direction, text)
            except Exception:
                pass

    def _reader(self, ser):
        buf = b""
        while self.ser is ser:
            try:
                data = ser.read(ser.in_waiting or 1)
            except (serial.SerialException, OSError, TypeError):
                break
            if not data:
                continue
            self.rx_bytes += len(data)
            self.last_rx = time.monotonic()
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except ValueError:
                    self._log("raw", text)                  # boot log / panic text / anything that is not protocol JSON
                    continue
                if not isinstance(msg, dict):
                    continue
                if "ok" in msg:
                    self._log("rx", text)
                    self._resp.put(msg)
                else:
                    if msg.get("evt") not in ("snap",):     # snapshot chunks are far too chatty for the terminal
                        self._log("rx", text)
                    for cb in [self.on_event] + list(self._taps):
                        if cb:
                            try:
                                cb(msg)
                            except Exception:
                                pass
        self._drop(ser)

    def send(self, msg):
        ser = self.ser
        if not ser:
            raise DeviceError("not connected")
        text = compact_json(msg)
        data = (text + "\n").encode("utf-8")
        try:
            with self._wlock:
                ser.write(data)
        except (serial.SerialException, OSError) as e:
            self._drop(ser)
            raise DeviceError(str(e))
        self.tx_bytes += len(data)
        if not (isinstance(msg, dict) and msg.get("cmd") in QUIET_CMDS):
            self._log("tx", text)

    def send_raw(self, text):
        """Dev terminal: send a line exactly as typed (no JSON validation)."""
        ser = self.ser
        if not ser:
            raise DeviceError("not connected")
        data = (text.rstrip("\r\n") + "\n").encode("utf-8")
        try:
            with self._wlock:
                ser.write(data)
        except (serial.SerialException, OSError) as e:
            self._drop(ser)
            raise DeviceError(str(e))
        self.tx_bytes += len(data)
        self._log("tx", text.rstrip("\r\n"))

    def request(self, msg, timeout=2.0):
        """Send one command and return its reply. Every request carries an id the firmware echoes back, so a late
        reply to an earlier (timed-out) request can never be mistaken for the answer to this one."""
        with self._rlock:
            while not self._resp.empty():
                self._resp.get_nowait()
            self._rid = (getattr(self, "_rid", 0) % 1_000_000) + 1
            rid = self._rid
            self.send(dict(msg, id=rid))
            end = time.monotonic() + timeout
            while True:
                try:
                    r = self._resp.get(timeout=max(0.0, end - time.monotonic()))
                except queue.Empty:
                    raise DeviceError("device did not answer (timeout)")
                if r.get("id", rid) == rid:                 # replies from older firmware carry no id: accept those
                    break
        if not r.get("ok"):
            err = r.get("err", "error")
            raise DeviceError(ERR_TEXT.get(err, err))
        return r

    def snapshot(self, timeout=25.0):
        """Download what the pad's frame buffer currently shows -> PIL image (everything except GIF mode)."""
        chunks, done = {}, threading.Event()

        def tap(m):
            if m.get("evt") == "snap":
                chunks[m["o"]] = base64.b64decode(m["d"])
            elif m.get("evt") == "snap_end":
                done.set()
        self._taps.append(tap)
        self.busy = True
        try:
            head = self.request({"cmd": "snapshot"}, timeout=6)
            if not done.wait(timeout):
                raise DeviceError("snapshot timed out")
        finally:
            self.busy = False
            if tap in self._taps:
                self._taps.remove(tap)
        raw = b"".join(chunks[k] for k in sorted(chunks))
        if len(raw) != int(head.get("bytes", 0)):
            raise DeviceError(f"snapshot incomplete ({len(raw)} of {head.get('bytes')} bytes)")
        return rgb565be_image(raw, int(head.get("w", 240)), int(head.get("h", 240)))

    def upload_gif(self, data, progress=None, slot=0):
        self.busy = True
        try:
            r = self.request({"cmd": "gif_begin", "size": len(data), "crc": zlib.crc32(data) & 0xFFFFFFFF, **({"slot": slot} if slot else {})}, timeout=20)
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
              "Productivity & Dev": "#a98bff", "Custom": "#2dd4d4", "Other": "#6b7280",
              "Layers & Pad": "#8b5cf6", "Mouse": "#f97316", "Navigation": "#22d3ee", "Computer": "#84cc16"}
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
    if t == "layer":
        return "switch to the next layer" if v == "next" else "switch to the previous layer" if v == "prev" else f"switch to layer {int(v) + 1}"
    if t == "panic":
        return "panic: release all keys and stop every macro"
    if t == "toggle":
        return "alternate: " + "  /  ".join(describe_spec((x.get("type"), x.get("val"))) for x in v)
    if t == "random":
        return f"random one of {len(v)}: " + ", ".join(describe_spec((x.get("type"), x.get("val"))) for x in v)[:60]
    if t == "mouse":
        if "wheel" in v:
            how = f"scroll {'left' if v['wheel'] > 0 else 'right'} {abs(v['wheel'])}" if v.get("h") else f"scroll {'up' if v['wheel'] > 0 else 'down'} {abs(v['wheel'])}"
            return how + (" with " + "+".join(m.capitalize() for m in v["mods"]) if v.get("mods") else "")
        if "move" in v:
            return f"move the pointer by {v['move'][0]}, {v['move'][1]}"
        return f"mouse {v.get('act', 'click')} ({v.get('btn', 'left')} button)"
    if t == "host":
        if v.get("op") == "clip":
            return "clipboard: " + textops.TRANSFORMS.get(v.get("arg"), (v.get("arg", ""),))[0]
        return {"url": "open ", "app": "start ", "shell": "run ", "file": "open file ", "clipboard": "type the clipboard", "notify": "notify: ",
                "snippet": "type snippet: ", "script": "run script: "}.get(v.get("op"), "") + (v.get("arg", "") if v.get("op") != "clipboard" else "")
    return "nothing"


# ============================================================================ live test: real keystrokes on this PC
class KeySender:
    """Common macro runner; backends implement combo(), text() and media()."""

    host_cb = None                                    # set by the app: runs {"op","arg"} host actions (whitelisted)

    def mouse(self, spec):
        raise ValueError("mouse actions are not available on this system")

    def host(self, spec):
        if self.host_cb:
            self.host_cb(spec)

    def run(self, spec, stop=None):
        t, v = spec
        if t == "combo":
            self.combo(v)
        elif t == "media":
            self.media(v)
        elif t == "text":
            self.text(v)
        elif t == "mouse":
            self.mouse(v)
        elif t == "host":
            self.host(v)
        elif t == "layer":
            pass                                      # layer changes belong to the pad / virtual pad, not to the PC
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
                elif "mouse" in s:
                    self.mouse(s["mouse"])
                elif "host" in s:
                    self.host(s["host"])
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

    MOUSE_FLAGS = {"left": (0x2, 0x4, 0), "right": (0x8, 0x10, 0), "middle": (0x20, 0x40, 0), "back": (0x80, 0x100, 1), "forward": (0x80, 0x100, 2)}

    def _mouse_event(self, flags, data=0, dx=0, dy=0):
        arr = (INPUT * 1)()
        arr[0].type = 0
        arr[0].mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
        if self.u.SendInput(1, arr, ctypes.sizeof(INPUT)) != 1:
            raise OSError("Windows refused the mouse input")

    def mouse(self, spec):
        if "wheel" in spec:
            self._mouse_event(0x800, int(spec["wheel"]) * 120)
        elif "move" in spec:
            self._mouse_event(0x1, 0, int(spec["move"][0]), int(spec["move"][1]))
        else:
            down, up, data = self.MOUSE_FLAGS[spec.get("btn", "left")]
            act = spec.get("act", "click")
            for _ in range(2 if act == "double" else 1):
                if act != "up":
                    self._mouse_event(down, data)
                if act in ("click", "double", "up"):
                    self._mouse_event(up, data)
                time.sleep(0.03)

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
        from pynput import mouse as _mouse
        self.kb, self.Key = Controller(), Key
        self.ms, self.Button = _mouse.Controller(), _mouse.Button
        self.mac = platform.system() == "Darwin"

    def mouse(self, spec):
        if "wheel" in spec:
            self.ms.scroll(0, int(spec["wheel"]))
        elif "move" in spec:
            self.ms.move(int(spec["move"][0]), int(spec["move"][1]))
        else:
            name = {"left": "left", "right": "right", "middle": "middle", "back": "button8", "forward": "button9"}[spec.get("btn", "left")]
            btn = getattr(self.Button, name, None)
            if btn is None:
                raise ValueError(f"the {spec.get('btn')} mouse button is not supported by pynput here")
            act = spec.get("act", "click")
            if act == "double":
                self.ms.click(btn, 2)
            elif act == "down":
                self.ms.press(btn)
            elif act == "up":
                self.ms.release(btn)
            else:
                self.ms.click(btn)

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


class HostMedia:
    """Reads this computer's real master volume / mute / playback state so the pad's media screen mirrors it.
    Linux: pactl (PulseAudio / PipeWire) or amixer, plus playerctl; macOS: osascript; Windows: optional `pip install pycaw`
    (best effort). Anything unavailable is reported as None, which the pad ignores - it then keeps its own estimate."""

    def __init__(self, runner=None, system=None):
        self._run = runner or self._shell
        self.system = system or platform.system()
        self.kind = self._detect()

    @staticmethod
    def _shell(args):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    def _have(self, tool):
        return shutil.which(tool) is not None

    def _detect(self):
        if self.system == "Linux":
            return "pactl" if self._have("pactl") else "amixer" if self._have("amixer") else ""
        if self.system == "Darwin":
            return "osascript" if self._have("osascript") else ""
        if self.system == "Windows":
            try:
                import importlib.util
                return "pycaw" if importlib.util.find_spec("pycaw") else ""
            except Exception:                                         # noqa: BLE001
                return ""
        return ""

    @property
    def available(self):
        return bool(self.kind)

    def state(self):
        """-> {"vol": 0..100|None, "muted": bool|None, "playing": bool|None}"""
        out = {"vol": None, "muted": None, "playing": None}
        try:
            if self.kind == "pactl":
                self._pactl(out)
            elif self.kind == "amixer":
                self._amixer(out)
            elif self.kind == "osascript":
                self._osascript(out)
            elif self.kind == "pycaw":
                self._pycaw(out)
            if self.system == "Linux" and out["playing"] is None and self._have("playerctl"):
                st = self._run(["playerctl", "status"])
                if st is not None:
                    out["playing"] = st.strip().lower() == "playing"
        except Exception:                                             # noqa: BLE001 - a flaky mixer must never kill the sync loop
            pass
        return out

    def _pactl(self, out):
        v = self._run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
        m = re.search(r"(\d+)%", v or "")
        if m:
            out["vol"] = max(0, min(100, int(m.group(1))))
        mu = self._run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
        if mu:
            out["muted"] = "yes" in mu.lower()
        sinks = self._run(["pactl", "list", "short", "sinks"])
        if sinks:                                                     # a RUNNING sink means something is making sound right now
            out["playing"] = any(ln.split("\t")[-1].strip() == "RUNNING" for ln in sinks.splitlines() if ln.strip())

    def _amixer(self, out):
        t = self._run(["amixer", "get", "Master"]) or ""
        m = re.search(r"\[(\d+)%\]", t)
        if m:
            out["vol"] = max(0, min(100, int(m.group(1))))
        m = re.search(r"\[(on|off)\]", t)
        if m:
            out["muted"] = m.group(1) == "off"

    def _osascript(self, out):
        v = self._run(["osascript", "-e", "output volume of (get volume settings)"])
        if v and v.strip().isdigit():
            out["vol"] = max(0, min(100, int(v.strip())))
        mu = self._run(["osascript", "-e", "output muted of (get volume settings)"])
        if mu:
            out["muted"] = mu.strip().lower() == "true"

    def _pycaw(self, out):
        from pycaw.pycaw import AudioUtilities
        ep = None
        try:
            ep = AudioUtilities.GetSpeakers().EndpointVolume          # pycaw >= 2023.x
        except AttributeError:
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import IAudioEndpointVolume
            from ctypes import POINTER, cast
            iface = AudioUtilities.GetSpeakers().Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            ep = cast(iface, POINTER(IAudioEndpointVolume))
        out["vol"] = max(0, min(100, int(round(ep.GetMasterVolumeLevelScalar() * 100))))
        out["muted"] = bool(ep.GetMute())
        try:
            out["playing"] = any(getattr(ss, "State", 0) == 1 for ss in AudioUtilities.GetAllSessions())
        except Exception:                                             # noqa: BLE001
            pass


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


# fr_CH and ja_JP are deliberately absent: they only exist in arduino-esp32 3.3.8+/3.3.9+, which most
# installed 3.x cores predate, so the firmware does not compile them in (see DeskCompanion.ino).
LANGID_LAYOUT = {0x0407: "de_DE", 0x0807: "de_DE", 0x0C07: "de_DE", 0x1007: "de_DE", 0x1407: "de_DE",
                 0x040C: "fr_FR", 0x080C: "fr_FR", 0x0C0C: "fr_FR", 0x0410: "it_IT",
                 0x0810: "it_IT", 0x040A: "es_ES", 0x0C0A: "es_ES", 0x0816: "pt_PT", 0x0416: "pt_BR",
                 0x041D: "sv_SE", 0x0406: "da_DK", 0x040E: "hu_HU"}
LAYOUTS = ["en_US", "de_DE", "fr_FR", "es_ES", "it_IT", "pt_PT", "pt_BR", "sv_SE", "da_DK", "hu_HU"]


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
M_CLOCK, M_POMO, M_MEDIA, M_TELEM, M_GIF, M_INFO = 1, 2, 3, 4, 5, 6
NUM_MODES = 12                                   # 1-6 classic screens, 7-12 optional ones that run on the pad (firmware 1.4)
MODE_NAMES = ["", "CLOCK", "FOCUS", "MEDIA", "SYSTEM", "GIF", "INFO", "STOPWATCH", "BREATHE", "DICE", "REACTION", "SNAKE", "HABITS"]
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
        self.mode_mask = 0x3F                                    # screens in the dial menu / long-press cycle (the pad's "mode_mask" setting)
        self.pomo_minutes, self.pomo_state = 25, PS_IDLE
        self.pomo_remain, self.pomo_last, self.pomo_done_at = 25 * 60.0, 0.0, 0.0
        self.gif_frames = self.gif_durs = self._demo_cache = None
        self.upload_frac = None
        self.overlay = None
        self.layer, self.menu_layer, self.flash_at = 0, 0, 0.0
        self.cards, self.badges, self.card_idx, self.card_at, self.card_rot, self.info_at = [], [], 0, 0.0, 6, 0.0
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
        elif t == "layer":
            self.set_layer((self.layer + 1) % LAYERS if v == "next" else (self.layer - 1) % LAYERS if v == "prev" else int(v))
        elif t == "macro":
            for s in v:
                if "media" in s:
                    self.apply_media(s["media"])
                elif "layer" in s:
                    x = s["layer"]
                    self.set_layer((self.layer + 1) % LAYERS if x == "next" else (self.layer - 1) % LAYERS if x == "prev" else int(x))

    def set_layer(self, n, notify=True):
        if 0 <= n < LAYERS:
            changed = n != self.layer
            self.layer, self.dirty = n, True
            self.toast(f"Layer {n + 1}", 1.4)
            if changed and notify:
                self.on_change("layer")

    def set_info(self, cards, badges, rot=6):
        self.cards, self.badges, self.card_rot = list(cards)[:4], list(badges)[:4], rot
        self.card_idx, self.card_at, self.info_at, self.dirty = 0, time.monotonic(), time.monotonic(), True

    def set_mode(self, m, notify=True):
        if 1 <= m <= NUM_MODES:
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
            self.menu_sel = (self.menu_sel + d * n) % 5
        elif self.menu_edit == 1:
            self.set_brightness(self.brightness + steps * 8)
        elif self.menu_edit == 2:
            for _ in range(n):
                self.exec_media("VOL_UP" if d > 0 else "VOL_DOWN")
        elif self.menu_edit == 3:
            for _ in range(n):
                self.menu_mode = self.next_mode(self.menu_mode, d)
        elif self.menu_edit == 4:
            self.menu_layer = (self.menu_layer + steps) % LAYERS
        self.dirty = True

    def _menu_click(self):
        self._touch()
        if not self.menu_edit:
            if self.menu_sel == 4:
                self._menu_close()
                return
            self.menu_edit = self.menu_sel + 1
            if self.menu_edit == 3:
                self.menu_mode = self.mode
            if self.menu_edit == 4:
                self.menu_layer = self.layer
        else:
            if self.menu_edit == 3 and self.menu_mode != self.mode:
                self.set_mode(self.menu_mode)
            if self.menu_edit == 4 and self.menu_layer != self.layer:
                self.set_layer(self.menu_layer)
            self.menu_edit = 0
        self.dirty = True

    # ---- inputs (identical behaviour to the firmware)
    def key(self, i):                           # i = 0..4
        self.flash_at, self.dirty = time.monotonic(), True
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
            self.set_mode(self.next_mode(self.mode, 1))

    def next_mode(self, m, d=1):
        for i in range(1, NUM_MODES + 1):
            c = (m - 1 + d * i) % NUM_MODES + 1
            if (self.mode_mask >> (c - 1)) & 1:
                return c
        return m

    # ---- timing
    def tick(self):
        """Advance timers; returns True when the screen needs to be redrawn."""
        now = time.monotonic()
        self._pomo_tick(now)
        if self.menu_open and now - self.menu_touched > 4.0:
            self._menu_close()
        if self.overlay and now >= self.overlay[1]:
            self.overlay, self.dirty = None, True
        if self.mode == M_INFO and len(self.cards) > 1 and self.card_rot and now - self.card_at >= self.card_rot:
            self.card_idx, self.card_at, self.dirty = (self.card_idx + 1) % len(self.cards), now, True
        if self.dirty:
            return True
        if now - self.flash_at < 0.28:
            iv = 0.03
        elif self.upload_frac is not None or self.menu_open or self.overlay:
            iv = 0.1
        elif self.mode == M_CLOCK:
            iv = 0.2
        elif self.mode == M_POMO:
            iv = 0.25 if self.pomo_state in (PS_RUN, PS_DONE) else 5.0
        elif self.mode == M_MEDIA:
            iv = 0.09 if self.playing else 5.0
        elif self.mode in (M_TELEM, M_INFO):
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

    def _scene_info(self, g, now):
        fresh = bool(self.cards) and self.info_at and now - self.info_at < 300
        g.band(120, 120, 116, 108, 0, 360, C_DIM)
        if not fresh:
            g.text("INFO", 120, 84, 15, C_GRAY)
            g.text("no data", 120, 118, 26, C_TXT, bold=True)
            g.text("open the companion app", 120, 152, 13, C_GRAY)
            g.text("(Info tab)", 120, 170, 13, C_GRAY)
        else:
            c = self.cards[self.card_idx % len(self.cards)]
            col = {"m": C_ACC2, "w": C_ACC, "e": C_WARN}.get(c.get("k", "c"), C_OK)
            if len(self.cards) > 1 and self.card_rot:
                g.band(120, 120, 116, 108, 0, 360 * max(0.0, min(1.0, (now - self.card_at) / self.card_rot)), col)
            else:
                g.band(120, 120, 116, 108, 0, 360, col)
            fit = lambda t, n: t if len(t) <= n else t[:n - 2] + ".."     # noqa: E731 - same truncation as the firmware
            g.text(fit(c.get("label", ""), 18), 120, 52, 13, C_GRAY)
            g.text(fit(c.get("t", ""), 11), 120, 98, 26, col, bold=True)
            g.text(fit(c.get("a", ""), 20), 120, 136, 13, C_TXT)
            g.text(fit(c.get("b", ""), 20), 120, 156, 13, C_GRAY)
            n = len(self.cards)
            for i in range(n if n > 1 else 0):
                x = 120 + (i - (n - 1) / 2.0) * 12
                g.circle(x, 182, 3 if i == self.card_idx % n else 2, fill=col if i == self.card_idx % n else C_DIM2)
        for i, b in enumerate(self.badges[:4]):
            if not b.get("n"):
                continue
            x = 120 + (i - (len(self.badges[:4]) - 1) / 2.0) * 46
            g.d.rounded_rectangle([(x - 20) * SS, 196 * SS, (x + 20) * SS, 218 * SS], radius=8 * SS, fill=C_ACC2)
            g.text(str(min(99, int(b["n"]))), x, 207, 13, C_BG, bold=True)
            g.text(str(b.get("name", ""))[:6], x, 228, 11, C_GRAY)

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
        labels, full = ["BRIGHT", "VOL", "MODE", "LAYER", "EXIT"], ["BRIGHTNESS", "VOLUME", "DISPLAY MODE", "KEY LAYER", "EXIT"]
        for i in range(5):
            c, sel = i * 72.0, i == self.menu_sel
            g.band(120, 120, 118, 72, c - 34, c + 34, (C_WARN if self.menu_edit else C_ACC) if sel else C_DIM2)
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
            g.text(f"{m} / {NUM_MODES}", 120, 112, 26, vc, bold=True)
            g.text(MODE_NAMES[m], 120, 136, 15, C_ACC)
        elif self.menu_sel == 3:
            g.text(f"{(self.menu_layer if self.menu_edit else self.layer) + 1} / {LAYERS}", 120, 118, 26, vc, bold=True)
        else:
            g.text("CLOSE", 120, 118, 26, vc, bold=True)
        g.text("CLICK = OK" if self.menu_edit else "CLICK = ENTER", 120, 158, 11, C_GRAY)

    def _draw_status(self, g, now):
        """Layer badge + key-press ring, drawn over every screen except GIF mode (same as the firmware)."""
        col = (C_ACC, C_ACC2, C_WARN)[self.layer % 3]
        if self.layer > 0:
            g.d.rounded_rectangle([100 * SS, 214 * SS, 140 * SS, 234 * SS], radius=8 * SS, fill=col)
            g.text(f"L{self.layer + 1}", 120, 224, 13, C_BG, bold=True)
        age = now - self.flash_at
        if self.flash_at and age < 0.26:
            th = 10 - int(age * 1000 / 28)
            if th > 0:
                g.band(120, 120, 119, 119 - th, 0, 360, col)

    def _draw_overlay(self, g, text):
        f = _font(int(13 * SS), True)
        w = min(150.0, g.d.textlength(text, font=f) / SS + 24)
        g.d.rounded_rectangle([(120 - w / 2) * SS, 196 * SS, (120 + w / 2) * SS, 222 * SS], radius=12 * SS,
                              fill=(18, 22, 30), outline=C_ACC, width=SS)
        g.text(text, 120, 209, 13, C_TXT, bold=True)

    def _scene_pad_only(self, g):
        """Screens 7-12 are drawn by the pad itself (stopwatch, breathing, dice, reaction test, snake, habits): the twin shows a card."""
        g.fill(C_BG)
        g.text(MODE_NAMES[self.mode], 120, 96, 22, C_ACC, bold=True)
        g.text("runs on the pad", 120, 130, 14, C_GRAY)
        g.text("(enable it in Device -> Screens)", 120, 150, 11, C_DIM2)

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
            elif self.mode == M_INFO:
                g.fill(C_BG)
                self._scene_info(g, now)
            elif self.mode > M_INFO:
                self._scene_pad_only(g)
            else:
                self._scene_gif(g, now)
            if self.menu_open:
                self._scene_menu(g)
            elif self.mode != M_GIF:
                self._draw_status(g, now)
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
        accent = {"ok": "#34d399", "warn": "#fbbf24", "off": "#8e8e9b"}.get(kind, "#34d399")
        self.win = w = tk.Toplevel(app)
        w.overrideredirect(True)
        try:
            w.attributes("-topmost", True)
        except tk.TclError:
            pass
        outer = tk.Frame(w, bg=accent)
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg="#121215")
        inner.pack(fill="both", expand=True, padx=(5, 1), pady=1)
        tk.Label(inner, text="\u25CF", fg=accent, bg="#121215", font=("TkDefaultFont", 20)).grid(row=0, column=0, rowspan=2, padx=(12, 6), pady=10)
        tk.Label(inner, text=title, fg="#ffffff", bg="#121215", font=("TkDefaultFont", 12, "bold"), anchor="w").grid(
            row=0, column=1, sticky="w", padx=(0, 16), pady=(10, 0))
        tk.Label(inner, text=text, fg="#aab2c0", bg="#121215", font=("TkDefaultFont", 9), anchor="w", justify="left",
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
# ============================================================================ Dev tab: bring-up / diagnostics tools
def info_lines(info):
    """Human readable lines for the firmware's `info` reply."""
    order = ["fw", "build", "chip", "rev", "cores", "cpu_mhz", "flash", "core", "usb_mode", "cdc_boot", "hid", "tft", "sim",
             "reset", "crashes", "safe", "up_ms", "heap", "heap_min", "heap_blk", "psram", "temp", "ok_prefs", "ok_fs",
             "ok_sprite", "ok_disp", "fs_free", "fs_total", "led_pin", "led_mode", "mode", "bright", "events", "gpio_touched", "boot"]
    names = {"usb_mode": "usb_mode (0 = TinyUSB, 1 = hardware CDC)", "crashes": "crash-loop counter", "heap_blk": "largest free block",
             "ok_fs": "filesystem ok", "ok_disp": "display ok", "ok_prefs": "settings (NVS) ok", "ok_sprite": "frame buffer ok"}
    lines = []
    for k in order:
        if k in info:
            lines.append(f"{names.get(k, k):42s} {info[k]}")
    for k in info:
        if k not in order and k not in ("ok", "evt", "id"):
            lines.append(f"{k:42s} {info[k]}")
    return lines


class DevTab:
    HELP = ("Bring-up & diagnostics. 1) Flash CoreBringup/CoreBringup.ino (no libraries) and check LED + ping here, "
            "2) then flash DeskCompanion/DeskCompanion.ino. Everything below talks to the real pad - or to the simulator.")

    def __init__(self, app, tab):
        self.app = app
        self.lines = []                                   # terminal history (also used for the diagnostic report)
        self._led_job = None
        self._snap_img = None
        self.key_labels, self.last_info = [], {}
        tab.grid_columnconfigure(0, weight=3)
        tab.grid_columnconfigure(1, weight=2)
        tab.grid_rowconfigure(1, weight=1)
        bold = ctk.CTkFont(size=14, weight="bold")
        self.bold = bold

        top = ctk.CTkFrame(tab)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=(6, 4))
        self.state_lbl = ctk.CTkLabel(top, text="Not connected", text_color=WARN, anchor="w")
        self.state_lbl.pack(side="left", padx=12, pady=8)
        for text, cmd in (("Ping x5", self.ping), ("Device info", self.show_info), ("Full self-test", self.full_selftest),
                          ("Copy diagnostic report", self.report)):
            ctk.CTkButton(top, text=text, width=130, command=cmd).pack(side="right", padx=4, pady=8)

        left = ctk.CTkScrollableFrame(tab)
        left.grid(row=1, column=0, sticky="nsew", padx=(6, 3), pady=4)
        right = ctk.CTkFrame(tab)
        right.grid(row=1, column=1, sticky="nsew", padx=(3, 6), pady=4)

        ctk.CTkLabel(left, text=self.HELP, text_color=MUTED, wraplength=560, justify="left").pack(anchor="w", padx=10, pady=(4, 6))
        self._build_flash(left)
        self._build_ports(left)
        self._build_led(left)
        self._build_inputs(left)
        self._build_display(left)
        self._build_hid(left)
        self._build_gpio(left)
        self._build_system(left)
        self._build_terminal(right)

    # ---------------------------------------------------------------- helpers
    def card(self, parent, title):
        f = ctk.CTkFrame(parent)
        f.pack(fill="x", padx=6, pady=5)
        ctk.CTkLabel(f, text=title, font=self.bold).pack(anchor="w", padx=10, pady=(8, 2))
        return f

    def row(self, parent):
        r = ctk.CTkFrame(parent, fg_color="transparent")
        r.pack(fill="x", padx=8, pady=3)
        return r

    def req(self, msg, ok=None, label="Command failed", timeout=4.0):
        if not self.app.dev.connected:
            self.app.set_status("Not connected - plug the pad in or use 'Simulate pad' on the Dashboard tab", error=True)
            return
        self.app.bg(lambda: self.app.dev.request(msg, timeout=timeout), ok, label)

    def refresh_state(self):
        d = self.app.dev
        if d.connected:
            sim = d.port == SIM_PORT
            fw = d.info.get("fw", "?")
            bits = [f"{'SIMULATED pad' if sim else d.port}", f"fw {fw}"]
            for k, nm in (("hid", "HID"), ("disp", "display")):
                if k in d.info:
                    bits.append(f"{nm} {'ok' if d.info[k] else 'OFF'}")
            if "fs" in d.info:
                st = d.info.get("fs_state", "ready" if d.info["fs"] else "failed")
                bits.append("files ok" if d.info["fs"] else f"files {st}...")
            if d.info.get("safe"):
                bits.append("SAFE MODE (crash loop!)")
            self.state_lbl.configure(text="Connected: " + "  |  ".join(bits), text_color=OK if not d.info.get("safe") else ERR)
        else:
            self.state_lbl.configure(text="Not connected", text_color=WARN)

    # ---------------------------------------------------------------- terminal
    def _build_terminal(self, parent):
        ctk.CTkLabel(parent, text="Terminal (everything on the wire)", font=self.bold).pack(anchor="w", padx=10, pady=(8, 2))
        self.term = ctk.CTkTextbox(parent, font=ctk.CTkFont(family="Courier", size=11), wrap="none")
        self.term.pack(fill="both", expand=True, padx=8, pady=4)
        tb = self.term._textbox
        tb.tag_config("tx", foreground="#3b82f6")
        tb.tag_config("rx", foreground="#10b981")
        tb.tag_config("raw", foreground="#f59e0b")
        tb.tag_config("sys", foreground="#8e8e9b")
        tb.tag_config("err", foreground="#ef4444")
        self.term.configure(state="disabled")
        r = self.row(parent)
        self.raw_var = tk.StringVar()
        e = ctk.CTkEntry(r, textvariable=self.raw_var, placeholder_text='raw JSON line, e.g. {"cmd":"ping"}')
        e.pack(side="left", fill="x", expand=True, padx=(0, 4))
        e.bind("<Return>", lambda _e: self.send_raw())
        ctk.CTkButton(r, text="Send", width=60, command=self.send_raw).pack(side="left")
        r2 = self.row(parent)
        ctk.CTkButton(r2, text="Clear", width=70, fg_color="#555", command=self.clear).pack(side="left", padx=(0, 4))
        ctk.CTkButton(r2, text="Copy", width=70, fg_color="#555", command=self.copy_log).pack(side="left", padx=(0, 4))
        self.rxinfo = ctk.CTkLabel(r2, text="rx 0 B / tx 0 B", text_color=MUTED)
        self.rxinfo.pack(side="right", padx=6)

    def log(self, direction, text):
        """Thread-safe: called from the serial reader / writer threads."""
        self.app.post(lambda: self._log_ui(direction, text))

    def _log_ui(self, direction, text):
        stamp = time.strftime("%H:%M:%S")
        prefix = {"tx": "->", "rx": "<-", "raw": "!!", "sys": "..", "err": "XX"}.get(direction, "  ")
        line = f"{stamp} {prefix} {text}"
        self.lines.append(line)
        del self.lines[:-600]
        self.term.configure(state="normal")
        self.term._textbox.insert("end", line[:600] + "\n", direction)
        if int(self.term._textbox.index("end-1c").split(".")[0]) > 600:
            self.term._textbox.delete("1.0", "100.0")
        self.term._textbox.see("end")
        self.term.configure(state="disabled")
        d = self.app.dev
        self.rxinfo.configure(text=f"rx {d.rx_bytes} B / tx {d.tx_bytes} B")

    def sys(self, text, err=False):
        self._log_ui("err" if err else "sys", text)

    def clear(self):
        self.lines.clear()
        self.term.configure(state="normal")
        self.term.delete("1.0", "end")
        self.term.configure(state="disabled")

    def copy_log(self):
        self.app.clipboard_clear()
        self.app.clipboard_append("\n".join(self.lines))
        self.app.set_status("Terminal copied to the clipboard")

    def send_raw(self):
        t = self.raw_var.get().strip()
        if not t:
            return
        try:
            self.app.dev.send_raw(t)
            self.raw_var.set("")
        except DeviceError as e:
            self.sys(f"send failed: {e}", err=True)

    # ---------------------------------------------------------------- flashing (prebuilt images via esptool)
    def _build_flash(self, parent):
        c = self.card(parent, "0. Flash firmware (no Arduino IDE needed)")
        r = self.row(c)
        self.flash_img = tk.StringVar(value="core")
        ctk.CTkOptionMenu(r, values=["core", "full"], variable=self.flash_img, width=90).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(r, text="core = CoreBringup (step 1)   full = DeskCompanion (step 2)", text_color=MUTED).pack(side="left")
        r = self.row(c)
        self.flash_btn = ctk.CTkButton(r, text="Flash to the board", width=150, fg_color="#7a4a1f", command=self.flash)
        self.flash_btn.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(c, text="Hold BOOT while plugging in the USB cable (download mode), then press the button. If the pad already runs "
                     "DeskCompanion it is put into download mode automatically. Needs:  pip install esptool", text_color=MUTED,
                     wraplength=540, justify="left").pack(anchor="w", padx=10, pady=(0, 6))

    def flash(self):
        self.app.flash_firmware(self.flash_img.get(), self.flash_btn)

    # ---------------------------------------------------------------- ports + probing
    def _build_ports(self, parent):
        c = self.card(parent, "1. Serial ports (is the pad visible to this PC?)")
        self.port_tree = ttk.Treeview(c, style="Pad.Treeview", columns=("vp", "desc", "res"), show="headings", height=4)
        for col, txt, w in (("vp", "VID:PID", 80), ("desc", "Port / description", 270), ("res", "Result", 200)):
            self.port_tree.heading(col, text=txt)
            self.port_tree.column(col, width=w, anchor="w")
        self.port_tree.pack(fill="x", padx=8, pady=4)
        self.port_hint = ctk.CTkLabel(c, text="", text_color=MUTED, wraplength=540, justify="left")
        self.port_hint.pack(anchor="w", padx=10)
        r = self.row(c)
        ctk.CTkButton(r, text="Refresh", width=90, command=self.refresh_ports).pack(side="left", padx=(0, 4))
        ctk.CTkButton(r, text="Probe all ports (send hello)", width=200, command=self.probe_ports).pack(side="left")
        self.port_tree.bind("<<TreeviewSelect>>", self._port_selected)
        self._port_rows = {}
        self.refresh_ports()

    def refresh_ports(self):
        self._ports = list_serial_ports()
        self.port_tree.delete(*self.port_tree.get_children())
        for p in self._ports:
            res = "connected (this app)" if (self.app.dev.connected and self.app.dev.port == p["device"]) else ("ESP32 device" if p["esp"] else "")
            self.port_tree.insert("", "end", iid=p["device"], values=(fmt_vidpid(p), f"{p['device']} - {p['desc']}", res))
        if not self._ports:
            self.port_hint.configure(text="No serial ports at all. Check the USB cable (many cables are charge-only) and the port on the board "
                                          "(use the USB-C of the ESP32-S3-Zero).")
        elif not any(p["esp"] for p in self._ports):
            self.port_hint.configure(text="Serial ports exist but none looks like an Espressif device (VID 303A). Is the right cable/port used? "
                                          "Unplug and re-plug the pad while watching this list.")
        else:
            self.port_hint.configure(text="Select a row for details.")

    def _port_selected(self, _e):
        sel = self.port_tree.selection()
        for p in getattr(self, "_ports", []):
            if sel and p["device"] == sel[0]:
                self.port_hint.configure(text=p["hint"] or ("Not an Espressif device." if not p["esp"] else ""))

    def probe_ports(self):
        ports = [p for p in list_serial_ports() if not (self.app.dev.connected and self.app.dev.port == p["device"])]
        self.sys(f"probing {len(ports)} port(s) with hello ...")

        def work():
            out = {}
            for p in ports:
                dev = p["device"]
                try:
                    s = Device._open(dev)
                except Exception as e:                       # noqa: BLE001
                    out[dev] = f"cannot open: {str(e)[:60]}"
                    continue
                try:
                    got, end = b"", time.monotonic() + 3.0
                    s.write(b'{"cmd":"hello"}\n')
                    while time.monotonic() < end and b"desk-companion" not in got:
                        got += s.read(256)
                        if b"\n" in got and b"desk-companion" not in got and time.monotonic() > end - 1.5:
                            s.write(b'{"cmd":"hello"}\n')
                    if b"desk-companion" in got:
                        try:
                            j = json.loads(got.decode("utf-8", "replace").strip().splitlines()[-1])
                            out[dev] = f"DeskCompanion fw {j.get('fw', '?')}"
                        except ValueError:
                            out[dev] = "DeskCompanion (reply unparsable)"
                    elif got:
                        out[dev] = f"talks, but not DeskCompanion: {got[:40]!r}"
                    else:
                        out[dev] = "opened, no reply in 3 s"
                finally:
                    try:
                        s.close()
                    except Exception:                        # noqa: BLE001
                        pass
            return out

        def done(out):
            self.refresh_ports()
            for dev, res in out.items():
                if self.port_tree.exists(dev):
                    v = list(self.port_tree.item(dev, "values"))
                    v[2] = res
                    self.port_tree.item(dev, values=v)
                self.sys(f"probe {dev}: {res}")
        self.app.bg(work, done, "Probe failed")

    # ---------------------------------------------------------------- LED
    def _build_led(self, parent):
        c = self.card(parent, "2. Onboard RGB LED (WS2812, GPIO21 on the Waveshare ESP32-S3-Zero)")
        self.led_vars = [tk.IntVar(value=0) for _ in range(3)]
        for i, (name, col) in enumerate((("R", "#ff6b6b"), ("G", "#4cd97b"), ("B", "#6ea8fe"))):
            r = self.row(c)
            ctk.CTkLabel(r, text=name, width=18, text_color=col).pack(side="left")
            ctk.CTkSlider(r, from_=0, to=255, number_of_steps=51, variable=self.led_vars[i], command=lambda _v: self._led_slider()).pack(side="left", fill="x", expand=True, padx=6)
        r = self.row(c)
        self.led_swatch = tk.Canvas(r, width=36, height=22, highlightthickness=1, highlightbackground="#555", bg="#000000")
        self.led_swatch.pack(side="left", padx=(0, 8))
        for text, rgbv in (("Red", (60, 0, 0)), ("Green", (0, 60, 0)), ("Blue", (0, 0, 60)), ("White", (50, 50, 50)), ("Off", (0, 0, 0))):
            ctk.CTkButton(r, text=text, width=58, command=lambda v=rgbv: self.led_color(*v)).pack(side="left", padx=2)
        r = self.row(c)
        for text, mode in (("Blink", "blink"), ("Rainbow", "rainbow"), ("Auto (status)", "auto"), ("LED off", "off")):
            ctk.CTkButton(r, text=text, width=88, fg_color="#555", command=lambda m=mode: self._led_mode(m)).pack(side="left", padx=2)

    def led_color(self, r, g, b):
        for v, x in zip(self.led_vars, (r, g, b)):
            v.set(x)
        self._led_slider(immediate=True)

    def _led_slider(self, immediate=False):
        r, g, b = (v.get() for v in self.led_vars)
        self.led_swatch.configure(bg=f"#{min(255, r * 4):02x}{min(255, g * 4):02x}{min(255, b * 4):02x}")
        if self._led_job:
            self.app.after_cancel(self._led_job)
        self._led_job = self.app.after(0 if immediate else 120, lambda: self.req({"cmd": "led", "r": r, "g": g, "b": b}, None, "LED command failed"))

    def _led_mode(self, mode):
        self.req({"cmd": "led", "mode": mode}, None, "LED command failed")

    # ---------------------------------------------------------------- inputs
    def _build_inputs(self, parent):
        c = self.card(parent, "3. Keys + encoder (press them on the pad - indicators follow live)")
        r = self.row(c)
        self.key_labels = []
        for i in range(5):
            l = ctk.CTkLabel(r, text=f"K{i + 1}", width=48, height=34, fg_color=CARD2, corner_radius=6)
            l.pack(side="left", padx=3)
            self.key_labels.append(l)
        self.enc_lbl = ctk.CTkLabel(r, text="ENC  pos 0", width=110, height=34, fg_color=CARD2, corner_radius=6)
        self.enc_lbl.pack(side="left", padx=(10, 3))
        r = self.row(c)
        self.events_var = tk.BooleanVar(value=False)
        ctk.CTkSwitch(r, text="Live events", variable=self.events_var, command=self.toggle_events).pack(side="left", padx=4)
        ctk.CTkButton(r, text="Read now", width=90, command=self.read_inputs).pack(side="left", padx=4)
        ctk.CTkLabel(c, text="Virtual presses run the key's real action (copy / paste / media ...) on THIS computer via the pad:",
                     text_color=MUTED, wraplength=540, justify="left").pack(anchor="w", padx=10)
        r = self.row(c)
        for i in range(5):
            ctk.CTkButton(r, text=f"Press K{i + 1}", width=70, fg_color="#555",
                          command=lambda k=i + 1: self.req({"cmd": "input", "k": k}, None, "Input failed")).pack(side="left", padx=2)
        r = self.row(c)
        for text, msg in (("Dial left", {"turn": -1}), ("Dial right", {"turn": 1}), ("Dial click", {"click": True}), ("Dial hold", {"hold": True})):
            ctk.CTkButton(r, text=text, width=88, fg_color="#555", command=lambda m=msg: self.req({"cmd": "input", **m}, None, "Input failed")).pack(side="left", padx=2)

    def toggle_events(self):
        self.req({"cmd": "events", "val": bool(self.events_var.get())}, None, "Events failed")

    def read_inputs(self):
        def ok(r):
            self._show_keys(r.get("keys", []), r.get("enc_sw", 0), r.get("enc_pos", 0))
            self.sys(f"inputs: keys {r.get('keys')} enc_sw {r.get('enc_sw')} A={r.get('enc_a')} B={r.get('enc_b')} pos {r.get('enc_pos')}")
        self.req({"cmd": "inputs"}, ok, "Read failed")

    def _show_keys(self, keys, enc_sw, pos):
        for l, v in zip(self.key_labels, keys):
            l.configure(fg_color=ui.OK_FILL if v else CARD2)
        self.enc_lbl.configure(text=f"ENC  pos {pos}", fg_color=ui.OK_FILL if enc_sw else CARD2)

    def on_event(self, m):
        """Device -> app messages without a reply slot (key / encoder events). Called from the reader thread."""
        evt = m.get("evt")
        if evt == "key" and 1 <= m.get("k", 0) <= 5:
            k, v = m["k"], m.get("v", 0)
            self.app.post(lambda: self.key_labels[k - 1].configure(fg_color=ui.OK_FILL if v else CARD2))
        elif evt == "enc":
            pos = m.get("pos", 0)
            self.app.post(lambda: self.enc_lbl.configure(text=f"ENC  pos {pos}  ({'+' if m.get('d', 0) > 0 else '-'})"))
        elif evt == "encsw":
            v = m.get("v", 0)
            self.app.post(lambda: self.enc_lbl.configure(fg_color=ui.OK_FILL if v else CARD2))

    # ---------------------------------------------------------------- display
    def _build_display(self, parent):
        c = self.card(parent, "4. Display (test patterns + what the frame buffer shows)")
        r = self.row(c)
        for text, msg in (("Red", {"test": "fill", "r": 255}), ("Green", {"test": "fill", "g": 255}), ("Blue", {"test": "fill", "b": 255}),
                          ("White", {"test": "fill", "r": 255, "g": 255, "b": 255}), ("Black", {"test": "fill"})):
            ctk.CTkButton(r, text=text, width=64, command=lambda m=msg: self.req({"cmd": "display", **m}, None, "Display test failed")).pack(side="left", padx=2)
        r = self.row(c)
        for text, t in (("Colour bars", "bars"), ("Grid", "grid"), ("Text", "text"), ("Back to normal", "off")):
            ctk.CTkButton(r, text=text, width=100, fg_color="#555", command=lambda t=t: self.req({"cmd": "display", "test": t}, None, "Display test failed")).pack(side="left", padx=2)
        r = self.row(c)
        ctk.CTkButton(r, text="Screenshot of the pad's screen", width=220, command=self.snapshot).pack(side="left", padx=2)
        self.snap_canvas = tk.Canvas(c, width=240, height=240, bg="#111", highlightthickness=1, highlightbackground="#333")
        self.snap_canvas.pack(pady=6)
        ctk.CTkLabel(c, text="Test patterns stay for 8 s. A screenshot shows the firmware's frame buffer (not GIF mode).", text_color=MUTED).pack(anchor="w", padx=10, pady=(0, 6))

    def snapshot(self):
        if not self.app.dev.connected:
            return self.app.set_status("Not connected", error=True)
        self.sys("downloading screen snapshot ...")
        self.app.bg(lambda: self.app.dev.snapshot(), self._show_snap, "Snapshot failed")

    def _show_snap(self, img):
        self._snap_img = ImageTk.PhotoImage(img)
        self.snap_canvas.delete("all")
        self.snap_canvas.create_image(0, 0, anchor="nw", image=self._snap_img)
        self.sys("snapshot received")

    # ---------------------------------------------------------------- HID
    def _build_hid(self, parent):
        c = self.card(parent, "5. Keyboard / media test (the pad types into whatever window has the focus)")
        r = self.row(c)
        self.hid_text = tk.StringVar(value="Hello from DeskCompanion!")
        ctk.CTkEntry(r, textvariable=self.hid_text).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(r, text="Type in 3 s", width=100, command=self.hid_type).pack(side="left")
        r = self.row(c)
        for text, name in (("Mute", "MUTE"), ("Vol +", "VOL_UP"), ("Vol -", "VOL_DOWN"), ("Play/Pause", "PLAY_PAUSE"), ("Next", "NEXT")):
            ctk.CTkButton(r, text=text, width=70, fg_color="#555", command=lambda n=name: self.req({"cmd": "run", "type": "media", "val": n}, None, "HID test failed")).pack(side="left", padx=2)
        r = self.row(c)
        ctk.CTkButton(r, text="Ctrl+A in 3 s", width=110, fg_color="#555", command=lambda: self.hid_later({"cmd": "run", "type": "combo", "val": ["CTRL", "a"]})).pack(side="left", padx=2)
        ctk.CTkButton(r, text="Win key in 3 s", width=110, fg_color="#555", command=lambda: self.hid_later({"cmd": "run", "type": "combo", "val": ["GUI"]})).pack(side="left", padx=2)

    def hid_type(self):
        self.hid_later({"cmd": "run", "type": "text", "val": self.hid_text.get()})

    def hid_later(self, msg, n=3):
        if not self.app.dev.connected:
            return self.app.set_status("Not connected", error=True)
        if n > 0:
            self.app.set_status(f"Click into a text editor now ... sending in {n}")
            self.app.after(1000, lambda: self.hid_later(msg, n - 1))
        else:
            self.req(msg, lambda r: self.app.set_status("HID test sent"), "HID test failed (is the pad in USB-OTG / TinyUSB mode?)")

    # ---------------------------------------------------------------- GPIO
    def _build_gpio(self, parent):
        c = self.card(parent, "6. GPIO tester (wiring check)")
        r = self.row(c)
        self.gpio_pin = tk.StringVar(value="1")
        ctk.CTkEntry(r, textvariable=self.gpio_pin, width=60).pack(side="left", padx=(0, 6))
        for text, op in (("Read", "read"), ("Pull-up", "pullup"), ("Drive low", "low"), ("Drive high", "high")):
            ctk.CTkButton(r, text=text, width=82, fg_color="#555", command=lambda o=op: self.gpio(o)).pack(side="left", padx=2)
        ctk.CTkButton(r, text="Scan all", width=80, command=self.gpio_scan).pack(side="left", padx=(8, 2))
        self.gpio_out = ctk.CTkLabel(c, text="Pins used by the pad: K1-K5 = 1,2,4,5,6  ENC A/B/SW = 13,14,15  TFT SDA/SCL/RES/DC/CS/BLK = 11,12,10,9,8,7",
                                     text_color=MUTED, wraplength=540, justify="left")
        self.gpio_out.pack(anchor="w", padx=10, pady=(2, 6))

    def gpio(self, op):
        try:
            pin = int(self.gpio_pin.get())
        except ValueError:
            return self.app.set_status("Pin must be a number", error=True)

        def ok(r):
            txt = f"GPIO{r['pin']} = {r['val']}  {('(' + r['use'] + ')') if r.get('use') else ''}  {r.get('warn', '')}"
            self.gpio_out.configure(text=txt)
            self.sys(txt)
        self.req({"cmd": "gpio", "pin": pin, "op": op}, ok, "GPIO command refused")

    def gpio_scan(self):
        def ok(r):
            hi = [p for p, v in r["pins"] if v]
            lo = [p for p, v in r["pins"] if not v]
            txt = f"HIGH: {hi}\nLOW: {lo}"
            self.gpio_out.configure(text=txt)
            self.sys("gpio scan " + txt.replace("\n", "  "))
        self.req({"cmd": "gpio", "op": "scan"}, ok, "Scan failed")

    # ---------------------------------------------------------------- system
    def _build_system(self, parent):
        c = self.card(parent, "7. System")
        r = self.row(c)
        ctk.CTkButton(r, text="Reboot pad", width=110, command=lambda: self.req({"cmd": "reboot"}, lambda _r: self.sys("rebooting ..."), "Reboot failed")).pack(side="left", padx=2)
        ctk.CTkButton(r, text="Reboot into download mode", width=190, fg_color="#7a4a1f",
                      command=self.reboot_download).pack(side="left", padx=2)
        ctk.CTkButton(r, text="Verify keys on pad", width=150, fg_color="#555", command=self.app.verify_pad_keys).pack(side="left", padx=2)
        ctk.CTkLabel(c, text="Download mode = the ROM flasher, same as holding BOOT while plugging in; no button needed for the next upload. "
                     "The port will change - flash from the Arduino IDE, then unplug / re-plug.", text_color=MUTED, wraplength=540, justify="left").pack(anchor="w", padx=10, pady=(2, 6))

    def reboot_download(self):
        if messagebox.askyesno("Download mode", "Reboot the pad into the ROM download (flashing) mode?\nIt will disappear from the app until you re-flash or re-plug it."):
            self.req({"cmd": "reboot", "mode": "download"}, lambda _r: self.sys("pad is rebooting into download mode"), "Reboot failed")

    # ---------------------------------------------------------------- composite actions
    def ping(self):
        if not self.app.dev.connected:
            return self.app.set_status("Not connected", error=True)

        def work():
            times = []
            for i in range(5):
                t = time.perf_counter()
                self.app.dev.request({"cmd": "ping", "t": i}, timeout=2)
                times.append((time.perf_counter() - t) * 1000)
            return times
        self.app.bg(work, lambda t: self.sys(f"ping x5: min {min(t):.1f} ms  avg {sum(t) / len(t):.1f} ms  max {max(t):.1f} ms"), "Ping failed")

    def show_info(self):
        def ok(r):
            self.last_info = r
            self.sys("---- device info ----")
            for ln in info_lines(r):
                self.sys(ln)
            self.sys("---------------------")
        self.req({"cmd": "info"}, ok, "Info failed")

    def full_selftest(self):
        dev = self.app.dev
        if not dev.connected:
            return self.app.set_status("Not connected - nothing to test", error=True)
        self.sys("===== full self-test: watch the LED (R,G,B) and the screen =====")

        def work():
            res = []
            t0 = time.monotonic()
            while time.monotonic() - t0 < 90:                    # first boot after flashing: storage is formatted in the background
                h = dev.request({"cmd": "hello"}, timeout=3)
                if h.get("fs") or h.get("fs_state") == "failed" or "fs" not in h:
                    break
                self.app.post(lambda st=h.get("fs_state"): self.sys(f"waiting for the pad's storage ({st}) - first boot only ..."))
                time.sleep(3)

            def check(name, fn):
                try:
                    detail = fn()
                    res.append((True, name, detail or ""))
                except Exception as e:                       # noqa: BLE001
                    res.append((False, name, str(e)))

            def t_ping():
                ts = []
                for i in range(5):
                    t = time.perf_counter()
                    dev.request({"cmd": "ping", "t": i}, timeout=2)
                    ts.append((time.perf_counter() - t) * 1000)
                return f"avg {sum(ts) / len(ts):.1f} ms"

            def t_echo():
                msg = {"a": 1, "b": "xé\"y", "c": [1, 2]}
                r = dev.request({"cmd": "echo", "data": msg}, timeout=2)
                if r.get("data") != msg:
                    raise DeviceError(f"echo mismatch: {r.get('data')}")

            def t_info():
                r = dev.request({"cmd": "info"}, timeout=3)
                self.last_info = r
                bad = [k for k in ("ok_prefs", "ok_fs", "ok_disp") if k in r and not r[k]]
                if r.get("safe"):
                    raise DeviceError(f"SAFE MODE after {r.get('crashes')} crashes (last reset: {r.get('reset')}) - display disabled")
                if bad:
                    raise DeviceError("subsystem(s) failed: " + ", ".join(bad) + f"  boot log: {r.get('boot')}")
                return f"{r.get('chip')} core {r.get('core')} heap {r.get('heap')} reset {r.get('reset')}"

            def t_led():
                for rgbv in ((60, 0, 0), (0, 60, 0), (0, 0, 60)):
                    dev.request({"cmd": "led", "r": rgbv[0], "g": rgbv[1], "b": rgbv[2]}, timeout=2)
                    time.sleep(0.5)
                dev.request({"cmd": "led", "mode": "auto"}, timeout=2)
                return "sent red / green / blue - did the LED change colour?"

            def t_firmware():
                r = dev.request({"cmd": "selftest"}, timeout=10)
                bad = [k for k in ("nvs", "fs", "heap_ok") if not r.get(k)]
                if bad:
                    raise DeviceError("failed: " + ", ".join(bad))
                return f"nvs ok, fs ok, heap {r.get('heap')}, display {r.get('display')}"

            def t_inputs():
                r = dev.request({"cmd": "inputs"}, timeout=2)
                stuck = [i + 1 for i, v in enumerate(r["keys"]) if v]
                if stuck:
                    raise DeviceError(f"key(s) {stuck} read as PRESSED while idle - wiring / short?")
                return f"all 5 keys idle, encoder A={r.get('enc_a')} B={r.get('enc_b')}"

            def t_keys():
                r = dev.request({"cmd": "getkeys"}, timeout=3)
                return f"{sum(1 for s in r['slots'] if not s['def'])} custom key slot(s) stored on the pad"

            check("link: ping x5", t_ping)
            check("link: JSON echo round trip (unicode / quotes)", t_echo)
            check("firmware: subsystems + safe mode", t_info)
            check("firmware: NVS / filesystem / heap self-test", t_firmware)
            check("onboard LED colour cycle", t_led)
            check("keys idle (wiring)", t_inputs)
            check("stored key mappings readable", t_keys)
            return res

        def done(res):
            ok = sum(1 for r in res if r[0])
            for good, name, detail in res:
                self.sys(f"{'PASS' if good else 'FAIL'}  {name}  {detail}", err=not good)
            self.sys(f"===== self-test finished: {ok}/{len(res)} passed =====", err=ok != len(res))
            self.refresh_state()
        self.app.bg(work, done, "Self-test failed")

    def report(self):
        d = self.app.dev
        out = [f"DeskCompanion diagnostic report  {time.strftime('%Y-%m-%d %H:%M:%S')}",
               f"app: {APP_NAME}  python {platform.python_version()}  {platform.platform()}",
               f"pyserial {getattr(serial, '__version__', '?')}", "", "serial ports:"]
        for p in list_serial_ports():
            out.append(f"  {p['device']:10s} {fmt_vidpid(p)}  {p['desc']}  [{p['mfr']}] {'<-- ESP32' if p['esp'] else ''}")
        out += ["", f"connected: {d.connected}  port: {d.port}  rx {d.rx_bytes} B  tx {d.tx_bytes} B", "hello:", f"  {d.info}", "info:"]
        out += ["  " + ln for ln in info_lines(self.last_info)] if self.last_info else ["  (not fetched - press 'Device info')"]
        out += ["", "last terminal lines:"] + ["  " + ln for ln in self.lines[-150:]]
        text = "\n".join(out)
        self.app.clipboard_clear()
        self.app.clipboard_append(text)
        path = Path.home() / "deskcompanion_diag.txt"
        try:
            path.write_text(text, encoding="utf-8")
            where = f"and saved to {path}"
        except OSError:
            where = ""
        self.app.set_status(f"Diagnostic report copied to the clipboard {where} - paste it to whoever is helping you")
        self.sys("diagnostic report copied")


class App(ctk.CTk):
    def __init__(self):
        ui.install_theme()
        super().__init__()
        self.cfg = load_config()
        ctk.set_appearance_mode(self.cfg.get("appearance", "dark"))
        self.title(APP_NAME)
        self.geometry("1400x880")
        self.minsize(1300, 780)
        self.ui_q, self.closing, self.auto_flag = queue.Queue(), False, True
        self.connect_lock = threading.Lock()
        self.dev = Device(lambda: self.post(self._on_disconnected))
        self.macro_steps, self.gif_frames, self.gif_durs, self.gif_data = [], None, [], None
        self._pv_idx, self._pv_job, self._pv_img, self._bright_job = 0, None, None, None
        self.pending_slots, self._warned, self._was_connected = set(), set(), False
        self._fails = {}
        self.host, self.host_q, self.host_stop = HostInput(), queue.Queue(), threading.Event()
        self.hostmedia = HostMedia()
        self.focus = None                                       # Windows: remembers the program you were working in
        if platform.system() == "Windows":
            try:
                self.focus = WinFocus(Win32Api())
            except Exception:
                self.focus = None
        self.dnd = DnD(self)
        self.edit_layer, self.pad_layer, self.hw_listener = 0, None, None   # layer shown in the app; layer the pad is on; hardware-test hook
        self.active_win, self.profile_poll, self._usage_dirty = activewin.ActiveWindow(), 1.0, False
        self.cliphist = cliphist.ClipHistory()
        self.info_poll, self._info_cache, self._info_sent = 3.0, {}, (None, 0.0)
        try:
            self.badges = feeds.BadgeServer(token=self.cfg["info"].get("token"), port=int(self.cfg["info"].get("port", 0) or 0))
        except OSError:                                       # the saved port is taken: use any free one
            try:
                self.badges = feeds.BadgeServer(token=self.cfg["info"].get("token"))
            except OSError:
                self.badges = None
        if self.badges:
            self.cfg["info"]["token"], self.cfg["info"]["port"] = self.badges.token, self.badges.port
        self._profile_state = {"win": None, "layer": None, "text": "profiles are off"}
        self.hostact = hostactions.HostActions(self._allowed_host, lambda: bool(self.cfg.get("allow_shell")),
                                               type_clipboard=self._type_clipboard, type_text=self._type_text,
                                               read_clipboard=self._clipboard_text, counter=self._next_counter, script_runner=self.run_script,
                                               sysact=sysactions.SysActions(), layouts=winlayout.WinLayouts(), layout_store=lambda: self.cfg["layouts"], cliphist=self.cliphist,
                                               cfg_get=lambda k, d=None: self.cfg.get(k, d), focus_program=lambda: (self.active_win.get()[0] or ""),
                                               notify=lambda t, m: self.post(lambda: self.notify(t, m, "ok")))
        if self.host.impl is not None:
            self.host.impl.host_cb = self._host_cb
        self._script_lock, self.script_stop = threading.Lock(), threading.Event()
        self.tray_icon = None
        self.scheduler = scheduler.Scheduler(lambda: self.cfg["schedules"])
        self.api, self.api_error = None, ""
        self.pad = VirtualPad(self.exec_slot, self.exec_media, self.on_pad_change)
        self.pad.mode, self.pad.brightness = int(self.cfg["twin_mode"]), int(self.cfg["twin_bright"])
        self._build()
        self.bind_all("<Control-k>", lambda e: self.open_palette())
        self.bind_all("<Control-K>", lambda e: self.open_palette())
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
        threading.Thread(target=self._media_loop, daemon=True).start()
        threading.Thread(target=self._profile_loop, daemon=True).start()
        threading.Thread(target=self._info_loop, daemon=True).start()
        threading.Thread(target=self._schedule_loop, daemon=True).start()
        threading.Thread(target=self._clip_loop, daemon=True).start()
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
        self.status.configure(text=text, text_color=ERR if error else MUTED)
        self._log(text)

    def _log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", time.strftime("%H:%M:%S  ") + text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def autobackup(self):
        return autobackup_for()

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def tray_layer(self, n):
        self.set_edit_layer(n)
        self.show_layer_on_pad()

    def quit_app(self):
        self._really_close()

    def _on_close(self):
        if self.cfg.get("tray") and self.tray_icon is not None and not self.closing:
            self.withdraw()                                   # keep running (pad profiles, scheduler, API) in the tray
            return
        self._really_close()

    def tray_enable(self, on):
        """Switch the tray icon on / off. Returns '' or a reason it is not possible."""
        if on:
            if not tray.available():
                return "the tray icon needs:  pip install pystray"
            try:
                self.tray_icon = self.tray_icon or tray.Tray(self)
                self.tray_icon.start()
            except Exception as e:                            # noqa: BLE001  (no tray on this desktop, ...)
                self.tray_icon = None
                return f"the tray icon could not start: {e}"
        elif self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        return ""

    def _really_close(self):
        self.closing = True
        if self.tray_icon is not None:
            self.tray_icon.stop()
        self.api_stop()
        if self.badges:
            try:
                self.badges.close()
            except Exception:                               # noqa: BLE001
                pass
        save_config(self.cfg)
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
                    self._led_follow_cpu(cpu)
                    self.dev.send({"cmd": "stats", "cpu": round(cpu), "ram": round(ram)})
                    if not self.dev.info.get("core_only") and time.time() - last_sync > 60:
                        last_sync = time.time()                      # BEFORE the request: a pad that refuses it is asked again in a minute, not every second
                        self.dev.request(time_msg())
                except DeviceError:
                    pass

    def _led_follow_cpu(self, cpu):
        """Optional: the pad's RGB LED goes green -> amber -> red with the CPU load (called once a second from the telemetry thread)."""
        on, last = bool(self.cfg.get("led_cpu")), getattr(self, "_led_cpu_last", None)
        if self.dev.info.get("core_only"):
            return
        if not on:
            if last is not None:
                self._led_cpu_last = None
                self.dev.request({"cmd": "led", "mode": "auto"}, timeout=2)
            return
        rgb = padextras.cpu_color(cpu)
        if last is None or max(abs(a - b) for a, b in zip(rgb, last[0])) > 14 or time.time() - last[1] > 30:
            self.dev.request({"cmd": "led", "r": rgb[0], "g": rgb[1], "b": rgb[2]}, timeout=2)
            self._led_cpu_last = (rgb, time.time())

    def _media_loop(self):
        """Mirror this PC's volume / mute / playing onto the pad's media screen: on change, and every 10 s as a keep-alive."""
        last, sent_at = None, 0.0
        if self.hostmedia.kind == "pycaw":                          # COM objects need initialising once per thread
            try:
                import comtypes
                comtypes.CoInitialize()
            except Exception:                                       # noqa: BLE001
                pass
        while not self.closing:
            time.sleep(1.5)
            if not (self.dev.connected and not self.dev.busy and self.cfg.get("host_media_sync", True) and self.hostmedia.available):
                last = None                                         # re-send everything after a reconnect
                continue
            st = {k: v for k, v in self.hostmedia.state().items() if v is not None}
            if not st:
                continue
            if st != last or time.time() - sent_at > 10:
                try:
                    self.dev.send(dict(st, cmd="media"))
                    last, sent_at = st, time.time()
                except DeviceError:
                    last = None
                self.post(lambda st=st: self._media_mirror(st))

    def _media_mirror(self, st):
        """The virtual pad follows the real values as well, so the twin never disagrees with the physical pad."""
        if "vol" in st:
            self.pad.vol = st["vol"]
        if "muted" in st:
            self.pad.muted = st["muted"]
        if "playing" in st:
            self.pad.playing = st["playing"]
        self.pad.dirty = True

    def notify(self, title, text, kind="ok"):
        if not self.cfg.get("notify", True) or self.closing:
            return
        play_event_sound(kind, self)
        Toast(self, title, text, kind)

    def _monitor_loop(self):
        seen = set()
        while not self.closing:
            allp = list_serial_ports()
            info = {p["device"]: p for p in allp}
            ports = [p["device"] for p in allp if p["esp"]]
            for d in set(ports) - seen:                      # a pad (or any Espressif device) just appeared
                p = info[d]
                self.post(lambda p=p: self._dev_note(f"Espressif USB device appeared: {p['device']}  {fmt_vidpid(p)}  {p['desc']}  - {p['hint']}"))
            for d in seen - set(ports):
                self.post(lambda d=d: self._dev_note(f"serial port {d} disappeared (unplugged / rebooting / re-enumerating)"))
            seen = set(ports)
            gone = set(self._fails) | self._warned
            for p in gone - set(ports):                      # unplugged: forget, so the next plug-in starts fresh
                self._fails.pop(p, None)
                self._warned.discard(p)
                self.post(self._clear_trouble)
            if self.auto_flag and not self.dev.connected:
                for port in ports:
                    if self._try_connect(port, info.get(port)):
                        break
            time.sleep(1.0)

    def _dev_note(self, text, err=False):
        if hasattr(self, "devtab"):
            self.devtab.sys(text, err)

    @staticmethod
    def trouble_text(pinfo, fails, recently_flashed=False):
        """Plain-language reason + next steps for a pad that is visible as a serial port but does not answer."""
        vp = fmt_vidpid(pinfo) if pinfo else "?"
        pid = (pinfo or {}).get("pid")
        if pid == 0x1001:
            return (f"The pad shows up as {vp}: that is the chip's built-in bootloader / serial, not DeskCompanion. "
                    "Unplug the cable and plug it back in WITHOUT holding BOOT, then wait ~10 s. "
                    "(It also looks like this when a sketch was built with USB Mode = Hardware CDC and JTAG.)")
        lines = [f"A USB device ({vp}) is there on {pinfo['device'] if pinfo else 'a port'} but it does not answer yet (tried {fails}x)."]
        if recently_flashed or fails < 4:
            lines.append("Right after flashing, the first start prepares the pad's storage and can take up to ~40 s - the app keeps trying by itself.")
        lines.append("If it stays like this: 1) unplug and re-plug the cable (try another USB port), 2) Diagnostics -> Probe all ports, "
                     "3) on Windows: Device Manager -> View -> Show hidden devices -> uninstall the old 'USB Serial Device' / 'USB Composite Device' "
                     "entries of the board, then re-plug, 4) check the LED: a red double-blink means safe mode, no LED means the sketch is not running.")
        return "  ".join(lines)

    def _show_trouble(self, port, pinfo, fails):
        text = self.trouble_text(pinfo, fails, time.time() - getattr(self, "_flashed_at", 0) < 180)
        self.conn_pill.set("Not answering", ERR)
        self.set_status(text.split(".  ")[0] + ".", error=True)
        if hasattr(self, "trouble_lbl"):
            self.trouble_lbl.configure(text=text)
            self.trouble_card.pack(fill="x", padx=2, pady=(0, 8), before=self.home_first)

    def _clear_trouble(self):
        if hasattr(self, "trouble_card"):
            self.trouble_card.pack_forget()

    def _try_connect(self, port, pinfo=None):
        if not self.connect_lock.acquire(blocking=False):
            return False
        try:
            info = self.dev.connect(port, hello_wait=8 if self._fails.get(port, 0) < 2 else 20)
        except (DeviceError, serial.SerialException, OSError) as e:
            self._fails[port] = self._fails.get(port, 0) + 1
            msg = str(e).lower()
            busy = isinstance(e, PermissionError) or any(w in msg for w in ("denied", "busy", "permission", "in use"))
            vp = fmt_vidpid(pinfo) if pinfo else "?"
            hint = (pinfo or {}).get("hint", "")
            self.post(lambda e=e: self._log(f"connect {port} failed: {e}"))
            if self._fails[port] >= 2 and not busy:
                self.post(lambda n=self._fails[port]: self._show_trouble(port, pinfo, n))
            self.post(lambda e=e: self._dev_note(f"connect {port} ({vp}) failed: {e}   {hint}", err=True))
            if port not in self._warned and (busy or self._fails[port] >= 2):   # 2 tries x 8 s = the pad had plenty of time to boot
                self._warned.add(port)
                if busy:
                    self.post(lambda: self.notify(f"{port} is in use by another program",
                              "Close the Arduino / PlatformIO serial monitor - DeskCompanion connects by itself afterwards.", "warn"))
                else:
                    self.post(lambda: self.notify(f"USB device found on {port}  ({vp})",
                              (hint + "  " if hint else "") + "It does not answer as DeskCompanion. Open the Dev tab and press 'Probe all ports'.", "warn"))
            return False
        finally:
            self.connect_lock.release()
        self._fails.pop(port, None)
        self.post(self._clear_trouble)
        self.post(lambda: self._on_connected(info))
        return True

    # ---------------------------------------------------------------- UI construction
    def _build(self):
        bar = ctk.CTkFrame(self, height=32, corner_radius=0, fg_color=ui.SIDE, border_width=0)
        bar.pack(side="bottom", fill="x")
        self.status = ctk.CTkLabel(bar, text="Searching for the pad...", anchor="w", text_color=MUTED, font=ui.font(12))
        self.status.pack(side="left", fill="x", expand=True, padx=16, pady=5)
        self.tabs = SideTabs(self, on_change=self._page_changed, version=f"v{APP_VERSION}")
        self.tabs.pack(side="top", fill="both", expand=True)
        for group, pages in PAGES:
            self.tabs.group(group)
            for name, label, subtitle in pages:
                self.tabs.add(name, label, label, subtitle)
        foot = self.tabs.foot
        self.conn_pill = Pill(foot, "Searching...", WARN)
        self.conn_pill.pack(fill="x", pady=(0, 8))
        self.theme_var = tk.BooleanVar(value=self.cfg.get("appearance", "dark") == "light")
        ctk.CTkSwitch(foot, text="Light theme", variable=self.theme_var, command=self._theme_toggled, font=ui.font(12)).pack(anchor="w", padx=4)
        self._build_device_tab(self.tabs.tab("Device"))     # first: creates log_box
        self._build_virtual(self.tabs.tab("Virtual Pad"))
        self._build_dashboard(self.tabs.tab("Dashboard"))
        self._build_macro(self.tabs.tab("Macro Creator"))
        self._build_gif(self.tabs.tab("GIF Upload"))
        self._build_profiles(self.tabs.tab("Profiles"))
        self._build_info(self.tabs.tab("Info Screen"))
        self.scripts = scripts_page.ScriptsPage(self, self.tabs.tab("Scripts"))
        self.automation = automation.AutomationPage(self, self.tabs.tab("Automation"))
        if self.cfg["api"]["on"]:
            self.api_start()
        self.devtab = DevTab(self, self.tabs.tab("Dev"))
        self.dev.on_line, self.dev.on_event = self.devtab.log, self._on_pad_event
        self.refresh_ports()
        self._restyle_plain_widgets()

    def _page_changed(self, name):
        if name == "Dashboard" and hasattr(self, "usage_bars"):
            self.refresh_usage()
        elif name == "Device":
            self.refresh_fw_status()

    def _theme_toggled(self):
        mode = "light" if self.theme_var.get() else "dark"
        self.cfg["appearance"] = mode
        save_config(self.cfg)
        ctk.set_appearance_mode(mode)
        self._restyle_plain_widgets()

    def _restyle_plain_widgets(self):
        """Widgets that are not customtkinter's (Treeview, Listbox, Canvas) do not follow the theme by themselves."""
        d = ctk.get_appearance_mode() == "Dark"
        style = ttk.Style(self)
        bg, fg = ("#121215", "#f4f4f6") if d else ("#ffffff", "#0b0b0d")
        style.configure("Pad.Treeview", background=bg, fieldbackground=bg, foreground=fg)
        style.map("Pad.Treeview", background=[("selected", "#0e7490")], foreground=[("selected", "#ffffff")])
        hbg, hfg = ("#1a1a1f", "#8e8e9b") if d else ("#f0f0f3", "#62626d")
        style.configure("Pad.Treeview.Heading", background=hbg, foreground=hfg, relief="flat", borderwidth=0)
        style.map("Pad.Treeview.Heading", background=[("active", hbg)])
        if hasattr(self, "seq_list"):
            self.seq_list.configure(bg="#1a1a1f" if d else "#f0f0f3", fg=fg, selectbackground="#0e7490")

    def _title(self, parent, text, row=0):
        ui.heading(parent, text).grid(row=row, column=0, columnspan=6, sticky="w", padx=14, pady=(12, 4))

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
                     text_color=MUTED, wraplength=240, justify="left").pack(anchor="w", padx=12)
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

        # -- layer selector
        lbar = ctk.CTkFrame(center, fg_color="transparent")
        lbar.pack(pady=(0, 6))
        self.layer_seg = ctk.CTkSegmentedButton(lbar, values=["Layer 1", "Layer 2", "Layer 3"], command=self._layer_seg, width=250)
        self.layer_seg.set("Layer 1")
        self.layer_seg.pack(side="left")
        self.pad_layer_pill = Pill(lbar, "pad layer: ?", FAINT)
        self.pad_layer_pill.pack(side="left", padx=10)
        ui.secondary_button(lbar, "Show on pad", self.show_layer_on_pad, width=100).pack(side="left")

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
                     text_color=MUTED, wraplength=280, justify="left").pack(anchor="w", padx=12)
        ctk.CTkButton(right, text="Reset this layer to defaults", fg_color="#555", command=self.reset_defaults).pack(
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
        ctk.CTkLabel(right, text="Sandbox: click inside, then press virtual keys", text_color=MUTED).pack(
            anchor="w", padx=12, pady=(6, 0))
        self.sandbox = ctk.CTkTextbox(right, height=64)
        self.sandbox.pack(fill="x", padx=12, pady=(2, 4))
        ctk.CTkLabel(right, text="Test log", text_color=MUTED).pack(anchor="w", padx=12)
        self.vp_log_box = ctk.CTkTextbox(right, height=96, state="disabled")
        self.vp_log_box.pack(fill="x", padx=12, pady=(0, 4))

        ctk.CTkLabel(right, text="Virtual screen", font=bold).pack(anchor="w", padx=12, pady=(8, 2))
        row = ctk.CTkFrame(right, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=2)
        ctk.CTkLabel(row, text="Mode").pack(side="left")
        self.vp_mode_var = tk.StringVar(value=MODE_CHOICES[self.pad.mode - 1])
        ctk.CTkOptionMenu(row, values=MODE_CHOICES, variable=self.vp_mode_var, width=150,
                          command=lambda v: self.pad.set_mode(int(v.split()[0]))).pack(side="right")
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
        if hasattr(self, "automation") and hasattr(self.automation, "gestures"):
            self.automation.gestures.reload_categories()
        if hasattr(self, "automation") and hasattr(self.automation, "choices"):
            self.automation.choices.reload_categories()

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

    def _layers_supported(self):
        return int(self.dev.info.get("layers", 1) or 1) >= LAYERS if self.dev.connected else True

    def _pending_for(self, layer):
        lm, pushed = self.cfg["layers"][layer], self.cfg["pushed_layers"][layer]
        out = set()
        for sl in range(1, 8):
            m = lm[str(sl)]
            if spec_json(resolve_spec(self.cfg, m["cat"], m["action"])) != pushed.get(str(sl)):
                out.add(sl)
        return out

    def recompute_pending(self):
        self.pending_by_layer = [self._pending_for(n) for n in range(LAYERS)]
        self.pending_slots = self.pending_by_layer[self.edit_layer]
        extra = int(self.cfg.get("pushed_mode") != self.pad.mode) + int(self.cfg.get("pushed_bright") != self.pad.brightness)
        n = sum(len(x) for x in self.pending_by_layer) + extra
        if hasattr(self, "upload_btn2"):
            self.upload_btn2.configure(text="Upload to pad" if not n else f"Upload to pad  ({n} unsent)",
                                       fg_color=ui.WARN_FILL if n else ui.ACCENT_FILL)
            self.padview.refresh_pending()

    def _mark_pushed(self, slot, j, layer=None):
        layer = self.edit_layer if layer is None else layer
        self.cfg["pushed_layers"][layer][str(slot)] = j
        save_config(self.cfg)
        self.recompute_pending()

    def _mark_display_pushed(self, mode, bright):
        self.cfg["pushed_mode"], self.cfg["pushed_bright"] = mode, bright
        save_config(self.cfg)
        self.recompute_pending()

    def _remap_msg(self, layer, slot, spec):
        msg = {"cmd": "remap", "key": slot, "type": spec[0], "val": spec[1]}
        if layer:
            msg["layer"] = layer
        return msg

    def resolve_action(self, cat, name):
        return resolve_spec(self.cfg, cat, name)

    def spec_valid(self, spec):
        return spec_ok({"type": spec[0], "val": spec[1]})

    def _pad_cap(self, cap):
        return bool(self.dev.connected and cap in (self.dev.info.get("caps") or []))

    # ---- key gestures (hold / double-tap): the pad must end up with exactly the gestures in cfg["gestures"]
    def _sync_gestures(self):
        existing = set()
        for lay in range(LAYERS):
            r = self.dev.request({"cmd": "getkeys", **({"layer": lay} if lay else {})}, timeout=4)
            for sl in r.get("slots", []):
                if sl.get("h"):
                    existing.add((lay, sl["s"], "hold"))
                if sl.get("d"):
                    existing.add((lay, sl["s"], "double"))
            for i, flag in enumerate(r.get("pt") or []):                   # dial pressed + turned right / left (firmware 1.4)
                if flag:
                    existing.add((lay, 8 + i, "press"))
        msgs = padextras.gesture_msgs(self, existing, lambda c, a: resolve_spec(self.cfg, c, a), set(self.dev.info.get("caps") or []))
        for m in msgs:
            self.dev.request(m)
        return len(msgs)

    def push_gestures(self, what=""):
        if not self.dev.connected:
            return self.set_status((what + " - " if what else "") + "saved; it is sent to the pad with the next upload")
        if not self._pad_cap("gestures"):
            return self.set_status("Hold / double-tap actions need firmware 1.3 - update the pad (Device -> Firmware). The setting is saved.", error=True)
        self.bg(self._sync_gestures, lambda n: self.set_status(f"{what or 'Gestures'} - sent to the pad"), "Sending the gesture failed")

    def _autostart_toggled(self):
        on = bool(self.autostart_var.get())
        try:
            autostart.enable(autostart.launch_command()) if on else autostart.disable()
        except Exception as e:                                # noqa: BLE001  (read-only registry, no home folder ...)
            self.autostart_var.set(not on)
            return self.set_status(f"Could not change the start-up setting: {e}", error=True)
        self.set_status("The app will start minimised when you log in" if on else "The app no longer starts with your computer")

    def _tray_toggled(self):
        on = bool(self.tray_var.get())
        why = self.tray_enable(on)
        if why:
            self.tray_var.set(False)
            self.cfg["tray"] = False
            return self.set_status(f"Tray: {why}", error=True)
        self.cfg["tray"] = on
        save_config(self.cfg)
        self.set_status("Closing the window now keeps the app running in the tray" if on else "Closing the window quits the app")

    def _core_only(self, what="that"):
        if self.dev.connected and self.dev.info.get("core_only"):
            self.set_status(f"The pad runs the CoreBringup diagnostic sketch, which cannot do {what}. Flash the full firmware first (Device -> Update).", error=True)
            return True
        return False

    def upload_slots(self, slots, layer=None):
        if not self.dev.connected or self._core_only("key mapping"):
            return
        layer = self.edit_layer if layer is None else layer
        if layer and not self._layers_supported():
            return self.set_status("This pad's firmware has no layers - update it (Device tab -> Firmware)", error=True)
        jobs = []
        for s in slots:
            m = self.cfg["layers"][layer][str(s)]
            spec = resolve_spec(self.cfg, m["cat"], m["action"])
            if spec:
                jobs.append((s, spec))
        osv = self.cfg["os"]

        def work():
            self.dev.request({"cmd": "os", "val": osv})
            for s, spec in jobs:
                self.dev.request(self._remap_msg(layer, s, spec))
                self.post(lambda s=s, j=spec_json(spec): self._mark_pushed(s, j, layer))
        self.bg(work, lambda _: self.set_status(("Layer %d: " % (layer + 1) if layer else "Sent to pad: ") + ", ".join(SLOT_LABELS[x[0]] for x in jobs)),
                "Upload failed")

    def _verify_keys_sync(self):
        """Compare what the pad stored (getkeys: length + CRC of each slot's JSON) with what the app believes it uploaded.
        Returns a list of problems, or None when the firmware is too old to answer."""
        bad, layers = [], range(LAYERS)
        for lay in layers:
            try:
                r = self.dev.request({"cmd": "getkeys", **({"layer": lay} if lay else {})}, timeout=4)
            except DeviceError:
                return None if lay == 0 else bad
            if lay and "layers" not in r:                       # firmware without layers: nothing more to compare
                break
            tag = f"L{lay + 1} " if lay else ""
            for slot in r.get("slots", []):
                s = slot["s"]
                m = self.cfg["layers"][lay][str(s)]
                spec = resolve_spec(self.cfg, m["cat"], m["action"])
                if slot["def"]:
                    if (m["cat"], m["action"]) != DEFAULT_LAYER_MAPS[lay][s]:
                        bad.append(f"{tag}{SLOT_LABELS[s]}: still factory default on the pad")
                    continue
                if not spec:
                    continue
                j = compact_json({"type": spec[0], "val": spec[1]}).encode("utf-8")
                if slot["len"] != len(j) or slot["crc"] != (zlib.crc32(j) & 0xFFFFFFFF):
                    bad.append(f"{tag}{SLOT_LABELS[s]}: pad has different data than the app sent")
        return bad

    def verify_pad_keys(self):
        if not self.dev.connected:
            return self.set_status("Not connected", error=True)

        def done(bad):
            if bad is None:
                self.set_status("This firmware cannot report its key slots (older version)", error=True)
            elif bad:
                self.set_status("Key check: " + "; ".join(bad), error=True)
                self._dev_note("key check FAILED: " + "; ".join(bad), err=True)
            else:
                self.set_status("Key check OK: the pad stores exactly what the app uploaded")
                self._dev_note("key check OK")
        self.bg(self._verify_keys_sync, done, "Key check failed")

    def upload_all(self):
        if not self.dev.connected:
            return self.set_status("Connect the pad first (Device tab / USB cable)", error=True)
        if self._core_only("key mapping"):
            return
        layers = range(LAYERS) if self._layers_supported() else range(1)
        jobs = []
        for lay in layers:
            for sl in range(1, 8):
                m = self.cfg["layers"][lay][str(sl)]
                spec = resolve_spec(self.cfg, m["cat"], m["action"])
                if spec:
                    jobs.append((lay, sl, spec))
        mode, bright, osv = self.pad.mode, self.pad.brightness, self.cfg["os"]
        self.upload_btn2.configure(state="disabled")

        def work():
            self.dev.request({"cmd": "os", "val": osv})
            self.dev.request(time_msg())
            self._push_layout()
            for lay, sl, spec in jobs:
                self.dev.request(self._remap_msg(lay, sl, spec))
                self.post(lambda lay=lay, sl=sl, j=spec_json(spec): self._mark_pushed(sl, j, lay))
            if self._pad_cap("gestures"):
                self._sync_gestures()
            self.dev.request({"cmd": "brightness", "val": bright})
            self.dev.request({"cmd": "mode", "val": mode})
            self.post(lambda: self._mark_display_pushed(mode, bright))
            return self._verify_keys_sync()

        def done(bad):
            self.upload_btn2.configure(state="normal")
            n = len(set(j[0] for j in jobs))
            if bad:
                self.set_status("Uploaded, but the read-back check found problems: " + "; ".join(bad), error=True)
            else:
                self.set_status(f"Uploaded to the pad: {n} layer{'s' if n > 1 else ''} x 7 key slots, brightness and mode" +
                                ("" if bad is None else " (read back and verified)"))
            self.vp_log("uploaded everything to the physical pad")
        self.bg(work, done, "Upload failed", fail=lambda: self.upload_btn2.configure(state="normal"))

    def on_pad_change(self, kind):
        if kind == "mode":
            v = MODE_CHOICES[self.pad.mode - 1]
            self.mode_var.set(v)
            if hasattr(self, "vp_mode_var"):
                self.vp_mode_var.set(v)
            self.cfg["twin_mode"] = self.pad.mode
        elif kind == "layer":
            self.set_edit_layer(self.pad.layer)
            return
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
                       ("  (pip install pynput)" if platform.system() != "Windows" else ""), ERR
        elif self.live_var.get():
            txt = ("LIVE: virtual keys press real keys on this PC. They go to the last program you used - focus is handed "
                   "back automatically. Click into the sandbox to test inside this app."
                   if self.focus else
                   "LIVE: virtual keys press real keys on the window that has the focus. Use the delay below or the sandbox.")
            col = OK
        else:
            txt, col = "DRY RUN: only the virtual screen reacts. Switch on to press real keys on this PC.", WARN
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
                if spec[0] == "host":                          # opening a URL / starting a program needs no key injection
                    self._host_cb(spec[1])
                else:
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
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        sc = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        sc.grid(row=0, column=0, sticky="nsew")
        # banners (shown only when needed)
        self.fw_banner = ctk.CTkFrame(sc, fg_color=ui.WARN_FILL, border_width=0)
        self.fw_banner_lbl = ctk.CTkLabel(self.fw_banner, text="", text_color=ui.WHITE, wraplength=860, justify="left", anchor="w")
        self.fw_banner_lbl.pack(side="left", padx=14, pady=10, fill="x", expand=True)
        ctk.CTkButton(self.fw_banner, text="Update firmware", width=130, fg_color=ui.WHITE, hover_color="#f0f0f0", text_color="#111111",
                      command=lambda: self.tabs.set("Device")).pack(side="right", padx=12)
        self.trouble_card = ctk.CTkFrame(sc, fg_color=ui.ERR_FILL, border_width=0)
        ctk.CTkLabel(self.trouble_card, text="Can't connect?", font=ui.font(14, "bold"), text_color=ui.WHITE, anchor="w").pack(anchor="w", padx=14, pady=(10, 0))
        self.trouble_lbl = ctk.CTkLabel(self.trouble_card, text="", text_color=ui.WHITE, wraplength=900, justify="left", anchor="w")
        self.trouble_lbl.pack(anchor="w", padx=14, pady=(2, 10))
        self.safe_banner = ctk.CTkFrame(sc, fg_color=ui.ERR_FILL, border_width=0)
        self.safe_banner_lbl = ctk.CTkLabel(self.safe_banner, text="", text_color=ui.WHITE, wraplength=860, justify="left", anchor="w")
        self.safe_banner_lbl.pack(side="left", padx=14, pady=10, fill="x", expand=True)
        ctk.CTkButton(self.safe_banner, text="Recovery options", width=140, fg_color=ui.WHITE, hover_color="#f0f0f0", text_color="#111111",
                      command=lambda: self.tabs.set("Device")).pack(side="right", padx=12)

        box = self.home_first = ctk.CTkFrame(sc)
        box.pack(fill="x", pady=6, padx=2)
        ui.heading(box, "Connection").grid(row=0, column=0, columnspan=6, sticky="w", padx=16, pady=(14, 4))
        self.port_var, self.port_map = tk.StringVar(), {}
        self.port_box = ctk.CTkComboBox(box, variable=self.port_var, values=[""], width=330)
        self.port_box.grid(row=1, column=0, padx=(16, 6), pady=4)
        ui.secondary_button(box, "Rescan", self.refresh_ports, width=80).grid(row=1, column=1, padx=4)
        self.conn_btn = ctk.CTkButton(box, text="Connect", width=110, command=self.toggle_connect)
        self.conn_btn.grid(row=1, column=2, padx=4)
        self.auto_var = tk.BooleanVar(value=True)
        ctk.CTkSwitch(box, text="Auto-connect", variable=self.auto_var,
                      command=lambda: setattr(self, "auto_flag", self.auto_var.get())).grid(row=1, column=3, padx=12)
        self.sim_btn = ui.secondary_button(box, "Simulate pad (no hardware)", self.toggle_simulate, width=210)
        self.sim_btn.grid(row=1, column=4, padx=(4, 12))
        self.conn_lbl = ctk.CTkLabel(box, text="Not connected", text_color=WARN, anchor="w")
        self.conn_lbl.grid(row=2, column=0, columnspan=6, sticky="w", padx=16, pady=(2, 4))
        ui.muted(box, "No pad handy? 'Simulate' connects the app to an in-process stand-in that speaks the same protocol, so you can try remapping, "
                 "macros and GIF upload end-to-end without hardware.", wraplength=900).grid(row=3, column=0, columnspan=6, sticky="w", padx=16, pady=(0, 12))

        hb = ctk.CTkFrame(sc)
        hb.pack(fill="x", pady=6, padx=2)
        top = ctk.CTkFrame(hb, fg_color="transparent", border_width=0)
        top.pack(fill="x", padx=16, pady=(14, 4))
        ui.heading(top, "Pad health").pack(side="left")
        ui.secondary_button(top, "Refresh", self.refresh_health, width=80).pack(side="right")
        grid = ctk.CTkFrame(hb, fg_color="transparent", border_width=0)
        grid.pack(fill="x", padx=12, pady=(0, 12))
        self.health = {}
        for i, (key, label) in enumerate((("fw", "Firmware"), ("layer", "Pad layer"), ("flash", "Free flash"), ("reset", "Last reset"),
                                          ("disp", "Display"), ("hid", "USB keyboard"), ("fs", "Storage"), ("temp", "Chip temp"))):
            t = ctk.CTkFrame(grid, fg_color=CARD2, corner_radius=10, border_width=0, width=200, height=64)
            t.grid(row=i // 4, column=i % 4, padx=5, pady=5, sticky="nsew")
            t.grid_propagate(False)
            ui.muted(t, label, font=ui.font(11)).place(x=12, y=8)
            v = ctk.CTkLabel(t, text="-", font=ui.font(15, "bold"), anchor="w")
            v.place(x=12, y=30)
            self.health[key] = v
        for c in range(4):
            grid.grid_columnconfigure(c, weight=1)

        met = ctk.CTkFrame(sc)
        met.pack(fill="x", pady=6, padx=2)
        ui.heading(met, "Live telemetry (sent to the pad every second)").grid(row=0, column=0, columnspan=6, sticky="w", padx=16, pady=(14, 4))
        self.cpu_bar, self.ram_bar = ctk.CTkProgressBar(met, width=360), ctk.CTkProgressBar(met, width=360)
        self.cpu_lbl, self.ram_lbl = ctk.CTkLabel(met, text="CPU  0%", width=80), ctk.CTkLabel(met, text="RAM  0%", width=80)
        for r, (lbl, bar) in enumerate(((self.cpu_lbl, self.cpu_bar), (self.ram_lbl, self.ram_bar)), start=1):
            lbl.grid(row=r, column=0, padx=(16, 6), pady=(3, 8))
            bar.grid(row=r, column=1, padx=6, pady=(3, 8), sticky="w")
            bar.set(0)

        qa = ctk.CTkFrame(sc)
        qa.pack(fill="x", pady=6, padx=2)
        ui.heading(qa, "Quick actions").pack(anchor="w", padx=16, pady=(14, 6))
        row = ctk.CTkFrame(qa, fg_color="transparent", border_width=0)
        row.pack(fill="x", padx=12, pady=(0, 14))
        ctk.CTkButton(row, text="Upload everything to the pad", command=self.upload_all).pack(side="left", padx=4)
        ui.secondary_button(row, "Setup wizard", self.open_wizard).pack(side="left", padx=4)
        ui.secondary_button(row, "Guided hardware test", self.open_hwtest).pack(side="left", padx=4)
        ui.secondary_button(row, "Pick a GIF", lambda: self.tabs.set("GIF Upload")).pack(side="left", padx=4)
        ui.secondary_button(row, "Command palette  (Ctrl+K)", self.open_palette).pack(side="left", padx=4)

        mp = ctk.CTkFrame(sc)
        mp.pack(fill="x", pady=6, padx=2)
        self.map_title = ui.heading(mp, "Key map - Layer 1")
        self.map_title.grid(row=0, column=0, columnspan=6, sticky="w", padx=16, pady=(14, 4))
        ui.muted(mp, "A keyboard-friendly alternative to dragging on the Pad page. Changes are stored on the pad once uploaded.").grid(
            row=1, column=0, columnspan=6, sticky="w", padx=16)
        self.rows = {}
        for i, slot in enumerate(SLOT_LABELS, start=2):
            ctk.CTkLabel(mp, text=SLOT_LABELS[slot], width=140, anchor="w").grid(row=i, column=0, padx=(16, 4), pady=3)
            cv, av = tk.StringVar(), tk.StringVar()
            cc = ctk.CTkComboBox(mp, variable=cv, values=[""], width=180, state="readonly", command=lambda v, s=slot: self._on_cat(s, v))
            ac = ctk.CTkComboBox(mp, variable=av, values=[""], width=300, state="readonly", command=lambda v, s=slot: self._on_action(s, v))
            cc.grid(row=i, column=1, padx=4, pady=3)
            ac.grid(row=i, column=2, padx=4, pady=3)
            self.rows[slot] = (cc, ac, cv, av)
        bt = ctk.CTkFrame(mp, fg_color="transparent", border_width=0)
        bt.grid(row=10, column=0, columnspan=6, sticky="w", padx=14, pady=10)
        ctk.CTkButton(bt, text="Upload all to pad", command=self.upload_all).pack(side="left", padx=(0, 8))
        ui.secondary_button(bt, "Reset this layer to defaults", self.reset_defaults).pack(side="left")
        self.refresh_action_lists()

        us = ctk.CTkFrame(sc)
        us.pack(fill="x", pady=6, padx=2)
        top = ctk.CTkFrame(us, fg_color="transparent", border_width=0)
        top.pack(fill="x", padx=16, pady=(14, 4))
        ui.heading(top, "How you use the pad").pack(side="left")
        ui.secondary_button(top, "Reset counters", self.reset_usage, width=120).pack(side="right")
        self.usage_bars = {}
        ug = ctk.CTkFrame(us, fg_color="transparent", border_width=0)
        ug.pack(fill="x", padx=16, pady=(0, 14))
        for i, key in enumerate(("K1", "K2", "K3", "K4", "K5", "dial+", "dial-")):
            ctk.CTkLabel(ug, text={"dial+": "dial right", "dial-": "dial left"}.get(key, key), width=80, anchor="w").grid(row=i, column=0, pady=2)
            bar = ctk.CTkProgressBar(ug, width=420, height=10)
            bar.grid(row=i, column=1, padx=8, pady=2, sticky="w")
            bar.set(0)
            lbl = ctk.CTkLabel(ug, text="0", width=60, anchor="w", text_color=MUTED)
            lbl.grid(row=i, column=2, pady=2)
            self.usage_bars[key] = (bar, lbl)
        ui.muted(us, "Counted while the app runs and the pad is connected. Stored only on this computer.").pack(anchor="w", padx=16, pady=(0, 12))
        self.after(30000, self._flush_usage)

    def refresh_usage(self):
        u = self.cfg["usage"]
        top = max([int(u.get(k, 0)) for k in self.usage_bars] + [1])
        for k, (bar, lbl) in self.usage_bars.items():
            n = int(u.get(k, 0))
            bar.set(n / top)
            lbl.configure(text=str(n))

    def reset_usage(self):
        self.cfg["usage"].clear()
        save_config(self.cfg)
        self.refresh_usage()

    def _flush_usage(self):
        if self.closing:
            return
        if self._usage_dirty:
            self._usage_dirty = False
            save_config(self.cfg)
            if self.tabs.get() == "Dashboard":
                self.refresh_usage()
        self.after(30000, self._flush_usage)

    def refresh_health(self):
        if not self.dev.connected:
            return self.set_status("Connect the pad first", error=True)
        self.bg(lambda: self.dev.request({"cmd": "info"}), self._update_health, "Could not read the pad")

    def _update_health(self, i):
        h = self.health
        kind, _txt = self.fw_status()
        h["fw"].configure(text=("CoreBringup" if i.get("core_only") else str(i.get("fw", "?"))), text_color={"ok": OK, "off": MUTED}.get(kind, WARN))
        h["layer"].configure(text=str(int(i.get("layer", 0)) + 1))
        h["flash"].configure(text=f"{i.get('fs_free', 0) // 1024} KB")
        h["reset"].configure(text=str(i.get("reset", "?")), text_color=TEXT if i.get("crashes", 0) == 0 else ERR)
        why = i.get("disp_why") or ""
        h["disp"].configure(text="ok" if i.get("ok_disp") else ("stalled" if why in ("init_hang", "skipped_after_hang") else "off" if i.get("nodisp") or i.get("safe") else "FAILED"),
                            text_color=OK if i.get("ok_disp") else ERR)
        if why in ("init_hang", "skipped_after_hang") and getattr(self, "_disp_warned", None) != id(self.dev.ser):
            self._disp_warned = id(self.dev.ser)
            self.set_status("The pad's display start-up stalled, so the display is off - everything else (keys, layers, LED, USB) works. "
                            "Check the wiring / User_Setup.h and re-plug the pad to try again.", error=True)
        h["hid"].configure(text="ok" if i.get("hid") else "off (USB Mode)", text_color=OK if i.get("hid") else WARN)
        h["fs"].configure(text=str(i.get("fs_state", "?")), text_color=OK if i.get("ok_fs") else WARN)
        h["temp"].configure(text=f"{i.get('temp', 0):.0f} C")
        self._safe_banner(i)

    def categories(self):
        return list(ACTIONS) + (["Custom"] if self.cfg["custom"] else [])

    def names_for(self, cat):
        return list(self.cfg["custom"]) if cat == "Custom" else [a[0] for a in ACTIONS.get(cat, [])]

    def refresh_action_lists(self):
        if hasattr(self, "map_title"):
            self.map_title.configure(text=f"Key map - {LAYER_NAMES[self.edit_layer]}")
        for slot, (cc, ac, cv, av) in self.rows.items():
            m = self.cfg["map"][str(slot)]
            cat, name = m["cat"], m["action"]
            if cat not in self.categories() or name not in self.names_for(cat):
                cat, name = DEFAULT_LAYER_MAPS[self.edit_layer][slot]
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
        lay = self.edit_layer
        lm = self.cfg["layers"][lay]
        lm.clear()
        lm.update({str(sl): {"cat": c, "action": n} for sl, (c, n) in DEFAULT_LAYER_MAPS[lay].items()})
        save_config(self.cfg)
        self.refresh_action_lists()
        self.padview.refresh()
        self.recompute_pending()
        if self.dev.connected and (lay == 0 or self._layers_supported()):
            def done(_):
                for sl in range(1, 8):
                    m = lm[str(sl)]
                    self._mark_pushed(sl, spec_json(resolve_spec(self.cfg, m["cat"], m["action"])), lay)
                self.set_status(f"Layer {lay + 1} on the pad reset to factory defaults")
            self.bg(lambda: self.dev.request({"cmd": "reset_keys", **({"layer": lay} if self._layers_supported() else {})}), done, "Reset failed")
        else:
            self.set_status(f"Layer {lay + 1} reset in the app - it reaches the pad with the next upload")

    # ---- layers: which one the editor shows, which one the physical pad is on
    def set_edit_layer(self, n):
        if not 0 <= n < LAYERS:
            return
        self.edit_layer = n
        self.cfg["edit_layer"] = n
        self.cfg["map"], self.cfg["pushed"] = self.cfg["layers"][n], self.cfg["pushed_layers"][n]       # the editors work on cfg["map"]
        if self.pad.layer != n:
            self.pad.set_layer(n, notify=False)
        if hasattr(self, "target_layer_lbl"):
            self.target_layer_lbl.configure(text=f"on layer {n + 1} ({LAYER_NAMES[n].split('  ')[-1]}) - change it on the Pad page")
        if hasattr(self, "layer_seg"):
            self.layer_seg.set(f"Layer {n + 1}")
            self.refresh_action_lists()
            self.padview.refresh()
            self.recompute_pending()

    def _layer_seg(self, label):
        self.set_edit_layer(int(label.split()[-1]) - 1)

    def show_layer_on_pad(self):
        if not self.dev.connected:
            return self.set_status("Not connected", error=True)
        self.bg(lambda: self.dev.request({"cmd": "layer", "val": self.edit_layer}), None, "Layer switch failed")

    def _pad_layer_changed(self, n):
        self.pad_layer = n
        if hasattr(self, "pad_layer_pill"):
            self.pad_layer_pill.set(f"pad is on layer {n + 1}", (ACCENT, PINK, WARN)[n % 3])

    # ---- events coming from the pad (reader thread): layer changes, host actions, usage counters
    def _on_pad_event(self, m):
        self.devtab.on_event(m)
        if self.hw_listener:
            self.hw_listener(m)
        evt = m.get("evt")
        if evt == "layer" and isinstance(m.get("n"), int):
            self.post(lambda n=m["n"]: self._pad_layer_changed(n))
        elif evt == "host" and self.hw_listener:
            self.post(lambda: self.vp_log("host action ignored while the hardware test is running"))
        elif evt == "host":
            self.host_q.put((("host", {"op": m.get("op"), "arg": m.get("arg", "")}), "pad action", f"{m.get('op')}", False))
        elif evt == "reminder":                                            # the pad's reminder fired: also tell the user on the computer
            self.post(lambda t=str(m.get("text", "")): self.notify("Reminder", t, "ok"))
        elif evt == "key" and m.get("v") == 1 and 1 <= m.get("k", 0) <= 5:
            self.post(lambda k=m["k"]: self._count_use(f"K{k}"))
        elif evt == "enc" and m.get("d"):
            self.post(lambda d=m["d"]: self._count_use("dial+" if d > 0 else "dial-"))

    def _count_use(self, key):
        u = self.cfg["usage"]
        u[key] = int(u.get(key, 0)) + 1
        self._usage_dirty = True

    # ---- host actions (open URL / app / command / type clipboard) - only ones that are part of YOUR configuration
    def _allowed_host(self):
        allowed = hostactions.collect_allowed(list(self.cfg["layers"]) + [self.cfg["gestures"]], self.cfg["custom"], lambda c, a: resolve_spec(self.host_cfg(), c, a))
        for e in self.cfg["schedules"]:                           # a scheduled rule is part of the user's own configuration too
            if e["do"]["kind"] == "host":
                allowed.add((e["do"]["op"], e["do"].get("arg", "")))
        return allowed

    # ---- local API for scripts / the command line (desk_lib/bridge.py)
    def api_start(self):
        self.api_stop()
        a = self.cfg["api"]
        try:
            self.api = bridge.Bridge(self._api_handle, token=a["token"], port=int(a["port"]), status=self._api_status)
            self.api_error = ""
        except OSError as e:
            self.api, self.api_error = None, f"port {a['port']} is not available ({e.strerror or e})"
        a["on"] = self.api is not None
        return self.api is not None

    def api_stop(self):
        if getattr(self, "api", None):
            try:
                self.api.close()
            except Exception:                                     # noqa: BLE001
                pass
        self.api = None

    def _api_status(self):
        i = self.dev.info if self.dev.connected else {}
        return {"app": APP_VERSION, "connected": self.dev.connected, "firmware": i.get("fw", ""), "layer": self.pad_layer, "mode": i.get("mode")}

    def _api_handle(self, r):
        """Runs on the API server's thread."""
        k = r["kind"]
        if k == "notify":
            self.post(lambda: self.notify("Desk Companion", r["text"], "ok"))
            return {}
        if k == "badge":
            if not self.badges:
                raise bridge.ApiError(503, "the badge service is not running")
            self.badges.set(r["name"], r["n"])
            return {}
        if k == "card":
            info = self.cfg["info"]
            info.update(custom=any(r[x] for x in ("label", "title", "a", "b")), c_label=r["label"], c_t=r["title"], c_a=r["a"], c_b=r["b"])
            return {}
        if not self.dev.connected:
            raise bridge.ApiError(409, "the pad is not connected")
        try:
            if k == "layer":
                if not self._layers_supported():
                    raise bridge.ApiError(409, "the pad's firmware has no layers")
                self.dev.request({"cmd": "layer", "val": r["val"]})
            elif k in ("mode", "brightness"):
                self.dev.request({"cmd": k, "val": r["val"]})
            elif k == "led":
                self.dev.request({"cmd": "led", **{x: r[x] for x in ("mode", "hex") if x in r}})
            elif k == "press":
                self.dev.request({"cmd": "input", "k": r["key"]} if r["key"] <= 5 else {"cmd": "input", "turn": 1 if r["key"] == 6 else -1})
        except DeviceError as e:
            raise bridge.ApiError(502, str(e)) from None
        return {}

    # ---- scheduled actions
    def _schedule_loop(self):
        while not self.closing:
            time.sleep(5)
            if self.closing:
                return
            try:
                due = self.scheduler.tick(datetime.now())
            except Exception:                                     # noqa: BLE001
                traceback.print_exc()
                continue
            for e in due:
                self.run_schedule(e)
            if any(e.pop("_retired", None) for e in self.cfg["schedules"]):
                self.post(lambda: (self.save_cfg(), self.automation.refresh()))

    def run_schedule(self, entry, manual=False):
        def work():
            def request(msg):
                if not self.dev.connected:
                    raise RuntimeError("the pad is not connected")
                self.dev.request(msg)
            return automation.run_entry(entry, request, self.hostact.run, lambda t: self.post(lambda: self.notify("Reminder", t, "ok")))
        label = entry.get("name") or "Scheduled action"
        self.bg(work, lambda desc: self.set_status(f"{label}: {desc}"), f"{label} failed")

    def run_host(self, op, arg=""):
        """Run one of the user's own host actions from a button in the app (no whitelist needed: the user pressed it). Raises RuntimeError with the reason."""
        ok, msg = self.hostact.run_trusted(op, arg)
        if not ok:
            raise RuntimeError(msg)
        return msg

    def _host_cb(self, spec):
        ok, msg = self.hostact.run(spec.get("op"), spec.get("arg", ""))
        if not ok:
            raise RuntimeError(msg)
        if spec.get("op") in hostactions.NEW_OPS:                       # OS / network actions say what they did
            self.post(lambda m=msg: self.set_status(m))

    def _clipboard_text(self):
        ev, box = threading.Event(), {}

        def grab():
            try:
                box["t"] = self.clipboard_get()
            except tk.TclError:
                box["t"] = ""
            ev.set()
        self.post(grab)
        ev.wait(2.0)
        return (box.get("t") or "")[:2000]

    def _type_text(self, txt):
        if self.host.impl is None:
            raise RuntimeError("typing needs the key sender (pip install pynput)")
        self.host.impl.run(("text", txt))

    def _type_clipboard(self):
        txt = self._clipboard_text()
        if txt:
            self._type_text(txt)

    def _clip_loop(self):
        """Clipboard history (opt-in): remember the last copied texts, in memory only."""
        while not self.closing:
            time.sleep(1.5)
            if not self.cfg.get("cliphist_on"):
                if self.cliphist.items:
                    self.cliphist.clear()
                continue
            try:
                self.cliphist.add(self._clipboard_text())
            except Exception:                                  # noqa: BLE001
                pass

    # ---- macro scripts (desk_lib/scripting.py)
    class _ScriptBackend(scripting.Backend):
        def __init__(self, app):
            self.app = app

        def _impl(self):
            if self.app.host.impl is None:
                raise scripting.ScriptError("the key sender is not available (pip install pynput)")
            return self.app.host.impl

        def key(self, keys): self._impl().run(("combo", list(keys)))
        def text(self, s): self._impl().run(("text", s))
        def click(self, how): self._impl().run(("mouse", {"btn": "left", "act": "double"} if how == "double" else {"btn": how, "act": "click"}))
        def scroll(self, n): self._impl().run(("mouse", {"wheel": n}))
        def media(self, name): self._impl().run(("media", name))
        def host(self, op, arg):
            ok, msg = self.app.hostact.run_trusted(op, arg)
            if not ok:
                raise scripting.ScriptError(msg)

        def wait(self, ms):
            if self.app.script_stop.wait(ms / 1000.0):
                raise scripting.Stop

        def window(self): return self.app.active_win.get()
        def clipboard(self): return self.app._clipboard_text()

    def script_ctx(self):
        return {"counter": self._next_counter}

    def run_script(self, name):
        """Runs a saved script (called on the host worker thread). Returns a message; raises on problems."""
        src = self.cfg["scripts"].get(name)
        if src is None:
            raise RuntimeError(f"there is no script called '{name}'")
        if not self._script_lock.acquire(blocking=False):
            raise RuntimeError("another script is still running (Scripts page -> Stop)")
        self.script_stop.clear()
        try:
            n = scripting.run(src, self._ScriptBackend(self), lookup=self.cfg["scripts"].get, ctx=self.script_ctx(), shell_ok=bool(self.cfg.get("allow_shell")))
        except scripting.ScriptError as e:
            raise RuntimeError(f"script '{name}': {e}") from None
        finally:
            self._script_lock.release()
        return f"script '{name}' ran {n} commands"

    def _next_counter(self, name):
        c = self.cfg["counters"]
        c[name] = int(c.get(name, 0)) + 1
        save_config(self.cfg)
        return c[name]

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

    def toggle_simulate(self):
        if self.dev.connected:
            was_sim = self.dev.port == SIM_PORT
            self.dev.disconnect()
            self._on_disconnected()
            if was_sim:
                return
        self.auto_var.set(False)
        self.auto_flag = False
        self.set_status("Starting simulated pad...")
        self.bg(lambda: self._connect_locked(SIM_PORT), self._on_connected, "Simulate failed")

    def _connect_locked(self, port):
        with self.connect_lock:
            return self.dev.connect(port)

    def _on_connected(self, info):
        simulated = self.dev.port == SIM_PORT
        label = "Simulated pad (no hardware)" if simulated else self.dev.port
        self.conn_lbl.configure(text=f"Connected on {label} - firmware {info.get('fw', '?')}, "
                                     f"free flash {info.get('fs_free', 0) // 1024} KB", text_color=OK)
        self.conn_btn.configure(text="Disconnect")
        self.conn_pill.set("Simulated pad" if simulated else "Pad connected", OK)
        self.sim_btn.configure(text="Stop simulating" if simulated else "Simulate pad (no hardware)")
        self.set_status("Simulated pad connected - try remapping keys, macros or a GIF upload" if simulated
                         else "Pad connected")
        self._was_connected = True
        self.notify("DeskCompanion connected", f"{label}  -  firmware {info.get('fw', '?')}", "ok")
        self._dev_note(f"connected on {label}: {info}")
        self.devtab.refresh_state()
        self.devtab.refresh_ports()
        self.bright.set(info.get("bright", 200))
        self.mode_var.set(MODE_CHOICES[max(0, min(NUM_MODES - 1, info.get("mode", 1) - 1))])
        m, b = max(1, min(NUM_MODES, int(info.get("mode", 1)))), int(info.get("bright", 200))
        self.pad.set_mode(m, notify=False)                     # the virtual screen adopts what the pad is showing
        self.pad.set_brightness(b, notify=False)
        self.vp_mode_var.set(MODE_CHOICES[m - 1])
        self.vp_bright.set(b)
        self.cfg["pushed_mode"], self.cfg["pushed_bright"] = m, b
        self.recompute_pending()
        self._pad_layer_changed(int(info.get("layer", 0)))
        self._info_sent = (None, 0.0)
        self._profile_state.update(win=None, layer=None)
        self.refresh_fw_status()
        self.refresh_health()
        self.refresh_pad_gifs()
        self.behaviour.on_connected()
        self.screens_card.on_connected()
        if not self.cfg.get("wizard_done") and not simulated and not getattr(self, "_wizard_offered", False):
            self._wizard_offered = True
            self.after(800, self.open_wizard)

        def work():
            if info.get("core_only"):                              # the CoreBringup sketch only knows the diagnostic commands
                return
            self.dev.request({"cmd": "os", "val": self.cfg["os"]})
            self.dev.request(time_msg())
            self._push_layout()
        self.bg(work, None, "Initial sync failed")
        if info.get("core_only"):
            self.set_status("Connected to the CoreBringup diagnostic sketch - LED, ports and GPIO tests work. Flash the full firmware (Device -> Update) for the rest.")

    def _on_disconnected(self):
        self.conn_lbl.configure(text="Not connected", text_color=WARN)
        self.conn_pill.set("Not connected", WARN)
        self.refresh_fw_status()
        self.behaviour.refresh()
        self.screens_card.refresh()
        if hasattr(self, "pad_gif_box"):
            self.refresh_pad_gifs()
        self.conn_btn.configure(text="Connect")
        self.sim_btn.configure(text="Simulate pad (no hardware)")
        self.set_status("Pad disconnected - waiting for it to reappear" if self.auto_flag else "Disconnected")
        if hasattr(self, "devtab"):
            self.devtab.refresh_state()
            self.devtab.refresh_ports()
            self._dev_note("disconnected")
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
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        sc = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        sc.grid(row=0, column=0, sticky="nsew")
        top = ctk.CTkFrame(sc)
        top.pack(fill="x", pady=5, padx=2)
        ctk.CTkLabel(top, text="Target key:").pack(side="left", padx=(16, 6), pady=12)
        self.target_var = tk.StringVar(value=SLOT_LABELS[1])
        ctk.CTkOptionMenu(top, values=list(SLOT_LABELS.values()), variable=self.target_var, width=180).pack(side="left")
        self.target_layer_lbl = ui.muted(top, "on the layer selected on the Pad page")
        self.target_layer_lbl.pack(side="left", padx=14)
        ui.muted(top, "Text is typed as US-layout ASCII (see README).").pack(side="right", padx=16)

        cb = ctk.CTkFrame(sc)
        cb.pack(fill="x", pady=5, padx=2)
        self._title(cb, "Key combination (up to 4 modifiers + main key)")
        self.mod_vars = []
        for i in range(4):
            v = tk.StringVar(value="CTRL" if i == 0 else "-")
            ctk.CTkOptionMenu(cb, values=MODIFIERS, variable=v, width=105).grid(row=1, column=i, padx=(16 if i == 0 else 4, 4), pady=(6, 14))
            self.mod_vars.append(v)
        ctk.CTkLabel(cb, text="+").grid(row=1, column=4)
        self.key_var = tk.StringVar(value="c")
        ctk.CTkComboBox(cb, values=KEY_CHOICES, variable=self.key_var, width=110).grid(row=1, column=5, padx=6)
        ctk.CTkButton(cb, text="Assign to key", width=110, command=self.assign_combo).grid(row=1, column=6, padx=4)
        ui.secondary_button(cb, "Add to sequence", self.seq_add_combo, width=120).grid(row=1, column=7, padx=4)
        ctk.CTkButton(cb, text="Test", width=70, fg_color="#2f7d4f", command=self.test_combo).grid(row=1, column=8, padx=4)

        tx = ctk.CTkFrame(sc)
        tx.pack(fill="x", pady=5, padx=2)
        self._title(tx, "Text snippet auto-typer")
        self.text_box = ctk.CTkTextbox(tx, height=64, width=520)
        self.text_box.grid(row=1, column=0, padx=(16, 8), pady=(4, 14), rowspan=2)
        self.enter_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(tx, text="Press Enter afterwards", variable=self.enter_var).grid(row=1, column=1, padx=8, sticky="w")
        bt = ctk.CTkFrame(tx, fg_color="transparent", border_width=0)
        bt.grid(row=2, column=1, padx=8, sticky="w")
        ctk.CTkButton(bt, text="Assign to key", width=110, command=self.assign_text).pack(side="left", padx=(0, 6))
        ui.secondary_button(bt, "Add to sequence", self.seq_add_text, width=120).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bt, text="Test", width=70, fg_color="#2f7d4f", command=self.test_text).pack(side="left")

        ac = ctk.CTkFrame(sc)
        ac.pack(fill="x", pady=5, padx=2)
        self._title(ac, "Computer, mouse and layer actions")
        ui.muted(ac, "Open a website or program, run a command, type the clipboard, click or scroll with the pad's USB mouse, or switch the pad's layer. "
                 "The pad asks this app to open things, so the app must be running for those.", wraplength=900).grid(row=1, column=0, columnspan=8, sticky="w", padx=16)
        self.act_kind = tk.StringVar(value=ACTION_KINDS[0][0])
        ctk.CTkOptionMenu(ac, values=[k[0] for k in ACTION_KINDS], variable=self.act_kind, width=200, command=self._act_kind_changed).grid(row=2, column=0, padx=(16, 6), pady=(8, 14))
        self.act_arg = ctk.CTkEntry(ac, width=330, placeholder_text=ACTION_KINDS[0][2])
        self.act_arg.grid(row=2, column=1, padx=6)
        self.act_transform = tk.StringVar(value=textops.TRANSFORMS["upper"][0])
        self.act_transform_menu = ctk.CTkOptionMenu(ac, values=[v[0] for v in textops.TRANSFORMS.values()], variable=self.act_transform, width=330)
        ctk.CTkButton(ac, text="Assign to key", width=110, command=self.assign_action).grid(row=2, column=2, padx=4)
        ui.secondary_button(ac, "Add to sequence", self.seq_add_action, width=120).grid(row=2, column=3, padx=4)
        ctk.CTkButton(ac, text="Test", width=70, fg_color="#2f7d4f", command=self.test_action).grid(row=2, column=4, padx=4)

        sq = ctk.CTkFrame(sc)
        sq.pack(fill="x", pady=5, padx=2)
        self._title(sq, "Macro sequence (with delays)")
        self.seq_list = tk.Listbox(sq, height=8, width=70, bg=ui.DARK["card2"], fg=ui.DARK["text"], selectbackground="#0e7490",
                                   borderwidth=0, highlightthickness=0, activestyle="none", font=("TkDefaultFont", 11))
        self.seq_list.grid(row=1, column=0, rowspan=4, padx=(16, 8), pady=4, sticky="nsew")
        for r, (t, f) in enumerate((("Move up", lambda: self.seq_move(-1)), ("Move down", lambda: self.seq_move(1)),
                                    ("Remove", self.seq_remove), ("Clear", self.seq_clear)), start=1):
            ui.secondary_button(sq, t, f, width=100).grid(row=r, column=1, padx=4, pady=2, sticky="w")
        adv = ctk.CTkFrame(sq, fg_color="transparent", border_width=0)
        adv.grid(row=5, column=0, columnspan=3, sticky="w", padx=12, pady=4)
        self.seq_delay_var = tk.StringVar(value="200")
        ctk.CTkEntry(adv, textvariable=self.seq_delay_var, width=70).pack(side="left", padx=4)
        ctk.CTkButton(adv, text="Add delay (ms)", width=120, command=self.seq_add_delay).pack(side="left", padx=(0, 16))
        self.media_var = tk.StringVar(value="PLAY_PAUSE")
        ctk.CTkOptionMenu(adv, values=MEDIA_CHOICES, variable=self.media_var, width=140).pack(side="left", padx=4)
        ctk.CTkButton(adv, text="Add media key", width=120, command=self.seq_add_media).pack(side="left", padx=(0, 16))
        self.rec_btn = ui.secondary_button(adv, "Record keystrokes", self.rec_toggle, width=150)
        self.rec_btn.pack(side="left")
        fin = ctk.CTkFrame(sq, fg_color="transparent", border_width=0)
        fin.grid(row=6, column=0, columnspan=3, sticky="w", padx=12, pady=(4, 14))
        self.name_var = tk.StringVar(value="My macro")
        ctk.CTkEntry(fin, textvariable=self.name_var, width=200).pack(side="left", padx=4)
        ctk.CTkButton(fin, text="Assign sequence to key", command=self.assign_seq).pack(side="left", padx=6)
        ui.secondary_button(fin, "Save to library only", self.save_seq).pack(side="left", padx=(0, 6))
        ctk.CTkButton(fin, text="Test sequence", fg_color="#2f7d4f", command=self.test_seq).pack(side="left")
        self._rec = None

    # ---- computer / mouse / layer actions
    def _act_kind_changed(self, kind):
        placeholder = next(k[2] for k in ACTION_KINDS if k[0] == kind)
        is_clip = next(k[1] for k in ACTION_KINDS if k[0] == kind) == "clip"
        if is_clip:
            self.act_arg.grid_remove()
            self.act_transform_menu.grid(row=2, column=1, padx=6)
        else:
            self.act_transform_menu.grid_remove()
            self.act_arg.grid(row=2, column=1, padx=6)
        self.act_arg.delete(0, "end")
        self.act_arg.configure(placeholder_text=placeholder, state="normal" if next(k[3] for k in ACTION_KINDS if k[0] == kind) else "disabled")

    def _action_spec(self):
        kind = self.act_kind.get()
        arg = self.act_arg.get().strip()
        k = next(x for x in ACTION_KINDS if x[0] == kind)
        if k[3] and not arg:
            raise ValueError(f"'{kind}': {k[2]}")
        op = k[1]
        if op == "url":
            if not re.match(r"^(https?://|mailto:)", arg, re.I):
                arg = "https://" + arg
            return ("host", {"op": "url", "arg": arg})
        if op in ("app", "file", "notify"):
            return ("host", {"op": op, "arg": arg})
        if op in ("snippet", "clip") and self.dev.connected and not self._pad_cap("hostx"):
            raise ValueError("Snippets and clipboard transforms need firmware 1.3 on the pad - update it (Device -> Firmware).")
        if op in hostactions.NEW_OPS:
            if self.dev.connected and not self._pad_cap("hostx2"):
                raise ValueError("This action needs firmware 1.5 on the pad - update it (Device -> Firmware).")
            return ("host", {"op": op, "arg": arg or "default"})
        if op == "snippet":
            return ("host", {"op": "snippet", "arg": arg})
        if op == "clip":
            key = next(k for k, v in textops.TRANSFORMS.items() if v[0] == self.act_transform.get())
            return ("host", {"op": "clip", "arg": key})
        if op == "shell":
            if not self.cfg.get("allow_shell"):
                raise ValueError("Shell commands are switched off. Turn on 'Allow the pad to run shell commands' on the Device page first.")
            return ("host", {"op": "shell", "arg": arg})
        if op == "clipboard":
            return ("host", {"op": "clipboard"})
        if op == "click":
            b = (arg or "left").lower()
            if b not in ("left", "right", "middle", "back", "forward"):
                raise ValueError("button must be left, right, middle, back or forward")
            return ("mouse", {"btn": b, "act": "double" if kind.endswith("double click") else "click"})
        if op == "scroll":
            try:
                n = int(arg)
            except ValueError:
                raise ValueError("scroll amount must be a number: positive = up, negative = down") from None
            if not -20 <= n <= 20 or n == 0:
                raise ValueError("scroll amount must be between -20 and 20 (not 0)")
            return ("mouse", {"wheel": n})
        if op == "layer":
            v = arg.lower() or "next"
            if v in ("next", "prev"):
                return ("layer", v)
            if v in ("1", "2", "3"):
                return ("layer", int(v) - 1)
            raise ValueError("layer must be 1, 2, 3, next or prev")
        raise ValueError("unknown action")

    def _action_step(self, spec):
        t, v = spec
        return {"host": {"host": v}, "mouse": {"mouse": v}, "layer": {"layer": v}}[t]

    def assign_action(self):
        def go():
            spec = self._action_spec()
            self.assign_spec(spec, describe_spec(spec)[:40])
        self._guard(go)

    def seq_add_action(self):
        self._guard(lambda: (self.macro_steps.append(self._action_step(self._action_spec())), self._seq_refresh()))

    def test_action(self):
        self._guard(lambda: self.test_spec(self._action_spec(), "Action"))

    # ---- keystroke recorder (needs pynput)
    def rec_toggle(self, listener_factory=None):
        if self._rec:
            return self._rec_stop()
        if listener_factory is None:
            try:
                from pynput import keyboard as kb
            except Exception:                                  # noqa: BLE001
                return self.set_status("Recording keystrokes needs pynput:  pip install pynput", error=True)
            if not messagebox.askokcancel("Record keystrokes", "While recording, everything you type in ANY program is captured into the sequence "
                                          "(at most 60 seconds, 64 steps). Do not type passwords.\n\nPress 'Stop recording' here when you are done."):
                return

            def listener_factory(on_press, on_release):
                def nm(k):
                    return getattr(k, "char", None) or getattr(k, "name", "") or ""
                return kb.Listener(on_press=lambda k: on_press(nm(k)), on_release=lambda k: on_release(nm(k)))
        self._recorder = recorder.MacroRecorder()
        self._rec = listener_factory(lambda n: self._recorder.key_down(n, time.monotonic()), lambda n: self._recorder.key_up(n, time.monotonic()))
        self._rec.start()
        self.rec_btn.configure(text="Stop recording", fg_color=ui.ERR_FILL)
        self.set_status("Recording keystrokes ... press 'Stop recording' when done")
        self._rec_job = self.after(60000, lambda: self._rec and self._rec_stop())

    def _rec_stop(self):
        rec, self._rec = self._rec, None
        try:
            rec.stop()
        except Exception:                                      # noqa: BLE001
            pass
        try:
            self.after_cancel(self._rec_job)
        except (AttributeError, ValueError, tk.TclError):
            pass
        steps = self._recorder.finish()
        room = 64 - len(self.macro_steps)
        self.macro_steps.extend(steps[:max(0, room)])
        self._seq_refresh()
        self.rec_btn.configure(text="Record keystrokes", fg_color=ui.CARD3)
        self.set_status(f"Recorded {len(steps)} step(s)" + (" - stopped at the 64-step limit" if self._recorder.truncated or len(steps) > room else ""))

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
        for st in self.macro_steps:
            if "combo" in st:
                d = "COMBO   " + "+".join(st["combo"])
            elif "text" in st:
                d = "TEXT    " + repr(st["text"])[:48]
            elif "delay" in st:
                d = f"DELAY   {st['delay']} ms"
            elif "media" in st:
                d = "MEDIA   " + st["media"]
            elif "host" in st:
                d = "COMPUTER " + describe_spec(("host", st["host"]))
            elif "mouse" in st:
                d = "MOUSE   " + describe_spec(("mouse", st["mouse"]))
            else:
                d = "LAYER   " + describe_spec(("layer", st["layer"]))
            self.seq_list.insert("end", d)

    def seq_add_combo(self):
        self._guard(lambda: (self.macro_steps.append({"combo": self._combo_keys()}), self._seq_refresh()))

    def seq_add_text(self):
        self._guard(lambda: (self.macro_steps.append({"text": self._text_value()}), self._seq_refresh()))

    def seq_add_delay(self):
        def go():
            try:
                ms = int(self.seq_delay_var.get())
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

    # ---- GIF library (built-in / my GIFs / online) + upload
    GIF_COLS, GIF_TILE = 6, 56

    def _build_gif(self, tab):
        left = ctk.CTkFrame(tab)
        left.pack(side="left", fill="y", padx=6, pady=6)
        self.gif_canvas = tk.Canvas(left, width=LCD, height=LCD, bg="#151515", highlightthickness=0)
        self.gif_canvas.pack(padx=14, pady=14)
        self.gif_canvas.create_oval(0, 0, LCD - 1, LCD - 1, outline="#3b82f6", width=2)
        self._pv_item = self.gif_canvas.create_image(0, 0, anchor="nw")
        self.gif_canvas.tag_lower(self._pv_item)
        ctk.CTkLabel(left, text="Round display preview", text_color=MUTED).pack(pady=(0, 6))

        right = ctk.CTkFrame(tab, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(right, text="GIF library", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(8, 2))
        self.gif_view = tk.StringVar(value="Built-in")
        ctk.CTkSegmentedButton(right, values=["Built-in", "My GIFs", "Online"], variable=self.gif_view,
                               command=self._gif_show_view).grid(row=1, column=0, sticky="w", padx=12, pady=(2, 6))
        body = ctk.CTkFrame(right)
        body.grid(row=2, column=0, sticky="nsew", padx=6)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self._gif_views = {}
        self._gif_thumbs = []                                    # keeps CTkImage objects alive
        for name, builder in (("Built-in", self._build_gif_builtin), ("My GIFs", self._build_gif_mine),
                              ("Online", self._build_gif_online)):
            f = ctk.CTkFrame(body, fg_color="transparent")
            f.grid(row=0, column=0, sticky="nsew")
            f.grid_columnconfigure(0, weight=1)
            builder(f)
            self._gif_views[name] = f
        self._gif_show_view("Built-in")

        ctrl = ctk.CTkFrame(right)
        ctrl.grid(row=3, column=0, sticky="ew", padx=6, pady=(8, 0))
        ctk.CTkButton(ctrl, text="Select GIF file...", command=self.choose_gif).grid(row=0, column=0, padx=10, pady=(10, 4), sticky="w")
        self.gif_name = ctk.CTkLabel(ctrl, text="nothing selected yet - pick a tile above or your own file", anchor="w")
        self.gif_name.grid(row=0, column=1, columnspan=3, sticky="w", padx=8, pady=(10, 4))
        self.keep_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(ctrl, text="Keep a copy of files / downloads in My GIFs", variable=self.keep_var).grid(
            row=1, column=0, columnspan=2, padx=10, pady=4, sticky="w")
        self.dither_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(ctrl, text="Dithering (smoother gradients, larger file)", variable=self.dither_var).grid(
            row=1, column=2, columnspan=2, padx=10, pady=4, sticky="w")
        self.gif_info = ctk.CTkLabel(ctrl, text="Frames are centre-cropped, resized to 240x240 and masked to a circle.",
                                     justify="left", wraplength=620, anchor="w")
        self.gif_info.grid(row=2, column=0, columnspan=4, padx=10, pady=4, sticky="w")
        self.gif_bar = ctk.CTkProgressBar(ctrl, width=440)
        self.gif_bar.grid(row=3, column=0, columnspan=3, padx=10, pady=6, sticky="w")
        self.gif_bar.set(0)
        self.upload_btn = ctk.CTkButton(ctrl, text="Upload to pad", state="disabled", command=self.upload_gif)
        self.upload_btn.grid(row=4, column=0, padx=10, pady=(4, 6), sticky="w")
        self.gif_slot_var = tk.StringVar(value="Slot 1")
        ctk.CTkOptionMenu(ctrl, values=["Slot 1", "Slot 2", "Slot 3", "Slot 4"], variable=self.gif_slot_var, width=100,
                          command=lambda _v: None).grid(row=4, column=1, sticky="w", padx=8, pady=(4, 6))
        ctk.CTkButton(ctrl, text="Clear this slot on the pad", fg_color="#555", command=self.delete_gif).grid(row=4, column=2, sticky="w", padx=8, pady=(4, 6))
        sl = ctk.CTkFrame(left, fg_color="transparent", border_width=0)
        sl.pack(fill="x", padx=12, pady=(4, 12))
        top = ctk.CTkFrame(sl, fg_color="transparent", border_width=0)
        top.pack(fill="x")
        ui.heading(top, "On the pad", 14).pack(side="left")
        ui.secondary_button(top, "Refresh", self.refresh_pad_gifs, width=70).pack(side="right")
        rot = ctk.CTkFrame(sl, fg_color="transparent", border_width=0)
        rot.pack(fill="x", pady=(6, 2))
        ctk.CTkLabel(rot, text="Rotate every").pack(side="left", padx=(0, 6))
        self.gif_rot_var = tk.StringVar(value="off")
        ctk.CTkOptionMenu(rot, values=[c[0] for c in self.ROT_CHOICES], variable=self.gif_rot_var, width=120, command=self._gif_rot_changed).pack(side="left")
        self.pad_gif_box = ctk.CTkFrame(sl, fg_color="transparent", border_width=0)
        self.pad_gif_box.pack(fill="x", pady=(6, 0))
        self.pad_gifs, self.pad_gif_free = {}, None

    # -- tiles
    def _gif_tile(self, parent, idx, label, pil, command, popup=None):
        cimg = None
        if pil is not None:
            cimg = ctk.CTkImage(light_image=pil, dark_image=pil, size=(self.GIF_TILE, self.GIF_TILE))
            self._gif_thumbs.append(cimg)
        b = ctk.CTkButton(parent, text=(label or "")[:14], image=cimg, compound="top", width=100, height=92,
                          fg_color=CARD2, hover_color=CARD3, text_color=TEXT, command=command)
        b.grid(row=idx // self.GIF_COLS, column=idx % self.GIF_COLS, padx=4, pady=4)
        if popup:
            for seq in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
                b.bind(seq, popup, add="+")
        return b

    def _gif_show_view(self, name):
        for n, f in self._gif_views.items():
            if n == name:
                f.grid()
            else:
                f.grid_remove()
        if name == "My GIFs":
            self.refresh_my_gifs()
        elif name == "Online" and not self._on_loaded and self.on_key.get().strip():
            self.online_run("")

    # -- Built-in
    def _build_gif_builtin(self, f):
        ctk.CTkLabel(f, text="Generated animations - nothing to download. Click one to preview it; then 'Upload to pad'.",
                     text_color=MUTED, anchor="w").grid(row=0, column=0, sticky="w", padx=8, pady=(6, 0))
        sc = ctk.CTkScrollableFrame(f)
        sc.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        f.grid_rowconfigure(1, weight=1)
        for idx, name in enumerate(GIF_PRESETS):
            self._gif_tile(sc, idx, name, preset_thumb(name, self.GIF_TILE), lambda n=name: self.use_preset(n))

    # -- My GIFs
    def _build_gif_mine(self, f):
        bar = ctk.CTkFrame(f, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(6, 0))
        ctk.CTkButton(bar, text="Add GIF...", width=100, command=self.add_my_gif).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="Open folder", width=100, fg_color="#555", command=self.open_gif_folder).pack(side="left", padx=4)
        self.mine_info = ctk.CTkLabel(bar, text="", text_color=MUTED, anchor="w")
        self.mine_info.pack(side="left", padx=10)
        self.mine_sc = ctk.CTkScrollableFrame(f)
        self.mine_sc.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        f.grid_rowconfigure(1, weight=1)
        self._mine_gen = 0
        self._mine_menu = tk.Menu(self, tearoff=0)

    def refresh_my_gifs(self):
        self._mine_gen += 1
        gen = self._mine_gen
        paths = lib_list()
        for w in self.mine_sc.winfo_children():
            w.destroy()
        self.mine_info.configure(text=f"{len(paths)} GIF(s) in {gif_lib_dir()}  -  right-click a tile to delete it")
        if not paths:
            ctk.CTkLabel(self.mine_sc, text="Empty. Use 'Add GIF...', 'Select GIF file...' or save something from Online.",
                         text_color=MUTED).grid(row=0, column=0, padx=10, pady=20)
            return

        def work():
            out = []
            for p in paths:
                try:
                    out.append((p, gif_file_thumb(p, self.GIF_TILE)))
                except Exception:                                  # unreadable file: still listed, no picture
                    out.append((p, None))
            return out

        def show(items):
            if gen != self._mine_gen:
                return
            for w in self.mine_sc.winfo_children():
                w.destroy()
            for idx, (p, th) in enumerate(items):
                self._gif_tile(self.mine_sc, idx, p.stem, th, lambda p=p: self.use_gif_file(str(p), p.name, save=False),
                               popup=lambda e, p=p: self._mine_popup(e, p))
        self.bg(work, show, "Could not read the GIF folder")

    def _mine_popup(self, event, path):
        m = self._mine_menu
        m.delete(0, "end")
        m.add_command(label=f"Use '{path.stem[:30]}'", command=lambda: self.use_gif_file(str(path), path.name, save=False))
        m.add_command(label="Delete from library", command=lambda: self.delete_my_gif(path))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def delete_my_gif(self, path):
        if messagebox.askyesno("Delete GIF", f"Remove '{path.name}' from your library?\n(The copy on the pad is not touched.)"):
            lib_delete(path)
            self.refresh_my_gifs()

    def add_my_gif(self):
        paths = filedialog.askopenfilenames(filetypes=[("GIF images", "*.gif"), ("All files", "*.*")])
        n = 0
        for p in paths:
            try:
                with open(p, "rb") as fh:
                    if fh.read(3) != b"GIF":
                        raise ValueError("not a GIF file")
                lib_add(p)
                n += 1
            except (OSError, ValueError) as e:
                self.set_status(f"Could not add {os.path.basename(p)}: {e}", error=True)
        if n:
            self.set_status(f"Added {n} GIF(s) to My GIFs")
        self.refresh_my_gifs()

    def open_gif_folder(self):
        d = gif_lib_dir()
        try:
            if platform.system() == "Windows":
                os.startfile(str(d))                                # noqa: S606 - local folder only
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(d)])
            else:
                subprocess.Popen(["xdg-open", str(d)])
        except Exception as e:                                     # noqa: BLE001
            self.set_status(f"Could not open the folder ({e}): {d}", error=True)

    # -- Online
    def _build_gif_online(self, f):
        self._on_gen, self._on_loaded = 0, False
        keys = self.cfg.setdefault("online_keys", {})
        top = ctk.CTkFrame(f, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=(6, 0))
        self.on_provider = tk.StringVar(value=self.cfg.get("online_provider") if self.cfg.get("online_provider") in ONLINE_PROVIDERS else "Tenor")
        ctk.CTkOptionMenu(top, values=list(ONLINE_PROVIDERS), variable=self.on_provider, width=90,
                          command=self._online_provider_changed).pack(side="left", padx=4)
        self.on_key = ctk.CTkEntry(top, width=230, show="*", placeholder_text="API key (free)")
        self.on_key.pack(side="left", padx=4)
        self.on_key.insert(0, keys.get(self.on_provider.get(), ""))
        ctk.CTkButton(top, text="Get a free key", width=110, fg_color="#555", command=self.online_key_help).pack(side="left", padx=4)
        row2 = ctk.CTkFrame(f, fg_color="transparent")
        row2.grid(row=1, column=0, sticky="ew", padx=4, pady=2)
        self.on_query = ctk.CTkEntry(row2, width=300, placeholder_text="search GIFs (empty = trending)")
        self.on_query.pack(side="left", padx=4)
        self.on_query.bind("<Return>", lambda _e: self.online_run(self.on_query.get()))
        ctk.CTkButton(row2, text="Search", width=80, command=lambda: self.online_run(self.on_query.get())).pack(side="left", padx=4)
        ctk.CTkButton(row2, text="Trending", width=80, fg_color="#555", command=lambda: self.online_run("")).pack(side="left", padx=4)
        self.on_status = ctk.CTkLabel(row2, text="", text_color=MUTED, anchor="w")
        self.on_status.pack(side="left", padx=10)
        self.on_sc = ctk.CTkScrollableFrame(f)
        self.on_sc.grid(row=2, column=0, sticky="nsew", padx=4, pady=4)
        f.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(self.on_sc, text="Paste a free Tenor or GIPHY API key above (needed once - it is stored in your config file),\n"
                     "then search. This is how you get the WhatsApp / GIPHY style GIFs without any file hunting.",
                     text_color=MUTED, justify="left").grid(row=0, column=0, padx=10, pady=20)

    def _online_provider_changed(self, prov):
        self.on_key.delete(0, "end")
        self.on_key.insert(0, self.cfg.setdefault("online_keys", {}).get(prov, ""))
        self._on_loaded = False

    def online_key_help(self):
        import webbrowser
        webbrowser.open(ONLINE_PROVIDERS[self.on_provider.get()])

    def online_run(self, query):
        prov, key = self.on_provider.get(), self.on_key.get().strip()
        self.cfg.setdefault("online_keys", {})[prov] = key
        self.cfg["online_provider"] = prov
        save_config(self.cfg)
        self._on_gen += 1
        gen = self._on_gen
        self.on_status.configure(text="searching...", text_color=MUTED)

        def fail():
            if gen == self._on_gen:
                self.on_status.configure(text="search failed - see the log below", text_color=ERR)
        self.bg(lambda: online_search(prov, key, query), lambda res: self._online_show(res, gen, query),
                "Online search failed", fail=fail)

    def _online_show(self, results, gen, query):
        if gen != self._on_gen:
            return
        self._on_loaded = True
        for w in self.on_sc.winfo_children():
            w.destroy()
        self.on_status.configure(text=f"{len(results)} result(s)" if results else "nothing found", text_color=MUTED)
        tiles = []
        for idx, r in enumerate(results):
            tiles.append(self._gif_tile(self.on_sc, idx, r["title"] or "GIF", None, lambda r=r: self.use_online(r)))

        def fetch():
            from concurrent.futures import ThreadPoolExecutor

            def one(i):
                if gen != self._on_gen or self.closing:
                    return
                try:
                    im = Image.open(io.BytesIO(_http_get(results[i]["thumb"], timeout=10, limit=2_000_000)))
                    im.seek(0)
                    th = round_thumb(im.copy(), self.GIF_TILE)
                except Exception:                                  # noqa: BLE001 - one bad thumbnail must not stop the rest
                    return
                self.post(lambda: self._online_thumb(tiles[i], th, gen))
            with ThreadPoolExecutor(max_workers=6) as ex:
                list(ex.map(one, range(len(results))))
        threading.Thread(target=fetch, daemon=True).start()

    def _online_thumb(self, tile, pil, gen):
        if gen != self._on_gen:
            return
        cimg = ctk.CTkImage(light_image=pil, dark_image=pil, size=(self.GIF_TILE, self.GIF_TILE))
        self._gif_thumbs.append(cimg)
        try:
            tile.configure(image=cimg)
        except tk.TclError:
            pass

    def use_online(self, item):
        self.use_gif_file(lambda: online_download(item["url"]), item["title"] or "online GIF", save=self.keep_var.get())

    # -- selecting a source
    def _gif_source_ready(self, label):
        self.gif_name.configure(text=label)
        self.gif_info.configure(text="Processing...")
        self.upload_btn.configure(state="disabled")
        self.gif_bar.set(0)
        free = self.dev.info.get("fs_free") if self.dev.connected else None
        if self.dev.connected and self.pad_gif_free is not None:
            free = self.pad_gif_free + self.pad_gifs.get(self._gif_slot(), 0)      # the upload replaces whatever is in its own slot
        return min(max((free or 1_000_000) - 16384, 50_000), 1_400_000), self.dither_var.get()

    def choose_gif(self):
        path = filedialog.askopenfilename(filetypes=[("GIF images", "*.gif"), ("All files", "*.*")])
        if path:
            self.use_gif_file(path, os.path.basename(path), save=self.keep_var.get())

    def use_gif_file(self, src, label, save=False):
        """src: path | bytes | callable returning either (run on the worker thread, e.g. a download)."""
        limit, dither = self._gif_source_ready(label)

        def work():
            s = src() if callable(src) else src
            frames, durs = load_gif_frames(io.BytesIO(bytes(s)) if isinstance(s, (bytes, bytearray)) else s)
            data, colors, n = fit_gif(frames, durs, limit, dither)
            saved = None
            if save:
                inside = not isinstance(s, (bytes, bytearray)) and Path(s).resolve().parent == gif_lib_dir().resolve()
                if not inside:
                    saved = lib_add(s, label if isinstance(s, (bytes, bytearray)) else None)
            return (frames, durs, data, colors, n, limit), saved

        def ok(res):
            self._gif_ready(res[0])
            if res[1]:
                self.set_status(f"Saved a copy as {res[1].name} in My GIFs")
                if self.gif_view.get() == "My GIFs":
                    self.refresh_my_gifs()
        self.bg(work, ok, "GIF processing failed")

    def use_preset(self, name):
        limit, dither = self._gif_source_ready(f"built-in: {name}")

        def work():
            frames, durs = build_preset(name)
            data, colors, n = fit_gif(frames, durs, limit, dither)
            return frames, durs, data, colors, n, limit
        self.bg(work, self._gif_ready, "Preset processing failed")

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

    def _gif_slot(self):
        return int(self.gif_slot_var.get().split()[1]) - 1

    def upload_gif(self):
        if not self.dev.connected:
            return self.set_status("Connect the pad first", error=True)
        data, slot = self.gif_data, self._gif_slot()
        if self._core_only("GIFs"):
            return
        if slot and int(self.dev.info.get("gifs", -1)) < 0:
            return self.set_status("This pad's firmware has a single GIF slot - update it (Device -> Firmware) to use more", error=True)
        self.upload_btn.configure(state="disabled")
        self.gif_bar.set(0)
        self.set_status(f"Uploading GIF to slot {slot + 1}...")

        def progress(f):
            self.post(lambda: (self.gif_bar.set(f), setattr(self.pad, "upload_frac", f)))

        def finished():
            self.pad.upload_frac, self.pad.dirty = None, True
            self.upload_btn.configure(state="normal")

        def done(_):
            finished()
            self.gif_bar.set(1)
            self.pad.set_mode(M_GIF)
            self.set_status(f"GIF uploaded to slot {slot + 1} - the pad switched to GIF mode")
            self.refresh_pad_gifs()
        self.bg(lambda: self.dev.upload_gif(data, progress, slot), done, "Upload failed", fail=finished)

    def delete_gif(self, slot=None):
        slot = self._gif_slot() if slot is None else slot
        self.bg(lambda: self.dev.request({"cmd": "gif_delete", **({"slot": slot} if slot else {})}),
                lambda _: (self.set_status(f"GIF slot {slot + 1} cleared" + (" - the built-in demo animation will be regenerated" if slot == 0 else "")),
                           self.refresh_pad_gifs()), "Delete failed")

    # ---- the pad's own GIF slots
    def refresh_pad_gifs(self):
        if not self.dev.connected:
            self.pad_gifs = {}
            return self._draw_pad_gifs({"slots": [], "cur": 0, "rot": 0, "max": 1})
        if self.dev.info.get("core_only"):
            self.pad_gifs, self.pad_gif_free = {}, None
            for w in self.pad_gif_box.winfo_children():
                w.destroy()
            return ui.muted(self.pad_gif_box, "CoreBringup has no GIF storage. Flash the full firmware.", wraplength=250).pack(anchor="w")
        if "gifslots" not in (self.dev.info.get("caps") or []):         # firmware 1.1: one GIF, no listing command
            self.pad_gifs, self.pad_gif_free = {}, None
            for w in self.pad_gif_box.winfo_children():
                w.destroy()
            return ui.muted(self.pad_gif_box, "This firmware has a single GIF slot. Update it (Device -> Firmware) for 4 slots and rotation.",
                            wraplength=250).pack(anchor="w")
        self.bg(lambda: self.dev.request({"cmd": "gif_list"}), self._draw_pad_gifs, "Could not list the pad's GIFs")

    def _draw_pad_gifs(self, r):
        if "slots" not in r:
            r = {"slots": [], "cur": 0, "rot": 0, "max": 1}
        self.pad_gifs = {x["s"]: x["size"] for x in r["slots"]}
        self.pad_gif_free = r.get("fs_free")
        for w in self.pad_gif_box.winfo_children():
            w.destroy()
        if not self.dev.connected:
            ui.muted(self.pad_gif_box, "Connect the pad to see what is stored on it.").pack(anchor="w")
            return
        for sl in range(int(r.get("max", 4))):
            row = ctk.CTkFrame(self.pad_gif_box, fg_color=CARD2, corner_radius=8, border_width=0)
            row.pack(fill="x", pady=2)
            have = sl in self.pad_gifs
            cur = have and sl == r.get("cur")
            ctk.CTkLabel(row, text=f"{sl + 1}", width=22, anchor="w", font=ui.font(13, "bold")).pack(side="left", padx=(12, 2), pady=6)
            ctk.CTkLabel(row, text=(f"{self.pad_gifs[sl] / 1024:.0f} KB" if have else "empty") + (" - playing" if cur else ""), width=104, anchor="w",
                         text_color=ACCENT if cur else (TEXT if have else FAINT)).pack(side="left")
            if have:
                ui.secondary_button(row, "Show", lambda sl=sl: self._pad_gif_show(sl), width=50).pack(side="right", padx=(2, 6))
                ui.secondary_button(row, "Del", lambda sl=sl: self.delete_gif(sl), width=40).pack(side="right", padx=2)
        self.gif_rot_var.set(self._rot_label(int(r.get("rot", 0))))

    ROT_CHOICES = [("off", 0), ("5 seconds", 5), ("10 seconds", 10), ("30 seconds", 30), ("1 minute", 60), ("5 minutes", 300)]

    def _rot_label(self, secs):
        return min(self.ROT_CHOICES, key=lambda c: abs(c[1] - secs))[0]

    def _pad_gif_show(self, slot):
        self.bg(lambda: self.dev.request({"cmd": "gif_cfg", "slot": slot}), lambda r: (self.pad.set_mode(M_GIF), self._draw_pad_gifs(r)), "Could not switch GIF")

    def _gif_rot_changed(self, label):
        secs = dict(self.ROT_CHOICES)[label]
        if self.dev.connected:
            self.bg(lambda: self.dev.request({"cmd": "gif_cfg", "rot": secs}), self._draw_pad_gifs, "Could not set rotation")

    # ---- profiles: the pad's layer follows the focused program
    def _build_profiles(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(tab)
        top.grid(row=0, column=0, sticky="ew", padx=6, pady=(4, 6))
        self.prof_on = tk.BooleanVar(value=bool(self.cfg.get("profiles_on")))
        ctk.CTkSwitch(top, text="Switch the pad's layer automatically", variable=self.prof_on, command=self._profiles_toggled).grid(
            row=0, column=0, padx=14, pady=(12, 4), sticky="w")
        ui.muted(top, "The app watches which program has the keyboard focus. The first matching rule below decides the layer; "
                 "your key maps for each layer are edited on the Pad page.", wraplength=820).grid(row=1, column=0, columnspan=4, padx=14, sticky="w")
        row = ctk.CTkFrame(top, fg_color="transparent")
        row.grid(row=2, column=0, columnspan=4, sticky="w", padx=14, pady=(8, 4))
        ctk.CTkLabel(row, text="When no rule matches:").pack(side="left")
        self.prof_default = tk.StringVar(value=self._default_label(self.cfg.get("profile_default", 0)))
        ctk.CTkOptionMenu(row, values=["keep the current layer", "Layer 1", "Layer 2", "Layer 3"], variable=self.prof_default, width=190,
                          command=self._profile_default_changed).pack(side="left", padx=8)
        self.prof_live = ctk.CTkLabel(top, text="", text_color=ACCENT, anchor="w", justify="left")
        self.prof_live.grid(row=3, column=0, columnspan=4, padx=14, pady=(2, 12), sticky="w")

        box = ctk.CTkFrame(tab)
        box.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(1, weight=1)
        self._title(box, "Rules (first match wins)")
        self.prof_list = ctk.CTkScrollableFrame(box, fg_color="transparent")
        self.prof_list.grid(row=1, column=0, sticky="nsew", padx=8)
        add = ctk.CTkFrame(box, fg_color="transparent")
        add.grid(row=2, column=0, sticky="ew", padx=12, pady=10)
        self.pr_name = ctk.CTkEntry(add, width=130, placeholder_text="name")
        self.pr_match = ctk.CTkEntry(add, width=190, placeholder_text="program or window text")
        self.pr_kind = tk.StringVar(value="either")
        self.pr_layer = tk.StringVar(value="Layer 3")
        self.pr_name.pack(side="left", padx=(0, 6))
        self.pr_match.pack(side="left", padx=6)
        ctk.CTkOptionMenu(add, values=["either", "process", "title"], variable=self.pr_kind, width=90).pack(side="left", padx=6)
        ctk.CTkOptionMenu(add, values=["Layer 1", "Layer 2", "Layer 3"], variable=self.pr_layer, width=90).pack(side="left", padx=6)
        ctk.CTkButton(add, text="Add rule", width=90, command=self.profile_add).pack(side="left", padx=6)
        ui.secondary_button(add, "Use the program I focus in 3 s", self.profile_capture, width=210).pack(side="left", padx=6)
        sug = ctk.CTkFrame(box, fg_color="transparent")
        sug.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        ui.muted(sug, "Quick add:").pack(side="left", padx=(0, 8))
        for name, match, kind, layer in (("VS Code", "code", "process", 2), ("Browser", "firefox", "process", 2), ("Spotify", "spotify", "process", 1),
                                         ("Zoom", "zoom", "process", 1)):
            ui.secondary_button(sug, name, lambda a=(name, match, kind, layer): self.profile_add(*a), width=80).pack(side="left", padx=3)
        choices = presets.profile_choices()
        self.pr_more = tk.StringVar(value="More programs...")
        ctk.CTkOptionMenu(sug, values=[c[0] for c in choices], variable=self.pr_more, width=150,
                          command=lambda n: self.profile_add(*next((c[0], c[1], c[2], int(self.pr_layer.get().split()[-1]) - 1) for c in choices if c[0] == n))).pack(side="left", padx=8)
        pre = ctk.CTkFrame(box, fg_color=CARD2, corner_radius=10, border_width=0)
        pre.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 12))
        ctk.CTkLabel(pre, text="Ready-made key layouts", font=ui.font(13, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
        ui.muted(pre, "Fills one layer with the usual shortcuts of a program (meetings, drawing, video editing, spreadsheets, writing, 3D, coding) - "
                 "and can add the program rule. Uses each program's default shortcuts.", wraplength=820).pack(anchor="w", padx=12)
        r = ctk.CTkFrame(pre, fg_color="transparent")
        r.pack(fill="x", padx=12, pady=(6, 10))
        self.preset_var = tk.StringVar(value=list(presets.PRESETS)[0])
        ctk.CTkOptionMenu(r, values=list(presets.PRESETS), variable=self.preset_var, width=260).pack(side="left")
        ctk.CTkLabel(r, text="onto").pack(side="left", padx=6)
        self.preset_layer = tk.StringVar(value="Layer 3")
        ctk.CTkOptionMenu(r, values=["Layer 1", "Layer 2", "Layer 3"], variable=self.preset_layer, width=90).pack(side="left")
        self.preset_rule = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(r, text="also add the program rule", variable=self.preset_rule).pack(side="left", padx=12)
        ctk.CTkButton(r, text="Apply", width=80, command=self.apply_preset).pack(side="left")
        self._refresh_profiles()

    @staticmethod
    def _default_label(v):
        return "keep the current layer" if v is None or int(v) < 0 else f"Layer {int(v) + 1}"

    def _profile_default_changed(self, label):
        self.cfg["profile_default"] = -1 if label.startswith("keep") else int(label.split()[-1]) - 1
        save_config(self.cfg)
        self._profile_state["win"] = None                       # re-evaluate at once

    def _profiles_toggled(self):
        self.cfg["profiles_on"] = bool(self.prof_on.get())
        save_config(self.cfg)
        self._profile_state.update(win=None, layer=None)

    def _refresh_profiles(self):
        for w in self.prof_list.winfo_children():
            w.destroy()
        rules = self.cfg["profiles"]
        if not rules:
            ui.muted(self.prof_list, "No rules yet. Add one below, or use 'Quick add'.").pack(anchor="w", padx=8, pady=14)
        for i, r in enumerate(rules):
            row = ctk.CTkFrame(self.prof_list, fg_color=CARD2, corner_radius=8, border_width=0)
            row.pack(fill="x", pady=3)
            v = tk.BooleanVar(value=r.get("enabled", True))
            ctk.CTkCheckBox(row, text="", variable=v, width=24, command=lambda i=i, v=v: self._profile_edit(i, enabled=bool(v.get()))).pack(side="left", padx=(10, 2), pady=8)
            ctk.CTkLabel(row, text=r.get("name") or r["match"], font=ui.font(13, "bold"), width=130, anchor="w").pack(side="left", padx=6)
            ui.muted(row, f"{r.get('kind', 'either')}: \"{r['match']}\"", width=260).pack(side="left", padx=6)
            lay = tk.StringVar(value=f"Layer {int(r.get('layer', 0)) + 1}")
            ctk.CTkOptionMenu(row, values=["Layer 1", "Layer 2", "Layer 3"], variable=lay, width=90,
                              command=lambda val, i=i: self._profile_edit(i, layer=int(val.split()[-1]) - 1)).pack(side="left", padx=6)
            for label, d in (("\u2191", -1), ("\u2193", 1)):
                ui.secondary_button(row, label, lambda i=i, d=d: self._profile_move(i, d), width=32).pack(side="left", padx=2)
            ui.secondary_button(row, "Remove", lambda i=i: self._profile_remove(i), width=70).pack(side="right", padx=10)

    def _profile_edit(self, i, **kw):
        self.cfg["profiles"][i].update(kw)
        save_config(self.cfg)
        self._profile_state["win"] = None

    def _profile_move(self, i, d):
        r = self.cfg["profiles"]
        if 0 <= i + d < len(r):
            r[i], r[i + d] = r[i + d], r[i]
            save_config(self.cfg)
            self._refresh_profiles()

    def _profile_remove(self, i):
        del self.cfg["profiles"][i]
        save_config(self.cfg)
        self._refresh_profiles()
        self._profile_state["win"] = None

    def apply_preset(self):
        name, layer = self.preset_var.get(), int(self.preset_layer.get().split()[-1]) - 1
        if layer and self.dev.connected and not self._layers_supported():
            return self.set_status("This pad's firmware has no layers - update it (Device -> Firmware)", error=True)
        try:
            slots, rule = presets.apply(self.cfg, name, layer, bool(self.preset_rule.get()), lambda c, a: (c, a) in ACTION_INDEX)
        except (KeyError, ValueError) as e:
            return self.set_status(f"Preset failed: {e}", error=True)
        if layer == self.edit_layer:
            self.refresh_action_lists()
            self.padview.refresh()
        save_config(self.cfg)
        self.recompute_pending()
        self._refresh_profiles()
        self._profile_state["win"] = None
        self.set_status(f"'{name}' applied to layer {layer + 1}" + (f"; rule added for {rule['match']}" if rule else "") +
                        " - press 'Upload to pad' on the Pad page to send it")

    def profile_add(self, name=None, match=None, kind=None, layer=None):
        name = name if name is not None else self.pr_name.get().strip()
        match = match if match is not None else self.pr_match.get().strip()
        kind = kind or self.pr_kind.get()
        layer = layer if layer is not None else int(self.pr_layer.get().split()[-1]) - 1
        if not match:
            return self.set_status("Enter the program or window text to match first", error=True)
        self.cfg["profiles"].append({"name": name or match, "match": match, "kind": kind, "layer": layer, "enabled": True})
        save_config(self.cfg)
        self.pr_name.delete(0, "end")
        self.pr_match.delete(0, "end")
        self._refresh_profiles()
        self._profile_state["win"] = None
        self.set_status(f"Rule added: '{match}' -> layer {layer + 1}")

    def profile_capture(self):
        self.set_status("Switch to the program you want to match - capturing in 3 seconds...")

        def grab():
            time.sleep(3.0)
            return self.active_win.get()

        def done(res):
            proc, title = res
            if not proc and not title:
                return self.set_status("Could not detect the focused program on this system (Linux needs xdotool or xprop)", error=True)
            self.pr_match.delete(0, "end")
            self.pr_match.insert(0, proc or title)
            self.pr_kind.set("process" if proc else "title")
            self.pr_name.delete(0, "end")
            self.pr_name.insert(0, (proc or title)[:20])
            self.set_status(f"Captured: {proc or '-'}  |  {title[:60]}")
        self.bg(grab, done, "Capture failed")

    def _profile_loop(self):
        while not self.closing:
            time.sleep(max(0.05, self.profile_poll))
            st = self._profile_state
            if not self.cfg.get("profiles_on") or not self.dev.connected or self.dev.busy:
                if st["text"] != "profiles are off" and not self.cfg.get("profiles_on"):
                    st["text"] = "profiles are off"
                    self.post(self._profile_label)
                continue
            if not self._layers_supported():
                if st["text"] != "the pad's firmware has no layers - update it (Device -> Firmware)":
                    st["text"] = "the pad's firmware has no layers - update it (Device -> Firmware)"
                    self.post(self._profile_label)
                continue
            proc, title = self.active_win.get()
            if (APP_NAME in title) and proc in ("", "python", "python3", "pythonw", "deskcompanion", "companion_app", "desk companion"):
                continue                                         # the focus is on this app itself: leave the layer alone
            if not proc and not title:
                st["text"] = "cannot read the focused program on this system"
                self.post(self._profile_label)
                continue
            if (proc, title) == st["win"]:
                continue
            st["win"] = (proc, title)
            default = self.cfg.get("profile_default", 0)
            lay = activewin.pick_layer(self.cfg["profiles"], proc, title, default=None if default is None or int(default) < 0 else int(default))
            st["text"] = f"focused: {proc or '?'} | {title[:50]}   ->   " + (f"layer {lay + 1}" if lay is not None else "no change")
            if lay is not None and lay != st["layer"]:
                try:
                    self.dev.request({"cmd": "layer", "val": lay})
                    st["layer"] = lay
                    self.post(lambda t=f"Profile: {proc or title[:20]} -> layer {lay + 1}": self._dev_note(t))
                except DeviceError:
                    st["win"] = None
            self.post(self._profile_label)

    def _profile_label(self):
        if hasattr(self, "prof_live"):
            self.prof_live.configure(text=self._profile_state["text"])

    # ---- info screen: now playing / weather / next event / custom card / badges, pushed to the pad's mode 6
    def _build_info(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_columnconfigure(1, weight=0)
        tab.grid_rowconfigure(0, weight=1)
        left = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(4, 6))
        right = ctk.CTkFrame(tab, width=300)
        right.grid(row=0, column=1, sticky="ns", padx=(0, 4), pady=4)
        right.pack_propagate(False)
        info = self.cfg["info"]
        self.info_vars = {}

        def card(title, key, subtitle):
            c = ctk.CTkFrame(left)
            c.pack(fill="x", pady=5, padx=2)
            v = tk.BooleanVar(value=bool(info.get(key)))
            self.info_vars[key] = v
            ctk.CTkSwitch(c, text=title, variable=v, font=ui.font(14, "bold"), command=self._info_changed).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 2))
            ui.muted(c, subtitle, wraplength=600).grid(row=1, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 6))
            return c

        c = card("Now playing", "music", "Title and artist of what plays on this computer (Spotify, browsers, media players).")
        self.info_music_lbl = ui.muted(c, "")
        self.info_music_lbl.grid(row=2, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 12))
        c = card("Weather", "weather", "Free service (Open-Meteo), no key needed. Updated every 10 minutes.")
        row = ctk.CTkFrame(c, fg_color="transparent")
        row.grid(row=2, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 6))
        self.info_city = ctk.CTkEntry(row, width=220, placeholder_text="city, e.g. Zurich")
        self.info_city.pack(side="left")
        if info.get("city"):
            self.info_city.insert(0, info["city"])
        ctk.CTkButton(row, text="Find", width=70, command=self.info_find_city).pack(side="left", padx=8)
        self.info_f = tk.BooleanVar(value=bool(info.get("fahrenheit")))
        ctk.CTkCheckBox(row, text="Fahrenheit", variable=self.info_f, command=self._info_changed).pack(side="left", padx=10)
        self.info_weather_lbl = ui.muted(c, info.get("label") and f"place: {info['label']}" or "no place chosen yet")
        self.info_weather_lbl.grid(row=3, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 12))
        c = card("Next calendar event", "event", "Reads a calendar file (.ics, e.g. exported from Google / Outlook / Apple Calendar) or a private .ics link. "
                 "Repeating events are not expanded.")
        row = ctk.CTkFrame(c, fg_color="transparent")
        row.grid(row=2, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 6))
        self.info_ics = ctk.CTkEntry(row, width=360, placeholder_text="path to a .ics file or an https:// link")
        self.info_ics.pack(side="left")
        if info.get("ics"):
            self.info_ics.insert(0, info["ics"])
        ui.secondary_button(row, "Browse...", self.info_browse_ics, width=80).pack(side="left", padx=8)
        ctk.CTkButton(row, text="Use", width=60, command=self._info_changed).pack(side="left")
        self.info_event_lbl = ui.muted(c, "")
        self.info_event_lbl.grid(row=3, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 12))
        c = card("Custom card", "custom", "Any text you like - a motto, a reminder, a status line (ASCII only, ~20 characters per line).")
        grid = ctk.CTkFrame(c, fg_color="transparent")
        grid.grid(row=2, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 12))
        self.info_custom = {}
        for i, (k, ph) in enumerate((("c_label", "small heading"), ("c_t", "big text"), ("c_a", "line 1"), ("c_b", "line 2"))):
            e = ctk.CTkEntry(grid, width=140, placeholder_text=ph)
            e.grid(row=0, column=i, padx=(0, 6))
            if info.get(k):
                e.insert(0, info[k])
            e.bind("<KeyRelease>", lambda _e: self._info_changed(save_only=True))
            self.info_custom[k] = e
        c = ctk.CTkFrame(left)
        c.pack(fill="x", pady=5, padx=2)
        ui.heading(c, "More cards").grid(row=0, column=0, sticky="w", padx=14, pady=(12, 2))
        ui.muted(c, "Countdowns, other time zones, a git repository's state, the latest GitHub Actions run, a crypto price. The pad shows up to 4 cards in total.",
                 wraplength=620).grid(row=1, column=0, columnspan=4, sticky="w", padx=14)
        self.extra_list = ctk.CTkFrame(c, fg_color="transparent")
        self.extra_list.grid(row=2, column=0, columnspan=4, sticky="ew", padx=10, pady=4)
        form = ctk.CTkFrame(c, fg_color="transparent")
        form.grid(row=3, column=0, columnspan=4, sticky="w", padx=14, pady=(2, 12))
        self.extra_kind = tk.StringVar(value=extras.KINDS["countdown"])
        ctk.CTkOptionMenu(form, values=list(extras.KINDS.values()), variable=self.extra_kind, width=190, command=lambda _v: self._extra_kind_changed()).pack(side="left")
        self.extra_label = ctk.CTkEntry(form, width=120, placeholder_text="label")
        self.extra_label.pack(side="left", padx=6)
        self.extra_arg = ctk.CTkEntry(form, width=230, placeholder_text="2026-12-24")
        self.extra_arg.pack(side="left", padx=6)
        ctk.CTkButton(form, text="Add card", width=90, command=self.extra_add).pack(side="left", padx=6)
        self._extra_refresh()
        c = ctk.CTkFrame(left)
        c.pack(fill="x", pady=5, padx=2)
        ui.heading(c, "Notification badges").grid(row=0, column=0, sticky="w", padx=14, pady=(12, 2))
        ui.muted(c, "Small counters on the Info screen. Any script, IFTTT / Home-Assistant rule or mail filter can set one - the app listens on this computer only "
                 "and needs the secret token:", wraplength=620).grid(row=1, column=0, columnspan=3, sticky="w", padx=14)
        self.badge_url = ctk.CTkEntry(c, width=560)
        self.badge_url.grid(row=2, column=0, padx=14, pady=6, sticky="w")
        if self.badges:
            self.badge_url.insert(0, self.badges.url())
        self.badge_url.configure(state="readonly")
        ui.secondary_button(c, "Copy", self.info_copy_badge, width=70).grid(row=2, column=1, padx=4)
        ctk.CTkButton(c, text="Test badge", width=100, command=self.info_test_badge).grid(row=2, column=2, padx=4)
        self.badge_lbl = ui.muted(c, "no badges set")
        self.badge_lbl.grid(row=3, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 12))

        ui.heading(right, "Preview").pack(anchor="w", padx=14, pady=(12, 4))
        self.info_preview = VirtualPad(lambda s: None, lambda n: None, lambda k: None)
        self.info_preview.mode = M_INFO
        self.info_canvas = tk.Canvas(right, width=DISP, height=DISP, bg=ui.DARK["card"], highlightthickness=0)
        self.info_canvas.pack(pady=4)
        self._info_img = None
        self.info_canvas_item = self.info_canvas.create_image(0, 0, anchor="nw")
        ctk.CTkLabel(right, text="Card rotation (seconds)").pack(anchor="w", padx=14, pady=(10, 0))
        self.info_rot = ctk.CTkSlider(right, from_=2, to=30, number_of_steps=28, command=lambda v: self._info_changed(save_only=True))
        self.info_rot.set(int(info.get("rot", 6)))
        self.info_rot.pack(fill="x", padx=14, pady=4)
        ctk.CTkButton(right, text="Send to the pad now", command=lambda: self.info_send_now(force=True)).pack(fill="x", padx=14, pady=(10, 4))
        ui.secondary_button(right, "Show the Info screen on the pad", self.info_show_on_pad).pack(fill="x", padx=14, pady=4)
        self.info_status = ui.muted(right, "", wraplength=260)
        self.info_status.pack(anchor="w", padx=14, pady=8)
        self._info_preview_tick()

    def _info_changed(self, save_only=False):
        info = self.cfg["info"]
        for k, v in self.info_vars.items():
            info[k] = bool(v.get())
        info["fahrenheit"] = bool(self.info_f.get())
        info["ics"] = self.info_ics.get().strip()
        info["rot"] = int(self.info_rot.get())
        for k, e in self.info_custom.items():
            info[k] = e.get()
        save_config(self.cfg)
        if not save_only:
            self._info_cache.pop("event_src", None)
            self._info_sent = (None, 0.0)

    EXTRA_HINTS = {"countdown": "date: 2026-12-24", "worldclock": "Europe/Zurich, Asia/Tokyo", "git": "folder of the repository",
                   "ci": "owner/name (public repository)", "crypto": "bitcoin  or  ethereum:eur"}

    def _extra_kind_changed(self):
        key = next(k for k, v in extras.KINDS.items() if v == self.extra_kind.get())
        self.extra_arg.delete(0, "end")
        self.extra_arg.configure(placeholder_text=self.EXTRA_HINTS[key])

    def _extra_refresh(self):
        for w in self.extra_list.winfo_children():
            w.destroy()
        items = self.cfg["info"]["extras"]
        if not items:
            ui.muted(self.extra_list, "None yet.").pack(anchor="w", padx=6, pady=4)
        for i, it in enumerate(items):
            row = ctk.CTkFrame(self.extra_list, fg_color=CARD2, corner_radius=8, border_width=0)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=extras.KINDS[it["type"]], width=170, anchor="w", font=ui.font(12, "bold")).pack(side="left", padx=(10, 4), pady=6)
            ui.muted(row, (it["label"] + "  " if it["label"] else "") + it["arg"], width=300).pack(side="left", padx=4)
            ui.secondary_button(row, "Remove", lambda i=i: self.extra_remove(i), width=70).pack(side="right", padx=8)

    def extra_add(self):
        key = next(k for k, v in extras.KINDS.items() if v == self.extra_kind.get())
        try:
            item = extras.validate({"type": key, "label": self.extra_label.get(), "arg": self.extra_arg.get()})
            if len(self.cfg["info"]["extras"]) >= 4:
                raise ValueError("the pad shows at most 4 cards - remove one first")
        except ValueError as e:
            return self.set_status(f"Cannot add the card: {e}", error=True)
        self.cfg["info"]["extras"].append(item)
        save_config(self.cfg)
        self._info_sent = (None, 0.0)
        self.extra_label.delete(0, "end")
        self.extra_arg.delete(0, "end")
        self._extra_refresh()
        self.set_status(f"Added: {extras.KINDS[key]}")

    def extra_remove(self, i):
        del self.cfg["info"]["extras"][i]
        save_config(self.cfg)
        self._info_sent = (None, 0.0)
        self._extra_refresh()

    def info_find_city(self):
        name = self.info_city.get().strip()
        if not name:
            return self.set_status("Type a city first", error=True)

        def done(res):
            lat, lon, label = res
            self.cfg["info"].update(city=name, lat=lat, lon=lon, label=label)
            save_config(self.cfg)
            self.info_weather_lbl.configure(text=f"place: {label}  ({lat:.2f}, {lon:.2f})")
            self.info_vars["weather"].set(True)
            self._info_changed()
            self.set_status(f"Weather place set to {label}")
        self.bg(lambda: feeds.geocode(name), done, "City lookup failed")

    def info_browse_ics(self):
        path = filedialog.askopenfilename(filetypes=[("Calendar files", "*.ics"), ("All files", "*.*")])
        if path:
            self.info_ics.delete(0, "end")
            self.info_ics.insert(0, path)
            self.info_vars["event"].set(True)
            self._info_changed()

    def info_copy_badge(self):
        self.clipboard_clear()
        self.clipboard_append(self.badges.url() if self.badges else "")
        self.set_status("Badge URL copied - change name=mail and n=3 to whatever you need")

    def info_test_badge(self):
        if self.badges:
            self.badges.set("test", 3)
            self.set_status("Test badge set - it appears on the Info screen")

    def info_show_on_pad(self):
        self.pad.set_mode(M_INFO)
        if self.dev.connected:
            self.bg(lambda: self.dev.request({"cmd": "mode", "val": M_INFO}), None, "Mode switch failed")

    def _info_preview_tick(self):
        if self.closing:
            return
        try:
            if self.tabs.get() == "Info Screen":
                img = ImageTk.PhotoImage(self.info_preview.render())
                self._info_img = img
                self.info_canvas.itemconfig(self.info_canvas_item, image=img)
        except Exception:                                    # noqa: BLE001
            traceback.print_exc()
        self.after(600, self._info_preview_tick)

    def _info_collect(self):
        """Gather the cards for the pad. Runs on a worker thread. -> (cards, badges, {source: error text})"""
        info, now, cards, errs = self.cfg["info"], time.time(), [], {}
        cache = self._info_cache

        def cached(key, ttl, fn):
            hit = cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
            val = fn()
            cache[key] = (now, val)
            return val
        if info.get("music"):
            try:
                np = feeds.now_playing()
                if np:
                    cards.append(feeds.music_card(np))
                errs["music"] = "" if np else ("nothing is playing" if feeds.have_player_tool() else "install 'playerctl' to read what is playing")
            except Exception as e:                           # noqa: BLE001
                errs["music"] = str(e)
        if info.get("event") and info.get("ics"):
            try:
                text = cached("event_src", 300, lambda: feeds.load_calendar(info["ics"]))
                ev = feeds.next_event(feeds.parse_ics(text))
                if ev:
                    cards.append(feeds.event_card(ev))
                errs["event"] = "" if ev else "no upcoming events found"
            except Exception as e:                           # noqa: BLE001
                errs["event"] = f"calendar problem: {e}"
        if info.get("weather") and info.get("lat") is not None:
            try:
                w = cached("weather", 600, lambda: feeds.weather_fetch(info["lat"], info["lon"]))
                cards.append(feeds.weather_card(info.get("label", ""), w, bool(info.get("fahrenheit"))))
                errs["weather"] = ""
            except Exception as e:                           # noqa: BLE001
                errs["weather"] = f"weather problem: {e}"
        if info.get("custom") and any(info.get(k) for k in ("c_label", "c_t", "c_a", "c_b")):
            f = feeds.ascii_fold
            cards.append({"k": "c", "label": f(info.get("c_label", ""), 24) or "NOTE", "t": f(info.get("c_t", ""), 24), "a": f(info.get("c_a", ""), 40), "b": f(info.get("c_b", ""), 40)})
        for i, item in enumerate(info.get("extras") or []):
            key = f"x{i}"
            try:
                cards.append(cached(key + json.dumps(item, sort_keys=True), extras.TTL[item["type"]], lambda it=item: extras.build(it)))
                errs[key] = ""
            except Exception as e:                           # noqa: BLE001
                errs[key] = f"{extras.KINDS.get(item.get('type'), 'card')}: {e}"
        badges = self.badges.get() if self.badges else []
        return cards[:4], badges, errs

    def info_send_now(self, force=False):
        def work():
            cards, badges, errs = self._info_collect()
            sig = json.dumps([cards, badges], sort_keys=True)
            last_sig, last_t = self._info_sent
            sent = False
            if self.dev.connected and not self.dev.busy and int(self.dev.info.get("modes", 5)) >= 6 and (force or sig != last_sig or time.time() - last_t > 60):
                self.dev.request({"cmd": "info_cards", "cards": cards, "badges": badges, "rot": int(self.cfg["info"].get("rot", 6))})
                self._info_sent, sent = (sig, time.time()), True
            return cards, badges, errs, sent
        self.bg(work, self._info_done, "Info update failed")

    def _info_done(self, res):
        cards, badges, errs, sent = res
        self.pad.set_info(cards, badges, int(self.cfg["info"].get("rot", 6)))
        self.info_preview.set_info(cards, badges, int(self.cfg["info"].get("rot", 6)))
        self.info_music_lbl.configure(text=errs.get("music") or "playing: " + (cards[0]["t"] if cards and cards[0]["k"] == "m" else ""))
        self.info_event_lbl.configure(text=errs.get("event") or "")
        self.badge_lbl.configure(text=("badges: " + ", ".join(f"{b['name']} {b['n']}" for b in badges)) if badges else "no badges set")
        bad = [v for v in errs.values() if v and "nothing is playing" not in v]
        self.info_status.configure(text=("sent to the pad " + time.strftime("%H:%M:%S") if sent else "not sent (pad not connected or firmware without Info screen)") +
                                   ("\n" + "\n".join(bad) if bad else ""))

    def _info_loop(self):
        while not self.closing:
            time.sleep(max(0.05, self.info_poll))
            info = self.cfg["info"]
            if not any(info.get(k) for k in ("music", "weather", "event", "custom")) and not info.get("extras") and not (self.badges and self.badges.get()):
                continue
            try:
                cards, badges, errs = self._info_collect()
            except Exception:                                # noqa: BLE001
                continue
            sig = json.dumps([cards, badges], sort_keys=True)
            last_sig, last_t = self._info_sent
            sent = False
            if self.dev.connected and not self.dev.busy and int(self.dev.info.get("modes", 5)) >= 6 and (sig != last_sig or time.time() - last_t > 60):
                try:
                    self.dev.request({"cmd": "info_cards", "cards": cards, "badges": badges, "rot": int(info.get("rot", 6))})
                    self._info_sent, sent = (sig, time.time()), True
                except DeviceError:
                    pass
            if sig != last_sig or sent:
                self.post(lambda r=(cards, badges, errs, sent): self._info_done(r))

    # ---- device tab
    # ---- wizards, palette
    def save_cfg(self):
        save_config(self.cfg)

    def open_wizard(self):
        wizards.SetupWizard(self)

    def open_hwtest(self):
        wizards.HardwareTest(self, APP_VERSION)

    def open_palette(self):
        cmds = [(f"Go to {label}  -  {sub}", lambda n=name: self.tabs.set(n)) for _g, pages in PAGES for name, label, sub in pages]
        cmds += [("Upload everything to the pad", self.upload_all), ("Connect / disconnect the simulated pad", self.toggle_simulate),
                 ("Rescan serial ports", self.refresh_ports), ("Verify the keys stored on the pad", self.verify_pad_keys),
                 ("Toggle light / dark theme", lambda: (self.theme_var.set(not self.theme_var.get()), self._theme_toggled())),
                 ("Run the guided hardware test", self.open_hwtest), ("Run the setup wizard", self.open_wizard),
                 ("Back up everything...", self.backup_export), ("Restore from a backup...", self.backup_import),
                 ("Show the Info screen on the pad", self.info_show_on_pad), ("Send info cards now", lambda: self.info_send_now(force=True)),
                 ("Check the pad's state (safe mode?)", self.recovery_check)]
        for n in range(LAYERS):
            cmds.append((f"Pad: switch to layer {n + 1}", lambda n=n: (self.set_edit_layer(n), self.show_layer_on_pad())))
        for i, label in enumerate(MODE_CHOICES, start=1):
            cmds.append((f"Pad: show screen {label}", lambda i=i: (self.pad.set_mode(i), self.dev.connected and self.bg(lambda: self.dev.request({"cmd": "mode", "val": i}), None, "Mode switch failed"))))
        wizards.CommandPalette(self, cmds)

    # ---- firmware: version check, USB flash, Wi-Fi OTA
    @staticmethod
    def _ver(v):
        m = re.match(r"(\d+)\.(\d+)\.(\d+)", str(v or ""))
        return tuple(int(x) for x in m.groups()) if m else None

    def fw_status(self):
        """-> (kind, text): kind in off | ok | old | newer | unknown"""
        if not self.dev.connected:
            return "off", "pad not connected"
        if self.dev.info.get("core_only"):
            return "core", "CoreBringup (diagnostic sketch) - flash the full firmware to use keys, layers, GIFs and the screens"
        cur = self.dev.info.get("fw")
        a, b = self._ver(cur), self._ver(FW_BUNDLED)
        if a is None:
            return "unknown", f"firmware '{cur}' (unknown version)"
        if a < b:
            return "old", f"firmware {cur} - older than the {FW_BUNDLED} that belongs to this app"
        if a > b:
            return "newer", f"firmware {cur} - newer than this app expects ({FW_BUNDLED}); update the app"
        return "ok", f"firmware {cur} - up to date"

    def wifi_supported(self):
        """Does the connected firmware contain the (optional) Wi-Fi code?  Firmware 1.1 predates the switch and always had it."""
        if not self.dev.connected:
            return True
        caps = self.dev.info.get("caps")
        return True if caps is None else "wifi" in caps

    def refresh_wifi_ui(self):
        if not hasattr(self, "_wifi_widgets"):
            return
        ok = self.wifi_supported()
        for w in self._wifi_widgets:
            w.configure(state="normal" if ok else "disabled")
        self.wifi_note.configure(text="Save your Wi-Fi in the card below, enable OTA with a password, then update without a cable. Not tested on real hardware by the author - "
                                 "keep the USB way as a fallback." if ok else
                                 "This pad's firmware is cable-only (its Wi-Fi code is switched off - DC_ENABLE_WIFI is 0 in the sketch, the default). "
                                 "Use the USB buttons above. To get Wi-Fi: set DC_ENABLE_WIFI to 1, build and flash.")

    def refresh_fw_status(self):
        self.refresh_wifi_ui()
        kind, text = self.fw_status()
        color = {"ok": OK, "old": WARN, "newer": WARN, "unknown": WARN, "off": MUTED, "core": WARN}[kind]
        if hasattr(self, "fw_lbl"):
            self.fw_lbl.configure(text=text, text_color=color)
            self.fw_btn.configure(state="normal")
        if hasattr(self, "fw_banner"):
            if kind in ("old", "newer", "unknown", "core"):
                self.fw_banner_lbl.configure(text=text + (".  Layers, mouse actions, the Info screen and GIF slots need the newer firmware." if kind == "old" else "."))
                self.fw_banner.pack(fill="x", padx=2, pady=(0, 8), before=self.home_first)
            else:
                self.fw_banner.pack_forget()

    def flash_firmware(self, which, button=None):
        script = APP_DIR / "firmware" / "flash.py"
        if not script.is_file():
            return self.set_status("firmware/flash.py not found next to the app", error=True)
        label = which if which in ("core", "full") else os.path.basename(which)
        if not messagebox.askyesno("Flash firmware", f"Write '{label}' to the ESP32-S3 now?\n\n"
                                   "The board should be in download mode (BOOT held while plugging in) - or already running DeskCompanion.\n"
                                   "Flashing resets the settings stored on the pad (key maps); the app can upload them again."):
            return
        port = self.dev.port if (self.dev.connected and self.dev.port != SIM_PORT) else ""
        was_auto = self.auto_flag
        self.auto_flag = False                            # keep the auto-connect loop away from the port while flashing
        if self.dev.connected:
            self.dev.disconnect()
            self._on_disconnected()
        for b in (button, getattr(self, "fw_btn", None)):
            if b is not None:
                b.configure(state="disabled")
        self.devtab.sys(f"===== flashing '{label}' =====")

        def work():
            args = ["--image", which, "--yes"] + (["--port", port] if port else [])
            # a packaged app has no Python to run flash.py with: the same executable runs it in "flasher mode" instead
            cmd = [sys.executable, "--flash-helper"] + args if getattr(sys, "frozen", False) else [sys.executable, "-u", str(script)] + args
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in p.stdout:
                line = line.rstrip()
                if line:
                    self.post(lambda l=line: self.devtab.sys(l))
            return p.wait()

        def finish():
            self.auto_flag = was_auto
            for b in (button, getattr(self, "fw_btn", None)):
                if b is not None:
                    b.configure(state="normal")

        def done(rc):
            finish()
            if rc == 0:
                self._flashed_at = time.time()
            self.devtab.sys("flash finished OK - unplug / re-plug the board; the app reconnects by itself" if rc == 0
                            else f"flash FAILED (exit code {rc}) - see the lines above", err=rc != 0)
            self.set_status("Flash finished - unplug and re-plug the board WITHOUT holding BOOT. The first start can take ~40 s; the app connects by itself." if rc == 0
                            else "Flash failed - details in Diagnostics / the log", error=rc != 0)
        self.bg(work, done, "Flashing failed", fail=finish)

    def flash_other(self):
        path = filedialog.askopenfilename(filetypes=[("Firmware images", "*.bin"), ("All files", "*.*")])
        if path:
            self.flash_firmware(path)

    def ota_enable(self, on):
        if not self.dev.connected:
            return self.set_status("Connect the pad first", error=True)
        pw = self.ota_pw.get()

        def work():
            return self.dev.request({"cmd": "ota", "val": on, **({"pass": pw} if pw else {})})
        self.bg(work, lambda r: self.set_status("Wi-Fi update " + ("enabled - the pad joins your Wi-Fi within ~30 s" if on else "disabled")), "OTA setting failed")

    def ota_update(self):
        if not self.dev.connected:
            return self.set_status("Connect the pad first", error=True)
        image = APP_DIR / "firmware" / "DeskCompanion-wifi.bin"
        if not image.is_file():
            return self.set_status("firmware/DeskCompanion-wifi.bin not found", error=True)
        pw = self.ota_pw.get()
        if not messagebox.askyesno("Update over Wi-Fi", "Send the bundled Wi-Fi-enabled firmware to the pad over Wi-Fi?\n\nExperimental: if it fails the pad keeps its old firmware, "
                                   "but keep a USB cable at hand."):
            return
        self.ota_btn.configure(state="disabled")
        self.ota_bar.set(0)

        def work():
            ip = self.dev.request({"cmd": "info"}).get("ip", "")
            if not ip:
                raise RuntimeError("the pad is not on Wi-Fi yet (save Wi-Fi, enable OTA, wait ~30 s)")
            import tempfile
            data = espota.app_image_from_merged(image)
            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
                f.write(data)
            try:
                espota.ota_upload(ip, f.name, pw, progress=lambda x: self.post(lambda x=x: self.ota_bar.set(x)))
            finally:
                os.unlink(f.name)
            return ip
        fin = lambda: self.ota_btn.configure(state="normal")           # noqa: E731
        self.bg(work, lambda ip: (fin(), self.set_status(f"Wi-Fi update sent to {ip} - the pad restarts now")), "Wi-Fi update failed", fail=fin)

    # ---- recovery
    def _shell_toggled(self):
        if self.shell_var.get() and not messagebox.askyesno("Allow shell commands", "Let key presses on the pad run shell commands on this computer?\n\n"
                                                            "Only commands that you put into your own key maps are ever run, but a shell command can do anything "
                                                            "you can do. Keep this off unless you need it."):
            self.shell_var.set(False)
        self.cfg["allow_shell"] = bool(self.shell_var.get())
        save_config(self.cfg)

    def recovery_check(self):
        if not self.dev.connected:
            return self.set_status("Connect the pad first", error=True)

        def done(i):
            if i.get("safe"):
                why = {"crash_loop": "it crashed 3 times in a row", "forced": "this is a safe-mode test build"}.get(i.get("safe_why"), "unknown reason")
                txt, col = f"SAFE MODE - {why}. Last reset: {i.get('reset', '?')}, crashes counted: {i.get('crashes', 0)}. Display and GIFs are off.", ERR
            else:
                txt, col = f"normal mode. Last reset: {i.get('reset', '?')}. Display {'ok' if i.get('ok_disp') else 'OFF'}, storage {i.get('fs_state', '?')}" + \
                    ("  (display disabled at boot)" if i.get("nodisp") else ""), (OK if i.get("ok_disp") else WARN)
            self.safe_lbl.configure(text=txt, text_color=col)
            self._safe_banner(i)
        self.bg(lambda: self.dev.request({"cmd": "info"}), done, "Could not read the pad state")

    def recovery(self, what):
        if not self.dev.connected:
            return self.set_status("Connect the pad first", error=True)
        confirm = {"keys": "Reset ALL key maps on the pad (every layer) to factory defaults?",
                   "settings": "Erase ALL settings stored on the pad (keys, brightness, mode, Wi-Fi, ...) and restart it?",
                   "gifs": "Delete every GIF stored on the pad? (the built-in demo animation comes back)"}.get(what)
        if confirm and not messagebox.askyesno("Are you sure?", confirm):
            return
        msgs = {"safe_retry": ({"cmd": "safe_retry"}, "Restarting the pad for a normal boot"),
                "nodisp": ({"cmd": "boot_opt", "nodisp": True}, "Display will stay off from the next boot (Re-enable display to undo)"),
                "disp": ({"cmd": "boot_opt", "nodisp": False}, "Display enabled again from the next boot"),
                "keys": ({"cmd": "factory", "what": "keys", "confirm": True}, "Pad key maps reset"),
                "settings": ({"cmd": "factory", "what": "settings", "confirm": True}, "Pad settings erased - restarting"),
                "gifs": ({"cmd": "factory", "what": "gifs", "confirm": True}, "Stored GIFs deleted")}
        cmd, ok_text = msgs[what]

        def done(_):
            self.set_status(ok_text)
            if what in ("keys", "settings"):
                for n in range(LAYERS):
                    self.cfg["pushed_layers"][n].clear()
                self.recompute_pending()
            if what in ("nodisp", "safe_retry"):
                self.bg(lambda: self.dev.request({"cmd": "reboot"}), None, "Reboot failed")
        self.bg(lambda: self.dev.request(cmd), done, "Recovery step failed")

    def _safe_banner(self, info):
        if not hasattr(self, "safe_banner") or info.get("core_only"):
            return
        if info.get("safe"):
            self.safe_banner_lbl.configure(text="The pad is in SAFE MODE (it crashed repeatedly). Display and GIFs are off so it stays reachable.")
            self.safe_banner.pack(fill="x", padx=2, pady=(0, 8), before=self.home_first)
        else:
            self.safe_banner.pack_forget()

    # ---- backup / restore / share macros
    def _read_pad_state(self):
        """Everything stored on the connected pad that is not in the app's config (layers read back slot by slot)."""
        layers = []
        for lay in range(LAYERS if self._layers_supported() else 1):
            row = []
            for sl in range(1, 8):
                r = self.dev.request({"cmd": "getkeys", "slot": sl, **({"layer": lay} if lay else {})}, timeout=4)
                row.append(r.get("spec"))
            layers.append(row)
        h = self.dev.request({"cmd": "hello"})
        return {"layers": layers, "bright": h.get("bright"), "mode": h.get("mode"), "os": h.get("os"), "layout": h.get("layout"), "fw": h.get("fw")}

    def backup_export(self):
        path = filedialog.asksaveasfilename(defaultextension=".zip", initialfile=time.strftime("deskcompanion-backup-%Y%m%d.zip"),
                                            filetypes=[("Backup", "*.zip")])
        if not path:
            return

        def work():
            pad = self._read_pad_state() if self.dev.connected else None
            data = backup.make_backup(self.cfg, pad, lib_list(), APP_VERSION)
            Path(path).write_bytes(data)
            return len(data), pad is not None
        self.bg(work, lambda r: self.set_status(f"Backup written: {path}  ({r[0] // 1024} KB, {'with' if r[1] else 'without'} the pad's own key data)"), "Backup failed")

    def backup_import(self):
        path = filedialog.askopenfilename(filetypes=[("Backup", "*.zip"), ("All files", "*.*")])
        if not path:
            return
        try:
            manifest, cfg, pad, gifs = backup.read_backup(Path(path).read_bytes())
        except Exception as e:                           # noqa: BLE001
            return self.set_status(f"This is not a usable backup: {e}", error=True)
        if not messagebox.askyesno("Restore backup", f"Backup from {manifest.get('created', '?')} (app {manifest.get('app', '?')}).\n\n"
                                   f"It replaces your key maps, macros, profiles and settings in this app and adds {len(gifs)} GIF(s) to My GIFs.\n\nContinue?"):
            return
        self.restore_config(cfg, gifs)
        self.set_status("Backup restored in the app - press 'Upload to pad' to send it to the device")

    def restore_config(self, new_cfg, gifs=None):
        keep_dev = {k: self.cfg[k] for k in ("os",) if k in self.cfg}
        new_cfg = normalize_config(dict(new_cfg))
        for lay in new_cfg["pushed_layers"]:
            lay.clear()                                       # nothing is known to be on the pad after a restore
        new_cfg["pushed_mode"] = new_cfg["pushed_bright"] = None
        new_cfg.update({k: v for k, v in keep_dev.items() if k not in new_cfg})
        self.cfg.clear()
        self.cfg.update(new_cfg)
        self.set_edit_layer(0)
        existing = {p.read_bytes() for p in lib_list()}
        for name, data in (gifs or {}).items():
            if data not in existing:
                lib_add(data, Path(name).stem)
        save_config(self.cfg)
        self.refresh_action_lists()
        self.padview.refresh()
        self.recompute_pending()
        if hasattr(self, "prof_list"):
            self._refresh_profiles()
        self.refresh_library()
        if hasattr(self, "mine_sc"):
            self.refresh_my_gifs()

    def macro_export(self):
        names = list(self.cfg["custom"])
        if not names:
            return self.set_status("You have no saved macros yet (Macros page -> Save to library)", error=True)
        path = filedialog.asksaveasfilename(defaultextension=".json", initialfile="deskcompanion-macros.json", filetypes=[("Macros", "*.json")])
        if path:
            Path(path).write_text(json.dumps({"deskcompanion_macros": 1, "macros": self.cfg["custom"]}, indent=1), encoding="utf-8")
            self.set_status(f"{len(names)} macro(s) exported to {path}")

    def macro_import(self):
        path = filedialog.askopenfilename(filetypes=[("Macros", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            macros = data["macros"]
            assert data.get("deskcompanion_macros") == 1 and isinstance(macros, dict)
        except Exception:                                # noqa: BLE001
            return self.set_status("That file is not a Desk Companion macro export", error=True)
        n = 0
        for name, spec in macros.items():
            if isinstance(spec, dict) and spec_ok({"type": spec.get("type"), "val": spec.get("val")}):
                self.cfg["custom"][str(name)[:60]] = {"type": spec["type"], "val": spec["val"]}
                n += 1
        save_config(self.cfg)
        self.refresh_action_lists()
        self.refresh_library()
        self.set_status(f"{n} macro(s) imported - find them in the 'Custom' category" + ("" if n == len(macros) else f" ({len(macros) - n} invalid ones skipped)"))

    def _card(self, parent, title, subtitle=""):
        """A titled card; returns the inner frame to put widgets into (grid)."""
        c = ctk.CTkFrame(parent)
        c.pack(fill="x", pady=6, padx=2)
        ui.heading(c, title).pack(anchor="w", padx=16, pady=(14, 0))
        if subtitle:
            ui.muted(c, subtitle, wraplength=900).pack(anchor="w", padx=16, pady=(0, 2))
        body = ctk.CTkFrame(c, fg_color="transparent", border_width=0)
        body.pack(fill="x", padx=12, pady=(6, 12))
        return body

    def _build_device_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        sc = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        sc.grid(row=0, column=0, sticky="nsew")
        box = self._card(sc, "Display and behaviour")
        ctk.CTkLabel(box, text="Brightness").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.bright = ctk.CTkSlider(box, from_=5, to=255, number_of_steps=50, width=300, command=self._bright_changed)
        self.bright.set(200)
        self.bright.grid(row=0, column=1, padx=6, sticky="w")
        ctk.CTkLabel(box, text="Screen mode").grid(row=1, column=0, padx=6, pady=6, sticky="w")
        self.mode_var = tk.StringVar(value=MODE_CHOICES[0])
        ctk.CTkOptionMenu(box, values=MODE_CHOICES, variable=self.mode_var, width=180, command=self._mode_changed).grid(row=1, column=1, padx=6, sticky="w")
        ctk.CTkLabel(box, text="Host OS").grid(row=2, column=0, padx=6, pady=6, sticky="w")
        self.os_var = tk.StringVar(value=self.cfg["os"])
        ctk.CTkOptionMenu(box, values=["win", "mac", "linux"], variable=self.os_var, width=180, command=self._os_changed).grid(row=2, column=1, padx=6, sticky="w")
        ctk.CTkLabel(box, text="Keyboard layout").grid(row=3, column=0, padx=6, pady=6, sticky="w")
        self.layout_var = tk.StringVar(value=self.cfg.get("layout", "auto"))
        ctk.CTkOptionMenu(box, values=["auto"] + LAYOUTS, variable=self.layout_var, width=180, command=self._layout_changed).grid(row=3, column=1, padx=6, sticky="w")
        ui.muted(box, "The pad presses keys for this layout (QWERTZ swaps Z/Y). 'auto' follows this PC.").grid(row=3, column=2, padx=10, sticky="w")
        ctk.CTkButton(box, text="Sync time now", width=130, command=lambda: self.bg(lambda: self.dev.request(time_msg()),
                      lambda _: self.set_status("Clock synchronised"), "Time sync failed")).grid(row=4, column=1, padx=6, pady=8, sticky="w")
        self.notify_var = tk.BooleanVar(value=bool(self.cfg.get("notify", True)))
        ctk.CTkSwitch(box, text="Popup + sound when the pad connects", variable=self.notify_var,
                      command=lambda: (self.cfg.__setitem__("notify", self.notify_var.get()), save_config(self.cfg))).grid(row=5, column=0, columnspan=2, padx=6, pady=4, sticky="w")
        self.hm_var = tk.BooleanVar(value=bool(self.cfg.get("host_media_sync", True)) and self.hostmedia.available)
        hm = ctk.CTkSwitch(box, text="Mirror this PC's volume / playback on the pad", variable=self.hm_var,
                           command=lambda: (self.cfg.__setitem__("host_media_sync", self.hm_var.get()), save_config(self.cfg)))
        hm.grid(row=6, column=0, columnspan=3, padx=6, pady=4, sticky="w")
        if not self.hostmedia.available:
            hm.configure(state="disabled", text="Mirror PC volume: not available here (Linux: pactl/amixer, macOS: built in, Windows: pip install pycaw)")
        self.ledcpu_var = tk.BooleanVar(value=bool(self.cfg.get("led_cpu")))
        ctk.CTkSwitch(box, text="Pad LED follows this PC's CPU load (green to red)", variable=self.ledcpu_var,
                      command=lambda: (self.cfg.__setitem__("led_cpu", bool(self.ledcpu_var.get())), save_config(self.cfg))).grid(row=7, column=0, columnspan=3, padx=6, pady=4, sticky="w")
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        ctk.CTkSwitch(box, text="Start this app when I log in (minimised)", variable=self.autostart_var, command=self._autostart_toggled).grid(
            row=8, column=0, columnspan=3, padx=6, pady=4, sticky="w")
        self.tray_var = tk.BooleanVar(value=bool(self.cfg.get("tray")))
        self.tray_sw = ctk.CTkSwitch(box, text="Keep running in the system tray when the window is closed", variable=self.tray_var, command=self._tray_toggled)
        self.tray_sw.grid(row=9, column=0, columnspan=3, padx=6, pady=4, sticky="w")
        if not tray.available():
            self.tray_sw.configure(state="disabled", text="Tray icon: not available (pip install pystray)")

        box = self._card(sc, "Pad behaviour", "Dial acceleration, clock face, screensaver and night dimming - stored on the pad (firmware 1.3).")
        self.behaviour = padextras.BehaviourCard(self, box)
        box = self._card(sc, "Computer actions", "Settings for the key actions that act on this PC: the AI action, screenshots, clipboard history and window layouts.")
        self.computer_card = padextras.ComputerCard(self, box)
        box = self._card(sc, "Screens, reminders and habits", "Optional screens on the pad (stopwatch, breathing, dice, reaction test, snake, habits), which screens the dial cycles through, and nudges every N minutes (firmware 1.4).")
        self.screens_card = padextras.ScreensCard(self, box)

        # ---- firmware
        box = self._card(sc, "Firmware", "Version check, one-click update over USB, and an experimental Wi-Fi update.")
        self.fw_lbl = ctk.CTkLabel(box, text="pad not connected", anchor="w", font=ui.font(14, "bold"))
        self.fw_lbl.grid(row=0, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 2))
        self.fw_hint = ui.muted(box, f"This app ships firmware {FW_BUNDLED} (firmware/DeskCompanion.bin).", wraplength=860)
        self.fw_hint.grid(row=1, column=0, columnspan=4, sticky="w", padx=6)
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.grid(row=2, column=0, columnspan=4, sticky="w", pady=8)
        self.fw_btn = ctk.CTkButton(row, text=f"Update the pad to {FW_BUNDLED}", command=lambda: self.flash_firmware("full"))
        self.fw_btn.pack(side="left", padx=6)
        ui.secondary_button(row, "Flash CoreBringup (diagnostic)", lambda: self.flash_firmware("core")).pack(side="left", padx=6)
        ui.secondary_button(row, "Flash the Wi-Fi build", lambda: self.flash_firmware("wifi")).pack(side="left", padx=6)
        ui.secondary_button(row, "Flash another .bin...", self.flash_other).pack(side="left", padx=6)
        ui.muted(box, "Needs:  pip install esptool.  The pad is put into download mode automatically when it runs DeskCompanion; otherwise hold BOOT while plugging in USB.",
                 wraplength=860).grid(row=3, column=0, columnspan=4, sticky="w", padx=6)
        ctk.CTkLabel(box, text="Wi-Fi update (optional, experimental)", font=ui.font(13, "bold")).grid(row=4, column=0, columnspan=2, sticky="w", padx=6, pady=(14, 2))
        self.wifi_note = ui.muted(box, "", wraplength=860)
        self.wifi_note.grid(row=5, column=0, columnspan=4, sticky="w", padx=6)
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.grid(row=6, column=0, columnspan=4, sticky="w", pady=6)
        self.ota_pw = ctk.CTkEntry(row, width=170, show="*", placeholder_text="OTA password")
        self.ota_pw.pack(side="left", padx=6)
        ui.secondary_button(row, "Enable OTA on the pad", lambda: self.ota_enable(True), width=170).pack(side="left", padx=6)
        ui.secondary_button(row, "Disable", lambda: self.ota_enable(False), width=80).pack(side="left", padx=6)
        self.ota_btn = ctk.CTkButton(row, text="Update over Wi-Fi", width=150, command=self.ota_update)
        self.ota_btn.pack(side="left", padx=6)
        self.ota_bar = ctk.CTkProgressBar(box, width=420)
        self.ota_bar.set(0)
        self.ota_bar.grid(row=7, column=0, columnspan=3, sticky="w", padx=6, pady=(2, 4))
        self._wifi_widgets = [self.ota_pw, self.ota_btn] + [w for w in row.winfo_children() if isinstance(w, ctk.CTkButton)]

        # ---- backup
        box = self._card(sc, "Backup and restore", "Key maps of all layers, macros, profiles, info settings, app settings and your GIF library in one .zip.")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w")
        ctk.CTkButton(row, text="Back up everything...", command=self.backup_export).pack(side="left", padx=6)
        ui.secondary_button(row, "Restore from a backup...", self.backup_import).pack(side="left", padx=6)
        ui.secondary_button(row, "Export one macro...", self.macro_export).pack(side="left", padx=6)
        ui.secondary_button(row, "Import macros...", self.macro_import).pack(side="left", padx=6)
        ui.secondary_button(row, "Automatic backups...", lambda: wizards.AutoBackupDialog(self)).pack(side="left", padx=6)

        # ---- recovery + safety
        box = self._card(sc, "Recovery and safety", "If the pad boots into safe mode (red double-blink) or misbehaves.")
        self.safe_lbl = ctk.CTkLabel(box, text="pad not connected", anchor="w")
        self.safe_lbl.pack(anchor="w", padx=6, pady=(0, 6))
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w")
        ctk.CTkButton(row, text="Check pad state", width=130, command=self.recovery_check).pack(side="left", padx=6)
        ui.secondary_button(row, "Retry normal boot", lambda: self.recovery("safe_retry"), width=140).pack(side="left", padx=6)
        ui.secondary_button(row, "Boot without display", lambda: self.recovery("nodisp"), width=160).pack(side="left", padx=6)
        ui.secondary_button(row, "Re-enable display", lambda: self.recovery("disp"), width=140).pack(side="left", padx=6)
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w", pady=(8, 0))
        ui.danger_button(row, "Reset key maps on the pad", lambda: self.recovery("keys"), width=190).pack(side="left", padx=6)
        ui.danger_button(row, "Reset all pad settings", lambda: self.recovery("settings"), width=180).pack(side="left", padx=6)
        ui.danger_button(row, "Delete stored GIFs", lambda: self.recovery("gifs"), width=160).pack(side="left", padx=6)
        self.shell_var = tk.BooleanVar(value=bool(self.cfg.get("allow_shell")))
        ctk.CTkSwitch(box, text="Allow the pad to run shell commands on this PC (only commands in your own key maps; off by default)", variable=self.shell_var,
                      command=self._shell_toggled).pack(anchor="w", padx=6, pady=(12, 0))

        # ---- Wi-Fi
        wf = self._card(sc, "Optional Wi-Fi / NTP time sync", "The pad is a cable device and keeps its time through this app. Wi-Fi is an optional extra that "
                        "is compiled into the firmware only when DC_ENABLE_WIFI is 1 (top of DeskCompanion.ino).")
        self.ssid_var, self.pass_var = tk.StringVar(), tk.StringVar()
        e1 = ctk.CTkEntry(wf, textvariable=self.ssid_var, placeholder_text="SSID", width=200)
        e1.grid(row=0, column=0, padx=6, pady=6)
        e2 = ctk.CTkEntry(wf, textvariable=self.pass_var, placeholder_text="Password", show="*", width=200)
        e2.grid(row=0, column=1, padx=4)
        b1 = ctk.CTkButton(wf, text="Save Wi-Fi", command=lambda: self.bg(
            lambda: self.dev.request({"cmd": "wifi", "ssid": self.ssid_var.get(), "pass": self.pass_var.get()}),
            lambda _: self.set_status("Wi-Fi saved on the pad"), "Wi-Fi save failed"))
        b1.grid(row=0, column=2, padx=8)
        self._wifi_widgets += [e1, e2, b1]

        c = ctk.CTkFrame(sc)
        c.pack(fill="both", expand=True, pady=6, padx=2)
        ui.heading(c, "Activity").pack(anchor="w", padx=16, pady=(12, 4))
        self.log_box = ctk.CTkTextbox(c, height=220, state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _bright_changed(self, v):
        if self._bright_job:
            self.after_cancel(self._bright_job)
        val = int(v)
        self.pad.set_brightness(val)
        self._bright_job = self.after(250, lambda: self.dev.connected and self.bg(
            lambda: self.dev.request({"cmd": "brightness", "val": val}), None, "Brightness failed"))

    def _mode_changed(self, v):
        m = int(v.split()[0])
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


def _flash_helper(argv):
    """`DeskCompanion --flash-helper --image full ...`: run firmware/flash.py inside this (possibly frozen) interpreter."""
    base = APP_DIR / "firmware"
    sys.path.insert(0, str(base))
    import flash                                          # firmware/flash.py
    return flash.main(argv)


if __name__ == "__main__":
    if "--flash-helper" in sys.argv:
        sys.exit(_flash_helper([a for a in sys.argv[1:] if a != "--flash-helper"]))
    app = App()
    if autostart.wants_minimized():                           # started by "Start with my computer": go straight to the tray / taskbar
        if app.cfg.get("tray") and not app.tray_enable(True):
            app.withdraw()
        else:
            app.iconify()
    elif app.cfg.get("tray"):
        app.tray_enable(True)
    app.mainloop()
