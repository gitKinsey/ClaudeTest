"""Minimal client for the Arduino / ESP32 'ArduinoOTA' protocol (what espota.py in the core does), so the app can update
a pad over Wi-Fi without any other tool.  EXPERIMENTAL: written from the protocol description and tested against a
local fake device only - it has not been run against real hardware."""
import hashlib
import os
import socket
import time

FLASH, AUTH = 0, 200


def _md5(b):
    return hashlib.md5(b).hexdigest()                      # noqa: S324 - mandated by the OTA protocol


def ota_upload(host, image, password="", port=3232, progress=None, timeout=10, local_port=0):
    """Send `image` (path to a firmware .bin - the app image, NOT the merged image) to the pad. Raises RuntimeError on failure."""
    with open(image, "rb") as f:
        data = f.read()
    size, md5 = len(data), _md5(data)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", local_port))
    srv.listen(1)
    lport = srv.getsockname()[1]
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp.settimeout(timeout)
        invite = f"{FLASH} {lport} {size} {md5}\n".encode()
        reply = b""
        for _ in range(3):                                  # UDP can drop the first packet
            udp.sendto(invite, (host, port))
            try:
                reply, _a = udp.recvfrom(256)
                break
            except socket.timeout:
                continue
        if not reply:
            raise RuntimeError("the pad did not answer (is OTA enabled and the pad on the same network?)")
        text = reply.decode("latin-1").strip()
        if text.startswith("AUTH"):
            if not password:
                raise RuntimeError("the pad wants an OTA password")
            nonce = text.split()[1]
            cnonce = _md5(f"{os.path.basename(image)}{size}{md5}{host}".encode())
            result = _md5(f"{_md5(password.encode())}:{nonce}:{cnonce}".encode())
            udp.sendto(f"{AUTH} {cnonce} {result}\n".encode(), (host, port))
            reply, _a = udp.recvfrom(256)
            text = reply.decode("latin-1").strip()
        if not text.startswith("OK"):
            raise RuntimeError(f"the pad refused the update: {text or 'no reason given'}")
        srv.settimeout(timeout + 10)
        try:
            conn, _a = srv.accept()
        except socket.timeout:
            raise RuntimeError("the pad did not connect back for the transfer") from None
        conn.settimeout(timeout + 10)
        sent = 0
        with conn:
            while sent < size:
                chunk = data[sent:sent + 1460]
                conn.sendall(chunk)
                sent += len(chunk)
                try:
                    conn.recv(32)                           # the device acknowledges what it has written
                except socket.timeout:
                    pass
                if progress:
                    progress(sent / size)
            conn.settimeout(30)
            end = time.time() + 30
            buf = b""
            while time.time() < end and b"OK" not in buf:
                try:
                    part = conn.recv(32)
                except socket.timeout:
                    break
                if not part:
                    break
                buf += part
            if b"OK" not in buf:
                raise RuntimeError("the pad did not confirm the update")
    finally:
        srv.close()
        udp.close()
    return True


def app_image_from_merged(path):
    """The merged image (flash.py) holds bootloader + partitions + app. OTA needs only the app, which starts at 0x10000."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 0x10000 + 64 or data[0x10000] != 0xE9:
        raise ValueError("this does not look like a merged ESP32 image (no app header at 0x10000)")
    return data[0x10000:]
