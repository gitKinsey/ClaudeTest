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
        self._pos, self._mdx, self._mdy, self._down = None, 0, 0, {}        # mouse: last position, movement not yet written, buttons that are down

    def _add(self, step):
        if len(self.steps) >= MAX_STEPS:
            self.truncated = True
            return
        self.steps.append(step)

    def _flush_move(self):
        """Pending pointer movement -> relative move steps (the pad's mouse moves by at most 100 per step and axis)."""
        dx, dy, self._mdx, self._mdy = self._mdx, self._mdy, 0, 0
        for _ in range(40):
            if not (dx or dy):
                break
            sx, sy = max(-100, min(100, dx)), max(-100, min(100, dy))
            self._add({"mouse": {"move": [sx, sy]}})
            dx, dy = dx - sx, dy - sy

    def _flush_text(self):
        self._flush_move()
        if self.text:
            self._add({"text": self.text})
            self.text = ""

    # ---- mouse (pointer position, buttons, wheel). The pad can only move the pointer RELATIVELY, so a recording replays from wherever the pointer is.
    def mouse_move(self, x, y, t):
        if self._pos is not None:
            dx, dy = int(x - self._pos[0]), int(y - self._pos[1])
            if (dx or dy) and self.text:
                self._flush_text()                           # typing that happened before the movement stays before it
            self._mdx += dx
            self._mdy += dy
        self._pos = (x, y)

    def mouse_button(self, button, pressed, t):
        b = str(button).lower().replace("button.", "")
        b = {"left": "left", "right": "right", "middle": "middle", "x1": "back", "back": "back", "x2": "forward", "forward": "forward"}.get(b)
        if b is None:
            return
        self._flush_text()
        if pressed:
            self._gap(t)
            self._down[b] = t
        elif b in self._down:
            t0 = self._down.pop(b)
            self._add({"mouse": {"btn": b, "act": "click" if t - t0 < 0.6 else "down"}})
            if t - t0 >= 0.6:                                  # a long press (drag): press, wait, release
                self._add({"delay": int(min(self.max_delay_ms, round((t - t0) * 1000, -1)))})
                self._add({"mouse": {"btn": b, "act": "up"}})
            self.last_t = t

    def mouse_scroll(self, dy, t):
        self._flush_text()
        self._gap(t)
        n = max(-20, min(20, int(round(dy))))
        if n:
            self._add({"mouse": {"wheel": n}})

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
        self._flush_move()                                   # pointer movement that happened before this key stays before it
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
