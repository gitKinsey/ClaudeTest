"""Small diagnostics: latency statistics and a rough USB power estimate. Pure functions."""


def latency_summary(ms):
    """List of round-trip times in ms -> 'n=20  min 1.2  avg 2.0  p95 3.4  max 4.1 ms  jitter 0.6'."""
    xs = sorted(float(x) for x in ms)
    if not xs:
        return "no samples"
    avg = sum(xs) / len(xs)
    p95 = xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]
    jit = (sum((x - avg) ** 2 for x in xs) / len(xs)) ** 0.5
    return f"n={len(xs)}  min {xs[0]:.1f}  avg {avg:.1f}  p95 {p95:.1f}  max {xs[-1]:.1f} ms  jitter {jit:.1f}"


SECRET_KEYS = ("key", "token", "password", "pass", "secret", "ssid")


def _secret(name):
    return isinstance(name, str) and any(w in name.lower() for w in SECRET_KEYS)


def redact(obj, _secret_parent=False, _depth=0):
    """A copy of the settings with every secret (API keys, tokens, passwords, anything inside a 'keys' / 'token' group) replaced by '***' - safe to attach to a bug report."""
    if _depth > 8:
        return "..."
    if isinstance(obj, dict):
        return {k: ("***" if (_secret(k) or _secret_parent) and not isinstance(v, (dict, list)) and v not in (None, "", False, True, 0) else redact(v, _secret(k) or _secret_parent, _depth + 1))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, _secret_parent, _depth + 1) for v in obj[:200]]
    if isinstance(obj, str) and len(obj) > 4000:
        return obj[:4000] + " ...(cut)"
    return obj


def build_zip(files):
    """{name: text} -> bytes of a .zip (names are flat, no paths)."""
    import io                                                  # noqa: PLC0415
    import zipfile                                             # noqa: PLC0415
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name.replace("/", "_").replace("\\", "_"), str(text))
    return buf.getvalue()


def power_estimate(brightness=200, led_mode="auto", wifi=False, screen_on=True):
    """Rough USB current draw (mA) of the pad: ESP32-S3 core + backlight + the one RGB LED. An estimate, not a measurement."""
    ma = 45.0                                                    # ESP32-S3 running USB + display driver
    if wifi:
        ma += 70.0
    if screen_on:
        ma += 5.0 + 55.0 * max(0, min(255, int(brightness))) / 255.0      # GC9A01 panel + backlight
    if led_mode != "off":
        ma += 12.0                                               # one WS2812 at the pad's capped brightness
    return round(ma)


def power_text(ma):
    return f"about {ma} mA from USB ({ma * 5 / 1000:.2f} W) - well inside the 500 mA a USB 2 port provides"
