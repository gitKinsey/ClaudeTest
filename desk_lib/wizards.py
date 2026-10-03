"""Guided windows: hardware test (with a printable report), first-run setup wizard and the Ctrl+K command palette.
They talk to the running app through a few of its methods (app.dev, app.bg, app.post, app.set_status ...)."""
import html
import time
import tkinter as tk
import webbrowser
from pathlib import Path

import customtkinter as ctk

from . import ui
from .ui import CARD2, ERR, MUTED, OK, TEXT, WARN

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def report_html(results, info, app_version):
    """A small standalone HTML page: what was tested, what the pad is, and the result of each step."""
    rows = "".join(
        f"<tr><td>{html.escape(n)}</td><td class='{r.lower()}'>{r}</td><td>{html.escape(d)}</td></tr>" for n, r, d in results)
    n_pass, n_fail = sum(r == PASS for _n, r, _d in results), sum(r == FAIL for _n, r, _d in results)
    head = "ALL PASSED" if n_fail == 0 and n_pass else (f"{n_fail} FAILED" if n_fail else "nothing tested")
    return f"""<!doctype html><meta charset="utf-8"><title>Desk Companion hardware test</title>
<style>body{{font:15px system-ui,sans-serif;margin:40px;color:#111}}h1{{margin:0 0 4px}}table{{border-collapse:collapse;margin-top:18px}}
td,th{{border:1px solid #ccc;padding:8px 14px;text-align:left}}.pass{{color:#15803d;font-weight:700}}.fail{{color:#b91c1c;font-weight:700}}.skip{{color:#888}}
.k{{color:#666}}@media print{{body{{margin:12px}}}}</style>
<h1>Desk Companion - hardware test: {head}</h1>
<div class="k">{time.strftime('%Y-%m-%d %H:%M')} &middot; app {html.escape(app_version)} &middot; firmware {html.escape(str(info.get('fw', '?')))}
&middot; core {html.escape(str(info.get('core', '?')))} &middot; reset {html.escape(str(info.get('reset', '?')))}</div>
<table><tr><th>Check</th><th>Result</th><th>Details</th></tr>{rows}</table>"""


class _Window(ctk.CTkToplevel):
    def __init__(self, app, title, w=640, h=520):
        super().__init__(app)
        self.app = app
        self.title(title)
        self.geometry(f"{w}x{h}")
        self.minsize(w, h)
        self.configure(fg_color=ui.BG)
        try:
            self.transient(app)
        except tk.TclError:
            pass
        self.after(150, self._front)

    def _front(self):
        try:
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass


