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
import copy
from datetime import datetime
import gc
import io
import json
import math
import os
import platform
import subprocess
import sys
import queue
import re
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
from PIL import Image, ImageDraw, ImageTk

sys.path.insert(0, str(Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)))    # desk_lib/ and core/ live there
from desk_lib import ui                                              # noqa: E402
from desk_lib import appcard, audio, diag, sharecode, hotkey, ledfx, padimage, history, i18n, plugins, tips, updates, activewin, automation, autostart, backup, bridge, cliphist, espota, extras, feeds, hostactions, netactions, padextras, presets, recorder, scheduler, screenstate, scripting, scripts_page, sysactions, textops, tray, winlayout, wizards     # noqa: E402
from desk_lib.ui import (CARD2, CARD3, ERR, FAINT, MUTED, OK, PINK, TEXT, WARN, SideTabs, Pill)   # noqa: E402

ACCENT = ui.ACCENT                                                   # rebound in App.__init__ when the user picked another accent colour

# the toolkit-free part lives in core/base.py; everything is re-exported here so `companion_app.X` keeps working
from core.base import (  # noqa: E402
    ACTIONS,
    ACTION_INDEX,
    ACTION_KINDS,
    API_PORT_OVERRIDE,
    APP_DIR,
    APP_NAME,
    APP_VERSION,
    ARROW_L,
    ARROW_R,
    ARROW_RAD,
    BOX,
    CAT_COLORS,
    CONFIG_PATH,
    DEFAULT_LAYER_MAPS,
    DISP,
    Device,
    DeviceError,
    ENC_C,
    ENC_R,
    FIXED_PORT,
    FW_BUNDLED,
    GIF_PRESETS,
    GIF_SLOTS,
    HOST_OS,
    HostInput,
    HostMedia,
    KEY_CHOICES,
    KEY_EDGE,
    KEY_FILL,
    KEY_H,
    KEY_ON,
    KEY_W,
    KEY_XS,
    KEY_Y,
    LAYERS,
    LAYER_NAMES,
    LAYOUTS,
    LCD,
    LONG_MS,
    MEDIA_CHOICES,
    MODE_CHOICES,
    MODIFIERS,
    M_GIF,
    M_INFO,
    NUM_MODES,
    ONLINE_PROVIDERS,
    PAGES,
    RESAMPLE,
    RISKY,
    SCR_C,
    SIM_PORT,
    SLOT_LABELS,
    VP_H,
    VP_W,
    VirtualPad,
    Win32Api,
    WinFocus,
    _http_get,
    autobackup_for,
    build_preset,
    candidate_ports,
    compact_json,
    describe_spec,
    detect_layout,
    fit_gif,
    fmt_vidpid,
    gif_file_thumb,
    gif_lib_dir,
    labels_for,
    lib_add,
    lib_delete,
    lib_list,
    list_serial_ports,
    load_config,
    load_gif_frames,
    make_body_image,
    normalize_config,
    online_download,
    online_search,
    preset_thumb,
    resolve_spec,
    round_thumb,
    save_config,
    spec_json,
    spec_needs_focus,
    spec_ok,
    time_msg,
    valid_key,
)
from core.base import (  # noqa: E402,F401  (re-exported for the tests and other callers)
    FX_NAMES,
    HOST_OPS,
    NEW13_CAPS,
    NEW14_CAPS,
    NEW15_CAPS,
    _cli_opt,
    short_label,
)
_REEXPORT = (FX_NAMES, HOST_OPS, NEW13_CAPS, NEW14_CAPS, NEW15_CAPS, _cli_opt, short_label)


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
                          ("Copy diagnostic report", self.report), ("Export report .zip", self.export_zip)):
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
        self.mirror_var, self._mirror_busy = tk.BooleanVar(value=False), False
        ctk.CTkSwitch(r, text="Live mirror (every 3 s)", variable=self.mirror_var, command=self.mirror_toggle).pack(side="left", padx=12)
        self.snap_canvas = tk.Canvas(c, width=240, height=240, bg="#111", highlightthickness=1, highlightbackground="#333")
        self.snap_canvas.pack(pady=6)
        ctk.CTkLabel(c, text="Test patterns stay for 8 s. A screenshot shows the firmware's frame buffer (not GIF mode).", text_color=MUTED).pack(anchor="w", padx=10, pady=(0, 6))

    def snapshot(self):
        if not self.app.dev.connected:
            return self.app.set_status("Not connected", error=True)
        self.sys("downloading screen snapshot ...")
        self.app.bg(lambda: self.app.dev.snapshot(), self._show_snap, "Snapshot failed")

    def _show_snap(self, img, quiet=False):
        self._snap_img = ImageTk.PhotoImage(img)
        self.snap_canvas.delete("all")
        self.snap_canvas.create_image(0, 0, anchor="nw", image=self._snap_img)
        if not quiet:
            self.sys("snapshot received")

    def mirror_toggle(self):
        if self.mirror_var.get():
            self._mirror_tick()

    def _mirror_tick(self):
        """Live view of the pad's real screen: one snapshot every 3 s while the switch is on (not while the pad is busy)."""
        if not self.mirror_var.get() or self.app.closing:
            return
        if self.app.dev.connected and not self.app.dev.busy and not self._mirror_busy:
            self._mirror_busy = True

            def fin():
                self._mirror_busy = False
            self.app.bg(lambda: self.app.dev.snapshot(), lambda img: (fin(), self._show_snap(img, quiet=True)), "Mirror stopped", fail=lambda: (fin(), self.mirror_var.set(False)))
        self.app.after(3000, self._mirror_tick)

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

    def _report_text(self):
        d = self.app.dev
        out = [f"DeskCompanion diagnostic report  {time.strftime('%Y-%m-%d %H:%M:%S')}",
               f"app: {APP_NAME}  python {platform.python_version()}  {platform.platform()}",
               f"pyserial {getattr(serial, '__version__', '?')}", "", "serial ports:"]
        for p in list_serial_ports():
            out.append(f"  {p['device']:10s} {fmt_vidpid(p)}  {p['desc']}  [{p['mfr']}] {'<-- ESP32' if p['esp'] else ''}")
        out += ["", f"connected: {d.connected}  port: {d.port}  rx {d.rx_bytes} B  tx {d.tx_bytes} B", "hello:", f"  {d.info}", "info:"]
        out += ["  " + ln for ln in info_lines(self.last_info)] if self.last_info else ["  (not fetched - press 'Device info')"]
        out += ["", "last terminal lines:"] + ["  " + ln for ln in self.lines[-150:]]
        return "\n".join(out)

    def report(self):
        text = self._report_text()
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

    def export_zip(self, path=None):
        """report.txt + the settings with every secret removed + (firmware 1.5) the pad's boot log, in one .zip to attach to a bug report."""
        from tkinter import filedialog                          # noqa: PLC0415
        path = path or filedialog.asksaveasfilename(title="Save the diagnostic report", defaultextension=".zip", initialfile="deskcompanion_report.zip",
                                                    filetypes=[("Zip file", "*.zip")])
        if not path:
            return
        d = self.app.dev

        def work():
            files = {"report.txt": self._report_text(), "settings_redacted.json": json.dumps(diag.redact(self.app.cfg), indent=1, default=str)}
            if d.connected and self.app._pad_cap("bootlog"):
                try:
                    files["pad_boot_log.json"] = json.dumps(d.request({"cmd": "boot_log"}), indent=1)
                except DeviceError as e:
                    files["pad_boot_log.json"] = f"unavailable: {e}"
            Path(path).write_bytes(diag.build_zip(files))
            return path
        self.app.bg(work, lambda p: self.app.set_status(f"Report saved to {p} (settings are included with passwords, tokens and API keys removed)"), "Could not save the report")


