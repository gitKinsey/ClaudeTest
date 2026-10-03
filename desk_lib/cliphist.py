"""Clipboard history: the last few things copied while the app runs (plain text only, never saved to disk, wiped when the app closes)."""


class ClipHistory:
    def __init__(self, size=9, max_len=2000):
        self.size, self.max_len, self.items = size, max_len, []

    def add(self, text):
        """Record a clipboard value; returns True when it was new. Empty and very long values are ignored."""
        t = (text or "")
        if not t.strip() or len(t) > self.max_len:
            return False
        if self.items and self.items[0] == t:
            return False
        if t in self.items:
            self.items.remove(t)
        self.items.insert(0, t)
        del self.items[self.size:]
        return True

    def get(self, n):
        """n = 1 is the latest, 2 the one before ... Raises ValueError with a readable reason."""
        if not 1 <= n <= self.size:
            raise ValueError(f"clipboard history goes from 1 to {self.size}")
        if n > len(self.items):
            raise ValueError(f"only {len(self.items)} clipboard entr{'y' if len(self.items) == 1 else 'ies'} remembered so far")
        return self.items[n - 1]

    def clear(self):
        self.items.clear()
