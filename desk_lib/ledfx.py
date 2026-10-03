"""LED colour sources besides the CPU load: a mood that follows the time of day, and alert detection (something new happened).
Pure functions / small classes, tested without a pad."""
import datetime as dt

# (hour, (r, g, b)) key frames over 24 h; linear blend between them
MOOD = [(0, (10, 0, 40)), (6, (255, 90, 20)), (9, (255, 200, 120)), (13, (255, 255, 235)),
        (18, (255, 150, 60)), (21, (170, 40, 90)), (24, (10, 0, 40))]


def mood_color(now=None):
    """Warm sunrise, white noon, orange evening, deep violet night."""
    now = now or dt.datetime.now()
    h = now.hour + now.minute / 60.0
    for (h0, c0), (h1, c1) in zip(MOOD, MOOD[1:]):
        if h0 <= h <= h1:
            f = (h - h0) / (h1 - h0)
            return tuple(int(round(a + (b - a) * f)) for a, b in zip(c0, c1))
    return MOOD[0][1]


class AlertTracker:
    """Feeds on snapshots {"mail": 3, "ci": "success", "event": "Standup"} and reports what changed in a way worth a LED blink.
    The first snapshot only sets the baseline (no alert at start-up)."""

    def __init__(self):
        self._last = None

    def update(self, snap):
        """-> list of (name, hex, times) alerts."""
        out, last = [], self._last
        self._last = dict(snap)
        if last is None:
            return out
        for name, val in snap.items():
            old = last.get(name)
            if name.startswith("ci"):
                done = ("success", "failure", "cancelled", "timed out", "startup failure")       # runs that are still going do not blink
                if val in done and val != old:
                    bad = val != "success"
                    out.append((name, "ff2030" if bad else "20ff60", 3 if bad else 1))
            elif name == "event" and val and val != old:
                out.append(("event", "ffb020", 2))
            elif isinstance(val, int) and not isinstance(val, bool) and val > (old if isinstance(old, int) else 0):
                out.append((name, "20a0ff", 2))              # a badge count grew: new mail / message
        return out