class HardwareTest(_Window):
    """Walks through LED, display, keys, dial, keyboard output and the self-test; every step is PASS / FAIL / SKIP."""
    STEPS = ("Connection", "LED", "Display", "Keys K1-K5", "Encoder", "USB keyboard", "Self-test")

    def __init__(self, app, app_version):
        super().__init__(app, "Guided hardware test", 680, 560)
        self.app_version, self.results, self.i, self.seen, self._after = app_version, [], -1, set(), None
        self.title_lbl = ui.heading(self, "", 20)
        self.title_lbl.pack(anchor="w", padx=24, pady=(20, 2))
        self.text = ui.muted(self, "", wraplength=620)
        self.text.pack(anchor="w", padx=24)
        self.progress = ctk.CTkProgressBar(self, width=620)
        self.progress.pack(padx=24, pady=(10, 4))
        self.progress.set(0)
        self.body = ctk.CTkFrame(self, fg_color="transparent", border_width=0)
        self.body.pack(fill="both", expand=True, padx=24, pady=6)
        self.btns = ctk.CTkFrame(self, fg_color="transparent", border_width=0)
        self.btns.pack(fill="x", padx=24, pady=(0, 18))
        app.hw_listener = self.on_event
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.next_step()

    # ---- plumbing
    def close(self):
        if getattr(self.app, "hw_listener", None) == self.on_event:
            self.app.hw_listener = None
        if self._after:
            try:
                self.after_cancel(self._after)
            except (tk.TclError, ValueError):
                pass
        self.destroy()

    def record(self, name, res, detail=""):
        self.results.append((name, res, detail))
        self.next_step()

    def clear(self):
        for w in list(self.body.winfo_children()) + list(self.btns.winfo_children()):
            w.destroy()

    def buttons(self, *spec):
        for text, cmd, kind in spec:
            if kind == "sec":
                b = ui.secondary_button(self.btns, text, cmd, width=130)
            else:
                b = ctk.CTkButton(self.btns, text=text, command=cmd, width=130)
            b.pack(side="left", padx=(0, 8))

    def yes_no(self, name, detail_yes="you confirmed it", detail_no="you reported a problem"):
        self.buttons(("Yes, works", lambda: self.record(name, PASS, detail_yes), "pri"), ("No", lambda: self.record(name, FAIL, detail_no), "sec"),
                     ("Skip", lambda: self.record(name, SKIP, "skipped"), "sec"))

    def req(self, cmd, then=None, timeout=6):
        def fail_cb():
            pass
        self.app.bg(lambda: self.app.dev.request(cmd, timeout=timeout), then or (lambda _r: None), "Pad did not answer", fail=fail_cb)

    # ---- steps
    def next_step(self):
        self.i += 1
        self.clear()
        self.progress.set(min(1.0, self.i / len(self.STEPS)))
        if self.i >= len(self.STEPS):
            return self.summary()
        name = self.STEPS[self.i]
        self.title_lbl.configure(text=f"{self.i + 1}/{len(self.STEPS)}  {name}")
        getattr(self, "step_" + ["conn", "led", "display", "keys", "enc", "hid", "self"][self.i])(name)

    def step_conn(self, name):
        info = self.app.dev.info if self.app.dev.connected else None
        if not info:
            self.text.configure(text="The pad is not connected. Plug it in (or use 'Simulate pad' on Home), then press Re-check.")
            self.buttons(("Re-check", self._redo, "pri"), ("Skip", lambda: self.record(name, SKIP, "pad not connected"), "sec"))
            return
        self.text.configure(text=f"Connected - firmware {info.get('fw')}.")
        self.record(name, PASS, f"firmware {info.get('fw')}, safe mode: {info.get('safe', False)}")

    def _redo(self):
        self.i -= 1
        self.next_step()

    def step_led(self, name):
        self.text.configure(text="Watch the small LED next to the USB port: it will show red, green, then blue.")

        def play():
            for r, g, b in ((60, 0, 0), (0, 60, 0), (0, 0, 60)):
                self.app.dev.request({"cmd": "led", "r": r, "g": g, "b": b})
                time.sleep(0.7)
            self.app.dev.request({"cmd": "led", "mode": "auto"})
        self.app.bg(play, lambda _r: (self.text.configure(text="Did the LED show red, green and blue?"), self.yes_no(name)), "LED test failed")

    def step_display(self, name):
        if not self.app.dev.info.get("disp", False):
            self.text.configure(text="The pad reports no working display (safe mode or the display is switched off).")
            return self.record(name, SKIP, "display not available on this pad")
        self.text.configure(text="The screen will show red, green, blue, colour bars and a grid with a circle.")

        def play():
            for r, g, b in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
                self.app.dev.request({"cmd": "display", "test": "fill", "r": r, "g": g, "b": b, "hold": 1500})
                time.sleep(0.9)
            self.app.dev.request({"cmd": "display", "test": "bars", "hold": 2500})
            time.sleep(1.2)
            self.app.dev.request({"cmd": "display", "test": "grid", "hold": 4000})
        self.app.bg(play, lambda _r: (self.text.configure(text="Were the colours right (not swapped or inverted) and is the grid circle round and centred?"), self.yes_no(name)), "Display test failed")

    def _wait_events(self, name, want, label, timeout=30):
        self.seen.clear()
        self.want, self.name = set(want), name
        self.lbls = {}
        row = ctk.CTkFrame(self.body, fg_color="transparent", border_width=0)
        row.pack(pady=14)
        for k in want:
            l = ctk.CTkLabel(row, text=label(k), width=84, height=40, fg_color=CARD2, corner_radius=8)
            l.pack(side="left", padx=5)
            self.lbls[k] = l
        self.req({"cmd": "events", "val": True})
        self.buttons(("Skip", lambda: self._finish_events(SKIP, "skipped"), "sec"), ("Give up (fail)", lambda: self._finish_events(FAIL, "not detected: " + ", ".join(sorted(map(str, self.want - self.seen)))), "sec"))
        self._deadline = time.time() + timeout
        self._tick()

    def _tick(self):
        if not self.winfo_exists():
            return
        if self.want and time.time() > self._deadline:
            return self._finish_events(FAIL, "timed out waiting for: " + ", ".join(sorted(map(str, self.want - self.seen))))
        if self.want and self.want <= self.seen:
            return self._finish_events(PASS, "all inputs detected")
        self._after = self.after(300, self._tick)

    def _finish_events(self, res, detail):
        if not self.want:
            return
        self.want = set()
        self.req({"cmd": "events", "val": False})
        self.record(self.name, res, detail)

    def on_event(self, m):
        """Called by the app (reader thread) for every pad event while this window is open."""
        evt = m.get("evt")
        key = None
        if evt == "key" and m.get("v") == 1:
            key = f"K{m.get('k')}"
        elif evt == "enc" and m.get("d"):
            key = "right" if m["d"] > 0 else "left"
        elif evt == "encsw" and m.get("v") == 1:
            key = "press"
        if key and key in getattr(self, "lbls", {}):
            self.seen.add(key)
            self.app.post(lambda k=key: self.lbls[k].configure(fg_color=ui.OK_FILL) if k in self.lbls and self.lbls[k].winfo_exists() else None)

    def step_keys(self, name):
        self.text.configure(text="Press the five keys K1 ... K5 on the pad, in any order. Each box turns green when its press arrives.")
        self._wait_events(name, [f"K{i}" for i in range(1, 6)], lambda k: k)

    def step_enc(self, name):
        self.text.configure(text="Turn the dial left, then right, then press it once.")
        self._wait_events(name, ["left", "right", "press"], lambda k: {"left": "turn left", "right": "turn right", "press": "press"}[k])

    def step_hid(self, name):
        if not self.app.dev.info.get("hid", False):
            self.text.configure(text="This firmware build has no USB keyboard (Tools > USB Mode must be 'USB-OTG (TinyUSB)').")
            return self.record(name, FAIL, "no HID in this firmware build")
        self.text.configure(text="Click into any text field (Notepad, a browser search box ...). Press 'Type it' - the pad types 'DeskCompanion OK' after 3 seconds.")

        def go():
            self.text.configure(text="Typing in 3 seconds - click into a text field now ...")
            self.after(3000, lambda: self.req({"cmd": "run", "type": "text", "val": "DeskCompanion OK"}, lambda _r: (
                self.text.configure(text="Did 'DeskCompanion OK' appear?"), self.clear(), self.yes_no(name))))
        self.buttons(("Type it", go, "pri"), ("Skip", lambda: self.record(name, SKIP, "skipped"), "sec"))

    def step_self(self, name):
        self.text.configure(text="Running the pad's own self-test (storage, settings memory, memory, LED, display) ...")

        def done(r):
            ok = all(bool(r.get(k)) for k in ("nvs", "fs", "heap_ok"))
            detail = f"settings memory {r.get('nvs')}, storage {r.get('fs')}, heap {r.get('heap')} bytes, display {r.get('display')}"
            self.record(name, PASS if ok else FAIL, detail)
        self.req({"cmd": "selftest"}, done, timeout=25)

    def summary(self):
        self.clear()
        n_fail = sum(r == FAIL for _n, r, _d in self.results)
        self.title_lbl.configure(text="Result: " + ("all checks passed" if not n_fail else f"{n_fail} check(s) failed"))
        self.text.configure(text="Save the report as a page you can print, or copy it to share.")
        colors = {PASS: OK, FAIL: ERR, SKIP: MUTED}
        for n, r, d in self.results:
            row = ctk.CTkFrame(self.body, fg_color="transparent", border_width=0)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=r, width=52, text_color=colors[r], font=ui.font(13, "bold"), anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=n, width=130, anchor="w").pack(side="left")
            ui.muted(row, d, wraplength=380).pack(side="left")
        self.buttons(("Save HTML report", self.save, "pri"), ("Copy as text", self.copy, "sec"), ("Close", self.close, "sec"))
        self.progress.set(1.0)

    def text_report(self):
        return "\n".join(f"{r:5s} {n}: {d}" for n, r, d in self.results)

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.text_report())
        self.app.set_status("Hardware test result copied")

    def save(self):
        path = Path.home() / time.strftime("deskcompanion_hwtest_%Y%m%d_%H%M.html")
        info = self.app.dev.info if self.app.dev.connected else {}
        path.write_text(report_html(self.results, info, self.app_version), encoding="utf-8")
        self.app.set_status(f"Report saved: {path}")
        self.last_report = path
        try:
            webbrowser.open(path.as_uri())
        except Exception:                                    # noqa: BLE001
            pass


