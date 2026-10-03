"""Toolkit-free core of Desk Companion: constants, config, action library, GIF tools, the serial Device and the simulated pad,
host input back-ends, and the virtual pad renderer. Nothing in here imports a GUI toolkit, so it serves the Tk app, the Qt app and the tests."""
import base64
import colorsys
import ctypes
import ctypes.wintypes
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
import zlib
from pathlib import Path

import serial
from serial.tools import list_ports
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageSequence

APP_DIR = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent.parent)    # repo root, or the PyInstaller bundle folder
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))                                                          # desk_lib/ lives there
from desk_lib import i18n, autobackup, extras, scheduler, scripting, textops, tokens     # noqa: E402

APP_NAME = "Desk Companion"
APP_VERSION = "1.5.0"
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


def _cli_opt(name, argv=None):
    """Value of  --name VALUE  or  --name=VALUE  on the command line, else None."""
    argv = sys.argv[1:] if argv is None else argv
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


# several pads on one PC: start one app per pad,   --config pad2.json --port COM7 --api-port 8766
CONFIG_PATH = Path(_cli_opt("--config") or os.environ.get("DESK_COMPANION_CONFIG") or Path.home() / ".desk_companion.json")
FIXED_PORT = _cli_opt("--port")                  # only ever connect to this serial port
API_PORT_OVERRIDE = _cli_opt("--api-port")
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
        ("Layer 2 while the key is held (use as 'hold')", ("layer", "hold2")), ("Layer 3 while the key is held (use as 'hold')", ("layer", "hold3")),
        ("Lock / unlock the dial", ("fx", "dial_lock")), ("Next colour theme", ("fx", "theme_next")), ("Rotate the display", ("fx", "rot_next")),
        ("Next screen", ("fx", "mode_next")), ("Previous screen", ("fx", "mode_prev")), ("Display brighter", ("fx", "bright_up")), ("Display dimmer", ("fx", "bright_down")),
        ("Sticky Ctrl (for the next key)", ("fx", "latch_ctrl")), ("Sticky Shift (for the next key)", ("fx", "latch_shift")),
        ("Sticky Alt (for the next key)", ("fx", "latch_alt")), ("Sticky Win / Cmd (for the next key)", ("fx", "latch_gui")),
        ("Popup menu of this layer's keys", ("fx", "popup")), ("Window switcher: next (use on the dial)", ("fx", "switch_next")),
        ("Window switcher: previous (use on the dial)", ("fx", "switch_prev")),
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
                ("Run a plugin", "plugin", "name  or  name:argument  (Device page -> Plugins)", True),
                ("Show the album cover on the pad", "art", "", False), ("Show a QR code on the pad", "qr", "text or link  (empty = the clipboard)", False),
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
MODE_CHOICES = ["1 Clock", "2 Focus timer", "3 Media", "4 System", "5 GIF", "6 Info", "7 Stopwatch", "8 Breathing", "9 Dice & coin", "10 Reaction test", "11 Snake", "12 Habits",
                "13 Pong", "14 Breakout", "15 Flappy", "16 Game of Life", "17 Pixel pet", "18 Simon", "19 Diagnostics", "20 Sound bars"]


def valid_key(k):
    if not k:
        return False
    if len(k) == 1:
        return 32 < ord(k) < 127
    u = k.upper()
    return u in NAMED_KEYS or (u[0] == "F" and u[1:].isdigit() and 1 <= int(u[1:]) <= 24)


