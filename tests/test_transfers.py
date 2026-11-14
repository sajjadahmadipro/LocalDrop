"""End-to-end tests of the LocalDrop HTTP API, driven in-process.

`Application.handle()` is transport-agnostic, so these exercise the real routing,
pairing, transfer and file-serving code without a socket. `test_http.py` covers the
socket layer on top of this.

Run:  python -m unittest discover -s tests -v
"""

import errno
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from localdrop.app import Application                      # noqa: E402
from localdrop.config import Config                        # noqa: E402
from localdrop.devices import LOCAL                        # noqa: E402

IPAD_UA = ("Mozilla/5.0 (iPad; CPU OS 9_3_5 like Mac OS X) AppleWebKit/538.48 "
           "(KHTML, like Gecko) Version/9.0 Mobile/13G36 Safari/8536.25")
IPAD_IP = "192.168.1.55"


class Harness(object):
    """A private data directory, config and Application."""

    def __init__(self, data_dir=None, **settings):
        self.dir = data_dir or tempfile.mkdtemp(prefix="localdrop-test-")
        self._saved_data_dir = os.environ.get("LOCALDROP_DATA")
        os.environ["LOCALDROP_DATA"] = self.dir
        self.config = Config(os.path.join(self.dir, "config.json"))
        if not data_dir:
            self.config.set("pin", "123456")
        for key, value in settings.items():
            self.config.set(key, value)
        self.app = Application(config=self.config)

    def close(self, keep_files=False):
        if self._saved_data_dir is None:
            os.environ.pop("LOCALDROP_DATA", None)
        else:
            os.environ["LOCALDROP_DATA"] = self._saved_data_dir
        if not keep_files:
            shutil.rmtree(self.dir, ignore_errors=True)

    # -- convenience ---------------------------------------------------
    def pair(self, name="iPad", pin="123456", ip=IPAD_IP, ua=IPAD_UA):
        status, body, _ = self.request("POST", "/api/pair",
                                       {"name": name, "pin": pin}, ip=ip,
                                       headers={"User-Agent": ua})
        assert status == 200, "pairing failed: %s %s" % (status, body)
        return body["token"], body["device"]

    def host(self):
        return self.pair(name="This PC", ip="127.0.0.1",
                         ua="LocalDrop launcher")

    def upload(self, token, transfer, data, chunk=None, start=0):
        """Feed bytes to the server in chunk-sized pieces; returns the end offset."""
        step = chunk or int(self.config.get("chunk_size"))
        position = 0
        while position < len(data):
            block = data[position:position + step]
            offset = start + position
            status, body, _ = self.request(
                "PUT", "/api/transfers/%s/chunk/%d" % (transfer, offset),
                token=token, raw=block)
            assert status == 200, "chunk at %d failed: %s %s" % (offset, status, body)
            assert body["offset"] == offset + len(block), body
            position += len(block)
        return start + len(data)

    def create(self, token, name, data, dest=LOCAL, size=None):
        status, body, _ = self.request("POST", "/api/transfers", {
            "name": name, "size": len(data) if size is None else size,
            "type": "application/octet-stream", "dest": dest}, token=token)
        assert status == 201, "%s %s" % (status, body)
        return body["transfer"]["id"], body

    # -- the actual call -----------------------------------------------
    def request(self, method, path, body=None, raw=None, token=None, device=None,
                ip="127.0.0.1", headers=None, content_length=None):
        parsed = urlparse(path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        payload = b""
        if raw is not None:
            payload = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        elif body is not None:
            payload = json.dumps(body).encode("utf-8")
        sent = dict(headers or {})
        sent.setdefault("Host", "127.0.0.1:%d" % self.config.get("port"))
        sent.setdefault("User-Agent", "LocalDropTest")
        if payload or content_length is not None:
            sent["Content-Length"] = str(len(payload) if content_length is None
                                         else content_length)
        if token:
            sent["X-LocalDrop-Token"] = token
        if device:
            sent["X-LocalDrop-Device"] = device
        response = self.app.handle(method, parsed.path, query, sent,
                                   io.BytesIO(payload), ip, "127.0.0.1")
        data = response.body
        if response.stream_path:
            with open(response.stream_path, "rb") as handle:
                handle.seek(response.range_start)
                data = handle.read(response.range_end - response.range_start + 1)
        decoded = data
        if data[:1] in (b"{", b"["):
            decoded = json.loads(data.decode("utf-8"))
        return response.status, decoded, response


class PairingTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()

    def test_unpaired_session_is_rejected(self):
        status, body, _ = self.h.request("GET", "/api/state")
        self.assertEqual(status, 401)
        self.assertTrue(body["needs_pairing"])

    def test_wrong_pin_is_refused(self):
        status, body, _ = self.h.request("POST", "/api/pair",
                                        {"name": "iPad", "pin": "000000"}, ip=IPAD_IP)
        self.assertEqual(status, 403)
        self.assertNotIn("token", body)

    def test_pin_is_not_required_on_the_host_machine(self):
        status, body, _ = self.h.request("POST", "/api/pair", {"name": "PC"},
                                        ip="127.0.0.1")
        self.assertEqual(status, 200)
        self.assertEqual(body["role"], "host")

    def test_remote_device_pairs_with_pin_and_gets_remote_role(self):
        token, device = self.h.pair()
        status, body, _ = self.h.request("GET", "/api/state", token=token, device=device)
        self.assertEqual(status, 200)
        self.assertEqual(body["me"]["role"], "remote")
        self.assertNotIn("host", body, "remote sessions must not see host settings")
        self.assertNotIn("pin", json.dumps(body["me"]))

    def test_token_is_enough_and_device_must_match(self):
        token, device = self.h.pair()
        status, _, _ = self.h.request("GET", "/api/state", token=token)
        self.assertEqual(status, 200)
        status, _, _ = self.h.request("GET", "/api/state", token=token, device="d_nope")
        self.assertEqual(status, 401)

    def test_pin_rotates_and_old_code_stops_working(self):
        self.h.app.registry.pin(rotate=True)
        live = self.h.config.get("pin")
        self.assertNotEqual(live, "123456")
        status, _, _ = self.h.request("POST", "/api/pair", {"pin": "123456"}, ip=IPAD_IP)
        self.assertEqual(status, 403)
        self.h.pair(pin=live)

    def test_pairing_throttles_brute_force(self):
        for _ in range(12):
            self.h.request("POST", "/api/pair", {"pin": "111111"}, ip=IPAD_IP)
        status, body, _ = self.h.request("POST", "/api/pair", {"pin": "123456"}, ip=IPAD_IP)
        self.assertEqual(status, 429)

    def test_only_local_network_addresses_are_served(self):
        for ip in ("8.8.8.8", "203.0.113.9", "198.51.100.7"):
            status, body, _ = self.h.request("GET", "/api/hello", ip=ip)
            self.assertEqual(status, 403, ip)
        for ip in ("127.0.0.1", "192.168.1.20", "10.0.0.5", "172.16.4.9"):
            status, _, _ = self.h.request("GET", "/api/hello", ip=ip)
            self.assertEqual(status, 200, ip)

    def test_admin_endpoints_need_the_host_role(self):
        token, device = self.h.pair()
        for path in ("/api/settings", "/api/pin/rotate", "/api/server/stop",
                     "/api/history/clear"):
            method = "GET" if path == "/api/settings" else "POST"
            status, _, _ = self.h.request(method, path, token=token, device=device,
                                          body={} if method == "POST" else None)
            self.assertEqual(status, 403, path)

    def test_host_can_rename_a_device(self):
        token, _ = self.h.host()
        ipad_token, ipad = self.h.pair()
        status, body, _ = self.h.request("POST", "/api/rename",
                                         {"name": "Kitchen iPad"}, token=ipad_token,
                                         device=ipad)
        self.assertEqual(status, 200)
        self.assertEqual(body["device"]["name"], "Kitchen iPad")
        status, body, _ = self.h.request("GET", "/api/state", token=token)
        names = [d["name"] for d in body["devices"]]
        self.assertIn("Kitchen iPad", names)

    def test_unknown_path_is_404(self):
        token, _ = self.h.host()
        status, _, _ = self.h.request("GET", "/api/nonsense", token=token)
        self.assertEqual(status, 404)
        status, _, _ = self.h.request("GET", "/../../etc/passwd")
        self.assertIn(status, (404, 400))


class UploadTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness(chunk_size=65536)
        self.token, self.device = self.h.host()

    def tearDown(self):
        self.h.close()

    def test_streamed_upload_lands_in_the_inbox_verified(self):
        payload = os.urandom(200000)
        tid, _ = self.h.create(self.token, "book.pdf", payload)
        self.h.upload(self.token, tid, payload, chunk=65536)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/finalize" % tid,
                                        token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "complete")
        self.assertEqual(body["sha256"], hashlib.sha256(payload).hexdigest())
        path = os.path.join(self.h.config.incoming, "book.pdf")
        self.assertTrue(os.path.exists(path))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), payload)
        # The partial is gone; nothing is left in the temp directory.
        self.assertEqual(os.listdir(self.h.config.temp), [])

    def test_server_owns_the_offset_after_an_interrupted_chunk(self):
        payload = os.urandom(1000)
        tid, _ = self.h.create(self.token, "notes.txt", payload)
        status, body, _ = self.h.request(
            "PUT", "/api/transfers/%s/chunk/0" % tid, token=self.token,
            raw=payload[:400], content_length=700)
        self.assertEqual(status, 500)
        self.assertEqual(body["offset"], 400, "server must report what it really has")
        status, body, _ = self.h.request("GET", "/api/transfers/%s/status" % tid,
                                         token=self.token)
        self.assertEqual(body["offset"], 400)
        # The client resyncs on the confirmed offset, never on its own progress bar.
        self.h.upload(self.token, tid, payload[400:], start=400)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/finalize" % tid,
                                        token=self.token)
        self.assertEqual(body["status"], "complete")
        self.assertEqual(body["transfer"]["size"], 1000)

    def test_out_of_order_chunk_is_rejected_with_the_expected_offset(self):
        payload = b"a" * 500
        tid, _ = self.h.create(self.token, "x.bin", payload)
        status, body, _ = self.h.request("PUT", "/api/transfers/%s/chunk/200" % tid,
                                        token=self.token, raw=b"b" * 100)
        self.assertEqual(status, 409)
        self.assertEqual(body["offset"], 0, "the server states the real offset")
        self.assertTrue(body["expected"])
        self.assertEqual(body["received"], 200)
        status, body, _ = self.h.request("PUT", "/api/transfers/%s/chunk/0" % tid,
                                        token=self.token, raw=payload)
        self.assertEqual(status, 200)
        self.assertEqual(body["offset"], 500)

    def test_duplicate_chunk_is_idempotent(self):
        payload = b"c" * 300
        tid, _ = self.h.create(self.token, "x.bin", payload)
        self.h.upload(self.token, tid, payload[:200], chunk=200)
        status, body, _ = self.h.request("PUT", "/api/transfers/%s/chunk/0" % tid,
                                        token=self.token, raw=payload[:200])
        self.assertEqual(status, 200)
        self.assertTrue(body["duplicate"])
        self.assertEqual(body["offset"], 200)

    def test_chunk_beyond_declared_size_is_refused(self):
        tid, _ = self.h.create(self.token, "x.bin", b"z" * 100)
        status, body, _ = self.h.request("PUT", "/api/transfers/%s/chunk/0" % tid,
                                        token=self.token, raw=b"z" * 500)
        self.assertEqual(status, 413)

    def test_finalize_before_all_bytes_keeps_the_partial(self):
        payload = os.urandom(5000)
        tid, _ = self.h.create(self.token, "big.zip", payload)
        self.h.upload(self.token, tid, payload[:3000], chunk=3000)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/finalize" % tid,
                                        token=self.token)
        self.assertEqual(status, 409)
        self.assertEqual(body["offset"], 3000)
        partial = os.path.join(self.h.config.temp, tid + ".part")
        self.assertEqual(os.path.getsize(partial), 3000)
        status, body, _ = self.h.request("GET", "/api/transfers/%s/status" % tid,
                                        token=self.token)
        self.assertEqual(body["status"], "interrupted")
        # Recovery continues from 3000 without restarting.
        self.h.upload(self.token, tid, payload[3000:], start=3000)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/finalize" % tid,
                                        token=self.token)
        self.assertEqual(body["status"], "complete")

    def test_empty_and_oversized_files_are_refused_at_creation(self):
        status, _, _ = self.h.request("POST", "/api/transfers",
                                      {"name": "e.bin", "size": 0, "dest": LOCAL},
                                      token=self.token)
        self.assertEqual(status, 400)
        self.h.config.set("max_upload_bytes", 1024)
        status, body, _ = self.h.request("POST", "/api/transfers",
                                         {"name": "e.bin", "size": 4096, "dest": LOCAL},
                                         token=self.token)
        self.assertEqual(status, 413)

    def test_unknown_destination_is_refused(self):
        status, _, _ = self.h.request("POST", "/api/transfers",
                                      {"name": "a", "size": 10, "dest": "d_missing"},
                                      token=self.token)
        self.assertEqual(status, 400)

    def test_chunk_route_needs_a_sane_offset(self):
        tid, _ = self.h.create(self.token, "x.bin", b"1234")
        for offset in ("-1", "abc"):
            status, body, _ = self.h.request("PUT", "/api/transfers/%s/chunk/%s" % (tid, offset),
                                             token=self.token, raw=b"1234")
            self.assertEqual(status, 400, offset)
        status, _, _ = self.h.request("GET", "/api/transfers/%s/chunk/0" % tid,
                                      token=self.token)
        self.assertEqual(status, 405)

    def test_filenames_are_sanitised_and_never_overwrite(self):
        payload = b"one"
        for name, expected in (("../../windows/system32/evil.dll", "evil.dll"),
                               ("CON.txt", "_CON.txt"),
                               ("weird <>:name?.pdf", "weird -name-.pdf"),
                               (".hidden", "hidden")):
            tid, _ = self.h.create(self.token, name, payload)
            self.h.upload(self.token, tid, payload)
            status, body, _ = self.h.request("POST", "/api/transfers/%s/finalize" % tid,
                                             token=self.token)
            self.assertEqual(status, 200, body)
            self.assertEqual(body["transfer"]["name"], expected, name)
        # Same clean name twice -> no overwrite.
        tid, _ = self.h.create(self.token, "evil.dll", payload)
        self.h.upload(self.token, tid, payload)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/finalize" % tid,
                                         token=self.token)
        self.assertEqual(body["transfer"]["name"], "evil (1).dll")
        listing = os.listdir(self.h.config.incoming)
        self.assertIn("evil.dll", listing)
        self.assertIn("evil (1).dll", listing)

    def test_html_and_executables_are_downgraded(self):
        for name, mime in (("page.html", "text/plain; charset=utf-8"),
                           ("icon.svg", "text/plain; charset=utf-8"),
                           ("tool.exe", "application/octet-stream"),
                           ("book.epub", "application/epub+zip"),
                           ("book.pdf", "application/pdf")):
            tid, _ = self.h.create(self.token, name, b"x" * 10)
            status, body, _ = self.h.request("GET", "/api/transfers/%s/status" % tid,
                                             token=self.token)
            self.assertEqual(body["transfer"]["mime"], mime, name)


class ControlTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness(chunk_size=65536)
        self.token, self.device = self.h.host()

    def tearDown(self):
        self.h.close()

    def test_pause_keeps_bytes_and_resume_continues_from_them(self):
        payload = os.urandom(150000)
        tid, _ = self.h.create(self.token, "movie.mp4", payload)
        self.h.upload(self.token, tid, payload[:100000], chunk=65536)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/pause" % tid,
                                        token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "paused")
        status, body, _ = self.h.request("GET", "/api/transfers/%s/status" % tid,
                                         token=self.token)
        self.assertEqual(body["offset"], 100000, "pause must not lose progress")
        status, body, _ = self.h.request("PUT", "/api/transfers/%s/chunk/100000" % tid,
                                        token=self.token, raw=payload[100000:110000])
        self.assertEqual(status, 409, "a paused transfer refuses new data")
        self.assertEqual(body["offset"], 100000)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/resume" % tid,
                                        token=self.token)
        self.assertEqual(body["status"], "uploading")
        self.assertEqual(body["offset"], 100000)
        self.h.upload(self.token, tid, payload[100000:], start=100000)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/finalize" % tid,
                                         token=self.token)
        self.assertEqual(body["status"], "complete")

    def test_chunks_are_refused_once_finished(self):
        payload = b"hello"
        tid, _ = self.h.create(self.token, "h.txt", payload)
        self.h.upload(self.token, tid, payload)
        self.h.request("POST", "/api/transfers/%s/finalize" % tid, token=self.token)
        status, body, _ = self.h.request("PUT", "/api/transfers/%s/chunk/5" % tid,
                                        token=self.token, raw=b"more")
        self.assertEqual(status, 409)
        status, _, _ = self.h.request("POST", "/api/transfers/%s/pause" % tid,
                                      token=self.token)
        self.assertEqual(status, 409)

    def test_cancel_removes_the_partial_but_keeps_the_row(self):
        payload = os.urandom(1000)
        tid, _ = self.h.create(self.token, "gone.bin", payload)
        self.h.upload(self.token, tid, payload[:500], chunk=500)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/cancel" % tid,
                                        token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "canceled")
        self.assertFalse(os.path.exists(os.path.join(self.h.config.temp, tid + ".part")))
        status, body, _ = self.h.request("GET", "/api/transfers", token=self.token)
        ids = [t["id"] for t in body["transfers"]]
        self.assertIn(tid, ids, "canceled transfers stay visible until deleted")
        status, _, _ = self.h.request("POST", "/api/transfers/%s/delete" % tid,
                                      token=self.token)
        self.assertEqual(status, 200)
        status, body, _ = self.h.request("GET", "/api/transfers", token=self.token)
        self.assertNotIn(tid, [t["id"] for t in body["transfers"]])

    def test_delete_removes_the_stored_file(self):
        payload = b"delete me"
        tid, _ = self.h.create(self.token, "del.txt", payload)
        self.h.upload(self.token, tid, payload)
        self.h.request("POST", "/api/transfers/%s/finalize" % tid, token=self.token)
        path = os.path.join(self.h.config.incoming, "del.txt")
        self.assertTrue(os.path.exists(path))
        self.h.request("POST", "/api/transfers/%s/delete" % tid, token=self.token)
        self.assertFalse(os.path.exists(path))

    def test_rebind_needs_the_identical_file(self):
        payload = os.urandom(2048)
        tid, _ = self.h.create(self.token, "resume-me.bin", payload)
        self.h.upload(self.token, tid, payload[:1024], chunk=1024)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/rebind" % tid,
                                        {"name": "other.bin", "size": 2048},
                                        token=self.token)
        self.assertEqual(status, 409)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/rebind" % tid,
                                        {"name": "resume-me.bin", "size": 999},
                                        token=self.token)
        self.assertEqual(status, 409)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/rebind" % tid,
                                        {"name": "resume-me.bin", "size": 2048},
                                        token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["offset"], 1024)
        self.h.upload(self.token, tid, payload[1024:], start=1024)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/finalize" % tid,
                                        token=self.token)
        self.assertEqual(body["status"], "complete")


class DeviceToDeviceTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness(chunk_size=65536)
        self.host_token, self.host_device = self.h.host()
        self.ipad_token, self.ipad_device = self.h.pair()

    def tearDown(self):
        self.h.close()

    def send_to_ipad(self, name="book.epub", payload=None):
        payload = payload or os.urandom(70000)
        tid, _ = self.h.create(self.host_token, name, payload, dest=self.ipad_device)
        self.h.upload(self.host_token, tid, payload)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/finalize" % tid,
                                        token=self.host_token)
        assert status == 200, body
        return tid, payload

    def test_offer_gates_the_download_until_the_receiver_accepts(self):
        tid, payload = self.send_to_ipad()
        status, body, _ = self.h.request("GET", "/api/transfers/%s/data" % tid,
                                        token=self.ipad_token, device=self.ipad_device)
        self.assertEqual(status, 403)
        self.assertEqual(body["offer"], "pending")
        status, body, _ = self.h.request("POST", "/api/transfers/%s/accept" % tid,
                                        token=self.ipad_token, device=self.ipad_device)
        self.assertEqual(status, 200)
        self.assertEqual(body["offer"], "accepted")
        status, data, response = self.h.request("GET", "/api/transfers/%s/data" % tid,
                                               token=self.ipad_token,
                                               device=self.ipad_device)
        self.assertEqual(status, 200)
        self.assertEqual(data, payload)
        self.assertEqual(response.headers["Content-Type"], "application/epub+zip")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["X-LocalDrop-Sha256"],
                         hashlib.sha256(payload).hexdigest())
        status, body, _ = self.h.request("GET", "/api/transfers/%s/status" % tid,
                                        token=self.ipad_token)
        self.assertEqual(body["status"], "downloading")
        # The transport reports the bytes it actually wrote; that completes delivery.
        self.h.app.transfers.note_download(tid, len(payload))
        status, body, _ = self.h.request("GET", "/api/transfers/%s/status" % tid,
                                        token=self.ipad_token)
        self.assertEqual(body["status"], "complete", "delivery completes the transfer")
        self.assertEqual(body["transfer"]["dest"], self.ipad_device)

    def test_pdf_is_offered_inline_for_books(self):
        tid, _ = self.send_to_ipad("guide.pdf", b"%PDF-1.4 fake")
        self.h.request("POST", "/api/transfers/%s/accept" % tid, token=self.ipad_token,
                      device=self.ipad_device)
        status, data, response = self.h.request("GET", "/api/transfers/%s/data" % tid,
                                               token=self.ipad_token,
                                               device=self.ipad_device)
        self.assertEqual(status, 200)
        self.assertEqual(response.headers["Content-Type"], "application/pdf")
        self.assertIn("inline", response.headers["Content-Disposition"])
        self.assertIn("guide.pdf", response.headers["Content-Disposition"])

    def test_reject_deletes_the_stored_file_and_notifies_sender(self):
        tid, _ = self.send_to_ipad("nope.bin", b"junk" * 100)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/reject" % tid,
                                        token=self.ipad_token, device=self.ipad_device)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "rejected")
        self.assertEqual(os.listdir(self.h.config.incoming), [])
        status, body, _ = self.h.request("GET", "/api/transfers", token=self.host_token)
        row = [t for t in body["transfers"] if t["id"] == tid][0]
        self.assertEqual(row["offer"], "rejected")

    def test_auto_accept_skips_the_offer(self):
        self.h.config.set("auto_accept", True)
        tid, payload = self.send_to_ipad("auto.bin", b"12345")
        status, data, _ = self.h.request("GET", "/api/transfers/%s/data" % tid,
                                        token=self.ipad_token, device=self.ipad_device)
        self.assertEqual(status, 200)
        self.assertEqual(data, b"12345")

    def test_a_device_cannot_read_another_devices_file(self):
        tid, _ = self.send_to_ipad()
        other_token, other = self.h.pair(name="Phone", ip="192.168.1.77")
        status, body, _ = self.h.request("GET", "/api/transfers/%s/data" % tid,
                                        token=other_token, device=other)
        self.assertEqual(status, 403)
        status, body, _ = self.h.request("POST", "/api/transfers/%s/accept" % tid,
                                        token=other_token, device=other)
        self.assertEqual(status, 403)

    def test_remote_device_only_sees_its_own_transfers(self):
        self.send_to_ipad("mine.epub", b"x" * 10)
        other_token, other = self.h.pair(name="Phone", ip="192.168.1.77")
        tid, _ = self.h.create(other_token, "theirs.txt", b"y" * 5, dest=LOCAL)
        self.h.upload(other_token, tid, b"y" * 5)
        status, body, _ = self.h.request("GET", "/api/transfers", token=self.ipad_token,
                                        device=self.ipad_device)
        names = [t["name"] for t in body["transfers"]]
        self.assertIn("mine.epub", names)
        self.assertNotIn("theirs.txt", names)

    def test_a_device_cannot_write_into_another_devices_upload(self):
        payload = b"hello" * 20
        tid, _ = self.h.create(self.ipad_token, "ipad-file.txt", payload, dest=LOCAL)
        other_token, other = self.h.pair(name="Phone", ip="192.168.1.77")
        status, body, _ = self.h.request("PUT", "/api/transfers/%s/chunk/0" % tid,
                                        token=other_token, raw=b"x" * len(payload))
        self.assertEqual(status, 403)
        self.assertEqual(self.h.app.store.transfer(tid)["offset"], 0)
        for action in ("finalize", "cancel", "pause"):
            status, _, _ = self.h.request("POST", "/api/transfers/%s/%s" % (tid, action),
                                         token=other_token)
            self.assertEqual(status, 403, action)

    def test_incoming_offer_is_visible_in_the_receiver_state(self):
        tid, _ = self.send_to_ipad("quiet.epub", b"z" * 20)
        status, body, _ = self.h.request("GET", "/api/state", token=self.ipad_token,
                                        device=self.ipad_device)
        row = [t for t in body["transfers"] if t["id"] == tid][0]
        self.assertEqual(row["offer"], "pending")
        self.assertTrue(row["for_me"])
        self.assertEqual(row["source_name"], "This PC")
        self.assertFalse(row["downloadable"], "not downloadable before accepting")


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness(chunk_size=65536)
        self.host_token, self.host_device = self.h.host()
        self.ipad_token, self.ipad_device = self.h.pair()
        self.payload = bytes(bytearray(range(256))) * 40      # 10240 bytes
        tid, _ = self.h.create(self.host_token, "ranges.bin", self.payload,
                              dest=self.ipad_device)
        self.tid = tid
        self.h.upload(self.host_token, tid, self.payload)
        self.h.request("POST", "/api/transfers/%s/finalize" % tid, token=self.host_token)
        self.h.request("POST", "/api/transfers/%s/accept" % tid, token=self.ipad_token,
                      device=self.ipad_device)

    def tearDown(self):
        self.h.close()

    def get(self, headers=None):
        return self.h.request("GET", "/api/transfers/%s/data" % self.tid,
                             token=self.ipad_token, device=self.ipad_device,
                             headers=headers or {})

    def test_full_download(self):
        status, data, response = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(data, self.payload)
        self.assertEqual(response.headers["Accept-Ranges"], "bytes")
        self.assertEqual(response.headers["Content-Length"], str(len(self.payload)))

    def test_open_ended_range(self):
        status, data, response = self.get({"Range": "bytes=10000-"})
        self.assertEqual(status, 206)
        self.assertEqual(data, self.payload[10000:])
        self.assertEqual(response.headers["Content-Range"],
                         "bytes 10000-%d/%d" % (len(self.payload) - 1, len(self.payload)))

    def test_bounded_and_suffix_ranges(self):
        status, data, _ = self.get({"Range": "bytes=100-199"})
        self.assertEqual(data, self.payload[100:200])
        status, data, _ = self.get({"Range": "bytes=-512"})
        self.assertEqual(data, self.payload[-512:])

    def test_unsatisfiable_range(self):
        status, data, response = self.get({"Range": "bytes=999999-"})
        self.assertEqual(status, 416)

    def test_head_reports_size_without_body(self):
        status, data, response = self.h.request("HEAD", "/api/transfers/%s/data" % self.tid,
                                               token=self.ipad_token,
                                               device=self.ipad_device)
        self.assertEqual(status, 200)
        self.assertEqual(response.body, b"")
        self.assertEqual(response.headers["Content-Length"], str(len(self.payload)))
        self.assertEqual(response.headers["ETag"],
                         '"%s"' % hashlib.sha256(self.payload).hexdigest()[:32])

    def test_partial_download_progress_is_recorded(self):
        status, data, _ = self.get({"Range": "bytes=0-4095"})
        self.assertEqual(status, 206)
        self.assertEqual(len(data), 4096)
        # The transport layer reports delivered bytes; simulate it here.
        self.h.app.transfers.note_download(self.tid, 4096)
        status, body, _ = self.h.request("GET", "/api/transfers/%s/status" % self.tid,
                                        token=self.ipad_token, device=self.ipad_device)
        self.assertEqual(body["transfer"]["downloaded"], 4096)
        self.assertIn(body["status"], ("downloading", "ready"))
        self.h.app.transfers.note_download(self.tid, len(self.payload))
        status, body, _ = self.h.request("GET", "/api/transfers/%s/status" % self.tid,
                                        token=self.ipad_token, device=self.ipad_device)
        self.assertEqual(body["status"], "complete")

    def test_local_inbox_file_is_not_offered_for_download(self):
        payload = b"kept locally"
        tid, _ = self.h.create(self.host_token, "mine.txt", payload, dest=LOCAL)
        self.h.upload(self.host_token, tid, payload)
        self.h.request("POST", "/api/transfers/%s/finalize" % tid, token=self.host_token)
        status, body, _ = self.h.request("GET", "/api/transfers", token=self.host_token)
        row = [t for t in body["transfers"] if t["id"] == tid][0]
        self.assertEqual(row["status"], "complete")
        self.assertFalse(row["downloadable"])


