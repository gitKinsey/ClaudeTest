"""Firmware 1.3 features in the app: the 'Pad behaviour' card (dial acceleration, clock style, screensaver, night dimming)
and the key-gesture panel (hold / double-tap actions). Everything the pad cannot do yet is greyed out with a reason."""
import tkinter as tk

import customtkinter as ctk

from desk_lib import ui

DIAL = [("Off", 0), ("Normal", 1), ("Fast", 2)]
CLOCKS = [("Classic (analog + digital)", 0), ("Digital", 1), ("Binary", 2), ("Minimal", 3), ("Analog, smooth seconds (1.5)", 4), ("Words (1.5)", 5)]
SAVER_TIMES = [("Off", 0), ("1 minute", 60), ("5 minutes", 300), ("10 minutes", 600), ("30 minutes", 1800), ("1 hour", 3600)]
SAVER_STYLES = [("Starfield", 1), ("Matrix rain", 2), ("Dim drifting clock", 3), ("Plasma (1.5)", 4), ("Fire (1.5)", 5), ("Lava lamp (1.5)", 6), ("Game of Life (1.5)", 7)]
THEMES = [("Cyan (default)", 0), ("High contrast", 1), ("Night red", 2), ("Terminal green", 3), ("Amber", 4), ("Monochrome", 5), ("Seasons (follows the month)", 6)]
ROTATIONS = [("0 degrees", 0), ("90 degrees", 1), ("180 degrees", 2), ("270 degrees", 3)]
HOURS = [f"{h:02d}:00" for h in range(24)]
GESTURES = [("Hold the key", "hold"), ("Double-tap the key", "double"), ("Triple-tap the key (1.5)", "triple")]
DIAL_PRESS = {"Dial pressed + turned right": 8, "Dial pressed + turned left": 9,        # slots 8 / 9 (firmware 1.4), gesture name "press"
              "Chord K1 + K2 together (1.5)": 10, "Chord K2 + K3 together (1.5)": 11, "Chord K3 + K4 together (1.5)": 12, "Chord K4 + K5 together (1.5)": 13,
              "Dial double-click (1.5)": 14, "Dial triple-click (1.5)": 15}
SLOT_CAP = {8: "pressturn", 9: "pressturn", 10: "chords", 11: "chords", 12: "chords", 13: "chords", 14: "dialclicks", 15: "dialclicks"}
SCREEN_NAMES = ["Clock", "Focus timer", "Media", "System", "GIF", "Info", "Stopwatch", "Breathing", "Dice & coin", "Reaction test", "Snake", "Habits",
                "Pong", "Breakout", "Flappy", "Game of Life", "Pixel pet", "Simon", "Diagnostics", "Sound bars"]


def _label(table, value, default=0):
    return next((n for n, v in table if v == value), table[default][0])


def _value(table, label, default=0):
    return next((v for n, v in table if n == label), table[default][1])


def has_cap(app, cap):
    return bool(app.dev.connected and cap in (app.dev.info.get("caps") or []))


def gesture_key(layer, slot, g):
    return f"{layer}:{slot}:{g}"


def parse_gesture_key(k):
    layer, slot, g = k.split(":")
    return int(layer), int(slot), g


