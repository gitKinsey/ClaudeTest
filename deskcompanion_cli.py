#!/usr/bin/env python3
"""Command line for the Desk Companion pad: talks to the running app's local API (switch it on: Automation page).

    python deskcompanion_cli.py status
    python deskcompanion_cli.py layer 2|next|prev          python deskcompanion_cli.py mode 1..6
    python deskcompanion_cli.py brightness 5..255          python deskcompanion_cli.py led auto|off|ff8800
    python deskcompanion_cli.py card "Label" "Title" "Line A" "Line B"      (no arguments after 'card' clears it)
    python deskcompanion_cli.py badge mail 3               python deskcompanion_cli.py press 1..7
    python deskcompanion_cli.py notify "Build finished"

The port and token come from the app's settings file (~/.desk_companion.json) or from the environment:
DESK_COMPANION_API=PORT:TOKEN.  Exit code 0 = done, 1 = the app / pad refused, 2 = usage or connection problem. Standard library only."""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_endpoint(env=None, cfg_path=None):
    env = os.environ if env is None else env
    if env.get("DESK_COMPANION_API"):
        port, _, token = env["DESK_COMPANION_API"].partition(":")
        if not port.isdigit() or not token:
            raise SystemExit("DESK_COMPANION_API must look like PORT:TOKEN")
        return int(port), token
    path = Path(cfg_path or env.get("DESK_COMPANION_CONFIG") or Path.home() / ".desk_companion.json")
    try:
        api = json.loads(path.read_text(encoding="utf-8")).get("api") or {}
        return int(api["port"]), str(api["token"])
    except (OSError, ValueError, KeyError):
        raise SystemExit(f"cannot read the API settings from {path} - open the app, Automation page, switch on 'Local API'") from None


def build_request(argv):
    """argv (without the program name) -> (method, path, body). Raises ValueError with a usage hint."""
    if not argv:
        raise ValueError("no command")
    c, a = argv[0], argv[1:]

    def num(x, what):
        try:
            return int(x)
        except ValueError:
            raise ValueError(f"{what} must be a number") from None
    if c == "status":
        return "GET", "/v1/status", None
    if c == "layer" and len(a) == 1:
        return "POST", "/v1/layer", {"n": a[0] if a[0] in ("next", "prev") else num(a[0], "layer")}
    if c in ("mode", "brightness") and len(a) == 1:
        return "POST", "/v1/" + c, {"n": num(a[0], c)}
    if c == "led" and len(a) == 1:
        return "POST", "/v1/led", {"mode": a[0]} if a[0] in ("auto", "off") else {"hex": a[0]}
    if c == "card" and len(a) <= 4:
        return "POST", "/v1/card", dict(zip(("label", "title", "a", "b"), a))
    if c == "badge" and len(a) == 2:
        return "POST", "/v1/badge", {"name": a[0], "n": num(a[1], "count")}
    if c == "press" and len(a) == 1:
        return "POST", "/v1/press", {"key": num(a[0], "key")}
    if c == "notify" and a:
        return "POST", "/v1/notify", {"text": " ".join(a)}
    raise ValueError(f"unknown or incomplete command '{c}'")


def call(port, token, method, path, body, timeout=8):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, {"ok": False, "error": str(e)}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        method, path, body = build_request(argv)
    except ValueError as e:
        print(f"error: {e}\n\n{__doc__}", file=sys.stderr)
        return 2
    port, token = load_endpoint()
    try:
        status, out = call(port, token, method, path, body)
    except OSError as e:
        print(f"error: cannot reach the app on port {port} ({e}). Is it running with the Local API switched on?", file=sys.stderr)
        return 2
    if out.get("ok"):
        print(json.dumps(out) if method == "GET" else "ok")
        return 0
    print(f"error: {out.get('error', status)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