class ReceiverPauseTest(unittest.TestCase):
    """The receiving device can hold a transfer, not just the sender."""

    def setUp(self):
        self.h = Harness(chunk_size=65536)
        self.host_token, self.host_device = self.h.host()
        self.ipad_token, self.ipad_device = self.h.pair()
        self.payload = bytes(bytearray(range(256))) * 40      # 10240 bytes
        tid, _ = self.h.create(self.host_token, "held.bin", self.payload,
                              dest=self.ipad_device)
        self.tid = tid
        self.h.upload(self.host_token, tid, self.payload)
        self.h.request("POST", "/api/transfers/%s/finalize" % tid, token=self.host_token)
        self.h.request("POST", "/api/transfers/%s/accept" % tid, token=self.ipad_token,
                      device=self.ipad_device)

    def tearDown(self):
        self.h.close()

    def receiver(self, action, **kw):
        return self.h.request("POST", "/api/transfers/%s/%s" % (self.tid, action),
                             token=self.ipad_token, device=self.ipad_device, **kw)

    def data(self, headers=None):
        return self.h.request("GET", "/api/transfers/%s/data" % self.tid,
                             token=self.ipad_token, device=self.ipad_device,
                             headers=headers or {})

    def test_pause_before_download_holds_the_file(self):
        status, body, _ = self.receiver("pause")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "paused")
        status, _, _ = self.data()
        self.assertEqual(status, 409, "a held transfer refuses downloads")
        status, body, _ = self.h.request("GET", "/api/transfers/%s/status" % self.tid,
                                        token=self.ipad_token, device=self.ipad_device)
        self.assertEqual(body["transfer"]["stage"], "download",
                         "a held file is still a download, not an upload")
        self.assertFalse(body["transfer"]["downloadable"])
        status, body, _ = self.receiver("resume")
        self.assertEqual(body["status"], "ready")
        status, data, _ = self.data()
        self.assertEqual(status, 200)
        self.assertEqual(data, self.payload)

    def test_pause_mid_download_keeps_the_bytes_already_received(self):
        status, data, _ = self.data({"Range": "bytes=0-4095"})
        self.assertEqual(status, 206)
        self.h.app.transfers.note_download(self.tid, 4096)
        status, body, _ = self.receiver("pause")
        self.assertEqual(body["status"], "paused")
        status, body, _ = self.h.request("GET", "/api/transfers/%s/status" % self.tid,
                                        token=self.ipad_token, device=self.ipad_device)
        self.assertEqual(body["transfer"]["downloaded"], 4096)
        self.receiver("resume")
        status, data, _ = self.data({"Range": "bytes=4096-"})
        self.assertEqual(status, 206)
        self.assertEqual(data, self.payload[4096:])

    def test_a_native_download_that_finishes_after_a_hold_still_completes(self):
        self.receiver("pause")
        self.h.app.transfers.note_download(self.tid, len(self.payload))
        status, body, _ = self.h.request("GET", "/api/transfers/%s/status" % self.tid,
                                        token=self.ipad_token, device=self.ipad_device)
        self.assertEqual(body["status"], "complete")

    def test_a_held_download_survives_a_server_restart(self):
        self.receiver("pause")
        data_dir = self.h.dir
        self.h.close(keep_files=True)
        h2 = Harness(data_dir=data_dir, chunk_size=65536)
        try:
            h2.app.transfers.reconcile()
            row = h2.app.store.transfer(self.tid)
            self.assertEqual(row["status"], "paused")
            self.assertTrue(row["stored_path"])
        finally:
            h2.close()