class SetupWizard(_Window):
    PAGES = ("Welcome", "Connect", "Firmware", "Check", "GIF", "Done")

    def __init__(self, app):
        super().__init__(app, "Setup wizard", 700, 540)
        self.i = -1
        self.head = ui.heading(self, "", 22)
        self.head.pack(anchor="w", padx=26, pady=(22, 2))
        self.sub = ui.muted(self, "", wraplength=640)
        self.sub.pack(anchor="w", padx=26)
        self.dots = ctk.CTkProgressBar(self, width=648)
        self.dots.pack(padx=26, pady=10)
        self.body = ctk.CTkFrame(self, fg_color="transparent", border_width=0)
        self.body.pack(fill="both", expand=True, padx=26)
        self.nav = ctk.CTkFrame(self, fg_color="transparent", border_width=0)
        self.nav.pack(fill="x", padx=26, pady=(0, 20))
        self.protocol("WM_DELETE_WINDOW", self.finish)
        self.go(1)

    def finish(self):
        self.app.cfg["wizard_done"] = True
        self.app.save_cfg()
        self.destroy()

    def go(self, d):
        self.i = max(0, min(len(self.PAGES) - 1, self.i + d))
        for w in list(self.body.winfo_children()) + list(self.nav.winfo_children()):
            w.destroy()
        self.dots.set((self.i + 1) / len(self.PAGES))
        name = self.PAGES[self.i]
        getattr(self, "page_" + name.lower())()
        if self.i > 0:
            ui.secondary_button(self.nav, "Back", lambda: self.go(-1), width=90).pack(side="left")
        if self.i < len(self.PAGES) - 1:
            ctk.CTkButton(self.nav, text="Next", width=110, command=lambda: self.go(1)).pack(side="right")
        else:
            ctk.CTkButton(self.nav, text="Finish", width=110, command=self.finish).pack(side="right")

    def line(self, text, color=None):
        l = ctk.CTkLabel(self.body, text=text, anchor="w", justify="left", wraplength=640, text_color=color or TEXT)
        l.pack(anchor="w", pady=3)
        return l

    def page_welcome(self):
        self.head.configure(text="Welcome to Desk Companion")
        self.sub.configure(text="A few steps to get your pad running. You can skip anything and come back from Home -> Setup wizard.")
        for t in ("1. Connect the pad and check its firmware", "2. Test the LED and the display", "3. Pick an animation for the round screen",
                  "", "Nothing is changed on the pad until you press a button that says so."):
            self.line(t, MUTED if t.startswith("Nothing") else None)

    def page_connect(self):
        self.head.configure(text="Connect the pad")
        self.sub.configure(text="Plug in the USB cable (use the USB-C port of the ESP32-S3-Zero). The app finds it by itself.")
        self.status = self.line("")
        row = ctk.CTkFrame(self.body, fg_color="transparent", border_width=0)
        row.pack(anchor="w", pady=10)
        ctk.CTkButton(row, text="Look again", width=120, command=self._connect_refresh).pack(side="left", padx=(0, 8))
        ui.secondary_button(row, "No pad yet - simulate one", self.app.toggle_simulate, width=200).pack(side="left")
        self._connect_refresh()
        self._poll = self.after(1000, self._connect_poll)

    def _connect_poll(self):
        if self.winfo_exists() and self.PAGES[self.i] == "Connect":
            self._connect_refresh()
            self._poll = self.after(1000, self._connect_poll)

    def _connect_refresh(self):
        if not self.status.winfo_exists():
            return
        if self.app.dev.connected:
            self.status.configure(text=f"Connected: firmware {self.app.dev.info.get('fw', '?')}", text_color=OK)
        else:
            from companion_app import list_serial_ports
            esp = [p for p in list_serial_ports() if p["esp"]]
            self.status.configure(text=("An Espressif device is visible (" + esp[0]["device"] + ") - connecting ...") if esp else
                                  "No pad found yet. Try another cable (many are charge-only) or hold BOOT while plugging in for the first flash.", text_color=WARN)

    def page_firmware(self):
        self.head.configure(text="Firmware")
        self.sub.configure(text="The pad needs the firmware that matches this app.")
        kind, txt = self.app.fw_status()
        self.line(txt, {"ok": OK, "off": MUTED}.get(kind, WARN))
        if kind == "off":
            self.line("Not connected: if the pad is new, flash it now. Hold BOOT while plugging in USB, then press the button.", MUTED)
        elif kind != "ok":
            self.line("Updating takes about a minute. The app reconnects by itself afterwards.", MUTED)
        row = ctk.CTkFrame(self.body, fg_color="transparent", border_width=0)
        row.pack(anchor="w", pady=10)
        ctk.CTkButton(row, text="Flash the bundled firmware", command=lambda: self.app.flash_firmware("full")).pack(side="left", padx=(0, 8))
        ui.secondary_button(row, "Skip (already up to date)", lambda: self.go(1)).pack(side="left")

    def page_check(self):
        self.head.configure(text="Quick check")
        self.sub.configure(text="Light the LED and put colours on the screen to see that everything answers.")
        row = ctk.CTkFrame(self.body, fg_color="transparent", border_width=0)
        row.pack(anchor="w", pady=10)

        def req(cmd):
            if not self.app.dev.connected:
                return self.app.set_status("Connect the pad first", error=True)
            self.app.bg(lambda: self.app.dev.request(cmd), None, "Pad did not answer")
        ctk.CTkButton(row, text="Blink the LED", command=lambda: req({"cmd": "led", "hex": "#00c8ff"})).pack(side="left", padx=(0, 8))
        ui.secondary_button(row, "LED back to normal", lambda: req({"cmd": "led", "mode": "auto"})).pack(side="left", padx=(0, 8))
        ui.secondary_button(row, "Show screen test", lambda: req({"cmd": "display", "test": "grid", "hold": 6000})).pack(side="left")
        self.line("For a thorough check of keys, dial and keyboard use Home -> Guided hardware test.", MUTED)
        ui.secondary_button(self.body, "Open the guided hardware test", self.app.open_hwtest, width=240).pack(anchor="w", pady=8)

    def page_gif(self):
        self.head.configure(text="Pick an animation")
        self.sub.configure(text="16 built-in animations, your own GIFs, or search Tenor / GIPHY.")
        ctk.CTkButton(self.body, text="Open the GIF library", command=lambda: (self.app.tabs.set("GIF Upload"), self.finish())).pack(anchor="w", pady=14)
        self.line("You can also do this later from the GIFs page.", MUTED)

    def page_done(self):
        self.head.configure(text="You're set")
        self.sub.configure(text="Everything is on the sidebar. Tips:")
        for t in ("Pad: drag actions onto the keys; three layers switch with the dial menu or an action key.",
                  "Profiles: let the layer follow the program you are using.",
                  "Info: now playing, weather, calendar on the pad's 6th screen.",
                  "Ctrl+K opens the command palette from anywhere."):
            self.line("-  " + t)


