"""Headless test of the GIF library (built-in / My GIFs / Online) against a local mock Tenor + GIPHY server, plus host media sync."""
import io, json, os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as HTTPServer

tmp = tempfile.mkdtemp(prefix="dcgif_")
os.environ["DESK_COMPANION_CONFIG"] = os.path.join(tmp, "cfg.json")
os.environ["DESK_COMPANION_GIFS"] = os.path.join(tmp, "gifs")
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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import companion_app as m

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

# ---------- GUI
app = m.App(); app.geometry("1240x820+0+0"); app.update()
def pump(n=20, cond=None, t=0.05):
    for _ in range(n):
        app.update(); time.sleep(t)
        if cond and cond(): return True
    return cond is None
app.toggle_simulate(); assert pump(80, lambda: app.dev.connected)
app.tabs.set("GIF Upload"); pump(10)

# built-in
app.use_preset("Fire"); assert pump(100, lambda: app.gif_data is not None), "preset"
assert app.gif_name.cget("text") == "built-in: Fire"
# my gifs view lists the one library file
app._gif_show_view("My GIFs"); app.gif_view.set("My GIFs"); pump(30)
tiles = [w for w in app.mine_sc.winfo_children()]; assert len(tiles) == 1, len(tiles)
app.gif_data = None
tiles[0].invoke(); assert pump(100, lambda: app.gif_data is not None), "my gif use"
# own file with keep copy -> stored, then listed
own = os.path.join(tmp, "own.gif"); open(own, "wb").write(GIFS["/g/3.gif"])
app.gif_data = None; app.use_gif_file(own, "own.gif", save=True); assert pump(100, lambda: app.gif_data is not None)
pump(10); assert len(m.lib_list()) == 2, [x.name for x in m.lib_list()]
# using a library file with save=True must not duplicate it
lp = m.lib_list()[0]; app.gif_data = None; app.use_gif_file(str(lp), lp.name, save=True); assert pump(100, lambda: app.gif_data is not None); pump(10)
assert len(m.lib_list()) == 2
app.refresh_my_gifs(); pump(30); assert len(app.mine_sc.winfo_children()) == 2
# delete
app.delete_my_gif.__func__  # exists
import tkinter.messagebox as mb; mb.askyesno = lambda *a, **k: True
app.delete_my_gif(m.lib_list()[0]); pump(30); assert len(m.lib_list()) == 1 and len(app.mine_sc.winfo_children()) == 1

# online: no key -> error text, no crash
app._gif_show_view("Online"); app.gif_view.set("Online"); pump(5)
app.on_key.delete(0, "end"); app.online_run("cat"); pump(30)
assert "failed" in app.on_status.cget("text"), app.on_status.cget("text")
assert "API key" in app.status.cget("text"), app.status.cget("text")
# bad key
app.on_key.insert(0, "BAD"); app.online_run("cat"); pump(30)
assert "403" in app.status.cget("text") or "HTTP" in app.status.cget("text"), app.status.cget("text")
# good key, Tenor search
app.on_key.delete(0, "end"); app.on_key.insert(0, "GOOD"); app.on_query.insert(0, "cat")
app.online_run(app.on_query.get()); assert pump(80, lambda: len(app.on_sc.winfo_children()) == 5), len(app.on_sc.winfo_children())
assert "5 result" in app.on_status.cget("text")
assert pump(80, lambda: all(getattr(t, "_image", None) is not None or True for t in app.on_sc.winfo_children()))
assert json.load(open(os.environ["DESK_COMPANION_CONFIG"]))["online_keys"]["Tenor"] == "GOOD"
# thumbnails arrived
time.sleep(0.5); pump(20)
th = [t for t in app.on_sc.winfo_children() if t._image is not None]; assert len(th) == 5, len(th)
# click a result -> downloaded, processed, kept in My GIFs
n_before = len(m.lib_list()); app.gif_data = None
app.on_sc.winfo_children()[2].invoke(); assert pump(100, lambda: app.gif_data is not None), "online use"
pump(10); assert len(m.lib_list()) == n_before + 1
assert "tenor 2" in app.gif_name.cget("text")
# provider switch -> GIPHY, own key slot
app.on_provider.set("GIPHY"); app._online_provider_changed("GIPHY"); assert app.on_key.get() == ""
app.on_key.insert(0, "GK"); app.online_run(""); assert pump(80, lambda: len(app.on_sc.winfo_children()) == 5)
assert any("/giphy/gifs/trending" in h for h in HITS)
# stale search never overwrites a newer one
app.online_run("a"); app.online_run("b"); pump(60); assert len(app.on_sc.winfo_children()) == 5
time.sleep(1.0); pump(20)
th = [t for t in app.on_sc.winfo_children() if t._image is not None]; assert len(th) == 5, ("thumbs after stale searches", len(th))
# upload to the simulator, verify bytes
app.upload_gif(); assert pump(200, lambda: app.dev.ser.sim.gif_present and app.dev.ser.sim.gif_bytes_used == len(app.gif_data)), "upload"
print("GUI library OK")
if os.environ.get("SHOTS"):
    from PIL import ImageGrab
    for v in ("Online", "My GIFs", "Built-in"):
        app._gif_show_view(v); app.gif_view.set(v); pump(30)
        ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).save(f"/tmp/shot_lib_{v.replace(' ','_')}.png")

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
app.destroy()
print("ALL GIFLIB TESTS PASSED")