class RecoveryTest(unittest.TestCase):
    def test_transfer_survives_a_server_restart(self):
        payload = os.urandom(250000)
        digest = hashlib.sha256(payload).hexdigest()
        first_dir = tempfile.mkdtemp(prefix="localdrop-test-")
        h = Harness(data_dir=first_dir, chunk_size=65536)
        h.config.set("pin", "123456")
        token, _ = h.host()
        tid, _ = h.create(token, "restart.bin", payload)
        h.upload(token, tid, payload[:150000], chunk=65536)
        h.close(keep_files=True)

        # A brand new Application over the very same data directory.
        h2 = Harness(data_dir=first_dir, chunk_size=65536)
        try:
            changed = h2.app.transfers.reconcile()
            self.assertTrue(changed, "reconcile must repair the stored state")
            # Pairing outlives a restart: the browser does not have to re-pair.
            status, body, _ = h2.request("GET", "/api/transfers/%s/status" % tid, token=token)
            self.assertEqual(status, 200)
            status, body, _ = h2.request("GET", "/api/transfers/%s/status" % tid,
                                        token=token)
            self.assertEqual(status, 200)
            self.assertEqual(body["status"], "interrupted")
            self.assertEqual(body["offset"], 150000, "the partial bytes survived")
            status, body, _ = h2.request("POST", "/api/transfers/%s/resume" % tid,
                                        token=token)
            self.assertEqual(body["status"], "uploading")
            self.assertEqual(body["offset"], 150000)
            h2.upload(token, tid, payload[150000:], start=150000)
            status, body, _ = h2.request("POST", "/api/transfers/%s/finalize" % tid,
                                        token=token)
            self.assertEqual(status, 200)
            self.assertEqual(body["sha256"], digest)
            with open(os.path.join(h2.config.incoming, "restart.bin"), "rb") as handle:
                self.assertEqual(handle.read(), payload)
        finally:
            h2.close()

    def test_silent_sender_becomes_interrupted_not_lost(self):
        h = Harness(chunk_size=65536, device_timeout_s=5)
        try:
            token, _ = h.host()
            ipad_token, ipad = h.pair()
            payload = os.urandom(1000)
            tid, _ = h.create(ipad_token, "vanish.bin", payload, dest=LOCAL)
            h.upload(ipad_token, tid, payload[:500], chunk=500)
            self.assertEqual(h.app.store.transfer(tid)["status"], "uploading")
            h.app.store.touch_device(ipad, now=time.time() - 600)

            expired, interrupted = h.app.transfers.sweep()
            self.assertIn(ipad, expired)
            self.assertEqual([r["id"] for r in interrupted], [tid])
            status, body, _ = h.request("GET", "/api/transfers/%s/status" % tid,
                                        token=token)
            self.assertEqual(body["status"], "interrupted")
            self.assertEqual(body["offset"], 500)
            self.assertEqual(os.path.getsize(os.path.join(h.config.temp, tid + ".part")), 500)
            # The sender comes back, heartbeats and continues where it stopped.
            h.app.store.touch_device(ipad)
            h.upload(ipad_token, tid, payload[500:], start=500)
            status, body, _ = h.request("POST", "/api/transfers/%s/finalize" % tid,
                                        token=ipad_token)
            self.assertEqual(body["status"], "complete")
        finally:
            h.close()

    def test_stalled_upload_is_marked_interrupted(self):
        h = Harness(chunk_size=65536)
        try:
            token, _ = h.host()
            payload = os.urandom(1000)
            tid, _ = h.create(token, "stall.bin", payload)
            h.upload(token, tid, payload[:600], chunk=600)
            h.app.store.update_transfer(tid, updated_at=time.time() - 4000)
            expired, interrupted = h.app.transfers.sweep()
            self.assertEqual([r["id"] for r in interrupted], [tid])
            status, body, _ = h.request("GET", "/api/transfers/%s/status" % tid,
                                       token=token)
            self.assertEqual(body["status"], "interrupted")
            self.assertEqual(body["offset"], 600)
            self.assertTrue(os.path.exists(os.path.join(h.config.temp, tid + ".part")))
        finally:
            h.close()


