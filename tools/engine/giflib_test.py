"""Engine test of the GIF library (built-in / My GIFs / Online) against a local mock Tenor + GIPHY server, plus host media sync."""
import io, json, os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kit import Kit, isolate    # noqa: E402
tmp = isolate("dcgif_")
from PIL import Image

def mk_gif(color, n=4):
    ims = [Image.new("RGB", (80, 60), tuple((c + i * 40) % 256 for c in color)) for i in range(n)]
    b = io.BytesIO(); ims[0].save(b, "GIF", save_all=True, append_images=ims[1:], duration=80, loop=0); return b.getvalue()

GIFS = {f"/g/{i}.gif": mk_gif((i * 50 % 256, 120, 200 - i * 20)) for i in range(5)}
HITS = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        HITS.append(self.path)
        base = f"http://127.0.0.1:{self.server.server_port}"
        if self.path.startswith("/g/"):
            self.send_response(200); self.end_headers(); self.wfile.write(GIFS[self.path]); return
        if self.path.startswith("/notgif"):
            self.send_response(200); self.end_headers(); self.wfile.write(b"<html>nope</html>"); return
        if "key=BAD" in self.path or "api_key=BAD" in self.path:
            self.send_response(403); self.end_headers(); self.wfile.write(b"{}"); return
        if self.path.startswith("/tenor/"):
            res = [{"content_description": f"tenor {i}", "media_formats": {"tinygif": {"url": f"{base}/g/{i}.gif"}, "mediumgif": {"url": f"{base}/g/{i}.gif"}}} for i in range(5)]
            body = {"results": res}
        else:
            body = {"data": [{"title": f"giphy {i}", "images": {"fixed_height": {"url": f"{base}/g/{i}.gif"}, "fixed_height_small": {"url": f"{base}/g/{i}.gif"}}} for i in range(5)]}
        d = json.dumps(body).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(d)
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
os.environ["DESK_COMPANION_TENOR_BASE"] = base + "/tenor"
os.environ["DESK_COMPANION_GIPHY_BASE"] = base + "/giphy"

import core.base as m    # noqa: E402

# ---------- pure functions
r = m.online_search("Tenor", "K", "cat"); assert len(r) == 5 and r[0]["url"].endswith(".gif"), r
r2 = m.online_search("GIPHY", "K", ""); assert len(r2) == 5 and r2[0]["title"] == "giphy 0"
assert any("/tenor/search" in h and "q=cat" in h for h in HITS) and any("/giphy/gifs/trending" in h for h in HITS)
try: m.online_search("Tenor", "BAD", "x"); raise SystemExit("bad key not rejected")
except ValueError as e: assert "403" in str(e) and "key" in str(e), e
try: m.online_search("Tenor", "", "x"); raise SystemExit("empty key not rejected")
except ValueError as e: assert "API key" in str(e)
try: m._http_get("http://127.0.0.1:1/x", timeout=2); raise SystemExit("no connection not reported")
except ValueError as e: assert "no connection" in str(e), e
try: m.online_download(base + "/notgif"); raise SystemExit("non-gif accepted")
except ValueError as e: assert "not a GIF" in str(e)
assert m.online_download(r[0]["url"])[:3] == b"GIF"
p = m.lib_add(GIFS["/g/0.gif"], "My Cat!"); p2 = m.lib_add(GIFS["/g/1.gif"], "My Cat!")
assert p.name == "My Cat.gif" and p2.name == "My Cat-2.gif", (p.name, p2.name)
assert len(m.lib_list()) == 2 and m.gif_file_thumb(p).size == (56, 56)
assert m.lib_delete(p) and not m.lib_delete(p) and len(m.lib_list()) == 1
for n in m.GIF_PRESETS: assert m.preset_thumb(n).size == (56, 56), n
assert len(m.GIF_PRESETS) == 16
print("pure functions OK")

# ---------- host media parsing with a fake shell
canned = {
    ("pactl", "get-sink-volume"): "Volume: front-left: 42000 /  64% / -11.99 dB,   front-right: 42000 /  64% / -11.99 dB\n",
    ("pactl", "get-sink-mute"): "Mute: yes\n",
    ("pactl", "list"): "0\talsa_output\tmodule-alsa-card.c\ts16le 2ch 44100Hz\tRUNNING\n",
}
def fake(args):
    for k, v in canned.items():
        if tuple(args[:2]) == k: return v
    if args[0] == "playerctl": return "Paused\n"
    return None
hm = m.HostMedia(runner=fake, system="Linux"); hm.kind = "pactl"
st = hm.state(); assert st == {"vol": 64, "muted": True, "playing": True}, st
hm2 = m.HostMedia(runner=lambda a: "[37%] [off]\n" if a[0] == "amixer" else None, system="Linux"); hm2.kind = "amixer"
assert hm2.state()["vol"] == 37 and hm2.state()["muted"] is True
hm3 = m.HostMedia(runner=lambda a: {"output volume": "55\n", "output muted": "false\n"}[a[2].split(" of")[0]] if a[0] == "osascript" else None, system="Darwin"); hm3.kind = "osascript"
assert hm3.state() == {"vol": 55, "muted": False, "playing": None}, hm3.state()
hm4 = m.HostMedia(runner=lambda a: (_ for _ in ()).throw(RuntimeError("boom")), system="Linux"); hm4.kind = "pactl"
assert hm4.state() == {"vol": None, "muted": None, "playing": None}      # exceptions swallowed
print("host media parsing OK")

