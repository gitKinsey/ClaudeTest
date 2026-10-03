"""The headless heart of Desk Companion: state, threads, connection handling and the services every page needs.
No widgets in here. A front end (Qt) talks to it through method calls and listens to events (`engine.on("status", fn)`).
Events are always delivered on the front end's main thread, in the order they were emitted (`pump()` drains the queue)."""
import platform
import queue
import threading
import time
import traceback
from datetime import datetime

import psutil
import serial
from serial.tools import list_ports

from core.base import (
    API_PORT_OVERRIDE, APP_VERSION, CONFIG_PATH, FIXED_PORT, LAYERS, SIM_PORT,
    Device, DeviceError, HostInput, HostMedia, VirtualPad, Win32Api, WinFocus,
    candidate_ports, fmt_vidpid, list_serial_ports, load_config, resolve_spec, save_config, time_msg,
)
from desk_lib import (
    activewin, audio, autorules, bridge, cliphist, feeds, history, hostactions, hotkey, i18n, ledfx, netactions, padconst,
    plugins, scheduler, screenstate, scripting, sysactions, tips, tray, updates, winlayout,
)


class NullFrontend:
    """What the engine needs from a front end besides events. The defaults make headless tests work (every question is answered 'yes')."""

    def __init__(self):
        self.clip = ""
        self.asked = []

    def confirm(self, title, text):
        self.asked.append((title, text))
        return True

    def ask_open(self, title="", filters=""):
        return ""

    def ask_open_many(self, title="", filters=""):
        return []

    def ask_save(self, title="", initial="", filters="", suffix=""):
        return ""

    def clipboard_get(self):
        return self.clip

    def clipboard_set(self, text):
        self.clip = text

    def sandbox_has_focus(self):
        return False


class PluginApi:
    """What a plugin gets as `api` (see desk_lib/plugins.py)."""

    def __init__(self, engine):
        self._e = engine

    def type_text(self, text):
        self._e._type_text(str(text))

    def notify(self, title, text):
        self._e.post(lambda: self._e.notify(str(title), str(text), "ok"))

    def clipboard(self):
        return self._e._clipboard_text()

    def set_card(self, label, title, a="", b=""):
        self._e.set_custom_card(label, title, a, b)


class ScriptBackend(scripting.Backend):
    """Lets macro scripts act on this computer (the interpreter checks the shell switch itself)."""

    def __init__(self, engine):
        self.e = engine

    def _impl(self):
        if self.e.host.impl is None:
            raise scripting.ScriptError("the key sender is not available (pip install pynput)")
        return self.e.host.impl

    def key(self, keys): self._impl().run(("combo", list(keys)))
    def text(self, s): self._impl().run(("text", s))
    def click(self, how): self._impl().run(("mouse", {"btn": "left", "act": "double"} if how == "double" else {"btn": how, "act": "click"}))
    def scroll(self, n): self._impl().run(("mouse", {"wheel": n}))
    def media(self, name): self._impl().run(("media", name))

    def host(self, op, arg):
        ok, msg = self.e.hostact.run_trusted(op, arg)
        if not ok:
            raise scripting.ScriptError(msg)

    def wait(self, ms):
        if self.e.script_stop.wait(ms / 1000.0):
            raise scripting.Stop

    def window(self): return self.e.active_win.get()
    def clipboard(self): return self.e._clipboard_text()

    def exec(self, cmd):
        import subprocess
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)           # noqa: S602 - the user's own script, shell switch checked by the interpreter
        return r.returncode, r.stdout

    def card(self, label, title, a, b):
        self.e.set_custom_card(label, title, a, b)

    def alert(self, hex_, times):
        if self.e.dev.connected and self.e._pad_cap("ledfx"):
            self.e.dev.request({"cmd": "led", "alert": hex_, "times": times})

    def http(self, arg):
        return netactions.webhook(arg, self.e.hostact.net_fetch)

    def ask(self, prompt):
        ai = self.e.cfg["ai"]
        return netactions.ask_ai(prompt, ai.get("key", ""), ai.get("model") or "claude-haiku-4-5-20251001", self.e.hostact.net_fetch)

    def translate(self, lang):
        return netactions.translate(self.e._clipboard_text(), lang, self.e.hostact.net_fetch)

    def moveto(self, x, y): self._impl().moveto(x, y)
    def clickat(self, x, y, btn): self._impl().clickat(x, y, btn)


