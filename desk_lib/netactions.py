"""Actions that talk to the internet on the user's behalf: webhooks (smart home, Slack, Discord, IFTTT ...), translation of the clipboard, and an
AI prompt template. HTTP is injectable (`fetch`), so everything is tested without a network. Nothing here runs unless the user put it on a key."""
import json
import re
import urllib.parse
import urllib.request

MAX_READ = 200_000
UA = "DeskCompanion/1.4"


def http_request(method, url, body=None, headers=None, timeout=10):
    """-> (status, text). Only http / https. body: str | bytes | dict (sent as JSON)."""
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("only http:// and https:// addresses are allowed")
    h = {"User-Agent": UA}
    data = None
    if body is not None:
        if isinstance(body, dict):
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        else:
            data = body.encode() if isinstance(body, str) else body
            h.setdefault("Content-Type", "application/json" if str(body).lstrip().startswith(("{", "[")) else "text/plain")
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(MAX_READ).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(MAX_READ).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"network problem: {getattr(e, 'reason', e)}") from None


def parse_webhook(arg):
    """'https://x' | 'GET https://x' | 'POST https://x {"a":1}'  ->  (METHOD, url, body or None)."""
    m = re.match(r"^\s*(?:(GET|POST|PUT|PATCH|DELETE)\s+)?(https?://\S+)(?:\s+(.*))?$", str(arg), re.I | re.S)
    if not m:
        raise ValueError("webhook: write  [GET|POST|PUT|PATCH|DELETE] https://address [body]")
    method, url, body = (m.group(1) or ("POST" if m.group(3) else "GET")).upper(), m.group(2), m.group(3)
    return method, url, (body.strip() if body else None)


def webhook(arg, fetch=None):
    method, url, body = parse_webhook(arg)
    status, text = (fetch or http_request)(method, url, body)
    if not 200 <= status < 300:
        raise ValueError(f"the server answered {status}: {text[:80].strip()}")
    return f"{method} {urllib.parse.urlparse(url).netloc}: {status}"


def translate(text, lang, fetch=None):
    """Translate `text` into `lang` (a code like de, es, fr, ja) with the free MyMemory service (no key, about 5000 characters a day)."""
    lang = str(lang).strip().lower()
    if not re.fullmatch(r"[a-z]{2,3}(-[a-z]{2,4})?", lang):
        raise ValueError("translate needs a language code like de, es, fr, ja")
    if not text.strip():
        raise ValueError("the clipboard is empty")
    fetch = fetch or http_request
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?\n])\s+", text.strip()):               # the service takes at most ~500 characters per request
        if len(cur) + len(sent) > 450 and cur:
            parts.append(cur)
            cur = ""
        cur = (cur + " " + sent).strip()
        while len(cur) > 450:
            parts.append(cur[:450])
            cur = cur[450:]
    if cur:
        parts.append(cur)
    out = []
    for part in parts[:8]:
        status, body = fetch("GET", "https://api.mymemory.translated.net/get?" + urllib.parse.urlencode({"q": part, "langpair": f"Autodetect|{lang}"}), None)
        try:
            d = json.loads(body)
            out.append(d["responseData"]["translatedText"])
        except (ValueError, KeyError, TypeError):
            raise ValueError(f"the translation service answered {status} without a translation") from None
    return " ".join(out)


def ask_ai(prompt, api_key, model="claude-haiku-4-5-20251001", fetch=None, max_tokens=700, system=None):
    """One question to the Claude API (https://api.anthropic.com/v1/messages) with the user's own key; returns the answer text."""
    if not api_key:
        raise ValueError("no API key: Device page -> AI actions -> paste your Anthropic API key")
    body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    status, text = (fetch or http_request)("POST", "https://api.anthropic.com/v1/messages", body,
                                           {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        d = json.loads(text)
    except ValueError:
        raise ValueError(f"the AI service answered {status} (not JSON)") from None
    if status != 200:
        msg = (d.get("error") or {}).get("message", text[:80]) if isinstance(d, dict) else text[:80]
        raise ValueError(f"the AI service refused: {msg}")
    out = "".join(b.get("text", "") for b in d.get("content", []) if isinstance(b, dict) and b.get("type") == "text").strip()
    if not out:
        raise ValueError("the AI service returned no text")
    return out
