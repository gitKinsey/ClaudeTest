"""The 'Automation' page: time-based rules (scheduler.py) and how a fired rule is carried out."""
import tkinter as tk
from datetime import datetime

import customtkinter as ctk

from desk_lib import padextras, scheduler, ui

WHEN_KINDS = ["Every day", "Weekdays", "Weekends", "Every N minutes", "Once"]
# label -> (kind of rule action, host op or None, hint)
DO_KINDS = {"Switch layer": ("layer", None, "1, 2, 3, next or prev"), "Show screen": ("mode", None, "1 clock, 2 focus, 3 media, 4 system, 5 GIF, 6 info"),
            "Set brightness": ("brightness", None, "5 to 255"), "LED": ("led", None, "auto, off, or a colour like ff8800"),
            "Open website": ("host", "url", "https://example.com"), "Start program": ("host", "app", "program name or path"),
            "Open file or folder": ("host", "file", "path"), "Type a snippet": ("host", "snippet", "text with {date} {time} {counter:name}"),
            "Run shell command": ("host", "shell", "command line (needs the shell switch)"), "Show a reminder": ("notify", None, "reminder text")}


def build_entry(when_label, when_arg, do_label, do_arg, days=None, name=""):
    """UI fields -> a validated rule (raises ValueError with a readable message)."""
    kind, op, _hint = DO_KINDS[do_label]
    a = str(do_arg).strip()
    if kind == "layer":
        do = {"kind": "layer", "n": a.lower() if a.lower() in ("next", "prev") else int(a) - 1 if a.isdigit() else a}
    elif kind in ("mode", "brightness"):
        if not a.isdigit():
            raise ValueError("enter a number")
        do = {"kind": kind, "n": int(a)}
    elif kind == "led":
        low = a.lower().lstrip("#")
        do = {"kind": "led", "mode": low if low in ("auto", "off") else "solid", "hex": low}
    elif kind == "host":
        do = {"kind": "host", "op": op, "arg": a}
    else:
        do = {"kind": "notify", "text": a}
    if when_label in ("Every day", "Weekdays", "Weekends"):
        when = {"kind": "daily", "time": when_arg, "days": list(range(7)) if when_label == "Every day" else [0, 1, 2, 3, 4] if when_label == "Weekdays" else [5, 6]}
    elif when_label == "Every N minutes":
        when = {"kind": "every", "minutes": int(when_arg) if str(when_arg).strip().isdigit() else 0}
    else:
        when = {"kind": "once", "at": str(when_arg).strip().replace(" ", "T")}
    return scheduler.validate({"name": name, "when": when, "do": do})


def run_entry(entry, request, host_run, notify, shell_ok=True):
    """Carry out a fired rule. request(dict) talks to the pad (may raise); host_run(op, arg) -> (ok, msg); notify(text).
    Returns a short log line."""
    d = entry["do"]
    k = d["kind"]
    if k == "layer":
        request({"cmd": "layer", "val": d["n"]})
    elif k == "mode":
        request({"cmd": "mode", "val": d["n"]})
    elif k == "brightness":
        request({"cmd": "brightness", "val": d["n"]})
    elif k == "led":
        if d["mode"] == "solid":
            request({"cmd": "led", "hex": d["hex"]})
        else:
            request({"cmd": "led", "mode": d["mode"]})
    elif k == "host":
        ok, msg = host_run(d["op"], d.get("arg", ""))
        if not ok:
            raise RuntimeError(msg)
    elif k == "notify":
        notify(d["text"])
    return scheduler.describe(entry)