class BehaviourCard:
    def __init__(self, app, body):
        self.app = app
        self.busy = False                                    # True while the controls are being filled from the pad (no echo back)
        self.v = {"dial_accel": tk.StringVar(value=DIAL[0][0]), "clock_style": tk.StringVar(value=CLOCKS[0][0]),
                  "saver_s": tk.StringVar(value=SAVER_TIMES[0][0]), "saver_style": tk.StringVar(value=SAVER_STYLES[0][0]),
                  "night_on": tk.BooleanVar(value=False), "night_from": tk.StringVar(value="22:00"), "night_to": tk.StringVar(value="07:00")}
        self.widgets = []

        def row(r, text, *ws):
            ctk.CTkLabel(body, text=text, anchor="w").grid(row=r, column=0, padx=6, pady=5, sticky="w")
            f = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
            f.grid(row=r, column=1, sticky="w", padx=6)
            for w in ws:
                w(f).pack(side="left", padx=(0, 8))
        menu = lambda var, table, key: (lambda f: self._reg(ctk.CTkOptionMenu(f, values=[n for n, _v in table], variable=var, width=210,   # noqa: E731
                                                                              command=lambda lab, k=key, t=table: self.apply(**{k: _value(t, lab)}))))
        row(0, "Dial acceleration", menu(self.v["dial_accel"], DIAL, "dial_accel"))
        row(1, "Clock style", menu(self.v["clock_style"], CLOCKS, "clock_style"))
        row(2, "Screensaver after", menu(self.v["saver_s"], SAVER_TIMES, "saver_s"), menu(self.v["saver_style"], SAVER_STYLES, "saver_style"))
        self.level = ctk.CTkSlider(body, from_=5, to=150, number_of_steps=29, width=160, command=lambda _v: self._level_moved())
        self.level.set(30)
        row(3, "Night dimming", lambda f: self._reg(ctk.CTkSwitch(f, text="on", variable=self.v["night_on"], width=60, command=self._night_changed)),
            lambda f: ctk.CTkLabel(f, text="from"),
            lambda f: self._reg(ctk.CTkOptionMenu(f, values=HOURS, variable=self.v["night_from"], width=80, command=lambda _v: self._night_changed())),
            lambda f: ctk.CTkLabel(f, text="to"),
            lambda f: self._reg(ctk.CTkOptionMenu(f, values=HOURS, variable=self.v["night_to"], width=80, command=lambda _v: self._night_changed())),
            lambda f: ctk.CTkLabel(f, text="max brightness"))
        self.level.grid(row=3, column=2, padx=6)
        self._reg(self.level)
        # ---- firmware 1.5: look and feel
        self.v.update({"theme": tk.StringVar(value=THEMES[0][0]), "rotation": tk.StringVar(value=ROTATIONS[0][0]), "tint": tk.BooleanVar(value=False),
                       "pixel_shift": tk.BooleanVar(value=False), "fade": tk.BooleanVar(value=False), "boot_anim": tk.BooleanVar(value=True),
                       "detent_led": tk.BooleanVar(value=False), "key_toast": tk.BooleanVar(value=False), "dial_lock": tk.BooleanVar(value=False)})
        self.rep = [tk.BooleanVar(value=False) for _ in range(5)]
        self.w15 = []

        def reg15(w):
            self.w15.append(w)
            return w
        sw = lambda key, text: (lambda f: reg15(ctk.CTkSwitch(f, text=text, variable=self.v[key], command=lambda k=key: self.apply(**{k: bool(self.v[k].get())}))))   # noqa: E731
        m15 = lambda var, table, key, width=200: (lambda f: reg15(ctk.CTkOptionMenu(f, values=[n for n, _v in table], variable=var, width=width,   # noqa: E731
                                                                                     command=lambda lab, k=key, t=table: self.apply(**{k: _value(t, lab)}))))
        row(4, "Colour theme", m15(self.v["theme"], THEMES, "theme", 240), sw("tint", "tint the accent per layer"))
        row(5, "Display", m15(self.v["rotation"], ROTATIONS, "rotation", 130), sw("pixel_shift", "pixel shift (against burn-in)"), sw("fade", "fade between screens"))
        def make_splash(f):
            self.splash = reg15(ctk.CTkEntry(f, width=170, placeholder_text="start-up name (12 chars)"))
            self.splash.bind("<Return>", lambda _e: self.apply(splash=self.splash.get().strip()))
            return self.splash
        row(6, "Start-up", sw("boot_anim", "start-up animation"), make_splash)
        row(7, "Feedback", sw("detent_led", "LED tick on every dial detent"), sw("key_toast", "show the key's name when pressed"))
        row(8, "Dial", sw("dial_lock", "lock the dial (turns do nothing; a click still opens the menu)"))
        row(9, "Key repeat", *[(lambda f, i=i: reg15(ctk.CTkCheckBox(f, text=f"K{i + 1}", variable=self.rep[i], width=60, command=self._repeat_changed))) for i in range(5)],
            lambda f: ctk.CTkLabel(f, text="  hold the key to repeat its action (for arrow keys, volume ...)"))
        self.note = ui.muted(body, "", wraplength=860)
        self.note.grid(row=10, column=0, columnspan=3, sticky="w", padx=6, pady=(2, 0))
        self._level_job = None
        self.refresh()

    def _reg(self, w):
        self.widgets.append(w)
        return w

    # ---- availability
    def refresh(self):
        app = self.app
        ok = has_cap(app, "dialaccel")
        for w in self.widgets:
            w.configure(state="normal" if ok else "disabled")
        for w in self.w15:
            w.configure(state="normal" if has_cap(app, "themes") else "disabled")
        if not app.dev.connected:
            self.note.configure(text="Connect the pad to change these. They are stored on the pad.")
        elif not ok:
            self.note.configure(text="This pad's firmware is older than 1.3 - update it (Firmware card below) to get these settings.")
        elif not has_cap(app, "themes"):
            self.note.configure(text="The look-and-feel options (themes, rotation, key names ...) need firmware 1.5 - update the pad (Firmware card below).")
        else:
            self.note.configure(text="Night dimming needs the pad's clock to be set (the app does that on connect). Everything here is stored on the pad. "
                                     "Rotation 90 / 270 turns the picture on the pad; the screenshot and the twin always show it upright.")

    def load(self, st):
        """Fill the controls from the pad's {"evt":"settings"} answer."""
        self.busy = True
        try:
            self.v["dial_accel"].set(_label(DIAL, st.get("dial_accel", 0)))
            self.v["clock_style"].set(_label(CLOCKS, st.get("clock_style", 0)))
            self.v["saver_s"].set(_label(SAVER_TIMES, st.get("saver_s", 0)))
            self.v["saver_style"].set(_label(SAVER_STYLES, st.get("saver_style", 1), 0))
            self.v["night_on"].set(bool(st.get("night_on")))
            self.v["night_from"].set(f"{int(st.get('night_from', 22)):02d}:00")
            self.v["night_to"].set(f"{int(st.get('night_to', 7)):02d}:00")
            self.level.set(max(5, min(150, int(st.get("night_level", 30)))))
            self.v["theme"].set(_label(THEMES, st.get("theme", 0)))
            self.v["rotation"].set(_label(ROTATIONS, st.get("rotation", 0)))
            for k in ("tint", "pixel_shift", "fade", "detent_led", "key_toast", "dial_lock"):
                self.v[k].set(bool(st.get(k)))
            self.v["boot_anim"].set(bool(st.get("boot_anim", True)))
            self.splash.delete(0, "end")
            self.splash.insert(0, st.get("splash", ""))
            mask = int(st.get("repeat_mask", 0))
            for i, var in enumerate(self.rep):
                var.set(bool((mask >> i) & 1))
        finally:
            self.busy = False

    def on_connected(self):
        self.refresh()
        if has_cap(self.app, "dialaccel"):
            self.app.bg(lambda: self.app.dev.request({"cmd": "settings"}), self.load, "Reading the pad's settings failed")

    # ---- changes
    def apply(self, **fields):
        if self.busy or not has_cap(self.app, "dialaccel"):
            return
        self.app.bg(lambda: self.app.dev.request({"cmd": "settings", **fields}), lambda _r: self.app.set_status("Pad setting changed"), "Pad setting failed")

    def _repeat_changed(self):
        self.apply(repeat_mask=sum(1 << i for i, v in enumerate(self.rep) if v.get()))

    def _night_changed(self):
        self.apply(night_on=bool(self.v["night_on"].get()), night_from=int(self.v["night_from"].get()[:2]),
                   night_to=int(self.v["night_to"].get()[:2]), night_level=int(self.level.get()))

    def _level_moved(self):
        if self._level_job:
            self.app.after_cancel(self._level_job)
        self._level_job = self.app.after(400, self._night_changed)            # send when the slider rests, not on every pixel


