"""Resumable, verified transfers.

Protocol (all bodies are raw bytes, never multipart, so legacy Safari only needs
XMLHttpRequest + File.slice):

    POST /api/transfers                      create -> {id, offset}
    PUT  /api/transfers/<id>/chunk/<offset>  one chunk; server replies with the
                                             authoritative confirmed offset
    POST /api/transfers/<id>/finalize        verify size + SHA-256, publish file
    GET  /api/transfers/<id>/data            download, with Range support

The server always owns the confirmed byte offset; a client's progress bar is never
trusted as the source of truth.
"""

import hashlib
import os
import shutil
import threading
import time

from . import security
from .devices import LOCAL

CHUNK_WRITE = 262144
UPLOAD_STATES = ("pending", "uploading", "interrupted", "paused")
TERMINAL_STATES = ("complete", "rejected", "canceled")


class TransferError(Exception):
    def __init__(self, message, status=400, payload=None):
        Exception.__init__(self, message)
        self.status = status
        self.payload = payload or {}


class TransferStore(object):
    def __init__(self, config, store, registry, log=None):
        self.config = config
        self.store = store
        self.registry = registry
        self.log = log
        self._locks = {}
        self._locks_guard = threading.Lock()

    # -- helpers -------------------------------------------------------
    def _lock(self, transfer_id):
        with self._locks_guard:
            lock = self._locks.get(transfer_id)
            if lock is None:
                lock = threading.RLock()
                self._locks[transfer_id] = lock
        return lock

    def _partial(self, transfer_id):
        # Only ever built from a server-generated id: no user input reaches the path.
        return os.path.join(self.config.temp, transfer_id + ".part")

    def _confirmed_size(self, path, record):
        """Bytes the partial file actually holds, never more than the file size."""
        try:
            return min(int(os.path.getsize(path)), int(record["size"]))
        except OSError:
            return 0

    def _log(self, message, *args):
        if self.log:
            self.log.info(message, *args)

    def get(self, transfer_id):
        record = self.store.transfer(transfer_id)
        if not record:
            raise TransferError("no such transfer", 404)
        return record

    # -- creation ------------------------------------------------------
    def create(self, source, dest, filename, size, mime, device_name=None):
        try:
            size = int(size)
        except (TypeError, ValueError):
            raise TransferError("invalid file size")
        if size <= 0:
            raise TransferError("empty files cannot be transferred")
        limit = int(self.config.get("max_upload_bytes") or 0)
        if limit and size > limit:
            raise TransferError("file is larger than the configured limit (%d bytes)" % limit,
                               413, {"limit": limit})

        name = security.sanitize_filename(filename)
        chunk = int(self.config.get("chunk_size") or 1048576)
        tid = security.new_transfer_id()
        now = time.time()
        auto = bool(self.config.get("auto_accept"))
        offer = "accepted" if auto else "pending"
        record = self.store.insert_transfer({
            "id": tid,
            "filename": name,
            "stored_path": "",
            "size": size,
            "mime": security.media_type_for(name, mime),
            "source": source,
            "dest": dest,
            "status": "pending",
            "offer": offer,
            "offset": 0,
            "chunk_size": chunk,
            "created_at": now,
            "updated_at": now,
        })
        self._log("transfer %s created: %s (%d bytes) %s -> %s",
                  tid, name, size, source, dest)
        return record

    # -- chunk upload --------------------------------------------------
    def store_chunk(self, transfer_id, offset, stream, length):
        """Write exactly `length` bytes from `stream` at `offset`.

        Returns (record, action) where action is 'stored', 'duplicate' or raises.
        """
        length = int(length)
        offset = int(offset)
        if offset < 0 or length < 0:
            raise TransferError("negative offset")
        with self._lock(transfer_id):
            record = self.get(transfer_id)
            if record["status"] in ("complete", "rejected", "canceled"):
                raise TransferError("transfer is finished", 409,
                                    {"status": record["status"]})
            confirmed = int(record["offset"])
            if offset + length <= confirmed:
                # A retry of a chunk that is already safely on disk: acknowledge it,
                # but never undo a pause.
                if record["status"] not in ("uploading", "paused"):
                    self.store.update_transfer(transfer_id, status="uploading")
                return self.get(transfer_id), "duplicate"
            if record["status"] == "paused":
                raise TransferError("transfer is paused", 409,
                                    {"status": "paused", "offset": confirmed})
            if offset != confirmed:
                raise TransferError(
                    "expected chunk at byte %d" % confirmed, 409,
                    {"offset": confirmed, "expected": True, "received": offset})
            if offset + length > record["size"]:
                raise TransferError("chunk runs past the declared file size", 413,
                                    {"offset": confirmed, "size": record["size"]})
            written = 0
            try:
                path = self._partial(transfer_id)
                mode = "r+b" if os.path.exists(path) else "w+b"
                handle = open(path, mode)
            except OSError as exc:
                self._log("could not open the partial for %s: %s", transfer_id, exc)
                raise TransferError(
                    "LocalDrop cannot write in its storage folder (disk full, folder "
                    "removed, or the drive went away). Nothing already confirmed is lost.",
                    503, {"offset": confirmed})
            try:
                with handle:
                    handle.seek(offset)
                    remaining = length
                    while remaining > 0:
                        block = stream.read(min(CHUNK_WRITE, remaining))
                        if not block:
                            break
                        handle.write(block)
                        written += len(block)
                        remaining -= len(block)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                # A write can fail halfway, and the prefix it managed to land is
                # still good bytes. Trust the file, not the plan.
                on_disk = self._confirmed_size(path, record)
                self.store.update_transfer(transfer_id, offset=on_disk, status="interrupted",
                                           error="could not write: %s" % exc)
                self._log("write failed for %s at %d: %s", transfer_id, offset, exc)
                raise TransferError("LocalDrop could not save that chunk (%s). The bytes "
                                    "already on disk are safe." % exc, 503, {"offset": on_disk})
            if written != length:
                # Connection dropped mid-chunk: keep what landed, resync the client.
                self.store.update_transfer(transfer_id, offset=confirmed + written,
                                           status="uploading")
                raise TransferError("chunk interrupted after %d bytes" % written, 500,
                                    {"offset": confirmed + written})
            record = self.store.update_transfer(
                transfer_id, offset=confirmed + written, status="uploading",
                error="", updated_at=time.time())
            return record, "stored"

    # -- finalising ----------------------------------------------------
    def finalize(self, transfer_id):
        with self._lock(transfer_id):
            record = self.get(transfer_id)
            if record["status"] in ("canceled", "rejected"):
                raise TransferError("this transfer was stopped", 409,
                                    {"status": record["status"]})
            path = self._partial(transfer_id)
            if not os.path.exists(path):
                raise TransferError("no data was received for this transfer", 409,
                                    {"offset": 0})
            actual = os.path.getsize(path)
            if actual != record["size"]:
                self.store.update_transfer(transfer_id, offset=actual, status="interrupted",
                                           error="incomplete data")
                raise TransferError("transfer is incomplete", 409,
                                    {"offset": actual, "size": record["size"]})
            self.store.update_transfer(transfer_id, status="verifying", offset=actual)
            digest = self._sha256(path)
            # Every finished file is kept on the server until the user deletes it:
            # it is the copy the destination device pulls, and the host's inbox.
            directory = self.config.incoming
            final_name = security.unique_name(directory, record["filename"])
            final_path = os.path.join(directory, final_name)
            try:
                os.replace(path, final_path)
            except OSError:
                # Storage directories on another drive: copy then drop the partial.
                try:
                    with open(path, "rb") as src, open(final_path, "wb") as out:
                        shutil.copyfileobj(src, out, 1048576)
                    os.remove(path)
                except OSError as exc:
                    self._discard_partial_copy(path, final_path, record)
                    on_disk = self._confirmed_size(path, record)
                    self.store.update_transfer(transfer_id, offset=on_disk,
                                               status="interrupted",
                                               error="could not store the file: %s" % exc)
                    self._log("could not move %s into the inbox: %s", transfer_id, exc)
                    raise TransferError(
                        "LocalDrop received every byte but could not put the file in the "
                        "received folder (%s). Make space and press Resume: nothing has to "
                        "be sent again." % exc, 507, {"offset": on_disk})
            complete = record["dest"] == LOCAL
            now = time.time()
            record = self.store.update_transfer(
                transfer_id,
                status="complete" if complete else "ready",
                stored_path=final_path,
                sha256=digest,
                filename=final_name,
                completed_at=now if complete else 0,
                error="",
            )
            self._log("transfer %s verified (%s) -> %s", transfer_id, digest[:12], final_path)
            return record

    def _discard_partial_copy(self, path, final_path, record):
        """A failed copy must not leave a truncated file sitting in the inbox."""
        try:
            if os.path.exists(final_path) and \
                    int(os.path.getsize(final_path)) != int(record["size"]):
                os.remove(final_path)
        except OSError:
            pass

    def _sha256(self, path):
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                block = handle.read(1048576)
                if not block:
                    break
                digest.update(block)
        return digest.hexdigest()

    # -- controls ------------------------------------------------------
    def pause(self, transfer_id):
        record = self.get(transfer_id)
        if record["status"] in TERMINAL_STATES + ("verifying",):
            raise TransferError("this transfer cannot be paused", 409,
                                {"status": record["status"]})
        # The receiving device may pause too: a held transfer keeps its file and
        # refuses downloads until it is resumed.
        return self.store.update_transfer(transfer_id, status="paused")

    def resume(self, transfer_id, device_id=None):
        """Move a stopped transfer back to a resumable state, offset untouched."""
        record = self.get(transfer_id)
        if record["status"] in ("complete", "rejected", "canceled"):
            raise TransferError("this transfer is finished", 409,
                                {"status": record["status"]})
        if record["status"] in ("paused", "interrupted", "error", "pending"):
            stored = record.get("stored_path") or ""
            if (record["status"] == "paused" and record["dest"] != LOCAL
                    and stored and os.path.exists(stored)):
                record = self.store.update_transfer(transfer_id, status="ready", error="")
            else:
                record = self.store.update_transfer(transfer_id, status="uploading", error="")
        return record

    def cancel(self, transfer_id):
        record = self.get(transfer_id)
        path = self._partial(transfer_id)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        record = self.store.update_transfer(transfer_id, status="canceled", offset=0,
                                            error="canceled")
        self._log("transfer %s canceled", transfer_id)
        return record

    def delete(self, transfer_id, remove_files=True):
        record = self.get(transfer_id)
        for path in (record.get("stored_path") or "", self._partial(transfer_id)):
            if remove_files and path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        self.store.delete_transfer(transfer_id)
        with self._locks_guard:
            self._locks.pop(transfer_id, None)
        return record

    def accept(self, transfer_id):
        record = self.get(transfer_id)
        return self.store.update_transfer(transfer_id, offer="accepted", error="")

    def reject(self, transfer_id, reason="rejected by the receiving device"):
        record = self.get(transfer_id)
        path = self._partial(transfer_id)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        stored = record.get("stored_path") or ""
        if stored and os.path.exists(stored):
            try:
                os.remove(stored)
            except OSError:
                pass
        self._log("transfer %s rejected", transfer_id)
        return self.store.update_transfer(transfer_id, status="rejected", offer="rejected",
                                          error=reason)

    def rebind(self, transfer_id, filename, size):
        """Re-attach a resumed upload to a re-selected file after a page reload."""
        record = self.get(transfer_id)
        if int(size) != int(record["size"]):
            raise TransferError("that file is not the same size as the original", 409,
                                {"size": record["size"], "selected": int(size)})
        if security.sanitize_filename(filename) != record["filename"]:
            raise TransferError("that is a different file", 409,
                                {"expected": record["filename"]})
        if record["status"] in ("paused", "interrupted", "pending", "error"):
            record = self.store.update_transfer(transfer_id, status="uploading", error="")
        return record

    # -- downloads -----------------------------------------------------
    def download_target(self, transfer_id, device_id=None):
        """Path to serve, or raise. Marks the transfer as downloading."""
        record = self.get(transfer_id)
        path = record.get("stored_path") or ""
        if not path or not os.path.exists(path):
            raise TransferError("the file is not available yet", 409,
                                {"status": record["status"]})
        if record["offer"] == "pending" and device_id and record["dest"] == device_id:
            if not self.config.get("auto_accept"):
                raise TransferError("waiting for the receiving device to accept", 403,
                                    {"offer": "pending"})
            # Auto-accept was switched on after the offer was made: settle it now.
            record = self.store.update_transfer(transfer_id, offer="accepted")
        if record["status"] == "paused":
            raise TransferError("this transfer is paused", 409,
                                {"status": "paused"})
        if record["status"] in ("ready", "downloading"):
            record = self.store.update_transfer(transfer_id, status="downloading",
                                                dl_started=record.get("dl_started")
                                                or time.time())
        elif record["status"] != "complete":
            raise TransferError("this transfer cannot be downloaded", 409,
                                {"status": record["status"]})
        return record, path

    def note_download(self, transfer_id, last_byte):
        """Record how far a download reached; mark delivered once the whole file went out."""
        record = self.get(transfer_id)
        reached = max(int(record.get("dl_bytes") or 0), int(last_byte))
        fields = {"dl_bytes": reached, "dl_done": time.time()}
        if record["status"] in ("ready", "downloading", "paused") and reached >= record["size"]:
            fields.update({"status": "complete", "completed_at": time.time(),
                           "offer": "accepted"})
            self._log("transfer %s delivered to %s", transfer_id, record["dest"])
            record = self.store.update_transfer(transfer_id, **fields)
            if record["dest"] != LOCAL:
                self._drop_relay_copy(transfer_id)
            return self.get(transfer_id)
        return self.store.update_transfer(transfer_id, **fields)

    def _drop_relay_copy(self, transfer_id):
        """The staged copy exists only so the destination can pull; once every
        byte went out the server must not keep it. A copy still held open by the
        stream (Windows lock) is left for sweep() to retry."""
        record = self.get(transfer_id)
        stored = record.get("stored_path") or ""
        if not stored:
            return
        try:
            os.remove(stored)
        except OSError:
            return
        self.store.update_transfer(transfer_id, stored_path="")

    # -- housekeeping --------------------------------------------------
    def reconcile(self):
        """Called on startup: make the stored state true again."""
        changed = []
        for record in self.store.transfers():
            tid = record["id"]
            partial = self._partial(tid)
            stored = record.get("stored_path") or ""
            has_partial = os.path.exists(partial)
            has_stored = bool(stored) and os.path.exists(stored)
            if record["status"] in ("pending", "uploading", "verifying", "downloading"):
                if has_stored:
                    fields = {"status": "ready" if record["dest"] != LOCAL else "complete",
                              "offset": int(record["size"]), "error": ""}
                elif has_partial:
                    fields = {"status": "interrupted", "offset": os.path.getsize(partial),
                              "error": "interrupted by a server restart"}
                else:
                    fields = {"status": "interrupted", "offset": 0,
                              "error": "interrupted by a server restart"}
                changed.append(self.store.update_transfer(tid, **fields))
            elif record["status"] in ("canceled", "rejected"):
                continue
            elif record["status"] == "complete" and record["dest"] != LOCAL:
                continue  # delivered: the staged copy was dropped on purpose
            elif has_partial and not stored:
                changed.append(self.store.update_transfer(
                    tid, offset=os.path.getsize(partial)))
            elif has_stored:
                continue
            else:
                changed.append(self.store.update_transfer(
                    tid, status="error", error="the stored file is missing"))
        return changed

    def sweep(self, now=None):
        """Expire silent devices and mark their in-flight uploads interrupted."""
        now = time.time() if now is None else now
        expired = []
        for device in self.store.devices():
            if device["id"] == LOCAL:
                continue
            if not self.registry.online(device, now):
                if self.registry.online(device, now - 2):
                    self._log("device %s went quiet", device["name"])
                expired.append(device["id"])
        interrupted = []
        # No chunk for longer than one piece would take over a crowded 25 KB/s
        # link means the sender has stopped; large chunks get proportionally more
        # room. Being early is harmless: the next chunk resumes the status.
        stall_after = max(90.0, float(self.config.get("chunk_size")) / 25600.0)
        for record in self.store.transfers(limit=500):
            status = record["status"]
            if status in ("uploading",) and record["source"] in expired:
                interrupted.append(self.store.update_transfer(
                    record["id"], status="interrupted", error="sender disconnected"))
            elif status == "uploading" and now - record["updated_at"] > stall_after:
                interrupted.append(self.store.update_transfer(
                    record["id"], status="interrupted", error="no chunks received"))
            elif status == "downloading" and now - record["updated_at"] > 90:
                interrupted.append(self.store.update_transfer(
                    record["id"], status="ready", error=""))
            elif status == "complete" and record["dest"] != LOCAL \
                    and record.get("stored_path"):
                self._drop_relay_copy(record["id"])
        return expired, interrupted

    def _is_pc(self, device_id):
        """The PC is LOCAL plus any host-role session device. Host session ids are
        ephemeral (a relaunch re-pairs with a new id), so role, not id, marks a send
        as originating from this machine."""
        if device_id == LOCAL:
            return True
        device = self.store.device(device_id)
        return bool(device) and device.get("role") == "host"

    def visible(self, records, viewer_role, viewer_id):
        """Only expose transfers that involve the asking device. The host sees its
        own sends and the PC inbox, but never traffic between two other devices."""
        if viewer_role == "host":
            return [r for r in records
                    if r["source"] == viewer_id or r["dest"] == viewer_id
                    or self._is_pc(r["source"]) or self._is_pc(r["dest"])]
        return [r for r in records
                if r["source"] == viewer_id or r["dest"] == viewer_id]

    def views(self, records, viewer_id=None, viewer_role=None):
        names = {LOCAL: (self.registry.local_device() or {}).get("name", "This PC")}
        for device in self.registry.store.devices():
            names[device["id"]] = device["name"]
        return [self.view(record, viewer_id, names, viewer_role) for record in records]

    def view(self, record, viewer_id=None, names=None, viewer_role=None):
        if names is None:
            names = {LOCAL: (self.registry.local_device() or {}).get("name", "This PC")}
            for device in self.registry.store.devices():
                names[device["id"]] = device["name"]
        status = record["status"]
        size = int(record["size"]) or 1
        uploaded = int(record["offset"])
        downloaded = int(record.get("dl_bytes") or 0)
        mine = bool(viewer_id and record["source"] == viewer_id)
        for_me = bool(viewer_id and record["dest"] == viewer_id)
        # Stage follows the viewer, not the wire status: the sender still owns
        # its send while the receiver pulls, so ready/downloading must not
        # turn the source device's row into a download row.
        held_file = status == "paused" and bool(record.get("stored_path"))
        if status == "complete":
            stage, confirmed = "done", size
        elif status in ("ready", "downloading") or held_file:
            stage, confirmed = ("upload" if mine else "download"), downloaded
        else:
            stage, confirmed = "upload", uploaded
        # The host PC receives files addressed to LOCAL, but never prompts to
        # accept its own sends: the source guard keeps self-sends out of offers.
        if not for_me and viewer_role == "host" and record["dest"] == LOCAL \
                and record["source"] != viewer_id:
            for_me = True
        return {
            "id": record["id"],
            "name": record["filename"],
            "size": size,
            "mime": record["mime"],
            "status": status,
            "offer": record["offer"],
            "stage": stage,
            "offset": confirmed,
            "uploaded": uploaded,
            "downloaded": downloaded,
            "progress": round(min(1.0, max(0.0, confirmed / float(size))), 4),
            "source": record["source"],
            "dest": record["dest"],
            "source_name": names.get(record["source"], "Another device"),
            "dest_name": names.get(record["dest"], "This PC" if record["dest"] == LOCAL else ""),
            "mine": mine,
            "for_me": for_me,
            "created_at": round(record["created_at"], 2),
            "updated_at": round(record["updated_at"], 2),
            "completed_at": round(record.get("completed_at") or 0, 2),
            "sha256": record["sha256"],
            "error": record["error"],
            "chunk_size": record["chunk_size"],
            "downloadable": status in ("ready", "downloading", "complete")
                            and record["dest"] != LOCAL and record["offer"] != "pending"
                            and bool(record.get("stored_path")),
            "on_disk": bool(record.get("stored_path")),
        }

    def stats(self):
        rows = self.store.transfers()
        counts = {}
        on_disk = 0
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
            path = row.get("stored_path") or ""
            if path:
                try:
                    on_disk += os.path.getsize(path)
                except OSError:
                    pass
        return {"transfers": len(rows), "by_status": counts, "bytes": on_disk}
