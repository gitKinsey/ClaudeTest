"""Display -> Look: brightness, screen mode, the pad's own look-and-feel settings (stored on the pad), dimming and LED effects."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSlider

from core.base import MODE_CHOICES
from desk_lib import audio, padconst
from ui_qt.forms import cfg_switch, combo, line, val
from ui_qt.widgets import Field
from ui_qt.layouts import PageGrid
from ui_qt.widgets import Card, CheckBox, ToggleRow, button, flow, label, separator


def _table_combo(table, key, apply, width=16):
    c = combo([n for n, _v in table], table[0][0], width)
    c.activated.connect(lambda _i: apply(**{key: padconst.value_of(table, val(c))}))
    return c


def build_look_tab(page):
    e = page.engine
    shell = page.shell
    loaders = []                                     # callables(st) that fill one control from the pad's settings
    gated = []                                       # (widget, capability) pairs: enabled only when the pad has the capability

    def apply(**fields):
        e.pad_settings_apply(**fields)

    def table_row(card, text, table, key, width=16, cap="dialaccel"):
        c = _table_combo(table, key, apply, width)
        card.add(Field(text, c))
        gated.append((c, cap))
        loaders.append(lambda st, c=c, t=table, k=key, d=0: c.setCurrentIndex(max(0, c.findData(padconst.label_of(t, st.get(k, d))))))
        return c

    def sw(card, text, key, default=False, cap="themes"):
        t = ToggleRow(text, default)
        t.toggled.connect(lambda v, k=key: apply(**{k: bool(v)}))
        card.add(t)
        gated.append((t, cap))
        loaders.append(lambda st, t=t, k=key, d=default: t.setChecked(bool(st.get(k, d))))
        return t

    # ---------------------------------------------------------------- display
    d = Card("Display")
    bright = QSlider(Qt.Orientation.Horizontal)
    bright.setRange(5, 255)
    bright.setValue(e.pad.brightness)
    bright.valueChanged.connect(lambda v: e.set_brightness(v))
    d.add(Field("Brightness", bright))
    mode = combo(MODE_CHOICES, MODE_CHOICES[e.pad.mode - 1], 16)
    mode.activated.connect(lambda i: e.set_mode(i + 1))
    d.add(Field("Screen mode", mode))

    def pad_state(kind):
        if kind == "mode":
            mode.blockSignals(True)
            mode.setCurrentIndex(e.pad.mode - 1)
            mode.blockSignals(False)
        elif kind == "bright":
            bright.blockSignals(True)
            bright.setValue(e.pad.brightness)
            bright.blockSignals(False)
    e.on("pad_state", pad_state)
    d.add(separator())
    table_row(d, "Colour theme", padconst.THEMES, "theme", 22, "themes")
    table_row(d, "Rotation", padconst.ROTATIONS, "rotation", 12, "themes")
    sw(d, "tint the accent per layer", "tint")
    sw(d, "pixel shift (against burn-in)", "pixel_shift")
    sw(d, "fade between screens", "fade")
    sw(d, "start-up animation", "boot_anim", True)
    splash = line("start-up name (12 chars)", "", 200)
    splash.returnPressed.connect(lambda: apply(splash=splash.text().strip()))
    d.add(Field("Start-up name", splash))
    d.add(flow(button("Save name", "secondary", lambda: apply(splash=splash.text().strip()))))
    gated.append((splash, "themes"))
    loaders.append(lambda st: splash.setText(st.get("splash", "")))
    sw(d, "LED tick on every dial detent", "detent_led")
    sw(d, "show the key's name when pressed", "key_toast")
    sw(d, "lock the dial (turns do nothing; a click still opens the menu)", "dial_lock")
    d.add(label("Key repeat: hold the key to repeat its action (for arrow keys, volume ...)", "muted", wrap=True))
    rep = [CheckBox(f"K{i + 1}") for i in range(5)]
    for r_ in rep:
        r_.toggled.connect(lambda _v: apply(repeat_mask=sum(1 << i for i, b in enumerate(rep) if b.isChecked())))
        gated.append((r_, "themes"))
    d.add(flow(*rep))
    loaders.append(lambda st: [b.setChecked(bool((int(st.get("repeat_mask", 0)) >> i) & 1)) for i, b in enumerate(rep)])
    d.stretch()

    # ---------------------------------------------------------------- dial, clock, saver, night
    b = Card("Dial, clock and screensaver")
    table_row(b, "Dial acceleration", padconst.DIAL, "dial_accel", 12)
    table_row(b, "Clock style", padconst.CLOCKS, "clock_style", 22)
    sv_t = _table_combo(padconst.SAVER_TIMES, "saver_s", apply, 12)
    sv_s = _table_combo(padconst.SAVER_STYLES, "saver_style", apply, 18)
    b.add(Field("Screensaver after", sv_t))
    b.add(Field("Screensaver style", sv_s))
    gated += [(sv_t, "dialaccel"), (sv_s, "dialaccel")]
    loaders.append(lambda st: (sv_t.setCurrentIndex(max(0, sv_t.findData(padconst.label_of(padconst.SAVER_TIMES, st.get("saver_s", 0))))),
                               sv_s.setCurrentIndex(max(0, sv_s.findData(padconst.label_of(padconst.SAVER_STYLES, st.get("saver_style", 1)))))))
    b.add(separator())
    b.add(label("Night dimming", "h3"))
    night = ToggleRow("on", False)
    n_from = combo(padconst.HOURS, "22:00", 6)
    n_to = combo(padconst.HOURS, "07:00", 6)
    level = QSlider(Qt.Orientation.Horizontal)
    level.setRange(5, 150)
    level.setValue(30)

    def night_changed(*_a):
        apply(night_on=night.isChecked(), night_from=int(val(n_from)[:2]), night_to=int(val(n_to)[:2]), night_level=int(level.value()))
    from PySide6.QtCore import QTimer
    rest = QTimer(b)
    rest.setSingleShot(True)
    rest.timeout.connect(night_changed)
    night.toggled.connect(night_changed)
    n_from.activated.connect(night_changed)
    n_to.activated.connect(night_changed)
    level.valueChanged.connect(lambda _v: rest.start(400))                # sent when the slider rests, not on every pixel
    b.add(night)
    b.add(Field("from", n_from))
    b.add(Field("to", n_to))
    b.add(Field("max brightness", level))
    gated += [(night, "dialaccel"), (n_from, "dialaccel"), (n_to, "dialaccel"), (level, "dialaccel")]

    def load_night(st):
        night.setChecked(bool(st.get("night_on")))
        n_from.setCurrentIndex(max(0, n_from.findData(f"{int(st.get('night_from', 22)):02d}:00")))
        n_to.setCurrentIndex(max(0, n_to.findData(f"{int(st.get('night_to', 7)):02d}:00")))
        level.setValue(max(5, min(150, int(st.get("night_level", 30)))))
    loaders.append(load_night)
    note = label("", "muted", wrap=True)
    b.add(note)
    b.stretch()

    def state():
        ok = e.pad_settings_ok()
        for w, cap in gated:
            w.setEnabled(bool(ok and (cap == "dialaccel" or e.pad_settings_ok15())))
        note.setText(e.pad_settings_note())
    e.on("pad_settings_state", state)

    def loaded(st):
        e.pad_settings_busy = True
        try:
            for fn in loaders:
                fn(st)
        finally:
            e.pad_settings_busy = False
    e.on("pad_settings", loaded)

    # ---------------------------------------------------------------- PC links: dim, LED
    p = Card("Pad and this PC")
    p.add(cfg_switch(e, "Dim the pad while this PC is locked", "dim_lock"))
    p.add(cfg_switch(e, "Dim the pad while a fullscreen window (video, game, presentation) is in front", "dim_fullscreen"))
    dim = QSlider(Qt.Orientation.Horizontal)
    dim.setRange(5, 120)
    dim.setValue(int(e.cfg.get("dim_level", 25)))
    dim.valueChanged.connect(lambda v: (e.cfg.__setitem__("dim_level", int(v)), e.save_cfg()))
    p.add(Field("Dimmed brightness", dim))
    p.add(separator())
    p.add(cfg_switch(e, "Pad LED follows this PC's CPU load (green to red)", "led_cpu"))
    p.add(cfg_switch(e, "Pad LED follows the time of day (warm morning, white noon, violet night)", "led_mood"))
    la = cfg_switch(e, "Pad LED reacts to sound from the audio input (low = red, mid = green, high = blue)", "led_audio")
    viz = cfg_switch(e, "Send the PC's sound spectrum to the pad's SOUND screen (screen 20, firmware 1.5)", "viz_on")
    if not audio.available():
        la.setEnabled(False)
        la.setText("Sound-reactive LED: not available (pip install sounddevice numpy)")
        viz.setEnabled(False)
        viz.setText("Sound bars: not available (pip install sounddevice numpy)")
    p.add(la)
    p.add(viz)
    p.add(cfg_switch(e, "Blink the LED for a new mail badge, a CI result or an upcoming event (firmware 1.4)", "led_alerts"))
    p.stretch()
    state()
    return PageGrid({"display": d, "dial": b, "links": p},
                    {"wide": [[("display", 3)], [("dial", 3)], [("links", 3)]], "medium": [[("display", 3)], [("dial", 3), ("links", 3)]]},
                    order=["display", "dial", "links"], titles={"display": "Display", "dial": "Dial & clock", "links": "PC links"},
                    tabs_factory=shell.make_tabs, wide_min=1000, medium_min=720)
