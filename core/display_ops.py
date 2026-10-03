"""Engine part 3: everything for the pad's display - GIF library and upload, the Info screen, the pad's own settings, screens, reminders, habits."""
import io
import json
import os
import platform
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image

from core.base import (
    GIF_PRESETS, GIF_SLOTS, M_GIF, M_INFO, ONLINE_PROVIDERS, DISP, VirtualPad, DeviceError,
    _http_get, build_preset, fit_gif, gif_file_thumb, gif_lib_dir, lib_add, lib_delete, lib_list, load_gif_frames, online_download, online_search,
    preset_thumb, round_thumb, save_config,
)
from datetime import datetime
from desk_lib import extras, feeds, padconst


class DisplayOps:
    GIF_TILE = 56
    ROT_CHOICES = [("off", 0), ("5 seconds", 5), ("10 seconds", 10), ("30 seconds", 30), ("1 minute", 60), ("5 minutes", 300)]
    EXTRA_HINTS = {"countdown": "date: 2026-12-24", "worldclock": "Europe/Zurich, Asia/Tokyo", "git": "folder of the repository",
                   "ci": "owner/name (public repository)", "crypto": "bitcoin  or  ethereum:eur",
                   "quote": "(empty)  or a text file of  quote | author  lines", "birthday": "Anna 03-14, Max 1990-07-02",
                   "ping": "example.com  or  192.168.1.1:22", "http": "https://example.com", "lyrics": "(nothing to enter)",
                   "progress": "year, month, week, day or work", "moon": "(nothing to enter)", "sun": "47.37, 8.54  (empty = your weather city)", "network": "(nothing to enter)",
                   "goal": "water:8  (a {counter:water} counter and its target)", "rain": "(uses your weather city)", "window": "(nothing to enter)", "battery": "(nothing to enter)",
                   "disk": "folder or drive, e.g. C:\\  or  /", "load": "(nothing to enter)"}

    def init_display_state(self):
        self.gif_frames, self.gif_durs, self.gif_data = None, [], None
        self.gif_slot, self.keep_copy, self.dither = 0, True, False
        self.pad_gifs, self.pad_gif_free = {}, None
        self._mine_gen = 0
        self.online_gen, self.online_loaded = 0, False
        self.info_preview = VirtualPad(lambda s: None, lambda n: None, lambda k: None)
        self.info_preview.mode = M_INFO
        self.info_last = ([], [], {}, False)
        self.pad_settings_cache = {}
        self.pad_settings_busy = False                          # True while the controls are being filled from the pad (no echo back)
        self.screens_cache = None

    # ------------------------------------------------------------------ GIF processing
    def gif_source_ready(self, label):
        self.gif_data = None
        free = self.dev.info.get("fs_free") if self.dev.connected else None
        if self.dev.connected and self.pad_gif_free is not None:
            free = self.pad_gif_free + self.pad_gifs.get(self.gif_slot, 0)      # the upload replaces whatever is in its own slot
        self.emit("gif_busy", label)
        return min(max((free or 1_000_000) - 16384, 50_000), 1_400_000), self.dither

    def use_gif_file(self, src, label, save=False):
        """src: path | bytes | callable returning either (run on the worker thread, e.g. a download)."""
        limit, dither = self.gif_source_ready(label)

        def work():
            s = src() if callable(src) else src
            frames, durs = load_gif_frames(io.BytesIO(bytes(s)) if isinstance(s, (bytes, bytearray)) else s)
            data, colors, n = fit_gif(frames, durs, limit, dither)
            saved = None
            if save:
                inside = not isinstance(s, (bytes, bytearray)) and Path(s).resolve().parent == gif_lib_dir().resolve()
                if not inside:
                    saved = lib_add(s, label if isinstance(s, (bytes, bytearray)) else None)
            return (frames, durs, data, colors, n, limit), saved

        def ok(res):
            self._gif_ready(res[0])
            if res[1]:
                self.set_status(f"Saved a copy as {res[1].name} in My GIFs")
                self.refresh_my_gifs()
        self.bg(work, ok, "GIF processing failed", fail=lambda: self.emit("gif_failed"))

    def use_preset(self, name):
        limit, dither = self.gif_source_ready(f"built-in: {name}")

        def work():
            frames, durs = build_preset(name)
            data, colors, n = fit_gif(frames, durs, limit, dither)
            return frames, durs, data, colors, n, limit
        self.bg(work, self._gif_ready, "Preset processing failed", fail=lambda: self.emit("gif_failed"))

    def _gif_ready(self, res):
        self.gif_frames, self.gif_durs, self.gif_data, colors, n, limit = res
        text = (f"{len(self.gif_frames)} source frames -> {n} frames, {colors} colours, "
                f"{len(self.gif_data) / 1024:.0f} KB (limit {limit // 1024} KB).")
        self.pad.set_gif(self.gif_frames, self.gif_durs)       # the virtual screen can show it before uploading
        self.emit("gif_ready", text, self.gif_frames, self.gif_durs)

    def upload_gif(self):
        if not self.dev.connected:
            return self.set_status("Connect the pad first", error=True)
        data, slot = self.gif_data, self.gif_slot
        if data is None:
            return self.set_status("Pick a GIF first", error=True)
        if self._core_only("GIFs"):
            return
        if slot and int(self.dev.info.get("gifs", -1)) < 0:
            return self.set_status("This pad's firmware has a single GIF slot - update it (Pad & App -> Firmware) to use more", error=True)
        self.emit("gif_uploading", True)
        self.set_status(f"Uploading GIF to slot {slot + 1}...")

        def progress(f):
            self.post(lambda: (self.emit("gif_progress", f), setattr(self.pad, "upload_frac", f)))

        def finished():
            self.pad.upload_frac, self.pad.dirty = None, True
            self.emit("gif_uploading", False)

        def done(_):
            finished()
            self.emit("gif_progress", 1.0)
            self.pad.set_mode(M_GIF)
            self.set_status(f"GIF uploaded to slot {slot + 1} - the pad switched to GIF mode")
            self.refresh_pad_gifs()
        self.bg(lambda: self.dev.upload_gif(data, progress, slot), done, "Upload failed", fail=finished)

    def delete_gif(self, slot=None):
        slot = self.gif_slot if slot is None else slot
        self.bg(lambda: self.dev.request({"cmd": "gif_delete", **({"slot": slot} if slot else {})}),
                lambda _: (self.set_status(f"GIF slot {slot + 1} cleared" + (" - the built-in demo animation will be regenerated" if slot == 0 else "")),
                           self.refresh_pad_gifs()), "Delete failed")

    # ------------------------------------------------------------------ the pad's own GIF slots
    def refresh_pad_gifs(self):
        if not self.dev.connected:
            self.pad_gifs = {}
            return self._draw_pad_gifs({"slots": [], "cur": 0, "rot": 0, "max": 1})
        if self.dev.info.get("core_only"):
            self.pad_gifs, self.pad_gif_free = {}, None
            return self.emit("pad_gifs", "core", {})
        if "gifslots" not in (self.dev.info.get("caps") or []):         # firmware 1.1: one GIF, no listing command
            self.pad_gifs, self.pad_gif_free = {}, None
            return self.emit("pad_gifs", "old", {})
        self.bg(lambda: self.dev.request({"cmd": "gif_list"}), self._draw_pad_gifs, "Could not list the pad's GIFs")

    def _draw_pad_gifs(self, r):
        if "slots" not in r:
            r = {"slots": [], "cur": 0, "rot": 0, "max": 1}
        self.pad_gifs = {x["s"]: x["size"] for x in r["slots"]}
        self.pad_gif_free = r.get("fs_free")
        if not self.dev.connected:
            return self.emit("pad_gifs", "disconnected", r)
        self.emit("pad_gifs", "ok", r)

    def rot_label(self, secs):
        return min(self.ROT_CHOICES, key=lambda c: abs(c[1] - secs))[0]

    def pad_gif_show(self, slot):
        self.bg(lambda: self.dev.request({"cmd": "gif_cfg", "slot": slot}), lambda r: (self.pad.set_mode(M_GIF), self._draw_pad_gifs(r)), "Could not switch GIF")

    def gif_rotation(self, label):
        secs = dict(self.ROT_CHOICES)[label]
        if self.dev.connected:
            self.bg(lambda: self.dev.request({"cmd": "gif_cfg", "rot": secs}), self._draw_pad_gifs, "Could not set rotation")

    # ------------------------------------------------------------------ My GIFs
    def refresh_my_gifs(self):
        self._mine_gen += 1
        gen = self._mine_gen
        paths = lib_list()
        if not paths:
            return self.emit("mine_gifs", gen, str(gif_lib_dir()), [])

        def work():
            out = []
            for p in paths:
                try:
                    out.append((p, gif_file_thumb(p, self.GIF_TILE)))
                except Exception:                                  # noqa: BLE001  unreadable file: still listed, no picture
                    out.append((p, None))
            return out
        self.bg(work, lambda items: gen == self._mine_gen and self.emit("mine_gifs", gen, str(gif_lib_dir()), items), "Could not read the GIF folder")

    def delete_my_gif(self, path):
        if self.ui.confirm("Delete GIF", f"Remove '{path.name}' from your library?\n(The copy on the pad is not touched.)"):
            lib_delete(path)
            self.refresh_my_gifs()

    def add_my_gifs(self, paths):
        n = 0
        for p in paths:
            try:
                with open(p, "rb") as fh:
                    if fh.read(3) != b"GIF":
                        raise ValueError("not a GIF file")
                lib_add(p)
                n += 1
            except (OSError, ValueError) as e:
                self.set_status(f"Could not add {os.path.basename(p)}: {e}", error=True)
        if n:
            self.set_status(f"Added {n} GIF(s) to My GIFs")
        self.refresh_my_gifs()

    def open_gif_folder(self):
        d = gif_lib_dir()
        try:
            if platform.system() == "Windows":
                os.startfile(str(d))                                # noqa: S606 - local folder only
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(d)])
            else:
                subprocess.Popen(["xdg-open", str(d)])
        except Exception as e:                                     # noqa: BLE001
            self.set_status(f"Could not open the folder ({e}): {d}", error=True)

    @staticmethod
    def builtin_presets():
        return list(GIF_PRESETS)

    def builtin_thumb(self, name):
        return preset_thumb(name, self.GIF_TILE)

    # ------------------------------------------------------------------ Online GIFs
    def online_provider(self):
        p = self.cfg.get("online_provider")
        return p if p in ONLINE_PROVIDERS else "Tenor"

    def online_key(self, provider):
        return self.cfg.setdefault("online_keys", {}).get(provider, "")

    def online_key_help(self, provider):
        import webbrowser
        webbrowser.open(ONLINE_PROVIDERS[provider])

    def online_run(self, provider, key, query):
        key = (key or "").strip()
        self.cfg.setdefault("online_keys", {})[provider] = key
        self.cfg["online_provider"] = provider
        save_config(self.cfg)
        self.online_gen += 1
        gen = self.online_gen
        self.emit("online_status", "searching...", False)

        def fail():
            if gen == self.online_gen:
                self.emit("online_status", "search failed - see the log", True)
        self.bg(lambda: online_search(provider, key, query), lambda res: self._online_show(res, gen, query), "Online search failed", fail=fail)

    def _online_show(self, results, gen, query):
        if gen != self.online_gen:
            return
        self.online_loaded = True
        self.emit("online_results", gen, results)
        self.emit("online_status", f"{len(results)} result(s)" if results else "nothing found", False)

        def fetch():
            from concurrent.futures import ThreadPoolExecutor

            def one(i):
                if gen != self.online_gen or self.closing:
                    return
                try:
                    im = Image.open(io.BytesIO(_http_get(results[i]["thumb"], timeout=10, limit=2_000_000)))
                    im.seek(0)
                    th = round_thumb(im.copy(), self.GIF_TILE)
                except Exception:                                  # noqa: BLE001 - one bad thumbnail must not stop the rest
                    return
                self.emit("online_thumb", gen, i, th)
            with ThreadPoolExecutor(max_workers=6) as ex:
                list(ex.map(one, range(len(results))))
        threading.Thread(target=fetch, daemon=True).start()

    def use_online(self, item):
        self.use_gif_file(lambda: online_download(item["url"]), item["title"] or "online GIF", save=self.keep_copy)

    # ------------------------------------------------------------------ Info screen
    def info_update(self, save_only=False, **kw):
        """Store changed Info settings (switches, fahrenheit, ics, rotation, custom card fields)."""
        info = self.cfg["info"]
        info.update(kw)
        save_config(self.cfg)
        if not save_only:
            self._info_cache.pop("event_src", None)
            self._info_sent = (None, 0.0)

    def extra_add(self, kind_label, label, arg):
        key = next(k for k, v in extras.KINDS.items() if v == kind_label)
        try:
            item = extras.validate({"type": key, "label": label, "arg": arg})
            if len(self.cfg["info"]["extras"]) >= 4:
                raise ValueError("the pad shows at most 4 cards - remove one first")
        except ValueError as e:
            self.set_status(f"Cannot add the card: {e}", error=True)
            return False
        self.cfg["info"]["extras"].append(item)
        save_config(self.cfg)
        self._info_sent = (None, 0.0)
        self.emit("extras")
        self.set_status(f"Added: {extras.KINDS[key]}")
        return True

    def extra_remove(self, i):
        del self.cfg["info"]["extras"][i]
        save_config(self.cfg)
        self._info_sent = (None, 0.0)
        self.emit("extras")

    def extra_kind_key(self, kind_label):
        return next(k for k, v in extras.KINDS.items() if v == kind_label)

    def info_find_city(self, name):
        name = (name or "").strip()
        if not name:
            return self.set_status("Type a city first", error=True)

        def done(res):
            lat, lon, label = res
            self.cfg["info"].update(city=name, lat=lat, lon=lon, label=label, weather=True)
            save_config(self.cfg)
            self._info_sent = (None, 0.0)
            self.emit("info_cfg")
            self.set_status(f"Weather place set to {label}")
        self.bg(lambda: feeds.geocode(name), done, "City lookup failed")

    def info_copy_badge(self):
        self.ui.clipboard_set(self.badges.url() if self.badges else "")
        self.set_status("Badge URL copied - change name=mail and n=3 to whatever you need")

    def info_test_badge(self):
        if self.badges:
            self.badges.set("test", 3)
            self.set_status("Test badge set - it appears on the Info screen")

    def send_pad_image(self, kind, arg=""):
        self.bg(lambda: self.pad_image(kind, arg), self.set_status, "Could not send the picture")

    def pad_image(self, kind, arg=""):
        """Cover art of the playing song (kind 'art') or a QR code ('qr', arg or clipboard) -> a spare GIF slot of the pad, shown right away."""
        from desk_lib import padimage
        if not self.dev.connected:
            raise ValueError("the pad is not connected")
        if "gifslots" not in (self.dev.info.get("caps") or []):
            raise ValueError("this firmware has no extra GIF slots (update it)")
        if kind == "art":
            img = padimage.square(padimage.fetch_image(padimage.art_url()))
        else:
            img = padimage.qr_image(arg.strip() or self._clipboard_text())
        slot = max(1, min(GIF_SLOTS - 1, int(self.cfg.get("image_slot", 3))))
        self.dev.upload_gif(padimage.to_gif(img), None, slot)
        self.dev.request({"cmd": "gif_cfg", "slot": slot})
        self.post(lambda: (self.pad.set_mode(M_GIF), self.refresh_pad_gifs()))
        return f"{'cover art' if kind == 'art' else 'QR code'} sent to GIF slot {slot + 1}"

    def info_show_on_pad(self):
        self.pad.set_mode(M_INFO)
        if self.dev.connected:
            self.bg(lambda: self.dev.request({"cmd": "mode", "val": M_INFO}), None, "Mode switch failed")

    def info_preview_image(self):
        return self.info_preview.render()

    def _info_collect(self):
        """Gather the cards for the pad. Runs on a worker thread. -> (cards, badges, {source: error text})"""
        info, now, cards, errs = self.cfg["info"], time.time(), [], {}
        cache, snap = self._info_cache, {}                       # snap: what the LED alerts compare between rounds

        def cached(key, ttl, fn):
            hit = cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
            val = fn()
            cache[key] = (now, val)
            return val
        if info.get("music"):
            try:
                np = feeds.now_playing()
                if np:
                    cards.append(feeds.music_card(np))
                errs["music"] = "" if np else ("nothing is playing" if feeds.have_player_tool() else "install 'playerctl' to read what is playing")
            except Exception as e:                           # noqa: BLE001
                errs["music"] = str(e)
        if info.get("event") and info.get("ics"):
            try:
                text = cached("event_src", 300, lambda: feeds.load_calendar(info["ics"]))
                ev = feeds.next_event(feeds.parse_ics(text))
                if ev:
                    cards.append(feeds.event_card(ev))
                    if not ev[1] and 0 <= (ev[0] - datetime.now().astimezone()).total_seconds() <= 600:
                        snap["event"] = f"{ev[0].isoformat()} {ev[2]}"          # starts within 10 minutes
                errs["event"] = "" if ev else "no upcoming events found"
            except Exception as e:                           # noqa: BLE001
                errs["event"] = f"calendar problem: {e}"
        if info.get("weather") and info.get("lat") is not None:
            try:
                w = cached("weather", 600, lambda: feeds.weather_fetch(info["lat"], info["lon"]))
                cards.append(feeds.weather_card(info.get("label", ""), w, bool(info.get("fahrenheit"))))
                errs["weather"] = ""
            except Exception as e:                           # noqa: BLE001
                errs["weather"] = f"weather problem: {e}"
        if info.get("custom") and any(info.get(k) for k in ("c_label", "c_t", "c_a", "c_b")):
            f = feeds.ascii_fold
            cards.append({"k": info.get("c_k", "c") if info.get("c_k") in ("c", "r", "p", "s") else "c", "label": f(info.get("c_label", ""), 24) or "NOTE", "t": f(info.get("c_t", ""), 24), "a": f(info.get("c_a", ""), 40), "b": f(info.get("c_b", ""), 40)})
        for i, item in enumerate(info.get("extras") or []):
            key = f"x{i}"
            try:
                card = cached(key + json.dumps(item, sort_keys=True), extras.TTL[item["type"]], lambda it=item: extras.build(it, **self._extras_ctx()))
                cards.append(card)
                if item["type"] == "ci":
                    snap[key.replace("x", "ci")] = card.get("t", "").lower()
                errs[key] = ""
            except Exception as e:                           # noqa: BLE001
                errs[key] = f"{extras.KINDS.get(item.get('type'), 'card')}: {e}"
        badges = self.badges.get() if self.badges else []
        snap.update({f"badge:{b['name']}": int(b["n"]) for b in badges})
        self._alert_snap = snap
        if self.dev.connected and not self._pad_cap("cards2"):                    # ring / progress / scrolling cards need firmware 1.5: older pads get a plain card
            for c in cards:
                if c.get("k") in ("r", "p", "s"):
                    if c["k"] != "s" and str(c.get("t", "")).isdigit():
                        c["t"] = c["t"] + "%"
                    c["k"] = "c"
        return cards[:4], badges, errs

    def _extras_ctx(self):
        """What some Info cards need from the app: the weather place, the snippet counters, the program in front."""
        i = self.cfg["info"]
        return {"latlon": (i.get("lat"), i.get("lon")), "counter_values": self.cfg["counters"], "window": self.active_win.get()}

    def _led_alerts(self):
        """Compare the latest snapshot with the previous one; blink the LED for what is new (only when the switch is on)."""
        found = self.alerts.update(self._alert_snap)
        if found and self.cfg.get("led_alerts") and self.dev.connected and self._pad_cap("ledfx"):
            name, hex_, times = found[0]
            try:
                self.dev.request({"cmd": "led", "alert": hex_, "times": times}, timeout=2)
            except DeviceError:
                pass
        return found

    def info_send_now(self, force=False):
        def work():
            cards, badges, errs = self._info_collect()
            sig = json.dumps([cards, badges], sort_keys=True)
            last_sig, last_t = self._info_sent
            sent = False
            if self.dev.connected and not self.dev.busy and int(self.dev.info.get("modes", 5)) >= 6 and (force or sig != last_sig or time.time() - last_t > 60):
                self.dev.request({"cmd": "info_cards", "cards": cards, "badges": badges, "rot": int(self.cfg["info"].get("rot", 6))})
                self._info_sent, sent = (sig, time.time()), True
            return cards, badges, errs, sent
        self.bg(work, self._info_done, "Info update failed")

    def _info_done(self, res):
        cards, badges, errs, sent = res
        rot = int(self.cfg["info"].get("rot", 6))
        self.pad.set_info(cards, badges, rot)
        self.info_preview.set_info(cards, badges, rot)
        bad = [v for v in errs.values() if v and "nothing is playing" not in v]
        self.info_last = (cards, badges, errs, sent)
        self.emit("info_done", {
            "music": errs.get("music") or "playing: " + (cards[0]["t"] if cards and cards[0]["k"] == "m" else ""),
            "event": errs.get("event") or "",
            "badges": ("badges: " + ", ".join(f"{b['name']} {b['n']}" for b in badges)) if badges else "no badges set",
            "status": ("sent to the pad " + time.strftime("%H:%M:%S") if sent else "not sent (pad not connected or firmware without Info screen)") +
                      ("\n" + "\n".join(bad) if bad else "")})

    def _info_loop(self):
        while not self.closing:
            time.sleep(max(0.05, self.info_poll))
            info = self.cfg["info"]
            if not any(info.get(k) for k in ("music", "weather", "event", "custom")) and not info.get("extras") and not (self.badges and self.badges.get()):
                continue
            try:
                cards, badges, errs = self._info_collect()
                self._led_alerts()
            except Exception:                                # noqa: BLE001
                continue
            sig = json.dumps([cards, badges], sort_keys=True)
            last_sig, last_t = self._info_sent
            sent = False
            if self.dev.connected and not self.dev.busy and int(self.dev.info.get("modes", 5)) >= 6 and (sig != last_sig or time.time() - last_t > 60):
                try:
                    self.dev.request({"cmd": "info_cards", "cards": cards, "badges": badges, "rot": int(info.get("rot", 6))})
                    self._info_sent, sent = (sig, time.time()), True
                except DeviceError:
                    pass
            if sig != last_sig or sent:
                self.post(lambda r=(cards, badges, errs, sent): self._info_done(r))

    # ------------------------------------------------------------------ the pad's own settings (firmware 1.3 / 1.5)
    def pad_settings_ok(self):
        return padconst.has_cap(self, "dialaccel")

    def pad_settings_ok15(self):
        return padconst.has_cap(self, "themes")

    def pad_settings_note(self):
        if not self.dev.connected:
            return "Connect the pad to change these. They are stored on the pad."
        if not self.pad_settings_ok():
            return "This pad's firmware is older than 1.3 - update it (Pad & App -> Firmware) to get these settings."
        if not self.pad_settings_ok15():
            return "The look-and-feel options (themes, rotation, key names ...) need firmware 1.5 - update the pad (Pad & App -> Firmware)."
        return ("Night dimming needs the pad's clock to be set (the app does that on connect). Everything here is stored on the pad. "
                "Rotation 90 / 270 turns the picture on the pad; the screenshot and the twin always show it upright.")

    def pad_settings_refresh(self):
        self.emit("pad_settings_state")

    def pad_settings_on_connected(self):
        self.emit("pad_settings_state")
        if self.pad_settings_ok():
            self.bg(lambda: self.dev.request({"cmd": "settings"}), self._pad_settings_loaded, "Reading the pad's settings failed")

    def _pad_settings_loaded(self, st):
        self.pad_settings_cache = dict(st)
        self.emit("pad_settings", st)

    def pad_settings_apply(self, **fields):
        if self.pad_settings_busy or not self.pad_settings_ok():
            return
        self.bg(lambda: self.dev.request({"cmd": "settings", **fields}), lambda _r: self.set_status("Pad setting changed"), "Pad setting failed")

    # ------------------------------------------------------------------ screens, reminders, habits (firmware 1.4)
    def pad_has(self, cap):
        return padconst.has_cap(self, cap)

    def screens_ok(self):
        return padconst.has_cap(self, "screens")

    def screens_need(self):
        """True (after saying why) when the pad cannot do this."""
        if self.screens_ok():
            return False
        self.set_status("The pad needs firmware 1.4 for this (Pad & App -> Firmware).", error=True)
        return True

    def screens_note(self):
        return ("Connect the pad to change these." if not self.dev.connected else
                "" if self.screens_ok() else "This pad's firmware is older than 1.4 - update it (Pad & App -> Firmware) to get these screens and reminders.")

    def screens_on_connected(self):
        self.emit("screens_state")
        if self.screens_ok():
            self.bg(lambda: [self.dev.request({"cmd": "settings"}), self.dev.request({"cmd": "reminders"}), self.dev.request({"cmd": "habits"})],
                    self._screens_loaded, "Reading the pad's screens failed")

    def _screens_loaded(self, res):
        self.screens_cache = res
        self.emit("screens_loaded", *res)

    def screens_refresh(self):
        self.emit("screens_state")

    @staticmethod
    def habit_text(hb):
        if not hb.get("synced", True):
            return "The pad's clock is not set yet, so habits cannot be ticked (the app sets it when it connects)."
        return "Today: " + ", ".join(f"{n} {'done' if t else '-'} ({s} d)" for n, t, s in zip(hb["names"], hb["today"], hb["streak"]))

    def set_mode_mask(self, mask):
        """Returns the mask that was really stored (at least one screen stays on)."""
        if not self.screens_ok():
            return mask
        if not mask:
            self.set_status("At least one screen must stay in the cycle", error=True)
            mask = 1
        self.bg(lambda: self.dev.request({"cmd": "settings", "mode_mask": mask}), lambda _r: self.set_status("Screen cycle saved on the pad"), "Saving the screens failed")
        self.pad.mode_mask = mask
        return mask

    @staticmethod
    def reminders_validate(rows):
        """rows: [(minutes text, reminder text)] -> [{"m","t"}] or ValueError."""
        out = []
        for m, t in rows:
            m, t = m.strip(), t.strip()
            if not m or m == "0" or not t:
                out.append({"m": 0, "t": ""})
                continue
            if not m.isdigit() or not 1 <= int(m) <= 1440:
                raise ValueError("minutes must be a number from 1 to 1440 (or 0 / empty = off)")
            if len(t) > 16 or any(not 32 <= ord(c) < 127 or c in "|;" for c in t):
                raise ValueError("the reminder text is at most 16 plain characters (no | or ;)")
            out.append({"m": int(m), "t": t})
        return out

    def save_reminders(self, rows):
        if self.screens_need():
            return
        try:
            lst = self.reminders_validate(rows)
        except ValueError as e:
            return self.set_status(f"Reminders not saved: {e}", error=True)
        self.bg(lambda: self.dev.request({"cmd": "reminders", "list": lst}), lambda _r: self.set_status("Reminders saved on the pad"), "Saving reminders failed")

    def test_reminder(self, i):
        if self.screens_need():
            return
        self.bg(lambda: self.dev.request({"cmd": "reminders", "test": i}), None, "That reminder is empty - fill in and save it first")

    HABIT_DEFAULTS = ("Water", "Move", "Read", "Sleep", "Focus")

    def save_habits(self, names):
        if self.screens_need():
            return
        names = [n.strip() or d for n, d in zip(names, self.HABIT_DEFAULTS)]
        if any(len(n) > 10 or "|" in n or any(not 32 <= ord(c) < 127 for c in n) for n in names):
            return self.set_status("Habit names: at most 10 plain characters each (no |)", error=True)
        self.bg(lambda: self.dev.request({"cmd": "habits", "names": names}), lambda r: (self.emit("habits", self.habit_text(r)), self.set_status("Habit names saved on the pad")), "Saving habits failed")

    def load_habits(self):
        if self.screens_need():
            return
        self.bg(lambda: self.dev.request({"cmd": "habits"}), lambda r: self.emit("habits", self.habit_text(r)), "Reading habits failed")

    # ------------------------------------------------------------------ computer actions settings
    def save_ai(self, key, model):
        self.cfg["ai"]["key"] = (key or "").strip()
        self.cfg["ai"]["model"] = (model or "").strip() or "claude-haiku-4-5-20251001"
        self.save_cfg()
        self.set_status("AI settings saved")

    def save_shot(self, folder):
        self.cfg["shot_dir"] = (folder or "").strip()
        self.save_cfg()
        self.set_status("Screenshot folder saved")

    def shot_now(self, folder=None):
        if folder is not None:
            self.save_shot(folder)
        self.bg(lambda: self.run_host("shot", "default"), lambda m: self.set_status(m), "Screenshot failed")

    def hist_set(self, on):
        self.cfg["cliphist_on"] = bool(on)
        self.save_cfg()
        self.set_status("Clipboard history is on - copy something, then use the 'Type from clipboard history' action" if on else "Clipboard history is off and forgotten")

    def layouts_text(self):
        names = sorted(self.cfg["layouts"])
        return ("Saved: " + ", ".join(f"{n} ({len(self.cfg['layouts'][n])} windows)" for n in names) if names else
                "No layouts yet. Arrange your windows, give the layout a name and press 'Save the current windows'. Then use the 'Window layout' key action.")

    def _layout_name(self, name):
        n = (name or "").strip()
        if not n or len(n) > 30:
            self.set_status("Type a layout name (up to 30 characters)", error=True)
            return ""
        return n

    def layout_save(self, name):
        n = self._layout_name(name)
        if n:
            self.bg(lambda: self.run_host("layout", f"save {n}"), lambda m: (self.save_cfg(), self.emit("layouts"), self.set_status(m)), "Saving the layout failed")

    def layout_restore(self, name):
        n = self._layout_name(name)
        if n:
            self.bg(lambda: self.run_host("layout", n), lambda m: self.set_status(m), "Restoring the layout failed")

    def layout_delete(self, name):
        n = self._layout_name(name)
        if n and self.cfg["layouts"].pop(n, None) is not None:
            self.save_cfg()
            self.emit("layouts")
            self.set_status(f"Layout '{n}' deleted")

    # keep the module's imported-for-the-UI names referenced
    _DISP = DISP
