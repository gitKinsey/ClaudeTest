"""Device page card 'This app': appearance (theme, accent colour, size, language), global hotkey, plugins, updates, tips, latency, power."""
import os
import platform
import subprocess
import time
import tkinter as tk
import webbrowser

import customtkinter as ctk

from desk_lib import diag, hotkey, i18n, ui, updates

SCALES = {"80 %": 0.8, "90 %": 0.9, "100 %": 1.0, "110 %": 1.1, "125 %": 1.25, "150 %": 1.5}


def open_folder(path):
    path = str(path)
    if platform.system() == "Windows":
        os.startfile(path)                                       # noqa: S606
    else:
        subprocess.Popen(["open" if platform.system() == "Darwin" else "xdg-open", path])      # noqa: S603


class AppCard:
    def __init__(self, app, body):
        self.app = app
        cfg = app.cfg
        r = 0
        ctk.CTkLabel(body, text="Appearance", anchor="w", font=ui.font(13, "bold")).grid(row=r, column=0, columnspan=4, padx=6, pady=(2, 2), sticky="w")
        row = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
        row.grid(row=r + 1, column=0, columnspan=4, sticky="w", padx=6)
        self.theme = ctk.CTkSegmentedButton(row, values=["dark", "light", "system"], command=self.set_theme, width=240)
        self.theme.set(cfg.get("appearance", "dark"))
        self.theme.pack(side="left")
        self.accent = tk.StringVar(value=cfg.get("accent", "cyan"))
        ctk.CTkLabel(row, text="  accent").pack(side="left")
        ctk.CTkOptionMenu(row, values=list(ui.ACCENTS), variable=self.accent, width=100, command=lambda v: self.set_restart("accent", v)).pack(side="left", padx=4)
        self.scale = tk.StringVar(value=next((k for k, v in SCALES.items() if abs(v - float(cfg.get("ui_scale", 1.0))) < 0.01), "100 %"))
        ctk.CTkLabel(row, text="  size").pack(side="left")
        ctk.CTkOptionMenu(row, values=list(SCALES), variable=self.scale, width=90, command=lambda v: self.set_restart("ui_scale", SCALES[v])).pack(side="left", padx=4)
        self.lang = tk.StringVar(value=i18n.LANGS.get(cfg.get("language", "en"), "English"))
        ctk.CTkLabel(row, text="  language").pack(side="left")
        ctk.CTkOptionMenu(row, values=list(i18n.LANGS.values()), variable=self.lang, width=120, command=self.set_language).pack(side="left", padx=4)
        self.note = ui.muted(body, "Accent colour, size and language apply the next time the app starts. Language covers navigation, buttons and headings, not every sentence.",
                             wraplength=860)
        self.note.grid(row=r + 2, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 8))

        ctk.CTkLabel(body, text="Global hotkey", anchor="w", font=ui.font(13, "bold")).grid(row=r + 3, column=0, columnspan=4, padx=6, pady=(2, 2), sticky="w")
        row = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
        row.grid(row=r + 4, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 8))
        self.hk_var = tk.BooleanVar(value=bool(cfg.get("hotkey_on")))
        self.hk_sw = ctk.CTkSwitch(row, text="Open the command palette from anywhere with", variable=self.hk_var, command=self.hk_apply)
        self.hk_sw.pack(side="left")
        self.hk_entry = ctk.CTkEntry(row, width=140)
        self.hk_entry.insert(0, cfg.get("hotkey", "ctrl+alt+k"))
        self.hk_entry.pack(side="left", padx=8)
        self.hk_entry.bind("<Return>", lambda e: self.hk_apply())
        if not hotkey.available():
            self.hk_sw.configure(state="disabled", text="Global hotkey: not available here (pip install pynput; Wayland blocks it)")

        ctk.CTkLabel(body, text="Plugins", anchor="w", font=ui.font(13, "bold")).grid(row=r + 5, column=0, columnspan=4, padx=6, pady=(2, 2), sticky="w")
        row = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
        row.grid(row=r + 6, column=0, columnspan=4, sticky="w", padx=6)
        self.pl_var = tk.BooleanVar(value=bool(cfg.get("plugins_on")))
        ctk.CTkSwitch(row, text="Allow plugins (Python files that add key actions)", variable=self.pl_var, command=self.pl_toggle).pack(side="left")
        ui.secondary_button(row, "Open the folder", self.pl_folder, width=120).pack(side="left", padx=8)
        ui.secondary_button(row, "Add an example", self.pl_example, width=120).pack(side="left", padx=2)
        ui.secondary_button(row, "Reload", self.pl_reload, width=80).pack(side="left", padx=2)
        self.pl_note = ui.muted(body, "", wraplength=860)
        self.pl_note.grid(row=r + 7, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 8))
        self.pl_refresh()

        ctk.CTkLabel(body, text="Updates and tips", anchor="w", font=ui.font(13, "bold")).grid(row=r + 8, column=0, columnspan=4, padx=6, pady=(2, 2), sticky="w")
        row = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
        row.grid(row=r + 9, column=0, columnspan=4, sticky="w", padx=6)
        ui.secondary_button(row, "Check for updates", self.check_updates, width=150).pack(side="left")
        self.upd_var = tk.BooleanVar(value=bool(cfg.get("update_check")))
        ctk.CTkSwitch(row, text="Check at start (asks GitHub; sends nothing about you)", variable=self.upd_var,
                      command=lambda: self.put("update_check", bool(self.upd_var.get()))).pack(side="left", padx=12)
        self.tips_var = tk.BooleanVar(value=bool(cfg.get("tips", True)))
        ctk.CTkSwitch(row, text="Show tips on the Home page", variable=self.tips_var, command=self.tips_toggle).pack(side="left", padx=4)
        self.upd_lbl = ui.muted(body, "", wraplength=860)
        self.upd_lbl.grid(row=r + 10, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 8))

        ctk.CTkLabel(body, text="Link quality and power", anchor="w", font=ui.font(13, "bold")).grid(row=r + 11, column=0, columnspan=4, padx=6, pady=(2, 2), sticky="w")
        row = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
        row.grid(row=r + 12, column=0, columnspan=4, sticky="w", padx=6)
        ui.secondary_button(row, "Latency test (20 pings)", self.latency, width=170).pack(side="left")
        self.power_lbl = ui.muted(body, "", wraplength=860)
        self.power_lbl.grid(row=r + 13, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 2))
        ui.muted(body, "Several pads on one PC: start one app per pad with   --config pad2.json --port COM7 --api-port 8766 .",
                 wraplength=860).grid(row=r + 14, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 4))
        self.refresh_power()

    # ---- settings helpers
    def put(self, key, value):
        self.app.cfg[key] = value
        self.app.save_cfg()

    def set_theme(self, mode):
        self.app.set_appearance(mode)

    def set_restart(self, key, value):
        self.put(key, value)
        self.app.set_status("Saved - it applies the next time the app starts")

    def set_language(self, label):
        code = next((k for k, v in i18n.LANGS.items() if v == label), "en")
        self.put("language", code)
        self.app.set_status("Saved - the language applies the next time the app starts")

    # ---- hotkey
    def hk_apply(self):
        on, combo = bool(self.hk_var.get()), self.hk_entry.get().strip()
        why = self.app.hotkey_set(on, combo)
        if why:
            self.hk_var.set(False)
            self.app.cfg["hotkey_on"] = False
            self.app.save_cfg()
            return self.app.set_status(why, error=True)
        self.app.cfg["hotkey_on"], self.app.cfg["hotkey"] = on, combo
        self.app.save_cfg()
        self.app.set_status(f"Global hotkey {combo} is on" if on else "Global hotkey is off")

    # ---- plugins
    def pl_toggle(self):
        self.put("plugins_on", bool(self.pl_var.get()))
        self.pl_reload()

    def pl_folder(self):
        self.app.plugins.folder.mkdir(parents=True, exist_ok=True)
        try:
            open_folder(self.app.plugins.folder)
        except OSError as e:
            self.app.set_status(f"Could not open the folder: {e}", error=True)

    def pl_example(self):
        p = self.app.plugins.install_example()
        self.pl_reload()
        self.app.set_status(f"Example written to {p} - put 'Run a plugin: hello:short' on a key")

    def pl_reload(self):
        if self.app.cfg.get("plugins_on"):
            self.app.plugins.load()
        self.pl_refresh()

    def pl_refresh(self):
        ph = self.app.plugins
        if not self.app.cfg.get("plugins_on"):
            text = f"Plugins are off. Folder: {ph.folder}"
        else:
            text = f"Loaded: {', '.join(sorted(ph.mods)) or 'none'}.   Folder: {ph.folder}"
            if ph.errors:
                text += "\nProblems: " + "; ".join(f"{k}: {v}" for k, v in ph.errors.items())
        self.pl_note.configure(text=text)

    # ---- updates / tips
    def check_updates(self):
        def done(r):
            self.upd_lbl.configure(text=r["message"] + (f"\n{r['url']}" if r["newer"] else ""))
            if r["newer"] and messagebox_ask(f"{r['message']}\n\nOpen the download page?"):
                webbrowser.open(r["url"])
        self.app.bg(lambda: updates.check(self.app.version_string()), done, "Update check failed")

    def tips_toggle(self):
        self.put("tips", bool(self.tips_var.get()))
        self.app.refresh_tip()

    # ---- latency / power
    def latency(self):
        if not self.app.dev.connected:
            return self.app.set_status("Not connected", error=True)

        def work():
            ms = []
            for i in range(20):
                t = time.perf_counter()
                self.app.dev.request({"cmd": "ping", "t": i}, timeout=2)
                ms.append((time.perf_counter() - t) * 1000)
            return diag.latency_summary(ms)
        self.app.bg(work, lambda s: (self.upd_lbl.configure(text="Latency: " + s), self.app.set_status("Latency: " + s)), "Latency test failed")

    def refresh_power(self):
        a = self.app
        ma = diag.power_estimate(a.pad.brightness, "auto", wifi="wifi" in (a.dev.info.get("caps") or []) if a.dev.connected else False)
        self.power_lbl.configure(text="USB power estimate: " + diag.power_text(ma) + " (rough estimate, not a measurement)")


def messagebox_ask(text):
    from tkinter import messagebox                               # noqa: PLC0415
    return messagebox.askyesno("Desk Companion", text)
