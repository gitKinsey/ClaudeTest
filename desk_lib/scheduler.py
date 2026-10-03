"""Time-based automation: 'at 09:00 on weekdays switch to layer 2', 'every 30 min flash the LED', 'once at ...'.

Pure logic, no GUI and no clock of its own: Scheduler.tick(now) returns the entries that are due, so every rule is testable.
Entry:  {"id", "name", "enabled", "when": {...}, "do": {...}}
  when:  {"kind": "daily", "time": "09:00", "days": [0..6, Mon=0]}   {"kind": "every", "minutes": 30}   {"kind": "once", "at": "2026-10-03T15:00"}
  do:    {"kind": "layer", "n": 0|1|2|"next"|"prev"}  {"kind": "mode", "n": 1..6}  {"kind": "brightness", "n": 5..255}
         {"kind": "led", "mode": "auto|off|solid", "hex": "ff8800"}  {"kind": "host", "op": "...", "arg": "..."}  {"kind": "notify", "text": "..."}
A rule is skipped (not run late) when the app was not running within GRACE seconds of its time: waking up at 11:00 does not
switch to the 09:00 layer, and an old 'once' rule is retired instead of firing."""
import re
import uuid
from datetime import datetime, timedelta

GRACE = 120
DO_KINDS = ("layer", "mode", "brightness", "led", "host", "notify")
HOST_OPS = ("url", "app", "shell", "clipboard", "file", "notify", "snippet", "clip", "script",
            "appvol", "dnd", "audio_out", "mic", "shot", "translate", "ai", "webhook", "layout", "cliphist")


def parse_hhmm(s):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(s))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"'{s}' is not a time like 09:30")
    return int(m.group(1)), int(m.group(2))


def validate(entry):
    """Return a cleaned copy of the entry or raise ValueError with a readable reason."""
    if not isinstance(entry, dict):
        raise ValueError("not a rule")
    when, do = entry.get("when") or {}, entry.get("do") or {}
    out = {"id": str(entry.get("id") or uuid.uuid4().hex[:8]), "name": str(entry.get("name") or "").strip()[:40],
           "enabled": bool(entry.get("enabled", True))}
    k = when.get("kind")
    if k == "daily":
        h, m = parse_hhmm(when.get("time"))
        days = sorted({int(d) for d in (when.get("days") if when.get("days") is not None else range(7))})
        if not days or not all(0 <= d <= 6 for d in days):
            raise ValueError("pick at least one weekday")
        out["when"] = {"kind": "daily", "time": f"{h:02d}:{m:02d}", "days": days}
    elif k == "every":
        n = int(when.get("minutes", 0))
        if not 1 <= n <= 1440:
            raise ValueError("'every' needs 1 to 1440 minutes")
        out["when"] = {"kind": "every", "minutes": n}
    elif k == "once":
        try:
            at = datetime.fromisoformat(str(when.get("at")))
        except ValueError:
            raise ValueError("'once' needs a date and time like 2026-10-03 15:00") from None
        out["when"] = {"kind": "once", "at": at.replace(microsecond=0).isoformat(timespec="minutes")}
    else:
        raise ValueError("choose daily, every or once")
    dk = do.get("kind")
    if dk == "layer":
        n = do.get("n")
        if n not in ("next", "prev") and not (isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 2):
            raise ValueError("layer must be 1, 2, 3, next or prev")
        out["do"] = {"kind": "layer", "n": n}
    elif dk == "mode":
        n = int(do.get("n", 0))
        if not 1 <= n <= 6:
            raise ValueError("screen must be 1 to 6")
        out["do"] = {"kind": "mode", "n": n}
    elif dk == "brightness":
        n = int(do.get("n", 0))
        if not 5 <= n <= 255:
            raise ValueError("brightness must be 5 to 255")
        out["do"] = {"kind": "brightness", "n": n}
    elif dk == "led":
        mode = do.get("mode", "auto")
        if mode not in ("auto", "off", "solid", "breathe", "fire"):
            raise ValueError("LED mode must be auto, off, breathe, fire or solid")
        hx = str(do.get("hex") or "").lstrip("#")
        if mode == "solid" and not re.fullmatch(r"[0-9a-fA-F]{6}", hx):
            raise ValueError("a solid LED needs a colour like ff8800")
        out["do"] = {"kind": "led", "mode": mode, **({"hex": hx.lower()} if mode == "solid" else {})}
    elif dk == "host":
        op, arg = do.get("op"), str(do.get("arg") or "")
        if op not in HOST_OPS or (op != "clipboard" and not arg):
            raise ValueError("pick a computer action and fill in its argument")
        out["do"] = {"kind": "host", "op": op, "arg": arg[:400]}
    elif dk == "notify":
        text = str(do.get("text") or "").strip()
        if not text:
            raise ValueError("write the reminder text")
        out["do"] = {"kind": "notify", "text": text[:200]}
    else:
        raise ValueError("choose what the rule should do")
    return out


