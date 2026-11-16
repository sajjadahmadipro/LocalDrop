# 📦 LocalDrop

Local file transfer between a Windows PC and an iPad, iPhone, Android phone or
laptop on the same Wi-Fi. No cloud, no account, no install on the phones —
the receiving device just opens a web address and types a 6-digit code.

- **The PC runs one portable `LocalDrop.exe`** — Python is embedded, nothing to install. Or run from source: Python 3.8+, standard library only.
- **Receivers install nothing** — Safari, Chrome, anything that can open a page. Built to work on an **iPad 2 / iOS 9 Safari**: plain HTTP, polling, ES5 — no WebRTC, WebSockets or service workers.
- **Transfers survive real life** — pause and resume per byte across Wi-Fi drops, closed tabs and PC restarts.
- **Private by design** — files never leave your LAN; the server is never exposed to the internet.

## 🚀 Getting started

**Download and run (Windows)**

1. Grab **LocalDrop.exe** from [Releases](https://github.com/sajjadahmadipro/LocalDrop/releases/latest) and put it anywhere.
2. Run it. Press **Start LocalDrop**, then **Allow on this network** and answer the Administrator prompt (one-time firewall consent).
3. On the device, open the address shown in the window — for example `http://192.168.1.10:8080` — or scan the QR.
4. Type the 6-digit pairing code. Done — drag files onto the page and press send.

If Windows shows "Windows protected your PC", the exe is unsigned: click
**More info → Run anyway**. If devices can't connect but the PC's own browser
can, press **Check firewall** in the launcher — a `mismatch` result means an
old private-only rule is stuck on a Public network (see Troubleshooting).

**Run from source**

```
git clone https://github.com/sajjadahmadipro/LocalDrop.git
cd LocalDrop
LocalDrop.bat              launcher window
LocalDrop-console.bat      same server, text window with the log
```

**Command line**

```
python -m localdrop                      start the server, open a browser
python -m localdrop --port 8123          use another port
python -m localdrop --host 192.168.1.10  bind one interface only
python -m localdrop --data D:\LocalDrop  another data directory
python -m localdrop --url-only           print the address and exit
python -m localdrop --rotate-pin         make a new pairing code
python -m localdrop.firewall status      is the port open?
```

## 📱 Pairing a device

1. Start LocalDrop on the PC. Both devices on the same Wi-Fi — not a guest network, not mobile data.
2. Open the address shown in the launcher and type the 6-digit code (or tap the QR on the phone's own screen).
3. The PC lists the device under **Devices**; the device sees the PC and the other paired devices.

The device list is honest: **Connected** only while polls are arriving. A
sleeping iPad goes *Offline* and its transfers become *Interrupted* — no
re-pairing needed; reopening the page reconnects with the same identity and
unfinished transfers offer **Resume**. Removing a device (**Forget**) is
refused while it still owns an unfinished transfer.

## 🖥️ The launcher window

The dark left rail holds the address list and QR code (drawn by LocalDrop's
own encoder — no web service), the 6-digit pairing code with **New code** to
rotate it, **Start / Stop**, **Open in browser**, **Received folder**, the
firewall buttons, the opt-in **Start with Windows** box, the storage paths and
a live activity tail.

**Closing this window stops LocalDrop.** The server lives in this process —
there is no background service to hunt for later. Minimize it instead.

## 🔄 Transfers and recovery

**Offers arrive as a sheet.** A file aimed at a device raises
Accept / Decline on that device, naming the sender, the file and its size;
nothing reaches an inbox until it accepts. The sender reads *Sending*, the
receiver *Receiving*, a refused offer *Declined*. Every moving row shows a
direction arrow, live MB/s and time left — the side actually moving bytes
measures them from its own chunk acknowledgements; the other side derives the
figure from the server's confirmed offset.

**The server owns the truth.** The confirmed byte offset lives in SQLite on
the PC, every chunk response repeats it back, and the sender continues from
the server's number if they disagree. Resume is per byte: partial files go to
`data/tmp/<transfer-id>.part` and are never deleted automatically — only
**Cancel** or **Delete** removes one.

| Event | What you see | What happens |
| --- | --- | --- |
| You press **Pause** | `Paused` | Sending stops; offset and partial kept. **Resume** continues from that byte. |
| Device loses Wi-Fi | `Interrupted` on both sides | Partial and offset survive. Reopen the page → **Resume** is offered. |
| Tab closed or refreshed | Same transfer still there | Identity comes from the session token in `localStorage`. |
| LocalDrop is restarted | `Interrupted` transfers remain | On start-up each record is repaired to what's actually on disk. |
| Chunk arrives with a hole | Rejected with the server's offset | The client re-sends from where the server really is — a gap can never silently corrupt a file. |
| Last byte arrives | `Verifying` → `Ready` | SHA-256 is computed by streaming the file from disk, then it moves into `incoming/` with a unique name. |

Downloads carry `Accept-Ranges`, `ETag` and the file's SHA-256 in
`X-LocalDrop-Sha256`, so a re-tapped link never restarts from scratch when the
server already has the bytes. One honest limit: once iOS Safari owns a
download, iOS's own download manager controls it — LocalDrop makes resuming
*possible* where iOS chooses to offer it, but no web app can pause a native
Safari download. Uploads — where the page controls the bytes — have full
pause/resume/recovery.

## 🔒 Security model

- Only addresses inside private subnets are answered at all; anything else gets `403`. No port forwarding, no UPnP, no cloud.
- Pairing needs the 6-digit code, throttled per address (8/min). Sessions are random 24-byte tokens stored only as SHA-256 hashes; the PIN is hashed too and compared in constant time.
- Admin endpoints — settings, PIN rotation, server stop, history clear — answer only to the browser on the PC itself (host role + loopback).
- Every transfer records its source and destination; each action checks ownership. The host can always intervene but **never sees traffic between two other devices**.
- `sanitize_filename()` strips paths, control characters, reserved Windows names; `unique_name()` never overwrites silently.
- Written paths are built from server-generated ids and re-checked to stay inside the data directory.
- Received files are stored, listed and offered for download — never run, opened or extracted. An uploaded `.exe` is bytes in a folder.

## 📂 Where files live

Default is a `data` folder next to the exe (or the project); override with
`--data PATH`. Settings live in `data/config.json` — port, chunk size (2 MiB,
tunable to 64 KB), device timeout, auto-accept, theme and more. A fresh
install generates it with the defaults already in place.

```
data/
  config.json      settings
  localdrop.db     SQLite: devices, transfers
  localdrop.log    append log
  incoming/        finished files
  tmp/             resumable partials
```

## 📚 Books and PDFs to the iPad

PDFs are served `inline` — Safari renders them and the share sheet offers
Books. EPUBs are served `attachment` — handing them to the download manager is
what makes **Open in iBooks** appear. Files sent *from* the iPad land in
`data/incoming` (**Received folder** opens it).

## 🧪 Tests and compatibility

134 automated cases, no network required:

```
python -m unittest discover -s tests
```

What "designed for iOS 9" means in the code: ES5 JavaScript and
`XMLHttpRequest` with `File.prototype.slice` — no `fetch`, `Promise` or
WebSockets in the page; plain HTTP/1.1 with keep-alive, `Content-Length`,
`Accept-Ranges` and `ETag` — no HTTPS, because iOS 9 will not accept a
self-signed certificate without a profile install; CSS without grid, flex or
custom properties. A static audit enforces all of it, and a scripted client
pairs with an iOS 9 Safari User-Agent to prove a remote old device is treated
exactly like the PC's own browser.

> **I have no iPad 2 or iOS 9 device in this environment.** Every "works on
> old Safari" statement is about *what the code avoids using*, verified by
> audit and emulated clients — not a substitute for real hardware.

## 🩺 Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Other devices can't reach the address, but the PC's browser can | The Wi-Fi is probably categorised **Public**, so the private-only rule never applies. **Check firewall** reports `mismatch`. Run **Allow on this network** (accept the prompt), delete any stale LocalDrop rule, or `python -m localdrop.firewall status` for what it says. |
| Files stall at a few % and go *Interrupted* | The device went quiet longer than the device timeout. Wake it, reopen the tab, press **Resume**. Genuinely bad Wi-Fi: lower the chunk size in `config.json`. |
| Port already in use | LocalDrop says which port and URL the other instance owns. Stop that one or change `port` in `data/config.json`. |
| Launcher does not open / "'python' is not recognized" | Python was installed without "tcl/tk and IDLE", or without PATH. Fix in the installer; meanwhile `LocalDrop-console.bat` still works. |

## 📄 License

MIT — see [LICENSE](LICENSE).
