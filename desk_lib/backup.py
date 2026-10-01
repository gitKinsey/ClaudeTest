"""Backup / restore of everything that makes a pad yours: app config, key maps of all layers, GIF library."""
import io
import json
import re
import time
import zipfile

SAFE_GIF = re.compile(r"^gifs/[A-Za-z0-9 _.\-]{1,80}\.gif$")
MAX_TOTAL = 40_000_000


def make_backup(cfg, pad=None, gif_files=(), app_version=""):
    """-> bytes of a zip.  pad: {"layers": [[spec x7] x3], "bright":.., "mode":.., "os":.., "layout":..} or None."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({"format": 1, "app": app_version, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                "has_pad": bool(pad), "gifs": len(gif_files)}, indent=1))
        z.writestr("config.json", json.dumps(cfg, indent=1))
        if pad:
            z.writestr("pad.json", json.dumps(pad, indent=1))
        for p in gif_files:
            z.write(p, f"gifs/{p.name}")
    return buf.getvalue()


def read_backup(data):
    """-> (manifest, cfg, pad_or_None, {gif_name: bytes}).  Refuses anything that is not what make_backup writes
    (no path tricks, size-limited), so a hostile zip cannot write outside the GIF library."""
    z = zipfile.ZipFile(io.BytesIO(data))
    total = sum(i.file_size for i in z.infolist())
    if total > MAX_TOTAL:
        raise ValueError("backup is unreasonably large")
    names = z.namelist()
    if "manifest.json" not in names or "config.json" not in names:
        raise ValueError("this is not a Desk Companion backup")
    manifest = json.loads(z.read("manifest.json"))
    if manifest.get("format") != 1:
        raise ValueError("unsupported backup format")
    cfg = json.loads(z.read("config.json"))
    if not isinstance(cfg, dict):
        raise ValueError("damaged config in backup")
    pad = json.loads(z.read("pad.json")) if "pad.json" in names else None
    gifs = {}
    for n in names:
        if n.startswith("gifs/") and not n.endswith("/"):
            if not SAFE_GIF.match(n):
                continue                                     # silently skip anything with an odd name
            body = z.read(n)
            if body[:3] == b"GIF":
                gifs[n[5:]] = body
    return manifest, cfg, pad, gifs