def describe(entry):
    w, d = entry["when"], entry["do"]
    if w["kind"] == "daily":
        days = w["days"]
        names = "every day" if len(days) == 7 else "weekdays" if days == [0, 1, 2, 3, 4] else "weekends" if days == [5, 6] else \
            ", ".join(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[x] for x in days)
        when = f"{names} at {w['time']}"
    elif w["kind"] == "every":
        when = f"every {w['minutes']} min"
    else:
        when = "once at " + w["at"].replace("T", " ")
    k = d["kind"]
    what = {"layer": lambda: f"switch to layer {d['n'] + 1}" if isinstance(d["n"], int) else f"switch to the {d['n']} layer",
            "mode": lambda: f"show screen {d['n']}", "brightness": lambda: f"set brightness {d['n']}",
            "led": lambda: "LED " + (d["mode"] if d["mode"] != "solid" else "#" + d["hex"]),
            "host": lambda: f"{d['op']}: {d.get('arg', '')}", "notify": lambda: f"remind: {d['text']}"}[k]()
    return f"{when}: {what}"


class Scheduler:
    def __init__(self, entries_fn, grace=GRACE):
        self.entries_fn, self.grace = entries_fn, grace
        self._last = {}                                   # id -> last fire time (every) / date key (daily)

    def tick(self, now):
        """Entries that are due at `now` (a naive local datetime). A fired 'once' rule comes back with enabled=False via .retire."""
        due = []
        for e in self.entries_fn():
            if not e.get("enabled", True):
                continue
            w, eid = e["when"], e["id"]
            if w["kind"] == "daily":
                if now.weekday() not in w["days"]:
                    continue
                h, m = parse_hhmm(w["time"])
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                key = target.date().isoformat()
                if now >= target and (now - target).total_seconds() <= self.grace and self._last.get(eid) != key:
                    self._last[eid] = key
                    due.append(e)
            elif w["kind"] == "every":
                last = self._last.get(eid)
                if last is None:
                    self._last[eid] = now                       # first sight: the countdown starts now
                elif now - last >= timedelta(minutes=w["minutes"]):
                    self._last[eid] = now
                    due.append(e)
            else:
                at = datetime.fromisoformat(w["at"])
                if now >= at and self._last.get(eid) != "done":
                    self._last[eid] = "done"
                    if (now - at).total_seconds() <= self.grace:
                        due.append(e)
                    e["enabled"] = False                         # fired or too old: retire it either way
                    e["_retired"] = True
        return due

    def next_run(self, entry, now):
        """Human-readable next time for the UI, or ''."""
        w = entry["when"]
        if not entry.get("enabled", True):
            return ""
        if w["kind"] == "daily":
            h, m = parse_hhmm(w["time"])
            for add in range(8):
                t = (now + timedelta(days=add)).replace(hour=h, minute=m, second=0, microsecond=0)
                if t >= now and t.weekday() in w["days"]:
                    return t.strftime("%a %H:%M")
            return ""
        if w["kind"] == "every":
            last = self._last.get(entry["id"]) or now
            return (last + timedelta(minutes=w["minutes"])).strftime("%H:%M")
        return w["at"].replace("T", " ")
