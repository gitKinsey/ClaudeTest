"""Engine part 4: rules (profiles, schedules, scripts, API, plugins), firmware / recovery / backup, pad health and the diagnostics tools."""
import json
import os
import platform
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import serial

from core.base import (
    ACTION_INDEX, APP_DIR, APP_NAME, APP_VERSION, FW_BUNDLED, LAYERS, SIM_PORT, Device, DeviceError,
    list_serial_ports, fmt_vidpid, lib_list, save_config,
)
from desk_lib import activewin, backup, diag, espota, netactions, presets, scheduler, scripting, updates
from desk_lib import autorules, hwreport, scriptconst


def info_lines(info):
    """Human readable lines for the firmware's `info` reply."""
    order = ["fw", "build", "chip", "rev", "cores", "cpu_mhz", "flash", "core", "usb_mode", "cdc_boot", "hid", "tft", "sim",
             "reset", "crashes", "safe", "up_ms", "heap", "heap_min", "heap_blk", "psram", "temp", "ok_prefs", "ok_fs",
             "ok_sprite", "ok_disp", "fs_free", "fs_total", "led_pin", "led_mode", "mode", "bright", "events", "gpio_touched", "boot"]
    names = {"usb_mode": "usb_mode (0 = TinyUSB, 1 = hardware CDC)", "crashes": "crash-loop counter", "heap_blk": "largest free block",
             "ok_fs": "filesystem ok", "ok_disp": "display ok", "ok_prefs": "settings (NVS) ok", "ok_sprite": "frame buffer ok"}
    lines = []
    for k in order:
        if k in info:
            lines.append(f"{names.get(k, k):42s} {info[k]}")
    for k in info:
        if k not in order and k not in ("ok", "evt", "id"):
            lines.append(f"{k:42s} {info[k]}")
    return lines


