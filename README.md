# LocalDrop

Local file transfer between a Windows PC and an iPad, iPhone, Android phone or
laptop on the same Wi-Fi network. No cloud, no account, no install on the
phones — the receiving device just opens a web address.

Built to work on **iPad 2 / iOS 9 Safari**, which means plain HTTP, polling and
files streamed in small chunks, rather than WebRTC, WebSockets or a service
worker.

- Version 1.0.0
- Python 3.8+ standard library only — see [requirements.txt](requirements.txt)
- Runs on Windows 10/11; works on macOS and Linux with the console entry point

---

## Contents

1. [Quick start](#quick-start-windows)
2. [Pairing a device](#pairing-a-device)
3. [The launcher window](#the-launcher-window)
4. [The transfer page](#the-transfer-page)
5. [Pause, resume, recovery](#pause-resume-and-recovery)
6. [Compatibility: what was tested and what was not](#compatibility)
7. [Security model](#security-model)
8. [Data, config and where files land](#data-and-configuration)
9. [Windows Firewall](#windows-firewall)
10. [Start with Windows](#start-with-windows)
11. [Sending books and PDFs to iBooks](#sending-a-book-or-pdf-to-the-ipad)
12. [Architecture and API](#architecture)
13. [Tests](#tests)
14. [Troubleshooting](#troubleshooting)
15. [What LocalDrop deliberately does not do](#what-localdrop-does-not-do)

---

## Quick start (Windows)

1. Install Python 3.8 or newer from <https://www.python.org/downloads/>.
   On the first installer screen leave **"Add python.exe to PATH"** ticked, and
   under *Optional features* leave **"tcl/tk and IDLE"** ticked (that is the
   launcher window).
2. Double-click **`LocalDrop.bat`**.
   A window opens with an address, a QR code and a 6-digit pairing code.
3. Press **Start LocalDrop**, then **Allow on this network** and answer the
   Administrator prompt. If devices still cannot connect, press
   **Check firewall** and follow what it says. If it answers `mismatch`, an
   older private-only rule is sitting on a Public network: see
   [Windows Firewall](#windows-firewall) — this is the most common reason the
   page loads on the PC but on no other device.
4. On the iPad, open Safari and **type** the address shown, for example
   `http://192.168.1.10:8080`. iOS 9's Camera app has no QR reader (that arrived
   in iOS 11), so scanning only works with a third-party scanner app, and some
   of those show the text without opening a browser. The launcher's address
   field is selectable and has a **Copy** button.
5. Type the 6-digit pairing code. Done — drag files onto the page, or pick
   them, and press send.

`LocalDrop-console.bat` runs the same server in a text window with the log
visible, and prints the QR code as block characters. Use it when the launcher
reports a failure you want to read.

Both scripts need no admin rights. Nothing is installed.

### Command line

```
python -m localdrop                      start the server, open a browser
python -m localdrop --port 8123          use another port
python -m localdrop --host 192.168.1.10  bind one interface only
python -m localdrop --data D:\LocalDrop  another data directory
python -m localdrop --no-browser --no-qr headless
python -m localdrop --url-only           print the address and exit
python -m localdrop --rotate-pin         make a new pairing code
python -m localdrop.launcher             the launcher window
python -m localdrop.firewall status      is the port open?
```

---

## Pairing a device

1. Start LocalDrop on the PC.
2. Same Wi-Fi. **Not** a guest network, **not** mobile data.
3. Safari → `http://<the address shown>:<port>`.
4. Enter the pairing code shown in the launcher window (or tap the QR code on
   the phone's own screen if it opened the page already).
5. The PC lists the device under **Devices**. The device sees the PC and the
   other paired devices.

The device list is honest: a device is marked **Connected** only while its
poll requests are arriving. If an iPad sleeps, it goes to *Offline* and the
transfers touching it become *Interrupted* rather than pretending to continue.
Re-pairing is not needed after that — the browser keeps its session token, so
opening the page again reconnects it with the same identity and unfinished
transfers offer **Resume**.

To remove a device permanently, use **Forget** next to its name. LocalDrop
refuses to forget a device that still owns an unfinished transfer, so nothing
can be orphaned.

---

## The launcher window

`LocalDrop.bat` / `python -m localdrop.launcher`.

The window wears the same deep-teal "still water" palette as the browser page
(see [The transfer page](#the-transfer-page)), so the two read as one product.

| Part | What it does |
| --- | --- |
| Address list | This PC's LAN addresses plus `127.0.0.1`. Click a row to select it, Ctrl-C to copy. **Copy** button does it in one press. |
| QR code | Drawn by LocalDrop's own encoder, no web service. Redraws only when the address changes. |
| Pairing code | The 6 digits a new device must type. **New code** rotates it immediately; already-paired devices stay paired. |
| Start / Stop | Starts or stops the server in this process. |
| Open in browser | Opens the host page (`http://127.0.0.1:<port>`) — the full control panel. |
| Received folder | Opens the folder where finished files land. |
| Start with Windows sign-in | Off by default; see [Start with Windows](#start-with-windows). |
| Check firewall / Allow on this network | Reads or, with your consent, writes the Windows Firewall rule for this network's own subnet. |
| Paths | Received, partials, database, log. |
| Recent activity | The tail of the log file. Scroll with the wheel. |

**Closing this window stops LocalDrop.** That is intentional: the server lives
in this process, so there is no orphaned background service you have to hunt
for in Task Manager. Minimize it instead if you want to keep transferring.

---

## The transfer page

The browser page and the launcher share one deep-teal "still water" palette —
cool porcelain with a deep-teal accent and an aqua signal for live data, plus a
dark variant behind the theme button. Layout and wording follow a few rules:

- **One primary action.** The send button names its target (*Send to Ahmadi*),
  and the device list above it is a picker rather than a row of competing
  buttons. Clear, settings and theme sit apart from the primary action.
- **Offers arrive as a sheet.** A file aimed at a device raises an
  Accept / Decline / Later sheet on that device naming the sender, the file and
  its size; nothing reaches its inbox until it accepts. The sender reads
  *Sending*, the receiver *Receiving*, and a refused offer reads *Declined*.
- **Live speed on both ends.** Every moving row shows a direction arrow
  (↑ sending, ↓ receiving), a live MB/s figure and a time-left estimate. The
  side actually moving bytes measures them from its own chunk acknowledgements;
  a side that is only watching derives its figure from the server's confirmed
  offset across polls, so both devices show an honest number instead of one
  guessing. The figure hides again when the transfer is not moving.
- **Pause on both sides where the browser allows it.** Uploads pause and resume
  per byte. A receiver on a browser that can save a Blob pulls the file in Range
  chunks and can pause that too; on iOS 9 the download is handed to Safari and
  the honest limit below applies.
- **Three layouts.** Two columns (device rail + queue) on laptops, a single
  column with two-up device cards on tablets, and a fully stacked single column
  on phones — all float / inline-block CSS that iOS 9 understands.

The transfer path is tuned for a LAN, not the internet: sockets set
`TCP_NODELAY` so small chunk acknowledgements are not held back by Nagle's
algorithm, and the default chunk is 2 MiB so a file crosses in far fewer
round-trips. What remains on an iPad 2 is the ceiling of its Wi-Fi radio, not
the protocol.

---

## Pause, resume and recovery

This is the part LocalDrop is built around, so the rules are worth reading.

**The server owns the truth.** A browser progress bar is a hint. The confirmed
byte offset is stored in SQLite on the PC and every chunk response repeats the
server's offset back to the sender; if the two disagree, the sender continues
from the server's number. A chunk sent twice (retry after a dropped response)
is recognised as a duplicate and acknowledged without writing anything.

**Resume is per byte, not per file.** Files are written to
`data/tmp/<transfer-id>.part`. Partial files are never deleted automatically,
not when a device disconnects, not when the server restarts, not when the
history is cleared.

| Event | What you see | What happens |
| --- | --- | --- |
| You press **Pause** | `Paused` | Sending stops; offset and partial file kept. **Resume** continues from that byte. |
| iPad loses Wi-Fi | `Interrupted` on both sides | Detected by the device going silent. The partial file and offset survive. When the iPad opens the page again, **Resume** is offered. |
| Safari tab closed / refreshed | Same transfer still there | Identity comes from the session token in `localStorage`, so the refresh re-joins the same device and its unfinished transfers. In Safari **Private browsing** `localStorage` is refused, so the token lives only in memory: the pairing screen says so, and a reload there needs the code again. Re-pairing then creates a *new* device identity, so a half-finished upload stays with the old entry and cannot be resumed by the new one — it must be sent again, and the PC can Cancel it or Forget the stale device. |
| Safari drops the `File` object | `Interrupted` + "re-select the file" | iOS can discard a picked file after a reload. Re-pick the *same* file: LocalDrop compares the sanitised name and the exact byte size and refuses anything else, then continues from the server's confirmed offset. The bytes already held are trusted because this transfer wrote them chunk by chunk; a name+size match is not a cryptographic re-check. |
| LocalDrop is stopped and restarted | `Interrupted` transfers are still in the queue | On start-up `reconcile()` compares each partial file with its stored offset and repairs the record to whatever is actually on disk. |
| Chunk arrives with a hole | Rejected with the server's offset | The client re-sends from where the server really is; a gap can never silently become a corrupt file. |
| The last byte arrives | `Verifying` → `Ready` | SHA-256 is computed by streaming the file from disk, then the partial file is moved into `incoming/` with a unique name. |
| You press **Cancel** | `Canceled` | The partial file is deleted. Explicit cancel is the only thing that deletes partials (apart from **Delete**). |
| Download to an iPad is interrupted | Tap the link again | `GET .../data` answers `Range`/`206` and sends `Accept-Ranges`, `ETag` and the file's SHA-256 in `X-LocalDrop-Sha256`. |

### The honest limit on downloads

When Safari on iOS downloads a file, **iOS's own download manager owns that
transfer**, not this web page. LocalDrop cannot pause, resume or restore a
native Safari download, and no web app can. What LocalDrop does is make
resuming *possible* where iOS chooses to offer it — byte-range support with a
stable `ETag`, and a retry that never starts the file from scratch when the
server already has it. If Safari shows a stopped download, tapping it again
resumes or restarts according to iOS's rules, not ours.

Uploads — the direction where the page controls the bytes — have full
pause/resume/recovery as described above.

### Nothing disappears on its own

Incomplete transfers are kept until you press **Cancel** or **Delete**, even
across restarts. Clearing finished history removes records only; the received
files stay on disk (the button says *history*, not *files*).

---

## Compatibility

**Designed** for iOS 9 Safari / iPad 2, and this is what that means in the code:

- JavaScript is ES5 only: `var`, function expressions, no arrow functions, no
  `let`/`const`, no template literals, no `async`/`await`, no destructuring, no
  `Promise`, no `Object.assign`, no optional chaining, no modules.
- Networking is `XMLHttpRequest`. Not `fetch`. Chunked upload uses
  `xhr.upload.onprogress` and `File.prototype.slice` (with the
  `webkitSlice` fallback). No WebSockets required — polling is the transport,
  and polling is also how progress and presence travel.
- No service worker, no IndexedDB requirement. Session identity lives in
  `localStorage`; when a browser refuses it — Safari Private browsing throws on
  write — the token falls back to a plain in-memory object, so it is lost on
  reload. The pairing screen says so instead of pretending nothing happened.
- CSS avoids custom properties, `grid`, `flex`, `filter`, `:has()`, sticky
  stacks and viewport-unit sizing tricks. Layout is float/table/inline-block
  based with `-webkit-` prefixes; themes are a `body.dark` class, not CSS
  variables.
- Server side is plain HTTP/1.1 with `Connection: keep-alive`, `Content-Length`
  on every response, `Accept-Ranges`, `ETag` and `304`. No HTTPS: iOS 9 will
  not accept a self-signed certificate without a profile install, and plain
  HTTP on your own LAN is the honest trade for "works on the oldest iPad".
- Chunk size defaults to 2 MiB (fewer round-trips per file) and is tunable down
  to 64 KB for flaky Wi-Fi, because an iPad 2 will not hold a large buffer.

**Tested** — see [Tests](#tests) and [docs/test-matrix.md](docs/test-matrix.md):
128 automated cases over the protocol, the transfer engine, pairing, presence,
byte ranges, QR geometry, firewall reporting and a static audit of everything in
`web/`; the real
page driven in a Chromium-based browser, both on the desktop host view and at a
395 px viewport; and a scripted client that pairs from the PC's LAN address with
an iOS 9 Safari User-Agent and streams a file in 64 KB chunks, which shows a
remote old device is treated exactly like the PC's own browser.

**Not tested** — and I will not claim otherwise:
> **I have no iPad 2, no iPhone running iOS 9 and no real Apple hardware of any
> kind in this environment.** Every "works on old Safari" statement above is a
> statement about *what the code avoids using*, verified by static audit and by
> emulated clients. It is not a substitute for a real device.
>
> To verify on hardware: connect the iPad to the same Wi-Fi, open the address,
> pair, send a small file to the PC, then a large one, then kill the Wi-Fi
> mid-transfer and resume. Anything that fails there is a real bug; please report
> it with the iOS version.

Specifically unverifiable without hardware: `File.slice` behaviour under the
iOS 9 memory pressure that kills tabs, iBooks' handling of the
`Content-Disposition` LocalDrop sends, whether the iOS 9 keyboard covers the
pairing field, and the actual throughput of an iPad 2's Wi-Fi radio.

---

## Security model

| Concern | How LocalDrop handles it |
| --- | --- |
| Who may connect | Only addresses inside the configured private subnets are answered at all; anything else gets `403`. Bound to the LAN. **Never exposed to the internet** — there is no port forwarding, no UPnP, no relay, no cloud. |
| Who may act | Pairing needs the 6-digit code shown on the PC. Success gives a random 24-byte session token (32 URL-safe characters), stored only as a SHA-256 hash in SQLite; the PIN is hashed too and compared in constant time, so a database copy does not hand out working credentials. |
| Brute force | Pairing attempts are throttled per address (8 per minute) and the throttle answers `429`. |
| PC vs phone powers | Only the browser on the PC itself (loopback) gets `role = "host"`. Admin endpoints — `/api/settings`, `/api/pin/rotate`, `/api/server/stop`, `/api/history/clear` — require host role **and** a loopback peer address, so a phone that guessed a URL cannot reconfigure the server. |
| Whose transfer is whose | Every transfer records its source and destination device id. Chunk, pause, resume, cancel, finalize and accept/reject check the session, with an explicit exception for the host so the PC can always intervene. `GET .../data` is refused for anyone but the destination device or the host. |
| Filenames | `sanitize_filename()` strips paths, control characters, reserved Windows names and leading dots; `unique_name()` prevents silent overwrite (`report.pdf`, `report (2).pdf`, …). Overwriting is never automatic. |
| Directory traversal | Written paths are built from server-generated ids only. `contains()` re-checks that a resolved path stays inside the data directory before any write. |
| Execution | Received files are stored, listed and offered for download. LocalDrop never runs, opens for parsing, imports as a module, or extracts an archive. An uploaded `.exe` is bytes in a folder. |
| Isolation | In-flight bytes go to `data/tmp/<transfer-id>.part`, a name no client controls, and only move into `incoming/` after the byte count matches and SHA-256 was computed over the file as stored. |
| Corruption | At finalize the server re-checks that the byte count matches the declared size, then computes SHA-256 by streaming the stored file, records it and serves it on downloads as `X-LocalDrop-Sha256` so either end can compare with a hash tool. iOS 9 Safari has no WebCrypto, so no browser-side digest is declared and none is claimed: what protects the upload is the offset/gap rules, not a hash handshake. |
| Storage | Files only ever land in the LocalDrop data directory (default `.\data`, next to the project). No writes to `Documents`, `Downloads`, the registry (except the opt-in sign-in entry) or anywhere else. |
| Secrets in the log | No code path formats a PIN or a session token into a log message, so `localdrop.log` is safe to attach to a bug report. |
| HTTPS | Not offered. Consequence: anyone on your LAN who can sniff traffic can read the file bytes. That is the price of supporting iOS 9 without installing a certificate profile. Do not use LocalDrop on a network you do not trust, and rotate the pairing code when you move networks. |

---

## Data and configuration

Default data directory: `.\data` next to the project, falling back to
`%LOCALAPPDATA%\LocalDrop` if that is not writable. Override with `--data PATH`
or `LOCALDROP_DATA=PATH`.

```
data/
  config.json      settings (below)
  localdrop.db     SQLite: devices, transfers, meta (WAL)
  localdrop.log    append log
  incoming/        finished files, renamed to be unique
  tmp/             <transfer-id>.part — resumable partials
```

`config.json` keys, with defaults:

| Key | Default | Meaning |
| --- | --- | --- |
| `port` | `8080` | TCP port. Changing it needs a restart; the UI says so. |
| `bind` | `"0.0.0.0"` | Interface to listen on. Set to one LAN IP to bind a single adapter. |
| `display_host` | `""` | Override the hostname shown to devices (behind a reverse proxy or fixed DNS name). |
| `chunk_size` | `2097152` | Bytes per uploaded chunk. 65536–33554432. |
| `max_upload_bytes` | `0` | Reject uploads larger than this. `0` = unlimited. |
| `parallel_uploads` | `2` | Hint sent to browsers for how many files to send at once. |
| `poll_interval_ms` | `2500` | Active polling period. |
| `poll_interval_idle_ms` | `5000` | Polling period when nothing is moving. |
| `device_timeout_s` | `15` | Silence after which a device is marked offline and its transfers *Interrupted*. |
| `require_pairing` | `true` | Whether the code is needed at all. |
| `pin` | `""` | Empty = generated on first start. |
| `auto_accept` | `false` | Accept incoming files without asking the receiving device. |
| `allow_host_without_pin` | `true` | The PC's own browser is host without typing the code. |
| `incoming_dir` / `temp_dir` | `"incoming"` / `"tmp"` | Relative to the data dir unless absolute. |
| `theme` | `"light"` | Last theme chosen in the UI. |
| `start_with_windows` | `false` | Opt-in sign-in launch. |
| `log_level` | `"info"` | `debug`, `info`, `warning`, `error`. |

Values are clamped to hard limits when read, so a bad hand-edit cannot make the
app unstartable. Unknown keys are dropped.

---

## Windows Firewall

Windows decides per port whether inbound connections are allowed. First **Start
LocalDrop**, then press **Allow on this network** in the window and answer the
Administrator prompt. If devices still cannot connect:

- Launcher → **Check firewall** reports `allowed`, `mismatch`, `program-blocked`,
  `blocked`, `unknown` or `not-windows` for the current port, with the exact
  `netsh` command.
- Launcher → **Allow on this network** asks Windows for consent once and
  adds the rule. Declining changes nothing and shows the command instead.
- Or double-click **`scripts/firewall-open.bat`** (it elevates itself).
- Or run this yourself in an Administrator prompt:

```
netsh advfirewall firewall add rule name="LocalDrop" dir=in action=allow protocol=TCP localport=8080 profile=any remoteip=localsubnet enable=yes
```

`profile=any` means the rule applies whatever category Windows gave the network.
That matters because Windows labels every new Wi-Fi network **Public** by
default, and the Public profile blocks all inbound connections — a
`profile=private` rule sits there enabled and never fires, which looks exactly
like a broken app. `remoteip=localsubnet` narrows the rule to devices on the PC's
own subnet rather than everything the interface can see.

The honest limit of that narrowing: a hotspot that hands out the same subnet
(many use `192.168.1.x`) is still inside the rule. LocalDrop's traffic is plain
HTTP and its pairing code is six digits, so on a hostile network the code and
the files can be read off the air by anyone present. If you want the stricter
behaviour, use `profile=private` instead and set your home network to Private
(Settings → Network & Internet → Wi-Fi → your network → Network profile type);
on your own router that is the accurate label and does not expose the PC to the
internet, which the router's NAT already prevents.

### `mismatch`: a rule that exists but never fires

`Check firewall` compares the rule's `Profiles:` line with the category Windows
is actually applying (`netsh advfirewall show currentprofile`). A private-only
rule on a Public network is reported as `mismatch`, with the fix in the message,
instead of `allowed` — which used to be true about the rule and false about your
devices. The default rule above covers every profile, so it cannot land in this
state; older or hand-made private-only rules can.

### `program-blocked`: a cancelled prompt beats every allow rule

When Windows shows "Windows Firewall has blocked some features of this app" and
you click **Cancel**, it keeps an inbound **BLOCK** rule for that executable
(named `TCP Query User{…}` / `UDP Query User{…}`). Block rules override allow
rules, so the port can be perfectly allowed and still dead for every device
while the PC's own browser works. `Check firewall` looks for those rules aimed
at the Python running LocalDrop and reports `program-blocked` with their names.
`scripts/firewall-open.bat` deletes them (only the auto-created `Query User`
ones, never a block you set deliberately).

Changing the port means a new rule; the old one does not hurt anything but can
be removed with

```
netsh advfirewall firewall delete rule name="LocalDrop"
```

Also worth checking if devices cannot connect: your router's **AP / client
isolation** setting (common on guest networks and some "IoT" SSIDs) blocks
device-to-device traffic entirely. No firewall rule can fix that; move to the
normal band.

---

## Start with Windows

Off by default; nothing is registered until you tick it.

Where to tick it: launcher window, or the web page's **Settings** sheet on the
PC. LocalDrop writes one value,

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run\LocalDrop
```

pointing at `pythonw.exe …\localdrop\launcher.py --minimized --start`. That
signs in with you (not before), opens the launcher minimized, and starts the
server. Unchecking removes the value. No service, no scheduled task, no admin
rights needed, nothing machine-wide.

Notes:
- If you move the folder, the entry points at the old path. Uncheck and re-check
  to refresh it; LocalDrop reports the setting as *off* when the stored command
  no longer matches, rather than showing a tick that does nothing.
- If Windows refuses the registry write, the checkbox snaps back and says so.

---

## Sending a book or PDF to the iPad

LocalDrop serves files with the type the browser needs:

| Kind | `Content-Type` | `Content-Disposition` |
| --- | --- | --- |
| PDF | `application/pdf` | `inline` (Safari renders it, then the share sheet offers Books) |
| image, text, audio, video | detected | `inline` |
| EPUB | `application/epub+zip` | `attachment` — Safari cannot view a book, so handing it to the download manager is what makes **Open in iBooks** appear |
| everything else | detected, else `application/octet-stream` | `attachment` |

`?disposition=attach` forces a download instead of a preview, and the response
always carries `Accept-Ranges: bytes`, a SHA-256-based `ETag`,
`X-Content-Type-Options: nosniff` and `Last-Modified`.

Tap the file in Safari's download list:

- **PDF** opens in Safari/QuickLook. *Share* → **Copy to Books** (iOS 12+) or
  **Open in iBooks** (iOS 9–11), or *Share* → **Save to Files**.
- **EPUB** iOS shows a preview. Use *Share* → **Open in Books / iBooks**. On
  iOS 9 an EPUB sometimes only offers *Open with iBooks* from the **Files**
  list, so: tap *Done* on the preview, open the download from the little
  download arrow, then **Open in iBooks** in the share sheet.
- If no *Books* option appears, the file reached the device but iOS does not
  recognise the type — check the extension survived (LocalDrop keeps it) and
  that the download is complete (the size shown matches the queue).

Files sent *from* the iPad go to `data/incoming` on the PC; **Received folder**
in the launcher opens it.

---

## Architecture

```
localdrop/
  __main__.py      console entry point, banner, terminal QR
  launcher.py      tkinter window, embedded server lifecycle
  server.py        HTTP/1.1 keep-alive parsing, threading server
  app.py           routing, pairing, state, settings, file serving
  transfers.py     chunk store, resume, finalize, verify, reconcile
  db.py            SQLite (WAL) schema + thread-local connections
  devices.py       device registry, presence, session tokens, PIN
  security.py      ids, tokens, filename sanitising, ranges, MIME
  netinfo.py       LAN address discovery, private-subnet test
  qr.py            QR encoder + SVG/terminal rendering (no dependency)
  config.py        config.json with defaults and clamps
  startup.py       HKCU Run key for "start with Windows"
  firewall.py      netsh status/add/remove/elevation
  logging_util.py  file + console log, plus a ring buffer for the launcher
web/
  index.html       one page: pairing, devices, queue, settings
  app.css          legacy-safe CSS, light + dark
  app.js           ES5 client: XHR, chunked upload, polling, diffing
  icon.svg         logo / favicon
tests/             128 unittest cases: protocol, transfers, security, QR,
                   firewall reporting, compatibility audit
docs/test-matrix.md the sixteen scenarios and how each was verified
LocalDrop.bat          launcher (double-click)
LocalDrop-console.bat  console server
scripts/firewall-open.bat  elevated firewall helper
requirements.txt       deliberately empty: standard library only
.gitignore             keeps data/, scratch files and caches out of git
```

### Flow

Browser → `POST /api/pair {pin}` → session token in `localStorage`. Then
`GET /api/state` every 1.5–5 s carries presence, device list, queue and offers.
An upload is: create → send chunks at the server's offset → finalize → verify.
A download is the destination device fetching `.../data`.

### API

| Method + path | Who | Purpose |
| --- | --- | --- |
| `GET /api/hello` | anyone paired-free | Is LocalDrop here? Needs pairing? |
| `POST /api/pair` | unpaired | Redeem the 6-digit code → session token |
| `GET /api/state` | device | Server info, own role, devices, transfers, settings hints |
| `GET /api/qr` | device | QR of the access URL as SVG |
| `POST /api/rename` | device | Set the name/kind the device shows as |
| `GET /api/transfers` | device | Visible queue |
| `POST /api/transfers` | device | Create `{dest, name, size, type}` → id, offset, chunk_size |
| `GET /api/transfers/<id>/status` | party | Offset/status poll |
| `PUT /api/transfers/<id>/chunk/<offset>` | source | Stream one chunk; responds with the server's offset |
| `POST /api/transfers/<id>/finalize` | source | Verify size, hash, move into `incoming/` |
| `POST /api/transfers/<id>/pause` \| `resume` \| `cancel` \| `delete` | party | Manual control. `resume` returns the offset to continue from |
| `POST /api/transfers/<id>/rebind` | source | Re-attach a re-picked `File` after a reload; name+size checked |
| `POST /api/transfers/<id>/accept` \| `reject` | destination | Answer an offer |
| `GET/HEAD /api/transfers/<id>/data` | destination, host | The bytes, with `Range`, `ETag`, `X-LocalDrop-Sha256` |
| `POST /api/files/open` | host | Open the received folder (local only) |
| `POST /api/devices/forget` | host | Drop a device and its session |
| `GET/POST /api/settings` | host + loopback | Read/change settings |
| `POST /api/pin/rotate` | host + loopback | New pairing code |
| `POST /api/history/clear` | host + loopback | Remove finished/canceled/rejected records |
| `POST /api/server/stop` | host + loopback | Ask the embedded server to shut down |

Everything else is a 404; unknown `/api/*` needs a session before it says so.

---

## Tests

```
python -m unittest discover -s tests -q
```

128 cases, no network required (each spins up the app in-process or against a
loopback socket):

- **Chunk protocol** — create, out-of-order chunk, overlapping chunk, duplicate
  retry, gap rejection, offset mismatch answer, oversized chunk refusal.
- **Resume** — pause/resume, mid-transfer disconnect → *Interrupted* → resume,
  server restart with `reconcile()` rebuilding offsets from the real partial
  file, partial-vs-final size checks, rebind with matching and mismatching
  file identity.
- **Receiver-side pause** — `test_pause_before_download_holds_the_file`,
  `test_pause_mid_download_keeps_the_bytes_already_received`: a destination can
  hold a ready file before pulling it, and pause a Range-chunk download while
  keeping the bytes it already received.
- **Integrity** — streaming SHA-256 on finalize, digest served on download,
  truncated file refused.
- **HTTP surface** — `Range` (open-ended, suffix, multi, unsatisfiable → 416),
  `HEAD`, `ETag`/`If-None-Match` → 304, `Content-Disposition` per MIME,
  keep-alive, 404/405, oversized header handling.
- **Security** — PIN hashing and constant-time compare, token hashing, filename
  sanitising (traversal, reserved names, control chars, unicode), unique naming,
  `contains()` jail, non-LAN client refusal, pairing throttle, admin endpoints
  rejecting a remote "host", ownership checks on every transfer action.
- **Presence** — heartbeat, device timeout, offline marking, forget refusing
  unfinished transfers.
- **QR** — finder/timing/format/version geometry and mask choice against
  independently computed expected modules for several byte-mode payloads.
- **Compatibility audit** — a small lexer blanks comments, string and regex
  literals in `web/app.js`, then the code is scanned for post-ES5 syntax
  (arrow, `let`/`const`, class, async, spread, template literals) and for
  browser APIs Safari did not have in 2015 (`fetch`, `Promise`, WebSocket,
  service worker, IndexedDB, `Object.assign`, `.includes`, `closest`, WebCrypto,
  and friends). `web/app.css` is scanned for custom properties, flex, grid,
  sticky, `gap`, `:has()`, viewport units, unprefixed `calc()` and modern
  filters; `web/index.html` for external assets, module scripts, and for every
  `$("id")` and `data-close` target the client expects to find. The audit
  double-checks itself by running the same scanners over synthetic modern
  JavaScript and CSS that must be rejected, so it cannot pass by quietly
  looking at nothing.
- **Storage failure** — the partial folder unopenable, a write failing
  halfway with `ENOSPC`, and a full inbox during the final move: each must
  report the bytes it really kept, keep the partial, leave no truncated file
  behind, and complete the same transfer after space is back.
- **First run** — an empty folder gains its own config, database, folders and
  6-digit code, and a device can pair against it.
- **Firewall reporting** — a `profile=private` rule on a network Windows calls
  Public must be reported as `mismatch`, not `allowed`; `Any` or a rule listing
  Public is allowed there; an unreadable profile or scope must not invent a
  mismatch; the CLI exits non-zero and explains the fix. The rule the app writes
  must cover every profile and be narrowed to `remoteip=localsubnet`, and the
  elevation path must run exactly the command it prints. A cancelled-prompt
  BLOCK rule aimed at the running interpreter must be reported as
  `program-blocked` even when the port rule is perfect.
- **Config** — clamping, unknown-key dropping, round-trip, port/path handling.

### Manual matrix

`docs/test-matrix.md` records the 16 scenarios from the brief (new file, large
file, Wi-Fi drop mid-upload, refresh mid-upload, server restart, device
disconnect/reconnect, duplicate chunk, corrupt file, two devices at once,
download resume, PIN wrong/rotated, non-LAN client, filename collision, cancel
twice, disk-full-ish refusal, cold start on another PC) with how each was
exercised and what was seen. Read its "emulated" column before trusting any
Apple-specific claim.

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| iPad cannot reach the address at all, but the PC's own browser can | Most often the Wi-Fi network is categorised **Public**, so the private-only firewall rule never applies — **Check firewall** reports `mismatch` and [Windows Firewall](#windows-firewall) has the fix. Otherwise: a different network (guest SSID, 5 GHz "IoT" band, mobile data), or router AP isolation. Loopback always works, so "it opens on my PC" proves nothing about the LAN. |
| Reaches it, but "This PC is not reachable" | LocalDrop was stopped or the port changed. Press Start again and re-read the address. |
| Pairing code rejected | It rotates when you press **New code**, and the PC's own window is the authority. Type the six digits currently shown. |
| Files stall at a few % and go *Interrupted* | The device went quiet for longer than *Device is offline after*. Wake it, reopen the tab, press **Resume**. If the Wi-Fi is genuinely bad, lower the chunk size to 256 KB in Settings. |
| Port already in use | LocalDrop says which port and URL the other instance owns. Stop that one, or pick another port in `data/config.json` and restart (firewall rule needed again for the new port). |
| Launcher window does not open | Python was installed without "tcl/tk and IDLE". Re-run the installer → Modify → tick it. Meanwhile `LocalDrop-console.bat` still works. |
| "'python' is not recognized" | Python was installed without PATH. Tick *Add python.exe to PATH* and reinstall, or use the full path to `python.exe`. |
| A received file is `name (2).ext` | A file with that name already exists. LocalDrop never overwrites silently. |
| Queue is huge after weeks | Settings → **Clear finished history**. Partial files are untouched by design. |
| Disk full mid-write | The chunk is refused with 503 and the offset LocalDrop really has; the transfer goes to *Interrupted* and the reason from Windows is stored with it. Free space, press **Resume**, and no byte is sent twice. If the last move into `incoming/` fails, the same applies with 507 and the assembled file is kept. |
| I want to see what happened | `data/localdrop.log`, or the **Recent activity** box in the launcher. |

If something breaks, the log line and the browser's iOS version are the two
things worth capturing.

---

## What LocalDrop does not do

No real AirDrop/Bonjour/multicast discovery — devices are added by opening an
address. No Bluetooth, no WebRTC, no hole-punching, no relay or sync between
networks, no end-to-end encryption or HTTPS, no resumable native Safari
downloads controlled from the page, no thumbnail or preview generation, no
antivirus scanning, no folder uploads (files are picked individually), no
multi-PC mesh, no macOS/Android-specific client app, no tray icon, and no
background service after the window closes. Each of those is either impossible
on iOS 9 Safari, needs a certificate trust dance, or would mean LocalDrop
quietly doing something you did not ask it to.