# ============================================================================ config
INFO_DEFAULTS = {"music": True, "weather": False, "event": False, "custom": False, "city": "", "lat": None, "lon": None, "label": "",
                 "fahrenheit": False, "ics": "", "rot": 6, "c_label": "", "c_t": "", "c_a": "", "c_b": "", "c_k": "c"}


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
    cfg.setdefault("language", "en")
    if cfg["language"] not in i18n.LANGS:
        cfg["language"] = "en"
    cfg.setdefault("tips", True)
    cfg.setdefault("tips_dismissed", [])
    cfg.setdefault("update_check", False)
    cfg.setdefault("plugins_on", False)
    cfg.setdefault("hotkey_on", False)
    cfg.setdefault("hotkey", "ctrl+alt+k")
    cfg.setdefault("appearance", "dark")         # dark | light | system
    if cfg["appearance"] not in ("dark", "light", "system"):
        cfg["appearance"] = "dark"
    if cfg.get("accent") not in tokens.ACCENTS:
        cfg["accent"] = "cyan"
    try:
        cfg["ui_scale"] = min(1.5, max(0.8, float(cfg.get("ui_scale", 1.0))))
    except (TypeError, ValueError):
        cfg["ui_scale"] = 1.0
    cfg.setdefault("profiles", [])               # per-program layer rules: {"name","match","kind","layer","enabled"}
    cfg.setdefault("profiles_on", False)
    cfg.setdefault("profile_default", 0)
    cfg.setdefault("allow_shell", False)         # may the pad run shell commands on this PC?  (off unless you switch it on)
    info = cfg.setdefault("info", {})            # info-screen feed settings
    for k, v in INFO_DEFAULTS.items():
        info.setdefault(k, v)
    cfg.setdefault("usage", {})                  # key press counters
    cfg.setdefault("dim_lock", False)            # dim the pad while the PC is locked
    cfg.setdefault("dim_fullscreen", False)      # dim the pad while a fullscreen window (video, game, presentation) is in front
    cfg.setdefault("dim_level", 25)
    cfg.setdefault("remember_layers", False)     # programs without a profile rule: the pad returns to the layer you last chose there
    cfg.setdefault("cliphist_on", False)         # remember the last copied texts (memory only)
    cfg.setdefault("shot_dir", "")               # screenshot action folder ("" = Pictures)
    ai = cfg.setdefault("ai", {})                # AI action: the user's own Anthropic API key (kept in this file in plain text) and model
    ai.setdefault("key", "")
    ai.setdefault("model", "claude-haiku-4-5-20251001")
    if not isinstance(cfg.get("layouts"), dict):
        cfg["layouts"] = {}                      # saved window layouts {name: [{process,title,x,y,w,h}]}
    cfg.setdefault("led_mood", False)            # the pad's LED follows the time of day
    cfg.setdefault("viz_on", False)              # send the sound spectrum to the pad's SOUND screen
    cfg.setdefault("led_audio", False)           # the pad's LED reacts to sound from the audio input
    cfg.setdefault("led_alerts", False)          # the LED blinks for new mail badges, a CI change, an upcoming event
    cfg.setdefault("image_slot", 3)              # GIF slot (0-based) that cover art / QR codes are written to
    cfg.setdefault("led_cpu", False)             # the pad's LED follows this computer's CPU load
    cfg.setdefault("tray", False)                # keep running in the system tray when the window is closed
    cfg.setdefault("counters", {})               # {counter:name} values used by snippets
    if not isinstance(cfg.get("script_history"), dict):
        cfg["script_history"] = {}               # earlier versions of scripts {name: [{"t","src"}]}
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
    cfg["gestures"] = {k: v for k, v in gs.items() if isinstance(v, dict) and "cat" in v and "action" in v and re.fullmatch(r"[0-2]:([1-5]:(hold|double|triple)|([89]|1[0-5]):press)", str(k))} \
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


_LABEL_SHORT = (("VOLUME ", "VOL "), ("PREVIOUS ", "PREV "), ("TRACK", "TRK"), ("NEXT ", "NXT "), ("WINDOW", "WIN"), ("SCREENSHOT", "SHOT"),
                ("DESKTOP", "DSK"), ("LAYER ", "L"), ("TOGGLE ", ""), ("ZOOM ", "Z "), ("TAB", "TAB"))


def short_label(name):
    """A key's action name as the pad's 8-character label (printable ASCII, no '|')."""
    t = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 /+.&-]", "", str(name))).upper().strip()
    for a, b in _LABEL_SHORT:
        t = t.replace(a, b)
    return t.strip()[:8].strip()


