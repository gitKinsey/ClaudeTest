"""Pad settings tables and gesture helpers (toolkit-free; shared by every front end)."""

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


def cpu_color(cpu):
    """CPU load 0..100 -> a calm RGB: green at idle, amber around 50 %, red at full load (about a third of full brightness)."""
    import colorsys
    c = max(0.0, min(100.0, float(cpu))) / 100.0
    r, g, b = colorsys.hsv_to_rgb((1.0 - c) / 3.0, 1.0, 0.38)          # hue 120 deg (green) -> 0 deg (red)
    return int(r * 255), int(g * 255), int(b * 255)


label_of, value_of = _label, _value
