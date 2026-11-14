"""Tests over a real HTTP/1.1 socket: keep-alive, ranges, aborts, concurrency.

These use `http.client`, which behaves like a browser at the transport level: one
connection reused for many requests, explicit Content-Length on PUT bodies and Range
on GETs. The in-process cases live in `test_transfers.py`.

Run:  python -m unittest discover -s tests -v
"""

import hashlib
import http.client
import json
import os
import sys
import threading
import unittest
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from localdrop.server import LocalDropServer                    # noqa: E402
from tests.test_transfers import Harness                        # noqa: E402

IPAD_UA = ("Mozilla/5.0 (iPad; CPU OS 9_3_5 like Mac OS X) AppleWebKit/538.48 "
           "(KHTML, like Gecko) Version/9.0 Mobile/13G36 Safari/8536.25")


class HttpCase(unittest.TestCase):
    """Boots the real threaded server on a loopback port."""

    def setUp(self):
        self.h = Harness(chunk_size=65536)
        self.server = LocalDropServer("127.0.0.1", 0, app=self.h.app)
        self.port = self.server.server_address[1]
        self.server.serve(blocking=False)
        self.tokens = {}
        self.conns = []

    def tearDown(self):
        for conn in reversed(self.conns):
            try:
                conn.close()
            except OSError:
                pass
        self.conns = []
        self.server.stop()
        self.h.close()

    # -- client helpers ------------------------------------------------
    def connect(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        # Every connection the case opens is closed in tearDown: a socket left
        # to the garbage collector makes noise, and a socket closed here while a
        # test still holds it would quietly break keep-alive instead of proving
        # it, because http.client reconnects on its own.
        self.conns.append(conn)
        return conn

    def call(self, method, path, body=None, raw=None, token=None, conn=None,
             headers=None, expect_continue=False):
        """One request; returns (status, json-or-bytes, response, connection)."""
        conn = conn or self.connect()
        payload = b""
        if raw is not None:
            payload = raw
        elif body is not None:
            payload = json.dumps(body).encode("utf-8")
        sent = dict(headers or {})
        sent.setdefault("User-Agent", IPAD_UA)
        if token:
            sent["X-LocalDrop-Token"] = token
        if payload:
            sent["Content-Length"] = str(len(payload))
        if expect_continue:
            sent["Expect"] = "100-continue"
        conn.request(method, path, body=payload if payload else None, headers=sent)
        response = conn.getresponse()
        data = response.read()
        decoded = data
        if (response.getheader("Content-Type") or "").startswith("application/json"):
            decoded = json.loads(data.decode("utf-8")) if data else {}
        return response.status, decoded, response, conn

    def pair(self, name="iPad", pin="123456"):
        status, body, _, _ = self.call("POST", "/api/pair", {"name": name, "pin": pin})
        assert status == 200, (status, body)
        self.tokens[name] = body["token"]
        return body["token"], body["device"]

    def host_token(self):
        if "host" not in self.tokens:
            token, device = self.pair(name="This PC")
            self.tokens["host"] = token
        return self.tokens["host"], None

    def put_file(self, token, name, payload, dest="local", conn=None, chunk=None):
        """Create + stream + finalize; returns (transfer id, body)."""
        step = chunk or int(self.h.config.get("chunk_size"))
        status, body, _, conn = self.call("POST", "/api/transfers", {
            "name": name, "size": len(payload), "dest": dest,
            "type": "application/octet-stream"}, token=token, conn=conn)
        assert status == 201, (status, body)
        tid = body["transfer"]["id"]
        offset = body["offset"]
        while offset < len(payload):
            block = payload[offset:offset + step]
            status, body, _, conn = self.call(
                "PUT", "/api/transfers/%s/chunk/%d" % (tid, offset), raw=block,
                token=token, conn=conn)
            assert status == 200, (offset, status, body)
            offset = body["offset"]
        status, final, _, conn = self.call("POST", "/api/transfers/%s/finalize" % tid,
                                          token=token, conn=conn)
        assert status == 200, (status, final)
        return tid, final, conn

    def put_file_rest(self, conn, token, tid, payload, start):
        """Continue an interrupted upload from the byte the server confirmed."""
        step = int(self.h.config.get("chunk_size"))
        offset = start
        while offset < len(payload):
            block = payload[offset:offset + step]
            status, body, _, conn = self.call("PUT", "/api/transfers/%s/chunk/%d" % (tid, offset),
                                             raw=block, token=token, conn=conn)
            assert status == 200, (offset, status, body)
            offset = body["offset"]
        status, final, _, conn = self.call("POST", "/api/transfers/%s/finalize" % tid,
                                          token=token, conn=conn)
        return tid, final, conn


class TransportTest(HttpCase):
    def test_keep_alives_one_connection_for_many_requests(self):
        conn = self.connect()
        try:
            for _ in range(6):
                status, body, response, conn = self.call("GET", "/api/hello", conn=conn)
                self.assertEqual(status, 200)
                self.assertEqual(response.getheader("Connection"), "keep-alive")
                self.assertIsNotNone(conn.sock, "the socket must not be replaced")
        finally:
            conn.close()

    def test_security_headers_and_server_signature(self):
        status, body, response, _ = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.getheader("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(response.getheader("Server"), "LocalDrop")
        csp = response.getheader("Content-Security-Policy") or ""
        self.assertIn("default-src 'self'", csp)
        self.assertIn("script-src 'self'", csp)

    def test_unknown_paths_and_wrong_methods_are_rejected(self):
        status, _, _, _ = self.call("GET", "/nope.txt")
        self.assertEqual(status, 404)
        status, _, _, _ = self.call("GET", "/api/state")
        self.assertEqual(status, 401)
        status, _, _, _ = self.call("POST", "/api/hello", body={})
        self.assertEqual(status, 401)

    def test_malformed_request_is_answered_and_the_socket_dropped(self):
        import socket
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            sock.sendall(b"GARBAGE\r\n\r\n")
            data = sock.recv(65536)
            self.assertIn(b"400", data.split(b"\r\n", 1)[0])
            self.assertIn(b"Connection: close", data)
            self.assertEqual(sock.recv(65536), b"", "the server must close the socket")
        finally:
            sock.close()
        # The server is still healthy for the next client.
        status, _, _, _ = self.call("GET", "/api/hello")
        self.assertEqual(status, 200)

    def test_query_string_token_authenticates(self):
        token, device = self.pair()
        status, body, _, _ = self.call("GET", "/api/state?token=%s&device=%s"
                                       % (quote(token, safe=""), device))
        self.assertEqual(status, 200)
        self.assertEqual(body["me"]["name"], "iPad")

    def test_expect_100_continue_upload(self):
        token, _ = self.host_token()
        payload = os.urandom(20000)
        tid, final, _ = self.put_file(token, "continue.bin", payload)
        self.assertEqual(final["sha256"], hashlib.sha256(payload).hexdigest())
        # And the header really is honoured on a chunk PUT.
        status, body, _, conn = self.call("POST", "/api/transfers",
                                         {"name": "c2.bin", "size": 1000, "dest": "local"},
                                         token=token)
        tid2 = body["transfer"]["id"]
        status, body, _, _ = self.call("PUT", "/api/transfers/%s/chunk/0" % tid2,
                                       raw=b"q" * 1000, token=token,
                                       expect_continue=True)
        self.assertEqual(status, 200)
        self.assertEqual(body["offset"], 1000)

    def test_bad_json_and_missing_body(self):
        token, _ = self.host_token()
        status, _, _, _ = self.call("POST", "/api/transfers", raw=b"{not json",
                                   token=token)
        self.assertEqual(status, 400)

    def test_oversized_chunk_body_is_refused(self):
        token, _ = self.host_token()
        status, body, _, _ = self.call("POST", "/api/transfers",
                                       {"name": "small.bin", "size": 10, "dest": "local"},
                                       token=token)
        tid = body["transfer"]["id"]
        status, body, _, _ = self.call("PUT", "/api/transfers/%s/chunk/0" % tid,
                                       raw=b"z" * (10 + 65536 + 1), token=token)
        self.assertEqual(status, 413)


class LargeTransferTest(HttpCase):
    def test_multi_chunk_upload_and_ranged_download(self):
        payload = os.urandom(3 * 1024 * 1024 + 1234)
        host_token, _ = self.host_token()
        ipad_token, ipad = self.pair(name="iPad 2")
        tid, final, conn = self.put_file(host_token, "video.mp4", payload, dest=ipad,
                                        chunk=1024 * 1024)
        self.assertEqual(final["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(final["transfer"]["status"], "ready")

        # Not downloadable before the receiver accepts.
        status, body, _, _ = self.call("GET", "/api/transfers/%s/data" % tid,
                                       token=ipad_token, conn=conn)
        self.assertEqual(status, 403)
        status, body, _, conn = self.call("POST", "/api/transfers/%s/accept" % tid,
                                         token=ipad_token, conn=conn)
        self.assertEqual(body["offer"], "accepted")

        status, data, response, conn = self.call("GET", "/api/transfers/%s/data" % tid,
                                                token=ipad_token, conn=conn)
        self.assertEqual(status, 200)
        self.assertEqual(len(data), len(payload))
        self.assertEqual(data, payload)
        self.assertEqual(response.getheader("Accept-Ranges"), "bytes")

        # A resumed download: Safari asks for the tail.
        tail_from = 2 * 1024 * 1024
        status, data, response, conn = self.call(
            "GET", "/api/transfers/%s/data" % tid, token=ipad_token, conn=conn,
            headers={"Range": "bytes=%d-" % tail_from})
        self.assertEqual(status, 206)
        self.assertEqual(data, payload[tail_from:])
        self.assertEqual(response.getheader("Content-Range"),
                         "bytes %d-%d/%d" % (tail_from, len(payload) - 1, len(payload)))

        # HEAD gives Safari the size before it commits to the download.
        status, data, response, _ = self.call("HEAD", "/api/transfers/%s/data" % tid,
                                             token=ipad_token)
        self.assertEqual(status, 200)
        self.assertEqual(data, b"")
        self.assertEqual(int(response.getheader("Content-Length")), len(payload))

        # The full GET delivered everything, so the transfer is complete.
        status, body, _, _ = self.call("GET", "/api/transfers/%s/status" % tid,
                                       token=ipad_token, conn=conn)
        self.assertEqual(body["status"], "complete")
        self.assertEqual(body["transfer"]["downloaded"], len(payload))

    def test_concurrent_uploads_finish_with_correct_bytes(self):
        host_token, _ = self.host_token()
        files = [("file-%d.bin" % i, os.urandom(200000 + i * 50000)) for i in range(4)]
        errors = []

        def worker(name, payload):
            try:
                conn = self.connect()
                tid, final, conn = self.put_file(host_token, name, payload, conn=conn)
                conn.close()
                assert final["sha256"] == hashlib.sha256(payload).hexdigest(), name
            except Exception as exc:          # reported through the assertion below
                errors.append("%s: %r" % (name, exc))

        threads = [threading.Thread(target=worker, args=pair) for pair in files]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        for name, payload in files:
            path = os.path.join(self.h.config.incoming, name)
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), payload, name)

    def test_state_reports_progress_while_uploading(self):
        host_token, _ = self.host_token()
        payload = os.urandom(300000)
        status, body, _, conn = self.call("POST", "/api/transfers",
                                         {"name": "progress.bin", "size": len(payload),
                                          "dest": "local"}, token=host_token)
        tid = body["transfer"]["id"]
        status, body, _, conn = self.call("PUT", "/api/transfers/%s/chunk/0" % tid,
                                         raw=payload[:150000], token=host_token, conn=conn)
        status, body, _, _ = self.call("GET", "/api/state", token=host_token, conn=conn)
        row = [t for t in body["transfers"] if t["id"] == tid][0]
        self.assertEqual(row["stage"], "upload")
        self.assertEqual(row["offset"], 150000)
        self.assertAlmostEqual(row["progress"], 0.5, places=3)
        self.assertEqual(row["status"], "uploading")


class InterruptionTest(HttpCase):
    def test_connection_drop_mid_upload_keeps_verified_bytes(self):
        host_token, _ = self.host_token()
        payload = os.urandom(400000)
        status, body, _, conn = self.call("POST", "/api/transfers",
                                         {"name": "drop.bin", "size": len(payload),
                                          "dest": "local"}, token=host_token)
        tid = body["transfer"]["id"]
        status, body, _, conn = self.call("PUT", "/api/transfers/%s/chunk/0" % tid,
                                         raw=payload[:100000], token=host_token, conn=conn)
        self.assertEqual(body["offset"], 100000)
        conn.close()                      # simulate Wi-Fi dying mid-transfer

        # A fresh connection asks the server what it actually has.
        conn = self.connect()
        status, body, _, conn = self.call("GET", "/api/transfers/%s/status" % tid,
                                         token=host_token, conn=conn)
        self.assertEqual(status, 200)
        self.assertEqual(body["offset"], 100000)
        status, body, _, conn = self.call("POST", "/api/transfers/%s/resume" % tid,
                                         token=host_token, conn=conn)
        self.assertEqual(body["offset"], 100000)
        tid, final, conn = self.put_file_rest(conn, host_token, tid, payload, 100000)
        self.assertEqual(final["sha256"], hashlib.sha256(payload).hexdigest())
        conn.close()

    def test_half_sent_chunk_is_resynced_not_corrupted(self):
        """The Wi-Fi dies inside a PUT: the server keeps only whole, verified bytes."""
        import socket
        host_token, _ = self.host_token()
        payload = os.urandom(5000)
        status, body, _, _ = self.call("POST", "/api/transfers",
                                      {"name": "half.bin", "size": len(payload),
                                       "dest": "local"}, token=host_token)
        tid = body["transfer"]["id"]
        head = ("PUT /api/transfers/%s/chunk/0 HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                "User-Agent: %s\r\nX-LocalDrop-Token: %s\r\nContent-Length: %d\r\n\r\n"
                % (tid, IPAD_UA, host_token, len(payload)))
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=20)
        try:
            sock.sendall(head.encode("latin-1") + payload[:1500])
            sock.shutdown(socket.SHUT_WR)     # the client vanishes mid-chunk
            sock.recv(4096)
        finally:
            sock.close()

        conn = self.connect()
        status, body, _, conn = self.call("GET", "/api/transfers/%s/status" % tid,
                                         token=host_token, conn=conn)
        self.assertEqual(status, 200)
        self.assertEqual(body["offset"], 1500, "only the bytes that arrived are confirmed")
        self.h.app.transfers.resume(tid)
        tid, final, conn = self.put_file_rest(conn, host_token, tid, payload, 1500)
        self.assertEqual(final["sha256"], hashlib.sha256(payload).hexdigest())
        path = os.path.join(self.h.config.incoming, "half.bin")
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), payload)
        conn.close()

    def test_download_abandoned_halfway_is_retried_from_the_start(self):
        """iOS 9's download manager owns resume; LocalDrop keeps the file ready."""
        host_token, _ = self.host_token()
        ipad_token, ipad = self.pair(name="iPad 2")
        payload = os.urandom(1500000)
        self.h.config.set("auto_accept", True)
        tid, final, _ = self.put_file(host_token, "abort.mp4", payload, dest=ipad,
                                     chunk=512 * 1024)
        conn = self.connect()
        conn.request("GET", "/api/transfers/%s/data" % tid,
                     headers={"X-LocalDrop-Token": ipad_token, "User-Agent": IPAD_UA})
        response = conn.getresponse()
        got = response.read(200000)
        conn.close()                        # Safari killed the download
        self.assertEqual(len(got), 200000)

        status, body, _, conn2 = self.call("GET", "/api/transfers/%s/status" % tid,
                                          token=ipad_token)
        self.assertIn(body["status"], ("downloading", "ready"))
        self.assertTrue(body["transfer"]["downloadable"])
        # A retry delivers the whole file again; nothing was lost.
        status, data, _, _ = self.call("GET", "/api/transfers/%s/data" % tid,
                                      token=ipad_token, conn=conn2)
        self.assertEqual(status, 200)
        self.assertEqual(data, payload)
        conn2.close()


