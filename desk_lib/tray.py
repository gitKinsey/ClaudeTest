"""Optional system-tray icon (needs `pip install pystray`): show / hide the window, switch the pad's layer, quit.
The pystray module is injectable, so the menu logic is testable without a desktop."""
from PIL import Image, ImageDraw


def make_icon(size=64, accent=(0, 210, 255)):
    """A small ring like the pad's own display."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 3, size - 3), fill=(14, 16, 20, 255), outline=accent, width=max(3, size // 10))
    d.ellipse((size * 0.42, size * 0.42, size * 0.58, size * 0.58), fill=accent)
    return img


def available():
    import importlib.util
    try:
        return importlib.util.find_spec("pystray") is not None
    except (ImportError, ValueError):
        return False


class Tray:
    def __init__(self, app, pystray=None):
        self.app, self.icon = app, None
        self.pystray = pystray
        if self.pystray is None:
            import pystray as _p
            self.pystray = _p

    def menu_labels(self):
        return ["Show Desk Companion", "Layer 1", "Layer 2", "Layer 3", "Quit"]

    def _menu(self):
        P = self.pystray
        item = P.MenuItem
        post = self.app.post
        return P.Menu(item("Show Desk Companion", lambda *_: post(self.app.show_window), default=True),
                      *[item(f"Layer {n + 1}", (lambda n: lambda *_: post(lambda: self.app.tray_layer(n)))(n)) for n in range(3)],
                      item("Quit", lambda *_: post(self.app.quit_app)))

    def start(self):
        if self.icon:
            return
        self.icon = self.pystray.Icon("DeskCompanion", make_icon(), "Desk Companion", self._menu())
        self.icon.run_detached()

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:                              # noqa: BLE001
                pass
            self.icon = None