class StorageFailureTest(unittest.TestCase):
    """What a full disk, a removed drive or a stuck copy looks like.

    The folder is sabotaged rather than the code, because that is the failure a
    person actually meets. The promise being tested is narrow and important:
    LocalDrop must say why, must not lose or invent bytes, and must finish the
    same transfer once storage works again.
    """

    def _partial(self, h, tid):
        return os.path.join(h.config.temp, tid + ".part")

    def test_a_folder_that_cannot_be_written_reports_the_offset_it_keeps(self):
        h = Harness(chunk_size=1024)
        try:
            token, _ = h.host()
            payload = os.urandom(3000)
            tid, _ = h.create(token, "disk.bin", payload)
            h.upload(token, tid, payload[:1024])
            partial = self._partial(h, tid)
            os.remove(partial)
            os.makedirs(partial)              # no open() of this path can succeed
            status, body, _ = h.request("PUT", "/api/transfers/%s/chunk/1024" % tid,
                                        raw=payload[1024:2048], token=token)
            self.assertEqual(status, 503)
            self.assertEqual(body["offset"], 1024)
            self.assertIn("Nothing already confirmed is lost", body["error"])
            row = h.app.transfers.get(tid)
            self.assertEqual(row["offset"], 1024)     # not zeroed, not guessed
            self.assertEqual(row["status"], "uploading")
            # Put the bytes back and the same upload completes in order.
            os.rmdir(partial)
            with open(partial, "wb") as handle:
                handle.write(payload[:1024])
            h.upload(token, tid, payload[1024:], start=1024)
            status, body, _ = h.request("POST", "/api/transfers/%s/finalize" % tid,
                                         token=token)
            self.assertEqual(status, 200)
            self.assertEqual(body["sha256"], hashlib.sha256(payload).hexdigest())
        finally:
            h.close()

    def test_a_write_that_fails_halfway_keeps_the_good_prefix(self):
        h = Harness(chunk_size=1024)
        try:
            token, _ = h.host()
            payload = os.urandom(3000)
            tid, _ = h.create(token, "full.bin", payload)
            h.upload(token, tid, payload[:2048])
            space_out = OSError(errno.ENOSPC, "No space left on device")
            with mock.patch("localdrop.transfers.os.fsync", side_effect=space_out):
                status, body, _ = h.request("PUT", "/api/transfers/%s/chunk/2048" % tid,
                                            raw=payload[2048:3000], token=token)
            self.assertEqual(status, 503)
            # The bytes had reached the file before the flush failed, so the
            # server reports what is really there instead of the plan.
            self.assertEqual(body["offset"], 3000)
            self.assertEqual(os.path.getsize(self._partial(h, tid)), 3000)
            row = h.app.transfers.get(tid)
            self.assertEqual(row["status"], "interrupted")
            self.assertIn("No space left", row["error"])
            # A browser that retries the same chunk is told it already has it.
            status, body, _ = h.request("PUT", "/api/transfers/%s/chunk/2048" % tid,
                                        raw=payload[2048:3000], token=token)
            self.assertEqual(status, 200)
            self.assertTrue(body["duplicate"])
            self.assertEqual(body["offset"], 3000)
            status, body, _ = h.request("POST", "/api/transfers/%s/finalize" % tid,
                                         token=token)
            self.assertEqual(status, 200)
            self.assertEqual(body["sha256"], hashlib.sha256(payload).hexdigest())
        finally:
            h.close()

    def test_a_full_inbox_does_not_report_a_completed_file(self):
        h = Harness(chunk_size=1024)
        try:
            token, _ = h.host()
            payload = os.urandom(2500)
            tid, _ = h.create(token, "notes.pdf", payload)
            h.upload(token, tid, payload)
            target = os.path.join(h.config.incoming, "notes.pdf")

            def runs_out(src, out, length=0):
                out.write(src.read(10))          # a truncated file in the inbox
                raise OSError(errno.ENOSPC, "No space left on device")

            cross_device = OSError(errno.EXDEV, "cross-device link")
            with mock.patch("localdrop.transfers.os.replace", side_effect=cross_device), \
                 mock.patch("localdrop.transfers.shutil.copyfileobj", side_effect=runs_out):
                status, body, _ = h.request("POST", "/api/transfers/%s/finalize" % tid,
                                            token=token)
            self.assertEqual(status, 507)
            self.assertIn("Make space and press Resume", body["error"])
            self.assertEqual(body["offset"], 2500)
            self.assertFalse(os.path.exists(target), "half-copied file left in the inbox")
            row = h.app.transfers.get(tid)
            self.assertEqual(row["status"], "interrupted")
            self.assertEqual(row["offset"], 2500)
            self.assertTrue(os.path.exists(self._partial(h, tid)),
                            "the only copy of the received bytes was deleted")
            self.assertEqual(row["stored_path"], "")
            # Space appears. The same transfer, no bytes re-sent.
            status, body, _ = h.request("POST", "/api/transfers/%s/finalize" % tid,
                                        token=token)
            self.assertEqual(status, 200)
            self.assertEqual(body["sha256"], hashlib.sha256(payload).hexdigest())
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), payload)
        finally:
            h.close()


