"""Device presence, pairing and session tokens.

The PC running LocalDrop is represented by the permanent pseudo-device LOCAL so
that "send to this computer" works exactly like sending to another device.
"""

import os
import platform
import time

from . import security
LOCAL = "local"


def detect_kind(user_agent):
    ua = (user_agent or "").lower()
    if "ipad" in ua:
        return "ipad"
    if "iphone" in ua:
        return "iphone"
    if "ipod" in ua:
        return "ipod"
    if "android" in ua:
        return "android"
    if "macintosh" in ua or "mac os x" in ua:
        return "mac"
    if "windows" in ua:
        return "windows"
    if "linux" in ua:
        return "linux"
    return "unknown"


def default_name(kind):
    return {
        "ipad": "iPad",
        "iphone": "iPhone",
        "ipod": "iPod",
        "android": "Android device",
        "mac": "Mac",
        "windows": "Windows PC",
        "linux": "Linux computer",
    }.get(kind, "Unnamed device")


class DeviceRegistry(object):
    def __init__(self, config, store, log=None):
        self.config = config
        self.store = store
        self.log = log
        self._ensure_local()

    # -- pairing code --------------------------------------------------
    def pin(self, rotate=False):
        current = self.config.get("pin") or ""
        if rotate or not current:
            current = security.new_pin()
            self.config.set("pin", current)
        return current

    def pair(self, name, user_agent, remote, pin, role="remote"):
        """Validate the pairing code and register a device."""
        if self.config.get("require_pairing") and role != "host":
            if not security.pin_matches(pin, self.config.get("pin")):
                return None, None
        device_id = security.new_device_id()
        token = security.new_token()
        kind = detect_kind(user_agent)
        supplied = (name or "").strip()[:40]
        device = self.store.upsert_device(
            device_id,
            name=supplied or default_name(kind),
            kind=kind,
            role=role,
            token_hash=security.hash_secret(token),
            paired=1,
            user_agent=(user_agent or "")[:300],
            remote=remote or "",
            extras={"ua_kind": kind},
        )
        return device, token

    def authenticate(self, token):
        if not token:
            return None
        return self.store.device_by_token(security.hash_secret(token))

    def local_device(self):
        return self.store.device(LOCAL)

    def _ensure_local(self):
        kind = "windows" if os.name == "nt" else platform.system().lower()
        name = platform.node() or default_name(kind)
        self.store.upsert_device(
            LOCAL, name=name[:40], kind=kind, role="host", paired=1,
            user_agent="LocalDrop server", remote="127.0.0.1",
        )

    # -- presence ------------------------------------------------------
    def heartbeat(self, device_id):
        if device_id == LOCAL:
            return
        self.store.touch_device(device_id)

    def timeout(self):
        return float(self.config.get("device_timeout_s") or 15)

    def online(self, device, now=None):
        if not device:
            return False
        if device["id"] == LOCAL:
            return True
        now = time.time() if now is None else now
        return (now - float(device.get("last_seen") or 0)) <= self.timeout()

    def view(self, device, now=None):
        now = time.time() if now is None else now
        extras = {}
        try:
            import json
            extras = json.loads(device.get("extras") or "{}")
        except (ValueError, TypeError):
            extras = {}
        return {
            "id": device["id"],
            "name": device["name"],
            "kind": device["kind"],
            "role": device["role"],
            "online": self.online(device, now),
            "last_seen": round(float(device.get("last_seen") or 0), 1),
            "seconds_since_seen": round(now - float(device.get("last_seen") or 0), 1)
            if device["id"] != LOCAL else 0,
            "local": device["id"] == LOCAL,
            "paired": bool(device.get("paired")),
            "hint": extras.get("hint", ""),
        }

    def devices(self, now=None):
        now = time.time() if now is None else now
        rows = [d for d in self.store.devices() if d["id"] != LOCAL]
        local = self.store.device(LOCAL)
        out = [self.view(local, now)] if local else []
        out.extend(self.view(d, now) for d in rows)
        return out

    def known_names(self):
        return [d["name"] for d in self.store.devices() if d["id"] != LOCAL]

    def forget(self, device_id):
        if device_id == LOCAL:
            return False
        self.store.delete_device(device_id)
        return True

    def update_name(self, device_id, name, hint=""):
        if device_id == LOCAL:
            return None
        clean = (name or "").strip()[:40] or default_name(
            (self.store.device(device_id) or {}).get("kind", "unknown"))
        extras = {"hint": hint[:40]} if hint else None
        return self.store.upsert_device(device_id, name=clean, extras=extras)