class CommandPalette(_Window):
    def __init__(self, app, commands):
        super().__init__(app, "Command palette", 560, 420)
        self.commands = commands                                  # [(label, callable)]
        self.entry = ctk.CTkEntry(self, placeholder_text="Type a command ...", height=38)
        self.entry.pack(fill="x", padx=16, pady=(16, 8))
        self.list = tk.Listbox(self, activestyle="none", borderwidth=0, highlightthickness=0, font=("TkDefaultFont", 12))
        self.list.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        d = ui.DARK
        self.list.configure(bg=d["card2"], fg=d["text"], selectbackground="#0e7490", selectforeground="#ffffff")
        self.entry.bind("<KeyRelease>", self._filter)
        self.entry.bind("<Down>", lambda e: self._move(1))
        self.entry.bind("<Up>", lambda e: self._move(-1))
        self.entry.bind("<Return>", lambda e: self._run())
        self.bind("<Escape>", lambda e: self.destroy())
        self.list.bind("<Double-Button-1>", lambda e: self._run())
        self.shown = []
        self._filter()
        self.after(100, self.entry.focus_set)

    def _filter(self, _e=None):
        q = self.entry.get().strip().lower()
        self.shown = [c for c in self.commands if all(w in c[0].lower() for w in q.split())]
        self.list.delete(0, "end")
        for label, _fn in self.shown:
            self.list.insert("end", "  " + label)
        if self.shown:
            self.list.selection_set(0)

    def _move(self, d):
        cur = (self.list.curselection() or (0,))[0]
        n = max(0, min(len(self.shown) - 1, cur + d))
        self.list.selection_clear(0, "end")
        self.list.selection_set(n)
        self.list.see(n)
        return "break"

    def _run(self):
        sel = self.list.curselection()
        if not sel:
            return
        fn = self.shown[sel[0]][1]
        self.destroy()
        self.app.after(60, fn)


