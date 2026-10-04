"""Engine part 5: what the app does with firmware 1.6 (each feature has its own entry in the pad's `caps`; an older pad just keeps working as before):
batched key uploads, the pad's own state events, the program name / layer names / banners shown on the pad, the pad following the app's accent colour."""

from core.base import BATCH_MAX_CHARS, BATCH_MAX_ITEMS, LAYERS, PROTO_LEVEL, DeviceError, compact_json
from desk_lib import tokens
from desk_lib.feeds import ascii_fold


class Fw16Ops:
    # ------------------------------------------------------------------ batched key uploads (cap "batch")
    def _remap_items(self, jobs):
        items = []
        for layer, slot, spec in jobs:
            it = {"key": slot, "type": spec[0], "val": spec[1]}
            if layer:
                it["layer"] = layer
            items.append(it)
        return items

    def _send_remaps(self, jobs, on_sent=None):
        """Send (layer, slot, spec) key actions: one `remap_batch` per ~4800 characters when the pad can, one `remap` each otherwise.
        on_sent(chunk_of_jobs) is called (on the engine thread) after each acknowledged request. Runs on a worker thread."""
        if not jobs:
            return
        if not self._pad_cap("batch"):
            for job in jobs:
                self.dev.request(self._remap_msg(job[0], job[1], job[2]))
                if on_sent:
                    self.post(lambda j=[job]: on_sent(j))
            return
        batches, cur, size = [], [], 0
        for job in jobs:
            n = len(compact_json(self._remap_items([job])[0])) + 2
            if cur and (len(cur) >= BATCH_MAX_ITEMS or size + n > BATCH_MAX_CHARS):
                batches.append(cur)
                cur, size = [], 0
            cur.append(job)
            size += n
        if cur:
            batches.append(cur)
        for group in batches:
            self.dev.request({"cmd": "remap_batch", "items": self._remap_items(group)}, timeout=8)
            self._batches_sent = getattr(self, "_batches_sent", 0) + 1
            if on_sent:
                self.post(lambda g=list(group): on_sent(g))

    # ------------------------------------------------------------------ events from the pad (cap "stateevt")
    def _on_state_event(self, m):
        """The pad changed screen / brightness / layer itself (dial menu, long press, auto-dim): the twin and the Look tab follow."""
        mode, bright, layer = m.get("mode"), m.get("bright"), m.get("layer")
        if isinstance(mode, int) and 1 <= mode <= 20 and mode != self.pad.mode:
            self.pad.set_mode(mode, notify=False)
            self.cfg["twin_mode"] = mode
            self.emit("pad_state", "mode")
        if isinstance(bright, int) and 5 <= bright <= 255 and bright != self.pad.brightness:
            self.pad.set_brightness(bright, notify=False)
            self.cfg["twin_bright"] = bright
            self.emit("pad_state", "bright")
        if isinstance(mode, int) and isinstance(bright, int):
            self.cfg["pushed_mode"], self.cfg["pushed_bright"] = self.pad.mode, self.pad.brightness     # the physical pad is what it says it is
            self.recompute_pending()
        if isinstance(layer, int) and 0 <= layer < LAYERS and layer != self.pad_layer:
            self._pad_layer_changed(layer)
        self.pad.dirty = True
        self.state_events = getattr(self, "state_events", 0) + 1

    # ------------------------------------------------------------------ the program name on the pad (cap "ctx")
    def send_ctx(self, text):
        """Show the program the layer follows next to the pad's layer badge. Quietly does nothing on older firmware or when switched off."""
        if not self.cfg.get("pad_ctx", True) or not self._pad_cap("ctx"):
            return False
        t = ascii_fold(str(text or ""), 16).strip()
        try:
            self.dev.request({"cmd": "ctx", "text": t}, timeout=3)
        except DeviceError:
            return False
        return True

    # ------------------------------------------------------------------ layer names (cap "lnames")
    def layer_names(self):
        names = self.cfg.get("layer_names")
        if not isinstance(names, list) or len(names) != LAYERS:
            names = ["", "", ""]
        return [str(n)[:10] for n in names]

    def layer_names_set(self, names):
        """Store (and send, when the pad can show them) up to ten printable ASCII characters per layer; '' = the default 'L1'..'L3'."""
        clean = [ascii_fold(str(n or ""), 10).strip() for n in list(names)[:LAYERS]]
        clean += [""] * (LAYERS - len(clean))
        self.cfg["layer_names"] = clean
        self.save_cfg()
        self.emit("layer_names", clean)
        if self._pad_cap("lnames"):
            self.bg(lambda: self.dev.request({"cmd": "layer_names", "names": clean}), lambda _r: self.set_status("Layer names sent to the pad"), "Layer names failed")
        else:
            self.set_status("Layer names saved" + (" - this pad's firmware cannot show them (update it for firmware 1.6)" if self.dev.connected else " - they are sent when a pad with firmware 1.6 connects"))
        return clean

    # ------------------------------------------------------------------ banners on the pad (cap "toast")
    def pad_toast(self, text, kind="ok", secs=3):
        """A short banner on the pad's screen (24 characters). Returns False when the pad cannot or the user switched it off."""
        if not self.cfg.get("pad_toasts", True) or not self._pad_cap("toast"):
            return False
        t = ascii_fold(str(text or ""), 24).strip()
        if not t:
            return False
        kind = kind if kind in ("ok", "warn", "err") else "ok"
        self.bg(lambda: self.dev.request({"cmd": "toast", "text": t, "kind": kind, "secs": int(secs)}, timeout=3), None, "Pad banner failed")
        return True

    # ------------------------------------------------------------------ the pad follows the app's accent colour (cap "accent")
    def accent_index(self):
        """0 = the pad keeps its own theme colour, 1..5 = cyan, pink, green, amber, violet (the same order as the app's accent list)."""
        if not self.cfg.get("pad_accent"):
            return 0
        names = list(tokens.ACCENTS)
        name = self.cfg.get("accent", "cyan")
        return names.index(name) + 1 if name in names else 0

    def push_accent(self):
        if not self._pad_cap("accent"):
            return False
        idx = self.accent_index()
        self.bg(lambda: self.dev.request({"cmd": "settings", "accent": idx}), None, "Pad accent failed")
        return True

    def set_pad_accent(self, on):
        self.cfg["pad_accent"] = bool(on)
        self.save_cfg()
        if self.dev.connected and not self.push_accent():
            self.set_status("Saved - the pad's firmware cannot follow the accent colour yet (update it for firmware 1.6)")
        return bool(on)

    # ------------------------------------------------------------------ everything 1.6 that is sent when a pad connects / on 'Upload everything'
    def _push_fw16_state(self):
        """Worker thread. Layer names and the accent colour live on the pad, so they are re-sent like key maps."""
        if self._pad_cap("lnames") and any(self.layer_names()):
            self.dev.request({"cmd": "layer_names", "names": self.layer_names()})
        if self._pad_cap("accent"):
            self.dev.request({"cmd": "settings", "accent": self.accent_index()})

    # ------------------------------------------------------------------ protocol level / performance (cap "perf", hello "proto")
    def proto_newer(self):
        """True when the pad speaks a newer protocol than this app was written for."""
        try:
            return int(self.dev.info.get("proto", 0)) > PROTO_LEVEL
        except (TypeError, ValueError):
            return False

    def perf_text(self):
        """One line for Diagnostics (empty when the firmware has no counters)."""
        i = self.dev.info if self.dev.connected else {}
        if "loop_max_us" not in i:
            return ""
        txt = f"loop: average {int(i.get('loop_avg_us', 0)) / 1000:.1f} ms, longest {int(i.get('loop_max_us', 0)) / 1000:.0f} ms;  link: {i.get('usb_drops', 0)} USB drops, {i.get('rx_overruns', 0)} over-long requests"
        return txt

    def perf_refresh(self):
        """Re-read `info` and tell the Diagnostics page (a slow pad loop shows as a warning)."""
        if not self.dev.connected or not self._pad_cap("perf"):
            return

        def done(i):
            self.dev.info.update({k: i[k] for k in ("loop_max_us", "loop_avg_us", "rx_overruns", "usb_drops") if k in i})
            slow = int(i.get("loop_max_us", 0)) > 120_000
            self.emit("perf", self.perf_text(), slow)
        self.bg(lambda: self.dev.request({"cmd": "info"}), done, "Could not read the pad's counters")


__all__ = ["Fw16Ops"]
