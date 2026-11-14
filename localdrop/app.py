"""Request routing for the LocalDrop HTTP API.

`Application.handle()` is transport-agnostic so the same logic can be exercised by
tests without a socket. Bodies are read as streams and never buffered whole.
"""

import json
import os
import time

from . import config as config_module
from . import netinfo, qr, security, startup
from .devices import LOCAL, DeviceRegistry
from .transfers import TransferError, TransferStore

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(os.path.dirname(APP_DIR), "web")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/favicon.ico": ("icon.svg", "image/svg+xml"),
}
TOKEN_KEYS = ("token", "t")
DEVICE_KEYS = ("device", "d")


class Response(object):
    def __init__(self, status=200, body=b"", headers=None, content_type="application/json"):
        self.status = status
        self.headers = headers or {}
        self.content_type = content_type
        if isinstance(body, bytes):
            self.body = body
        elif isinstance(body, str):
            self.body = body.encode("utf-8")
        elif body is None:
            self.body = b""
        else:
            self.body = json.dumps(body, default=_json_default).encode("utf-8")
        self.stream_path = None
        self.stream_headers = None
        self.transfer_id = None
        self.range_start = 0
        self.range_end = 0
        self.total = 0

    @classmethod
    def file(cls, path, headers):
        resp = cls(status=200, body=b"", headers=headers)
        resp.stream_path = path
        resp.stream_headers = headers
        return resp

    @classmethod
    def json(cls, payload, status=200, headers=None):
        return cls(status=status, body=payload, headers=headers)

    @classmethod
    def error(cls, message, status=400, **extra):
        payload = {"error": message, "ok": False}
        payload.update(extra)
        return cls.json(payload, status)


def _json_default(value):
    if isinstance(value, float):
        return round(value, 3)
    return str(value)