class EngineCore:
    # ------------------------------------------------------------------ construction
    def __init__(self, frontend=None, start_threads=True):
        self.ui = frontend or NullFrontend()
        self.cfg = load_config()
        i18n.set_language(self.cfg.get("language", "en"))
        self.ui_q, self.closing, self.auto_flag = queue.Queue(), False, True
        self._main = threading.get_ident()
        self._subs = {}
        self.status, self.log_lines, self.term_lines = ("", False), [], []
        self.conn_state = ("Searching...", "warn")
        self.connect_lock = threading.Lock()
        self.dev = Device(lambda: self.post(self._on_disconnected))
        self.macro_steps = []
        self.pending_slots, self._warned, self._was_connected = set(), set(), False
        self.pending_by_layer = [set() for _ in range(LAYERS)]
        self._fails = {}
        self.host, self.host_q, self.host_stop = HostInput(), queue.Queue(), threading.Event()
        self.hostmedia = HostMedia()
        self.focus = None                                       # Windows: remembers the program you were working in
        if platform.system() == "Windows":
            try:
                self.focus = WinFocus(Win32Api())
            except Exception:                                   # noqa: BLE001
                self.focus = None
        self.edit_layer, self.pad_layer, self.hw_listener = 0, None, None   # layer shown in the app; layer the pad is on; hardware-test hook
        self.active_win, self.profile_poll, self._usage_dirty = activewin.ActiveWindow(), 1.0, False
        self.cliphist = cliphist.ClipHistory()
        self.info_poll, self._info_cache, self._info_sent = 3.0, {}, (None, 0.0)
        try:
            self.badges = feeds.BadgeServer(token=self.cfg["info"].get("token"), port=int(self.cfg["info"].get("port", 0) or 0))
        except OSError:                                         # the saved port is taken: use any free one
            try:
                self.badges = feeds.BadgeServer(token=self.cfg["info"].get("token"))
            except OSError:
                self.badges = None
        if self.badges:
            self.cfg["info"]["token"], self.cfg["info"]["port"] = self.badges.token, self.badges.port
        self._profile_state = {"win": None, "layer": None, "text": "profiles are off"}
        self.game_mode, self._app_layers = False, {}
        self.screen = screenstate.ScreenState()
        self._dim = {"locked": None, "fs": None, "applied": None, "saved": None, "tick": time.monotonic()}
        self.hostact = hostactions.HostActions(
            self._allowed_host, lambda: bool(self.cfg.get("allow_shell")),
            type_clipboard=self._type_clipboard, type_text=self._type_text, read_clipboard=self._clipboard_text, counter=self._next_counter,
            script_runner=self.run_script, sysact=sysactions.SysActions(), layouts=winlayout.WinLayouts(), layout_store=lambda: self.cfg["layouts"],
            cliphist=self.cliphist, cfg_get=lambda k, d=None: self.cfg.get(k, d), focus_program=lambda: (self.active_win.get()[0] or ""),
            notify=lambda t, m: self.post(lambda: self.notify(t, m, "ok")), plugin_runner=self._run_plugin, pad_image=self.pad_image)
        self.plugins = plugins.PluginHost(CONFIG_PATH.parent / (CONFIG_PATH.name + ".plugins"), api=PluginApi(self))
        if self.cfg.get("plugins_on"):
            self.plugins.load()
        self.hotkey_obj = None
        self.spectrum, self.alerts, self._alert_snap = audio.Spectrum(), ledfx.AlertTracker(), {}
        self.history = history.EditHistory()
        if self.host.impl is not None:
            self.host.impl.host_cb = self._host_cb
        self._script_lock, self.script_stop = threading.Lock(), threading.Event()
        self.tray_icon = None
        self.scheduler = scheduler.Scheduler(lambda: self.cfg["schedules"])
        self.api, self.api_error = None, ""
        self.health, self.trouble, self.safe_info, self._disp_warned = {}, None, None, None
        self.tip_id = None
        self._flashed_at = 0.0
        self._bright_timer = None
        self.pad = VirtualPad(self.exec_slot, self.exec_media, self.on_pad_change)
        self.pad.mode, self.pad.brightness = int(self.cfg["twin_mode"]), int(self.cfg["twin_bright"])
        self.autoup = False                                     # "upload every change immediately"
        self.init_state()                                       # mixins create their own state
        if self.cfg["api"]["on"]:
            self.api_start()
        self.dev.on_line, self.dev.on_event = self._on_dev_line, self._on_pad_event
        self.history.record(self._edit_snapshot())
        if self.cfg.get("hotkey_on"):
            self.hotkey_set(True, self.cfg["hotkey"])
        if start_threads:
            self.start_threads()

    def start_threads(self):
        if self.cfg.get("update_check"):
            self.after(4000, self._startup_update_check)
        self.refresh_tip()
        if self.focus:
            threading.Thread(target=self._focus_loop, daemon=True).start()
        for fn in (self._host_worker, self._telemetry_loop, self._monitor_loop, self._media_loop, self._profile_loop, self._info_loop,
                   self._schedule_loop, self._clip_loop, self._audio_led_loop, self._screen_loop):
            threading.Thread(target=fn, daemon=True).start()

    def init_state(self):
        """Overridden by the mixins' combined class."""

    # ------------------------------------------------------------------ threads, events, status
    def on(self, name, fn):
        self._subs.setdefault(name, []).append(fn)
        return fn

    def off(self, name, fn):
        try:
            self._subs.get(name, []).remove(fn)
        except ValueError:
            pass

    def _dispatch(self, name, args):
        for fn in list(self._subs.get(name, ())):
            try:
                fn(*args)
            except Exception:                                   # noqa: BLE001
                traceback.print_exc()

    def emit(self, name, *args):
        """Deliver an event on the main thread (at once when already there, queued otherwise)."""
        if threading.get_ident() == self._main:
            self._dispatch(name, args)
        else:
            self.ui_q.put(lambda: self._dispatch(name, args))

    def post(self, fn):
        self.ui_q.put(fn)

    def pump(self):
        """Run what other threads queued. The front end calls this from a timer on its main thread."""
        n = 0
        try:
            while True:
                fn = self.ui_q.get_nowait()
                n += 1
                try:
                    fn()
                except Exception:                               # noqa: BLE001
                    traceback.print_exc()
        except queue.Empty:
            pass
        return n

    def after(self, ms, fn):
        t = threading.Timer(ms / 1000.0, lambda: None if self.closing else self.post(fn))
        t.daemon = True
        t.start()
        return t

    @staticmethod
    def after_cancel(t):
        try:
            t.cancel()
        except Exception:                                       # noqa: BLE001
            pass

    def bg(self, fn, ok=None, label="Error", fail=None):
        def run():
            try:
                res = fn()
            except Exception as e:                              # noqa: BLE001
                self.post(lambda e=e: self.set_status(f"{label}: {e}", error=True))
                if fail:
                    self.post(fail)
            else:
                if ok:
                    self.post(lambda: ok(res))
        threading.Thread(target=run, daemon=True).start()

    def set_status(self, text, error=False):
        self.status = (text, error)
        self._log(text)
        self.emit("status", text, error)

    def _log(self, text):
        line = time.strftime("%H:%M:%S  ") + text
        self.log_lines.append(line)
        del self.log_lines[:-500]
        self.emit("log", line)

    def set_conn(self, text, kind):
        """kind: ok | warn | err | off  (the connection pill)."""
        self.conn_state = (text, kind)
        self.emit("conn_state", text, kind)

    def goto(self, page, section=None):
        self.emit("goto", page, section)

    def notify(self, title, text, kind="ok"):
        if not self.cfg.get("notify", True) or self.closing:
            return
        self.emit("toast", title, text, kind)

    def save_cfg(self):
        save_config(self.cfg)

    def autobackup(self):
        from core.base import autobackup_for
        return autobackup_for()

    def version_string(self):
        return APP_VERSION

    # ------------------------------------------------------------------ window / tray / hotkey
    def show_window(self):
        self.emit("show_window")

    def tray_layer(self, n):
        self.set_edit_layer(n)
        self.show_layer_on_pad()

    def quit_app(self):
        self.emit("quit")

    def tray_enable(self, on):
        """Switch the tray icon on / off. Returns '' or a reason it is not possible."""
        if on:
            if not tray.available():
                return "the tray icon needs:  pip install pystray"
            try:
                self.tray_icon = self.tray_icon or tray.Tray(self)
                self.tray_icon.start()
            except Exception as e:                              # noqa: BLE001  (no tray on this desktop, ...)
                self.tray_icon = None
                return f"the tray icon could not start: {e}"
        elif self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        return ""

    def want_hide_on_close(self):
        return bool(self.cfg.get("tray") and self.tray_icon is not None and not self.closing)

    def shutdown(self):
        """Everything the old _really_close did except destroying a window."""
        self.closing = True
        if self.tray_icon is not None:
            self.tray_icon.stop()
        self.api_stop()
        if self.badges:
            try:
                self.badges.close()
            except Exception:                                   # noqa: BLE001
                pass
        if self.hotkey_obj is not None:
            self.hotkey_obj.stop()
        save_config(self.cfg)
        self.host_stop.set()
        self.host_q.put(None)
        self.dev.disconnect()

    def hotkey_set(self, on, combo):
        """Returns '' or the reason it is not possible."""
        if self.hotkey_obj is not None:
            self.hotkey_obj.stop()
            self.hotkey_obj = None
        if not on:
            return ""
        try:
            self.hotkey_obj = hotkey.GlobalHotkey(combo, lambda: self.post(self._hotkey_fired))
            self.hotkey_obj.start()
        except Exception as e:                                  # noqa: BLE001  (no pynput, no display, Wayland ...)
            self.hotkey_obj = None
            return f"the global hotkey is not possible here: {e}"
        return ""

    def _hotkey_fired(self):
        self.show_window()
        self.emit("open_palette")

    def _startup_update_check(self):
        def done(r):
            if r["newer"]:
                self.set_status(r["message"] + "  -  Pad & App -> This app")
                self.notify("Desk Companion update", r["message"], "ok")
        self.bg(lambda: updates.check(APP_VERSION), done, "Update check failed")

    # ------------------------------------------------------------------ tips
    def refresh_tip(self):
        t = tips.pick(self.cfg, (self.dev.info.get("caps") or []) if self.dev.connected else [], self.cfg["tips_dismissed"],
                      seed=int(time.time() // 86400)) if self.cfg.get("tips", True) else None
        self.tip_id = t[0] if t else None
        self.emit("tip", t[1] if t else None)

    def dismiss_tip(self):
        if self.tip_id:
            self.cfg["tips_dismissed"].append(self.tip_id)
            save_config(self.cfg)
        self.refresh_tip()

    # ------------------------------------------------------------------ appearance (the front end applies it)
    def set_appearance(self, mode):
        mode = mode if mode in ("dark", "light", "system") else "dark"
        self.cfg["appearance"] = mode
        save_config(self.cfg)
        self.emit("appearance", mode)

    def set_pref(self, key, value, restart=False):
        """Store a simple setting. Appearance-like settings are announced so the front end can apply them."""
        self.cfg[key] = value
        save_config(self.cfg)
        self.emit("pref", key, value)
        if restart:
            self.set_status("Saved - it applies the next time the app starts")

    # ------------------------------------------------------------------ clipboard / host input
    def _clipboard_text(self):
        if threading.get_ident() == self._main:
            return (self.ui.clipboard_get() or "")[:2000]
        ev, box = threading.Event(), {}

        def grab():
            try:
                box["t"] = self.ui.clipboard_get()
            except Exception:                                   # noqa: BLE001
                box["t"] = ""
            ev.set()
        self.post(grab)
        ev.wait(2.0)
        return (box.get("t") or "")[:2000]

    def clipboard_set(self, text):
        self.ui.clipboard_set(text)

    def _type_text(self, txt):
        if self.host.impl is None:
            raise RuntimeError("typing needs the key sender (pip install pynput)")
        self.host.impl.run(("text", txt))

    def _type_clipboard(self):
        txt = self._clipboard_text()
        if txt:
            self._type_text(txt)

    def _next_counter(self, name):
        c = self.cfg["counters"]
        c[name] = int(c.get(name, 0)) + 1
        save_config(self.cfg)
        return c[name]

    def _allowed_host(self):
        allowed = hostactions.collect_allowed(list(self.cfg["layers"]) + [self.cfg["gestures"]], self.cfg["custom"],
                                              lambda c, a: resolve_spec(self.host_cfg(), c, a))
        for e in self.cfg["schedules"]:                         # a scheduled rule is part of the user's own configuration too
            if e["do"]["kind"] == "host":
                allowed.add((e["do"]["op"], e["do"].get("arg", "")))
        return allowed

    def run_host(self, op, arg=""):
        """Run one of the user's own host actions from a button in the app (no whitelist needed: the user pressed it). Raises RuntimeError with the reason."""
        ok, msg = self.hostact.run_trusted(op, arg)
        if not ok:
            raise RuntimeError(msg)
        return msg

    def _host_cb(self, spec):
        ok, msg = self.hostact.run(spec.get("op"), spec.get("arg", ""))
        if not ok:
            raise RuntimeError(msg)
        if spec.get("op") in hostactions.NEW_OPS:               # OS / network actions say what they did
            self.post(lambda m=msg: self.set_status(m))

    def _run_plugin(self, spec):
        if not self.cfg.get("plugins_on"):
            raise ValueError("plugins are switched off (Pad & App -> This app -> Plugins)")
        return self.plugins.run(spec)

    def _host_worker(self):
        while True:
            item = self.host_q.get()
            if item is None:
                return
            spec, name, desc, use_focus = item
            target = ""
            try:
                if use_focus:
                    target = self.focus.refocus()
                    if not target:
                        raise RuntimeError("no other program to send to - switch to one first, or click into the sandbox")
                if spec[0] == "host":                           # opening a URL / starting a program needs no key injection
                    self._host_cb(spec[1])
                else:
                    self.host.run(spec, self.host_stop)
            except Exception as e:                              # noqa: BLE001
                self.post(lambda e=e, n=name: self.vp_log(f"!! {n} failed: {e}"))
            else:
                self.post(lambda n=name, d=desc, t=target: self.vp_log(f"sent to {t or 'this PC'}: {n}  ({d})"))

    def _focus_loop(self):
        while not self.closing and self.focus:
            try:
                self.focus.poll()
            except Exception:                                   # noqa: BLE001
                pass
            time.sleep(0.15)

    # ------------------------------------------------------------------ scripts
    def set_custom_card(self, label, title, a, b, kind="c"):
        """The pad's Info screen custom card (also used by the local API and by scripts). kind: c plain, r ring, p progress bar, s scrolling text (firmware 1.5)."""
        info = self.cfg["info"]
        info.update(custom=any((label, title, a, b)), c_label=label, c_t=title, c_a=a, c_b=b, c_k=kind if kind in ("c", "r", "p", "s") else "c")
        self._info_sent = (None, 0.0)

    def script_ctx(self):
        return {"counter": self._next_counter}

    def run_script(self, name):
        """Runs a saved script (called on the host worker thread). Returns a message; raises on problems."""
        src = self.cfg["scripts"].get(name)
        if src is None:
            raise RuntimeError(f"there is no script called '{name}'")
        if not self._script_lock.acquire(blocking=False):
            raise RuntimeError("another script is still running (Scripts page -> Stop)")
        self.script_stop.clear()
        try:
            n = scripting.run(src, ScriptBackend(self), lookup=self.cfg["scripts"].get, ctx=self.script_ctx(), shell_ok=bool(self.cfg.get("allow_shell")))
        except scripting.ScriptError as e:
            raise RuntimeError(f"script '{name}': {e}") from None
        finally:
            self._script_lock.release()
        return f"script '{name}' ran {n} commands"

    # ------------------------------------------------------------------ local API
    def api_start(self):
        self.api_stop()
        a = self.cfg["api"]
        try:
            self.api = bridge.Bridge(self._api_handle, token=a["token"], port=int(API_PORT_OVERRIDE or a["port"]), status=self._api_status)
            self.api_error = ""
        except OSError as e:
            self.api, self.api_error = None, f"port {a['port']} is not available ({e.strerror or e})"
        a["on"] = self.api is not None
        return self.api is not None

    def api_stop(self):
        if getattr(self, "api", None):
            try:
                self.api.close()
            except Exception:                                   # noqa: BLE001
                pass
        self.api = None

    def _api_status(self):
        i = self.dev.info if self.dev.connected else {}
        return {"app": APP_VERSION, "connected": self.dev.connected, "firmware": i.get("fw", ""), "layer": self.pad_layer, "mode": i.get("mode")}

    def _api_handle(self, r):
        """Runs on the API server's thread."""
        k = r["kind"]
        if k == "notify":
            self.post(lambda: self.notify("Desk Companion", r["text"], "ok"))
            return {}
        if k == "badge":
            if not self.badges:
                raise bridge.ApiError(503, "the badge service is not running")
            self.badges.set(r["name"], r["n"])
            return {}
        if k == "card":
            info = self.cfg["info"]
            info.update(custom=any(r[x] for x in ("label", "title", "a", "b")), c_label=r["label"], c_t=r["title"], c_a=r["a"], c_b=r["b"], c_k=r.get("k", "c"))
            return {}
        if not self.dev.connected:
            raise bridge.ApiError(409, "the pad is not connected")
        try:
            if k == "layer":
                if not self._layers_supported():
                    raise bridge.ApiError(409, "the pad's firmware has no layers")
                self.dev.request({"cmd": "layer", "val": r["val"]})
            elif k in ("mode", "brightness"):
                self.dev.request({"cmd": k, "val": r["val"]})
            elif k == "led":
                self.dev.request({"cmd": "led", **{x: r[x] for x in ("mode", "hex") if x in r}})
            elif k == "press":
                self.dev.request({"cmd": "input", "k": r["key"]} if r["key"] <= 5 else {"cmd": "input", "turn": 1 if r["key"] == 6 else -1})
        except DeviceError as e:
            raise bridge.ApiError(502, str(e)) from None
        return {}

    # ------------------------------------------------------------------ scheduled actions
    def _schedule_loop(self):
        while not self.closing:
            time.sleep(5)
            if self.closing:
                return
            try:
                due = self.scheduler.tick(datetime.now())
            except Exception:                                   # noqa: BLE001
                traceback.print_exc()
                continue
            for e in due:
                self.run_schedule(e)
            if any(e.pop("_retired", None) for e in self.cfg["schedules"]):
                self.post(lambda: (self.save_cfg(), self.emit("schedules")))

    def run_schedule(self, entry, manual=False):
        if self.game_mode and not manual:
            return

        def work():
            def request(msg):
                if not self.dev.connected:
                    raise RuntimeError("the pad is not connected")
                self.dev.request(msg)
            return autorules.run_entry(entry, request, self.hostact.run, lambda t: self.post(lambda: self.notify("Reminder", t, "ok")))
        label = entry.get("name") or "Scheduled action"
        self.bg(work, lambda desc: self.set_status(f"{label}: {desc}"), f"{label} failed")

    # ------------------------------------------------------------------ background loops
    def _telemetry_loop(self):
        psutil.cpu_percent(None)
        nxt, last_sync, last_flush = time.monotonic() + 1.0, 0.0, time.monotonic()
        while not self.closing:
            time.sleep(max(0.0, nxt - time.monotonic()))
            nxt += 1.0
            cpu, ram = psutil.cpu_percent(None), psutil.virtual_memory().percent
            self.post(lambda c=cpu, r=ram: self._meters(c, r))
            if time.monotonic() - last_flush > 30:
                last_flush = time.monotonic()
                if self._usage_dirty:
                    self._usage_dirty = False
                    save_config(self.cfg)
                    self.emit("usage")
            if self.dev.connected and not self.dev.busy:
                try:
                    self._led_follow_cpu(cpu)
                    self.dev.send({"cmd": "stats", "cpu": round(cpu), "ram": round(ram)})
                    if not self.dev.info.get("core_only") and time.time() - last_sync > 60:
                        last_sync = time.time()                  # BEFORE the request: a pad that refuses it is asked again in a minute, not every second
                        self.dev.request(time_msg())
                except DeviceError:
                    pass

    def _meters(self, cpu, ram):
        self.cpu, self.ram = cpu, ram
        self.pad.cpu, self.pad.ram = cpu, ram
        self.emit("meters", cpu, ram)

    def _led_follow_cpu(self, cpu):
        """Optional: the pad's RGB LED goes green -> amber -> red with the CPU load (called once a second from the telemetry thread)."""
        want_cpu, want_mood = bool(self.cfg.get("led_cpu")), bool(self.cfg.get("led_mood"))
        on, last = (want_cpu or want_mood) and not self.cfg.get("led_audio"), getattr(self, "_led_cpu_last", None)
        if self.dev.info.get("core_only") or (self.cfg.get("led_audio") and last is None):
            return
        if not on:
            if last is not None:
                self._led_cpu_last = None
                self.dev.request({"cmd": "led", "mode": "auto"}, timeout=2)
            return
        rgb = padconst.cpu_color(cpu) if want_cpu else ledfx.mood_color()
        if last is None or max(abs(a - b) for a, b in zip(rgb, last[0])) > 14 or time.time() - last[1] > 30:
            self.dev.request({"cmd": "led", "r": rgb[0], "g": rgb[1], "b": rgb[2]}, timeout=2)
            self._led_cpu_last = (rgb, time.time())

    def _audio_led_loop(self):
        """Sound-reactive pad: the LED colour (about 8 updates a second) and / or the SOUND screen's bars, from the audio input (needs sounddevice + numpy)."""
        running, last, last_viz = False, None, 0.0
        while not self.closing:
            time.sleep(0.12)
            live = self.dev.connected and not self.dev.info.get("core_only")
            want_led, want_viz = bool(self.cfg.get("led_audio")) and live, bool(self.cfg.get("viz_on")) and live and self._pad_cap("viz")
            if not (want_led or want_viz):
                if running:
                    self.spectrum.stop()
                    running = False
                    if self.dev.connected and last is not None:
                        try:
                            self.dev.request({"cmd": "led", "mode": "auto"}, timeout=2)
                        except DeviceError:
                            pass
                last = None
                continue
            if not running:
                try:
                    self.spectrum.start()
                    running = True
                except ValueError as e:
                    self.cfg["led_audio"] = self.cfg["viz_on"] = False
                    self.post(lambda e=e: (self.set_status(str(e), error=True), self.emit("pref", "led_audio", False), self.emit("pref", "viz_on", False)))
                    continue
            if want_led:
                rgb = audio.color(self.spectrum.current())
                if rgb != last and not self.dev.busy:
                    try:
                        self.dev.send({"cmd": "led", "r": rgb[0], "g": rgb[1], "b": rgb[2]})
                        last = rgb
                    except DeviceError:
                        pass
            elif last is not None:
                try:
                    self.dev.request({"cmd": "led", "mode": "auto"}, timeout=2)
                except DeviceError:
                    pass
                last = None
            if want_viz and not self.dev.busy and time.monotonic() - last_viz >= 0.12:
                last_viz = time.monotonic()
                try:
                    self.dev.send({"cmd": "viz", "v": self.spectrum.current_bars()})
                except DeviceError:
                    pass
        self.spectrum.stop()

    def _media_loop(self):
        """Mirror this PC's volume / mute / playing onto the pad's media screen: on change, and every 10 s as a keep-alive."""
        last, sent_at = None, 0.0
        if self.hostmedia.kind == "pycaw":                          # COM objects need initialising once per thread
            try:
                import comtypes
                comtypes.CoInitialize()
            except Exception:                                       # noqa: BLE001
                pass
        while not self.closing:
            time.sleep(1.5)
            if not (self.dev.connected and not self.dev.busy and self.cfg.get("host_media_sync", True) and self.hostmedia.available):
                last = None                                         # re-send everything after a reconnect
                continue
            st = {k: v for k, v in self.hostmedia.state().items() if v is not None}
            if not st:
                continue
            if st != last or time.time() - sent_at > 10:
                try:
                    self.dev.send(dict(st, cmd="media"))
                    last, sent_at = st, time.time()
                except DeviceError:
                    last = None
                self.post(lambda st=st: self._media_mirror(st))

    def _media_mirror(self, st):
        """The virtual pad follows the real values as well, so the twin never disagrees with the physical pad."""
        if "vol" in st:
            self.pad.vol = st["vol"]
        if "muted" in st:
            self.pad.muted = st["muted"]
        if "playing" in st:
            self.pad.playing = st["playing"]
        self.pad.dirty = True

    def _monitor_loop(self):
        seen = set()
        while not self.closing:
            allp = list_serial_ports()
            info = {p["device"]: p for p in allp}
            ports = [p["device"] for p in allp if p["esp"] and (not FIXED_PORT or p["device"] == FIXED_PORT)]
            for d in set(ports) - seen:                      # a pad (or any Espressif device) just appeared
                p = info[d]
                self.post(lambda p=p: self._dev_note(f"Espressif USB device appeared: {p['device']}  {fmt_vidpid(p)}  {p['desc']}  - {p['hint']}"))
            for d in seen - set(ports):
                self.post(lambda d=d: self._dev_note(f"serial port {d} disappeared (unplugged / rebooting / re-enumerating)"))
            seen = set(ports)
            gone = set(self._fails) | self._warned
            for p in gone - set(ports):                      # unplugged: forget, so the next plug-in starts fresh
                self._fails.pop(p, None)
                self._warned.discard(p)
                self.post(self._clear_trouble)
            if self.auto_flag and not self.dev.connected:
                for port in ports:
                    if self._try_connect(port, info.get(port)):
                        break
            time.sleep(1.0)

    @staticmethod
    def trouble_text(pinfo, fails, recently_flashed=False):
        """Plain-language reason + next steps for a pad that is visible as a serial port but does not answer."""
        vp = fmt_vidpid(pinfo) if pinfo else "?"
        pid = (pinfo or {}).get("pid")
        if pid == 0x1001:
            return (f"The pad shows up as {vp}: that is the chip's built-in bootloader / serial, not DeskCompanion. "
                    "Unplug the cable and plug it back in WITHOUT holding BOOT, then wait ~10 s. "
                    "(It also looks like this when a sketch was built with USB Mode = Hardware CDC and JTAG.)")
        lines = [f"A USB device ({vp}) is there on {pinfo['device'] if pinfo else 'a port'} but it does not answer yet (tried {fails}x)."]
        if recently_flashed or fails < 4:
            lines.append("Right after flashing, the first start prepares the pad's storage and can take up to ~40 s - the app keeps trying by itself.")
        lines.append("If it stays like this: 1) unplug and re-plug the cable (try another USB port), 2) Diagnostics -> Probe all ports, "
                     "3) on Windows: Device Manager -> View -> Show hidden devices -> uninstall the old 'USB Serial Device' / 'USB Composite Device' "
                     "entries of the board, then re-plug, 4) check the LED: a red double-blink means safe mode, no LED means the sketch is not running.")
        return "  ".join(lines)

    def _show_trouble(self, port, pinfo, fails):
        text = self.trouble_text(pinfo, fails, time.time() - self._flashed_at < 180)
        self.set_conn("Not answering", "err")
        self.set_status(text.split(".  ")[0] + ".", error=True)
        self.trouble = text
        self.emit("trouble", text)

    def _clear_trouble(self):
        if self.trouble is not None:
            self.trouble = None
            self.emit("trouble", None)

    def _try_connect(self, port, pinfo=None):
        if not self.connect_lock.acquire(blocking=False):
            return False
        try:
            info = self.dev.connect(port, hello_wait=8 if self._fails.get(port, 0) < 2 else 20)
        except (DeviceError, serial.SerialException, OSError) as e:
            self._fails[port] = self._fails.get(port, 0) + 1
            msg = str(e).lower()
            busy = isinstance(e, PermissionError) or any(w in msg for w in ("denied", "busy", "permission", "in use"))
            vp = fmt_vidpid(pinfo) if pinfo else "?"
            hint = (pinfo or {}).get("hint", "")
            self.post(lambda e=e: self._log(f"connect {port} failed: {e}"))
            if self._fails[port] >= 2 and not busy:
                self.post(lambda n=self._fails[port]: self._show_trouble(port, pinfo, n))
            self.post(lambda e=e: self._dev_note(f"connect {port} ({vp}) failed: {e}   {hint}", err=True))
            if port not in self._warned and (busy or self._fails[port] >= 2):   # 2 tries x 8 s = the pad had plenty of time to boot
                self._warned.add(port)
                if busy:
                    self.post(lambda: self.notify(f"{port} is in use by another program",
                              "Close the Arduino / PlatformIO serial monitor - DeskCompanion connects by itself afterwards.", "warn"))
                else:
                    self.post(lambda: self.notify(f"USB device found on {port}  ({vp})",
                              (hint + "  " if hint else "") + "It does not answer as DeskCompanion. Open Diagnostics and press 'Probe all ports'.", "warn"))
            return False
        finally:
            self.connect_lock.release()
        self._fails.pop(port, None)
        self.post(self._clear_trouble)
        self.post(lambda: self._on_connected(info))
        return True

    # ------------------------------------------------------------------ connection (user driven)
    def port_choices(self):
        """[(label, device)] sorted so Espressif devices come first."""
        pm = {f"{p.device} - {p.description}": p.device for p in list_ports.comports()}
        auto = candidate_ports()
        return [(k, pm[k]) for k in sorted(pm, key=lambda k: pm[k] not in auto)]

    def toggle_connect(self, port):
        if self.dev.connected:
            self.dev.disconnect()
            self.auto_flag = False
            self.emit("auto_flag", False)
            return self._on_disconnected()
        if not port:
            return self.set_status("Pick a serial port first", error=True)
        self.set_status(f"Connecting to {port}...")
        self.bg(lambda: self._connect_locked(port), self._on_connected, "Connect failed")

    def set_auto_connect(self, on):
        self.auto_flag = bool(on)

    def toggle_simulate(self):
        if self.dev.connected:
            was_sim = self.dev.port == SIM_PORT
            self.dev.disconnect()
            self._on_disconnected()
            if was_sim:
                return
        self.auto_flag = False
        self.emit("auto_flag", False)
        self.set_status("Starting simulated pad...")
        self.bg(lambda: self._connect_locked(SIM_PORT), self._on_connected, "Simulate failed")

    def _connect_locked(self, port):
        with self.connect_lock:
            return self.dev.connect(port)

    @property
    def simulated(self):
        return self.dev.connected and self.dev.port == SIM_PORT

    def _on_connected(self, info):
        simulated = self.dev.port == SIM_PORT
        label = "Simulated pad (no hardware)" if simulated else self.dev.port
        self.set_conn("Simulated pad" if simulated else "Pad connected", "ok")
        self.set_status("Simulated pad connected - try remapping keys, macros or a GIF upload" if simulated else "Pad connected")
        self._was_connected = True
        self.notify("DeskCompanion connected", f"{label}  -  firmware {info.get('fw', '?')}", "ok")
        self._dev_note(f"connected on {label}: {info}")
        from core.base import NUM_MODES
        m, b = max(1, min(NUM_MODES, int(info.get("mode", 1)))), int(info.get("bright", 200))
        self.pad.set_mode(m, notify=False)                     # the virtual screen adopts what the pad is showing
        self.pad.set_brightness(b, notify=False)
        self.cfg["pushed_mode"], self.cfg["pushed_bright"] = m, b
        self.recompute_pending()
        self._pad_layer_changed(int(info.get("layer", 0)))
        self._info_sent = (None, 0.0)
        self._profile_state.update(win=None, layer=None)
        self.emit("connected", info, label)
        self.emit("pad_state", "mode")
        self.emit("pad_state", "bright")
        self.refresh_fw_status()
        self.refresh_health()
        self.refresh_pad_gifs()
        self.pad_settings_on_connected()
        self.screens_on_connected()
        self.refresh_tip()
        if not self.cfg.get("wizard_done") and not simulated and not getattr(self, "_wizard_offered", False):
            self._wizard_offered = True
            self.after(800, lambda: self.emit("open_wizard"))

        def work():
            if info.get("core_only"):                              # the CoreBringup sketch only knows the diagnostic commands
                return
            self.dev.request({"cmd": "os", "val": self.cfg["os"]})
            self.dev.request(time_msg())
            self._push_layout()
        self.bg(work, None, "Initial sync failed")
        if info.get("core_only"):
            self.set_status("Connected to the CoreBringup diagnostic sketch - LED, ports and GPIO tests work. Flash the full firmware (Pad & App -> Firmware) for the rest.")

    def _on_disconnected(self):
        self.set_conn("Not connected", "warn")
        self.refresh_fw_status()
        self.pad_settings_refresh()
        self.refresh_pad_gifs()
        self.set_status("Pad disconnected - waiting for it to reappear" if self.auto_flag else "Disconnected")
        self.emit("disconnected")
        self._dev_note("disconnected")
        self.refresh_tip()
        if self._was_connected:
            self._was_connected = False
            self.notify("DeskCompanion disconnected", "Waiting for the pad to be plugged in again...", "off")

    # ------------------------------------------------------------------ terminal / device lines
    def _on_dev_line(self, direction, text):
        """Called from the serial reader / writer threads."""
        self.post(lambda: self.term_add(direction, text))

    def term_add(self, direction, text):
        prefix = {"tx": "->", "rx": "<-", "raw": "!!", "sys": "..", "err": "XX"}.get(direction, "  ")
        line = f"{time.strftime('%H:%M:%S')} {prefix} {text}"
        self.term_lines.append(line)
        del self.term_lines[:-600]
        self.emit("term", direction, line[:600])

    def _dev_note(self, text, err=False):
        self.term_add("err" if err else "sys", text)

    def term_clear(self):
        self.term_lines.clear()
        self.emit("term_clear")

    # ------------------------------------------------------------------ events coming from the pad
    def _on_pad_event(self, m):
        """Reader thread: layer changes, host actions, usage counters."""
        self.emit("dev_event", m)
        if self.hw_listener:
            self.hw_listener(m)
        evt = m.get("evt")
        if evt == "layer" and isinstance(m.get("n"), int):
            self.post(lambda n=m["n"]: self._pad_layer_changed(n))
        elif evt == "host" and self.hw_listener:
            self.post(lambda: self.vp_log("host action ignored while the hardware test is running"))
        elif evt == "host" and self.game_mode:
            self.post(lambda: self.vp_log("host action ignored: game mode (the program in front has a game profile)"))
        elif evt == "host":
            self.host_q.put((("host", {"op": m.get("op"), "arg": m.get("arg", "")}), "pad action", f"{m.get('op')}", False))
        elif evt == "reminder":                                            # the pad's reminder fired: also tell the user on the computer
            self.post(lambda t=str(m.get("text", "")): self.notify("Reminder", t, "ok"))
        elif evt == "key" and m.get("v") == 1 and 1 <= m.get("k", 0) <= 5:
            self.post(lambda k=m["k"]: self._count_use(f"K{k}"))
        elif evt == "enc" and m.get("d"):
            self.post(lambda d=m["d"]: self._count_use("dial+" if d > 0 else "dial-"))

    def _count_use(self, key):
        u = self.cfg["usage"]
        u[key] = int(u.get(key, 0)) + 1
        self._usage_dirty = True

    def reset_usage(self):
        self.cfg["usage"].clear()
        save_config(self.cfg)
        self.emit("usage")

    def _pad_layer_changed(self, n):
        st = self._profile_state
        if self.cfg.get("remember_layers") and st.get("win") and not st.get("rule") and st.get("layer") != n:
            self._app_layers[st["win"][0]] = n                               # the user chose this layer in a program that has no rule
        self.pad_layer = n
        self.emit("pad_layer", n)

    # ------------------------------------------------------------------ screen dimming / resync
    def _screen_loop(self):
        while not self.closing:
            time.sleep(3)
            try:
                self._screen_step()
            except Exception:                                    # noqa: BLE001
                traceback.print_exc()

    def _screen_step(self, now=None):
        d, now = self._dim, now if now is not None else time.monotonic()
        gap, d["tick"] = now - d["tick"], now
        if not self.dev.connected or self.dev.busy or self.dev.info.get("core_only"):
            d["applied"] = None
            return
        if gap > 25:                                             # the loop did not run for a while: the PC slept - send the clock and the display state again
            self._resync_pad()
        want = None
        if self.cfg.get("dim_lock") and self.screen.locked():
            want = int(self.cfg.get("dim_level", 25))
        if want is None and self.cfg.get("dim_fullscreen") and self.screen.fullscreen():
            want = int(self.cfg.get("dim_level", 25))
        if want == d["applied"]:
            return
        d["applied"] = want
        if self._pad_cap("dimcmd"):
            self.dev.request({"cmd": "dim", "level": want if want is not None else 0})
        elif want is not None:                                   # older firmware: use (and later restore) the saved brightness
            d["saved"] = d["saved"] or int(self.pad.brightness)
            self.dev.request({"cmd": "brightness", "val": want})
        elif d["saved"]:
            self.dev.request({"cmd": "brightness", "val": d["saved"]})
            d["saved"] = None

    def _resync_pad(self):
        for msg in (time_msg(), {"cmd": "brightness", "val": int(self.pad.brightness)}, {"cmd": "mode", "val": int(self.pad.mode)}):
            try:
                self.dev.request(msg, timeout=2)
            except DeviceError:
                break
        self._info_sent = (None, 0.0)

    def _clip_loop(self):
        """Clipboard history (opt-in): remember the last copied texts, in memory only."""
        while not self.closing:
            time.sleep(1.5)
            if not self.cfg.get("cliphist_on"):
                if self.cliphist.items:
                    self.cliphist.clear()
                continue
            try:
                self.cliphist.add(self._clipboard_text())
            except Exception:                                  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ misc helpers
    def _pad_cap(self, cap):
        return bool(self.dev.connected and cap in (self.dev.info.get("caps") or []))

    def _core_only(self, what="that"):
        if self.dev.connected and self.dev.info.get("core_only"):
            self.set_status(f"The pad runs the CoreBringup diagnostic sketch, which cannot do {what}. Flash the full firmware first (Pad & App -> Firmware).", error=True)
            return True
        return False

    def _layers_supported(self):
        return int(self.dev.info.get("layers", 1) or 1) >= LAYERS if self.dev.connected else True

    def host_cfg(self):
        from core.base import HOST_OS
        return dict(self.cfg, os=HOST_OS)             # tests act on THIS computer, so resolve variants for its OS

    def twin_step(self, render=True):
        """One 40 ms tick of the virtual pad. Returns the rendered 240x240 image when it changed (else None). render=False only advances the model."""
        try:
            need = self.pad.tick()
            if need and not render:
                self.pad.dirty = True                           # draw it as soon as something is looking
                return None
            return self.pad.render() if need else None
        except Exception:                                      # noqa: BLE001
            traceback.print_exc()
            return None
