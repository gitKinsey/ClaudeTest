"""Display: GIFs, Info screen, Screens, Look - with the pad twin beside them."""
from ui_qt.layouts import PageGrid
from ui_qt.media import TabArea
from ui_qt.padview import PadViewQt
from ui_qt.pages.base import Page
from ui_qt.pages.gifs_tab import build_gifs_tab
from ui_qt.pages.info_tab import build_info_tab
from ui_qt.pages.look_tab import build_look_tab
from ui_qt.pages.screens_tab import build_screens_tab
from ui_qt.widgets import Card, button, flow


class DisplayPage(Page):
    id, title, subtitle, icon = "display", "Display", "GIFs, info cards, screens and the pad's look", "display"

    def __init__(self, shell):
        super().__init__(shell)
        e = self.engine
        self.gifs = build_gifs_tab(self)
        self.info = build_info_tab(self)
        self.screens = build_screens_tab(self)
        self.look = build_look_tab(self)
        self.tabs = TabArea({"GIFs": self.gifs, "Info": self.info, "Screens": self.screens, "Look": self.look}, "GIFs")
        t = Card("Pad")
        self.twin = PadViewQt(e, interactive=True)
        shell.add_twin(self.twin)
        t.add(self.twin, 1)
        t.add(flow(button("Show on pad", "ghost", e.show_layer_on_pad)))
        self.grid = PageGrid({"main": self.tabs, "twin": t}, {"wide": [[("main", 4)], [("twin", 1)]], "medium": [[("main", 1)]]},
                             order=["main", "twin"], titles={"main": "Display", "twin": "Pad"}, tabs_factory=None, wide_min=1180, medium_min=720)
        self.root.addWidget(self.grid, 1)
        self.tabs.changed.connect(lambda k: self.info_activate(k))

    def info_activate(self, key):
        if key == "GIFs":
            self.engine.refresh_pad_gifs()
        elif key == "Info":
            self.engine.info_send_now()

    def show_section(self, section):
        m = {"gifs": "GIFs", "info": "Info", "screens": "Screens", "look": "Look"}.get(section)
        if m:
            self.tabs.show_tab(m)

    def on_show(self):
        self.engine.pad.dirty = True
