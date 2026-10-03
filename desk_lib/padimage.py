"""Pictures for the pad's round screen made on the PC: the cover of the song that is playing, or a QR code.
They are uploaded like any GIF (one frame) into a spare GIF slot. Pure helpers: PIL only; `qrcode` is optional (pip install qrcode)."""
import io
import platform
import subprocess
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw

SIZE = 240


def _sh(args, timeout=3):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def art_url(runner=None, system=None):
    """URL of the cover art of the current song, or None. Linux: playerctl (MPRIS); macOS: Spotify."""
    run, system = runner or _sh, system or platform.system()
    if system == "Linux":
        out = run(["playerctl", "metadata", "mpris:artUrl"])
    elif system == "Darwin":
        out = run(["osascript", "-e", 'if application "Spotify" is running then tell application "Spotify" to return artwork url of current track'])
    else:
        raise ValueError("album art is available on Linux (playerctl) and macOS (Spotify) only")
    out = (out or "").strip()
    if not out:
        raise ValueError("no cover art is available for what is playing (nothing playing, or the player does not publish it)")
    return out


def fetch_image(url, opener=None):
    """http(s) or file:// URL -> PIL image. Capped at 6 MB."""
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https", "file"):
        raise ValueError("unsupported cover art address")
    if opener is None:
        def opener(u):
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "DeskCompanion"}), timeout=8) as r:       # noqa: S310
                return r.read(6_000_000)
    data = opener(url)
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:                                                  # noqa: BLE001
        raise ValueError("the cover art could not be read as a picture") from None


def square(img, size=SIZE):
    """Centre-crop to a square and scale to size x size."""
    w, h = img.size
    s = min(w, h)
    img = img.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    return img.resize((size, size), Image.LANCZOS)


def to_gif(img, colors=128):
    """One-frame GIF bytes (what the pad's GIF player takes)."""
    buf = io.BytesIO()
    img.convert("P", palette=Image.ADAPTIVE, colors=colors).save(buf, format="GIF")
    return buf.getvalue()


def qr_image(text, size=SIZE):
    """A QR code for text, centred on a white round-screen-safe canvas (quiet zone included)."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("nothing to encode (the text / clipboard is empty)")
    if len(text) > 300:
        raise ValueError("too long for a QR code on this screen (300 characters at most)")
    try:
        import qrcode                                               # noqa: PLC0415
    except ImportError:
        raise ValueError("QR codes need:  pip install qrcode") from None
    q = qrcode.QRCode(border=1, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
    q.add_data(text)
    q.make(fit=True)
    modules = q.get_matrix()
    n = len(modules)
    inner = int(size * 0.70)                                         # the round screen cuts the corners: keep the code inside the inscribed square
    cell = max(1, inner // n)
    side = cell * n
    canvas = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(canvas)
    ox = oy = (size - side) // 2
    for y, row in enumerate(modules):
        for x, on in enumerate(row):
            if on:
                d.rectangle((ox + x * cell, oy + y * cell, ox + (x + 1) * cell - 1, oy + (y + 1) * cell - 1), fill="black")
    return canvas
