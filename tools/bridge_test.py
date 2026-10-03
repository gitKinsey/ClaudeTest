"""Tests for the local API (desk_lib/bridge.py) and the command-line client.   python3 tools/bridge_test.py"""
import http.client
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import deskcompanion_cli as cli   # noqa: E402
from desk_lib import bridge   # noqa: E402

# ---- validate(): pure request checking
v = bridge.validate
assert v("/v1/layer", {"n": 2}) == {"kind": "layer", "val": 1} and v("/v1/layer", {"n": "next"}) == {"kind": "layer", "val": "next"}
assert v("/v1/mode", {"n": 6}) == {"kind": "mode", "val": 6} and v("/v1/brightness", {"n": 200}) == {"kind": "brightness", "val": 200}
assert v("/v1/led", {"hex": "#FF8800"}) == {"kind": "led", "hex": "ff8800"} and v("/v1/led", {"mode": "off"}) == {"kind": "led", "mode": "off"}
assert v("/v1/card", {"label": "BUILD", "title": "x" * 99}) == {"kind": "card", "label": "BUILD", "title": "x" * 40, "a": "", "b": ""}
assert v("/v1/badge", {"name": "mail", "n": 3}) == {"kind": "badge", "name": "mail", "n": 3} and v("/v1/press", {"key": 7}) == {"kind": "press", "key": 7}
assert v("/v1/notify", {"text": " hi "}) == {"kind": "notify", "text": "hi"}
for path, body, code in (("/v1/layer", {"n": 4}, 400), ("/v1/layer", {"n": True}, 400), ("/v1/layer", {}, 400), ("/v1/mode", {"n": 0}, 400),
                         ("/v1/brightness", {"n": 4}, 400), ("/v1/brightness", {"n": "9"}, 400), ("/v1/led", {"hex": "zzzzzz"}, 400),
                         ("/v1/led", {}, 400), ("/v1/badge", {"n": 1}, 400), ("/v1/badge", {"name": "a", "n": 1000}, 400),
                         ("/v1/press", {"key": 8}, 400), ("/v1/notify", {"text": ""}, 400), ("/v1/nope", {}, 404)):
    try:
        v(path, body); raise SystemExit(f"accepted {path} {body}")
    except bridge.ApiError as e:
        assert e.status == code, (path, body, e.status)

# ---- HTTP server: auth, browser protection, errors, routing
seen = []
def handler(req):
    seen.append(req)
    if req["kind"] == "press" and req["key"] == 7:
        raise bridge.ApiError(409, "the pad is not connected")
    if req["kind"] == "notify":
        raise RuntimeError("boom")
    return {"done": req["kind"]}
b = bridge.Bridge(handler, token="T" * 24, status=lambda: {"connected": True, "layer": 1})

def http_call(method, path, body=None, token="T" * 24, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", b.port, timeout=5)
    h = {"Authorization": f"Bearer {token}"} if token else {}
    h.update(headers or {})
    if host:
        h["Host"] = host
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body))
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = json.loads(r.read())
    c.close()
    return r.status, out
assert http_call("GET", "/v1/status") == (200, {"ok": True, "connected": True, "layer": 1})
assert http_call("POST", "/v1/layer", {"n": 3}) == (200, {"ok": True, "done": "layer"}) and seen[-1] == {"kind": "layer", "val": 2}
assert http_call("GET", "/v1/status", token="")[0] == 401 and http_call("GET", "/v1/status", token="wrong")[0] == 401
assert http_call("GET", "/v1/status", headers={"Origin": "https://evil.example"})[0] == 403, "a web page must not reach the API even with the token"
assert http_call("GET", "/v1/status", host="evil.example")[0] == 403, "DNS-rebinding style Host header refused"
assert http_call("GET", "/v1/status", host=f"localhost:{b.port}")[0] == 200
n_seen = len(seen)
assert http_call("POST", "/v1/layer", {"n": 9})[0] == 400 and http_call("POST", "/v1/layer", b"not json")[0] == 400 and http_call("POST", "/v1/layer", b"[1]")[0] == 400
assert http_call("POST", "/v1/nope", {})[0] == 404 and http_call("GET", "/v1/layer")[0] == 404 and len(seen) == n_seen, "bad requests never reach the app"
assert http_call("POST", "/v1/press", {"key": 7}) == (409, {"ok": False, "error": "the pad is not connected"})
s, o = http_call("POST", "/v1/notify", {"text": "x"}); assert s == 500 and o["ok"] is False and "boom" in o["error"]
assert http_call("POST", "/v1/layer", b"x" * 9000, headers={"Content-Length": "9000"})[0] == 413
print("bridge OK")

# ---- command-line client
br = cli.build_request
assert br(["status"]) == ("GET", "/v1/status", None) and br(["layer", "2"]) == ("POST", "/v1/layer", {"n": 2}) and br(["layer", "next"])[2] == {"n": "next"}
assert br(["led", "off"])[2] == {"mode": "off"} and br(["led", "ff8800"])[2] == {"hex": "ff8800"} and br(["brightness", "90"])[2] == {"n": 90}
assert br(["card", "A", "B"])[2] == {"label": "A", "title": "B"} and br(["card"])[2] == {} and br(["badge", "mail", "3"])[2] == {"name": "mail", "n": 3}
assert br(["notify", "build", "done"])[2] == {"text": "build done"} and br(["press", "5"])[2] == {"key": 5}
for bad in ([], ["layer"], ["layer", "x"], ["nope"], ["badge", "a"], ["notify"], ["card"] + ["x"] * 5):
    try:
        br(bad); raise SystemExit(f"cli accepted {bad}")
    except ValueError:
        pass
assert cli.load_endpoint({"DESK_COMPANION_API": "1234:abc"}) == (1234, "abc")
tmp = tempfile.mkdtemp(); cfgp = os.path.join(tmp, "c.json")
open(cfgp, "w").write(json.dumps({"api": {"port": 4321, "token": "tok"}}))
assert cli.load_endpoint({}, cfgp) == (4321, "tok")
for bad_env, p in (({"DESK_COMPANION_API": "x"}, None), ({}, os.path.join(tmp, "missing.json"))):
    try:
        cli.load_endpoint(bad_env, p); raise SystemExit("accepted a bad endpoint")
    except SystemExit as e:
        assert str(e) and e.code != 0
os.environ["DESK_COMPANION_API"] = f"{b.port}:{'T' * 24}"
assert cli.main(["layer", "1"]) == 0 and seen[-1] == {"kind": "layer", "val": 0}
assert cli.main(["status"]) == 0 and cli.main(["press", "7"]) == 1 and cli.main(["layer", "9"]) == 1 and cli.main(["bogus"]) == 2
os.environ["DESK_COMPANION_API"] = f"{b.port}:wrongtoken"
assert cli.main(["status"]) == 1
b.close()
os.environ["DESK_COMPANION_API"] = f"{b.port}:{'T' * 24}"
assert cli.main(["status"]) == 2, "app not running -> exit 2"
print("cli OK")
print("ALL BRIDGE TESTS PASSED")