class RestartTest(HttpCase):
    def test_paused_transfer_continues_after_the_server_restarts(self):
        host_token, _ = self.host_token()
        ipad_token, ipad = self.pair(name="iPad 2")
        payload = os.urandom(260000)
        status, body, _, conn = self.call("POST", "/api/transfers",
                                         {"name": "survivor.epub", "size": len(payload),
                                          "dest": ipad}, token=host_token)
        tid = body["transfer"]["id"]
        self.call("PUT", "/api/transfers/%s/chunk/0" % tid, raw=payload[:65536],
                  token=host_token, conn=conn)
        status, body, _, conn = self.call("POST", "/api/transfers/%s/pause" % tid,
                                         token=host_token, conn=conn)
        self.assertEqual(body["status"], "paused")
        conn.close()
        data_dir = self.h.dir
        digest = hashlib.sha256(payload).hexdigest()
        self.server.stop()
        self.h.close(keep_files=True)

        # Cold start on the same data directory, exactly like relaunching the app.
        self.h = Harness(data_dir=data_dir, chunk_size=65536)
        self.h.app.transfers.reconcile()
        self.server = LocalDropServer("127.0.0.1", 0, app=self.h.app)
        self.port = self.server.server_address[1]
        self.server.serve(blocking=False)
        self.tokens = {}

        token, _ = self.pair(name="This PC")     # re-pair: same LAN, new session
        status, body, _, conn = self.call("GET", "/api/transfers", token=token)
        rows = [t for t in body["transfers"] if t["id"] == tid]
        self.assertEqual(len(rows), 1, "the transfer is still in the queue")
        self.assertEqual(rows[0]["status"], "paused", "a pause outlives the restart")
        self.assertEqual(rows[0]["offset"], 65536)
        self.assertEqual(rows[0]["progress"], round(65536 / float(len(payload)), 4))

        # Resume, then continue from the byte the server actually holds.
        status, body, _, conn = self.call("PUT", "/api/transfers/%s/chunk/65536" % tid,
                                         raw=payload[65536:131072], token=token, conn=conn)
        self.assertEqual(status, 409, "a paused transfer refuses new data")
        status, body, _, conn = self.call("POST", "/api/transfers/%s/resume" % tid,
                                        token=token, conn=conn)
        self.assertEqual(body["status"], "uploading")
        self.assertEqual(body["offset"], 65536)
        tid, final, conn = self.put_file_rest(conn, token, tid, payload, 65536)
        self.assertEqual(final["sha256"], digest)
        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