def gesture_text(slot, g):
    return next((n for n, v in DIAL_PRESS.items() if v == slot), f"K{slot}") if g == "press" else f"K{slot}  {_label(GESTURES, g)}"


def gesture_msgs(app, existing, resolve, caps=None):
    """Requests that make the pad's hold / double-tap actions equal the app's configuration.
    existing: {(layer, slot, 'hold'|'double')} flags found on the pad; resolve(cat, action) -> (type, val) | None.
    -> list of dicts for dev.request()."""
    msgs = []
    wanted = set()
    for k, m in sorted(app.cfg.get("gestures", {}).items()):
        layer, slot, g = parse_gesture_key(k)
        need = SLOT_CAP.get(slot) if g == "press" else ("tapdance" if g == "triple" else None)
        if need and caps is not None and need not in caps:
            continue                                                         # an older pad cannot do it: keep it in the settings, send nothing
        spec = resolve(m["cat"], m["action"])
        if not spec:
            continue
        wanted.add((layer, slot, g))
        msg = {"cmd": "remap", "key": slot, "type": spec[0], "val": spec[1]}
        if g != "press":
            msg["gesture"] = g
        if layer:
            msg["layer"] = layer
        msgs.append(msg)
    for (layer, slot, g) in sorted(existing - wanted):                       # on the pad but no longer wanted
        msg = {"cmd": "remap", "key": slot, "clear": True}
        if g != "press":
            msg["gesture"] = g
        if layer:
            msg["layer"] = layer
        msgs.append(msg)
    return msgs


