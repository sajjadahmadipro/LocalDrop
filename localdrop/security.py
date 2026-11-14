"""Pairing codes, session tokens, filename hygiene and path containment."""

import hashlib
import hmac
import mimetypes
import os
import re
import secrets
import string
import threading
import time

from . import netinfo

PIN_ALPHABET = string.digits
PIN_LENGTH = 6
TOKEN_BYTES = 24

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
    "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
    "LPT6", "LPT7", "LPT8", "LPT9", "CLOCK$",
}

_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*\u200e\u200f]+')
_TRAILING = re.compile(r'[ .]+$')
_MAX_STEM = 120
_MAX_EXT = 24


def new_device_id():
    return "d_" + secrets.token_hex(6)


def new_transfer_id():
    return "t_" + secrets.token_hex(8)


def new_token():
    return secrets.token_urlsafe(TOKEN_BYTES)


def new_pin():
    return "".join(secrets.choice(PIN_ALPHABET) for _ in range(PIN_LENGTH))


def hash_secret(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pin_matches(candidate, expected):
    """Constant-time comparison of normalised digits."""
    clean = re.sub(r"[^0-9]", "", candidate or "")[:PIN_LENGTH]
    want = re.sub(r"[^0-9]", "", expected or "")[:PIN_LENGTH]
    if len(want) != PIN_LENGTH or len(clean) != PIN_LENGTH:
        return False
    return hmac.compare_digest(clean, want)


def sanitize_filename(name, fallback="file"):
    """A single, safe, Windows-friendly display name. Never contains a path."""
    if not name:
        return fallback
    # Keep only the final path component, however it was separated.
    name = str(name).replace("\\", "/").split("/")[-1]
    name = name.replace("\x00", "")
    name = _UNSAFE.sub("-", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.lstrip(".")
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    stem = _TRAILING.sub("", stem)[:_MAX_STEM] or fallback
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:_MAX_EXT]
    if stem.upper().split(".")[0] in _WINDOWS_RESERVED:
        stem = "_" + stem
    if ext:
        return "%s.%s" % (stem, ext)
    return stem


def unique_name(directory, name):
    """Never overwrite: returns an available name in `directory`."""
    candidate = name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    index = 1
    while os.path.exists(os.path.join(directory, candidate)):
        suffix = " (%d)" % index
        candidate = "%s%s.%s" % (stem, suffix, ext) if ext else "%s%s" % (stem, suffix)
        index += 1
        if index > 9999:
            candidate = "%s-%s%s" % (stem[:60], secrets.token_hex(3), (".%s" % ext) if ext else "")
            break
    return candidate


def contains(root, path):
    """True when `path` stays inside `root` after resolution."""
    try:
        real_root = os.path.realpath(root)
        real_path = os.path.realpath(path)
    except OSError:
        return False
    return real_path == real_root or real_path.startswith(real_root + os.sep)


def client_is_local(client_ip):
    return netinfo.is_local_network_ip(client_ip or "")


class Throttle:
    """Small in-memory rate limiter, keyed by client address."""

    def __init__(self, attempts=8, window=60.0):
        self.attempts = attempts
        self.window = window
        self._hits = {}
        self._lock = threading.Lock()

    def allowed(self, key):
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(hits) >= self.attempts:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)


def parse_range(header, size):
    """Parse a single Range header -> (start, end) inclusive, or None."""
    if not header:
        return None
    header = header.strip()
    if not header.lower().startswith("bytes="):
        return None
    spec = header[6:].split(",")[0].strip()
    if "-" not in spec:
        return None
    first, _, last = spec.partition("-")
    try:
        if first == "":
            length = int(last)
            if length <= 0:
                return None
            start = max(0, size - length)
            end = size - 1
        else:
            start = int(first)
            end = int(last) if last else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return (start, min(end, size - 1))


def media_type_for(name, declared=""):
    """Trust our own allow-list over a client-supplied MIME type."""
    ext = os.path.splitext(name)[1].lower()
    fixed = {
        ".epub": "application/epub+zip",
        ".pdf": "application/pdf",
        ".zip": "application/zip",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".m4a": "audio/mp4",
        ".m4b": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/plain; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",
        ".html": "text/plain; charset=utf-8",
        ".htm": "text/plain; charset=utf-8",
        ".svg": "text/plain; charset=utf-8",
        ".js": "text/plain; charset=utf-8",
        ".py": "text/plain; charset=utf-8",
        ".exe": "application/octet-stream",
        ".bat": "application/octet-stream",
        ".cmd": "application/octet-stream",
        ".scr": "application/octet-stream",
        ".jar": "application/octet-stream",
        ".apk": "application/octet-stream",
    }
    if ext in fixed:
        return fixed[ext]
    guess = mimetypes.guess_type(name)[0]
    if guess:
        return guess
    declared = (declared or "").split(";")[0].strip().lower()
    if declared and re.match(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$", declared):
        if declared not in ("text/html", "application/xhtml+xml"):
            return declared
    return "application/octet-stream"


def inline_capable(media_type):
    """Formats legacy Safari can render in-place (and offer to Books)."""
    main = (media_type or "").split("/")[0]
    return main in ("image", "text", "audio", "video") or media_type == "application/pdf"