class _PluginApi:
    """What a plugin gets as `api` (see desk_lib/plugins.py)."""

    def __init__(self, app):
        self._app = app

    def type_text(self, text):
        self._app._type_text(str(text))

    def notify(self, title, text):
        self._app.post(lambda: self._app.notify(str(title), str(text), "ok"))

    def clipboard(self):
        return self._app._clipboard_text()

    def set_card(self, label, title, a="", b=""):
        self._app.set_custom_card(label, title, a, b)


class App(ctk.CTk):
    def __init__(self):
        self.cfg = load_config()
        global ACCENT
        i18n.set_language(self.cfg.get("language", "en"))
        ui.set_accent(self.cfg.get("accent", "cyan"))                             # before the theme file is written
        ACCENT = ui.ACCENT
        ui.install_theme()
        ctk.set_widget_scaling(float(self.cfg.get("ui_scale", 1.0)))
        super().__init__()
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
        self.game_mode, self._app_layers = False, {}
        self.screen = screenstate.ScreenState()
        self._dim = {"locked": None, "fs": None, "applied": None, "saved": None, "tick": time.monotonic()}
        self.hostact = hostactions.HostActions(self._allowed_host, lambda: bool(self.cfg.get("allow_shell")),
                                               type_clipboard=self._type_clipboard, type_text=self._type_text,
                                               read_clipboard=self._clipboard_text, counter=self._next_counter, script_runner=self.run_script,
                                               sysact=sysactions.SysActions(), layouts=winlayout.WinLayouts(), layout_store=lambda: self.cfg["layouts"], cliphist=self.cliphist,
                                               cfg_get=lambda k, d=None: self.cfg.get(k, d), focus_program=lambda: (self.active_win.get()[0] or ""),
                                               notify=lambda t, m: self.post(lambda: self.notify(t, m, "ok")), plugin_runner=self._run_plugin, pad_image=self.pad_image)
        self.plugins = plugins.PluginHost(CONFIG_PATH.parent / (CONFIG_PATH.name + ".plugins"), api=_PluginApi(self))
        if self.cfg.get("plugins_on"):
            self.plugins.load()
        self.hotkey_obj = None
        self.spectrum, self.alerts, self._alert_snap = audio.Spectrum(), ledfx.AlertTracker(), {}
        self.history = history.EditHistory()
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
        self.bind_all("<Control-z>", lambda e: self._undo_key(e, False))
        self.bind_all("<Control-y>", lambda e: self._undo_key(e, True))
        self.bind_all("<Control-Shift-Z>", lambda e: self._undo_key(e, True))
        self.padview.refresh()
        self.refresh_library()
        self.recompute_pending()
        self.history.record(self._edit_snapshot())
        gc.disable()                                          # Tk objects (fonts) must be freed on the main thread: a collection inside a worker thread can deadlock
        self.after(2000, self._gc_tick)
        if self.cfg.get("hotkey_on"):
            self.hotkey_set(True, self.cfg["hotkey"])
        if self.cfg.get("update_check"):
            self.after(4000, self._startup_update_check)
        self.refresh_tip()
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
        threading.Thread(target=self._audio_led_loop, daemon=True).start()
        threading.Thread(target=self._screen_loop, daemon=True).start()
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
        want_cpu, want_mood = bool(self.cfg.get("led_cpu")), bool(self.cfg.get("led_mood"))
        on, last = (want_cpu or want_mood) and not self.cfg.get("led_audio"), getattr(self, "_led_cpu_last", None)
        if self.dev.info.get("core_only") or (self.cfg.get("led_audio") and last is None):
            return
        if not on:
            if last is not None:
                self._led_cpu_last = None
                self.dev.request({"cmd": "led", "mode": "auto"}, timeout=2)
            return
        rgb = padextras.cpu_color(cpu) if want_cpu else ledfx.mood_color()
        if last is None or max(abs(a - b) for a, b in zip(rgb, last[0])) > 14 or time.time() - last[1] > 30:
            self.dev.request({"cmd": "led", "r": rgb[0], "g": rgb[1], "b": rgb[2]}, timeout=2)
            self._led_cpu_last = (rgb, time.time())

    def _audio_led_loop(self):
        """Sound-reactive pad: the LED colour (about 8 updates a second) and / or the SOUND screen's bars, from the audio input (needs sounddevice + numpy)."""
        running, last, last_viz = False, None, 0.0
        while not self.closing:
            time.sleep(0.12)
            live = self.dev.connected and not self.dev.info.get("core_only")
            want_led, want_viz = bool(self.cfg.get("led_audio")) and live, bool(self.cfg.get("viz_on")) and live and self._pad_cap("viz")
            if not (want_led or want_viz):
                if running:
                    self.spectrum.stop()
                    running = False
                    if self.dev.connected and last is not None:
                        try:
                            self.dev.request({"cmd": "led", "mode": "auto"}, timeout=2)
                        except DeviceError:
                            pass
                last = None
                continue
            if not running:
                try:
                    self.spectrum.start()
                    running = True
                except ValueError as e:
                    self.cfg["led_audio"] = self.cfg["viz_on"] = False
                    self.post(lambda e=e: (self.set_status(str(e), error=True), self.ledaudio_var.set(False), self.viz_var.set(False)))
                    continue
            if want_led:
                rgb = audio.color(self.spectrum.current())
                if rgb != last and not self.dev.busy:
                    try:
                        self.dev.send({"cmd": "led", "r": rgb[0], "g": rgb[1], "b": rgb[2]})
                        last = rgb
                    except DeviceError:
                        pass
            elif last is not None:
                try:
                    self.dev.request({"cmd": "led", "mode": "auto"}, timeout=2)
                except DeviceError:
                    pass
                last = None
            if want_viz and not self.dev.busy and time.monotonic() - last_viz >= 0.12:
                last_viz = time.monotonic()
                try:
                    self.dev.send({"cmd": "viz", "v": self.spectrum.current_bars()})
                except DeviceError:
                    pass
        self.spectrum.stop()

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
            ports = [p["device"] for p in allp if p["esp"] and (not FIXED_PORT or p["device"] == FIXED_PORT)]
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
        self.set_appearance("light" if self.theme_var.get() else "dark")

    def set_appearance(self, mode):
        """dark | light | system"""
        mode = mode if mode in ("dark", "light", "system") else "dark"
        self.cfg["appearance"] = mode
        save_config(self.cfg)
        ctk.set_appearance_mode(mode)
        if hasattr(self, "theme_var"):
            self.theme_var.set(ctk.get_appearance_mode() == "Light")
        self._restyle_plain_widgets()

    def version_string(self):
        return APP_VERSION

    # ---- plugins, global hotkey, updates, tips, undo/redo
    def _run_plugin(self, spec):
        if not self.cfg.get("plugins_on"):
            raise ValueError("plugins are switched off (Device page -> Plugins)")
        return self.plugins.run(spec)

    def hotkey_set(self, on, combo):
        """Returns '' or the reason it is not possible."""
        if self.hotkey_obj is not None:
            self.hotkey_obj.stop()
            self.hotkey_obj = None
        if not on:
            return ""
        try:
            self.hotkey_obj = hotkey.GlobalHotkey(combo, lambda: self.post(self._hotkey_fired))
            self.hotkey_obj.start()
        except Exception as e:                                # noqa: BLE001  (no pynput, no display, Wayland ...)
            self.hotkey_obj = None
            return f"the global hotkey is not possible here: {e}"
        return ""

    def pad_image(self, kind, arg=""):
        """Cover art of the playing song (kind 'art') or a QR code ('qr', arg or clipboard) -> a spare GIF slot of the pad, shown right away."""
        if not self.dev.connected:
            raise ValueError("the pad is not connected")
        if "gifslots" not in (self.dev.info.get("caps") or []):
            raise ValueError("this firmware has no extra GIF slots (update it)")
        if kind == "art":
            img = padimage.square(padimage.fetch_image(padimage.art_url()))
        else:
            img = padimage.qr_image(arg.strip() or self._clipboard_text())
        slot = max(1, min(GIF_SLOTS - 1, int(self.cfg.get("image_slot", 3))))
        self.dev.upload_gif(padimage.to_gif(img), None, slot)
        self.dev.request({"cmd": "gif_cfg", "slot": slot})
        self.post(lambda: (self.pad.set_mode(M_GIF), self.refresh_pad_gifs()))
        return f"{'cover art' if kind == 'art' else 'QR code'} sent to GIF slot {slot + 1}"

    def _hotkey_fired(self):
        self.show_window()
        self.open_palette()

    def _startup_update_check(self):
        def done(r):
            if r["newer"]:
                self.set_status(r["message"] + "  -  Device page -> Updates and tips")
                self.notify("Desk Companion update", r["message"], "ok")
        self.bg(lambda: updates.check(APP_VERSION), done, "Update check failed")

    def refresh_tip(self):
        if not hasattr(self, "tip_card"):
            return
        t = tips.pick(self.cfg, self.dev.info.get("caps") or [] if self.dev.connected else [], self.cfg["tips_dismissed"], seed=int(time.time() // 86400)) \
            if self.cfg.get("tips", True) else None
        if t is None:
            self.tip_card.pack_forget()
            self._tip_id = None
            return
        self._tip_id = t[0]
        self.tip_lbl.configure(text="Tip:  " + t[1])
        self.tip_card.pack(fill="x", padx=2, pady=(0, 8), before=self.home_first)

    def dismiss_tip(self):
        if getattr(self, "_tip_id", None):
            self.cfg["tips_dismissed"].append(self._tip_id)
            save_config(self.cfg)
        self.refresh_tip()

    def _edit_snapshot(self):
        return {"layers": copy.deepcopy(self.cfg["layers"]), "custom": copy.deepcopy(self.cfg["custom"])}

    def _undo_key(self, event, redo):
        w = str(event.widget.winfo_class()) if hasattr(event.widget, "winfo_class") else ""
        if w in ("Entry", "Text", "TEntry", "TCombobox", "Spinbox"):
            return None                                      # let a text field undo its own typing
        self.edit_redo() if redo else self.edit_undo()
        return "break"

    def edit_undo(self):
        st = self.history.undo()
        self._edit_apply(st, "Undone") if st else self.set_status("Nothing to undo")

    def edit_redo(self):
        st = self.history.redo()
        self._edit_apply(st, "Redone") if st else self.set_status("Nothing to redo")

    def _edit_apply(self, state, word):
        for n in range(LAYERS):                              # in place: cfg["map"] is one of these dicts
            lm = self.cfg["layers"][n]
            lm.clear()
            lm.update(copy.deepcopy(state["layers"][n]))
        self.cfg["custom"].clear()
        self.cfg["custom"].update(copy.deepcopy(state["custom"]))
        save_config(self.cfg)
        self.refresh_action_lists()
        self.refresh_library()
        self.padview.refresh()
        self.recompute_pending()
        if self.autoup_var.get() and self.dev.connected:
            self.upload_slots(list(range(1, 8)))
        self.set_status(f"{word}: key assignments")

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
        self.history.record(self._edit_snapshot())
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
                if sl.get("t"):
                    existing.add((lay, sl["s"], "triple"))
            for i, flag in enumerate(r.get("pt") or []):                   # dial pressed + turned right / left (firmware 1.4)
                if flag:
                    existing.add((lay, 8 + i, "press"))
            for i, flag in enumerate(r.get("ch") or []):                   # chords K1+K2 ... K4+K5 (firmware 1.5)
                if flag:
                    existing.add((lay, 10 + i, "press"))
            for i, flag in enumerate(r.get("dc") or []):                   # dial double / triple click (firmware 1.5)
                if flag:
                    existing.add((lay, 14 + i, "press"))
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
            self._push_labels([layer])
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
            self._push_labels()
            self.dev.request({"cmd": "brightness", "val": bright})
            self.dev.request({"cmd": "mode", "val": mode})
            self.post(lambda: self._mark_display_pushed(mode, bright))
            bad = self._verify_keys_sync()
            self._healed = False
            if bad:                                                  # auto-heal: send everything once more, then look again
                for lay, sl, spec in jobs:
                    self.dev.request(self._remap_msg(lay, sl, spec))
                again = self._verify_keys_sync()
                self._healed = not again
                bad = again
            return bad

        def done(bad):
            self.upload_btn2.configure(state="normal")
            n = len(set(j[0] for j in jobs))
            if bad:
                self.set_status("Uploaded, but the read-back check found problems: " + "; ".join(bad), error=True)
            else:
                self.set_status(f"Uploaded to the pad: {n} layer{'s' if n > 1 else ''} x 7 key slots, brightness and mode" +
                                ("" if bad is None else " (read back and verified" + (" after one automatic re-send)" if getattr(self, "_healed", False) else ")")))
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

    def _gc_tick(self):
        gc.collect()
        if not self.closing:
            self.after(3000, self._gc_tick)

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

        self.tip_card = ctk.CTkFrame(sc, fg_color=ui.CARD2, border_width=0)
        self.tip_lbl = ctk.CTkLabel(self.tip_card, text="", wraplength=780, justify="left", anchor="w")
        self.tip_lbl.pack(side="left", padx=14, pady=10, fill="x", expand=True)
        ui.secondary_button(self.tip_card, "Got it", self.dismiss_tip, width=70).pack(side="right", padx=10)

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
        self.history.record(self._edit_snapshot())
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
        st = self._profile_state
        if self.cfg.get("remember_layers") and st.get("win") and not st.get("rule") and st.get("layer") != n:
            self._app_layers[st["win"][0]] = n                               # the user chose this layer in a program that has no rule
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
        elif evt == "host" and self.game_mode:
            self.post(lambda: self.vp_log("host action ignored: game mode (the program in front has a game profile)"))
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
            self.api = bridge.Bridge(self._api_handle, token=a["token"], port=int(API_PORT_OVERRIDE or a["port"]), status=self._api_status)
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
            info.update(custom=any(r[x] for x in ("label", "title", "a", "b")), c_label=r["label"], c_t=r["title"], c_a=r["a"], c_b=r["b"], c_k=r.get("k", "c"))
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
        if self.game_mode and not manual:
            return
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

    # ---- dim the pad while the PC is locked / a fullscreen window is in front; re-sync after the PC woke up
    def _screen_loop(self):
        while not self.closing:
            time.sleep(3)
            try:
                self._screen_step()
            except Exception:                                    # noqa: BLE001
                traceback.print_exc()

    def _screen_step(self, now=None):
        d, now = self._dim, now if now is not None else time.monotonic()
        gap, d["tick"] = now - d["tick"], now
        if not self.dev.connected or self.dev.busy or self.dev.info.get("core_only"):
            d["applied"] = None
            return
        if gap > 25:                                             # the loop did not run for a while: the PC slept - send the clock and the display state again
            self._resync_pad()
        want = None
        if self.cfg.get("dim_lock") and self.screen.locked():
            want = int(self.cfg.get("dim_level", 25))
        if want is None and self.cfg.get("dim_fullscreen") and self.screen.fullscreen():
            want = int(self.cfg.get("dim_level", 25))
        if want == d["applied"]:
            return
        d["applied"] = want
        if self._pad_cap("dimcmd"):
            self.dev.request({"cmd": "dim", "level": want if want is not None else 0})
        elif want is not None:                                   # older firmware: use (and later restore) the saved brightness
            d["saved"] = d["saved"] or int(self.pad.brightness)
            self.dev.request({"cmd": "brightness", "val": want})
        elif d["saved"]:
            self.dev.request({"cmd": "brightness", "val": d["saved"]})
            d["saved"] = None

    def _resync_pad(self):
        for msg in (time_msg(), {"cmd": "brightness", "val": int(self.pad.brightness)}, {"cmd": "mode", "val": int(self.pad.mode)}):
            try:
                self.dev.request(msg, timeout=2)
            except DeviceError:
                break
        self._info_sent = (None, 0.0)

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

        def exec(self, cmd):
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)           # noqa: S602 - the user's own script, shell switch checked by the interpreter
            return r.returncode, r.stdout

        def card(self, label, title, a, b):
            self.app.set_custom_card(label, title, a, b)

        def alert(self, hex_, times):
            if self.app.dev.connected and self.app._pad_cap("ledfx"):
                self.app.dev.request({"cmd": "led", "alert": hex_, "times": times})

        def http(self, arg):
            return netactions.webhook(arg, self.app.hostact.net_fetch)

        def ask(self, prompt):
            ai = self.app.cfg["ai"]
            return netactions.ask_ai(prompt, ai.get("key", ""), ai.get("model") or "claude-haiku-4-5-20251001", self.app.hostact.net_fetch)

        def translate(self, lang):
            return netactions.translate(self.app._clipboard_text(), lang, self.app.hostact.net_fetch)

        def moveto(self, x, y): self._impl().moveto(x, y)
        def clickat(self, x, y, btn): self._impl().clickat(x, y, btn)

    def set_custom_card(self, label, title, a, b, kind="c"):
        """The pad's Info screen custom card (also used by the local API and by scripts). kind: c plain, r ring, p progress bar, s scrolling text (firmware 1.5)."""
        info = self.cfg["info"]
        info.update(custom=any((label, title, a, b)), c_label=label, c_t=title, c_a=a, c_b=b, c_k=kind if kind in ("c", "r", "p", "s") else "c")
        self._info_sent = (None, 0.0)

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
        self.rec_mouse_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(adv, text="also the mouse", variable=self.rec_mouse_var, width=110).pack(side="left", padx=(8, 0))
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
    def rec_toggle(self, listener_factory=None, mouse_factory=None):
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
        self._rec_mouse = None
        if self.rec_mouse_var.get():                                   # pointer movement, clicks and the wheel as well
            if mouse_factory is None:
                try:
                    from pynput import mouse as ms

                    def mouse_factory(move, button, scroll):
                        return ms.Listener(on_move=move, on_click=lambda x, y, b, p: button(str(b), p), on_scroll=lambda x, y, dx, dy: scroll(dy))
                except Exception:                                      # noqa: BLE001
                    mouse_factory = None
            if mouse_factory is not None:
                r = self._recorder
                self._rec_mouse = mouse_factory(lambda x, y: r.mouse_move(x, y, time.monotonic()), lambda b, p: r.mouse_button(b, p, time.monotonic()),
                                                lambda dy: r.mouse_scroll(dy, time.monotonic()))
                self._rec_mouse.start()
        self.rec_btn.configure(text="Stop recording", fg_color=ui.ERR_FILL)
        self.set_status("Recording keystrokes ... press 'Stop recording' when done")
        self._rec_job = self.after(60000, lambda: self._rec and self._rec_stop())

    def _rec_stop(self):
        rec, self._rec = self._rec, None
        for lst in (rec, getattr(self, "_rec_mouse", None)):
            try:
                lst.stop()
            except Exception:                                  # noqa: BLE001
                pass
        self._rec_mouse = None
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
        opt = ctk.CTkFrame(box, fg_color="transparent")
        opt.grid(row=5, column=0, sticky="ew", padx=12, pady=(0, 12))
        ui.muted(opt, "Options for the next rule you add:").pack(side="left", padx=(0, 8))
        self.pr_time = ctk.CTkEntry(opt, width=140, placeholder_text="time 09:00-17:00")
        self.pr_time.pack(side="left", padx=4)
        self.pr_days = ctk.CTkEntry(opt, width=110, placeholder_text="days mon-fri")
        self.pr_days.pack(side="left", padx=4)
        self.pr_game = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(opt, text="game mode", variable=self.pr_game).pack(side="left", padx=10)
        self.remember_var = tk.BooleanVar(value=bool(self.cfg.get("remember_layers")))
        ctk.CTkCheckBox(opt, text="remember my layer per program (when no rule matches)", variable=self.remember_var,
                        command=lambda: (self.cfg.__setitem__("remember_layers", bool(self.remember_var.get())), save_config(self.cfg))).pack(side="left", padx=10)
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
            extra = "".join([f"  {r['time']}" if r.get("time") else "", f"  {r['days']}" if r.get("days") else "", "  GAME" if r.get("game") else ""])
            ui.muted(row, f"{r.get('kind', 'either')}: \"{r['match']}\"{extra}", width=300).pack(side="left", padx=6)
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
        tw, dy = self.pr_time.get().strip(), self.pr_days.get().strip()
        try:
            activewin.validate_window(tw, dy)
        except ValueError as e:
            return self.set_status(f"Rule not added: {e}", error=True)
        rule = {"name": name or match, "match": match, "kind": kind, "layer": layer, "enabled": True}
        if tw:
            rule["time"] = tw
        if dy:
            rule["days"] = dy
        if self.pr_game.get():
            rule["game"] = True
        self.cfg["profiles"].append(rule)
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
            rule = activewin.pick_rule(self.cfg["profiles"], proc, title)
            self.game_mode = bool(rule and rule.get("game"))
            if rule:
                lay = int(rule.get("layer", 0))
            elif self.cfg.get("remember_layers") and proc in self._app_layers:
                lay = self._app_layers[proc]                                 # no rule: back to what the user last chose in this program
            else:
                lay = None if default is None or int(default) < 0 else int(default)
            st["rule"] = bool(rule)
            st["text"] = f"focused: {proc or '?'} | {title[:50]}   ->   " + (f"layer {lay + 1}" if lay is not None else "no change") + ("   [game mode: pad actions paused]" if self.game_mode else "")
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
        ui.secondary_button(right, "Show the album cover on the pad", lambda: self._send_pad_image("art", "")).pack(fill="x", padx=14, pady=4)
        ui.secondary_button(right, "Show a QR code of the clipboard", lambda: self._send_pad_image("qr", "")).pack(fill="x", padx=14, pady=4)
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
                   "ci": "owner/name (public repository)", "crypto": "bitcoin  or  ethereum:eur",
                   "quote": "(empty)  or a text file of  quote | author  lines", "birthday": "Anna 03-14, Max 1990-07-02",
                   "ping": "example.com  or  192.168.1.1:22", "http": "https://example.com", "lyrics": "(nothing to enter)",
                   "progress": "year, month, week, day or work", "moon": "(nothing to enter)", "sun": "47.37, 8.54  (empty = your weather city)", "network": "(nothing to enter)",
                   "goal": "water:8  (a {counter:water} counter and its target)", "rain": "(uses your weather city)", "window": "(nothing to enter)", "battery": "(nothing to enter)", "disk": "folder or drive, e.g. C:\\  or  /", "load": "(nothing to enter)"}

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

    def _send_pad_image(self, kind, arg):
        self.bg(lambda: self.pad_image(kind, arg), self.set_status, "Could not send the picture")

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
        cache, snap = self._info_cache, {}                       # snap: what the LED alerts compare between rounds

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
                    if not ev[1] and 0 <= (ev[0] - datetime.now().astimezone()).total_seconds() <= 600:
                        snap["event"] = f"{ev[0].isoformat()} {ev[2]}"          # starts within 10 minutes
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
            cards.append({"k": info.get("c_k", "c") if info.get("c_k") in ("c", "r", "p", "s") else "c", "label": f(info.get("c_label", ""), 24) or "NOTE", "t": f(info.get("c_t", ""), 24), "a": f(info.get("c_a", ""), 40), "b": f(info.get("c_b", ""), 40)})
        for i, item in enumerate(info.get("extras") or []):
            key = f"x{i}"
            try:
                card = cached(key + json.dumps(item, sort_keys=True), extras.TTL[item["type"]], lambda it=item: extras.build(it, **self._extras_ctx()))
                cards.append(card)
                if item["type"] == "ci":
                    snap[key.replace("x", "ci")] = card.get("t", "").lower()
                errs[key] = ""
            except Exception as e:                           # noqa: BLE001
                errs[key] = f"{extras.KINDS.get(item.get('type'), 'card')}: {e}"
        badges = self.badges.get() if self.badges else []
        snap.update({f"badge:{b['name']}": int(b["n"]) for b in badges})
        self._alert_snap = snap
        if self.dev.connected and not self._pad_cap("cards2"):                    # ring / progress / scrolling cards need firmware 1.5: older pads get a plain card
            for c in cards:
                if c.get("k") in ("r", "p", "s"):
                    if c["k"] != "s" and str(c.get("t", "")).isdigit():
                        c["t"] = c["t"] + "%"
                    c["k"] = "c"
        return cards[:4], badges, errs

    def _extras_ctx(self):
        """What some Info cards need from the app: the weather place, the snippet counters, the program in front."""
        i = self.cfg["info"]
        return {"latlon": (i.get("lat"), i.get("lon")), "counter_values": self.cfg["counters"], "window": self.active_win.get()}

    def _led_alerts(self):
        """Compare the latest snapshot with the previous one; blink the LED for what is new (only when the switch is on)."""
        found = self.alerts.update(self._alert_snap)
        if found and self.cfg.get("led_alerts") and self.dev.connected and self._pad_cap("ledfx"):
            name, hex_, times = found[0]
            try:
                self.dev.request({"cmd": "led", "alert": hex_, "times": times}, timeout=2)
            except DeviceError:
                pass
        return found

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
                self._led_alerts()
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
        if not self.cfg.get("palette_used"):
            self.cfg["palette_used"] = True
            save_config(self.cfg)
        cmds = [(f"Go to {label}  -  {sub}", lambda n=name: self.tabs.set(n)) for _g, pages in PAGES for name, label, sub in pages]
        cmds += [("Upload everything to the pad", self.upload_all), ("Connect / disconnect the simulated pad", self.toggle_simulate),
                 ("Rescan serial ports", self.refresh_ports), ("Verify the keys stored on the pad", self.verify_pad_keys),
                 ("Toggle light / dark theme", lambda: (self.theme_var.set(not self.theme_var.get()), self._theme_toggled())),
                 ("Run the guided hardware test", self.open_hwtest), ("Run the setup wizard", self.open_wizard),
                 ("Back up everything...", self.backup_export), ("Restore from a backup...", self.backup_import),
                 ("Show the Info screen on the pad", self.info_show_on_pad), ("Send info cards now", lambda: self.info_send_now(force=True)),
                 ("Check the pad's state (safe mode?)", self.recovery_check),
                 ("Undo the last key assignment  (Ctrl+Z)", self.edit_undo), ("Redo  (Ctrl+Y)", self.edit_redo),
                 ("Check for app updates", self.app_card.check_updates), ("Latency test", self.app_card.latency)]
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
        self.dimlock_var = tk.BooleanVar(value=bool(self.cfg.get("dim_lock")))
        ctk.CTkSwitch(box, text="Dim the pad while this PC is locked", variable=self.dimlock_var,
                      command=lambda: (self.cfg.__setitem__("dim_lock", bool(self.dimlock_var.get())), save_config(self.cfg))).grid(row=10, column=0, columnspan=3, padx=6, pady=4, sticky="w")
        self.dimfs_var = tk.BooleanVar(value=bool(self.cfg.get("dim_fullscreen")))
        ctk.CTkSwitch(box, text="Dim the pad while a fullscreen window (video, game, presentation) is in front", variable=self.dimfs_var,
                      command=lambda: (self.cfg.__setitem__("dim_fullscreen", bool(self.dimfs_var.get())), save_config(self.cfg))).grid(row=11, column=0, columnspan=3, padx=6, pady=4, sticky="w")
        ctk.CTkLabel(box, text="Dimmed brightness").grid(row=12, column=0, padx=6, pady=4, sticky="w")
        self.dimlevel = ctk.CTkSlider(box, from_=5, to=120, number_of_steps=23, width=220, command=lambda v: (self.cfg.__setitem__("dim_level", int(v)), save_config(self.cfg)))
        self.dimlevel.set(int(self.cfg.get("dim_level", 25)))
        self.dimlevel.grid(row=12, column=1, padx=6, sticky="w")
        self.ledmood_var = tk.BooleanVar(value=bool(self.cfg.get("led_mood")))
        ctk.CTkSwitch(box, text="Pad LED follows the time of day (warm morning, white noon, violet night)", variable=self.ledmood_var,
                      command=lambda: (self.cfg.__setitem__("led_mood", bool(self.ledmood_var.get())), save_config(self.cfg))).grid(row=13, column=0, columnspan=3, padx=6, pady=4, sticky="w")
        self.ledaudio_var = tk.BooleanVar(value=bool(self.cfg.get("led_audio")))
        self.ledaudio_sw = ctk.CTkSwitch(box, text="Pad LED reacts to sound from the audio input (low = red, mid = green, high = blue)", variable=self.ledaudio_var,
                                         command=lambda: (self.cfg.__setitem__("led_audio", bool(self.ledaudio_var.get())), save_config(self.cfg)))
        self.ledaudio_sw.grid(row=14, column=0, columnspan=3, padx=6, pady=4, sticky="w")
        if not audio.available():
            self.ledaudio_sw.configure(state="disabled", text="Sound-reactive LED: not available (pip install sounddevice numpy)")
        self.viz_var = tk.BooleanVar(value=bool(self.cfg.get("viz_on")))
        self.viz_sw = ctk.CTkSwitch(box, text="Send the PC's sound spectrum to the pad's SOUND screen (screen 20, firmware 1.5)", variable=self.viz_var,
                                    command=lambda: (self.cfg.__setitem__("viz_on", bool(self.viz_var.get())), save_config(self.cfg)))
        self.viz_sw.grid(row=16, column=0, columnspan=3, padx=6, pady=4, sticky="w")
        if not audio.available():
            self.viz_sw.configure(state="disabled", text="Sound bars: not available (pip install sounddevice numpy)")
        self.ledalert_var = tk.BooleanVar(value=bool(self.cfg.get("led_alerts")))
        ctk.CTkSwitch(box, text="Blink the LED for a new mail badge, a CI result or an upcoming event (firmware 1.4)", variable=self.ledalert_var,
                      command=lambda: (self.cfg.__setitem__("led_alerts", bool(self.ledalert_var.get())), save_config(self.cfg))).grid(row=15, column=0, columnspan=3, padx=6, pady=4, sticky="w")
        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        ctk.CTkSwitch(box, text="Start this app when I log in (minimised)", variable=self.autostart_var, command=self._autostart_toggled).grid(
            row=8, column=0, columnspan=3, padx=6, pady=4, sticky="w")
        self.tray_var = tk.BooleanVar(value=bool(self.cfg.get("tray")))
        self.tray_sw = ctk.CTkSwitch(box, text="Keep running in the system tray when the window is closed", variable=self.tray_var, command=self._tray_toggled)
        self.tray_sw.grid(row=9, column=0, columnspan=3, padx=6, pady=4, sticky="w")
        if not tray.available():
            self.tray_sw.configure(state="disabled", text="Tray icon: not available (pip install pystray)")

        box = self._card(sc, "This app", "Appearance, a global hotkey, plugins, update check, tips, link latency and power estimate.")
        self.app_card = appcard.AppCard(self, box)
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
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w", pady=(8, 0))
        ui.secondary_button(row, "Copy a share code of this layer", self._share_copy, width=210).pack(side="left", padx=6)
        self.share_entry = ctk.CTkEntry(row, width=300, placeholder_text="paste a share code (DC1:...)")
        self.share_entry.pack(side="left", padx=6)
        ui.secondary_button(row, "Replace this layer with it", self._share_import, width=190).pack(side="left", padx=6)

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
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w", pady=(8, 0))
        ui.secondary_button(row, "Boot report", self.boot_report, width=130).pack(side="left", padx=6)
        ui.danger_button(row, "Roll back to the previous firmware", self.rollback_fw, width=250).pack(side="left", padx=6)
        ui.muted(row, "  (the rollback only works after a Wi-Fi update, when the old firmware is still in the other slot)").pack(side="left")
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

    # ---- share codes: one layer as a short text
    def layer_share_code(self, layer=None):
        layer = self.edit_layer if layer is None else layer
        lm = self.cfg["layers"][layer]
        custom = {m["action"]: self.cfg["custom"][m["action"]] for m in lm.values() if m["cat"] == "Custom" and m["action"] in self.cfg["custom"]}
        gestures = {k.split(":", 1)[1]: v for k, v in self.cfg.get("gestures", {}).items() if k.startswith(f"{layer}:")}
        for v in gestures.values():
            if v["cat"] == "Custom" and v["action"] in self.cfg["custom"]:
                custom[v["action"]] = self.cfg["custom"][v["action"]]
        return sharecode.encode({"v": 1, "layer": layer + 1, "map": {k: dict(v) for k, v in lm.items()}, "custom": custom, "gestures": gestures})

    def import_layer_share_code(self, code, layer=None):
        """Replace one layer with the contents of a share code. Every action is validated first; nothing changes if anything is wrong. Returns a summary."""
        layer = self.edit_layer if layer is None else layer
        p = sharecode.decode(code)
        custom_in = p.get("custom") if isinstance(p.get("custom"), dict) else {}
        for name, c in custom_in.items():
            if not (isinstance(name, str) and 0 < len(name) <= 48 and isinstance(c, dict) and spec_ok({"type": c.get("type"), "val": c.get("val")})):
                raise ValueError(f"the code contains an action ('{str(name)[:30]}') this app does not accept")

        def resolves(m):
            if not (isinstance(m, dict) and isinstance(m.get("cat"), str) and isinstance(m.get("action"), str)):
                return False
            if m["cat"] == "Custom":
                return m["action"] in custom_in or m["action"] in self.cfg["custom"]
            return (m["cat"], m["action"]) in ACTION_INDEX
        new_map = p.get("map")
        if not isinstance(new_map, dict) or set(new_map) != {str(i) for i in range(1, 8)} or not all(resolves(m) for m in new_map.values()):
            raise ValueError("the code does not hold a complete, valid key map")
        gest = {}
        for k, m in (p.get("gestures") or {}).items():
            if not (isinstance(k, str) and re.fullmatch(r"([1-5]:(hold|double|triple)|([89]|1[0-5]):press)", k) and resolves(m)):
                raise ValueError("the code contains a gesture this app does not accept")
            gest[f"{layer}:{k}"] = {"cat": m["cat"], "action": m["action"]}
        for name, c in custom_in.items():
            self.cfg["custom"][name] = {"type": c["type"], "val": c["val"]}
        lm = self.cfg["layers"][layer]
        lm.clear()
        lm.update({k: {"cat": m["cat"], "action": m["action"]} for k, m in new_map.items()})
        self.cfg["gestures"] = {k: v for k, v in self.cfg.get("gestures", {}).items() if not k.startswith(f"{layer}:")}
        self.cfg["gestures"].update(gest)
        self.history.record(self._edit_snapshot())
        save_config(self.cfg)
        self.refresh_library()
        self.refresh_action_lists()
        self.padview.refresh()
        self.recompute_pending()
        return f"layer {layer + 1}: 7 keys, {len(custom_in)} custom action(s), {len(gest)} gesture(s)"

    def _share_copy(self):
        code = self.layer_share_code()
        self.clipboard_clear()
        self.clipboard_append(code)
        self.set_status(f"Share code of layer {self.edit_layer + 1} copied ({len(code)} characters) - paste it into a message")

    def _share_import(self):
        code = self.share_entry.get().strip()
        if not code:
            return self.set_status("Paste a share code first", error=True)
        if not messagebox.askyesno("Share code", f"Replace layer {self.edit_layer + 1} with the contents of this code?\n(you can undo it with Ctrl+Z)"):
            return
        try:
            self.set_status("Imported: " + self.import_layer_share_code(code) + "  - upload to send it to the pad")
        except ValueError as e:
            self.set_status(f"Not imported: {e}", error=True)

    def boot_report(self):
        """How the pad's last start-up went, its reset statistics and USB link events (firmware 1.5)."""
        if not self.dev.connected:
            return self.set_status("Not connected", error=True)
        if not self._pad_cap("bootlog"):
            return self.set_status("The boot report needs firmware 1.5 - update the pad (Device -> Firmware).", error=True)

        def done(r):
            c = r.get("counts") or [0] * 6
            lines = ["---- boot report ----", f"last reset: {r.get('reset')}   crashes in a row: {r.get('crashes')}   display: {r.get('disp_why') or 'ok'}",
                     "resets since the counters were cleared: power-on %d, software %d, panic %d, watchdog %d, brownout %d, other %d" % tuple(c),
                     f"USB/host link: {r.get('usb_connects')} connects, {r.get('usb_drops')} drops   heap {r.get('heap', 0) // 1024} KB (lowest {r.get('heap_min', 0) // 1024} KB)",
                     "start-up notes: " + str(r.get("log", "")), "---------------------"]
            for ln in lines:
                self._dev_note(ln)
            self.set_status("Boot report written to the Diagnostics log" + (" - the pad crashed recently!" if (r.get("crashes") or 0) else ""))
        self.bg(lambda: self.dev.request({"cmd": "boot_log"}), done, "Boot report failed")

    def rollback_fw(self):
        if not self.dev.connected:
            return self.set_status("Not connected", error=True)
        if not self._pad_cap("rollback"):
            return self.set_status("Rolling back needs firmware 1.5 on the pad first.", error=True)
        if not messagebox.askyesno("Roll back", "Boot the previous firmware again?\n\nThis only works after a Wi-Fi update, when the old firmware is still stored in the pad's other slot. "
                                   "The pad restarts."):
            return

        self.bg(lambda: self.dev.request({"cmd": "rollback", "confirm": True}), lambda r: self.set_status("Rolling back - the pad restarts"), "Rollback not possible")

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

    def _push_labels(self, layers=None):
        """Key names for the pad's key toast / popup menu (firmware 1.5). Never fails an upload."""
        if not self._pad_cap("labels"):
            return
        for lay in (range(LAYERS) if layers is None else layers):
            try:
                self.dev.request({"cmd": "labels", "layer": lay, "l": labels_for(self.cfg, lay)})
            except DeviceError:
                return

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
