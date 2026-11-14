"""File + console logging with a ring buffer for the launcher window."""

import collections
import logging
import logging.handlers
import os
import sys

_BUFFER = collections.deque(maxlen=400)
_CONFIGURED = False

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


class RingHandler(logging.Handler):
    def emit(self, record):
        try:
            _BUFFER.append(self.format(record))
        except Exception:
            pass

    @staticmethod
    def lines():
        return list(_BUFFER)


def configure(log_path=None, level="info"):
    global _CONFIGURED
    root = logging.getLogger("localdrop")
    if _CONFIGURED:
        return root
    _CONFIGURED = True
    root.setLevel(_LEVELS.get(str(level).lower(), logging.INFO))
    root.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    ring = RingHandler()
    ring.setFormatter(fmt)
    root.addHandler(ring)
    if log_path:
        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)
        except OSError:
            pass
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    root.addHandler(stream)
    return root


def get_logger(name="localdrop"):
    if name != "localdrop" and not name.startswith("localdrop."):
        name = "localdrop." + name
    return logging.getLogger(name)


def recent_lines(count=200):
    return list(_BUFFER)[-count:]
