"""Scheduled-rule building and execution (toolkit-free)."""
from desk_lib import scheduler

WHEN_KINDS = ["Every day", "Weekdays", "Weekends", "Every N minutes", "Once"]
# label -> (kind of rule action, host op or None, hint)
DO_KINDS = {"Switch layer": ("layer", None, "1, 2, 3, next or prev"), "Show screen": ("mode", None, "1 clock, 2 focus, 3 media, 4 system, 5 GIF, 6 info"),
            "Set brightness": ("brightness", None, "5 to 255"), "LED": ("led", None, "auto, off, breathe, fire, or a colour like ff8800"),
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
        do = {"kind": "led", "mode": low if low in ("auto", "off", "breathe", "fire") else "solid", "hex": low}
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
