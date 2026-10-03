"""A system-wide hotkey (works while another program has the focus), via the optional `pynput` package.
`factory(mapping)` returns an object with start()/stop() (default: pynput.keyboard.GlobalHotKeys) so tests need no display."""
import re

MODS = {"ctrl": "<ctrl>", "control": "<ctrl>", "alt": "<alt>", "shift": "<shift>", "win": "<cmd>", "cmd": "<cmd>", "super": "<cmd>", "meta": "<cmd>"}
SPECIAL = {"space", "enter", "tab", "esc", "up", "down", "left", "right", "home", "end", "delete", "backspace"} | {f"f{i}" for i in range(1, 13)}


def normalize(combo):
    """'Ctrl+Alt+K' -> '<ctrl>+<alt>+k'. Needs at least one modifier (a bare letter would hijack typing). Raises ValueError."""
    parts = [p.strip().lower() for p in re.split(r"\s*\+\s*", str(combo).strip()) if p.strip()]
    if len(parts) < 2:
        raise ValueError("use a modifier plus a key, e.g. ctrl+alt+k")
    *mods, key = parts
    if any(m not in MODS for m in mods):
        raise ValueError("modifiers can be ctrl, alt, shift, win")
    if not (len(key) == 1 and key.isalnum()) and key not in SPECIAL:
        raise ValueError(f"'{key}' is not a key I know (a letter, a digit, F1-F12, space, ...)")
    out = [MODS[m] for m in dict.fromkeys(mods)]
    return "+".join(out + [f"<{key}>" if key in SPECIAL else key])


def available():
    try:
        from pynput import keyboard                              # noqa: PLC0415
        return keyboard is not None
    except Exception:                                            # noqa: BLE001  (not installed / no display)
        return False


def _default_factory(mapping):
    from pynput import keyboard                                  # noqa: PLC0415
    return keyboard.GlobalHotKeys(mapping)


class GlobalHotkey:
    def __init__(self, combo, callback, factory=None):
        self.combo, self.callback, self.factory, self._h = normalize(combo), callback, factory or _default_factory, None

    def start(self):
        self._h = self.factory({self.combo: self.callback})
        self._h.start()

    def stop(self):
        if self._h is not None:
            self._h.stop()
            self._h = None
