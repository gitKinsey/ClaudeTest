"""The 'Scripts' page: write macro scripts with loops, conditions and variables, check them, dry-run them, run them, put them on a key."""
import tkinter as tk
from datetime import datetime

import customtkinter as ctk

import time
import urllib.parse

from desk_lib import netactions, scripting, ui

EXAMPLE = '''# Fill in a form: copy, switch window, paste, confirm
key ctrl+c
wait 200
key alt+tab
wait 400
key ctrl+v
if window "Sign in"
  key enter
end
'''

HELP = ("Commands: key ctrl+shift+t | text Hello {date} | wait 200 | click left|right|middle|double | scroll -3 | media mute | open https://... | app calc | file path | "
        "notify text | shell cmd | run other_script | set name = value | stop\n"
        "Blocks: repeat 3 ... end    if [not] window \"text\" / process \"text\" / clipboard \"text\" / time 09:00-17:00 / weekday mon-fri ... [else ...] end\n"
        "{date} {time} {clipboard} {counter:n} {uuid} {random:1-6} and your own {name} variables work in text and arguments.")


class ScriptsPage:
    def __init__(self, app, tab):
        self.app = app
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        sc = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        sc.grid(row=0, column=0, sticky="nsew")
        box = ctk.CTkFrame(sc)
        box.pack(fill="x", pady=5, padx=2)
        ctk.CTkLabel(box, text="Macro scripts", font=ui.font(15, "bold"), anchor="w").pack(anchor="w", padx=16, pady=(14, 2))
        ui.muted(box, "A script is a small program for one key: repeat things, do different things depending on the program in front, remember values, "
                 "call other scripts. The app runs it, so it must be running; the pad only asks for it.", wraplength=880).pack(anchor="w", padx=16, pady=(0, 6))
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=4)
        self.pick = tk.StringVar(value="")
        self.menu = ctk.CTkOptionMenu(row, values=[""], variable=self.pick, width=220, command=self._load)
        self.menu.pack(side="left")
        ui.secondary_button(row, "New", self.new, width=70).pack(side="left", padx=6)
        self.name = ctk.CTkEntry(row, width=200, placeholder_text="script name")
        self.name.pack(side="left", padx=6)
        ctk.CTkButton(row, text="Save", width=80, command=self.save).pack(side="left", padx=6)
        ui.secondary_button(row, "Delete", self.delete, width=80).pack(side="left", padx=6)
        r2 = ctk.CTkFrame(box, fg_color="transparent")
        r2.pack(fill="x", padx=14, pady=(2, 2))
        self.tpl_var = tk.StringVar(value="Start from a ready-made script...")
        ctk.CTkOptionMenu(r2, values=list(scripting.TEMPLATES), variable=self.tpl_var, width=300, command=self.use_template).pack(side="left")
        self.hist_var = tk.StringVar(value="Earlier versions")
        self.hist_menu = ctk.CTkOptionMenu(r2, values=["(none)"], variable=self.hist_var, width=190)
        self.hist_menu.pack(side="left", padx=(14, 4))
        ui.secondary_button(r2, "Restore that version", self.restore_version, width=150).pack(side="left", padx=4)
        r3 = ctk.CTkFrame(box, fg_color="transparent")
        r3.pack(fill="x", padx=14, pady=(2, 2))
        self.url = ctk.CTkEntry(r3, width=420, placeholder_text="https:// address of a shared script (plain text)")
        self.url.pack(side="left")
        ui.secondary_button(r3, "Import from the address", self.import_url, width=170).pack(side="left", padx=6)
        self.editor = ctk.CTkTextbox(box, height=260, font=ctk.CTkFont(family="Courier", size=13), wrap="none")
        self.editor.pack(fill="x", padx=16, pady=6)
        ui.muted(box, HELP, wraplength=880).pack(anchor="w", padx=16)
        bar = ctk.CTkFrame(box, fg_color="transparent")
        bar.pack(fill="x", padx=14, pady=(8, 4))
        ui.secondary_button(bar, "Check", self.check, width=80).pack(side="left", padx=4)
        ui.secondary_button(bar, "Dry run", self.dry, width=90).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="Run in 3 s", width=100, fg_color="#2f7d4f", command=self.run_now).pack(side="left", padx=4)
        ui.secondary_button(bar, "Stop", self.stop, width=70).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="Assign to the selected key", width=190, command=self.assign).pack(side="left", padx=(16, 4))
        self.pretend = ctk.CTkEntry(bar, width=230, placeholder_text="dry run: pretend this window is focused")
        self.pretend.pack(side="left", padx=(16, 4))
        self.log = ctk.CTkTextbox(box, height=140, state="disabled")
        self.log.pack(fill="x", padx=16, pady=(4, 14))
        self.refresh()

    # ---- storage
    def names(self):
        return sorted(self.app.cfg["scripts"])

    def refresh(self, select=None):
        names = self.names()
        self.menu.configure(values=names or [""])
        cur = select if select in names else (self.pick.get() if self.pick.get() in names else (names[0] if names else ""))
        self.pick.set(cur)
        self._load(cur)

    def _load(self, name):
        self.name.delete(0, "end")
        self.name.insert(0, name)
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", self.app.cfg["scripts"].get(name, EXAMPLE if not name else ""))
        self._refresh_history(name)

    def _refresh_history(self, name):
        items = self.app.cfg["script_history"].get(name, [])
        self.hist_menu.configure(values=[h["t"] for h in items] or ["(none)"])
        self.hist_var.set(items[0]["t"] if items else "(none)")

    def restore_version(self):
        name = self.pick.get()
        hit = next((h for h in self.app.cfg["script_history"].get(name, []) if h["t"] == self.hist_var.get()), None)
        if not hit:
            return self.app.set_status("There is no earlier version of this script yet", error=True)
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", hit["src"])
        self.app.set_status(f"Version from {hit['t']} loaded - press Save to keep it (the current one stays in the history)")

    def use_template(self, key):
        self.new()
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", scripting.TEMPLATES[key])
        self.name.insert(0, "".join(ch for ch in key.split(":")[0] if ch.isalnum() or ch in " _-.").strip()[:30])
        self.app.set_status("Template loaded - change it and press Save")

    def import_url(self):
        url = self.url.get().strip()

        def work():
            status, text = netactions.http_request("GET", url)
            if status != 200:
                raise ValueError(f"the server answered {status}")
            scripting.parse(text)                                          # must be a valid script
            return text

        def done(text):
            self.new()
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", text)
            leaf = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "imported"
            self.name.insert(0, leaf[:30])
            self.app.set_status("Script imported - READ IT, then press Save (it can run commands and open things)")
        self.app.bg(work, done, "Import failed")

    def new(self):
        self.pick.set("")
        self._load("")
        self.name.focus_set()

    def source(self):
        return self.editor.get("1.0", "end-1c")

    def save(self):
        name = self.name.get().strip()[:32]
        if not name or not all(ch.isalnum() or ch in " _-." for ch in name):
            return self.app.set_status("Give the script a name (letters, digits, space, - _ .)", error=True)
        try:
            scripting.parse(self.source())
        except scripting.ScriptError as e:
            return self.app.set_status(f"Not saved - {e}", error=True)
        old = self.app.cfg["scripts"].get(name)
        if old is not None and old != self.source():                     # keep the last 10 versions of every script
            hist = self.app.cfg["script_history"].setdefault(name, [])
            label = time.strftime("%Y-%m-%d %H:%M:%S")
            while any(h["t"] == label for h in hist):                  # two saves in the same second keep distinct labels
                label += "+"
            hist.insert(0, {"t": label, "src": old})
            del hist[10:]
        self.app.cfg["scripts"][name] = self.source()
        self.app.save_cfg()
        self.refresh(name)
        self.app.set_status(f"Script '{name}' saved")

    def delete(self):
        name = self.pick.get()
        if name in self.app.cfg["scripts"]:
            del self.app.cfg["scripts"][name]
            self.app.save_cfg()
            self.refresh()
            self.app.set_status(f"Script '{name}' deleted (keys that run it will report an error)")

    # ---- actions
    def _say(self, lines):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", "\n".join(lines))
        self.log.configure(state="disabled")

    def check(self):
        try:
            nodes = scripting.parse(self.source())
        except scripting.ScriptError as e:
            self._say([f"Problem: {e}"])
            return self.app.set_status(f"Script problem: {e}", error=True)
        self._say([f"OK - {self._count(nodes)} commands"])
        self.app.set_status("The script is valid")

    @staticmethod
    def _count(nodes):
        """Commands written in the script (loop bodies counted once)."""
        n = 0
        for it in nodes:
            n += 1 if it["t"] == "cmd" else ScriptsPage._count(it.get("body", [])) + ScriptsPage._count(it.get("then", [])) + ScriptsPage._count(it.get("else", []))
        return n

    def dry(self):
        try:
            pretend = self.pretend.get().strip()
            win = (pretend.lower(), pretend)                       # pretend this window has the focus (process and title)
            lines = scripting.dry_run(self.source(), lookup=self.app.cfg["scripts"].get, window=win, clipboard="(clipboard)", now=datetime.now(), ctx={})
        except scripting.ScriptError as e:
            self._say([f"Problem: {e}"])
            return self.app.set_status(f"Script problem: {e}", error=True)
        self._say(["Dry run - nothing was sent to the computer:"] + lines)

    def run_now(self):
        name = self.name.get().strip()
        if name not in self.app.cfg["scripts"] or self.app.cfg["scripts"][name] != self.source():
            self.save()
            if name not in self.app.cfg["scripts"]:
                return
        self.app.set_status(f"Running '{name}' in 3 seconds - click into the program it should act on")
        self.app.after(3000, lambda: self.app.bg(lambda: self.app.run_script(name), lambda m: self._say([m]), "Script failed"))

    def stop(self):
        self.app.script_stop.set()
        self.app.set_status("Stopping the running script")

    def assign(self):
        name = self.name.get().strip()
        if name not in self.app.cfg["scripts"]:
            return self.app.set_status("Save the script first", error=True)
        self.app.assign_spec(("host", {"op": "script", "arg": name}), f"Script: {name}")
