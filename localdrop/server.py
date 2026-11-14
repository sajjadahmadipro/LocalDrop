"""Threaded HTTP/1.1 plumbing around the Application.

HTTP/1.1 with keep-alive matters on iPad 2: fewer TCP handshakes per poll and per
chunk. Every response therefore carries an explicit Content-Length, and any request
whose body was not consumed closes the connection instead of desynchronising it.
"""

import io
import json
import os
import socket
import socketserver
import threading
import time
from urllib.parse import parse_qs, urlparse

from . import logging_util
from .app import Application, Response

READ_BLOCK = 524288
SECURITY_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "SAMEORIGIN"),
    ("Referrer-Policy", "no-referrer"),
    ("X-XSS-Protection", "1; mode=block"),
)
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; font-src 'self'; frame-ancestors 'self'; "
       "object-src 'none'; base-uri 'self'")


class Body(io.RawIOBase):
    """Bounded reader over the socket's request body, tracking what was consumed."""

    def __init__(self, raw, length):
        self.raw = raw
        self.length = length
        self.consumed = 0

    def readable(self):
        return True

    def read(self, size=-1):
        if size is None or size < 0:
            size = self.length - self.consumed
        want = min(size, max(self.length - self.consumed, 0))
        if want <= 0:
            return b""
        data = self.raw.read(want)
        if not data:
            return b""
        self.consumed += len(data)
        return data

    @property
    def remaining(self):
        return max(self.length - self.consumed, 0)