def labels_for(cfg, layer):
    """The seven labels (K1-K5, dial right / left) of one layer, from the action names in the key map."""
    lm = cfg["layers"][layer]
    return [short_label(lm[str(s)]["action"]) for s in range(1, 8)]


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
    "no_previous": "the pad has no previous firmware stored (a USB update replaces it; only a Wi-Fi update keeps the old one)",
    "rollback": "the pad could not switch back to the other firmware slot", "labels": "key names are at most 8 plain characters each (seven per layer)",
    "level": "the dim level must be 0 (off) or 5-255", "chord": "chords are 1-4 (K1+K2 ... K4+K5)", "dclick": "dial clicks are 1-3",
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
FX_NAMES = ("dial_lock", "theme_next", "rot_next", "mode_next", "mode_prev", "bright_up", "bright_down",
            "latch_ctrl", "latch_shift", "latch_alt", "latch_gui", "popup", "switch_next", "switch_prev")      # pad functions (type "fx"), same list as the firmware
HOST_OPS = ("url", "app", "shell", "clipboard", "file", "notify", "snippet", "clip", "script",
            "appvol", "dnd", "audio_out", "mic", "shot", "translate", "ai", "webhook", "layout", "cliphist", "plugin", "art", "qr")
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
        return x in ("next", "prev", "hold1", "hold2", "hold3") or (isinstance(x, int) and not isinstance(x, bool) and 0 <= x < LAYERS)
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
    if t == "fx":
        return v in FX_NAMES
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
            elif "fx" in o:
                if o["fx"] not in FX_NAMES:
                    return False
            elif "layer" in o:
                if not layer_ok(o["layer"]):
                    return False
            else:
                return False
        return True
    return False


NEW13_CAPS = ["hostx", "gestures", "dialaccel", "clockstyle", "saver", "nightdim"]       # firmware 1.3 additions (see hello "caps")
NEW14_CAPS = ["screens", "pressturn", "toggle", "wheelmods", "ledfx", "reminders", "habits"]     # firmware 1.4 additions
NEW15_CAPS = ["hostx2", "dimcmd", "themes", "fx", "chords", "tapdance", "dialclicks", "keyrepeat", "games", "pet", "diag", "viz", "labels", "bootlog", "rollback",
              "cards2", "saver2", "clock2", "display2", "konami"]                                           # firmware 1.5 additions (see hello "caps")
SETTINGS_DEFAULT = {"dial_accel": 0, "clock_style": 0, "saver_s": 0, "saver_style": 1, "night_on": False, "night_from": 22, "night_to": 7, "night_level": 30, "mode_mask": 0x3F}
SETTINGS_RANGE = {"dial_accel": (0, 2), "clock_style": (0, 3), "saver_s": (0, 3600), "saver_style": (1, 3), "night_from": (0, 23), "night_to": (0, 23), "night_level": (5, 255), "mode_mask": (1, 4095)}
SETTINGS15_DEFAULT = {"theme": 0, "tint": False, "rotation": 0, "pixel_shift": False, "fade": False, "boot_anim": True, "splash": "", "detent_led": False, "key_toast": False,
                      "repeat_mask": 0, "dial_lock": False, "host_dim": 0, "pomo_today": 0}