class FirstRunTest(unittest.TestCase):
    """A machine that has never run LocalDrop, pointed at an empty folder."""

    def test_a_new_machine_builds_its_own_space(self):
        root = tempfile.mkdtemp(prefix="localdrop-fresh-")
        home = os.path.join(root, "nothing here yet")
        saved = os.environ.get("LOCALDROP_DATA")
        try:
            os.environ["LOCALDROP_DATA"] = home
            config = Config()
            self.assertTrue(os.path.isdir(config.incoming))
            self.assertTrue(os.path.isdir(config.temp))
            self.assertTrue(os.path.exists(os.path.join(home, "config.json")))
            app = Application(config=config)
            pin = app.registry.pin()
            self.assertEqual(len(pin), 6)
            self.assertTrue(pin.isdigit(), pin)
            request = json.dumps({"name": "iPad", "pin": pin}).encode("utf-8")
            response = app.handle("POST", "/api/pair", {},
                                  {"Content-Length": str(len(request)),
                                   "User-Agent": IPAD_UA},
                                  io.BytesIO(request), IPAD_IP, "192.168.1.42")
            self.assertEqual(response.status, 200)
            paired = json.loads(response.body.decode("utf-8"))
            self.assertEqual(len(paired["token"]), 32)
            self.assertEqual(paired["role"], "remote")
            self.assertEqual(app.store.device(paired["device"])["paired"], 1)
            second = Application(config=Config())
            self.assertEqual(second.registry.pin(), pin, "the code changed under the user")
        finally:
            if saved is None:
                os.environ.pop("LOCALDROP_DATA", None)
            else:
                os.environ["LOCALDROP_DATA"] = saved
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
