"""Local API: let scripts, other programs and the command line drive the pad through the running app.

  Off by default.  127.0.0.1 only.  Every call needs the secret token (header  Authorization: Bearer TOKEN).
  Requests that carry an Origin header (= a web page in a browser) or a Host header that is not 127.0.0.1 / localhost are
  refused, so a website cannot reach it even if it guesses the port.

    GET  /v1/status
    POST /v1/layer       {"n": 1|2|3|"next"|"prev"}      POST /v1/mode        {"n": 1..6}
    POST /v1/brightness  {"n": 5..255}                    POST /v1/led         {"mode": "auto|off"}  or  {"hex": "ff8800"}
    POST /v1/card        {"label","title","a","b"}  (a custom Info card; {} clears)       POST /v1/badge  {"name": "mail", "n": 3}
    POST /v1/press       {"key": 1..7}  (a virtual key press: 1-5 keys, 6/7 dial turn)    POST /v1/notify {"text": "..."}
Answers are JSON: {"ok": true, ...} or {"ok": false, "error": "..."} with a matching HTTP status."""
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 8192


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _int(body, key, lo, hi, what):
    v = body.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ApiError(400, f"'{key}' must be a whole number from {lo} to {hi} ({what})")
    return v


def validate(path, body):
    """-> a normalised request for the app: {"kind": ..., ...}. Raises ApiError. Pure: no device access."""
    if path == "/v1/layer":
        n = body.get("n")
        if n in ("next", "prev"):
            return {"kind": "layer", "val": n}
        return {"kind": "layer", "val": _int(body, "n", 1, 3, "layer number") - 1}
    if path == "/v1/mode":
        return {"kind": "mode", "val": _int(body, "n", 1, 6, "screen number")}
    if path == "/v1/brightness":
        return {"kind": "brightness", "val": _int(body, "n", 5, 255, "brightness")}
    if path == "/v1/led":
        mode, hx = body.get("mode"), str(body.get("hex") or "").lstrip("#")
        if hx:
            if len(hx) != 6 or any(c not in "0123456789abcdefABCDEF" for c in hx):
                raise ApiError(400, "'hex' must be a colour like ff8800")
            return {"kind": "led", "hex": hx.lower()}
        if mode in ("auto", "off", "breathe", "fire"):
            return {"kind": "led", "mode": mode}
        raise ApiError(400, "send {\"mode\": \"auto\"|\"off\"|\"breathe\"|\"fire\"} or {\"hex\": \"ff8800\"}")
    if path == "/v1/card":
        out = {k: str(body.get(k) or "")[:40] for k in ("label", "title", "a", "b")}
        return {"kind": "card", **out}
    if path == "/v1/badge":
        name = str(body.get("name") or "").strip()[:8]
        if not name:
            raise ApiError(400, "'name' is missing")
        return {"kind": "badge", "name": name, "n": _int(body, "n", 0, 999, "count")}
    if path == "/v1/press":
        return {"kind": "press", "key": _int(body, "key", 1, 7, "1-5 keys, 6 dial right, 7 dial left")}
    if path == "/v1/notify":
        text = str(body.get("text") or "").strip()
        if not text:
            raise ApiError(400, "'text' is missing")
        return {"kind": "notify", "text": text[:200]}
    raise ApiError(404, "unknown endpoint")


class Bridge:
    def __init__(self, handler, token=None, port=0, status=None):
        """handler(request dict) -> dict (may raise ApiError); status() -> dict for GET /v1/status."""
        self.token = token or secrets.token_urlsafe(18)
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _guard(self):
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
                if self.headers.get("Origin") is not None or host not in ("127.0.0.1", "localhost", "::1"):
                    raise ApiError(403, "browser / non-local requests are refused")
                auth = self.headers.get("Authorization") or ""
                if not auth.startswith("Bearer ") or not secrets.compare_digest(auth[7:], outer.token):
                    raise ApiError(401, "missing or wrong token")

            def _run(self, method):
                try:
                    self._guard()
                    if method == "GET":
                        if self.path.split("?")[0] != "/v1/status":
                            raise ApiError(404, "unknown endpoint")
                        return self._reply(200, {"ok": True, **(status() if status else {})})
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > MAX_BODY:
                        raise ApiError(413, "request too large")
                    try:
                        body = json.loads(self.rfile.read(n) or b"{}")
                    except ValueError:
                        raise ApiError(400, "body must be JSON") from None
                    if not isinstance(body, dict):
                        raise ApiError(400, "body must be a JSON object")
                    req = validate(self.path.split("?")[0], body)
                    self._reply(200, {"ok": True, **(handler(req) or {})})
                except ApiError as e:
                    self._reply(e.status, {"ok": False, "error": e.message})
                except Exception as e:                           # noqa: BLE001
                    self._reply(500, {"ok": False, "error": str(e)})

            def do_GET(self):
                self._run("GET")

            def do_POST(self):
                self._run("POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