SETTINGS15_RANGE = {"clock_style": (0, 5), "saver_style": (1, 7), "mode_mask": (1, 0xFFFFF), "theme": (0, 6), "rotation": (0, 3), "repeat_mask": (0, 31)}
SETTINGS15_BOOL = ("tint", "pixel_shift", "fade", "boot_anim", "detent_led", "key_toast", "dial_lock")
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
        self.dim = 0                          # temporary dim level (0 = off) set by {"cmd":"dim"}
        self.viz, self.viz_at = [0] * 8, 0.0
        self._momentary = None                # the layer to return to when the key that held "layer while held" is released
        self.labels = [[""] * 7 for _ in range(LAYERS)]
        self.rst_counts = [1, 0, 0, 0, 0, 0]
        self.settings15 = dict(SETTINGS15_DEFAULT)
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
            if "fx" in st:
                self._run_fx(st["fx"])
            elif "layer" in st:
                x = st["layer"]
                if isinstance(x, str) and x.startswith("hold"):                    # "layer while held": back again on {"input","g":"release"}
                    if self._momentary is None:
                        self._momentary = self.layer
                    self.layer = int(x[4]) - 1
                else:
                    self.layer = (self.layer + 1) % LAYERS if x == "next" else (self.layer - 1) % LAYERS if x == "prev" else int(x)
                self._send({"evt": "layer", "n": self.layer})
            elif "host" in st:
                self.host_log.append(st["host"])
                self._send({"evt": "host", "op": st["host"]["op"], "arg": st["host"].get("arg", "")})

    def _modes(self):
        return 6 if (self.fw12 or self.fw13) else 12 if self.fw14 else NUM_MODES

    def _fw15(self):
        return not (self.fw12 or self.fw13 or self.fw14)

    def _max_key(self):
        return 7 if (self.fw12 or self.fw13) else 9 if self.fw14 else 15

    def _gestures(self):
        return ("tap", "hold", "double", "triple") if self._fw15() else ("tap", "hold", "double")

    def _run_fx(self, name):
        """The pad functions that change something the app can see (settings, brightness, screen); latch / popup / switcher have no visible state here."""
        s15 = self.settings15
        if name == "dial_lock":
            s15["dial_lock"] = not s15["dial_lock"]
        elif name == "theme_next":
            s15["theme"] = (s15["theme"] + 1) % 7
        elif name == "rot_next":
            s15["rotation"] = (s15["rotation"] + 1) % 4
        elif name in ("mode_next", "mode_prev"):
            d = 1 if name == "mode_next" else -1
            for i in range(1, NUM_MODES + 1):
                c = (self.mode - 1 + d * i) % NUM_MODES + 1
                if (self.settings["mode_mask"] >> (c - 1)) & 1:
                    self.mode = c
                    break
        elif name in ("bright_up", "bright_down"):
            self.bright = max(5, min(255, self.bright + (24 if name == "bright_up" else -24)))

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
        if (self.fw12 and cmd in ("settings", "gesture_test")) or ((self.fw12 or self.fw13) and cmd in ("screens", "habits", "reminders")) or \
                ((self.fw12 or self.fw13 or self.fw14) and cmd in ("dim", "labels", "boot_log", "rollback", "viz")):
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
                   "layer": self.layer, "layers": LAYERS, "modes": self._modes(), "gifs": len(self.gifs), "gif_rot": self.gif_rot,
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
                if g == "release" and self._fw15():
                    if self._momentary is not None:
                        self.layer, self._momentary = self._momentary, None
                        self._send({"evt": "layer", "n": self.layer})
                elif g == "tap":
                    self._run_spec(self._spec_for(self.layer, k))
                elif g in self._gestures()[1:]:
                    sp = self.gest.get((self.layer, k, g))
                    if sp:
                        self._run_spec(sp)
                else:
                    reply({"ok": False, "err": "gesture"})
                    return
            elif "chord" in msg and self._fw15():
                p = msg["chord"]
                if not isinstance(p, int) or isinstance(p, bool) or not 1 <= p <= 4:
                    reply({"ok": False, "err": "chord"})
                    return
                if (9 + p) in self.layers[self.layer]:
                    self._run_spec(self.layers[self.layer][9 + p])
            elif "dclick" in msg and self._fw15():
                n = msg["dclick"]
                if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 3:
                    reply({"ok": False, "err": "dclick"})
                    return
                lay = self.layers[self.layer]
                if n >= 3 and 15 in lay:
                    self._run_spec(lay[15])
                elif n >= 2 and 14 in lay:
                    self._run_spec(lay[14])
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
                if self._fw15() and self.settings15["dial_lock"]:
                    runs = 0
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
                if not isinstance(sl, int) or not 1 <= sl <= self._max_key():
                    reply({"ok": False, "err": "key"})
                    return
                if sl > 7:
                    sp = self.layers[lay].get(sl)
                    reply(dict(base, s=sl, **{"def": sp is None}, **({"spec": sp} if sp else {})))
                    return
                g = msg.get("gesture", "tap")
                if g != "tap" and not self.fw12:
                    if g not in self._gestures()[1:] or sl > 5:
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
                    if self._fw15():
                        slots[-1]["t"] = (lay, i, "triple") in self.gest
            if not (self.fw12 or self.fw13):
                base["pt"] = [8 in self.layers[lay], 9 in self.layers[lay]]
            if self._fw15():
                base["ch"] = [(10 + p) in self.layers[lay] for p in range(4)]
                base["dc"] = [14 in self.layers[lay], 15 in self.layers[lay]]
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
            if not isinstance(key, int) or not 1 <= key <= self._max_key():
                reply({"ok": False, "err": "key"})
            elif not isinstance(lay, int) or not 0 <= lay < LAYERS:
                reply({"ok": False, "err": "layer"})
            elif not self.fw12 and msg.get("gesture", "tap") not in self._gestures():
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
        elif cmd == "dim":
            if "level" in msg:
                v = msg["level"]
                if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 255:
                    reply({"ok": False, "err": "level"})
                    return
                self.dim = 0 if v == 0 else max(5, v)
            elif "on" in msg:
                if not isinstance(msg["on"], bool):
                    reply({"ok": False, "err": "on"})
                    return
                self.dim = (self.dim or 25) if msg["on"] else 0
            reply({"ok": True, "evt": "dim", "level": self.dim, "bl": min(self.bright, self.dim) if self.dim else self.bright})
        elif cmd == "viz":
            v = msg.get("v")
            if isinstance(v, list):
                self.viz = [max(0, min(100, int(x))) for x in v[:8] if isinstance(x, (int, float))]
                self.viz_at = time.monotonic()
            return                                                      # no reply, like the firmware (sent several times a second)
        elif cmd == "labels":
            lay = msg.get("layer", 0)
            if not isinstance(lay, int) or not 0 <= lay < LAYERS:
                reply({"ok": False, "err": "layer"})
                return
            if "l" in msg:
                lst = msg["l"]
                if not (isinstance(lst, list) and len(lst) == 7 and all(isinstance(x, str) and len(x.strip()) <= 8 and "|" not in x and x.isascii() and x.isprintable() for x in lst)):
                    reply({"ok": False, "err": "labels"})
                    return
                self.labels[lay] = [x.strip() for x in lst]
            reply({"ok": True, "evt": "labels", "layer": lay, "l": self.labels[lay]})
        elif cmd == "boot_log":
            if msg.get("clear"):
                self.rst_counts = [0] * 6
            reply({"ok": True, "evt": "boot_log", "log": "1:prefs=ok;2:usb=ok;3:display=ok;4:ready=ok;", "reset": "power-on", "crashes": self.crashes, "disp_why": self.disp_why,
                   "counts": self.rst_counts, "usb_connects": 1, "usb_drops": 0, "up_ms": int((time.monotonic() - self.t0) * 1000), "heap": 210_000, "heap_min": 180_000})
        elif cmd == "rollback":
            if not msg.get("confirm"):
                reply({"ok": False, "err": "confirm"})
                return
            reply({"ok": False, "err": "no_previous"})
        elif cmd == "screens":
            reply({"ok": True, "evt": "screens", "mask": self.settings["mode_mask"], "reminder_active": False,
                   **({"dial_lock": self.settings15["dial_lock"], "viz_age": int((time.monotonic() - self.viz_at) * 1000) if self.viz_at else 0} if self._fw15() else {})})
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
            if not (self.fw12 or self.fw13 or self.fw14):                       # firmware 1.5: wider ranges and the new fields (all validated before anything changes)
                new = {}
                for k, v in msg.items():
                    if k in ("cmd", "id"):
                        continue
                    if k == "night_on" or k in SETTINGS15_BOOL:
                        if not isinstance(v, bool):
                            reply({"ok": False, "err": "settings"})
                            return
                        new[k] = v
                    elif k == "splash":
                        if not isinstance(v, str) or len(v.strip()) > 12 or not v.isascii() or not (v.strip() == "" or v.strip().isprintable()):
                            reply({"ok": False, "err": "settings"})
                            return
                        new[k] = v.strip()
                    elif k in SETTINGS15_RANGE or k in SETTINGS_RANGE:
                        lo, hi = SETTINGS15_RANGE.get(k) or SETTINGS_RANGE[k]
                        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                            reply({"ok": False, "err": "settings"})
                            return
                        new[k] = v
                for k, v in new.items():
                    (self.settings if k in self.settings else self.settings15)[k] = v
                if "dial_lock" in new:
                    self.settings15["dial_lock"] = new["dial_lock"]
                self.settings15["host_dim"] = self.dim
                reply(dict({"ok": True, "evt": "settings", "bl": min(self.bright, self.dim) if self.dim else self.bright, "saver_on": False}, **self.settings, **self.settings15))
                return
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
            if 1 <= v <= self._modes():
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
        return ("switch to the next layer" if v == "next" else "switch to the previous layer" if v == "prev" else f"layer {v[4]} while the key is held" if str(v).startswith("hold")
                else f"switch to layer {int(v) + 1}")
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

    def moveto(self, x, y):
        raise ValueError("moving the pointer to a screen position is not available on this system")

    def clickat(self, x, y, btn="left"):
        self.moveto(x, y)
        self.mouse({"btn": btn, "act": "click"})

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

    def moveto(self, x, y):
        if not self.u.SetCursorPos(int(x), int(y)):
            raise OSError("Windows refused to move the pointer")

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

    def moveto(self, x, y):
        self.ms.position = (int(x), int(y))

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
NUM_MODES = 20                                   # 1-6 classic screens, 7-20 optional ones that run on the pad (firmware 1.4 / 1.5)
MODE_NAMES = ["", "CLOCK", "FOCUS", "MEDIA", "SYSTEM", "GIF", "INFO", "STOPWATCH", "BREATHE", "DICE", "REACTION", "SNAKE", "HABITS",
              "PONG", "BREAKOUT", "FLAPPY", "LIFE", "PET", "SIMON", "DIAGNOSTICS", "SOUND"]
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
            kind = c.get("k", "c")
            try:
                val = max(0, min(100, int(str(c.get("t", "0")).strip() or 0)))
            except ValueError:
                val = 0
            if kind == "r":                                                    # ring: value 0..100 as a gauge
                g.gauge(120, 112, 70, 56, val / 100.0, C_RED if val > 85 else C_WARN if val > 65 else C_ACC)
                g.text(f"{val}%", 120, 112, 26, C_TXT, bold=True)
                g.text(fit(c.get("a", ""), 20), 120, 156, 13, C_TXT)
                g.text(fit(c.get("b", ""), 20), 120, 172, 13, C_GRAY)
            elif kind == "p":                                                  # progress bar
                g.text(f"{val}%", 120, 92, 26, C_ACC, bold=True)
                g.d.rounded_rectangle([40 * SS, 116 * SS, 200 * SS, 132 * SS], radius=8 * SS, fill=C_DIM)
                if val:
                    g.d.rounded_rectangle([40 * SS, 116 * SS, (40 + max(16, val * 160 // 100)) * SS, 132 * SS], radius=8 * SS, fill=C_ACC)
                g.text(fit(c.get("a", ""), 20), 120, 148, 13, C_TXT)
                g.text(fit(c.get("b", ""), 20), 120, 168, 13, C_GRAY)
            elif kind == "s":                                                  # scrolling text (the twin shows the start of it)
                g.text(fit(c.get("t", ""), 22), 120, 98, 20, C_ACC2, bold=True)
                g.text(fit(c.get("a", ""), 20), 120, 136, 13, C_TXT)
                g.text(fit(c.get("b", ""), 20), 120, 156, 13, C_GRAY)
            else:
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
        """Screens 7-20 are drawn by the pad itself (stopwatch, games, pet, diagnostics ...): the twin shows a card."""
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