class Application(object):
    def __init__(self, config=None, log=None):
        from .db import Database
        self.config = config or config_module.Config()
        self.log = log
        self.store = Database(self.config.db_path)
        self.registry = DeviceRegistry(self.config, self.store, log)
        self.transfers = TransferStore(self.config, self.store, self.registry, log)
        self.started_at = time.time()
        self.throttle = security.Throttle(attempts=12, window=60.0)
        self.server = None            # set by server.py, used for /api/server/stop

    # -- plumbing ------------------------------------------------------
    def note(self, level, message, *args):
        if not self.log:
            return
        getattr(self.log, level)(message, *args)

    def port(self):
        """The port that is actually listening, once the server has bound."""
        if self.server is not None:
            return int(self.server.port)
        return int(self.config.get("port"))

    def access_url(self, client_host=None):
        override = self.config.get("display_host")
        host = override or client_host or netinfo.primary_address()
        return "http://%s:%d" % (host, self.port())

    def session(self, query, headers):
        """Resolve the caller from token + device id."""
        token = _first(query, headers, TOKEN_KEYS)
        device_id = _first(query, headers, DEVICE_KEYS)
        device = self.registry.authenticate(token) if token else None
        if device and device_id and device["id"] != device_id:
            device = None
        return device

    # -- dispatch ------------------------------------------------------
    def handle(self, method, path, query, headers, body, client_ip, client_host=""):
        """body is a readable stream; returns Response."""
        method = (method or "GET").upper()
        if not security.client_is_local(client_ip):
            self.note("warning", "refused request from non-local address %s", client_ip)
            return Response.error("LocalDrop only answers on the local network.", 403)

        if path.startswith("/api/"):
            try:
                return self._api(method, path, query, headers, body, client_ip, client_host)
            except TransferError as exc:
                payload = {"error": str(exc), "ok": False}
                payload.update(exc.payload)
                return Response.json(payload, exc.status)
            except ValueError as exc:
                return Response.error(str(exc) or "bad request", 400)
        if method in ("GET", "HEAD"):
            return self._static(path, headers)
        return Response.error("not found", 404)

    # -- static --------------------------------------------------------
    def _static(self, path, headers):
        entry = STATIC_FILES.get(path)
        if not entry:
            return Response.error("not found", 404)
        filename, content_type = entry
        full = os.path.join(WEB_DIR, filename)
        if not os.path.exists(full):
            return Response.error("missing %s" % filename, 500)
        stat = os.stat(full)
        etag = '"%s-%d"' % (int(stat.st_mtime), stat.st_size)
        if _h(headers, "If-None-Match") == etag:
            resp = Response(status=304, body=b"")
            resp.headers["ETag"] = etag
            return resp
        with open(full, "rb") as handle:
            data = handle.read()
        resp = Response(status=200, body=data, content_type=content_type)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    # -- API -----------------------------------------------------------
    def _api(self, method, path, query, headers, body, client_ip, client_host):
        loopback = client_ip in ("127.0.0.1", "::1", "localhost")
        device = self.session(query, headers)

        if path == "/api/hello" and method == "GET":
            return Response.json({
                "app": "LocalDrop",
                "version": _version(),
                "paired": False,
                "needs_pairing": bool(self.config.get("require_pairing")),
                "server_time": time.time(),
            })

        if path == "/api/pair" and method == "POST":
            return self._pair(query, headers, body, client_ip, loopback)

        if device is None:
            return Response.error("This browser is not paired with LocalDrop.", 401,
                                  needs_pairing=True)

        self.registry.heartbeat(device["id"])
        role = device["role"]
        viewer_id = device["id"]

        if path == "/api/state" and method == "GET":
            return self._state(device, client_host, loopback)
        if path == "/api/qr" and method == "GET":
            return self._qr(client_host)
        if path == "/api/rename" and method == "POST":
            payload = _json_body(body, headers)
            updated = self.registry.update_name(viewer_id, payload.get("name", ""),
                                                payload.get("kind_hint", ""))
            if updated is None:
                return Response.error("this device cannot be renamed", 403)
            return Response.json({"ok": True, "device": self.registry.view(updated)})

        if path.startswith("/api/transfers"):
            return self._transfers(method, path, query, headers, body, device, loopback)

        if path == "/api/files/open" and method == "POST":
            return self._open_folder(device, loopback, query, headers, body)
        if path == "/api/devices/forget" and method == "POST":
            if role != "host":
                return Response.error("host controls only", 403)
            payload = _json_body(body, headers)
            target = str(payload.get("device", ""))
            if target == viewer_id or target == LOCAL:
                return Response.error("cannot forget this session", 400)
            for record in self.transfers.visible(self.store.transfers(), role, viewer_id):
                if record["status"] not in ("complete", "canceled", "rejected"):
                    if record["source"] == target or record["dest"] == target:
                        return Response.error(
                            "that device still has an unfinished transfer", 409)
            self.registry.forget(target)
            return Response.json({"ok": True})

        if path in ("/api/settings", "/api/pin/rotate", "/api/server/stop",
                    "/api/history/clear"):
            if role != "host" or not loopback:
                return Response.error("these controls live on the computer running LocalDrop",
                                      403)
            if path == "/api/settings" and method == "GET":
                return Response.json(self._settings_payload())
            if path == "/api/settings" and method == "POST":
                payload = _json_body(body, headers)
                return self._save_settings(payload)
            if path == "/api/pin/rotate" and method == "POST":
                pin = self.registry.pin(rotate=True)
                self.note("info", "pairing code rotated")
                return Response.json({"ok": True, "pin": pin})
            if path == "/api/history/clear" and method == "POST":
                removed = 0
                for record in self.store.transfers():
                    if record["status"] in ("complete", "canceled", "rejected"):
                        self.transfers.delete(record["id"], remove_files=False)
                        removed += 1
                return Response.json({"ok": True, "removed": removed})
            if path == "/api/server/stop" and method == "POST":
                if self.server is not None:
                    self.server.shutdown_from_request()
                    return Response.json({"ok": True, "stopping": True})
                return Response.error("the server is not controlled from here", 409)

        return Response.error("not found", 404)

    # -- pairing -------------------------------------------------------
    def _pair(self, query, headers, body, client_ip, loopback):
        payload = _json_body(body, headers)
        if not self.throttle.allowed(client_ip or "unknown"):
            self.note("warning", "too many pairing attempts from %s", client_ip)
            return Response.error("Too many attempts. Wait a minute and try again.", 429)
        pin = str(payload.get("pin", ""))
        user_agent = _h(headers, "User-Agent", "") or payload.get("user_agent", "")
        role = "host" if (loopback and self.config.get("allow_host_without_pin")) else "remote"
        device, token = self.registry.pair(
            payload.get("name", ""), user_agent,
            client_ip, pin, role=role)
        if device is None:
            self.note("warning", "pairing rejected for %s", client_ip)
            return Response.error("That pairing code is not correct.", 403)
        self.throttle.reset(client_ip or "unknown")
        self.note("info", "paired %s (%s) from %s", device["name"], device["kind"], client_ip)
        return Response.json({
            "ok": True,
            "device": device["id"],
            "token": token,
            "role": device["role"],
            "name": device["name"],
        })

    # -- state ---------------------------------------------------------
    def _settings_payload(self):
        snapshot = self.config.snapshot()
        snapshot.pop("incoming_dir", None)
        snapshot.pop("temp_dir", None)
        snapshot["start_with_windows"] = self._startup_state()
        return {
            "settings": snapshot,
            "pin": self.registry.pin(),
            "paths": {
                "data": config_module.data_dir(),
                "incoming": self.config.incoming,
                "temp": self.config.temp,
                "database": self.config.db_path,
                "log": self.config.log_path,
            },
            "addresses": netinfo.lan_addresses(),
            "url": self.access_url(),
            "stats": self.transfers.stats(),
            "uptime": round(time.time() - self.started_at, 1),
        }

    def _state(self, device, client_host, loopback):
        role = device["role"]
        now = time.time()
        records = self.transfers.visible(self.store.transfers(), role, device["id"])
        views = self.transfers.views(records, device["id"])
        payload = {
            "ok": True,
            "now": round(now, 2),
            "server": {
                "version": _version(),
                "url": self.access_url(client_host),
                "addresses": netinfo.lan_addresses(),
                "port": self.port(),
                "chunk_size": int(self.config.get("chunk_size")),
                "poll_ms": int(self.config.get("poll_interval_ms")),
                "idle_poll_ms": int(self.config.get("poll_interval_idle_ms")),
                "device_timeout_s": int(self.config.get("device_timeout_s")),
                "parallel_uploads": int(self.config.get("parallel_uploads")),
                "auto_accept": bool(self.config.get("auto_accept")),
                "max_upload_bytes": int(self.config.get("max_upload_bytes") or 0),
                "theme": self.config.get("theme"),
                "uptime": round(now - self.started_at, 1),
            },
            "me": self.registry.view(device, now),
            "devices": self.registry.devices(now),
            "transfers": views,
            "local": {"id": LOCAL, "name": (self.registry.local_device() or {}).get("name")},
        }
        if role == "host":
            payload["host"] = {
                "loopback": loopback,
                "pin": self.registry.pin(),
                "settings": self.config.snapshot(),
                "paths": {
                    "incoming": self.config.incoming,
                    "data": config_module.data_dir(),
                },
                "stats": self.transfers.stats(),
            }
        return Response.json(payload)

    def _qr(self, client_host=None):
        url = self.access_url(client_host)
        symbol = qr.encode(url, level="M")
        return Response(status=200, body=symbol.to_svg(scale=6, quiet=3),
                        content_type="image/svg+xml")

    # -- transfers -----------------------------------------------------
    def _transfers(self, method, path, query, headers, body, device, loopback):
        role, viewer_id = device["role"], device["id"]
        if path == "/api/transfers" and method == "GET":
            records = self.transfers.visible(self.store.transfers(), role, viewer_id)
            return Response.json({"ok": True,
                                  "transfers": self.transfers.views(records, viewer_id)})
        if path == "/api/transfers" and method == "POST":
            payload = _json_body(body, headers)
            dest = str(payload.get("dest", ""))
            if dest != LOCAL and self.store.device(dest) is None:
                return Response.error("unknown destination device", 400)
            if dest != LOCAL and self.store.device(dest)["paired"] != 1:
                return Response.error("that device is not paired", 403)
            record = self.transfers.create(
                viewer_id, dest, payload.get("name", ""), payload.get("size", 0),
                payload.get("type", ""))
            return Response.json({
                "ok": True,
                "transfer": self.transfers.view(record),
                "offset": record["offset"],
                "chunk_size": record["chunk_size"],
            }, status=201)

        parts = [p for p in path.split("/") if p]
        # /api/transfers/<id>/<action>[/<offset>]
        if len(parts) not in (4, 5) or parts[:2] != ["api", "transfers"]:
            return Response.error("not found", 404)
        transfer_id, action = parts[2], parts[3]
        record = self.transfers.get(transfer_id)

        if action == "data" and method in ("GET", "HEAD"):
            return self._serve_file(record, device, query, headers, method, loopback)

        if action == "chunk":
            if method not in ("PUT", "POST"):
                return Response.error("use PUT", 405)
            if record["source"] != viewer_id and role != "host":
                return Response.error("not your transfer", 403)
            try:
                offset = int(parts[4]) if len(parts) > 4 else -1
            except ValueError:
                return Response.error("offset must be a number", 400)
            if offset < 0:
                return Response.error("chunk offset is required", 400)
            length = int(_h(headers, "Content-Length", 0) or 0)
            if length < 0:
                return Response.error("bad Content-Length", 400)
            fresh = self.transfers.get(transfer_id)
            if length > max(int(fresh["size"]), 1) + int(fresh["chunk_size"]):
                return Response.error("chunk is far too large", 413)
            updated, action_name = self.transfers.store_chunk(
                transfer_id, offset, body, length)
            return Response.json({
                "ok": True,
                "offset": updated["offset"],
                "size": updated["size"],
                "status": updated["status"],
                "duplicate": action_name == "duplicate",
            })

        if record["source"] != viewer_id and record["dest"] != viewer_id and role != "host":
            return Response.error("not your transfer", 403)

        if action == "status":
            if method != "GET":
                return Response.error("use GET", 405)
            return Response.json({"ok": True, "transfer": self.transfers.view(record, viewer_id),
                                  "offset": record["offset"], "size": record["size"],
                                  "status": record["status"]})
        if method != "POST":
            return Response.error("use POST", 405)
        if action == "finalize":
            updated = self.transfers.finalize(transfer_id)
            return Response.json({"ok": True, "transfer": self.transfers.view(updated, viewer_id),
                                  "sha256": updated["sha256"], "status": updated["status"]})
        if action == "pause":
            return Response.json({"ok": True, "status": self.transfers.pause(transfer_id)["status"]})
        if action == "resume":
            return Response.json({"ok": True,
                                  "status": self.transfers.resume(transfer_id)["status"],
                                  "offset": self.transfers.get(transfer_id)["offset"]})
        if action == "cancel":
            return Response.json({"ok": True, "status": self.transfers.cancel(transfer_id)["status"]})
        if action == "delete":
            self.transfers.delete(transfer_id)
            return Response.json({"ok": True, "deleted": transfer_id})
        if action in ("accept", "reject"):
            if record["dest"] != viewer_id and role != "host":
                return Response.error("only the receiving device decides", 403)
            if action == "accept":
                updated = self.transfers.accept(transfer_id)
            else:
                updated = self.transfers.reject(transfer_id)
            return Response.json({"ok": True, "offer": updated["offer"],
                                  "status": updated["status"]})
        if action == "rebind":
            payload = _json_body(body, headers)
            updated = self.transfers.rebind(transfer_id, payload.get("name", ""),
                                           payload.get("size", 0))
            return Response.json({"ok": True, "offset": updated["offset"],
                                  "status": updated["status"]})
        return Response.error("not found", 404)

    # -- file serving --------------------------------------------------
    def _serve_file(self, record, device, query, headers, method, loopback):
        viewer_id = device["id"]
        if record["dest"] != viewer_id and device["role"] != "host":
            return Response.error("not your file", 403)
        target, path = self.transfers.download_target(record["id"], viewer_id)
        size = os.path.getsize(path)
        mime = target["mime"] or "application/octet-stream"
        range_header = _h(headers, "Range")
        span = security.parse_range(range_header, size)
        if range_header and span is None:
            return Response.error("unsatisfiable range", 416)

        disposition = "inline" if (security.inline_capable(mime)
                                   and _first(query, headers, ("disposition",)) != "attach") \
            else "attachment"
        filename = _quote_filename(target["filename"])
        response_headers = {
            "Content-Type": mime,
            "Content-Disposition": "%s; filename=\"%s\"; filename*=UTF-8''%s"
                                   % (disposition, filename, filename),
            "Accept-Ranges": "bytes",
            "Last-Modified": _http_date(target.get("completed_at") or target["updated_at"]),
            "ETag": '"%s"' % (target["sha256"][:32] or target["id"]),
            "Cache-Control": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-LocalDrop-Sha256": target["sha256"] or "",
        }
        if span is None:
            response_headers["Content-Length"] = str(size)
            start, end = 0, size - 1
        else:
            start, end = span
            response_headers["Content-Length"] = str(end - start + 1)
            response_headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
        if method == "HEAD":
            # Headers only: Safari uses these to size a download before fetching it.
            return Response(status=206 if span else 200, body=b"",
                            headers=response_headers, content_type=mime)
        resp = Response.file(path, response_headers)
        resp.status = 206 if span else 200
        self._track_download(resp, record["id"], start, end, size)
        return resp

    def _track_download(self, resp, transfer_id, start, end, size):
        # Counted as delivered once the bytes have actually been written; the
        # server sets `delivered_bytes` on the response when streaming finishes.
        resp.transfer_id = transfer_id
        resp.range_start = start
        resp.range_end = end
        resp.total = size

    def _open_folder(self, device, loopback, query, headers, body):
        if device["role"] != "host" or not loopback:
            return Response.error("host controls only", 403)
        payload = {}
        try:
            payload = _json_body(body, headers)
        except ValueError:
            payload = {}
        target = self.config.incoming
        transfer_id = str(payload.get("transfer", ""))
        reveal = None
        if transfer_id:
            record = self.transfers.get(transfer_id)
            stored = record.get("stored_path") or ""
            if stored and os.path.exists(stored) and security.contains(self.config.incoming,
                                                                      stored):
                reveal = stored
        if not security.contains(config_module.data_dir(), target):
            return Response.error("refusing to open that location", 403)
        try:
            import subprocess
            import sys
            if reveal:
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", "/select,", _native(reveal)])
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", "-R", reveal])
                else:
                    subprocess.Popen(["xdg-open", os.path.dirname(reveal)])
            else:
                if sys.platform == "win32":
                    os.startfile(_native(target))  # noqa: S606 - host-only, fixed path
                else:
                    subprocess.Popen(["xdg-open", target])
        except Exception as exc:  # a missing shell handler must never break the server
            return Response.error("could not open the folder: %s" % exc, 500)
        return Response.json({"ok": True})

    # -- settings ------------------------------------------------------
    EDITABLE = ("port", "chunk_size", "poll_interval_ms", "poll_interval_idle_ms",
                "device_timeout_s", "auto_accept", "max_upload_bytes", "theme",
                "parallel_uploads", "display_host", "require_pairing",
                "start_with_windows")

    def _save_settings(self, payload):
        changed = {}
        for key, value in (payload or {}).items():
            if key in self.EDITABLE:
                self.config.set(key, value)
                changed[key] = self.config.get(key)
        restart = "port" in changed
        note = ""
        if (payload or {}).get("start_with_windows") is not None:
            wanted = bool(payload["start_with_windows"])
            ok, note = startup.apply(wanted)
            if not ok:
                self.config.set("start_with_windows", not wanted)
                changed["start_with_windows"] = bool(self.config.get("start_with_windows"))
        return Response.json({"ok": True, "changed": changed, "restart_needed": restart,
                              "startup_note": note,
                              "start_with_windows": self._startup_state()})

    def _startup_state(self):
        """What Windows actually holds, not what the config file hoped for."""
        if startup.supported():
            return startup.is_enabled()
        return bool(self.config.get("start_with_windows"))


