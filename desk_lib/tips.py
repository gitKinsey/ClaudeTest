"""Tip engine: suggests ONE feature the user has not used yet, based on their own settings. Pure; the app shows the result as a card."""

# (id, text, predicate(cfg, caps) -> True when the tip is relevant / the feature is unused)
TIPS = [
    ("gestures", "Keys can do more than one thing: set a hold or double-press action in Pad behaviour (needs firmware 1.3).",
     lambda c, caps: "gestures" in caps and not c.get("gestures")),
    ("scripts", "Scripts can repeat things and react to the program in front - try a template on the Scripts page.",
     lambda c, caps: not c.get("scripts")),
    ("automation", "Let the pad act on a schedule: 'weekdays 09:00 -> layer 2' on the Automation page.",
     lambda c, caps: not c.get("schedules")),
    ("profiles", "Switch layers automatically per program: turn on Profiles and pick a preset.",
     lambda c, caps: not c.get("profiles_on")),
    ("api", "A local API lets other programs (and the deskcompanion command) drive the pad - switch it on under Automation.",
     lambda c, caps: not (c.get("api") or {}).get("on")),
    ("autobackup", "Switch on automatic backups so a broken setup is one click from restored.",
     lambda c, caps: not (c.get("autobackup") or {}).get("on")),
    ("tray", "Keep the app running in the tray so profiles and schedules keep working with the window closed.",
     lambda c, caps: not c.get("tray")),
    ("dimlock", "Dim the pad while the PC is locked or a video is fullscreen (Device page).",
     lambda c, caps: not (c.get("dim_lock") or c.get("dim_fullscreen"))),
    ("screens", "The pad has more screens than the first six: stopwatch, breathing, habits, snake ... (Screens card).",
     lambda c, caps: "screens" in caps and not c.get("screens_seen")),
    ("palette", "Press Ctrl+K anywhere in the app for the command palette.", lambda c, caps: not c.get("palette_used")),
    ("cliphist", "Clipboard history: type any of your last 9 copies from a key.", lambda c, caps: not c.get("cliphist_on")),
]


def pick(cfg, caps=(), dismissed=(), seed=0):
    """-> (id, text) of a relevant tip not dismissed yet, or None. `seed` rotates through the candidates (e.g. the day number)."""
    caps = set(caps or ())
    cand = [(i, t) for i, t, ok in TIPS if i not in set(dismissed) and ok(cfg, caps)]
    return cand[seed % len(cand)] if cand else None