class SystemOps:
    # ------------------------------------------------------------------ profiles (the pad's layer follows the focused program)
    @staticmethod
    def default_label(v):
        return "keep the current layer" if v is None or int(v) < 0 else f"Layer {int(v) + 1}"

    def profile_default_set(self, label):
        self.cfg["profile_default"] = -1 if label.startswith("keep") else int(label.split()[-1]) - 1
        save_config(self.cfg)
        self._profile_state["win"] = None                       # re-evaluate at once

    def profiles_set(self, on):
        self.cfg["profiles_on"] = bool(on)
        save_config(self.cfg)
        self._profile_state.update(win=None, layer=None)

    def remember_set(self, on):
        self.cfg["remember_layers"] = bool(on)
        save_config(self.cfg)

    def profile_edit(self, i, **kw):
        self.cfg["profiles"][i].update(kw)
        save_config(self.cfg)
        self._profile_state["win"] = None

    def profile_move(self, i, d):
        r = self.cfg["profiles"]
        if 0 <= i + d < len(r):
            r[i], r[i + d] = r[i + d], r[i]
            save_config(self.cfg)
            self.emit("profiles")

    def profile_remove(self, i):
        del self.cfg["profiles"][i]
        save_config(self.cfg)
        self.emit("profiles")
        self._profile_state["win"] = None

    def apply_preset(self, name, layer, add_rule=True):
        if layer and self.dev.connected and not self._layers_supported():
            return self.set_status("This pad's firmware has no layers - update it (Pad & App -> Firmware)", error=True)
        try:
            slots, rule = presets.apply(self.cfg, name, layer, bool(add_rule), lambda c, a: (c, a) in ACTION_INDEX)
        except (KeyError, ValueError) as e:
            return self.set_status(f"Preset failed: {e}", error=True)
        if layer == self.edit_layer:
            self.emit("map_changed")
        save_config(self.cfg)
        self.recompute_pending()
        self.emit("profiles")
        self._profile_state["win"] = None
        self.set_status(f"'{name}' applied to layer {layer + 1}" + (f"; rule added for {rule['match']}" if rule else "") +
                        " - press 'Upload to pad' on the Keys page to send it")

    def profile_add(self, name, match, kind, layer, time_window="", days="", game=False):
        match = (match or "").strip()
        if not match:
            self.set_status("Enter the program or window text to match first", error=True)
            return False
        tw, dy = (time_window or "").strip(), (days or "").strip()
        try:
            activewin.validate_window(tw, dy)
        except ValueError as e:
            self.set_status(f"Rule not added: {e}", error=True)
            return False
        rule = {"name": (name or "").strip() or match, "match": match, "kind": kind or "either", "layer": layer, "enabled": True}
        if tw:
            rule["time"] = tw
        if dy:
            rule["days"] = dy
        if game:
            rule["game"] = True
        self.cfg["profiles"].append(rule)
        save_config(self.cfg)
        self.emit("profiles")
        self._profile_state["win"] = None
        self.set_status(f"Rule added: '{match}' -> layer {layer + 1}")
        return True

    def profile_capture(self):
        self.set_status("Switch to the program you want to match - capturing in 3 seconds...")

        def grab():
            time.sleep(3.0)
            return self.active_win.get()

        def done(res):
            proc, title = res
            if not proc and not title:
                return self.set_status("Could not detect the focused program on this system (Linux needs xdotool or xprop)", error=True)
            self.emit("profile_captured", proc, title)
            self.set_status(f"Captured: {proc or '-'}  |  {title[:60]}")
        self.bg(grab, done, "Capture failed")

    def _profile_loop(self):
        while not self.closing:
            time.sleep(max(0.05, self.profile_poll))
            st = self._profile_state
            if not self.cfg.get("profiles_on") or not self.dev.connected or self.dev.busy:
                if st["text"] != "profiles are off" and not self.cfg.get("profiles_on"):
                    st["text"] = "profiles are off"
                    self.post(self._profile_label)
                continue
            if not self._layers_supported():
                if st["text"] != "the pad's firmware has no layers - update it (Pad & App -> Firmware)":
                    st["text"] = "the pad's firmware has no layers - update it (Pad & App -> Firmware)"
                    self.post(self._profile_label)
                continue
            proc, title = self.active_win.get()
            if (APP_NAME in title) and proc in ("", "python", "python3", "pythonw", "deskcompanion", "companion_app", "companion_qt", "desk companion"):
                continue                                         # the focus is on this app itself: leave the layer alone
            if not proc and not title:
                st["text"] = "cannot read the focused program on this system"
                self.post(self._profile_label)
                continue
            if (proc, title) == st["win"]:
                continue
            st["win"] = (proc, title)
            default = self.cfg.get("profile_default", 0)
            rule = activewin.pick_rule(self.cfg["profiles"], proc, title)
            self.game_mode = bool(rule and rule.get("game"))
            if rule:
                lay = int(rule.get("layer", 0))
            elif self.cfg.get("remember_layers") and proc in self._app_layers:
                lay = self._app_layers[proc]                                 # no rule: back to what the user last chose in this program
            else:
                lay = None if default is None or int(default) < 0 else int(default)
            st["rule"] = bool(rule)
            st["text"] = f"focused: {proc or '?'} | {title[:50]}   ->   " + (f"layer {lay + 1}" if lay is not None else "no change") + ("   [game mode: pad actions paused]" if self.game_mode else "")
            if lay is not None and lay != st["layer"]:
                try:
                    self.dev.request({"cmd": "layer", "val": lay})
                    st["layer"] = lay
                    self.post(lambda t=f"Profile: {proc or title[:20]} -> layer {lay + 1}": self._dev_note(t))
                except DeviceError:
                    st["win"] = None
            self.post(self._profile_label)

    def _profile_label(self):
        self.emit("profile_label", self._profile_state["text"])

    # ------------------------------------------------------------------ schedules
    def schedule_add(self, when_label, when_arg, do_label, do_arg, name=""):
        try:
            if do_label == "Run shell command" and not self.cfg.get("allow_shell"):
                raise ValueError("Shell commands are switched off (Pad & App -> Behaviour -> 'Allow the pad to run shell commands').")
            entry = autorules.build_entry(when_label, when_arg, do_label, do_arg, name=name)
        except (ValueError, TypeError) as e:
            self.set_status(f"Cannot add the rule: {e}", error=True)
            return None
        self.cfg["schedules"].append(entry)
        self.save_cfg()
        self.emit("schedules")
        self.set_status(f"Rule added: {scheduler.describe(entry)}")
        return entry

    def schedule_enable(self, i, on):
        self.cfg["schedules"][i]["enabled"] = on
        self.save_cfg()
        self.emit("schedules")

    def schedule_remove(self, i):
        del self.cfg["schedules"][i]
        self.save_cfg()
        self.emit("schedules")

    def schedule_next(self, entry):
        return self.scheduler.next_run(entry, datetime.now())

    # ------------------------------------------------------------------ local API
    def api_enable(self, on, port_text):
        """Switch the local API on / off. Returns True when it ended up on."""
        if on:
            try:
                port = int(port_text)
                if not 1024 <= port <= 65535:
                    raise ValueError
            except ValueError:
                self.set_status("The port must be a number from 1024 to 65535", error=True)
                self.emit("api_state")
                return False
            self.cfg["api"]["port"] = port
            ok = self.api_start()
        else:
            self.api_stop()
            self.cfg["api"]["on"] = False
            ok = False
        self.save_cfg()
        self.emit("api_state")
        return ok

    def api_new_token(self):
        self.cfg["api"]["token"] = secrets.token_urlsafe(18)
        if self.api:
            self.api_start()
        self.save_cfg()
        self.emit("api_state")
        self.set_status("New API token created - scripts using the old one are locked out")

    def api_copy_token(self):
        self.ui.clipboard_set(self.cfg["api"]["token"])
        self.set_status("API token copied to the clipboard")

    def api_text(self):
        """(state text, kind ok|err|muted, help text)"""
        a = self.cfg["api"]
        if self.api:
            return (f"Running on http://127.0.0.1:{a['port']}  -  the token is saved with your settings. Use 'Copy token' to put it on the clipboard.", "ok",
                    "python deskcompanion_cli.py layer 2\npython deskcompanion_cli.py led ff8800\n"
                    f"curl -H \"Authorization: Bearer <token>\" -d '{{\"n\": 2}}' http://127.0.0.1:{a['port']}/v1/layer")
        return (self.api_error or "Off.", "err" if self.api_error else "muted", "")

    # ------------------------------------------------------------------ plugins / hotkey / updates / latency
    def plugins_set(self, on):
        self.cfg["plugins_on"] = bool(on)
        self.save_cfg()
        self.plugins_reload()

    def plugins_reload(self):
        if self.cfg.get("plugins_on"):
            self.plugins.load()
        self.emit("plugins")

    def plugins_folder(self):
        from desk_lib.appconst import open_folder
        self.plugins.folder.mkdir(parents=True, exist_ok=True)
        try:
            open_folder(self.plugins.folder)
        except OSError as e:
            self.set_status(f"Could not open the folder: {e}", error=True)

    def plugins_example(self):
        p = self.plugins.install_example()
        self.plugins_reload()
        self.set_status(f"Example written to {p} - put 'Run a plugin: hello:short' on a key")

    def plugins_text(self):
        ph = self.plugins
        if not self.cfg.get("plugins_on"):
            return f"Plugins are off. Folder: {ph.folder}"
        text = f"Loaded: {', '.join(sorted(ph.mods)) or 'none'}.   Folder: {ph.folder}"
        if ph.errors:
            text += "\nProblems: " + "; ".join(f"{k}: {v}" for k, v in ph.errors.items())
        return text

    def hotkey_apply(self, on, combo):
        why = self.hotkey_set(on, combo)
        if why:
            self.cfg["hotkey_on"] = False
            self.save_cfg()
            self.set_status(why, error=True)
            return False
        self.cfg["hotkey_on"], self.cfg["hotkey"] = on, combo
        self.save_cfg()
        self.set_status(f"Global hotkey {combo} is on" if on else "Global hotkey is off")
        return True

    def check_updates(self):
        def done(r):
            self.emit("update_result", r)
        self.bg(lambda: updates.check(self.version_string()), done, "Update check failed")

    def latency_test(self):
        if not self.dev.connected:
            return self.set_status("Not connected", error=True)

        def work():
            ms = []
            for i in range(20):
                t = time.perf_counter()
                self.dev.request({"cmd": "ping", "t": i}, timeout=2)
                ms.append((time.perf_counter() - t) * 1000)
            return diag.latency_summary(ms)
        self.bg(work, lambda s: (self.emit("latency", "Latency: " + s), self.set_status("Latency: " + s)), "Latency test failed")

    def power_text(self):
        ma = diag.power_estimate(self.pad.brightness, "auto", wifi="wifi" in (self.dev.info.get("caps") or []) if self.dev.connected else False)
        return "USB power estimate: " + diag.power_text(ma) + " (rough estimate, not a measurement)"

    # ------------------------------------------------------------------ scripts
    def script_names(self):
        return sorted(self.cfg["scripts"])

    def script_source(self, name):
        return self.cfg["scripts"].get(name, scriptconst.EXAMPLE if not name else "")

    def script_history(self, name):
        return self.cfg["script_history"].get(name, [])

    def script_save(self, name, src):
        name = (name or "").strip()[:32]
        if not name or not all(ch.isalnum() or ch in " _-." for ch in name):
            self.set_status("Give the script a name (letters, digits, space, - _ .)", error=True)
            return None
        try:
            scripting.parse(src)
        except scripting.ScriptError as e:
            self.set_status(f"Not saved - {e}", error=True)
            return None
        old = self.cfg["scripts"].get(name)
        if old is not None and old != src:                       # keep the last 10 versions of every script
            hist = self.cfg["script_history"].setdefault(name, [])
            label = time.strftime("%Y-%m-%d %H:%M:%S")
            while any(h["t"] == label for h in hist):                  # two saves in the same second keep distinct labels
                label += "+"
            hist.insert(0, {"t": label, "src": old})
            del hist[10:]
        self.cfg["scripts"][name] = src
        self.save_cfg()
        self.emit("scripts")
        self.set_status(f"Script '{name}' saved")
        return name

    def script_delete(self, name):
        if name in self.cfg["scripts"]:
            del self.cfg["scripts"][name]
            self.save_cfg()
            self.emit("scripts")
            self.set_status(f"Script '{name}' deleted (keys that run it will report an error)")

    def script_version(self, name, label):
        hit = next((h for h in self.script_history(name) if h["t"] == label), None)
        if not hit:
            self.set_status("There is no earlier version of this script yet", error=True)
            return None
        self.set_status(f"Version from {hit['t']} loaded - press Save to keep it (the current one stays in the history)")
        return hit["src"]

    def script_import_url(self, url, done):
        url = (url or "").strip()

        def work():
            status, text = netactions.http_request("GET", url)
            if status != 200:
                raise ValueError(f"the server answered {status}")
            scripting.parse(text)                                          # must be a valid script
            return text

        def ok(text):
            leaf = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "imported"
            self.set_status("Script imported - READ IT, then press Save (it can run commands and open things)")
            done(text, leaf[:30])
        self.bg(work, ok, "Import failed")

    def script_check(self, src):
        """-> (ok, message)"""
        try:
            nodes = scripting.parse(src)
        except scripting.ScriptError as e:
            self.set_status(f"Script problem: {e}", error=True)
            return False, f"Problem: {e}"
        self.set_status("The script is valid")
        return True, f"OK - {scriptconst.count_commands(nodes)} commands"

    def script_dry(self, src, pretend=""):
        try:
            pretend = (pretend or "").strip()
            win = (pretend.lower(), pretend)                       # pretend this window has the focus (process and title)
            lines = scripting.dry_run(src, lookup=self.cfg["scripts"].get, window=win, clipboard="(clipboard)", now=datetime.now(), ctx={})
        except scripting.ScriptError as e:
            self.set_status(f"Script problem: {e}", error=True)
            return [f"Problem: {e}"]
        return ["Dry run - nothing was sent to the computer:"] + lines

    def script_run_later(self, name, src, say):
        """Save when changed, then run in 3 s (so the user can click into the target program)."""
        if name not in self.cfg["scripts"] or self.cfg["scripts"][name] != src:
            saved = self.script_save(name, src)
            if saved is None:
                return
            name = saved
        self.set_status(f"Running '{name}' in 3 seconds - click into the program it should act on")
        self.after(3000, lambda: self.bg(lambda: self.run_script(name), lambda m: say([m]), "Script failed"))

    def script_stop_request(self):
        self.script_stop.set()
        self.set_status("Stopping the running script")

    def script_assign(self, name):
        if name not in self.cfg["scripts"]:
            return self.set_status("Save the script first", error=True)
        self.assign_spec(("host", {"op": "script", "arg": name}), f"Script: {name}")

    # ------------------------------------------------------------------ pad health
    def refresh_health(self):
        if not self.dev.connected:
            return self.set_status("Connect the pad first", error=True)
        self.bg(lambda: self.dev.request({"cmd": "info"}), self._update_health, "Could not read the pad")

    def _update_health(self, i):
        self.health = dict(i)
        kind, _txt = self.fw_status()
        why = i.get("disp_why") or ""
        tiles = {
            "fw": ("CoreBringup" if i.get("core_only") else str(i.get("fw", "?")), {"ok": "ok", "off": "muted"}.get(kind, "warn")),
            "layer": (str(int(i.get("layer", 0)) + 1), "text"),
            "flash": (f"{i.get('fs_free', 0) // 1024} KB", "text"),
            "reset": (str(i.get("reset", "?")), "text" if i.get("crashes", 0) == 0 else "err"),
            "disp": ("ok" if i.get("ok_disp") else ("stalled" if why in ("init_hang", "skipped_after_hang") else "off" if i.get("nodisp") or i.get("safe") else "FAILED"),
                     "ok" if i.get("ok_disp") else "err"),
            "hid": ("ok" if i.get("hid") else "off (USB Mode)", "ok" if i.get("hid") else "warn"),
            "fs": (str(i.get("fs_state", "?")), "ok" if i.get("ok_fs") else "warn"),
            "temp": (f"{i.get('temp', 0):.0f} C", "text"),
        }
        if why in ("init_hang", "skipped_after_hang") and self._disp_warned != id(self.dev.ser):
            self._disp_warned = id(self.dev.ser)
            self.set_status("The pad's display start-up stalled, so the display is off - everything else (keys, layers, LED, USB) works. "
                            "Check the wiring / User_Setup.h and re-plug the pad to try again.", error=True)
        self.emit("health", tiles)
        self._safe_banner(i)

    # ------------------------------------------------------------------ firmware: version check, USB flash, Wi-Fi OTA
    @staticmethod
    def _ver(v):
        m = re.match(r"(\d+)\.(\d+)\.(\d+)", str(v or ""))
        return tuple(int(x) for x in m.groups()) if m else None

    def fw_status(self):
        """-> (kind, text): kind in off | ok | old | newer | unknown | core"""
        if not self.dev.connected:
            return "off", "pad not connected"
        if self.dev.info.get("core_only"):
            return "core", "CoreBringup (diagnostic sketch) - flash the full firmware to use keys, layers, GIFs and the screens"
        cur = self.dev.info.get("fw")
        a, b = self._ver(cur), self._ver(FW_BUNDLED)
        if a is None:
            return "unknown", f"firmware '{cur}' (unknown version)"
        if a < b:
            return "old", f"firmware {cur} - older than the {FW_BUNDLED} that belongs to this app"
        if a > b:
            return "newer", f"firmware {cur} - newer than this app expects ({FW_BUNDLED}); update the app"
        return "ok", f"firmware {cur} - up to date"

    def wifi_supported(self):
        """Does the connected firmware contain the (optional) Wi-Fi code?  Firmware 1.1 predates the switch and always had it."""
        if not self.dev.connected:
            return True
        caps = self.dev.info.get("caps")
        return True if caps is None else "wifi" in caps

    def wifi_note(self):
        return ("Save your Wi-Fi in the card below, enable OTA with a password, then update without a cable. Not tested on real hardware by the author - "
                "keep the USB way as a fallback." if self.wifi_supported() else
                "This pad's firmware is cable-only (its Wi-Fi code is switched off - DC_ENABLE_WIFI is 0 in the sketch, the default). "
                "Use the USB buttons above. To get Wi-Fi: set DC_ENABLE_WIFI to 1, build and flash.")

    def refresh_fw_status(self):
        kind, text = self.fw_status()
        banner = None
        if kind in ("old", "newer", "unknown", "core"):
            banner = text + (".  Layers, mouse actions, the Info screen and GIF slots need the newer firmware." if kind == "old" else ".")
        self.emit("fw_status", kind, text, banner)

    def flash_firmware(self, which):
        script = APP_DIR / "firmware" / "flash.py"
        if not script.is_file():
            return self.set_status("firmware/flash.py not found next to the app", error=True)
        label = which if which in ("core", "full") else os.path.basename(which)
        if not self.ui.confirm("Flash firmware", f"Write '{label}' to the ESP32-S3 now?\n\n"
                               "The board should be in download mode (BOOT held while plugging in) - or already running DeskCompanion.\n"
                               "Flashing resets the settings stored on the pad (key maps); the app can upload them again."):
            return
        port = self.dev.port if (self.dev.connected and self.dev.port != SIM_PORT) else ""
        was_auto = self.auto_flag
        self.auto_flag = False                            # keep the auto-connect loop away from the port while flashing
        if self.dev.connected:
            self.dev.disconnect()
            self._on_disconnected()
        self.emit("flash_state", True)
        self._dev_note(f"===== flashing '{label}' =====")

        def work():
            args = ["--image", which, "--yes"] + (["--port", port] if port else [])
            # a packaged app has no Python to run flash.py with: the same executable runs it in "flasher mode" instead
            cmd = [sys.executable, "--flash-helper"] + args if getattr(sys, "frozen", False) else [sys.executable, "-u", str(script)] + args
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in p.stdout:
                line = line.rstrip()
                if line:
                    self.post(lambda l=line: self._dev_note(l))
            return p.wait()

        def finish():
            self.auto_flag = was_auto
            self.emit("flash_state", False)

        def done(rc):
            finish()
            if rc == 0:
                self._flashed_at = time.time()
            self._dev_note("flash finished OK - unplug / re-plug the board; the app reconnects by itself" if rc == 0
                           else f"flash FAILED (exit code {rc}) - see the lines above", err=rc != 0)
            self.set_status("Flash finished - unplug and re-plug the board WITHOUT holding BOOT. The first start can take ~40 s; the app connects by itself." if rc == 0
                            else "Flash failed - details in Diagnostics / the log", error=rc != 0)
        self.bg(work, done, "Flashing failed", fail=finish)

    def flash_other(self, path=None):
        path = path or self.ui.ask_open("Firmware image", "Firmware images (*.bin);;All files (*)")
        if path:
            self.flash_firmware(path)

    def ota_enable(self, on, pw=""):
        if not self.dev.connected:
            return self.set_status("Connect the pad first", error=True)

        def work():
            return self.dev.request({"cmd": "ota", "val": on, **({"pass": pw} if pw else {})})
        self.bg(work, lambda r: self.set_status("Wi-Fi update " + ("enabled - the pad joins your Wi-Fi within ~30 s" if on else "disabled")), "OTA setting failed")

    def ota_update(self, pw=""):
        if not self.dev.connected:
            return self.set_status("Connect the pad first", error=True)
        image = APP_DIR / "firmware" / "DeskCompanion-wifi.bin"
        if not image.is_file():
            return self.set_status("firmware/DeskCompanion-wifi.bin not found", error=True)
        if not self.ui.confirm("Update over Wi-Fi", "Send the bundled Wi-Fi-enabled firmware to the pad over Wi-Fi?\n\nExperimental: if it fails the pad keeps its old firmware, "
                               "but keep a USB cable at hand."):
            return
        self.emit("ota_state", True)
        self.emit("ota_progress", 0.0)

        def work():
            ip = self.dev.request({"cmd": "info"}).get("ip", "")
            if not ip:
                raise RuntimeError("the pad is not on Wi-Fi yet (save Wi-Fi, enable OTA, wait ~30 s)")
            data = espota.app_image_from_merged(image)
            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
                f.write(data)
            try:
                espota.ota_upload(ip, f.name, pw, progress=lambda x: self.post(lambda x=x: self.emit("ota_progress", x)))
            finally:
                os.unlink(f.name)
            return ip
        fin = lambda: self.emit("ota_state", False)           # noqa: E731
        self.bg(work, lambda ip: (fin(), self.set_status(f"Wi-Fi update sent to {ip} - the pad restarts now")), "Wi-Fi update failed", fail=fin)

    def wifi_save(self, ssid, password):
        self.bg(lambda: self.dev.request({"cmd": "wifi", "ssid": ssid, "pass": password}), lambda _: self.set_status("Wi-Fi saved on the pad"), "Wi-Fi save failed")

    # ------------------------------------------------------------------ recovery
    def recovery_check(self):
        if not self.dev.connected:
            return self.set_status("Connect the pad first", error=True)

        def done(i):
            if i.get("safe"):
                why = {"crash_loop": "it crashed 3 times in a row", "forced": "this is a safe-mode test build"}.get(i.get("safe_why"), "unknown reason")
                txt, kind = f"SAFE MODE - {why}. Last reset: {i.get('reset', '?')}, crashes counted: {i.get('crashes', 0)}. Display and GIFs are off.", "err"
            else:
                txt, kind = f"normal mode. Last reset: {i.get('reset', '?')}. Display {'ok' if i.get('ok_disp') else 'OFF'}, storage {i.get('fs_state', '?')}" + \
                    ("  (display disabled at boot)" if i.get("nodisp") else ""), ("ok" if i.get("ok_disp") else "warn")
            self.emit("safe_text", txt, kind)
            self._safe_banner(i)
        self.bg(lambda: self.dev.request({"cmd": "info"}), done, "Could not read the pad state")

    def recovery(self, what):
        if not self.dev.connected:
            return self.set_status("Connect the pad first", error=True)
        confirm = {"keys": "Reset ALL key maps on the pad (every layer) to factory defaults?",
                   "settings": "Erase ALL settings stored on the pad (keys, brightness, mode, Wi-Fi, ...) and restart it?",
                   "gifs": "Delete every GIF stored on the pad? (the built-in demo animation comes back)"}.get(what)
        if confirm and not self.ui.confirm("Are you sure?", confirm):
            return
        msgs = {"safe_retry": ({"cmd": "safe_retry"}, "Restarting the pad for a normal boot"),
                "nodisp": ({"cmd": "boot_opt", "nodisp": True}, "Display will stay off from the next boot (Re-enable display to undo)"),
                "disp": ({"cmd": "boot_opt", "nodisp": False}, "Display enabled again from the next boot"),
                "keys": ({"cmd": "factory", "what": "keys", "confirm": True}, "Pad key maps reset"),
                "settings": ({"cmd": "factory", "what": "settings", "confirm": True}, "Pad settings erased - restarting"),
                "gifs": ({"cmd": "factory", "what": "gifs", "confirm": True}, "Stored GIFs deleted")}
        cmd, ok_text = msgs[what]

        def done(_):
            self.set_status(ok_text)
            if what in ("keys", "settings"):
                for n in range(LAYERS):
                    self.cfg["pushed_layers"][n].clear()
                self.recompute_pending()
            if what in ("nodisp", "safe_retry"):
                self.bg(lambda: self.dev.request({"cmd": "reboot"}), None, "Reboot failed")
        self.bg(lambda: self.dev.request(cmd), done, "Recovery step failed")

    def _safe_banner(self, info):
        if info.get("core_only"):
            return
        self.safe_info = info if info.get("safe") else None
        self.emit("safe", "The pad is in SAFE MODE (it crashed repeatedly). Display and GIFs are off so it stays reachable." if info.get("safe") else None)

    def boot_report(self):
        """How the pad's last start-up went, its reset statistics and USB link events (firmware 1.5)."""
        if not self.dev.connected:
            return self.set_status("Not connected", error=True)
        if not self._pad_cap("bootlog"):
            return self.set_status("The boot report needs firmware 1.5 - update the pad (Pad & App -> Firmware).", error=True)

        def done(r):
            c = r.get("counts") or [0] * 6
            lines = ["---- boot report ----", f"last reset: {r.get('reset')}   crashes in a row: {r.get('crashes')}   display: {r.get('disp_why') or 'ok'}",
                     "resets since the counters were cleared: power-on %d, software %d, panic %d, watchdog %d, brownout %d, other %d" % tuple(c),
                     f"USB/host link: {r.get('usb_connects')} connects, {r.get('usb_drops')} drops   heap {r.get('heap', 0) // 1024} KB (lowest {r.get('heap_min', 0) // 1024} KB)",
                     "start-up notes: " + str(r.get("log", "")), "---------------------"]
            for ln in lines:
                self._dev_note(ln)
            self.set_status("Boot report written to the Diagnostics log" + (" - the pad crashed recently!" if (r.get("crashes") or 0) else ""))
        self.bg(lambda: self.dev.request({"cmd": "boot_log"}), done, "Boot report failed")

    def rollback_fw(self):
        if not self.dev.connected:
            return self.set_status("Not connected", error=True)
        if not self._pad_cap("rollback"):
            return self.set_status("Rolling back needs firmware 1.5 on the pad first.", error=True)
        if not self.ui.confirm("Roll back", "Boot the previous firmware again?\n\nThis only works after a Wi-Fi update, when the old firmware is still stored in the pad's other slot. "
                               "The pad restarts."):
            return
        self.bg(lambda: self.dev.request({"cmd": "rollback", "confirm": True}), lambda r: self.set_status("Rolling back - the pad restarts"), "Rollback not possible")

    # ------------------------------------------------------------------ backup / restore
    def _read_pad_state(self):
        """Everything stored on the connected pad that is not in the app's config (layers read back slot by slot)."""
        layers = []
        for lay in range(LAYERS if self._layers_supported() else 1):
            row = []
            for sl in range(1, 8):
                r = self.dev.request({"cmd": "getkeys", "slot": sl, **({"layer": lay} if lay else {})}, timeout=4)
                row.append(r.get("spec"))
            layers.append(row)
        h = self.dev.request({"cmd": "hello"})
        return {"layers": layers, "bright": h.get("bright"), "mode": h.get("mode"), "os": h.get("os"), "layout": h.get("layout"), "fw": h.get("fw")}

    def backup_export(self, path=None):
        path = path or self.ui.ask_save("Back up everything", time.strftime("deskcompanion-backup-%Y%m%d.zip"), "Backup (*.zip)", ".zip")
        if not path:
            return

        def work():
            pad = self._read_pad_state() if self.dev.connected else None
            data = backup.make_backup(self.cfg, pad, lib_list(), APP_VERSION)
            Path(path).write_bytes(data)
            return len(data), pad is not None
        self.bg(work, lambda r: self.set_status(f"Backup written: {path}  ({r[0] // 1024} KB, {'with' if r[1] else 'without'} the pad's own key data)"), "Backup failed")

    def backup_import(self, path=None):
        path = path or self.ui.ask_open("Restore a backup", "Backup (*.zip);;All files (*)")
        if not path:
            return
        try:
            manifest, cfg, pad, gifs = backup.read_backup(Path(path).read_bytes())
        except Exception as e:                           # noqa: BLE001
            return self.set_status(f"This is not a usable backup: {e}", error=True)
        if not self.ui.confirm("Restore backup", f"Backup from {manifest.get('created', '?')} (app {manifest.get('app', '?')}).\n\n"
                               f"It replaces your key maps, macros, profiles and settings in this app and adds {len(gifs)} GIF(s) to My GIFs.\n\nContinue?"):
            return
        self.restore_config(cfg, gifs)
        self.set_status("Backup restored in the app - press 'Upload to pad' to send it to the device")

    def autobackup_list(self):
        return self.autobackup().list()

    def autobackup_now(self):
        p = self.autobackup().maybe(self.cfg, force=True)
        self.set_status("Backed up now" if p else "Nothing changed since the last backup")
        return p

    def autobackup_restore(self, path):
        try:
            cfg = self.autobackup().read(path)
        except ValueError as e:
            self.set_status(str(e), error=True)
            return False
        self.restore_config(cfg)
        self.set_status(f"Settings restored from {Path(path).name} - press 'Upload to pad' to send them to the device")
        return True

    # ------------------------------------------------------------------ diagnostics tools (was: Dev tab)
    def dreq(self, msg, ok=None, label="Command failed", timeout=4.0):
        if not self.dev.connected:
            self.set_status("Not connected - plug the pad in or use 'Simulate pad' on the Overview page", error=True)
            return
        self.bg(lambda: self.dev.request(msg, timeout=timeout), ok, label)

    def dev_state_text(self):
        """(text, kind) for the Diagnostics header."""
        d = self.dev
        if not d.connected:
            return "Not connected", "warn"
        sim = d.port == SIM_PORT
        bits = [f"{'SIMULATED pad' if sim else d.port}", f"fw {d.info.get('fw', '?')}"]
        for k, nm in (("hid", "HID"), ("disp", "display")):
            if k in d.info:
                bits.append(f"{nm} {'ok' if d.info[k] else 'OFF'}")
        if "fs" in d.info:
            st = d.info.get("fs_state", "ready" if d.info["fs"] else "failed")
            bits.append("files ok" if d.info["fs"] else f"files {st}...")
        if d.info.get("safe"):
            bits.append("SAFE MODE (crash loop!)")
        return "Connected: " + "  |  ".join(bits), ("ok" if not d.info.get("safe") else "err")

    def send_raw(self, text):
        t = (text or "").strip()
        if not t:
            return False
        try:
            self.dev.send_raw(t)
            return True
        except DeviceError as e:
            self._dev_note(f"send failed: {e}", err=True)
            return False

    def copy_term(self):
        self.ui.clipboard_set("\n".join(self.term_lines))
        self.set_status("Terminal copied to the clipboard")

    def ports_table(self):
        ports = list_serial_ports()
        rows = []
        for p in ports:
            res = "connected (this app)" if (self.dev.connected and self.dev.port == p["device"]) else ("ESP32 device" if p["esp"] else "")
            rows.append((p["device"], fmt_vidpid(p), f"{p['device']} - {p['desc']}", res, p))
        if not ports:
            hint = ("No serial ports at all. Check the USB cable (many cables are charge-only) and the port on the board "
                    "(use the USB-C of the ESP32-S3-Zero).")
        elif not any(p["esp"] for p in ports):
            hint = ("Serial ports exist but none looks like an Espressif device (VID 303A). Is the right cable/port used? "
                    "Unplug and re-plug the pad while watching this list.")
        else:
            hint = "Select a row for details."
        return rows, hint

    def probe_ports(self):
        ports = [p for p in list_serial_ports() if not (self.dev.connected and self.dev.port == p["device"])]
        self._dev_note(f"probing {len(ports)} port(s) with hello ...")

        def work():
            out = {}
            for p in ports:
                dev = p["device"]
                try:
                    s = Device._open(dev)
                except Exception as e:                       # noqa: BLE001
                    out[dev] = f"cannot open: {str(e)[:60]}"
                    continue
                try:
                    got, end = b"", time.monotonic() + 3.0
                    s.write(b'{"cmd":"hello"}\n')
                    while time.monotonic() < end and b"desk-companion" not in got:
                        got += s.read(256)
                        if b"\n" in got and b"desk-companion" not in got and time.monotonic() > end - 1.5:
                            s.write(b'{"cmd":"hello"}\n')
                    if b"desk-companion" in got:
                        try:
                            j = json.loads(got.decode("utf-8", "replace").strip().splitlines()[-1])
                            out[dev] = f"DeskCompanion fw {j.get('fw', '?')}"
                        except ValueError:
                            out[dev] = "DeskCompanion (reply unparsable)"
                    elif got:
                        out[dev] = f"talks, but not DeskCompanion: {got[:40]!r}"
                    else:
                        out[dev] = "opened, no reply in 3 s"
                finally:
                    try:
                        s.close()
                    except Exception:                        # noqa: BLE001
                        pass
            return out

        def done(out):
            self.emit("probe_result", out)
            for dev, res in out.items():
                self._dev_note(f"probe {dev}: {res}")
        self.bg(work, done, "Probe failed")

    def led_color(self, r, g, b):
        self.dreq({"cmd": "led", "r": r, "g": g, "b": b}, None, "LED command failed")

    def led_mode(self, mode):
        self.dreq({"cmd": "led", "mode": mode}, None, "LED command failed")

    def read_inputs(self):
        def ok(r):
            self.emit("inputs", r.get("keys", []), r.get("enc_sw", 0), r.get("enc_pos", 0))
            self._dev_note(f"inputs: keys {r.get('keys')} enc_sw {r.get('enc_sw')} A={r.get('enc_a')} B={r.get('enc_b')} pos {r.get('enc_pos')}")
        self.dreq({"cmd": "inputs"}, ok, "Read failed")

    def events_set(self, on):
        self.dreq({"cmd": "events", "val": bool(on)}, None, "Events failed")

    def snapshot(self, quiet=False):
        if not self.dev.connected:
            return self.set_status("Not connected", error=True)
        if not quiet:
            self._dev_note("downloading screen snapshot ...")
        self.bg(lambda: self.dev.snapshot(), lambda img: (self.emit("snapshot", img), None if quiet else self._dev_note("snapshot received")), "Snapshot failed")

    def mirror_busy_snapshot(self, done, fail):
        """One live-mirror frame (not while the pad is busy)."""
        self.bg(lambda: self.dev.snapshot(), done, "Mirror stopped", fail=fail)

    def hid_later(self, msg, n=3):
        if not self.dev.connected:
            return self.set_status("Not connected", error=True)
        if n > 0:
            self.set_status(f"Click into a text editor now ... sending in {n}")
            self.after(1000, lambda: self.hid_later(msg, n - 1))
        else:
            self.dreq(msg, lambda r: self.set_status("HID test sent"), "HID test failed (is the pad in USB-OTG / TinyUSB mode?)")

    def gpio(self, pin_text, op):
        try:
            pin = int(pin_text)
        except ValueError:
            return self.set_status("Pin must be a number", error=True)

        def ok(r):
            txt = f"GPIO{r['pin']} = {r['val']}  {('(' + r['use'] + ')') if r.get('use') else ''}  {r.get('warn', '')}"
            self.emit("gpio_text", txt)
            self._dev_note(txt)
        self.dreq({"cmd": "gpio", "pin": pin, "op": op}, ok, "GPIO command refused")

    def gpio_scan(self):
        def ok(r):
            hi = [p for p, v in r["pins"] if v]
            lo = [p for p, v in r["pins"] if not v]
            txt = f"HIGH: {hi}\nLOW: {lo}"
            self.emit("gpio_text", txt)
            self._dev_note("gpio scan " + txt.replace("\n", "  "))
        self.dreq({"cmd": "gpio", "op": "scan"}, ok, "Scan failed")

    def reboot(self, download=False):
        if download:
            if self.ui.confirm("Download mode", "Reboot the pad into the ROM download (flashing) mode?\nIt will disappear from the app until you re-flash or re-plug it."):
                self.dreq({"cmd": "reboot", "mode": "download"}, lambda _r: self._dev_note("pad is rebooting into download mode"), "Reboot failed")
        else:
            self.dreq({"cmd": "reboot"}, lambda _r: self._dev_note("rebooting ..."), "Reboot failed")

    def ping5(self):
        if not self.dev.connected:
            return self.set_status("Not connected", error=True)

        def work():
            times = []
            for i in range(5):
                t = time.perf_counter()
                self.dev.request({"cmd": "ping", "t": i}, timeout=2)
                times.append((time.perf_counter() - t) * 1000)
            return times
        self.bg(work, lambda t: self._dev_note(f"ping x5: min {min(t):.1f} ms  avg {sum(t) / len(t):.1f} ms  max {max(t):.1f} ms"), "Ping failed")

    def show_info(self):
        def ok(r):
            self.last_info = r
            self._dev_note("---- device info ----")
            for ln in info_lines(r):
                self._dev_note(ln)
            self._dev_note("---------------------")
        self.dreq({"cmd": "info"}, ok, "Info failed")

    def full_selftest(self):
        dev = self.dev
        if not dev.connected:
            return self.set_status("Not connected - nothing to test", error=True)
        self._dev_note("===== full self-test: watch the LED (R,G,B) and the screen =====")

        def work():
            res = []
            t0 = time.monotonic()
            while time.monotonic() - t0 < 90:                    # first boot after flashing: storage is formatted in the background
                h = dev.request({"cmd": "hello"}, timeout=3)
                if h.get("fs") or h.get("fs_state") == "failed" or "fs" not in h:
                    break
                self.post(lambda st=h.get("fs_state"): self._dev_note(f"waiting for the pad's storage ({st}) - first boot only ..."))
                time.sleep(3)

            def check(name, fn):
                try:
                    detail = fn()
                    res.append((True, name, detail or ""))
                except Exception as e:                       # noqa: BLE001
                    res.append((False, name, str(e)))

            def t_ping():
                ts = []
                for i in range(5):
                    t = time.perf_counter()
                    dev.request({"cmd": "ping", "t": i}, timeout=2)
                    ts.append((time.perf_counter() - t) * 1000)
                return f"avg {sum(ts) / len(ts):.1f} ms"

            def t_echo():
                msg = {"a": 1, "b": "xé\"y", "c": [1, 2]}
                r = dev.request({"cmd": "echo", "data": msg}, timeout=2)
                if r.get("data") != msg:
                    raise DeviceError(f"echo mismatch: {r.get('data')}")

            def t_info():
                r = dev.request({"cmd": "info"}, timeout=3)
                self.last_info = r
                bad = [k for k in ("ok_prefs", "ok_fs", "ok_disp") if k in r and not r[k]]
                if r.get("safe"):
                    raise DeviceError(f"SAFE MODE after {r.get('crashes')} crashes (last reset: {r.get('reset')}) - display disabled")
                if bad:
                    raise DeviceError("subsystem(s) failed: " + ", ".join(bad) + f"  boot log: {r.get('boot')}")
                return f"{r.get('chip')} core {r.get('core')} heap {r.get('heap')} reset {r.get('reset')}"

            def t_led():
                for rgbv in ((60, 0, 0), (0, 60, 0), (0, 0, 60)):
                    dev.request({"cmd": "led", "r": rgbv[0], "g": rgbv[1], "b": rgbv[2]}, timeout=2)
                    time.sleep(0.5)
                dev.request({"cmd": "led", "mode": "auto"}, timeout=2)
                return "sent red / green / blue - did the LED change colour?"

            def t_firmware():
                r = dev.request({"cmd": "selftest"}, timeout=10)
                bad = [k for k in ("nvs", "fs", "heap_ok") if not r.get(k)]
                if bad:
                    raise DeviceError("failed: " + ", ".join(bad))
                return f"nvs ok, fs ok, heap {r.get('heap')}, display {r.get('display')}"

            def t_inputs():
                r = dev.request({"cmd": "inputs"}, timeout=2)
                stuck = [i + 1 for i, v in enumerate(r["keys"]) if v]
                if stuck:
                    raise DeviceError(f"key(s) {stuck} read as PRESSED while idle - wiring / short?")
                return f"all 5 keys idle, encoder A={r.get('enc_a')} B={r.get('enc_b')}"

            def t_keys():
                r = dev.request({"cmd": "getkeys"}, timeout=3)
                return f"{sum(1 for s in r['slots'] if not s['def'])} custom key slot(s) stored on the pad"

            check("link: ping x5", t_ping)
            check("link: JSON echo round trip (unicode / quotes)", t_echo)
            check("firmware: subsystems + safe mode", t_info)
            check("firmware: NVS / filesystem / heap self-test", t_firmware)
            check("onboard LED colour cycle", t_led)
            check("keys idle (wiring)", t_inputs)
            check("stored key mappings readable", t_keys)
            return res

        def done(res):
            ok = sum(1 for r in res if r[0])
            for good, name, detail in res:
                self._dev_note(f"{'PASS' if good else 'FAIL'}  {name}  {detail}", err=not good)
            self._dev_note(f"===== self-test finished: {ok}/{len(res)} passed =====", err=ok != len(res))
            self.emit("selftest_done", res)
        self.bg(work, done, "Self-test failed")

    def report_text(self):
        d = self.dev
        out = [f"DeskCompanion diagnostic report  {time.strftime('%Y-%m-%d %H:%M:%S')}",
               f"app: {APP_NAME}  python {platform.python_version()}  {platform.platform()}",
               f"pyserial {getattr(serial, '__version__', '?')}", "", "serial ports:"]
        for p in list_serial_ports():
            out.append(f"  {p['device']:10s} {fmt_vidpid(p)}  {p['desc']}  [{p['mfr']}] {'<-- ESP32' if p['esp'] else ''}")
        out += ["", f"connected: {d.connected}  port: {d.port}  rx {d.rx_bytes} B  tx {d.tx_bytes} B", "hello:", f"  {d.info}", "info:"]
        out += ["  " + ln for ln in info_lines(self.last_info)] if self.last_info else ["  (not fetched - press 'Device info')"]
        out += ["", "last terminal lines:"] + ["  " + ln for ln in self.term_lines[-150:]]
        return "\n".join(out)

    def report(self):
        text = self.report_text()
        self.ui.clipboard_set(text)
        path = Path.home() / "deskcompanion_diag.txt"
        try:
            path.write_text(text, encoding="utf-8")
            where = f"and saved to {path}"
        except OSError:
            where = ""
        self.set_status(f"Diagnostic report copied to the clipboard {where} - paste it to whoever is helping you")
        self._dev_note("diagnostic report copied")

    def export_zip(self, path=None):
        """report.txt + the settings with every secret removed + (firmware 1.5) the pad's boot log, in one .zip to attach to a bug report."""
        path = path or self.ui.ask_save("Save the diagnostic report", "deskcompanion_report.zip", "Zip file (*.zip)", ".zip")
        if not path:
            return
        d = self.dev

        def work():
            files = {"report.txt": self.report_text(), "settings_redacted.json": json.dumps(diag.redact(self.cfg), indent=1, default=str)}
            if d.connected and self._pad_cap("bootlog"):
                try:
                    files["pad_boot_log.json"] = json.dumps(d.request({"cmd": "boot_log"}), indent=1)
                except DeviceError as e:
                    files["pad_boot_log.json"] = f"unavailable: {e}"
            Path(path).write_bytes(diag.build_zip(files))
            return path
        self.bg(work, lambda p: self.set_status(f"Report saved to {p} (settings are included with passwords, tokens and API keys removed)"), "Could not save the report")

    # ------------------------------------------------------------------ hardware test report
    def hwtest_save(self, results):
        path = Path.home() / time.strftime("deskcompanion_hwtest_%Y%m%d_%H%M.html")
        info = self.dev.info if self.dev.connected else {}
        path.write_text(hwreport.report_html(results, info, APP_VERSION), encoding="utf-8")
        self.set_status(f"Report saved: {path}")
        return path