class GesturePanel:
    def __init__(self, app, parent):
        self.app = app
        box = ctk.CTkFrame(parent)
        box.pack(fill="x", pady=5, padx=2)
        ctk.CTkLabel(box, text="Key gestures: hold, double-tap, triple-tap, chords, dial clicks", font=ui.font(15, "bold"), anchor="w").pack(anchor="w", padx=16, pady=(14, 2))
        ui.muted(box, "Give K1 to K5 more actions. A key that has a hold, double-tap or triple-tap action reacts when you let go (a multi-tap waits "
                 "a quarter of a second to see whether another tap follows); keys without one still act the instant you press them. A chord is two neighbouring keys pressed "
                 "together (those two keys wait 45 ms to see whether the other follows). Hold / double-tap need firmware 1.3, the rest 1.5.",
                 wraplength=880).pack(anchor="w", padx=16, pady=(0, 6))
        self.list = ctk.CTkFrame(box, fg_color="transparent")
        self.list.pack(fill="x", padx=10, pady=4)
        add = ctk.CTkFrame(box, fg_color=ui.CARD2, corner_radius=10, border_width=0)
        add.pack(fill="x", padx=14, pady=(8, 14))
        self.layer = tk.StringVar(value="Layer 1")
        self.slot = tk.StringVar(value="K1")
        self.gest = tk.StringVar(value=GESTURES[0][0])
        cats = app.categories()
        self.cat = tk.StringVar(value=cats[0])
        self.action = tk.StringVar(value=app.names_for(cats[0])[0])
        r1 = ctk.CTkFrame(add, fg_color="transparent")
        r1.pack(fill="x", padx=10, pady=(10, 4))
        ctk.CTkOptionMenu(r1, values=["Layer 1", "Layer 2", "Layer 3"], variable=self.layer, width=100).pack(side="left", padx=(0, 6))
        ctk.CTkOptionMenu(r1, values=["K1", "K2", "K3", "K4", "K5"] + list(DIAL_PRESS), variable=self.slot, width=270).pack(side="left", padx=6)
        ctk.CTkOptionMenu(r1, values=[n for n, _g in GESTURES], variable=self.gest, width=170).pack(side="left", padx=6)
        ui.muted(r1, "(the gesture choice only applies to K1-K5; chords and dial clicks need firmware 1.5)").pack(side="left", padx=6)
        r2 = ctk.CTkFrame(add, fg_color="transparent")
        r2.pack(fill="x", padx=10, pady=(4, 10))
        self.cat_menu = ctk.CTkOptionMenu(r2, values=cats, variable=self.cat, width=150, command=self._cat_changed)
        self.cat_menu.pack(side="left", padx=(0, 6))
        self.action_menu = ctk.CTkOptionMenu(r2, values=app.names_for(cats[0]), variable=self.action, width=260)
        self.action_menu.pack(side="left", padx=6)
        ctk.CTkButton(r2, text="Assign", width=90, command=self.assign).pack(side="left", padx=6)
        self.refresh()

    def _cat_changed(self, cat):
        names = self.app.names_for(cat) or [""]
        self.action_menu.configure(values=names)
        self.action.set(names[0])

    def reload_categories(self):                                  # custom actions appear / disappear
        cats = self.app.categories()
        self.cat_menu.configure(values=cats)
        if self.cat.get() not in cats:
            self.cat.set(cats[0])
            self._cat_changed(cats[0])

    def assign(self):
        app = self.app
        if not self.action.get():
            return app.set_status("Pick an action first", error=True)
        layer = int(self.layer.get().split()[-1]) - 1
        if self.slot.get() in DIAL_PRESS:
            slot, g = DIAL_PRESS[self.slot.get()], "press"
        else:
            slot, g = int(self.slot.get()[1:]), _value(GESTURES, self.gest.get(), 0)
        app.cfg.setdefault("gestures", {})[gesture_key(layer, slot, g)] = {"cat": self.cat.get(), "action": self.action.get()}
        app.save_cfg()
        self.refresh()
        app.push_gestures(f"{gesture_text(slot, g)} on layer {layer + 1}: {self.action.get()}")

    def remove(self, key):
        self.app.cfg["gestures"].pop(key, None)
        self.app.save_cfg()
        self.refresh()
        self.app.push_gestures("Gesture removed")

    def refresh(self):
        for w in self.list.winfo_children():
            w.destroy()
        items = sorted(self.app.cfg.get("gestures", {}).items())
        if not items:
            ui.muted(self.list, "No gestures yet.").pack(anchor="w", padx=8, pady=8)
        for k, m in items:
            layer, slot, g = parse_gesture_key(k)
            row = ctk.CTkFrame(self.list, fg_color=ui.CARD2, corner_radius=8, border_width=0)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=f"Layer {layer + 1}  {gesture_text(slot, g)}", width=300, anchor="w", font=ui.font(13, "bold")).pack(side="left", padx=(12, 6), pady=8)
            ui.muted(row, f"{m['cat']}: {m['action']}", width=330).pack(side="left", padx=6)
            ui.secondary_button(row, "Remove", lambda k=k: self.remove(k), width=70).pack(side="right", padx=10)


def cpu_color(cpu):
    """CPU load 0..100 -> a calm RGB: green at idle, amber around 50 %, red at full load (about a third of full brightness)."""
    import colorsys
    c = max(0.0, min(100.0, float(cpu))) / 100.0
    r, g, b = colorsys.hsv_to_rgb((1.0 - c) / 3.0, 1.0, 0.38)          # hue 120 deg (green) -> 0 deg (red)
    return int(r * 255), int(g * 255), int(b * 255)