class AutomationPage:
    def __init__(self, app, tab):
        self.app = app
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        sc = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        sc.grid(row=0, column=0, sticky="nsew")
        sc.grid_columnconfigure(0, weight=1)
        box = ctk.CTkFrame(sc)
        box.pack(fill="x", pady=5, padx=2)
        ctk.CTkLabel(box, text="Scheduled actions", font=ui.font(15, "bold"), anchor="w").pack(anchor="w", padx=16, pady=(14, 2))
        ui.muted(box, "Do something at a set time while this app is running: switch the layer when work starts, dim the screen in the evening, "
                 "open your daily page, remind you to stretch. A rule that was due while the app was closed is skipped, never run late.",
                 wraplength=880).pack(anchor="w", padx=16, pady=(0, 6))
        self.list = ctk.CTkFrame(box, fg_color="transparent")
        self.list.pack(fill="x", padx=10, pady=4)
        add = ctk.CTkFrame(box, fg_color=ui.CARD2, corner_radius=10, border_width=0)
        add.pack(fill="x", padx=14, pady=(8, 14))
        self.when = tk.StringVar(value=WHEN_KINDS[1])
        self.do = tk.StringVar(value="Switch layer")
        r1 = ctk.CTkFrame(add, fg_color="transparent")
        r1.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkLabel(r1, text="When", width=50, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(r1, values=WHEN_KINDS, variable=self.when, width=160, command=lambda _v: self._when_changed()).pack(side="left", padx=6)
        self.when_arg = ctk.CTkEntry(r1, width=170, placeholder_text="09:00")
        self.when_arg.pack(side="left", padx=6)
        self.when_hint = ui.muted(r1, "time, 24 h")
        self.when_hint.pack(side="left", padx=6)
        r2 = ctk.CTkFrame(add, fg_color="transparent")
        r2.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(r2, text="Do", width=50, anchor="w").pack(side="left")
        ctk.CTkOptionMenu(r2, values=list(DO_KINDS), variable=self.do, width=160, command=lambda _v: self._do_changed()).pack(side="left", padx=6)
        self.do_arg = ctk.CTkEntry(r2, width=330, placeholder_text=DO_KINDS["Switch layer"][2])
        self.do_arg.pack(side="left", padx=6)
        r3 = ctk.CTkFrame(add, fg_color="transparent")
        r3.pack(fill="x", padx=10, pady=(4, 10))
        self.name = ctk.CTkEntry(r3, width=160, placeholder_text="name (optional)")
        self.name.pack(side="left", padx=(56, 6))
        ctk.CTkButton(r3, text="Add rule", width=100, command=self.add).pack(side="left", padx=6)
        self._when_changed()
        self.refresh()
        self.gestures = padextras.GesturePanel(app, sc)
        self._build_api(sc)

    # ---- local API
    def _build_api(self, sc):
        app = self.app
        box = ctk.CTkFrame(sc)
        box.pack(fill="x", pady=5, padx=2)
        ctk.CTkLabel(box, text="Local API and command line", font=ui.font(15, "bold"), anchor="w").pack(anchor="w", padx=16, pady=(14, 2))
        ui.muted(box, "Lets scripts, shortcuts and other programs on THIS computer drive the pad while the app runs (switch layer, set the LED, "
                 "show a custom card, press a key, send a notification). Off by default; protected by a secret token; never reachable from the "
                 "network or from web pages.", wraplength=880).pack(anchor="w", padx=16, pady=(0, 6))
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=4)
        self.api_on = tk.BooleanVar(value=bool(app.cfg["api"]["on"]))
        ctk.CTkSwitch(row, text="Enable the local API", variable=self.api_on, command=self._api_toggled).pack(side="left")
        ctk.CTkLabel(row, text="Port").pack(side="left", padx=(24, 6))
        self.api_port = ctk.CTkEntry(row, width=80)
        self.api_port.insert(0, str(app.cfg["api"]["port"]))
        self.api_port.pack(side="left")
        ui.secondary_button(row, "New token", self._api_newtoken, width=100).pack(side="left", padx=(16, 4))
        ui.secondary_button(row, "Copy token", self._api_copy, width=100).pack(side="left", padx=4)
        self.api_state = ui.muted(box, "", wraplength=880)
        self.api_state.pack(anchor="w", padx=16, pady=(2, 4))
        self.api_help = ctk.CTkLabel(box, text="", justify="left", anchor="w", font=ctk.CTkFont(family="Courier", size=12), wraplength=880)
        self.api_help.pack(anchor="w", padx=16, pady=(0, 14))
        self._api_refresh()

    def _api_refresh(self):
        app = self.app
        a = app.cfg["api"]
        if app.api:
            self.api_state.configure(text=f"Running on http://127.0.0.1:{a['port']}  -  the token is saved with your settings. Use 'Copy token' to put it on the clipboard.", text_color=ui.OK)
            self.api_help.configure(text="python deskcompanion_cli.py layer 2\npython deskcompanion_cli.py led ff8800\n"
                                    f"curl -H \"Authorization: Bearer <token>\" -d '{{\"n\": 2}}' http://127.0.0.1:{a['port']}/v1/layer")
        else:
            self.api_state.configure(text=app.api_error or "Off.", text_color=ui.ERR if app.api_error else ui.MUTED)
            self.api_help.configure(text="")

    def _api_toggled(self):
        app = self.app
        if self.api_on.get():
            try:
                port = int(self.api_port.get())
                if not 1024 <= port <= 65535:
                    raise ValueError
            except ValueError:
                self.api_on.set(False)
                return app.set_status("The port must be a number from 1024 to 65535", error=True)
            app.cfg["api"]["port"] = port
            if not app.api_start():
                self.api_on.set(False)
        else:
            app.api_stop()
            app.cfg["api"]["on"] = False
        app.save_cfg()
        self._api_refresh()

    def _api_newtoken(self):
        import secrets
        self.app.cfg["api"]["token"] = secrets.token_urlsafe(18)
        if self.app.api:
            self.app.api_start()
        self.app.save_cfg()
        self.app.set_status("New API token created - scripts using the old one are locked out")

    def _api_copy(self):
        self.app.clipboard_clear()
        self.app.clipboard_append(self.app.cfg["api"]["token"])
        self.app.set_status("API token copied to the clipboard")

    # ---- form
    def _when_changed(self):
        w = self.when.get()
        hint = {"Every N minutes": ("30", "minutes (1 to 1440)"), "Once": ("2026-10-03 15:00", "date and time")}.get(w, ("09:00", "time, 24 h"))
        self.when_arg.delete(0, "end")
        self.when_arg.configure(placeholder_text=hint[0])
        self.when_hint.configure(text=hint[1])

    def _do_changed(self):
        self.do_arg.delete(0, "end")
        self.do_arg.configure(placeholder_text=DO_KINDS[self.do.get()][2])

    def add(self):
        app = self.app
        try:
            if self.do.get() == "Run shell command" and not app.cfg.get("allow_shell"):
                raise ValueError("Shell commands are switched off (Device page -> 'Allow the pad to run shell commands').")
            entry = build_entry(self.when.get(), self.when_arg.get() or self.when_arg.cget("placeholder_text"), self.do.get(), self.do_arg.get(), name=self.name.get())
        except (ValueError, TypeError) as e:
            return app.set_status(f"Cannot add the rule: {e}", error=True)
        app.cfg["schedules"].append(entry)
        app.save_cfg()
        self.name.delete(0, "end")
        self.do_arg.delete(0, "end")
        self.refresh()
        app.set_status(f"Rule added: {scheduler.describe(entry)}")

    # ---- list
    def refresh(self):
        for w in self.list.winfo_children():
            w.destroy()
        rules = self.app.cfg["schedules"]
        if not rules:
            ui.muted(self.list, "No rules yet.").pack(anchor="w", padx=8, pady=8)
        now = datetime.now()
        for i, e in enumerate(rules):
            row = ctk.CTkFrame(self.list, fg_color=ui.CARD2, corner_radius=8, border_width=0)
            row.pack(fill="x", pady=3)
            v = tk.BooleanVar(value=e.get("enabled", True))
            ctk.CTkCheckBox(row, text="", variable=v, width=24, command=lambda i=i, v=v: self._enable(i, bool(v.get()))).pack(side="left", padx=(10, 2), pady=8)
            ctk.CTkLabel(row, text=e.get("name") or "Rule", font=ui.font(13, "bold"), width=110, anchor="w").pack(side="left", padx=6)
            ui.muted(row, scheduler.describe(e), width=420).pack(side="left", padx=6)
            nxt = self.app.scheduler.next_run(e, now)
            ui.muted(row, f"next: {nxt}" if nxt else "off", width=130).pack(side="left", padx=6)
            ui.secondary_button(row, "Remove", lambda i=i: self._remove(i), width=70).pack(side="right", padx=10)
            ui.secondary_button(row, "Run now", lambda e=e: self.app.run_schedule(e, manual=True), width=80).pack(side="right", padx=2)

    def _enable(self, i, on):
        self.app.cfg["schedules"][i]["enabled"] = on
        self.app.save_cfg()
        self.refresh()

    def _remove(self, i):
        del self.app.cfg["schedules"][i]
        self.app.save_cfg()
        self.refresh()