class AutoBackupDialog(_Window):
    """The app keeps the last 15 versions of its settings by itself (after every meaningful change); pick one to go back to it."""

    def __init__(self, app):
        super().__init__(app, "Automatic backups", 560, 440)
        ctk.CTkLabel(self, text="Automatic backups of your settings", font=ui.font(16, "bold")).pack(anchor="w", padx=18, pady=(16, 2))
        ui.muted(self, "Made by the app after changes to key maps, macros, scripts, profiles and settings (at most one every two minutes, newest 15 kept). "
                 "Restoring replaces the settings in this app; press 'Upload to pad' afterwards to send them to the device.", wraplength=520).pack(anchor="w", padx=18)
        ctk.CTkButton(self, text="Back up now", width=120, command=self.now).pack(anchor="w", padx=18, pady=8)
        self.box = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.refresh()

    def refresh(self):
        import time as _t
        for w in self.box.winfo_children():
            w.destroy()
        files = self.app.autobackup().list()
        if not files:
            ui.muted(self.box, "No automatic backup yet - change something, or press 'Back up now'.").pack(anchor="w", padx=8, pady=10)
        for path, mtime, size in files:
            row = ctk.CTkFrame(self.box, fg_color=ui.CARD2, corner_radius=8, border_width=0)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=_t.strftime("%a %d %b %Y  %H:%M:%S", _t.localtime(mtime)), anchor="w", width=250).pack(side="left", padx=12, pady=8)
            ui.muted(row, f"{size // 1024 + 1} KB").pack(side="left", padx=6)
            ui.secondary_button(row, "Restore", lambda p=path: self.restore(p), width=80).pack(side="right", padx=10)

    def now(self):
        p = self.app.autobackup().maybe(self.app.cfg, force=True)
        self.app.set_status("Backed up now" if p else "Nothing changed since the last backup")
        self.refresh()

    def restore(self, path):
        try:
            cfg = self.app.autobackup().read(path)
        except ValueError as e:
            return self.app.set_status(str(e), error=True)
        self.app.restore_config(cfg)
        self.app.set_status(f"Settings restored from {path.name} - press 'Upload to pad' to send them to the device")
        self.destroy()