class ScreensCard:
    """Device page: which screens the dial menu / long press visits, the three reminders, the five habits (firmware 1.4)."""

    def __init__(self, app, body):
        self.app = app
        self.busy = False
        self.widgets = []
        ctk.CTkLabel(body, text="Screens in the cycle", anchor="w").grid(row=0, column=0, padx=6, pady=(4, 2), sticky="w")
        grid = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
        grid.grid(row=1, column=0, columnspan=4, sticky="w", padx=6)
        self.mask_vars = []
        self.new_boxes = []                                  # screens 13-20 need firmware 1.5
        for i, name in enumerate(SCREEN_NAMES):
            v = tk.BooleanVar(value=i < 6)
            self.mask_vars.append(v)
            cb = ctk.CTkCheckBox(grid, text=f"{i + 1} {name}", variable=v, width=150, command=self._mask_changed)
            cb.grid(row=i // 4, column=i % 4, padx=(0, 10), pady=3, sticky="w")
            self.widgets.append(cb)
            if i >= 12:
                self.new_boxes.append(cb)
        ui.muted(body, "Screens 7-20 are drawn by the pad itself and use K1-K5 (for example K1 = start / stop on the stopwatch, the dial steers the games); your key actions are back on every other screen.",
                 wraplength=860).grid(row=2, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 8))
        ctk.CTkLabel(body, text="Reminders", anchor="w", font=ui.font(13, "bold")).grid(row=3, column=0, padx=6, pady=(6, 2), sticky="w")
        self.rem = []
        for i in range(3):
            r = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
            r.grid(row=4 + i, column=0, columnspan=4, sticky="w", padx=6, pady=2)
            mins = ctk.CTkEntry(r, width=60, placeholder_text="min")
            txt = ctk.CTkEntry(r, width=220, placeholder_text=("Drink water", "Stand up", "Look away 20 s")[i])
            mins.pack(side="left")
            ctk.CTkLabel(r, text="minutes  -  show").pack(side="left", padx=6)
            txt.pack(side="left", padx=4)
            tb = ui.secondary_button(r, "Show now", lambda i=i: self.test_reminder(i), width=90)
            tb.pack(side="left", padx=6)
            self.rem.append((mins, txt))
            self.widgets += [mins, txt, tb]
        r = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
        r.grid(row=7, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 8))
        b = ctk.CTkButton(r, text="Save reminders", width=130, command=self.save_reminders)
        b.pack(side="left")
        ui.muted(r, "  0 or empty = off. The pad shows the text full-screen and also tells this app (a notification).").pack(side="left")
        self.widgets.append(b)
        ctk.CTkLabel(body, text="Habits (screen 12): five things to tick off every day", anchor="w", font=ui.font(13, "bold")).grid(row=8, column=0, columnspan=3, padx=6, pady=(6, 2), sticky="w")
        r = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
        r.grid(row=9, column=0, columnspan=4, sticky="w", padx=6)
        self.hab = []
        for i, d in enumerate(("Water", "Move", "Read", "Sleep", "Focus")):
            e = ctk.CTkEntry(r, width=110, placeholder_text=d)
            e.pack(side="left", padx=(0, 6))
            self.hab.append(e)
            self.widgets.append(e)
        b = ctk.CTkButton(r, text="Save names", width=100, command=self.save_habits)
        b.pack(side="left", padx=6)
        b2 = ui.secondary_button(r, "Refresh", self.load_habits, width=80)
        b2.pack(side="left")
        self.widgets += [b, b2]
        self.habit_lbl = ui.muted(body, "", wraplength=860)
        self.habit_lbl.grid(row=10, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 0))
        self.note = ui.muted(body, "", wraplength=860)
        self.note.grid(row=11, column=0, columnspan=4, sticky="w", padx=6, pady=(4, 0))
        self.refresh()

    def _ok(self):
        return has_cap(self.app, "screens")

    def _need(self):
        """True (after saying why) when the pad cannot do this."""
        if self._ok():
            return False
        self.app.set_status("The pad needs firmware 1.4 for this (Device -> Firmware).", error=True)
        return True

    def refresh(self):
        ok = self._ok()
        for w in self.widgets:
            w.configure(state="normal" if ok else "disabled")
        if ok and not has_cap(self.app, "games"):
            for w in self.new_boxes:
                w.configure(state="disabled")
        self.note.configure(text="Connect the pad to change these." if not self.app.dev.connected else
                            "" if ok else "This pad's firmware is older than 1.4 - update it (Firmware card below) to get these screens and reminders.")

    def on_connected(self):
        self.refresh()
        if self._ok():
            self.app.bg(lambda: [self.app.dev.request({"cmd": "settings"}), self.app.dev.request({"cmd": "reminders"}), self.app.dev.request({"cmd": "habits"})], self.load, "Reading the pad's screens failed")

    def load(self, res):
        st, rm, hb = res
        self.busy = True
        try:
            mask = int(st.get("mode_mask", 0x3F))
            for i, v in enumerate(self.mask_vars):
                v.set(bool((mask >> i) & 1))
            for (mins, txt), item in zip(self.rem, rm.get("list", [])):
                mins.delete(0, "end"); txt.delete(0, "end")
                if item.get("m"):
                    mins.insert(0, str(item["m"]))
                    txt.insert(0, item.get("t", ""))
            for e, name in zip(self.hab, hb.get("names", [])):
                e.delete(0, "end"); e.insert(0, name)
        finally:
            self.busy = False
        self._habit_text(hb)

    def _habit_text(self, hb):
        if not hb.get("synced", True):
            return self.habit_lbl.configure(text="The pad's clock is not set yet, so habits cannot be ticked (the app sets it when it connects).")
        self.habit_lbl.configure(text="Today: " + ", ".join(f"{n} {'done' if t else '-'} ({s} d)" for n, t, s in zip(hb["names"], hb["today"], hb["streak"])))

    def mask_value(self):
        return sum(1 << i for i, v in enumerate(self.mask_vars) if v.get())

    def _mask_changed(self):
        if self.busy or not self._ok():
            return
        mask = self.mask_value()
        if not mask:
            self.busy = True
            self.mask_vars[0].set(True)
            self.busy = False
            self.app.set_status("At least one screen must stay in the cycle", error=True)
            mask = 1
        self.app.bg(lambda: self.app.dev.request({"cmd": "settings", "mode_mask": mask}), lambda _r: self.app.set_status("Screen cycle saved on the pad"), "Saving the screens failed")
        self.app.pad.mode_mask = mask

    def reminders_list(self):
        out = []
        for mins, txt in self.rem:
            m = mins.get().strip()
            t = txt.get().strip()
            if not m or m == "0" or not t:
                out.append({"m": 0, "t": ""})
                continue
            if not m.isdigit() or not 1 <= int(m) <= 1440:
                raise ValueError("minutes must be a number from 1 to 1440 (or 0 / empty = off)")
            if len(t) > 16 or any(not 32 <= ord(c) < 127 or c in "|;" for c in t):
                raise ValueError("the reminder text is at most 16 plain characters (no | or ;)")
            out.append({"m": int(m), "t": t})
        return out

    def save_reminders(self):
        if self._need():
            return
        try:
            lst = self.reminders_list()
        except ValueError as e:
            return self.app.set_status(f"Reminders not saved: {e}", error=True)
        self.app.bg(lambda: self.app.dev.request({"cmd": "reminders", "list": lst}), lambda _r: self.app.set_status("Reminders saved on the pad"), "Saving reminders failed")

    def test_reminder(self, i):
        if self._need():
            return
        self.app.bg(lambda: self.app.dev.request({"cmd": "reminders", "test": i}), None, "That reminder is empty - fill in and save it first")

    def save_habits(self):
        if self._need():
            return
        names = [e.get().strip() or d for e, d in zip(self.hab, ("Water", "Move", "Read", "Sleep", "Focus"))]
        if any(len(n) > 10 or "|" in n or any(not 32 <= ord(c) < 127 for c in n) for n in names):
            return self.app.set_status("Habit names: at most 10 plain characters each (no |)", error=True)
        self.app.bg(lambda: self.app.dev.request({"cmd": "habits", "names": names}), lambda r: (self._habit_text(r), self.app.set_status("Habit names saved on the pad")), "Saving habits failed")

    def load_habits(self):
        if self._need():
            return
        self.app.bg(lambda: self.app.dev.request({"cmd": "habits"}), self._habit_text, "Reading habits failed")


