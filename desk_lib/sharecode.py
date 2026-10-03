"""Share codes: one layer of key assignments as a short text (letters and digits) to paste into a chat or an e-mail.
Format  DC1:<base64url(zlib(json))>.  Decoding refuses anything that is not exactly that, and caps the unpacked size, so a hostile code cannot do harm;
the app additionally re-validates every action before it touches the user's key map."""
import base64
import json
import re
import zlib

PREFIX = "DC1:"
MAX_CODE = 30000                  # characters pasted
MAX_JSON = 60000                  # bytes after unpacking


def encode(payload):
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return PREFIX + base64.urlsafe_b64encode(zlib.compress(raw, 9)).decode("ascii").rstrip("=")


def decode(code):
    """-> payload dict. Raises ValueError with a readable reason."""
    code = re.sub(r"\s+", "", str(code or ""))
    if not code.startswith(PREFIX):
        raise ValueError("this is not a Desk Companion share code (it starts with DC1:)")
    if len(code) > MAX_CODE:
        raise ValueError("the code is too long")
    body = code[len(PREFIX):]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", body):
        raise ValueError("the code contains characters that do not belong to it")
    try:
        packed = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        d = zlib.decompressobj()
        raw = d.decompress(packed, MAX_JSON + 1)
        if len(raw) > MAX_JSON or d.unconsumed_tail:
            raise ValueError("the code unpacks to something too large")
        payload = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        if "too large" in str(e):
            raise
        raise ValueError("the code is damaged (copy it again, completely)") from None
    except (zlib.error, UnicodeDecodeError):
        raise ValueError("the code is damaged (copy it again, completely)") from None
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("this code was made by a different version of the app")
    return payload