def _native(path):
    if os.name == "nt":
        return os.path.abspath(path)
    return path


def _quote_filename(name):
    from urllib.parse import quote
    safe = "".join(ch for ch in (name or "file") if ord(ch) > 31 and ch not in '"\\')
    return quote(safe or "file", safe="")


def _http_date(epoch):
    import email.utils
    return email.utils.formatdate(epoch, usegmt=True)


def _h(headers, name, default=None):
    """Case-insensitive header lookup that also works with plain dicts."""
    missing = object()
    try:
        value = headers.get(name, missing)
    except AttributeError:
        return default
    if value not in (None, missing):
        return value
    target = name.lower()
    for key, candidate in (headers.items() if hasattr(headers, "items") else []):
        if str(key).lower() == target:
            return candidate
    return default


def _first(query, headers, keys):
    for key in keys:
        value = query.get(key) if hasattr(query, "get") else None
        if value:
            return value[-1] if isinstance(value, list) else value
    for key in keys:
        value = _h(headers, "x-localdrop-" + key, "")
        if value:
            return value
    return ""


def _json_body(stream, headers):
    raw = _read_body(stream, headers)
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise ValueError("request body is not valid JSON")
    return data if isinstance(data, dict) else {}


def _read_body(stream, headers):
    length = _h(headers, "Content-Length", 0)
    try:
        length = int(length) if length else 0
    except (TypeError, ValueError):
        raise ValueError("bad Content-Length")
    if length <= 0:
        return b""
    if length > 1024 * 1024:
        raise ValueError("JSON body is too large")
    return stream.read(length)


def _version():
    from . import __version__
    return __version__
