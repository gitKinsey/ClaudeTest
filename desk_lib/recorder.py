"""Turn a stream of key presses into macro steps (the logic only - the app feeds it from a pynput listener)."""

MOD_NAMES = {"ctrl": "CTRL", "ctrl_l": "CTRL", "ctrl_r": "CTRL", "shift": "SHIFT", "shift_l": "SHIFT", "shift_r": "SHIFT",
             "alt": "ALT", "alt_l": "ALT", "alt_r": "ALT", "alt_gr": "ALT", "cmd": "GUI", "cmd_l": "GUI", "cmd_r": "GUI",
             "gui": "GUI", "win": "GUI", "super": "GUI"}
NAMED = {"enter": "ENTER", "return": "ENTER", "tab": "TAB", "esc": "ESC", "escape": "ESC", "backspace": "BACKSPACE", "delete": "DELETE",
         "space": "SPACE", "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT", "home": "HOME", "end": "END",
         "page_up": "PGUP", "page_down": "PGDN", "insert": "INSERT", "caps_lock": "CAPSLOCK", "print_screen": "PRTSC", "menu": "MENU"}
MAX_STEPS = 64


class MacroRecorder:
    """key_down / key_up take a pynput-style name: a single character ('a', 'A', '/') or a key name ('ctrl_l', 'enter', 'f5').
    Modifier combinations become {"combo": [...]} steps, plain typing becomes {"text": "..."} runs, pauses longer than
    `gap_ms` become {"delay": ms}."""

    def __init__(self, gap_ms=350, max_delay_ms=5000):
        self.steps, self.held, self.text, self.last_t = [], [], "", None
        self.gap_ms, self.max_delay_ms, self.truncated = gap_ms, max_delay_ms, False

    def _add(self, step):
        if len(self.steps) >= MAX_STEPS:
            self.truncated = True
            return
        self.steps.append(step)

    def _flush_text(self):
        if self.text:
            self._add({"text": self.text})
            self.text = ""

    def _gap(self, t):
        if self.last_t is not None and (t - self.last_t) * 1000 >= self.gap_ms:
            self._flush_text()
            self._add({"delay": int(min(self.max_delay_ms, round((t - self.last_t) * 1000, -1)))})
        self.last_t = t

    def key_down(self, name, t):
        n = str(name)
        low = n.lower()
        if low in MOD_NAMES:
            m = MOD_NAMES[low]
            if m not in self.held:
                self.held.append(m)
            return
        self._gap(t)
        if low in NAMED or (len(low) > 1 and low[0] == "f" and low[1:].isdigit() and 1 <= int(low[1:]) <= 24):
            key = NAMED.get(low, low.upper())
        elif len(n) == 1 and 32 <= ord(n) < 127:
            key = n.lower() if self.held and n.isalpha() else n
        else:
            return                                           # dead keys, media keys, anything the pad cannot type
        shift_only = self.held == ["SHIFT"]
        if not self.held or (shift_only and len(n) == 1):
            if len(key) == 1 or key == "SPACE":
                self.text += " " if key == "SPACE" else n
                return
        self._flush_text()
        self._add({"combo": list(self.held[:4]) + [key]})

    def key_up(self, name, t):
        low = str(name).lower()
        if low in MOD_NAMES and MOD_NAMES[low] in self.held:
            self.held.remove(MOD_NAMES[low])

    def finish(self):
        self._flush_text()
        while self.steps and "delay" in self.steps[-1]:
            self.steps.pop()                                 # a trailing pause is just the time it took to press Stop
        return list(self.steps)
