"""Engine part 2: the key library, assignment / pending tracking / upload / verification, gestures, undo, test-on-this-PC, macro builder, share codes."""
import copy
import json
import re
import time
import zlib
from pathlib import Path

from core.base import (
    ACTION_INDEX, ACTION_KINDS, ACTIONS, DEFAULT_LAYER_MAPS, HOST_OS, LAYER_NAMES, LAYERS, MODE_CHOICES, RISKY, SLOT_LABELS, DeviceError,
    compact_json, describe_spec, normalize_config, resolve_spec, save_config, spec_json, spec_needs_focus, spec_ok, time_msg, valid_key, labels_for,
    lib_add, lib_list,
)
from desk_lib import sharecode, textops, hostactions, recorder
from desk_lib import padconst


class KeysOps:
    # ------------------------------------------------------------------ state
    def init_keys_state(self):
        self.target_slot = 1
        self._rec = None
        self._rec_mouse = None
        self._recorder = None
        self._rec_job = None
        self.recompute_pending()

    def set_target_slot(self, slot):
        """The key the 'Assign ...' buttons of the builders act on."""
        if slot != self.target_slot:
            self.target_slot = slot
        self.emit("target_slot", slot)

    # ------------------------------------------------------------------ library
    def categories(self):
        return list(ACTIONS) + (["Custom"] if self.cfg["custom"] else [])

    def names_for(self, cat):
        return list(self.cfg["custom"]) if cat == "Custom" else [a[0] for a in ACTIONS.get(cat, [])]

    def library_tree(self, query=""):
        """[(category, [names])] filtered by a search text (matches names and category names)."""
        q = (query or "").strip().lower()
        out = []
        for cat in self.categories():
            names = self.names_for(cat)
            if q:
                names = [n for n in names if q in n.lower() or q in cat.lower()]
            if names:
                out.append((cat, names))
        return out

    def refresh_library(self):
        self.emit("library")

    def refresh_action_lists(self):
        self.emit("map_changed")

    def slot_assignment(self, slot):
        """(cat, name) shown for a slot on the layer being edited (falls back to the layer default when the action no longer exists)."""
        m = self.cfg["map"][str(slot)]
        cat, name = m["cat"], m["action"]
        if cat not in self.categories() or name not in self.names_for(cat):
            cat, name = DEFAULT_LAYER_MAPS[self.edit_layer][slot]
        return cat, name

    def resolve_action(self, cat, name):
        return resolve_spec(self.cfg, cat, name)

    def spec_valid(self, spec):
        return spec_ok({"type": spec[0], "val": spec[1]})

    # ------------------------------------------------------------------ assignment
    def drop_assign(self, slot, payload):
        if "slot" in payload:                                # key dragged onto another key = swap
            a, b = str(payload["slot"]), str(slot)
            if a == b:
                return
            self.cfg["map"][a], self.cfg["map"][b] = self.cfg["map"][b], self.cfg["map"][a]
            self.mapping_changed([int(a), int(b)])
            self.vp_log(f"swapped {SLOT_LABELS[int(a)]} and {SLOT_LABELS[slot]}")
        else:
            self.cfg["map"][str(slot)] = {"cat": payload["cat"], "action": payload["action"]}
            self.mapping_changed([slot])
            self.vp_log(f"{SLOT_LABELS[slot]} = {payload['action']}")

    def assign_combo_action(self, slot, cat, name):
        """The key-map rows: category + action chosen for a slot."""
        self.cfg["map"][str(slot)] = {"cat": cat, "action": name}
        self.mapping_changed([slot])

    def mapping_changed(self, slots):
        self.history.record(self._edit_snapshot())
        save_config(self.cfg)
        self.emit("map_changed")
        self.recompute_pending()
        if self.autoup and self.dev.connected:
            self.upload_slots(list(slots))

    def vp_log(self, text):
        self.vp_log_lines.append(time.strftime("%H:%M:%S  ") + text)
        del self.vp_log_lines[:-200]
        self.emit("vp_log", self.vp_log_lines[-1])

    # ------------------------------------------------------------------ pending
    def _pending_for(self, layer):
        lm, pushed = self.cfg["layers"][layer], self.cfg["pushed_layers"][layer]
        out = set()
        for sl in range(1, 8):
            m = lm[str(sl)]
            if spec_json(resolve_spec(self.cfg, m["cat"], m["action"])) != pushed.get(str(sl)):
                out.add(sl)
        return out

    def recompute_pending(self):
        self.pending_by_layer = [self._pending_for(n) for n in range(LAYERS)]
        self.pending_slots = self.pending_by_layer[self.edit_layer]
        extra = int(self.cfg.get("pushed_mode") != self.pad.mode) + int(self.cfg.get("pushed_bright") != self.pad.brightness)
        self.pending_count = sum(len(x) for x in self.pending_by_layer) + extra
        self.emit("pending", self.pending_count)

    def _mark_pushed(self, slot, j, layer=None):
        layer = self.edit_layer if layer is None else layer
        self.cfg["pushed_layers"][layer][str(slot)] = j
        save_config(self.cfg)
        self.recompute_pending()

    def _mark_display_pushed(self, mode, bright):
        self.cfg["pushed_mode"], self.cfg["pushed_bright"] = mode, bright
        save_config(self.cfg)
        self.recompute_pending()

    def _remap_msg(self, layer, slot, spec):
        msg = {"cmd": "remap", "key": slot, "type": spec[0], "val": spec[1]}
        if layer:
            msg["layer"] = layer
        return msg

    # ------------------------------------------------------------------ gestures
    def _sync_gestures(self):
        existing = set()
        for lay in range(LAYERS):
            r = self.dev.request({"cmd": "getkeys", **({"layer": lay} if lay else {})}, timeout=4)
            for sl in r.get("slots", []):
                if sl.get("h"):
                    existing.add((lay, sl["s"], "hold"))
                if sl.get("d"):
                    existing.add((lay, sl["s"], "double"))
                if sl.get("t"):
                    existing.add((lay, sl["s"], "triple"))
            for i, flag in enumerate(r.get("pt") or []):                   # dial pressed + turned right / left (firmware 1.4)
                if flag:
                    existing.add((lay, 8 + i, "press"))
            for i, flag in enumerate(r.get("ch") or []):                   # chords K1+K2 ... K4+K5 (firmware 1.5)
                if flag:
                    existing.add((lay, 10 + i, "press"))
            for i, flag in enumerate(r.get("dc") or []):                   # dial double / triple click (firmware 1.5)
                if flag:
                    existing.add((lay, 14 + i, "press"))
        msgs = padconst.gesture_msgs(self, existing, lambda c, a: resolve_spec(self.cfg, c, a), set(self.dev.info.get("caps") or []))
        for m in msgs:
            self.dev.request(m)
        return len(msgs)

    def push_gestures(self, what=""):
        if not self.dev.connected:
            return self.set_status((what + " - " if what else "") + "saved; it is sent to the pad with the next upload")
        if not self._pad_cap("gestures"):
            return self.set_status("Hold / double-tap actions need firmware 1.3 - update the pad (Pad & App -> Firmware). The setting is saved.", error=True)
        self.bg(self._sync_gestures, lambda n: self.set_status(f"{what or 'Gestures'} - sent to the pad"), "Sending the gesture failed")

    def gesture_assign(self, layer, slot_label, gesture_label, cat, action):
        """Add / replace a gesture. slot_label: 'K1'..'K5' or a DIAL_PRESS name. Returns the new key or raises ValueError."""
        if not action:
            raise ValueError("Pick an action first")
        if slot_label in padconst.DIAL_PRESS:
            slot, g = padconst.DIAL_PRESS[slot_label], "press"
        else:
            slot, g = int(slot_label[1:]), padconst.value_of(padconst.GESTURES, gesture_label, 0)
        self.cfg.setdefault("gestures", {})[padconst.gesture_key(layer, slot, g)] = {"cat": cat, "action": action}
        self.save_cfg()
        self.emit("gestures")
        self.push_gestures(f"{padconst.gesture_text(slot, g)} on layer {layer + 1}: {action}")

    def gesture_remove(self, key):
        self.cfg["gestures"].pop(key, None)
        self.save_cfg()
        self.emit("gestures")
        self.push_gestures("Gesture removed")

    def gesture_items(self):
        return sorted(self.cfg.get("gestures", {}).items())

    # ------------------------------------------------------------------ alternate / random keys
    def choice_pick(self, picks, kind_alternate, cat, action):
        """Validate one more action for an alternating / random key. Returns the pick or raises ValueError."""
        limit = 2 if kind_alternate else 6
        if len(picks) >= limit:
            raise ValueError(f"At most {limit} actions here")
        spec = self.resolve_action(cat, action)
        if not spec or spec[0] in ("none", "toggle", "random") or not self.spec_valid(spec):
            raise ValueError("That action cannot be part of an alternating / random key")
        return (cat, action, spec)

    def choice_assign(self, picks, alt):
        if len(picks) < 2 or (alt and len(picks) != 2):
            raise ValueError("Alternate needs exactly 2 actions, random needs 2 to 6")
        if self.dev.connected and not padconst.has_cap(self, "toggle"):
            raise ValueError("Alternating / random keys need firmware 1.4 - update the pad (Pad & App -> Firmware).")
        subs = [{"type": s[0], "val": s[1]} for _c, _a, s in picks]
        names = " / ".join(a for _c, a, _s in picks)
        label = (f"Alternate: {names}" if alt else f"Random: {names}")[:48]
        self.assign_spec(("toggle" if alt else "random", subs), label)

    # ------------------------------------------------------------------ autostart / tray / shell switches (pad & app page)
    def autostart_set(self, on):
        from desk_lib import autostart
        try:
            autostart.enable(autostart.launch_command()) if on else autostart.disable()
        except Exception as e:                                # noqa: BLE001  (read-only registry, no home folder ...)
            self.set_status(f"Could not change the start-up setting: {e}", error=True)
            return False
        self.set_status("The app will start minimised when you log in" if on else "The app no longer starts with your computer")
        return True

    def tray_set(self, on):
        why = self.tray_enable(on)
        if why:
            self.cfg["tray"] = False
            self.set_status(f"Tray: {why}", error=True)
            return False
        self.cfg["tray"] = on
        save_config(self.cfg)
        self.set_status("Closing the window now keeps the app running in the tray" if on else "Closing the window quits the app")
        return True

    def shell_set(self, on):
        """Returns the value that ended up stored (the user may decline)."""
        if on and not self.ui.confirm("Allow shell commands", "Let key presses on the pad run shell commands on this computer?\n\n"
                                      "Only commands that you put into your own key maps are ever run, but a shell command can do anything "
                                      "you can do. Keep this off unless you need it."):
            on = False
        self.cfg["allow_shell"] = bool(on)
        save_config(self.cfg)
        return bool(on)

    # ------------------------------------------------------------------ upload
    def upload_slots(self, slots, layer=None):
        if not self.dev.connected or self._core_only("key mapping"):
            return
        layer = self.edit_layer if layer is None else layer
        if layer and not self._layers_supported():
            return self.set_status("This pad's firmware has no layers - update it (Pad & App -> Firmware)", error=True)
        jobs = []
        for s in slots:
            m = self.cfg["layers"][layer][str(s)]
            spec = resolve_spec(self.cfg, m["cat"], m["action"])
            if spec:
                jobs.append((s, spec))
        osv = self.cfg["os"]

        def work():
            self.dev.request({"cmd": "os", "val": osv})
            for s, spec in jobs:
                self.dev.request(self._remap_msg(layer, s, spec))
                self.post(lambda s=s, j=spec_json(spec): self._mark_pushed(s, j, layer))
            self._push_labels([layer])
        self.bg(work, lambda _: self.set_status(("Layer %d: " % (layer + 1) if layer else "Sent to pad: ") + ", ".join(SLOT_LABELS[x[0]] for x in jobs)),
                "Upload failed")

    def _verify_keys_sync(self):
        """Compare what the pad stored (getkeys: length + CRC of each slot's JSON) with what the app believes it uploaded.
        Returns a list of problems, or None when the firmware is too old to answer."""
        bad, layers = [], range(LAYERS)
        for lay in layers:
            try:
                r = self.dev.request({"cmd": "getkeys", **({"layer": lay} if lay else {})}, timeout=4)
            except DeviceError:
                return None if lay == 0 else bad
            if lay and "layers" not in r:                       # firmware without layers: nothing more to compare
                break
            tag = f"L{lay + 1} " if lay else ""
            for slot in r.get("slots", []):
                s = slot["s"]
                m = self.cfg["layers"][lay][str(s)]
                spec = resolve_spec(self.cfg, m["cat"], m["action"])
                if slot["def"]:
                    if (m["cat"], m["action"]) != DEFAULT_LAYER_MAPS[lay][s]:
                        bad.append(f"{tag}{SLOT_LABELS[s]}: still factory default on the pad")
                    continue
                if not spec:
                    continue
                j = compact_json({"type": spec[0], "val": spec[1]}).encode("utf-8")
                if slot["len"] != len(j) or slot["crc"] != (zlib.crc32(j) & 0xFFFFFFFF):
                    bad.append(f"{tag}{SLOT_LABELS[s]}: pad has different data than the app sent")
        return bad

    def verify_pad_keys(self):
        if not self.dev.connected:
            return self.set_status("Not connected", error=True)

        def done(bad):
            if bad is None:
                self.set_status("This firmware cannot report its key slots (older version)", error=True)
            elif bad:
                self.set_status("Key check: " + "; ".join(bad), error=True)
                self._dev_note("key check FAILED: " + "; ".join(bad), err=True)
            else:
                self.set_status("Key check OK: the pad stores exactly what the app uploaded")
                self._dev_note("key check OK")
        self.bg(self._verify_keys_sync, done, "Key check failed")

    def upload_all(self):
        if not self.dev.connected:
            return self.set_status("Connect the pad first (Overview / USB cable)", error=True)
        if self._core_only("key mapping"):
            return
        layers = range(LAYERS) if self._layers_supported() else range(1)
        jobs = []
        for lay in layers:
            for sl in range(1, 8):
                m = self.cfg["layers"][lay][str(sl)]
                spec = resolve_spec(self.cfg, m["cat"], m["action"])
                if spec:
                    jobs.append((lay, sl, spec))
        mode, bright, osv = self.pad.mode, self.pad.brightness, self.cfg["os"]
        self.uploading = True
        self.emit("uploading", True)

        def work():
            self.dev.request({"cmd": "os", "val": osv})
            self.dev.request(time_msg())
            self._push_layout()
            for lay, sl, spec in jobs:
                self.dev.request(self._remap_msg(lay, sl, spec))
                self.post(lambda lay=lay, sl=sl, j=spec_json(spec): self._mark_pushed(sl, j, lay))
            if self._pad_cap("gestures"):
                self._sync_gestures()
            self._push_labels()
            self.dev.request({"cmd": "brightness", "val": bright})
            self.dev.request({"cmd": "mode", "val": mode})
            self.post(lambda: self._mark_display_pushed(mode, bright))
            bad = self._verify_keys_sync()
            self._healed = False
            if bad:                                                  # auto-heal: send everything once more, then look again
                for lay, sl, spec in jobs:
                    self.dev.request(self._remap_msg(lay, sl, spec))
                again = self._verify_keys_sync()
                self._healed = not again
                bad = again
            return bad

        def finished():
            self.uploading = False
            self.emit("uploading", False)

        def done(bad):
            finished()
            n = len(set(j[0] for j in jobs))
            if bad:
                self.set_status("Uploaded, but the read-back check found problems: " + "; ".join(bad), error=True)
            else:
                self.set_status(f"Uploaded to the pad: {n} layer{'s' if n > 1 else ''} x 7 key slots, brightness and mode" +
                                ("" if bad is None else " (read back and verified" + (" after one automatic re-send)" if getattr(self, "_healed", False) else ")")))
            self.vp_log("uploaded everything to the physical pad")
        self.bg(work, done, "Upload failed", fail=finished)

    def on_pad_change(self, kind):
        """The virtual pad changed mode / layer / brightness (by the user, or by a key action)."""
        if kind == "mode":
            self.cfg["twin_mode"] = self.pad.mode
        elif kind == "layer":
            self.set_edit_layer(self.pad.layer)
            return
        elif kind == "bright":
            self.cfg["twin_bright"] = self.pad.brightness
        save_config(self.cfg)
        self.emit("pad_state", kind)
        self.recompute_pending()
        if self.autoup and self.dev.connected:
            cmd = {"cmd": "mode", "val": self.pad.mode} if kind == "mode" else {"cmd": "brightness", "val": self.pad.brightness}
            self.bg(lambda: self.dev.request(cmd), None, "Display sync failed")

    def set_autoup(self, on):
        self.autoup = bool(on)

    def reset_defaults(self):
        lay = self.edit_layer
        lm = self.cfg["layers"][lay]
        lm.clear()
        lm.update({str(sl): {"cat": c, "action": n} for sl, (c, n) in DEFAULT_LAYER_MAPS[lay].items()})
        self.history.record(self._edit_snapshot())
        save_config(self.cfg)
        self.emit("map_changed")
        self.recompute_pending()
        if self.dev.connected and (lay == 0 or self._layers_supported()):
            def done(_):
                for sl in range(1, 8):
                    m = lm[str(sl)]
                    self._mark_pushed(sl, spec_json(resolve_spec(self.cfg, m["cat"], m["action"])), lay)
                self.set_status(f"Layer {lay + 1} on the pad reset to factory defaults")
            self.bg(lambda: self.dev.request({"cmd": "reset_keys", **({"layer": lay} if self._layers_supported() else {})}), done, "Reset failed")
        else:
            self.set_status(f"Layer {lay + 1} reset in the app - it reaches the pad with the next upload")

    def push_slot(self, slot):
        self.upload_slots([slot])

    def push_all(self):
        self.upload_all()

    # ------------------------------------------------------------------ layers
    def set_edit_layer(self, n):
        if not 0 <= n < LAYERS:
            return
        self.edit_layer = n
        self.cfg["edit_layer"] = n
        self.cfg["map"], self.cfg["pushed"] = self.cfg["layers"][n], self.cfg["pushed_layers"][n]       # the editors work on cfg["map"]
        if self.pad.layer != n:
            self.pad.set_layer(n, notify=False)
        self.emit("edit_layer", n)
        self.emit("map_changed")
        self.recompute_pending()

    def show_layer_on_pad(self):
        if not self.dev.connected:
            return self.set_status("Not connected", error=True)
        self.bg(lambda: self.dev.request({"cmd": "layer", "val": self.edit_layer}), None, "Layer switch failed")

    # ------------------------------------------------------------------ undo / redo
    def _edit_snapshot(self):
        return {"layers": copy.deepcopy(self.cfg["layers"]), "custom": copy.deepcopy(self.cfg["custom"])}

    def edit_undo(self):
        st = self.history.undo()
        self._edit_apply(st, "Undone") if st else self.set_status("Nothing to undo")

    def edit_redo(self):
        st = self.history.redo()
        self._edit_apply(st, "Redone") if st else self.set_status("Nothing to redo")

    def _edit_apply(self, state, word):
        for n in range(LAYERS):                              # in place: cfg["map"] is one of these dicts
            lm = self.cfg["layers"][n]
            lm.clear()
            lm.update(copy.deepcopy(state["layers"][n]))
        self.cfg["custom"].clear()
        self.cfg["custom"].update(copy.deepcopy(state["custom"]))
        save_config(self.cfg)
        self.emit("map_changed")
        self.emit("library")
        self.recompute_pending()
        if self.autoup and self.dev.connected:
            self.upload_slots(list(range(1, 8)))
        self.set_status(f"{word}: key assignments")

    # ------------------------------------------------------------------ virtual pad: running / testing actions
    @property
    def live_test(self):
        return bool(self.cfg.get("live_test", True)) and self.host.available

    def set_live_test(self, on):
        self.cfg["live_test"] = bool(on)
        save_config(self.cfg)
        if self.live_test:
            self.vp_log("LIVE TEST ON - virtual keys now act on this computer")
            self.pad.toast("LIVE TEST ON", 1.6)
        else:
            self.vp_log("live test off (dry run)")
        self.emit("live_test")

    def live_hint(self):
        """(text, kind) for the line under the live-test switch. kind: err | ok | warn."""
        import platform
        if not self.host.available:
            return ("Live test is not available here: " + (self.host.error or "no key injection backend") +
                    ("  (pip install pynput)" if platform.system() != "Windows" else ""), "err")
        if self.live_test:
            return (("LIVE: virtual keys press real keys on this PC. They go to the last program you used - focus is handed "
                     "back automatically. Click into the sandbox to test inside this app.") if self.focus else
                    "LIVE: virtual keys press real keys on the window that has the focus. Use the delay below or the sandbox.", "ok")
        return "DRY RUN: only the virtual screen reacts. Switch on to press real keys on this PC.", "warn"

    def set_test_delay(self, secs):
        self.cfg["test_delay"] = int(secs)

    def vp_key(self, slot):
        self.emit("key_flash", slot)
        self.pad.key(slot - 1)

    def vp_turn(self, steps):
        self.emit("spin", steps)
        self.emit("key_flash", 6 if steps > 0 else 7)
        self.pad.turn(steps)

    def run_spec(self, spec, name, desc, label=""):
        """Play an action on the twin and - in live mode - on this PC."""
        self.pad.apply_spec(spec)
        self.pad.toast(name)
        if self.live_test:
            self.run_live(spec, name, desc)
        else:
            self.vp_log(f"[dry run] {label}{name}  ({desc})")

    def exec_slot(self, slot):
        m = self.cfg["map"][str(slot)]
        spec = resolve_spec(self.host_cfg(), m["cat"], m["action"])
        if not spec or spec[0] == "none":
            self.vp_log(f"{SLOT_LABELS[slot]}: nothing assigned")
            return
        self.run_spec(spec, m["action"], describe_spec(spec), f"{SLOT_LABELS[slot]}: ")

    def exec_media(self, name):
        self.pad.apply_media(name)
        if self.live_test:
            self.host_q.put((("media", name), "menu volume", name, False))

    def run_live(self, spec, name, desc):
        if name in RISKY and not self.ui.confirm("Run on this PC?", f"'{name}' will really run on this computer now.\nContinue?"):
            return
        delay = int(self.cfg.get("test_delay", 0))
        if delay:
            self._countdown(delay, spec, name, desc)
        else:
            self._enqueue(spec, name, desc)

    def _countdown(self, n, spec, name, desc):
        if self.closing:
            return
        if n <= 0:
            self._enqueue(spec, name, desc)
            return
        self.pad.toast(f"{name} in {n}s", 1.1)
        self.after(1000, lambda: self._countdown(n - 1, spec, name, desc))

    def _enqueue(self, spec, name, desc):
        use_focus = False
        if self.focus and spec_needs_focus(spec):
            in_sandbox = bool(self.ui.sandbox_has_focus())
            use_focus = (not in_sandbox) and self.focus.in_own_window()     # we have the focus: hand it back first
        self.host_q.put((spec, name, desc, use_focus))

    # ------------------------------------------------------------------ macro builder
    def test_spec(self, spec, label):
        self.run_spec(spec, label, describe_spec(spec), "Test: ")

    def guard(self, fn):
        """Run fn; a ValueError becomes a red status message. Returns fn's result or None."""
        try:
            return fn()
        except ValueError as e:
            self.set_status(str(e), error=True)
            return None

    def combo_keys(self, mods, key):
        out = []
        for m in mods:
            if m != "-" and m not in out:
                out.append(m)
        key = (key or "").strip()
        key = key if len(key) == 1 else key.upper()
        if not valid_key(key):
            raise ValueError(f"'{key}' is not a valid key")
        return out + [key]

    @staticmethod
    def text_value(text, enter=False):
        if not text:
            raise ValueError("Enter some text first")
        if any(ord(c) > 126 for c in text):
            raise ValueError("Only ASCII characters can be typed by the pad")
        return text + ("\n" if enter else "")

    def action_spec(self, kind, arg="", transform_label=""):
        """Computer / mouse / layer actions of the macro builder -> a spec, or ValueError with the reason."""
        arg = (arg or "").strip()
        k = next(x for x in ACTION_KINDS if x[0] == kind)
        if k[3] and not arg:
            raise ValueError(f"'{kind}': {k[2]}")
        op = k[1]
        if op == "url":
            if not re.match(r"^(https?://|mailto:)", arg, re.I):
                arg = "https://" + arg
            return ("host", {"op": "url", "arg": arg})
        if op in ("app", "file", "notify"):
            return ("host", {"op": op, "arg": arg})
        if op in ("snippet", "clip") and self.dev.connected and not self._pad_cap("hostx"):
            raise ValueError("Snippets and clipboard transforms need firmware 1.3 on the pad - update it (Pad & App -> Firmware).")
        if op in hostactions.NEW_OPS:
            if self.dev.connected and not self._pad_cap("hostx2"):
                raise ValueError("This action needs firmware 1.5 on the pad - update it (Pad & App -> Firmware).")
            return ("host", {"op": op, "arg": arg or "default"})
        if op == "snippet":
            return ("host", {"op": "snippet", "arg": arg})
        if op == "clip":
            key = next(k2 for k2, v in textops.TRANSFORMS.items() if v[0] == transform_label)
            return ("host", {"op": "clip", "arg": key})
        if op == "shell":
            if not self.cfg.get("allow_shell"):
                raise ValueError("Shell commands are switched off. Turn on 'Allow the pad to run shell commands' in Pad & App first.")
            return ("host", {"op": "shell", "arg": arg})
        if op == "clipboard":
            return ("host", {"op": "clipboard"})
        if op == "click":
            b = (arg or "left").lower()
            if b not in ("left", "right", "middle", "back", "forward"):
                raise ValueError("button must be left, right, middle, back or forward")
            return ("mouse", {"btn": b, "act": "double" if kind.endswith("double click") else "click"})
        if op == "scroll":
            try:
                n = int(arg)
            except ValueError:
                raise ValueError("scroll amount must be a number: positive = up, negative = down") from None
            if not -20 <= n <= 20 or n == 0:
                raise ValueError("scroll amount must be between -20 and 20 (not 0)")
            return ("mouse", {"wheel": n})
        if op == "layer":
            v = arg.lower() or "next"
            if v in ("next", "prev"):
                return ("layer", v)
            if v in ("1", "2", "3"):
                return ("layer", int(v) - 1)
            raise ValueError("layer must be 1, 2, 3, next or prev")
        raise ValueError("unknown action")

    @staticmethod
    def action_step(spec):
        t, v = spec
        return {"host": {"host": v}, "mouse": {"mouse": v}, "layer": {"layer": v}}[t]

    def assign_spec(self, spec, label, slot=None):
        slot = slot or self.target_slot
        self.cfg["custom"][label] = {"type": spec[0], "val": spec[1]}
        self.cfg["map"][str(slot)] = {"cat": "Custom", "action": label}
        self.emit("library")
        self.mapping_changed([slot])
        self.set_status(f"'{label}' assigned to {SLOT_LABELS[slot]} - test it on the pad twin, then upload")

    # -- the sequence
    @staticmethod
    def step_text(st):
        if "combo" in st:
            return "COMBO   " + "+".join(st["combo"])
        if "text" in st:
            return "TEXT    " + repr(st["text"])[:48]
        if "delay" in st:
            return f"DELAY   {st['delay']} ms"
        if "media" in st:
            return "MEDIA   " + st["media"]
        if "host" in st:
            return "COMPUTER " + describe_spec(("host", st["host"]))
        if "mouse" in st:
            return "MOUSE   " + describe_spec(("mouse", st["mouse"]))
        return "LAYER   " + describe_spec(("layer", st["layer"]))

    def seq_lines(self):
        return [self.step_text(s) for s in self.macro_steps]

    def seq_changed(self):
        self.emit("sequence")

    def seq_add(self, step):
        if len(self.macro_steps) >= 64:
            raise ValueError("Sequences are limited to 64 steps")
        self.macro_steps.append(step)
        self.seq_changed()

    def seq_add_delay(self, text):
        try:
            ms = int(text)
        except ValueError:
            raise ValueError("Delay must be a whole number of milliseconds") from None
        if not 0 <= ms <= 60000:
            raise ValueError("Delay must be between 0 and 60000 ms")
        self.seq_add({"delay": ms})

    def seq_remove(self, indexes):
        for i in sorted(indexes, reverse=True):
            if 0 <= i < len(self.macro_steps):
                del self.macro_steps[i]
        self.seq_changed()

    def seq_clear(self):
        self.macro_steps.clear()
        self.seq_changed()

    def seq_move(self, i, d):
        """Move step i by d. Returns the new index (or i when it cannot move)."""
        j = i + d
        if 0 <= i < len(self.macro_steps) and 0 <= j < len(self.macro_steps):
            self.macro_steps[i], self.macro_steps[j] = self.macro_steps[j], self.macro_steps[i]
            self.seq_changed()
            return j
        return i

    def seq_spec(self):
        if not self.macro_steps:
            raise ValueError("The sequence is empty")
        if len(self.macro_steps) > 64:
            raise ValueError("Sequences are limited to 64 steps")
        return ("macro", [dict(s) for s in self.macro_steps])

    def save_seq(self, name):
        def go():
            spec, nm = self.seq_spec(), (name or "").strip() or "My macro"
            self.cfg["custom"][nm] = {"type": spec[0], "val": spec[1]}
            save_config(self.cfg)
            self.emit("map_changed")
            self.emit("library")
            self.set_status(f"Saved '{nm}' - drag it from the 'Custom' category onto a key")
        self.guard(go)

    # -- recorder (pynput)
    @property
    def recording(self):
        return self._rec is not None

    def rec_toggle(self, listener_factory=None, mouse_factory=None, with_mouse=False):
        if self._rec:
            return self._rec_stop()
        if listener_factory is None:
            try:
                from pynput import keyboard as kb
            except Exception:                                  # noqa: BLE001
                return self.set_status("Recording keystrokes needs pynput:  pip install pynput", error=True)
            if not self.ui.confirm("Record keystrokes", "While recording, everything you type in ANY program is captured into the sequence "
                                   "(at most 60 seconds, 64 steps). Do not type passwords.\n\nPress 'Stop recording' here when you are done."):
                return

            def listener_factory(on_press, on_release):
                def nm(k):
                    return getattr(k, "char", None) or getattr(k, "name", "") or ""
                return kb.Listener(on_press=lambda k: on_press(nm(k)), on_release=lambda k: on_release(nm(k)))
        self._recorder = recorder.MacroRecorder()
        self._rec = listener_factory(lambda n: self._recorder.key_down(n, time.monotonic()), lambda n: self._recorder.key_up(n, time.monotonic()))
        self._rec.start()
        self._rec_mouse = None
        if with_mouse:                                         # pointer movement, clicks and the wheel as well
            if mouse_factory is None:
                try:
                    from pynput import mouse as ms

                    def mouse_factory(move, button, scroll):
                        return ms.Listener(on_move=move, on_click=lambda x, y, b, p: button(str(b), p), on_scroll=lambda x, y, dx, dy: scroll(dy))
                except Exception:                              # noqa: BLE001
                    mouse_factory = None
            if mouse_factory is not None:
                r = self._recorder
                self._rec_mouse = mouse_factory(lambda x, y: r.mouse_move(x, y, time.monotonic()), lambda b, p: r.mouse_button(b, p, time.monotonic()),
                                                lambda dy: r.mouse_scroll(dy, time.monotonic()))
                self._rec_mouse.start()
        self.emit("recording", True)
        self.set_status("Recording keystrokes ... press 'Stop recording' when done")
        self._rec_job = self.after(60000, lambda: self._rec and self._rec_stop())

    def _rec_stop(self):
        rec, self._rec = self._rec, None
        for lst in (rec, self._rec_mouse):
            try:
                lst.stop()
            except Exception:                                  # noqa: BLE001
                pass
        self._rec_mouse = None
        if self._rec_job:
            self.after_cancel(self._rec_job)
        steps = self._recorder.finish()
        room = 64 - len(self.macro_steps)
        self.macro_steps.extend(steps[:max(0, room)])
        self.seq_changed()
        self.emit("recording", False)
        self.set_status(f"Recorded {len(steps)} step(s)" + (" - stopped at the 64-step limit" if self._recorder.truncated or len(steps) > room else ""))

    # ------------------------------------------------------------------ share codes / macro files
    def layer_share_code(self, layer=None):
        layer = self.edit_layer if layer is None else layer
        lm = self.cfg["layers"][layer]
        custom = {m["action"]: self.cfg["custom"][m["action"]] for m in lm.values() if m["cat"] == "Custom" and m["action"] in self.cfg["custom"]}
        gestures = {k.split(":", 1)[1]: v for k, v in self.cfg.get("gestures", {}).items() if k.startswith(f"{layer}:")}
        for v in gestures.values():
            if v["cat"] == "Custom" and v["action"] in self.cfg["custom"]:
                custom[v["action"]] = self.cfg["custom"][v["action"]]
        return sharecode.encode({"v": 1, "layer": layer + 1, "map": {k: dict(v) for k, v in lm.items()}, "custom": custom, "gestures": gestures})

    def import_layer_share_code(self, code, layer=None):
        """Replace one layer with the contents of a share code. Every action is validated first; nothing changes if anything is wrong. Returns a summary."""
        layer = self.edit_layer if layer is None else layer
        p = sharecode.decode(code)
        custom_in = p.get("custom") if isinstance(p.get("custom"), dict) else {}
        for name, c in custom_in.items():
            if not (isinstance(name, str) and 0 < len(name) <= 48 and isinstance(c, dict) and spec_ok({"type": c.get("type"), "val": c.get("val")})):
                raise ValueError(f"the code contains an action ('{str(name)[:30]}') this app does not accept")

        def resolves(m):
            if not (isinstance(m, dict) and isinstance(m.get("cat"), str) and isinstance(m.get("action"), str)):
                return False
            if m["cat"] == "Custom":
                return m["action"] in custom_in or m["action"] in self.cfg["custom"]
            return (m["cat"], m["action"]) in ACTION_INDEX
        new_map = p.get("map")
        if not isinstance(new_map, dict) or set(new_map) != {str(i) for i in range(1, 8)} or not all(resolves(m) for m in new_map.values()):
            raise ValueError("the code does not hold a complete, valid key map")
        gest = {}
        for k, m in (p.get("gestures") or {}).items():
            if not (isinstance(k, str) and re.fullmatch(r"([1-5]:(hold|double|triple)|([89]|1[0-5]):press)", k) and resolves(m)):
                raise ValueError("the code contains a gesture this app does not accept")
            gest[f"{layer}:{k}"] = {"cat": m["cat"], "action": m["action"]}
        for name, c in custom_in.items():
            self.cfg["custom"][name] = {"type": c["type"], "val": c["val"]}
        lm = self.cfg["layers"][layer]
        lm.clear()
        lm.update({k: {"cat": m["cat"], "action": m["action"]} for k, m in new_map.items()})
        self.cfg["gestures"] = {k: v for k, v in self.cfg.get("gestures", {}).items() if not k.startswith(f"{layer}:")}
        self.cfg["gestures"].update(gest)
        self.history.record(self._edit_snapshot())
        save_config(self.cfg)
        self.emit("library")
        self.emit("map_changed")
        self.emit("gestures")
        self.recompute_pending()
        return f"layer {layer + 1}: 7 keys, {len(custom_in)} custom action(s), {len(gest)} gesture(s)"

    def share_copy(self):
        code = self.layer_share_code()
        self.ui.clipboard_set(code)
        self.set_status(f"Share code of layer {self.edit_layer + 1} copied ({len(code)} characters) - paste it into a message")
        return code

    def share_import(self, code):
        code = (code or "").strip()
        if not code:
            return self.set_status("Paste a share code first", error=True)
        if not self.ui.confirm("Share code", f"Replace layer {self.edit_layer + 1} with the contents of this code?\n(you can undo it with Ctrl+Z)"):
            return
        try:
            self.set_status("Imported: " + self.import_layer_share_code(code) + "  - upload to send it to the pad")
        except ValueError as e:
            self.set_status(f"Not imported: {e}", error=True)

    def macro_export(self, path=None):
        names = list(self.cfg["custom"])
        if not names:
            return self.set_status("You have no saved macros yet (Keys -> Macro builder -> Save to library)", error=True)
        path = path or self.ui.ask_save("Export macros", "deskcompanion-macros.json", "Macros (*.json)", ".json")
        if path:
            Path(path).write_text(json.dumps({"deskcompanion_macros": 1, "macros": self.cfg["custom"]}, indent=1), encoding="utf-8")
            self.set_status(f"{len(names)} macro(s) exported to {path}")

    def macro_import(self, path=None):
        path = path or self.ui.ask_open("Import macros", "Macros (*.json);;All files (*)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            macros = data["macros"]
            assert data.get("deskcompanion_macros") == 1 and isinstance(macros, dict)
        except Exception:                                # noqa: BLE001
            return self.set_status("That file is not a Desk Companion macro export", error=True)
        n = 0
        for name, spec in macros.items():
            if isinstance(spec, dict) and spec_ok({"type": spec.get("type"), "val": spec.get("val")}):
                self.cfg["custom"][str(name)[:60]] = {"type": spec["type"], "val": spec["val"]}
                n += 1
        save_config(self.cfg)
        self.emit("map_changed")
        self.emit("library")
        self.set_status(f"{n} macro(s) imported - find them in the 'Custom' category" + ("" if n == len(macros) else f" ({len(macros) - n} invalid ones skipped)"))

    # ------------------------------------------------------------------ restore config (backup / auto-backup)
    def restore_config(self, new_cfg, gifs=None):
        keep_dev = {k: self.cfg[k] for k in ("os",) if k in self.cfg}
        new_cfg = normalize_config(dict(new_cfg))
        for lay in new_cfg["pushed_layers"]:
            lay.clear()                                       # nothing is known to be on the pad after a restore
        new_cfg["pushed_mode"] = new_cfg["pushed_bright"] = None
        new_cfg.update({k: v for k, v in keep_dev.items() if k not in new_cfg})
        self.cfg.clear()
        self.cfg.update(new_cfg)
        self.set_edit_layer(0)
        existing = {p.read_bytes() for p in lib_list()}
        for name, data in (gifs or {}).items():
            if data not in existing:
                lib_add(data, Path(name).stem)
        save_config(self.cfg)
        self.emit("map_changed")
        self.recompute_pending()
        for ev in ("profiles", "schedules", "library", "gestures", "scripts", "info_cfg", "mine_gifs", "cfg_replaced"):
            self.emit(ev)

    # ------------------------------------------------------------------ pad display / layout / os settings
    def effective_layout(self):
        from core.base import detect_layout
        v = self.cfg.get("layout", "auto")
        return detect_layout() if v == "auto" else v

    def _push_labels(self, layers=None):
        """Key names for the pad's key toast / popup menu (firmware 1.5). Never fails an upload."""
        if not self._pad_cap("labels"):
            return
        for lay in (range(LAYERS) if layers is None else layers):
            try:
                self.dev.request({"cmd": "labels", "layer": lay, "l": labels_for(self.cfg, lay)})
            except DeviceError:
                return

    def _push_layout(self):
        try:
            self.dev.request({"cmd": "layout", "val": self.effective_layout()})
        except DeviceError:
            pass                                             # older firmware without the layout command

    def set_layout(self, v):
        self.cfg["layout"] = v
        save_config(self.cfg)
        if self.dev.connected:
            self.bg(self._push_layout, lambda _: self.set_status(f"Keyboard layout: {self.effective_layout()}"), "Layout change failed")

    def set_os(self, v):
        self.cfg["os"] = v
        save_config(self.cfg)
        self.recompute_pending()
        if self.dev.connected:
            self.upload_all()

    def set_brightness(self, v):
        """Slider moved: the twin follows at once, the pad after the slider rests for 250 ms."""
        if self._bright_timer:
            self.after_cancel(self._bright_timer)
        val = int(v)
        self.pad.set_brightness(val)
        self._bright_timer = self.after(250, lambda: self.dev.connected and self.bg(
            lambda: self.dev.request({"cmd": "brightness", "val": val}), None, "Brightness failed"))

    def set_mode(self, m):
        m = int(m)
        self.pad.set_mode(m)
        if self.dev.connected:
            self.bg(lambda: self.dev.request({"cmd": "mode", "val": m}),
                    lambda _: self._mark_display_pushed(self.pad.mode, self.pad.brightness), "Mode change failed")

    def sync_time(self):
        self.bg(lambda: self.dev.request(time_msg()), lambda _: self.set_status("Clock synchronised"), "Time sync failed")

    @staticmethod
    def mode_label(m):
        return MODE_CHOICES[max(1, min(len(MODE_CHOICES), int(m))) - 1]

    @staticmethod
    def layer_name(n):
        return LAYER_NAMES[n]

    @staticmethod
    def host_os():
        return HOST_OS
