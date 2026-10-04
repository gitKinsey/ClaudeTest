"""The Desk Companion engine: all the app's logic without a toolkit. Compose of the parts in engine_core, keys_ops, display_ops, system_ops, fw16_ops."""
from core.display_ops import DisplayOps
from core.engine_core import EngineCore, NullFrontend
from core.fw16_ops import Fw16Ops
from core.keys_ops import KeysOps
from core.system_ops import SystemOps, info_lines


class Engine(EngineCore, KeysOps, DisplayOps, SystemOps, Fw16Ops):
    def init_state(self):
        self.vp_log_lines = []
        self.last_info = {}
        self.cpu = self.ram = 0.0
        self.uploading = False
        self.pending_count = 0
        self.init_display_state()
        self.init_keys_state()


__all__ = ["Engine", "NullFrontend", "info_lines"]