class ChoicePanel:
    """Automation page: one key that ALTERNATES between two actions (each press the other one) or picks one of up to six at RANDOM (firmware 1.4)."""

    def __init__(self, app, parent):
        self.app, self.picks = app, []
        box = ctk.CTkFrame(parent)
        box.pack(fill="x", pady=5, padx=2)
        ctk.CTkLabel(box, text="Alternating and random keys", font=ui.font(15, "bold"), anchor="w").pack(anchor="w", padx=16, pady=(14, 2))
        ui.muted(box, "Alternate: every press runs the other of two actions (mute / unmute, play / next ...). Random: every press runs one of 2-6 actions "
                 "(a different greeting, a random emoji). Pick the actions below, then assign the result to the selected key.", wraplength=880).pack(anchor="w", padx=16, pady=(0, 6))
        r = ctk.CTkFrame(box, fg_color=ui.CARD2, corner_radius=10, border_width=0)
        r.pack(fill="x", padx=14, pady=(4, 14))
        r1 = ctk.CTkFrame(r, fg_color="transparent")
        r1.pack(fill="x", padx=10, pady=(10, 4))
        self.kind = tk.StringVar(value="Alternate between two actions")
        ctk.CTkOptionMenu(r1, values=["Alternate between two actions", "Pick one at random"], variable=self.kind, width=240, command=lambda _v: self._kind_changed()).pack(side="left")
        cats = app.categories()
        self.cat = tk.StringVar(value=cats[0])
        self.action = tk.StringVar(value=app.names_for(cats[0])[0])
        self.cat_menu = ctk.CTkOptionMenu(r1, values=cats, variable=self.cat, width=150, command=self._cat_changed)
        self.cat_menu.pack(side="left", padx=6)
        self.action_menu = ctk.CTkOptionMenu(r1, values=app.names_for(cats[0]), variable=self.action, width=240)
        self.action_menu.pack(side="left", padx=6)
        ctk.CTkButton(r1, text="Add", width=60, command=self.add).pack(side="left", padx=6)
        self.list_lbl = ctk.CTkLabel(r, text="", anchor="w", justify="left", wraplength=860)
        self.list_lbl.pack(anchor="w", padx=12, pady=2)
        r2 = ctk.CTkFrame(r, fg_color="transparent")
        r2.pack(fill="x", padx=10, pady=(4, 10))
        ctk.CTkButton(r2, text="Assign to the selected key", width=190, command=self.assign).pack(side="left")
        ui.secondary_button(r2, "Clear the list", self.clear, width=110).pack(side="left", padx=8)
        self._refresh()

    def _cat_changed(self, cat):
        names = self.app.names_for(cat) or [""]
        self.action_menu.configure(values=names)
        self.action.set(names[0])

    def reload_categories(self):
        cats = self.app.categories()
        self.cat_menu.configure(values=cats)
        if self.cat.get() not in cats:
            self.cat.set(cats[0])
            self._cat_changed(cats[0])

    def _limit(self):
        return 2 if self.kind.get().startswith("Alternate") else 6

    def _kind_changed(self):
        del self.picks[self._limit():]
        self._refresh()

    def add(self):
        if len(self.picks) >= self._limit():
            return self.app.set_status(f"At most {self._limit()} actions here", error=True)
        spec = self.app.resolve_action(self.cat.get(), self.action.get())
        if not spec or spec[0] in ("none", "toggle", "random") or not self.app.spec_valid(spec):
            return self.app.set_status("That action cannot be part of an alternating / random key", error=True)
        self.picks.append((self.cat.get(), self.action.get(), spec))
        self._refresh()

    def clear(self):
        self.picks.clear()
        self._refresh()

    def _refresh(self):
        self.list_lbl.configure(text=("  /  ".join(f"{i + 1}. {a}" for i, (_c, a, _s) in enumerate(self.picks))) or f"(add {self._limit()} action{'s' if self._limit() > 1 else ''})")

    def assign(self):
        alt = self.kind.get().startswith("Alternate")
        if len(self.picks) < 2 or (alt and len(self.picks) != 2):
            return self.app.set_status("Alternate needs exactly 2 actions, random needs 2 to 6", error=True)
        if self.app.dev.connected and not has_cap(self.app, "toggle"):
            return self.app.set_status("Alternating / random keys need firmware 1.4 - update the pad (Device -> Firmware).", error=True)
        subs = [{"type": s[0], "val": s[1]} for _c, _a, s in self.picks]
        names = " / ".join(a for _c, a, _s in self.picks)
        label = (f"Alternate: {names}" if alt else f"Random: {names}")[:48]
        self.app.assign_spec(("toggle" if alt else "random", subs), label)


