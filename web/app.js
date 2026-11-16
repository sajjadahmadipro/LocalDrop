/* LocalDrop client.
 *
 * Legacy Safari first: ES5 syntax only, XMLHttpRequest (no fetch, no Promise, no
 * modules), File.slice for chunked uploads, HTTP polling for presence. Every byte
 * count shown here is reconciled against the server's confirmed offset, because a
 * browser progress bar is not a source of truth.
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------------ dom */
  function $(id) { return document.getElementById(id); }
  function on(el, ev, fn) { if (el) { el.addEventListener(ev, fn, false); } }
  function has(el, name) {
    return (" " + el.className + " ").indexOf(" " + name + " ") >= 0;
  }
  function addClass(el, name) { if (!has(el, name)) { el.className = (el.className + " " + name).replace(/^\s+/, ""); } }
  function drop(el, name) {
    el.className = (" " + el.className + " ").replace(" " + name + " ", " ").replace(/^\s+|\s+$/g, "");
  }
  function toggle(el, name, yes) { if (yes) { addClass(el, name); } else { drop(el, name); } }
  function show(el, yes) { toggle(el, "hidden", !yes); }
  function text(el, value) { if (el && el.innerHTML !== value) { el.innerHTML = value; } }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* --------------------------------------------------------------- icons */
  /* A small local set drawn in a rounded line style (1.8 stroke, round caps).
     No external icon font or sprite: a phone on a dead Wi-Fi still gets them. */
  var ICON_PATHS = {
    send: '<path d="M21 3 10.5 13.5M21 3l-6.8 18-3.7-7.5L3 9.8z"/>',
    download: '<path d="M12 3v11M8 10.5l4 4 4-4"/><path d="M4 16v2.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V16"/>',
    inbox: '<path d="M12 3v10M8 9.5l4 4 4-4"/><path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    pause: '<path d="M9 5v14M15 5v14"/>',
    play: '<path d="M7 4.5v15l12-7.5z"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    check: '<path d="M4.5 12.5l5 5 10-11"/>',
    trash: '<path d="M4 7h16M9.5 7V5a1 1 0 0 1 1-1h3a1 1 0 0 1 1 1v2M6.5 7l1 12a1.5 1.5 0 0 0 1.5 1.4h6a1.5 1.5 0 0 0 1.5-1.4l1-12"/>',
    folder: '<path d="M3.5 6.5A1.5 1.5 0 0 1 5 5h4l2 2.5h8A1.5 1.5 0 0 1 20.5 9v8.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-1.6 5.7M20 5v6h-6"/>',
    laptop: '<rect x="4" y="5" width="16" height="10.5" rx="1.6"/><path d="M2.5 19h19"/>',
    tablet: '<rect x="5" y="2.8" width="14" height="18.4" rx="2.4"/><path d="M10.5 18.4h3"/>',
    phone: '<rect x="6.5" y="2.8" width="11" height="18.4" rx="2.4"/><path d="M10.8 18.4h2.4"/>',
    book: '<path d="M4 4.5h6.5v15H4zM13.5 4.5H20v15h-6.5z"/>',
    image: '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M7 16l4-5 3 3 2-2 1.5 4"/><circle cx="9" cy="9.5" r="1.3"/>',
    doc: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
    zip: '<path d="M5.5 6h13v14h-13z"/><path d="M10 3h4v3h-4zM10 8h4v3h-4zM10 13h4v3h-4z"/>',
    video: '<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M11 9.5l4 2.5-4 2.5z"/>',
    music: '<path d="M9 18V6l10-2v12"/><circle cx="7" cy="18" r="2.3"/><circle cx="17" cy="16" r="2.3"/>',
    file: '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4"/>',
    arrowup: '<path d="M12 19V5M6 11l6-6 6 6"/>',
    arrowdown: '<path d="M12 5v14M6 13l6 6 6-6"/>'
  };
  function icon(name, size) {
    var body = ICON_PATHS[name] || ICON_PATHS.file;
    return '<svg viewBox="0 0 24 24" width="' + size + '" height="' + size +
           '" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" ' +
           'stroke-linecap="round" stroke-linejoin="round">' + body + "</svg>";
  }

  /* -------------------------------------------------------------- formats */
  function bytes(n) {
    n = Number(n) || 0;
    if (n < 1024) { return n + " B"; }
    var u = ["KB", "MB", "GB", "TB"], i = -1, v = n;
    do { v /= 1024; i++; } while (v >= 1024 && i < u.length - 1);
    return (v >= 100 ? Math.round(v) : Math.round(v * 10) / 10) + " " + u[i];
  }
  function speed(bps) { return bytes(bps) + "/s"; }
  function eta(seconds) {
    if (!isFinite(seconds) || seconds <= 0) { return ""; }
    if (seconds < 60) { return Math.round(seconds) + "s"; }
    var m = Math.floor(seconds / 60), s = Math.round(seconds % 60);
    if (m < 60) { return m + "m " + s + "s"; }
    return Math.floor(m / 60) + "h " + (m % 60) + "m";
  }
  function ago(seconds) {
    if (seconds < 45) { return "just now"; }
    if (seconds < 90) { return "a minute ago"; }
    if (seconds < 3600) { return Math.round(seconds / 60) + " minutes ago"; }
    if (seconds < 7200) { return "an hour ago"; }
    return Math.round(seconds / 3600) + " hours ago";
  }

  /* -------------------------------------------------------------- storage */
  var memory = {};
  var storage = (function () {
    try {
      var probe = "__ld";
      window.localStorage.setItem(probe, "1");
      window.localStorage.removeItem(probe);
      return window.localStorage;
    } catch (e) { return null; }             // Safari private mode throws on write
  })();
  function getItem(k) { return storage ? storage.getItem(k) : (memory[k] || null); }
  function setItem(k, v) { if (storage) { try { storage.setItem(k, v); } catch (e) { memory[k] = v; } } else { memory[k] = v; } }
  function delItem(k) { if (storage) { try { storage.removeItem(k); } catch (e) {} } delete memory[k]; }

  var K_TOKEN = "localdrop.token", K_DEVICE = "localdrop.device",
      K_NAME = "localdrop.name", K_THEME = "localdrop.theme";

  /* ------------------------------------------------------------- session */
  var S = {
    token: getItem(K_TOKEN) || "",
    device: getItem(K_DEVICE) || "",
    me: null,
    devices: [],
    transfers: [],
    server: null,
    host: null,
    reachable: false,
    misses: 0,
    timer: null,
    screen: "boot",
    target: "",
    picked: [],
    selected: {}
  };

  function isHost() { return !!(S.me && S.me.role === "host"); }
  function isTouch() { return has(document.body, "touch"); }
  function parallel() {
    var n = S.server ? parseInt(S.server.parallel_uploads, 10) : 2;
    return (n > 0 && n <= 8) ? n : 2;
  }
  function chunkSize() {
    var n = S.server ? parseInt(S.server.chunk_size, 10) : 1048576;
    return (n > 1024 && n <= 33554432) ? n : 1048576;
  }

  /* ------------------------------------------------------------- network */
  var BASE = "";   // same origin

  function headersFor(xhr, json) {
    if (S.token) { xhr.setRequestHeader("X-LocalDrop-Token", S.token); }
    if (S.device) { xhr.setRequestHeader("X-LocalDrop-Device", S.device); }
    if (json) { xhr.setRequestHeader("Content-Type", "application/json"); }
  }

  // cb(err, status, data)  - err is truthy for network failures ("net") or parse problems
  function api(method, path, body, cb, timeoutMs) {
    var xhr = new XMLHttpRequest();
    xhr.open(method, BASE + path, true);
    xhr.timeout = timeoutMs || 20000;
    headersFor(xhr, body !== null && body !== undefined);
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) { return; }
      if (!xhr.status) { cb("net", 0, null); return; }
      var raw = xhr.responseText || "";
      var data = null;
      if (raw) {
        try { data = JSON.parse(raw); }
        catch (e) { cb("bad response from the server", xhr.status, null); return; }
      }
      cb(xhr.status >= 400 ? (data && data.error) || ("http " + xhr.status) : null,
         xhr.status, data);
    };
    xhr.onerror = function () { cb("net", 0, null); };
    xhr.ontimeout = function () { cb("net", 0, null); };
    try {
      xhr.send(body === null || body === undefined ? null : JSON.stringify(body));
    } catch (e) { cb(String(e), 0, null); }
    return xhr;
  }

  /* --------------------------------------------------------- file slicing */
  var slicer = window.Blob && Blob.prototype.slice ? Blob.prototype.slice
             : (window.File && File.prototype.slice ? File.prototype.slice : null)
             || (window.Blob && Blob.prototype.webkitSlice)
             || (window.File && File.prototype.webkitSlice)
             || (window.Blob && Blob.prototype.mozSlice)
             || null;

  function slice(file, from, to) {
    return slicer ? slicer.call(file, from, to) : null;
  }
  function canSlice() { return !!slicer; }

  var sendBlobOk = true;

  // Safari 9 sends a Blob body natively. If an older engine refuses, fall back to
  // reading the slice into memory and sending that instead.
  function sendBody(xhr, part, cb) {
    if (sendBlobOk) {
      try { xhr.send(part); return; }
      catch (e) { sendBlobOk = false; }
    }
    if (!window.FileReader) { cb("this browser cannot read files for upload"); return; }
    var reader = new FileReader();
    reader.onerror = function () { cb("could not read that part of the file"); };
    reader.onload = function () {
      try { xhr.send(reader.result); }
      catch (e2) { cb(String(e2)); }
    };
    reader.readAsArrayBuffer(part);
  }

  // cb(err, {offset, status})
  function putChunk(tid, offset, file, length, cb) {
    var part = slice(file, offset, Math.min(offset + length, file.size));
    if (!part) { cb("this browser cannot read the file in pieces"); return null; }
    var xhr = new XMLHttpRequest();
    var settled = false;
    xhr.open("PUT", BASE + "/api/transfers/" + encodeURIComponent(tid) +
             "/chunk/" + offset, true);
    headersFor(xhr, false);
    xhr.timeout = 0;
    function finish(err, status, data) {
      if (settled) { return; }
      settled = true;
      cb(err, status, data);
    }
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) { return; }
      if (!xhr.status) { finish("net", 0, null); return; }
      var data = null;
      try { data = xhr.responseText ? JSON.parse(xhr.responseText) : {}; }
      catch (e) { finish("bad chunk reply", xhr.status, null); return; }
      finish(xhr.status >= 400 ? (data.error || ("http " + xhr.status)) : null,
             xhr.status, data);
    };
    xhr.onerror = function () { finish("net", 0, null); };
    xhr.ontimeout = function () { finish("net", 0, null); };
    xhr.onabort = function () { finish("aborted", 0, null); };
    if (xhr.upload) {
      xhr.upload.onprogress = function (e) {
        var job = up[tid];
        if (e.lengthComputable && job) { job.progressAcked(offset + e.loaded, e.length); }
      };
    }
    sendBody(xhr, part, finish);
    return xhr;
  }

  /* --------------------------------------------------- upload state machine */
  var up = {};       // transferId -> Uploader
  var upCount = function () {
    var n = 0, id;
    for (id in up) { if (up.hasOwnProperty(id) && up[id].busy) { n++; } }
    return n;
  };

  function Uploader(rec, file) {
    this.tid = rec.id;
    this.file = file;
    this.name = rec.name;
    this.size = rec.size;
    this.offer = rec.offer;
    this.offset = rec.offset || 0;
    this.sent = this.offset;      // bytes the browser has pushed at the server
    this.chunk = chunkSize();
    this.busy = false;
    this.paused = false;
    this.closing = false;
    this.done = false;
    this.retrying = 0;
    this.waiting = 0;
    this.xhr = null;
    this.samples = [];
    this.acked = null;
    up[this.tid] = this;
  }

  Uploader.prototype.progressAcked = function (bytesSent, total) {
    this.sent = Math.max(this.sent || 0, bytesSent);
    paintProgress(this.tid, this.sent, total || this.size);
  };

  Uploader.prototype.confirmed = function (offset) {
    this.offset = offset;
    if (offset >= this.sent) { this.sent = offset; }
    this.sample(offset);
  };

  Uploader.prototype.sample = function (offset) {
    var now = new Date().getTime();
    this.samples.push([now, offset]);
    while (this.samples.length > 2 && now - this.samples[0][0] > 3000) { this.samples.shift(); }
    if (this.samples.length > 64) { this.samples.shift(); }
  };

  Uploader.prototype.rate = function () {
    var s = this.samples;
    if (s.length < 2) { return 0; }
    var first = s[0], last = s[s.length - 1];
    var dt = (last[0] - first[0]) / 1000;
    if (dt <= 0.4) { return 0; }
    var db = last[1] - first[1];
    return db > 0 ? db / dt : 0;
  };

  Uploader.prototype.pump = function () {
    if (this.busy || this.done || this.paused || this.closing) { return; }
    if (this.waiting) { return; }
    if (this.offset >= this.size) { this.finalize(); return; }
    var self = this;
    this.busy = true;
    this.xhr = putChunk(this.tid, this.offset, this.file, this.chunk,
      function (err, status, data) {
        self.busy = false;
        self.xhr = null;
        if (err === "aborted") { return; }
        if (!err) {
          self.retrying = 0;
          self.confirmed(data && typeof data.offset === "number" ? data.offset
                                                                : self.offset);
          runQueue();
          paintSoon();
          return;
        }
        if (status === 409 && data && typeof data.offset === "number") {
          self.confirmed(data.offset);
          if (data.status === "paused") { self.paused = true; paintSoon(); return; }
          if (data.status === "complete") { self.finish(); return; }
          runQueue();
          paintSoon();
          return;
        }
        if (status === 401) { lostSession(); return; }
        if (status === 403 || status === 404 || status === 413) {
          self.fail(err);
          return;
        }
        self.retry(err);
      });
  };

  Uploader.prototype.retry = function (why) {
    var self = this;
    this.retrying = (this.retrying || 0) + 1;
    this.lastError = why === "net" ? "network interrupted" : why;
    var delay = Math.min(30000, 1000 * Math.pow(2, Math.min(this.retrying, 5)));
    this.waiting = delay / 1000;
    markInterrupted(this.tid, this.lastError);
    var tick = window.setInterval(function () {
      self.waiting -= 1;
      if (self.waiting <= 0) { window.clearInterval(tick); }
      paintSoon();
    }, 1000);
    window.setTimeout(function () {
      window.clearInterval(tick);
      self.waiting = 0;
      if (!self.paused && !self.done && !self.closing) { runQueue(); }
      paintSoon();
    }, delay);
  };

  Uploader.prototype.finalize = function () {
    var self = this;
    this.busy = true;
    api("POST", "/api/transfers/" + encodeURIComponent(this.tid) + "/finalize", {},
      function (err, status, data) {
        self.busy = false;
        if (err && status === 409 && data && typeof data.offset === "number") {
          self.confirmed(data.offset);
          runQueue();
          return;
        }
        if (err) {
          if (status === 401) { lostSession(); return; }
          self.fail(err);
          return;
        }
        self.finish();
        note("Sent " + self.name);
        poll(true);
      }, 120000);
  };

  Uploader.prototype.finish = function () {
    this.done = true;
    delete up[this.tid];
    runQueue();
    paintSoon();
  };

  Uploader.prototype.fail = function (why) {
    this.done = true;
    this.paused = true;
    delete up[this.tid];
    markInterrupted(this.tid, why);
    note("Could not send " + this.name + ": " + why, true);
    paintSoon();
  };

  function uploaderFor(tid) { return up[tid] || null; }

  // While a send sits held for the receiver's Accept, re-check far faster than the
  // normal poll cadence, so tapping Accept starts the transfer almost at once.
  var gateTimer = null;
  function gatePoll(held) {
    if (!held) {
      if (gateTimer) { window.clearTimeout(gateTimer); gateTimer = null; }
      return;
    }
    if (gateTimer) { return; }
    gateTimer = window.setTimeout(function () {
      gateTimer = null;
      poll(true);
    }, 800);
  }

  function runQueue() {
    var limit = parallel(), id, job, rec, held = 0, slots = limit - upCount();
    for (id in up) {
      if (!up.hasOwnProperty(id)) { continue; }
      job = up[id];
      if (job.busy || job.paused || job.done || job.waiting) { continue; }
      // Sending waits for the receiver's Accept: a pending offer holds its chunks.
      rec = transferById(id);
      if (rec) { job.offer = rec.offer; }
      if (job.offer === "pending") { held++; continue; }
      if (slots <= 0) { continue; }
      job.pump();
      slots--;
    }
    gatePoll(held > 0);
  }

  // The receiver can pause or resume an upload too. When a resume lands on the
  // server while this browser's uploader is still locally paused, follow it.
  function syncUploadJobs() {
    var id, job, rec;
    for (id in up) {
      if (!up.hasOwnProperty(id)) { continue; }
      job = up[id];
      if (job.done || job.closing || !job.paused) { continue; }
      rec = transferById(id);
      if (rec && rec.offer !== "pending" &&
          (rec.status === "uploading" || rec.status === "pending")) {
        job.paused = false;
        job.retrying = 0;
      }
    }
  }

  /* --------------------------------------------------------- upload start */
  function beginUpload(rec, file) {
    return new Uploader(rec, file);
  }

  function reselect(tid) {
    var input = document.createElement("input");
    input.type = "file";
    input.style.position = "fixed";
    input.style.left = "-1000px";
    input.style.top = "0";
    document.body.appendChild(input);
    input.onchange = function () {
      var file = input.files && input.files[0];
      if (input.parentNode) { input.parentNode.removeChild(input); }
      if (!file) { return; }
      attach(tid, file);
    };
    input.click();
  }

  function attach(tid, file) {
    var row = transferById(tid);
    if (!row) { return; }
    if (!canSlice()) {
      note("This browser cannot send files in pieces. Try a newer browser.", true);
      return;
    }
    api("POST", "/api/transfers/" + encodeURIComponent(tid) + "/rebind",
      { name: file.name, size: file.size }, function (err, status, data) {
        if (err) {
          if (status === 401) { lostSession(); return; }
          note((data && data.error) || "That is not the same file.", true);
          return;
        }
        beginUpload({ id: tid, name: row.name, size: row.size, offset: data.offset || 0, offer: row.offer }, file);
        note("Resumed " + row.name + " from " + bytes(data.offset || 0));
        runQueue();
        poll(true);
      });
  }

  function sendFiles(list) {
    var files = [], i;
    if (!list || !list.length) { return; }
    if (!S.target) { note("Choose a device first.", true); return; }
    if (!canSlice()) {
      note("This browser cannot read files in chunks, so LocalDrop cannot resume an " +
           "interrupted send. It is a limitation of the browser, not of your network.", true);
      return;
    }
    var limit = S.server ? parseInt(S.server.max_upload_bytes, 10) : 0;
    for (i = 0; i < list.length; i++) {
      var f = list[i];
      if (!f.size) { note("Skipped " + f.name + " (it is empty).", true); continue; }
      if (limit && f.size > limit) {
        note("Skipped " + f.name + ": larger than the limit set on the server.", true);
        continue;
      }
      files.push(f);
    }
    if (!files.length) { return; }
    var target = S.target;
    var next = function (index) {
      if (index >= files.length) {
        S.picked = [];
        renderPicked();
        runQueue();
        poll(true);
        return;
      }
      var file = files[index];
      api("POST", "/api/transfers", {
        name: file.name, size: file.size,
        type: file.type || "", dest: target
      }, function (err, status, data) {
        if (err) {
          if (status === 401) { lostSession(); return; }
          note("Could not queue " + file.name + ": " + ((data && data.error) || err), true);
          next(index + 1);
          return;
        }
        beginUpload({
          id: data.transfer.id, name: data.transfer.name,
          size: data.transfer.size, offset: data.offset || 0,
          offer: data.transfer.offer
        }, file);
        next(index + 1);
      });
    };
    note("Sending " + files.length + " file" + (files.length > 1 ? "s" : "") + "\u2026");
    next(0);
  }

  /* ------------------------------------------------- download state machine */
  /* Where the browser can save a Blob (a[download] + object URL), the receiver
     pulls the file in Range chunks so it can show real progress and pause. Old
     Safari (iOS 9) cannot save a Blob, so it gets the native download link and
     an honest note instead. */
  var down = {};     // transferId -> Downloader
  var autoStart = {};   // transferId -> 1 after the receiver accepts, until it auto-starts
  var saveBlobOk = null;
  function canSaveBlob() {
    if (saveBlobOk === null) {
      var a = document.createElement("a");
      saveBlobOk = ("download" in a) && !!window.URL && !!window.URL.createObjectURL && !!window.Blob;
    }
    return saveBlobOk;
  }
  function jsDownloadLimit() { return isTouch() ? 536870912 : 2147483648; }

  function Downloader(rec) {
    this.tid = rec.id;
    this.name = rec.name;
    this.size = rec.size;
    this.mime = rec.mime || "application/octet-stream";
    this.received = 0;
    this.parts = [];
    this.chunk = chunkSize();
    this.busy = false;
    this.paused = false;
    this.done = false;
    this.waiting = 0;
    this.retrying = 0;
    this.xhr = null;
    this.samples = [];
    down[this.tid] = this;
  }

  Downloader.prototype.sample = function (offset) {
    var now = new Date().getTime();
    this.samples.push([now, offset]);
    while (this.samples.length > 2 && now - this.samples[0][0] > 3000) { this.samples.shift(); }
    if (this.samples.length > 64) { this.samples.shift(); }
  };

  Downloader.prototype.rate = function () {
    var s = this.samples;
    if (s.length < 2) { return 0; }
    var dt = (s[s.length - 1][0] - s[0][0]) / 1000;
    if (dt <= 0.4) { return 0; }
    var db = s[s.length - 1][1] - s[0][1];
    return db > 0 ? db / dt : 0;
  };

  Downloader.prototype.pump = function () {
    if (this.busy || this.done || this.paused || this.waiting) { return; }
    if (this.received >= this.size) { this.save(); return; }
    var self = this;
    var end = Math.min(this.received + this.chunk, this.size) - 1;
    var xhr = new XMLHttpRequest();
    xhr.open("GET", BASE + "/api/transfers/" + encodeURIComponent(this.tid) + "/data", true);
    xhr.responseType = "arraybuffer";
    headersFor(xhr, false);
    xhr.setRequestHeader("Range", "bytes=" + this.received + "-" + end);
    xhr.timeout = 0;
    this.busy = true;
    this.xhr = xhr;
    xhr.onabort = function () { self.busy = false; self.xhr = null; };
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) { return; }
      self.busy = false;
      self.xhr = null;
      if (self.paused || self.done) { return; }
      if (!xhr.status) { self.retry("network interrupted"); return; }
      if (xhr.status === 401) { lostSession(); return; }
      if (xhr.status === 409) { self.paused = true; paintSoon(); poll(true); return; }
      if (xhr.status !== 200 && xhr.status !== 206) { self.retry("http " + xhr.status); return; }
      var buf = xhr.response;
      if (!buf || !buf.byteLength) { self.retry("empty reply"); return; }
      self.retrying = 0;
      self.parts.push(buf);
      self.received += buf.byteLength;
      self.sample(self.received);
      paintProgress(self.tid, self.received, self.size);
      paintSoon();
      if (self.received >= self.size) { self.save(); return; }
      self.pump();
    };
    xhr.onerror = function () {
      self.busy = false; self.xhr = null;
      if (!self.paused && !self.done) { self.retry("network interrupted"); }
    };
    try { xhr.send(null); }
    catch (e) { this.busy = false; this.xhr = null; this.retry(String(e)); }
  };

  Downloader.prototype.retry = function (why) {
    var self = this;
    this.retrying = (this.retrying || 0) + 1;
    var delay = Math.min(30000, 1000 * Math.pow(2, Math.min(this.retrying, 5)));
    this.waiting = delay / 1000;
    var tick = window.setInterval(function () {
      self.waiting -= 1;
      if (self.waiting <= 0) { window.clearInterval(tick); }
      paintSoon();
    }, 1000);
    window.setTimeout(function () {
      window.clearInterval(tick);
      self.waiting = 0;
      if (!self.paused && !self.done) { self.pump(); }
      paintSoon();
    }, delay);
  };

  Downloader.prototype.save = function () {
    this.done = true;
    var blob;
    try { blob = new Blob(this.parts, { type: this.mime }); }
    catch (e) { this.fail("this browser could not assemble the file"); return; }
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = this.name;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    // The browser may hold the save behind a Keep/Discard prompt; revoking too
    // early would kill a download the user then approves.
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 60000);
    delete down[this.tid];
    note("Saved " + this.name);
    poll(true);
    paintSoon();
  };

  Downloader.prototype.fail = function (why) {
    this.done = true;
    delete down[this.tid];
    note("Could not download " + this.name + ": " + why, true);
    paintSoon();
  };

  function downloaderFor(tid) { return down[tid] || null; }

  function startDownload(tid) {
    var row = transferById(tid);
    if (!row) { return; }
    var job = downloaderFor(tid);
    if (job) { job.paused = false; job.pump(); return; }
    if (!canSaveBlob() || row.size > jsDownloadLimit()) {
      // Browsers that cannot save a Blob (old iOS Safari) play the file instead:
      // watching is the only way they can use it. Modern browsers hitting the
      // memory limit get a forced attachment so videos do not open in the
      // browser's player tab.
      nativeDownload(tid, row, canSaveBlob());
      return;
    }
    if (row.status === "paused") {
      callAction(tid, "resume", {}, function () { beginDownload(tid); });
      return;
    }
    beginDownload(tid);
  }

  function beginDownload(tid) {
    var row = transferById(tid);
    if (!row || downloaderFor(tid)) { return; }
    var job = new Downloader(row);
    job.pump();
    poll(true);
  }

  function nativeDownload(tid, row, modern) {
    var a = document.createElement("a");
    a.href = dataUrl(tid) + (modern ? "&disposition=attach" : "");
    if (modern && row && row.name) { a.download = row.name; }
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    note("Your browser is downloading " + (row ? row.name : "the file") +
         ". LocalDrop marks it delivered once it finishes.");
    poll(true);
  }

  function pauseDownload(tid) {
    var job = downloaderFor(tid);
    if (job) {
      job.paused = true;
      if (job.xhr) { try { job.xhr.abort(); } catch (e) {} }
    }
    callAction(tid, "pause", {});
  }

  function resumeDownload(tid) {
    var job = downloaderFor(tid);
    if (job) {
      job.paused = false;
      job.retrying = 0;
      callAction(tid, "resume", {}, function () { job.pump(); });
      return;
    }
    callAction(tid, "resume", {}, function () { startDownload(tid); });
  }

  /* ---------------------------------------------------------- row controls */
  function transferById(id) {
    for (var i = 0; i < S.transfers.length; i++) {
      if (S.transfers[i].id === id) { return S.transfers[i]; }
    }
    return null;
  }

  function markInterrupted(tid, why) {
    var row = transferById(tid);
    if (row && row.status !== "interrupted") {
      row.status = "interrupted";
      row.error = why || row.error;
    }
  }

  function callAction(tid, action, body, cb) {
    api("POST", "/api/transfers/" + encodeURIComponent(tid) + "/" + action,
      body || {}, function (err, status, data) {
        if (err) {
          if (status === 401) { lostSession(); return; }
          note(((data && data.error) || ("Could not " + action)), true);
          return;
        }
        if (cb) { cb(data); }
        poll(true);
      });
  }

  function pauseUpload(tid) {
    var job = uploaderFor(tid);
    if (job) { job.paused = true; }
    callAction(tid, "pause", {});
  }

  function resumeUpload(tid) {
    var job = uploaderFor(tid), row = transferById(tid);
    if (job) {
      job.paused = false;
      job.retrying = 0;
      callAction(tid, "resume", {}, function (data) {
        if (data && typeof data.offset === "number") { job.confirmed(data.offset); }
        runQueue();
        paintSoon();
      });
      return;
    }
    if (row && row.mine && row.stage === "upload") {
      askForFile(tid, row);
      return;
    }
    callAction(tid, "resume", {});
  }

  function askForFile(tid, row) {
    note("Select \u201c" + row.name + "\u201d again to continue from " +
         bytes(row.offset) + " \u2014 nothing already sent is repeated.");
    reselect(tid);
  }

  function cancelUpload(tid) {
    var job = uploaderFor(tid);
    if (job) {
      job.closing = true;
      if (job.xhr) { try { job.xhr.abort(); } catch (e) {} }
      delete up[tid];
    }
    callAction(tid, "cancel", {});
  }

  function dataUrl(tid) {
    return BASE + "/api/transfers/" + encodeURIComponent(tid) +
           "/data?token=" + encodeURIComponent(S.token) +
           (S.device ? "&device=" + encodeURIComponent(S.device) : "");
  }

  /* -------------------------------------------------------------- pairing */
  function screenTo(which) {
    S.screen = which;
    show($("boot"), which === "boot");
    show($("pair"), which === "pair");
    show($("app"), which === "app");
    document.body.className = document.body.className.replace(/\b(booting|pairing|ready)\b/g, "");
    addClass(document.body, which === "boot" ? "booting" : (which === "pair" ? "pairing" : "ready"));
    layout();
  }

  function lostSession() {
    window.clearTimeout(S.timer);
    delItem(K_TOKEN);
    delItem(K_DEVICE);
    S.token = "";
    S.device = "";
    text($("notif-stack"), "");
    screenTo("pair");
    setPairError("LocalDrop does not recognise this browser any more. Enter the pairing code again.");
  }

  function setPairError(message) {
    var el = $("pair-error");
    if (!message) { show(el, false); return; }
    el.innerHTML = esc(message);
    show(el, true);
  }

  function doPair(pin, name, cb) {
    api("POST", "/api/pair", { pin: pin || "", name: name || "" },
      function (err, status, data) {
        if (err) {
          if (status === 403) { cb("bad-pin"); return; }
          if (status === 429) { cb("throttled"); return; }
          cb("offline");
          return;
        }
        S.token = data.token;
        S.device = data.device;
        setItem(K_TOKEN, S.token);
        setItem(K_DEVICE, S.device);
        if (name) { setItem(K_NAME, name); }
        cb(null, data);
      });
  }

  function startPairing() {
    screenTo("pair");
    var name = $("device-name");
    if (name && !name.value) { name.value = getItem(K_NAME) || ""; }
    show($("pair-nostore"), !storage);
    if ($("pin")) { $("pin").focus(); }
  }

  function enterApp() {
    screenTo("app");
    S.reachable = false;
    poll(true);
  }

  function hostFastPath() {
    doPair("", getItem(K_NAME) || "", function (err) {
      if (err) { startPairing(); return; }
      enterApp();
    });
  }

  /* ----------------------------------------------------------------- boot */
  // Installable-app shell worker. Browsers only expose service workers on
  // secure origins (http://localhost on the PC); phones on plain-http LAN
  // addresses skip this, and the page keeps working as before.
  function registerSW() {
    if (!navigator.serviceWorker) { return; }
    try { navigator.serviceWorker.register(BASE + "/sw.js"); } catch (e) {}
  }

  function boot() {
    registerSW();
    screenTo("boot");
    addClass($("boot"), "waiting");
    text($("boot-msg"), "Connecting to LocalDrop&hellip;");
    show($("boot-error"), false);
    api("GET", "/api/hello", null, function (err, status, data) {
      if (err || !data || data.app !== "LocalDrop") {
        removeClassWaiting();
        text($("boot-error-text"), status
          ? "This address is not a LocalDrop server."
          : "LocalDrop is not answering. Start it on the computer you want to send to.");
        show($("boot-error"), true);
        $("boot-retry").onclick = function () { boot(); };
        return;
      }
      if (!S.token) { removeClassWaiting(); hostFastPath(); return; }
      api("GET", "/api/state", null, function (err2, status2) {
        removeClassWaiting();
        if (err2) {
          if (status2 === 401) { startPairing(); return; }
          text($("boot-error-text"), "The server did not answer. Check it is still running.");
          show($("boot-error"), true);
          $("boot-retry").onclick = function () { boot(); };
          return;
        }
        enterApp();
      });
    });
  }
  function removeClassWaiting() { drop($("boot"), "waiting"); }

  /* --------------------------------------------------------------- polling */
  function poll(intervalOnly) {
    if (!S.token) { return; }
    window.clearTimeout(S.timer);
    api("GET", "/api/state", null, function (err, status, data) {
      if (status === 401) { lostSession(); return; }
      if (err) {
        S.misses++;
        S.reachable = false;
        renderStatus();
        S.timer = window.setTimeout(function () { poll(); },
                                    Math.min(15000, 1500 * S.misses));
        return;
      }
      var wasUnreachable = !S.reachable;
      S.misses = 0;
      S.reachable = true;
      S.server = data.server;
      S.me = data.me;
      S.host = data.host || null;
      S.devices = data.devices || [];
      S.transfers = data.transfers || [];
      applyTheme();
      renderAll();
      syncUploadJobs();
      runQueue();
      if (wasUnreachable) { reconnectHeal(); }
      var delay = busyCount() ? (S.server.poll_ms || 1500)
                              : (S.server.idle_poll_ms || 5000);
      S.timer = window.setTimeout(function () { poll(); }, delay);
      if (!intervalOnly) { /* nothing extra */ }
    }, 20000);
  }

  function busyCount() {
    var seen = {}, n = 0, id, i, t;
    for (id in up) {
      if (up.hasOwnProperty(id)) { seen[id] = true; n++; }
    }
    for (id in down) {
      if (down.hasOwnProperty(id) && !seen[id]) { seen[id] = true; n++; }
    }
    for (i = 0; S.transfers && i < S.transfers.length; i++) {
      t = S.transfers[i];
      if (!seen[t.id] && activeRows(t)) { seen[t.id] = true; n++; }
    }
    return n;
  }

  function activeRows(t) {
    return t.status === "uploading" || t.status === "downloading" ||
           t.status === "verifying" || t.offer === "pending";
  }

  function reconnectHeal() {
    var id, job;
    for (id in up) {
      if (!up.hasOwnProperty(id)) { continue; }
      job = up[id];
      if (job.paused || job.done) { continue; }
      (function (j) {
        api("GET", "/api/transfers/" + encodeURIComponent(j.tid) + "/status", null,
          function (err, status, data) {
            if (err || !data) { return; }
            j.confirmed(data.offset || 0);
            if (data.status === "complete") { j.finish(); return; }
            runQueue();
          });
      }(job));
    }
    runQueue();
    note("Back online");
  }

  /* --------------------------------------------------------------- render */
  var rowNodes = {};
  var paintTimer = null;

  function paintSoon() {
    if (paintTimer) { return; }
    paintTimer = window.setTimeout(function () { paintTimer = null; renderQueue(); }, 300);
  }

  function paintProgress(tid, sent, total) {
    var node = rowNodes[tid];
    if (!node) { return; }
    var bar = firstBy(node, "bar");
    var fill = bar ? bar.getElementsByTagName("i")[0] : null;
    var pct = total ? Math.min(1, sent / total) : 0;
    if (fill) {
      var v = "scaleX(" + pct + ")";
      fill.style.webkitTransform = v;
      fill.style.transform = v;
    }
  }

  // The desktop shell never scrolls: the transfer list is the only scroll
  // region. Its top edge is measured from the live position of the Transfers
  // heading (the drop zone and picked-files block above it change height), and
  // the send panel lifts to make room for the Books help card when it docks.
  function layout() {
    if (S.screen !== "app") { return; }
    var panel = $("send-panel"), head = $("transfers-head"),
        scroll = $("queue-scroll"), books = $("books-panel");
    if (!panel || !head || !scroll) { return; }
    var booksH = (books && !has(books, "hidden")) ? books.offsetHeight + 12 : 0;
    panel.style.bottom = booksH + "px";
    scroll.style.top = (head.offsetTop + head.offsetHeight + 10) + "px";
  }

  function renderAll() {
    defaultTarget();
    renderStatus();
    renderAddress();
    renderDevices();
    renderTarget();
    renderMobileList();
    renderQueue();
    renderPicked();
    renderOffers();
    runAutoStart();
    renderBookHelp();
    renderSettingsButton();
    layout();
  }

  function renderStatus() {
    var pill = $("net-pill"), dot = $("net-dot"), label = $("net-text");
    drop(dot, "dot-on"); drop(dot, "dot-off"); drop(dot, "dot-warn"); drop(dot, "dot-bad");
    drop(dot, "dot-live");
    if (!S.reachable) {
      addClass(dot, "dot-bad");
      text(label, S.misses > 2 ? "Reconnecting" : "Trying again");
    } else {
      addClass(dot, "dot-on");
      if (busyCount()) { addClass(dot, "dot-live"); }
      text(label, "Connected");
    }
    toggle(pill, "is-off", !S.reachable);
    var meEl = $("me-name");
    if (meEl) {
      if (S.me && S.me.name) {
        meEl.innerHTML = kindIcon(S.me.kind) + "<span>" + esc(S.me.name) + "</span>";
        show(meEl, true);
      } else {
        show(meEl, false);
      }
    }
  }

  function renderAddress() {
    if (!S.server) { return; }
    text($("address-value"), esc(S.server.url || ""));
    text($("qr-url"), esc(S.server.url || ""));
    if (isHost()) {
      if (S.host && S.host.pin) { text($("qr-pin"), esc(S.host.pin)); }
    } else {
      text($("qr-code"), "The pairing code is shown on the PC running LocalDrop.");
    }
  }

  function kindIcon(kind) {
    if (kind === "ipad" || kind === "iphone" || kind === "ipod") { return icon("tablet", 20); }
    if (kind === "android") { return icon("phone", 20); }
    if (kind === "mac") { return icon("laptop", 20); }
    return icon("laptop", 20);
  }

  function deviceById(id) {
    for (var i = 0; i < S.devices.length; i++) {
      if (S.devices[i].id === id) { return S.devices[i]; }
    }
    return null;
  }

  // The host session id differs from the local device id, so "this computer" must be excluded too.
  function isSelf(d) {
    return !!d && (d.id === (S.me && S.me.id) || (d.local && isHost()));
  }

  function renderDevices() {
    var searchEl = $("device-search");
    var q = searchEl ? (searchEl.value || "").toLowerCase().replace(/^\s+|\s+$/g, "") : "";
    var list = S.devices || [], ordered = [], i, d, total = 0, html = "";
    for (i = 0; i < list.length; i++) {
      d = list[i];
      if (!isSelf(d) && S.target && d.id === S.target) { ordered.push(d); }
    }
    for (i = 0; i < list.length; i++) {
      d = list[i];
      if (!isSelf(d) && !(S.target && d.id === S.target)) { ordered.push(d); }
    }
    for (i = 0; i < ordered.length; i++) {
      d = ordered[i];
      total++;
      if (q && String(d.name || "").toLowerCase().indexOf(q) < 0) { continue; }
      var usable = d.online && d.paired;
      html += '<button type="button" class="device' +
              (S.target === d.id ? " is-target" : "") +
              (usable ? "" : " is-offline") +
              '" data-device="' + esc(d.id) + '">' +
              '<span class="dev-icon">' + kindIcon(d.kind) + "</span>" +
              '<span class="dev-name">' + esc(d.name) + "</span>" +
              '<span class="dev-meta">' + esc(kindLabel(d)) + "</span>" +
              '<span class="dev-state ' + (d.online ? "on" : "off") + '">' +
              (d.online ? "Online" : "Seen " + ago(d.seconds_since_seen)) +
              "</span>" +
              "</button>";
    }
    text($("devices"), html);
    var emptyEl = $("devices-empty");
    if (!html) {
      text(emptyEl, (total && q)
        ? "No devices match \u201c" + esc(q) + "\u201d."
        : "Only this computer is connected. Open the address below in another browser and enter the pairing code.");
      show(emptyEl, true);
    } else {
      show(emptyEl, false);
    }
  }

  function kindLabel(d) {
    if (d.local) { return "This computer"; }
    var names = { ipad: "iPad", iphone: "iPhone", ipod: "iPod", android: "Android",
                  mac: "Mac", windows: "Windows", linux: "Linux" };
    return names[d.kind] || "Browser";
  }

  // Rebuilt only when the target, query, or device set changes, so polling
  // never resets the list scroll position while the picker is open.
  var pickerSig = "";
  function renderMobileList() {
    var listEl = $("mpicker-list");
    if (!listEl) { return; }
    var searchEl = $("mpicker-search");
    var q = searchEl ? (searchEl.value || "").toLowerCase().replace(/^\s+|\s+$/g, "") : "";
    var list = S.devices || [], i, d, sig = (S.target || "") + "|" + q + "|", html = "", total = 0, usable;
    for (i = 0; i < list.length; i++) {
      d = list[i];
      if (isSelf(d)) { continue; }
      sig += d.id + ":" + (d.online ? 1 : 0) + ":" + (d.paired ? 1 : 0) + ",";
    }
    if (sig === pickerSig) { return; }
    pickerSig = sig;
    for (i = 0; i < list.length; i++) {
      d = list[i];
      if (isSelf(d)) { continue; }
      total++;
      if (q && String(d.name || "").toLowerCase().indexOf(q) < 0) { continue; }
      usable = d.online && d.paired;
      html += '<button type="button" class="mpicker-opt' + (S.target === d.id ? " is-on" : "") +
              (usable ? "" : " is-offline") + '" data-device="' + esc(d.id) + '"' +
              (usable ? "" : " disabled") + '>' +
              '<span class="mo-icon">' + kindIcon(d.kind) + "</span>" +
              '<span class="mo-text"><span class="mo-name">' + esc(d.name) + "</span>" +
              '<span class="mo-meta">' + esc(kindLabel(d)) + "</span></span>" +
              '<span class="mo-state ' + (d.online ? "on" : "off") + '">' +
              (d.online ? "Online" : "Seen " + ago(d.seconds_since_seen)) +
              "</span></button>";
    }
    text(listEl, html);
    var emptyEl = $("mpicker-empty");
    if (!html) {
      text(emptyEl, (total && q)
        ? "No devices match \u201c" + esc(q) + "\u201d."
        : "No other devices yet. Open the address on another device and pair it.");
      show(emptyEl, true);
    } else {
      show(emptyEl, false);
    }
  }

  function openMPicker() {
    var trig = $("mobile-trigger");
    show($("mpicker-backdrop"), true);
    show($("mpicker"), true);
    if (trig) { addClass(trig, "is-open"); trig.setAttribute("aria-expanded", "true"); }
    pickerSig = "";
    renderMobileList();
  }

  function closeMPicker() {
    var trig = $("mobile-trigger"), searchEl = $("mpicker-search");
    show($("mpicker-backdrop"), false);
    show($("mpicker"), false);
    if (trig) { drop(trig, "is-open"); trig.setAttribute("aria-expanded", "false"); }
    if (searchEl) { searchEl.value = ""; }
    pickerSig = "";
  }

  function renderTarget() {
    var d = S.target ? deviceById(S.target) : null;
    var btn = $("send-picked");
    if (btn) {
      btn.disabled = !S.target;
      toggle(btn, "hidden", false);
      var short = d ? (d.name.length > 20 ? d.name.slice(0, 19) + "\u2026" : d.name) : "";
      text(btn, d ? "Send to " + esc(short) : "Send");
    }
    var hint = $("drop-hint");
    if (hint) {
      text(hint, "Files are uploaded in " + bytes(S.server ? S.server.chunk_size : 1048576) +
           " pieces, so an interrupted send picks up where it stopped.");
    }
    var mtName = $("mt-name"), mtIcon = $("mt-icon");
    if (mtName) { text(mtName, d ? esc(d.name) : "Choose a device to send"); }
    if (mtIcon) { mtIcon.innerHTML = d ? kindIcon(d.kind) : icon("laptop", 20); }
  }

  function chooseTarget(id) {
    var d = deviceById(id);
    if (!d || isSelf(d)) { return; }
    if (!d.online) { note("That device is not reachable right now.", true); return; }
    S.target = id;
    renderDevices();
    renderTarget();
  }

  function defaultTarget() {
    if (S.target) {
      var cur = deviceById(S.target);
      if (cur && cur.online && !isSelf(cur)) { return; }
    }
    S.target = "";
    var i, d;
    if (isHost()) {
      for (i = 0; i < S.devices.length; i++) {
        d = S.devices[i];
        if (!d.local && d.online && d.role !== "host" && !isSelf(d)) { S.target = d.id; return; }
      }
    } else {
      for (i = 0; i < S.devices.length; i++) {
        d = S.devices[i];
        if (d.local && !isSelf(d)) { S.target = d.id; return; }
      }
    }
    for (i = 0; i < S.devices.length; i++) {
      d = S.devices[i];
      if (d.online && !isSelf(d)) { S.target = d.id; return; }
    }
  }

  /* --------------------------------------------------------- picked files */
  function renderPicked() {
    var i, f, kind, html = "", total = 0;
    for (i = 0; i < S.picked.length; i++) {
      f = S.picked[i];
      total += f.size;
      kind = fileIcon(f.name);
      html += '<span class="drop-file"><span class="pf-icon ' + kind[0] + '">' + kind[2] +
              '</span><span class="pk-name">' + esc(f.name) +
              '</span><span class="pk-size">' + bytes(f.size) + "</span></span>";
    }
    text($("picked"), html);
    show($("picked"), S.picked.length > 0);
    show($("picked-actions"), S.picked.length > 0);
    toggle($("drop"), "is-picked", S.picked.length > 0);
    layout();
  }

  function addPicked(list) {
    var i, f, names = {}, j;
    for (j = 0; j < S.picked.length; j++) { names[S.picked[j].name + ":" + S.picked[j].size] = 1; }
    for (i = 0; i < list.length; i++) {
      f = list[i];
      if (names[f.name + ":" + f.size]) { continue; }
      if (!f.size) { continue; }
      S.picked.push(f);
    }
    renderPicked();
  }

  /* ---------------------------------------------------------------- queue */
  function fileIcon(name) {
    var ext = (name.indexOf(".") >= 0 ? name.split(".").pop() : "").toLowerCase();
    if (ext === "epub" || ext === "pdf" || ext === "mobi") {
      return ["t-book", ext === "pdf" ? "PDF" : "EPUB", icon("book", 18)];
    }
    if (ext === "png" || ext === "jpg" || ext === "jpeg" || ext === "gif" || ext === "webp") {
      return ["t-img", "IMG", icon("image", 18)];
    }
    if (ext === "txt" || ext === "md" || ext === "csv" || ext === "doc" || ext === "docx" ||
        ext === "pages" || ext === "rtf") {
      return ["t-doc", ext.toUpperCase().slice(0, 4), icon("doc", 18)];
    }
    if (ext === "zip" || ext === "tar" || ext === "gz" || ext === "rar" || ext === "7z") {
      return ["", "ZIP", icon("zip", 18)];
    }
    if (ext === "mp4" || ext === "mov" || ext === "m4v") {
      return ["", ext.toUpperCase(), icon("video", 18)];
    }
    if (ext === "mp3" || ext === "m4a" || ext === "m4b" || ext === "aac" || ext === "wav") {
      return ["", ext.toUpperCase(), icon("music", 18)];
    }
    return ["", ext ? ext.toUpperCase().slice(0, 4) : "FILE", icon("file", 18)];
  }

  // The label depends on who is reading: the sender is "Sending", the receiver
  // is "Receiving", and a finished file is "Delivered" / "Received".
  function stateFor(t) {
    var s = stalledUpload(t) ? "interrupted" : t.status;
    var mine = t.mine, forMe = t.for_me;
    if (forMe && t.offer === "pending" && s !== "rejected" && s !== "canceled") {
      return ["Needs your OK", "s-warn"];
    }
    if (mine && t.offer === "pending" && s !== "rejected" && s !== "canceled") {
      return ["Waiting for OK", "s-warn"];
    }
    if (s === "pending") { return [forMe ? "Incoming" : "Queued", forMe ? "s-run" : ""]; }
    if (s === "uploading") { return [mine ? "Sending" : (forMe ? "On its way" : "Sending"), "s-run"]; }
    if (s === "paused") { return ["Paused", "s-warn"]; }
    if (s === "interrupted") { return [forMe && !mine ? "Waiting for sender" : "Interrupted", "s-warn"]; }
    if (s === "verifying") { return ["Verifying", "s-run"]; }
    if (s === "ready") {
      return [mine ? "Waiting for " + (t.dest_name || "device") : (forMe ? "Ready to download" : "Ready"), "s-run"];
    }
    if (s === "downloading") {
      return [mine ? (t.dest_name || "Device") + " is receiving" : "Receiving", "s-run"];
    }
    if (s === "complete") { return [forMe && !mine ? "Received" : "Delivered", "s-ok"]; }
    if (s === "rejected") { return ["Declined", "s-bad"]; }
    if (s === "canceled") { return ["Canceled", ""]; }
    if (s === "error") { return ["Failed", "s-bad"]; }
    return [s, ""];
  }

  // Direction strap: a thin coloured stripe on the row's left edge. The hue says
  // what the transfer is without spending a whole column on it.
  function strapFor(t) {
    if (/^(error|rejected|canceled)$/.test(t.status)) { return "dir-stopped"; }
    if (t.status === "complete") { return t.mine ? "dir-sent" : "dir-recvd"; }
    return (t.for_me && !t.mine) ? "dir-recv" : "dir-send";
  }

  function visibleRows() {
    var out = [], i, t;
    for (i = 0; i < S.transfers.length; i++) {
      t = S.transfers[i];
      // Pending incoming offers live in the notification inbox; accepting one
      // moves it into this list.
      if (t.for_me && t.offer === "pending" && t.status !== "rejected" && t.status !== "canceled") { continue; }
      out.push(t);
      if (out.length >= 80) { break; }      // keep the DOM small on an iPad 2
    }
    return out;
  }

  function renderQueue() {
    if (!S.reachable) { return; }
    var rows = visibleRows(), keep = {}, i, t, node;
    var queue = $("queue");
    for (i = 0; i < rows.length; i++) {
      t = rows[i];
      node = rowNodes[t.id];
      if (!node || node.parentNode !== queue) {
        node = document.createElement("div");
        node.className = "row";
        node.setAttribute("data-tid", t.id);
        node.innerHTML = rowInner();
        queue.appendChild(node);
        rowNodes[t.id] = node;
      }
      keep[t.id] = 1;
      updateRow(node, t);
    }
    var id;
    for (id in rowNodes) {
      if (!rowNodes.hasOwnProperty(id)) { continue; }
      if (!keep[id]) {
        if (rowNodes[id].parentNode) { rowNodes[id].parentNode.removeChild(rowNodes[id]); }
        delete rowNodes[id];
        if (watch[id]) { delete watch[id]; }
      }
    }
    show($("queue-empty"), rows.length === 0);
    text($("queue-empty"), "Nothing transferring yet. Files you send or receive appear here.");
    renderBulk();
    layout();
  }

  function rowInner() {
    return '<span class="row-strap"></span>' +
      '<div class="row-line">' +
      '<span class="c-check"><input type="checkbox" class="row-check" aria-label="Select this transfer"></span>' +
      '<span class="c-ctl"></span>' +
      '<span class="c-name"><span class="row-name"></span></span>' +
      '<span class="c-stats"><span class="stats-table">' +
        '<span class="c-bar"><div class="bar"><i></i></div><span class="row-pct"></span></span>' +
        '<span class="c-speed"><span class="row-speed"></span></span>' +
        '<span class="c-size"><span class="row-size"></span></span>' +
      "</span></span>" +
      '<span class="c-del"></span>' +
    "</div>";
  }

  function stalledUpload(t) {
    return t.mine && t.stage === "upload" && !uploaderFor(t.id) &&
           /^(pending|uploading)$/.test(t.status);
  }

  /* When this browser is not the one moving bytes (the PC watching an iPad
     upload, or the sender watching the receiver pull), the server's confirmed
     offset is the only progress signal. Sample it across polls so that side
     shows an honest, if coarser, live speed too. */
  var watch = {};
  function watchRate(tid, offset, active) {
    var now = new Date().getTime(), w = watch[tid];
    if (!active) { if (w) { delete watch[tid]; } return 0; }
    if (!w) { w = watch[tid] = { s: [] }; }
    var s = w.s;
    if (!s.length || s[s.length - 1][1] !== offset) { s.push([now, offset]); }
    while (s.length > 2 && now - s[0][0] > 5000) { s.shift(); }
    if (s.length > 64) { s.shift(); }
    if (s.length < 2) { return 0; }
    var dt = (s[s.length - 1][0] - s[0][0]) / 1000;
    if (dt <= 0.5 || now - s[s.length - 1][0] > 4000) { return 0; }
    var db = s[s.length - 1][1] - s[0][1];
    return db > 0 ? db / dt : 0;
  }

  function ctlBtn(act, tid, iconName, title, danger) {
    return '<button type="button" class="row-btn' + (danger ? " row-btn-danger" : "") +
           '" data-act="' + act + '" data-tid="' + esc(tid) + '" title="' + esc(title) +
           '" aria-label="' + esc(title) + '">' + icon(iconName, 16) + "</button>";
  }

  // Motion controls sit at the start of the row, delete at the far end. A finished
  // file shows open-folder (the PC inbox) or download again while the server
  // still holds a copy; delivered relays drop theirs, so only Delete remains.
  function rowControls(t) {
    var job = uploaderFor(t.id), djob = downloaderFor(t.id), left = "", right = "";
    var uploading = t.stage === "upload" &&
                    /^(pending|ready|uploading|downloading|paused|interrupted|verifying)$/.test(t.status);
    var terminal = /^(complete|canceled|rejected|error)$/.test(t.status);
    if (t.mine && uploading) {
      if (stalledUpload(t)) { left = ctlBtn("reselect", t.id, "refresh", "Reselect file"); }
      else if (t.status === "paused") { left = ctlBtn("resume", t.id, "play", "Resume"); }
      else if (t.status === "interrupted") {
        left = job ? ctlBtn("resume", t.id, "play", "Resume")
                   : ctlBtn("reselect", t.id, "refresh", "Reselect file");
      } else if (t.status !== "verifying") { left = ctlBtn("pause", t.id, "pause", "Pause"); }
      right = ctlBtn("cancel", t.id, "x", "Cancel", true);
    } else if (t.status === "complete") {
      if (isHost() && t.dest === "local") { left = ctlBtn("folder", t.id, "folder", "Open folder"); }
      else if (t.downloadable) {
        left = ctlBtn("download", t.id, "download", "Download again");
      }
      right = ctlBtn("delete", t.id, "trash", "Delete", true);
    } else if (t.for_me && t.stage === "download" && t.offer !== "pending") {
      if (t.status === "ready") { left = ctlBtn("download", t.id, "play", "Download"); }
      else if (t.status === "downloading") { left = djob ? ctlBtn("pause", t.id, "pause", "Pause") : ""; }
      else if (t.status === "paused") { left = ctlBtn("resume", t.id, "play", "Resume"); }
      right = ctlBtn("delete", t.id, "trash", "Delete", true);
    } else if (t.for_me && t.stage === "upload" && t.offer !== "pending") {
      // The receiver shares pause/resume/cancel while the sender is uploading.
      if (t.status === "paused" || t.status === "interrupted") {
        left = ctlBtn("resume", t.id, "play", "Resume");
      } else if (t.status === "uploading" || t.status === "pending") {
        left = ctlBtn("pause", t.id, "pause", "Pause");
      }
      right = ctlBtn("cancel", t.id, "x", "Cancel", true);
    } else if (t.downloadable && isHost() && !t.for_me) {
      left = ctlBtn("download", t.id, "download", "Download");
    }
    if (!right && terminal) { right = ctlBtn("delete", t.id, "trash", "Delete", true); }
    return { left: left, right: right };
  }

  function updateRow(node, t) {
    var job = uploaderFor(t.id), djob = downloaderFor(t.id);
    var shown = stalledUpload(t) ? "interrupted" : t.status;
    var live = shown === "uploading" || shown === "downloading";
    var done = shown === "complete";
    var bad = /^(error|rejected|canceled)$/.test(shown);
    var sent = t.offset, total = t.size, rate = 0;
    if (job) {
      if (job.sent > sent) { sent = job.sent; }
      if (job.offset > sent) { sent = job.offset; }
      rate = job.rate();
    }
    if (djob) {
      sent = djob.received;
      rate = djob.rate();
    }
    if (job || djob) {
      if (watch[t.id]) { delete watch[t.id]; }
    } else {
      rate = watchRate(t.id, t.offset, live);
    }
    if (t.stage === "done") { sent = total; }
    var rowCls = "row " + strapFor(t) + (done ? " is-done" : "") +
                 (t.for_me && t.offer === "pending" ? " is-incoming" : "") +
                 (S.selected[t.id] ? " is-selected" : "");
    setCls(node, rowCls, node.className);
    text(firstBy(node, "row-name"), esc(t.name));
    text(firstBy(node, "row-size"), bytes(t.size));

    var pct = total ? Math.min(1, sent / total) : 0;
    var bar = firstBy(node, "bar");
    var fill = bar.getElementsByTagName("i")[0];
    var v = "scaleX(" + pct + ")";
    if (fill.style.transform !== v) { fill.style.webkitTransform = v; fill.style.transform = v; }
    var run = shown === "ready" || (shown === "pending" && !t.for_me);
    bar.className = "bar" + (shown === "paused" || shown === "interrupted" ? " is-paused"
                          : (bad ? " is-bad"
                          : (done ? " is-done"
                          : (live ? " is-live" : (run ? " is-run" : "")))));

    var pctText;
    if (done) { pctText = ""; }
    else if (shown === "ready") { pctText = "Ready"; }
    else if (shown === "pending") {
      pctText = t.offer === "pending" ? "Waiting"
              : (t.mine ? "Queued" : "Incoming");
    }
    else if (bad) { pctText = stateFor(t)[0]; }
    else { pctText = Math.round(pct * 100) + "%"; }
    text(firstBy(node, "row-pct"), pctText);

    var speedEl = firstBy(node, "row-speed");
    var showSpeed = !done && !bad;
    if (live && rate > 0) {
      var receiving = t.for_me || (!t.mine && t.stage === "download");
      text(speedEl, icon(receiving ? "arrowdown" : "arrowup", 13) + speed(rate));
    } else if (showSpeed) {
      text(speedEl, speed(0));
    } else {
      text(speedEl, "");
    }
    show(firstBy(node, "c-speed"), showSpeed);

    var chk = firstBy(node, "row-check"), selOn = !!S.selected[t.id];
    if (chk.checked !== selOn) { chk.checked = selOn; }
    var ctl = rowControls(t);
    var leftEl = firstBy(node, "c-ctl"), rightEl = firstBy(node, "c-del");
    if (ctl.left !== leftEl.lastCtl) { leftEl.lastCtl = ctl.left; text(leftEl, ctl.left); }
    if (ctl.right !== rightEl.lastCtl) { rightEl.lastCtl = ctl.right; text(rightEl, ctl.right); }
  }

  function nowSeconds() { return Math.round(new Date().getTime() / 1000); }

  function setCls(node, wanted, current) {
    if (wanted !== current) { node.className = wanted; }
  }

  function firstBy(node, className) {
    var els = node.getElementsByTagName("*"), i;
    for (i = 0; i < els.length; i++) {
      if ((" " + els[i].className + " ").indexOf(" " + className + " ") >= 0) { return els[i]; }
    }
    return node;
  }

  /* ---------------------------------------------------- incoming notifications */
  function pendingOffers() {
    var out = [], i, t;
    for (i = 0; i < S.transfers.length; i++) {
      t = S.transfers[i];
      if (t.for_me && t.offer === "pending" && t.status !== "rejected" && t.status !== "canceled") {
        out.push(t);
      }
    }
    return out;
  }

  function notifCard(t) {
    var info = fileIcon(t.name);
    return '<div class="notif" data-offer="' + esc(t.id) + '">' +
      '<div class="notif-top">' +
        '<span class="notif-ico ' + info[0] + '" aria-hidden="true">' + info[2] + "</span>" +
        '<span class="notif-heading">' +
          '<span class="notif-eyebrow">Incoming file</span>' +
          '<span class="notif-name">' + esc(t.name) + "</span>" +
          '<span class="notif-meta">' + bytes(t.size) + " \u00b7 from " +
            esc(t.source_name || "another device") + "</span>" +
        "</span>" +
      "</div>" +
      '<div class="notif-actions">' +
        '<button type="button" class="btn btn-primary btn-small" data-act="accept" data-tid="' + esc(t.id) + '">' +
          icon("check", 14) + "Accept</button>" +
        '<button type="button" class="btn btn-ghost btn-small" data-act="reject" data-tid="' + esc(t.id) + '">' +
          icon("x", 14) + "Decline</button>" +
      "</div>" +
    "</div>";
  }

  function renderOffers() {
    var stack = $("notif-stack");
    if (!stack) { return; }
    var offers = pendingOffers(), keep = {}, i, t, found, child, node, id;
    for (i = 0; i < offers.length; i++) {
      t = offers[i];
      keep[t.id] = 1;
      found = null; child = stack.firstChild;
      while (child) {
        if (child.getAttribute && child.getAttribute("data-offer") === t.id) { found = child; break; }
        child = child.nextSibling;
      }
      if (!found) {
        node = document.createElement("div");
        node.innerHTML = notifCard(t);
        stack.appendChild(node.firstChild);
      }
    }
    var kids = [], k = stack.firstChild;
    while (k) { kids.push(k); k = k.nextSibling; }
    for (i = 0; i < kids.length; i++) {
      id = kids[i].getAttribute ? kids[i].getAttribute("data-offer") : null;
      if (!id || !keep[id]) { stack.removeChild(kids[i]); }
    }
  }

  function decideOffer(action, tid) {
    if (!tid) { return; }
    var row = transferById(tid);
    if (action === "accept") { autoStart[tid] = 1; }
    callAction(tid, action, {}, function () {
      if (action === "accept") { note("Accepted " + (row ? row.name : "the file")); }
      else { note("Declined " + (row ? row.name : "the file")); }
      poll(true);
    });
  }

  // Accepting starts the download as soon as the file is ready, without a second
  // tap. The flag survives until then, so accepting mid-upload still auto-starts.
  function runAutoStart() {
    var i, t;
    for (i = 0; i < S.transfers.length; i++) {
      t = S.transfers[i];
      if (!autoStart[t.id]) { continue; }
      if (t.status === "rejected" || t.status === "canceled" ||
          t.status === "error" || t.status === "complete") {
        delete autoStart[t.id]; continue;
      }
      if (t.for_me && t.offer === "accepted" && t.status === "ready" && !downloaderFor(t.id)) {
        delete autoStart[t.id];
        startDownload(t.id);
      }
    }
  }

  /* ----------------------------------------------------------- multi-select */
  function setSelected(tid, on) {
    if (on) { S.selected[tid] = 1; } else { delete S.selected[tid]; }
    renderQueue();
  }

  function selectedIds() {
    var ids = [], id;
    for (id in S.selected) { if (S.selected.hasOwnProperty(id)) { ids.push(id); } }
    return ids;
  }

  function renderBulk() {
    var rows = visibleRows(), sel = 0, i, n = selectedIds().length;
    for (i = 0; i < rows.length; i++) { if (S.selected[rows[i].id]) { sel++; } }
    show($("bulk-tools"), n > 0);
    text($("bulk-count"), n + " selected");
    $("bulk-delete").disabled = n === 0;
    var allOn = rows.length > 0 && sel === rows.length;
    $("bulk-all").checked = allOn;
    $("head-all").checked = allOn;
  }

  function setAllSelected(on) {
    var rows = visibleRows(), i;
    if (on) { for (i = 0; i < rows.length; i++) { S.selected[rows[i].id] = 1; } }
    else { S.selected = {}; }
    renderQueue();
  }

  function deleteSelected() {
    var ids = selectedIds();
    if (ids.length) { openDeleteConfirm(ids); }
  }

  /* ------------------------------------------------------ delete confirmation */
  var deleteTargets = [];

  function openDeleteConfirm(ids) {
    if (!ids || !ids.length) { return; }
    deleteTargets = ids;
    var n = ids.length, first = transferById(ids[0]);
    text($("delete-title"), n > 1 ? "Delete " + n + " transfers?" : "Delete transfer?");
    text($("delete-msg"), n > 1 ? n + " selected transfers"
                                : esc(first ? first.name : "this transfer"));
    show($("delete-sheet"), true);
  }

  function removeTransfers(ids, keepFile) {
    var i, tid, uj, dj;
    for (i = 0; i < ids.length; i++) {
      tid = ids[i]; uj = uploaderFor(tid); dj = downloaderFor(tid);
      if (uj) { uj.closing = true; if (uj.xhr) { try { uj.xhr.abort(); } catch (e) {} } delete up[tid]; }
      if (dj) { dj.done = true; if (dj.xhr) { try { dj.xhr.abort(); } catch (e) {} } delete down[tid]; }
      delete S.selected[tid];
      callAction(tid, "delete", { keep_file: keepFile });
    }
    note((keepFile ? "Removed " : "Deleted ") + ids.length +
         " transfer" + (ids.length > 1 ? "s" : ""));
    renderQueue();
  }

  function doDelete(keepFile) {
    var ids = deleteTargets;
    deleteTargets = [];
    show($("delete-sheet"), false);
    if (ids.length) { removeTransfers(ids, keepFile); }
  }

  var cancelTarget = null;

  function openCancelConfirm(tid) {
    if (!tid) { return; }
    cancelTarget = tid;
    var row = transferById(tid);
    text($("cancel-msg"), esc(row ? row.name : "this transfer"));
    show($("cancel-sheet"), true);
  }

  // Cancelling stops the transfer and clears the file and the row everywhere.
  function doCancel() {
    var tid = cancelTarget;
    cancelTarget = null;
    show($("cancel-sheet"), false);
    if (tid) { removeTransfers([tid], false); }
  }

  /* -------------------------------------------------------- book help panel */
  function renderBookHelp() {
    var i, t, showBooks = false;
    for (i = 0; i < S.transfers.length; i++) {
      t = S.transfers[i];
      if (t.for_me && /\.(epub|pdf)$/i.test(t.name) &&
          (t.offer === "accepted" || t.status === "complete")) { showBooks = true; }
    }
    show($("books-panel"), showBooks && !isHost());
  }

  function renderSettingsButton() {
    show($("btn-settings"), isHost());
  }

  /* -------------------------------------------------------------- settings */
  var settingsCache = null;

  function pickOption(sel, value, label) {
    var v = String(value), i;
    sel.value = v;
    if (sel.value === v) { return; }
    var opt = document.createElement("option");
    opt.value = v;
    opt.innerHTML = esc(label);
    for (i = 0; i < sel.options.length; i++) {
      if (parseInt(sel.options[i].value, 10) > parseInt(v, 10)) { break; }
    }
    sel.insertBefore(opt, sel.options[i] || null);
    sel.value = v;
  }

  function openSettings() {
    show($("settings-sheet"), true);
    api("GET", "/api/settings", null, function (err, status, data) {
      if (err) { settingsError("Could not read the settings: " + err); return; }
      settingsCache = data;
      pickOption($("set-chunk"), data.settings.chunk_size, bytes(data.settings.chunk_size) + " (config file)");
      pickOption($("set-poll"), data.settings.poll_interval_ms,
                 (data.settings.poll_interval_ms / 1000) + " s (config file)");
      pickOption($("set-timeout"), data.settings.device_timeout_s,
                 data.settings.device_timeout_s + " s (config file)");
      $("set-autoaccept").checked = !!data.settings.auto_accept;
      $("set-limit").value = String(Math.round((data.settings.max_upload_bytes || 0) / 1048576));
      text($("set-pin"), data.settings.require_pairing ? esc(data.pin) : "Pairing is switched off");
      text($("settings-stats"), (data.stats && data.stats.transfers)
        ? data.stats.transfers + " transfer" + (data.stats.transfers === 1 ? "" : "s") +
          " remembered \u00b7 " + bytes(data.stats.bytes || 0) + " on disk"
        : "Nothing transferred yet.");
    });
  }

  function settingsError(message) {
    var el = $("set-error");
    if (!message) { show(el, false); return; }
    el.innerHTML = esc(message);
    show(el, true);
  }

  function saveSettings() {
    var payload = {
      chunk_size: parseInt($("set-chunk").value, 10),
      poll_interval_ms: parseInt($("set-poll").value, 10),
      device_timeout_s: parseInt($("set-timeout").value, 10),
      auto_accept: $("set-autoaccept").checked,
      max_upload_bytes: Math.max(0, parseInt($("set-limit").value || "0", 10) || 0) * 1048576
    };
    api("POST", "/api/settings", payload, function (err, status, data) {
      if (err) { settingsError("Could not save: " + err); return; }
      settingsError("");
      show($("settings-sheet"), false);
      note("Settings saved");
      if (data && data.restart_needed) { note("Restart LocalDrop to use the new port."); }
      poll(true);
    });
  }

  /* ----------------------------------------------------------------- toast */
  var toastTimer = null;
  function note(message, bad) {
    var el = $("toast");
    el.className = "toast" + (bad ? " is-bad" : "");
    el.innerHTML = esc(message);
    show(el, true);
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { show(el, false); }, bad ? 6000 : 3200);
  }

  /* ----------------------------------------------------------------- theme */
  function applyTheme() {
    var wanted = getItem(K_THEME);
    if (!wanted && S.server && S.server.theme) { wanted = S.server.theme; }
    toggle(document.body, "dark", wanted === "dark");
  }

  function flipTheme() {
    var nowDark = !has(document.body, "dark");
    toggle(document.body, "dark", nowDark);
    setItem(K_THEME, nowDark ? "dark" : "light");
  }

  /* --------------------------------------------------------------- wiring */
  function openQr() {
    show($("qr-sheet"), true);
    var img = $("qr-box");
    img.innerHTML = "";
    var xhr = new XMLHttpRequest();
    xhr.open("GET", BASE + "/api/qr?token=" + encodeURIComponent(S.token), true);
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) { return; }
      if (xhr.status === 200 && xhr.responseText) { img.innerHTML = xhr.responseText; }
      else { img.innerHTML = '<p class="hint">Could not draw the code. Type the address instead.</p>'; }
    };
    headersFor(xhr, false);
    xhr.send(null);
  }

  function copyAddress() {
    var url = (S.server && S.server.url) || "";
    if (!url) { note("No address to copy yet.", true); return; }
    var ta = document.createElement("textarea");
    ta.value = url;
    ta.setAttribute("readonly", "");
    ta.style.position = "absolute";
    ta.style.left = "-9999px";
    ta.style.top = "0";
    document.body.appendChild(ta);
    ta.select();
    if (ta.setSelectionRange) { ta.setSelectionRange(0, url.length); }
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    if (ok) { note("Address copied"); }
    else { note("Copy is not available here. The address is " + url, true); }
  }

  function wire() {
    if ("ontouchstart" in window || navigator.maxTouchPoints > 0) {
      addClass(document.body, "touch");
      addClass($("drop"), "no-drag");
      text($("drop-title"), "Choose files to share");
      text($("drop-sub"), "Tap Browse files, then pick the receiving device.");
    }

    on($("pair-form"), "submit", function (e) {
      e.preventDefault();
      var pin = ($("pin").value || "").replace(/[^0-9]/g, "");
      var name = ($("device-name").value || "").replace(/^\s+|\s+$/g, "");
      if (pin.length !== 6) { setPairError("The pairing code is 6 digits."); return; }
      var go = $("pair-go");
      go.disabled = true;
      go.innerHTML = "Connecting\u2026";
      doPair(pin, name, function (err) {
        go.disabled = false;
        go.innerHTML = "Connect";
        if (err === "bad-pin") { setPairError("That pairing code is not correct."); return; }
        if (err === "throttled") {
          setPairError("Too many attempts. Wait a minute, then try again.");
          return;
        }
        if (err) { setPairError("LocalDrop did not answer. Is it running?"); return; }
        setPairError("");
        screenTo("app");
        S.reachable = false;
        poll(true);
      });
    });
    on($("pin"), "input", function () {
      this.value = (this.value || "").replace(/[^0-9]/g, "").slice(0, 6);
    });

    on($("btn-theme"), "click", flipTheme);
    on($("btn-qr-open"), "click", openQr);
    on($("address-value"), "click", copyAddress);
    on($("device-search"), "input", function () { renderDevices(); });
    on($("btn-settings"), "click", openSettings);
    on($("set-save"), "click", saveSettings);
    on($("set-rotate"), "click", function () {
      api("POST", "/api/pin/rotate", {}, function (err, status, data) {
        if (err) { settingsError("Could not rotate the code: " + err); return; }
        settingsError("");
        note("New pairing code " + data.pin);
        text($("qr-pin"), esc(data.pin));
        text($("set-pin"), esc(data.pin));
      });
    });
    on($("set-folder"), "click", function () { openFolder(""); });

    var closers = document.querySelectorAll("[data-close]"), i;
    for (i = 0; i < closers.length; i++) {
      on(closers[i], "click", function () {
        show($(this.getAttribute("data-close")), false);
      });
    }
    on($("qr-sheet"), "click", function (e) {
      if (e.target === this) { show(this, false); }
    });
    on($("settings-sheet"), "click", function (e) {
      if (e.target === this) { show(this, false); }
    });

    on($("notif-stack"), "click", function (e) {
      var el = e.target;
      while (el && el !== this && !(el.getAttribute && el.getAttribute("data-act"))) {
        el = el.parentNode;
      }
      if (!el || !el.getAttribute) { return; }
      var act = el.getAttribute("data-act"), tid = el.getAttribute("data-tid");
      if (tid && (act === "accept" || act === "reject")) { decideOffer(act, tid); }
    });

    on($("file-input"), "change", function () {
      if (this.files && this.files.length) { addPicked(this.files); }
      this.value = "";
    });
    on($("send-picked"), "click", function () { sendFiles(S.picked); });
    on($("clear-picked"), "click", function () { S.picked = []; renderPicked(); });

    var dz = $("drop");
    on(dz, "dragenter", function (e) { e.preventDefault(); addClass(dz, "is-over"); });
    on(dz, "dragover", function (e) { e.preventDefault(); addClass(dz, "is-over"); });
    on(dz, "dragleave", function (e) { e.preventDefault(); drop(dz, "is-over"); });
    on(dz, "drop", function (e) {
      e.preventDefault();
      drop(dz, "is-over");
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        addPicked(e.dataTransfer.files);
      }
    });

    on($("devices"), "click", function (e) {
      var el = e.target;
      while (el && el !== this && !(el.getAttribute && el.getAttribute("data-device"))) {
        el = el.parentNode;
      }
      if (el && el.getAttribute) {
        var id = el.getAttribute("data-device");
        if (id) { chooseTarget(id); }
      }
    });

    on($("mobile-trigger"), "click", function () {
      if (has($("mpicker"), "hidden")) { openMPicker(); } else { closeMPicker(); }
    });
    on($("mpicker-backdrop"), "click", closeMPicker);
    on($("mpicker-close"), "click", closeMPicker);
    on($("mpicker-search"), "input", renderMobileList);
    on($("mpicker-list"), "click", function (e) {
      var el = e.target;
      while (el && el !== this && !(el.getAttribute && el.getAttribute("data-device"))) {
        el = el.parentNode;
      }
      if (el && el.getAttribute) {
        var id = el.getAttribute("data-device");
        if (id) { chooseTarget(id); closeMPicker(); }
      }
    });

    on($("queue"), "click", function (e) {
      var el = e.target, act;
      if (el && el.className && (" " + el.className + " ").indexOf(" row-check ") >= 0) {
        var rowEl = el;
        while (rowEl && rowEl !== this && !(rowEl.getAttribute && rowEl.getAttribute("data-tid") && has(rowEl, "row"))) {
          rowEl = rowEl.parentNode;
        }
        if (rowEl && rowEl !== this && rowEl.getAttribute) {
          var selId = rowEl.getAttribute("data-tid");
          if (selId) { setSelected(selId, el.checked); }
        }
        return;
      }
      while (el && el !== this && !(el.getAttribute && el.getAttribute("data-act"))) {
        el = el.parentNode;
      }
      if (!el || !el.getAttribute) { return; }
      act = el.getAttribute("data-act");
      var tid = el.getAttribute("data-tid");
      if (!act || !tid) { return; }
      var row = transferById(tid);
      if (act === "pause") {
        if (row && row.stage === "download") { pauseDownload(tid); } else { pauseUpload(tid); }
      }
      else if (act === "resume") {
        if (row && row.stage === "download") { resumeDownload(tid); } else { resumeUpload(tid); }
      }
      else if (act === "download") { startDownload(tid); }
      else if (act === "reselect") { askForFile(tid, row); }
      else if (act === "cancel") { openCancelConfirm(tid); }
      else if (act === "delete") { openDeleteConfirm([tid]); }
      else if (act === "folder") { openFolder(tid); }
    });

    on($("delete-file"), "click", function () { doDelete(false); });
    on($("delete-keep"), "click", function () { doDelete(true); });
    on($("delete-sheet"), "click", function (e) {
      if (e.target === this) { show(this, false); }
    });
    on($("cancel-confirm"), "click", doCancel);
    on($("cancel-keep"), "click", function () { show($("cancel-sheet"), false); });
    on($("cancel-sheet"), "click", function (e) {
      if (e.target === this) { show(this, false); }
    });
    on($("bulk-delete"), "click", deleteSelected);
    on($("bulk-all"), "change", function () { setAllSelected(this.checked); });
    on($("head-all"), "change", function () { setAllSelected(this.checked); });

    on(window, "online", function () {
      S.reachable = false;
      S.misses = 3;
      poll();
      window.setTimeout(function () { reconnectHeal(); }, 800);
    });
    on(window, "offline", function () {
      S.reachable = false;
      renderStatus();
    });
    on(window, "resize", layout);

    document.onkeydown = function (e) {
      if (e && (e.keyCode === 27)) {
        show($("qr-sheet"), false);
        show($("settings-sheet"), false);
        show($("delete-sheet"), false);
        show($("cancel-sheet"), false);
        closeMPicker();
        if (selectedIds().length) { S.selected = {}; renderQueue(); }
      }
    };
  }

  function openFolder(tid) {
    api("POST", "/api/files/open", tid ? { transfer: tid } : {}, function (err, status, data) {
      if (err) {
        note(status === 403 ? "Only the computer running LocalDrop can open folders."
                            : "Could not open the folder: " + err, true);
        return;
      }
      note("Opened the received-files folder");
    });
  }

  /* ------------------------------------------------------------------ start */
  wire();
  boot();
}());