class Handler(socketserver.StreamRequestHandler):
    timeout = 300
    protocol_version = "HTTP/1.1"
    rbufsize = -1
    wbufsize = -1

    def setup(self):
        socketserver.StreamRequestHandler.setup(self)
        # Nagle holds small writes (the per-chunk acks) waiting for more data,
        # which stalls stop-and-wait transfers on WiFi. Send them immediately.
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

    # -- lifecycle -----------------------------------------------------
    def handle(self):
        self.app = self.server.app
        self.command = "GET"
        self.request_version = "HTTP/1.1"
        self.keep_alive = True
        while self.keep_alive and not self.server.stopping:
            if not self._one_request():
                break
        self._close_stream()

    def _one_request(self):
        try:
            line = self.rfile.readline(65536)
        except (OSError, socket.timeout):
            return False
        if not line:
            return False
        try:
            request = line.decode("latin-1").strip()
        except UnicodeDecodeError:
            return False
        if not request:
            return False
        parts = request.split()
        if len(parts) != 3 or not parts[0].isalpha():
            self.app.note("warning", "malformed request line from %s",
                          self.client_address[0] if self.client_address else "?")
            self.close_connection = True
            self.keep_alive = False
            self._respond(Response.error("malformed request", 400))
            return False
        method, target, version = parts
        self.command = method.upper()
        self.request_version = version if version.startswith("HTTP/") else "HTTP/1.1"
        self.close_connection = self.request_version == "HTTP/1.0"
        headers = self._read_headers()
        if headers is None:
            self.keep_alive = False
            return False
        length = _int(headers.get("Content-Length", 0))
        if length < 0 or length > (1 << 40):
            self._respond(Response.error("bad Content-Length", 400))
            self.keep_alive = False
            return False
        if "100-continue" in (headers.get("Expect") or "").lower():
            self.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")
            self.wfile.flush()
        try:
            self._dispatch(method, target, headers, length)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self._close_stream()
            return False
        except Exception as exc:
            self.app.note("error", "request %s %s failed: %s", method, _path_only(target), exc)
            try:
                self._respond(Response.error("the server could not handle that request", 500))
            except Exception:
                return False
        return self.keep_alive and not self.close_connection

    def _read_headers(self):
        headers = {}
        while True:
            line = self.rfile.readline(65536)
            if not line:
                return None
            if line in (b"\r\n", b"\n"):
                return headers
            try:
                text = line.decode("latin-1").rstrip("\r\n")
            except UnicodeDecodeError:
                return None
            name, _, value = text.partition(":")
            if not _:
                return None
            headers[name.strip()] = value.strip()

    def _dispatch(self, method, target, headers, length):
        parsed = urlparse(target)
        path = _normalise_path(parsed.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        client_ip = self.client_address[0] if self.client_address else ""
        client_host = _host_of(headers.get("Host")) or client_ip
        body = Body(self.rfile, length)
        try:
            response = self.app.handle(method, path, query, headers, body,
                                       client_ip, client_host)
        except Exception:
            raise
        if body.remaining and length <= 65536:
            body.read(body.remaining)      # small leftovers are cheap to drain
        elif body.remaining:
            self.close_connection = True   # unread bulk body: drop the connection
        self._respond(response)

    # -- output --------------------------------------------------------
    def _respond(self, response):
        if response is None:
            return
        body = response.body or b""
        head = ["%s %d %s" % (self.request_version, response.status,
                              _reason(response.status))]
        headers = dict(response.headers)
        headers.setdefault("Content-Type", response.content_type)
        if response.stream_path:
            if not _int(headers.get("Content-Length", 0)):
                try:
                    headers["Content-Length"] = str(os.path.getsize(response.stream_path))
                except OSError:
                    headers["Content-Length"] = "0"
        else:
            declared = headers.get("Content-Length")
            if self.command != "HEAD" or not declared:
                # A HEAD must still carry the size a GET would return; Safari reads
                # it to size a download before committing to it.
                headers["Content-Length"] = str(len(body))
        if self.request_version != "HTTP/1.0":
            headers.setdefault("Connection", "keep-alive" if not self.close_connection
                               else "close")
        for name, value in SECURITY_HEADERS:
            headers.setdefault(name, value)
        if headers.get("Content-Type", "").startswith("text/html"):
            headers.setdefault("Content-Security-Policy", CSP)
        headers.setdefault("Server", "LocalDrop")
        out = io.BytesIO()
        out.write(("\r\n".join(head) + "\r\n").encode("latin-1"))
        for name, value in headers.items():
            out.write(("%s: %s\r\n" % (name, value)).encode("latin-1"))
        out.write(b"\r\n")
        try:
            self.wfile.write(out.getvalue())
        except (BrokenPipeError, ConnectionResetError):
            self.keep_alive = False
            return
        if response.stream_path and self.command != "HEAD":
            self._stream(response)
        elif body and self.command != "HEAD":
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                self.keep_alive = False
                return
        else:
            try:
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                self.keep_alive = False

    def _stream(self, response):
        path = response.stream_path
        start = int(getattr(response, "range_start", 0) or 0)
        end = int(getattr(response, "range_end", 0) or 0)
        length = max(end - start + 1, 0)
        if length <= 0:
            return
        delivered = 0
        handle = None
        try:
            handle = open(path, "rb")
            if start:
                handle.seek(start)
            remaining = length
            while remaining > 0 and not self.server.stopping:
                block = handle.read(min(READ_BLOCK, remaining))
                if not block:
                    break
                self.wfile.write(block)
                self.wfile.flush()
                delivered += len(block)
                remaining -= len(block)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            self.keep_alive = False
            self.app.note("warning", "download of %s stopped after %d bytes: %s",
                          os.path.basename(path), delivered, exc)
        finally:
            if handle is not None:
                handle.close()
        transfer_id = getattr(response, "transfer_id", None)
        if transfer_id and delivered:
            try:
                self.app.transfers.note_download(transfer_id, start + delivered)
            except Exception as exc:
                self.app.note("error", "could not record download progress: %s", exc)

    def _close_stream(self):
        try:
            self.wfile.flush()
        except Exception:
            pass

    # -- logging -------------------------------------------------------
    def log_message(self, format, *args):
        if self.app and getattr(self.app, "log", None):
            try:
                self.app.log.debug("http: " + format, *args)
            except Exception:
                pass


def _normalise_path(path):
    if not path:
        return "/"
    path = path.replace("\\", "/")
    while "//" in path:
        path = path.replace("//", "/")
    return path


def _path_only(target):
    return target.split("?")[0]


def _host_of(value):
    if not value:
        return ""
    value = value.strip()
    if value.startswith("["):          # IPv6 literal
        return value.split("]")[0].strip("[")
    return value.split(":")[0]


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


REASONS = {200: "OK", 201: "Created", 206: "Partial Content", 304: "Not Modified",
           400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
           405: "Method Not Allowed", 409: "Conflict", 413: "Payload Too Large",
           416: "Requested Range Not Satisfiable", 429: "Too Many Requests",
           500: "Internal Server Error", 503: "Service Unavailable",
           507: "Insufficient Storage"}


def _reason(status):
    return REASONS.get(status, "OK" if 200 <= status < 300 else "Error")


class LocalDropServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    address_family = socket.AF_INET

    def __init__(self, host, port, app=None, log=None):
        self.app = app or Application(log=log)
        self.stopping = False
        self.host = host
        self.port = port
        self.sweeper = None
        self.poll_thread = None
        socketserver.ThreadingTCPServer.__init__(self, (host, port), Handler)
        # Binding port 0 lets the OS choose; everything the devices are told
        # afterwards has to report the socket that actually exists.
        self.port = self.server_address[1]
        self.app.server = self

    # -- background work ----------------------------------------------
    def start_sweeper(self, interval=3.0):
        stop = threading.Event()
        self._sweep_stop = stop

        def loop():
            log = logging_util.get_logger("sweeper")
            while not stop.wait(interval):
                try:
                    expired, interrupted = self.app.transfers.sweep()
                    if interrupted:
                        log.info("%d transfer(s) marked recoverable", len(interrupted))
                except Exception as exc:
                    log.error("sweeper failed: %s", exc)

        thread = threading.Thread(target=loop, name="localdrop-sweep")
        thread.daemon = True
        thread.start()
        self.sweeper = thread
        return thread

    def stop_sweeper(self):
        stop = getattr(self, "_sweep_stop", None)
        if stop is not None:
            stop.set()

    def shutdown_from_request(self):
        threading.Thread(target=self.shutdown, name="localdrop-stop").start()

    def serve(self, blocking=True):
        self.start_sweeper()
        if blocking:
            try:
                self.serve_forever(poll_interval=0.3)
            finally:
                self.stop_sweeper()
        else:
            thread = threading.Thread(target=self.serve_forever, args=(0.2,),
                                      name="localdrop-http")
            thread.daemon = True
            thread.start()
            self.poll_thread = thread
        return self

    def stop(self):
        self.stopping = True
        try:
            self.shutdown()
        except Exception:
            pass
        self.stop_sweeper()
        try:
            self.server_close()
        except Exception:
            pass


def run(host=None, port=None, log=None, on_ready=None):
    """Console mode: build the server from config and block until stopped."""
    log = log or logging_util.configure(level="info")
    app = Application(log=log)
    config = app.config
    port = int(port or config.get("port"))
    host = host or config.get("bind")
    app.transfers.reconcile()
    try:
        server = LocalDropServer(host, port, app=app, log=log)
    except OSError as exc:
        log.error("cannot listen on %s:%s - %s", host, port, exc)
        raise
    if on_ready:
        on_ready(server, app)
    log.info("LocalDrop listening on port %d", server.port)
    try:
        server.serve(blocking=True)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return server