class ComputerCard:
    """Device page: settings for the computer-side actions (AI key, screenshot folder, clipboard history, window layouts)."""

    def __init__(self, app, body):
        self.app = app
        cfg = app.cfg
        ctk.CTkLabel(body, text="AI action", anchor="w", font=ui.font(13, "bold")).grid(row=0, column=0, columnspan=3, padx=6, pady=(2, 2), sticky="w")
        r = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
        r.grid(row=1, column=0, columnspan=4, sticky="w", padx=6)
        self.ai_key = ctk.CTkEntry(r, width=300, show="*", placeholder_text="Anthropic API key (sk-ant-...)")
        self.ai_key.pack(side="left")
        if cfg["ai"].get("key"):
            self.ai_key.insert(0, cfg["ai"]["key"])
        self.ai_model = ctk.CTkEntry(r, width=230, placeholder_text="model")
        self.ai_model.pack(side="left", padx=6)
        self.ai_model.insert(0, cfg["ai"].get("model", ""))
        ctk.CTkButton(r, text="Save", width=70, command=self.save_ai).pack(side="left", padx=6)
        ui.muted(body, "The 'Ask the AI' key action sends your prompt (and the clipboard, if you use {clipboard}) to the Claude API with YOUR key and types the answer. "
                 "The key is stored in this app's settings file in plain text.", wraplength=860).grid(row=2, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 8))
        ctk.CTkLabel(body, text="Screenshots", anchor="w", font=ui.font(13, "bold")).grid(row=3, column=0, padx=6, pady=(2, 2), sticky="w")
        r = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
        r.grid(row=4, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 8))
        self.shot_dir = ctk.CTkEntry(r, width=360, placeholder_text="folder (empty = your Pictures folder)")
        self.shot_dir.pack(side="left")
        if cfg.get("shot_dir"):
            self.shot_dir.insert(0, cfg["shot_dir"])
        ui.secondary_button(r, "Save", self.save_shot, width=70).pack(side="left", padx=6)
        ui.secondary_button(r, "Take one now", self.shot_now, width=110).pack(side="left", padx=2)
        ctk.CTkLabel(body, text="Clipboard history", anchor="w", font=ui.font(13, "bold")).grid(row=5, column=0, padx=6, pady=(2, 2), sticky="w")
        r = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
        r.grid(row=6, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 8))
        self.hist_var = tk.BooleanVar(value=bool(cfg.get("cliphist_on")))
        ctk.CTkSwitch(r, text="Remember the last 9 copied texts (memory only, never saved)", variable=self.hist_var, command=self.hist_toggled).pack(side="left")
        ui.secondary_button(r, "Forget them", lambda: app.cliphist.clear(), width=100).pack(side="left", padx=12)
        ctk.CTkLabel(body, text="Window layouts", anchor="w", font=ui.font(13, "bold")).grid(row=7, column=0, padx=6, pady=(2, 2), sticky="w")
        r = ctk.CTkFrame(body, fg_color="transparent", border_width=0)
        r.grid(row=8, column=0, columnspan=4, sticky="w", padx=6)
        self.layout_name = ctk.CTkEntry(r, width=170, placeholder_text="layout name, e.g. work")
        self.layout_name.pack(side="left")
        ctk.CTkButton(r, text="Save the current windows", width=170, command=self.save_layout).pack(side="left", padx=6)
        ui.secondary_button(r, "Restore", self.restore_layout, width=80).pack(side="left", padx=2)
        ui.secondary_button(r, "Delete", self.delete_layout, width=70).pack(side="left", padx=6)
        self.layout_lbl = ui.muted(body, "", wraplength=860)
        self.layout_lbl.grid(row=9, column=0, columnspan=4, sticky="w", padx=6, pady=(2, 0))
        self.refresh_layouts()

    def save_ai(self):
        self.app.cfg["ai"]["key"] = self.ai_key.get().strip()
        self.app.cfg["ai"]["model"] = self.ai_model.get().strip() or "claude-haiku-4-5-20251001"
        self.app.save_cfg()
        self.app.set_status("AI settings saved")

    def save_shot(self):
        self.app.cfg["shot_dir"] = self.shot_dir.get().strip()
        self.app.save_cfg()
        self.app.set_status("Screenshot folder saved")

    def shot_now(self):
        self.save_shot()
        self.app.bg(lambda: self.app.run_host("shot", "default"), lambda m: self.app.set_status(m), "Screenshot failed")

    def hist_toggled(self):
        self.app.cfg["cliphist_on"] = bool(self.hist_var.get())
        self.app.save_cfg()
        self.app.set_status("Clipboard history is on - copy something, then use the 'Type from clipboard history' action" if self.hist_var.get() else "Clipboard history is off and forgotten")

    def refresh_layouts(self):
        names = sorted(self.app.cfg["layouts"])
        self.layout_lbl.configure(text="Saved: " + ", ".join(f"{n} ({len(self.app.cfg['layouts'][n])} windows)" for n in names) if names else
                                  "No layouts yet. Arrange your windows, give the layout a name and press 'Save the current windows'. Then use the 'Window layout' key action.")

    def _name(self):
        n = self.layout_name.get().strip()
        if not n or len(n) > 30:
            self.app.set_status("Type a layout name (up to 30 characters)", error=True)
        return n

    def save_layout(self):
        n = self._name()
        if n:
            self.app.bg(lambda: self.app.run_host("layout", f"save {n}"), lambda m: (self.app.save_cfg(), self.refresh_layouts(), self.app.set_status(m)), "Saving the layout failed")

    def restore_layout(self):
        n = self._name()
        if n:
            self.app.bg(lambda: self.app.run_host("layout", n), lambda m: self.app.set_status(m), "Restoring the layout failed")

    def delete_layout(self):
        n = self._name()
        if n and self.app.cfg["layouts"].pop(n, None) is not None:
            self.app.save_cfg()
            self.refresh_layouts()
            self.app.set_status(f"Layout '{n}' deleted")