# ---------- engine: library views, online search, upload, the pad's slots
k = Kit("dcgif2_", fresh=False)
os.environ.pop("X", None)
app, pump = k.e, k.pump
ev = {"busy": [], "mine": None, "results": None, "thumbs": 0, "status": [], "gifs": None}
app.on("gif_busy", lambda lbl: ev["busy"].append(lbl))
app.on("mine_gifs", lambda gen, folder, items: ev.__setitem__("mine", items))
app.on("online_results", lambda gen, res: ev.__setitem__("results", res))
app.on("online_thumb", lambda gen, i, th: ev.__setitem__("thumbs", ev["thumbs"] + (1 if gen == app.online_gen else 0)))
app.on("online_status", lambda t, err: ev["status"].append((t, err)))
app.on("pad_gifs", lambda st, r: ev.__setitem__("gifs", (st, r)))

app.use_preset("Fire"); assert pump(100, lambda: app.gif_data is not None), "preset"
assert ev["busy"][-1] == "built-in: Fire"
app.refresh_my_gifs(); assert pump(60, lambda: ev["mine"] is not None and len(ev["mine"]) == 1), ev["mine"]
app.gif_data = None
app.use_gif_file(str(ev["mine"][0][0]), "x.gif", save=False); assert pump(100, lambda: app.gif_data is not None), "my gif use"
own = os.path.join(tmp, "own.gif"); open(own, "wb").write(GIFS["/g/3.gif"])
app.gif_data = None; app.use_gif_file(own, "own.gif", save=True); assert pump(100, lambda: app.gif_data is not None)
pump(10); assert len(m.lib_list()) == 2, [x.name for x in m.lib_list()]
lp = m.lib_list()[0]; app.gif_data = None; app.use_gif_file(str(lp), lp.name, save=True); assert pump(100, lambda: app.gif_data is not None); pump(10)
assert len(m.lib_list()) == 2, "using a library file with save=True must not duplicate it"
app.delete_my_gif(m.lib_list()[0]); pump(30); assert len(m.lib_list()) == 1
app.add_my_gifs([own, __file__]); pump(30)
assert len(m.lib_list()) == 2 and any("Could not add" in l for l in app.log_lines), app.log_lines

# online: no key -> error, no crash
app.online_run("Tenor", "", "cat"); pump(30)
assert ev["status"][-1] == ("search failed - see the log", True) and "API key" in k.status, (ev["status"], k.status)
app.online_run("Tenor", "BAD", "cat"); pump(30)
assert "403" in k.status or "HTTP" in k.status, k.status
app.online_run("Tenor", "GOOD", "cat"); assert pump(80, lambda: ev["results"] and len(ev["results"]) == 5)
assert ev["status"][-1][0] == "5 result(s)"
assert json.load(open(os.environ["DESK_COMPANION_CONFIG"]))["online_keys"]["Tenor"] == "GOOD"
assert pump(60, lambda: ev["thumbs"] == 5), ev["thumbs"]
n_before = len(m.lib_list()); app.gif_data = None; app.keep_copy = True
app.use_online(ev["results"][2]); assert pump(100, lambda: app.gif_data is not None), "online use"
pump(10); assert len(m.lib_list()) == n_before + 1 and ev["busy"][-1] == "tenor 2", ev["busy"]
assert app.online_key("GIPHY") == "" and app.online_provider() == "Tenor"
app.online_run("GIPHY", "GK", ""); assert pump(80, lambda: ev["results"] and ev["results"][0]["title"] == "giphy 0")
assert app.online_provider() == "GIPHY" and any("/giphy/gifs/trending" in h for h in HITS)
ev["thumbs"] = 0
app.online_run("GIPHY", "GK", "a"); app.online_run("GIPHY", "GK", "b"); pump(60)
time.sleep(1.0); pump(20)
assert ev["thumbs"] == 5, ("stale searches must not deliver thumbnails", ev["thumbs"])

# upload to the simulator, verify bytes, then the slots
sim = k.sim
app.gif_slot = 0
app.upload_gif(); assert pump(200, lambda: sim.gif_present and sim.gif_bytes_used == len(app.gif_data)), "upload"
app.gif_slot = 2; n0 = len(app.gif_data)
app.upload_gif(); assert pump(200, lambda: 2 in sim.gifs and sim.gifs[2] == n0), sim.gifs
assert pump(40, lambda: set(app.pad_gifs) == {0, 2}), app.pad_gifs
assert ev["gifs"][0] == "ok" and int(ev["gifs"][1]["max"]) == 4
app.gif_rotation("10 seconds"); assert pump(40, lambda: sim.gif_rot == 10) and app.rot_label(10) == "10 seconds"
app.pad_gif_show(0); assert pump(40, lambda: sim.gif_cur == 0)
app.delete_gif(2); assert pump(40, lambda: 2 not in sim.gifs) and pump(40, lambda: set(app.pad_gifs) == {0})
app.gif_slot = 0
print("engine library OK")

# ---------- host media sync into the simulator
app.hostmedia = hm; app.cfg["host_media_sync"] = True
seen = []
orig = app.dev.send
def spy(msg, *a, **k):
    if msg.get("cmd") == "media": seen.append(dict(msg))
    return orig(msg, *a, **k)
app.dev.send = spy
t0 = time.time()
while not seen and time.time() - t0 < 6: pump(2)
assert seen and seen[0] == {"cmd": "media", "vol": 64, "muted": True, "playing": True}, seen
pump(5)
assert app.pad.vol == 64 and app.pad.muted is True and app.pad.playing is True
canned[("pactl", "get-sink-volume")] = "Volume: front-left: 1 /  10% / x dB\n"
n = len(seen); t0 = time.time()
while len(seen) == n and time.time() - t0 < 6: pump(2)
assert seen[-1]["vol"] == 10, seen[-1]
print("host media sync OK")
k.close()
print("ALL ENGINE GIFLIB TESTS PASSED")
