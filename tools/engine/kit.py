"""Helpers for the engine tests: an isolated HOME, an Engine on the simulated pad, a pump that runs the engine's event queue."""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def isolate(prefix="dceng_"):
    tmp = tempfile.mkdtemp(prefix=prefix)
    os.environ["HOME"] = tmp
    os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
    os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    return tmp


isolate("dceng_")          # at import time: core.base reads its paths when it is first imported, which must never be the real home folder


class Kit:
    def __init__(self, prefix="dceng_", simulate=True, start_threads=True, frontend=None, fresh=True):
        self.tmp = isolate(prefix) if fresh else os.environ["HOME"]
        from core.engine import Engine, NullFrontend
        self.fe = frontend or NullFrontend()
        self.e = Engine(self.fe, start_threads=start_threads)
        self.events = []
        if simulate:
            self.e.toggle_simulate()
            assert self.pump(80, lambda: self.e.dev.connected), "simulated pad did not connect"
            self.sim = self.e.dev.ser.sim

    def listen(self, *names):
        for n in names:
            self.e.on(n, lambda *a, n=n: self.events.append((n, a)))

    def pump(self, n=20, cond=None):
        for _ in range(n * (3 if cond else 1)):                       # generous: CI machines can be slow
            self.e.pump()
            time.sleep(0.05)
            if cond and cond():
                return True
        return cond is None

    @property
    def status(self):
        return self.e.status[0]

    def check_handlers(self):
        errs = self.e.__dict__.get("handler_errors", [])
        assert not errs, "an engine event handler raised:\n" + "\n".join(f"{n}: {t}" for n, t in errs[:3])

    def close(self):
        self.e.shutdown()
