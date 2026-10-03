"""Firmware 1.3 features in the app: the 'Pad behaviour' card (dial acceleration, clock style, screensaver, night dimming)
and the key-gesture panel (hold / double-tap actions). Everything the pad cannot do yet is greyed out with a reason."""
import tkinter as tk

import customtkinter as ctk

from desk_lib import ui

DIAL = [("Off", 0), ("Normal", 1), ("Fast", 2)]
CLOCKS = [("Classic (analog + digital)", 0), ("Digital", 1), ("Binary", 2), ("Minimal", 3)]
SAVER_TIMES = [("Off", 0), ("1 minute", 60), ("5 minutes", 300), ("10 minutes", 600), ("30 minutes", 1800), ("1 hour", 3600)]
SAVER_STYLES = [("Starfield", 1), ("Matrix rain", 2), ("Dim drifting clock", 3)]
HOURS = [f"{h:02d}:00" for h in range(24)]
GESTURES = [("Hold the key", "hold"), ("Double-tap the key", "double")]


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
        self.note = ui.muted(body, "", wraplength=860)
        self.note.grid(row=4, column=0, columnspan=3, sticky="w", padx=6, pady=(2, 0))
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
        if not app.dev.connected:
            self.note.configure(text="Connect the pad to change these. They are stored on the pad.")
        elif not ok:
            self.note.configure(text="This pad's firmware is older than 1.3 - update it (Firmware card below) to get these settings.")
        else:
            self.note.configure(text="Night dimming needs the pad's clock to be set (the app does that on connect). Everything here is stored on the pad.")

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

    def _night_changed(self):
        self.apply(night_on=bool(self.v["night_on"].get()), night_from=int(self.v["night_from"].get()[:2]),
                   night_to=int(self.v["night_to"].get()[:2]), night_level=int(self.level.get()))

    def _level_moved(self):
        if self._level_job:
            self.app.after_cancel(self._level_job)
        self._level_job = self.app.after(400, self._night_changed)            # send when the slider rests, not on every pixel


def gesture_msgs(app, existing, resolve):
    """Requests that make the pad's hold / double-tap actions equal the app's configuration.
    existing: {(layer, slot, 'hold'|'double')} flags found on the pad; resolve(cat, action) -> (type, val) | None.
    -> list of dicts for dev.request()."""
    msgs = []
    wanted = set()
    for k, m in sorted(app.cfg.get("gestures", {}).items()):
        layer, slot, g = parse_gesture_key(k)
        spec = resolve(m["cat"], m["action"])
        if not spec:
            continue
        wanted.add((layer, slot, g))
        msg = {"cmd": "remap", "key": slot, "gesture": g, "type": spec[0], "val": spec[1]}
        if layer:
            msg["layer"] = layer
        msgs.append(msg)
    for (layer, slot, g) in sorted(existing - wanted):                       # on the pad but no longer wanted
        msg = {"cmd": "remap", "key": slot, "gesture": g, "clear": True}
        if layer:
            msg["layer"] = layer
        msgs.append(msg)
    return msgs


class GesturePanel:
    def __init__(self, app, parent):
        self.app = app
        box = ctk.CTkFrame(parent)
        box.pack(fill="x", pady=5, padx=2)
        ctk.CTkLabel(box, text="Key gestures: hold and double-tap", font=ui.font(15, "bold"), anchor="w").pack(anchor="w", padx=16, pady=(14, 2))
        ui.muted(box, "Give K1 to K5 a second and third action. A key that has a hold or double-tap action reacts when you let go (a double-tap waits "
                 "a quarter of a second to see whether a second tap follows); keys without one still act the instant you press them. Needs firmware 1.3.",
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
        ctk.CTkOptionMenu(r1, values=["K1", "K2", "K3", "K4", "K5"], variable=self.slot, width=70).pack(side="left", padx=6)
        ctk.CTkOptionMenu(r1, values=[n for n, _g in GESTURES], variable=self.gest, width=170).pack(side="left", padx=6)
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
        layer, slot, g = int(self.layer.get().split()[-1]) - 1, int(self.slot.get()[1:]), _value(GESTURES, self.gest.get(), 0)
        app.cfg.setdefault("gestures", {})[gesture_key(layer, slot, g)] = {"cat": self.cat.get(), "action": self.action.get()}
        app.save_cfg()
        self.refresh()
        app.push_gestures(f"{self.slot.get()} {self.gest.get().lower()} on layer {layer + 1}: {self.action.get()}")

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
            ctk.CTkLabel(row, text=f"Layer {layer + 1}  K{slot}  {_label(GESTURES, g)}", width=270, anchor="w", font=ui.font(13, "bold")).pack(side="left", padx=(12, 6), pady=8)
            ui.muted(row, f"{m['cat']}: {m['action']}", width=330).pack(side="left", padx=6)
            ui.secondary_button(row, "Remove", lambda k=k: self.remove(k), width=70).pack(side="right", padx=10)


def cpu_color(cpu):
    """CPU load 0..100 -> a calm RGB: green at idle, amber around 50 %, red at full load (about a third of full brightness)."""
    import colorsys
    c = max(0.0, min(100.0, float(cpu))) / 100.0
    r, g, b = colorsys.hsv_to_rgb((1.0 - c) / 3.0, 1.0, 0.38)          # hue 120 deg (green) -> 0 deg (red)
    return int(r * 255), int(g * 255), int(b * 255)
