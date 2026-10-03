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
