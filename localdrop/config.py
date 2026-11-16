"""Configuration: a single JSON file inside the LocalDrop data directory."""

import copy
import json
import os
import sys
import threading

DEFAULTS = {
    # Network
    "port": 8080,
    "bind": "0.0.0.0",
    "display_host": "",            # override the address shown to devices
    # Transfers
    "chunk_size": 2097152,         # 2 MiB - fewer round-trips, still safe on iPad 2
    "max_upload_bytes": 0,         # 0 == unlimited
    "parallel_uploads": 2,         # server-side concurrency hint for clients
    # Presence
    "poll_interval_ms": 2500,
    "poll_interval_idle_ms": 5000,
    "device_timeout_s": 15,
    # Security
    "require_pairing": True,
    "pin": "",                     # empty == generated at first start
    "auto_accept": False,
    "allow_host_without_pin": True,
    # Storage (relative to the data dir unless absolute)
    "incoming_dir": "incoming",
    "temp_dir": "tmp",
    # Behaviour
    "theme": "light",
    "start_with_windows": False,
    "log_level": "info",
}

# Hard floors/ceilings so a bad hand-edit cannot make the app unusable.
LIMITS = {
    "port": (1, 65535),
    "chunk_size": (65536, 33554432),
    "poll_interval_ms": (500, 60000),
    "poll_interval_idle_ms": (1000, 120000),
    "device_timeout_s": (5, 600),
    "parallel_uploads": (1, 8),
    "max_upload_bytes": (0, 1 << 62),
}

_INT_KEYS = tuple(LIMITS.keys())


def default_data_dir():
    """Dedicated data directory next to the project (portable) with a fallback."""
    root = project_root()
    candidate = os.path.join(root, "data")
    try:
        os.makedirs(candidate, exist_ok=True)
        return candidate
    except OSError:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "LocalDrop")


def project_root():
    if getattr(sys, "frozen", False):
        # A frozen exe has no package tree on disk: keep the data folder next to
        # it so the exe stays portable (move it, the data moves with nothing else).
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    # normpath so a hand-typed LOCALDROP_DATA with forward slashes does not
    # produce paths that mix separators.
    return os.path.normpath(os.environ.get("LOCALDROP_DATA") or default_data_dir())


def _clamp(key, value):
    if key in LIMITS:
        lo, hi = LIMITS[key]
        try:
            value = int(value)
        except (TypeError, ValueError):
            return DEFAULTS[key]
        return max(lo, min(hi, value))
    return value


class Config:
    """Thread-safe view over config.json with sane defaults."""

    def __init__(self, path=None):
        self._lock = threading.RLock()
        self.path = path or os.path.join(data_dir(), "config.json")
        self.values = copy.deepcopy(DEFAULTS)
        self.load()

    # -- persistence ---------------------------------------------------
    def load(self):
        with self._lock:
            raw = {}
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as fh:
                        raw = json.load(fh)
                except (ValueError, OSError):
                    raw = {}
            merged = copy.deepcopy(DEFAULTS)
            for key, value in (raw or {}).items():
                if key in DEFAULTS:
                    merged[key] = value
            for key in _INT_KEYS:
                merged[key] = _clamp(key, merged[key])
            merged["port"] = int(merged["port"])
            merged["pin"] = str(merged.get("pin") or "")
            self.values = merged
            self._write_locked()
            return self.values

    def _write_locked(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.values, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def save(self):
        with self._lock:
            self._write_locked()

    # -- access --------------------------------------------------------
    def get(self, key, fallback=None):
        with self._lock:
            return self.values.get(key, fallback)

    def set(self, key, value):
        with self._lock:
            if key not in DEFAULTS:
                raise KeyError(key)
            self.values[key] = _clamp(key, value)
            self._write_locked()

    def update(self, mapping):
        with self._lock:
            for key, value in mapping.items():
                if key in DEFAULTS and value is not None:
                    self.set(key, value)

    def snapshot(self):
        """Public settings view - never leaks the PIN itself."""
        with self._lock:
            return {key: value for key, value in self.values.items() if key != "pin"}

    # -- derived paths -------------------------------------------------
    def abs_path(self, key):
        value = self.get(key) or ""
        if os.path.isabs(value):
            return value
        return os.path.join(data_dir(), value or "")

    @property
    def incoming(self):
        path = self.abs_path("incoming_dir")
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def temp(self):
        path = self.abs_path("temp_dir")
        os.makedirs(path, exist_ok=True)
        return path

    @property
    def db_path(self):
        os.makedirs(data_dir(), exist_ok=True)
        return os.path.join(data_dir(), "localdrop.db")

    @property
    def log_path(self):
        os.makedirs(data_dir(), exist_ok=True)
        return os.path.join(data_dir(), "localdrop.log")
