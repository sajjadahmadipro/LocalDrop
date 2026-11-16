# 📦 LocalDrop

Send files between a Windows PC and any phone, tablet or laptop on the same
Wi-Fi. No cloud, no account, no install on the other device — it just opens a
web address and types a 6-digit code.

🖥️ **PC side** — one portable `LocalDrop.exe`, Python embedded, nothing to install
📱 **Device side** — any browser, including an iPad 2 / iOS 9 Safari
🔒 **Private by design** — files never leave your LAN, no internet access

---

## 🚀 Quick start

1. ⬇️ Download **LocalDrop.exe** from
   [Releases](https://github.com/sajjadahmadipro/LocalDrop/releases/latest)
   and put it anywhere.
2. ▶️ Run it, press **Start LocalDrop**, then **Allow on this network**
   (one-time firewall consent).
3. 📷 On the other device, open the address shown in the window or scan the QR.
4. 🔢 Type the 6-digit pairing code — then drag files in and press send.

> 🛡️ Windows may show "Windows protected your PC" (the exe is unsigned) —
> click **More info → Run anyway**.
> ⛔ Closing the window stops LocalDrop. Minimize it instead to keep transfers
> going.

## ✨ What it does

- 📤 PC → device, device → PC, or device → device on one network
- 🤝 The receiver gets an **Accept / Decline** sheet — nothing lands uninvited
- ⏸️ **Pause and resume per byte** — survives Wi-Fi drops, closed tabs, restarts
- 🧾 Finished files are SHA-256 verified and never silently overwrite
- 👀 The PC never sees transfers between two other devices
- ⚡ Live speed and time-left on both ends

## 🖥️ Run from source instead

Python 3.8+ — standard library only, no dependencies:

```
git clone https://github.com/sajjadahmadipro/LocalDrop.git
cd LocalDrop
LocalDrop.bat
```

## ⌨️ Command line

```
python -m localdrop --port 8123      use another port
python -m localdrop --data D:\LD     another data folder
python -m localdrop --url-only       print the address and exit
python -m localdrop --rotate-pin     new pairing code
```

## 🛠️ Build the .exe yourself

```
pip install pyinstaller
python -m PyInstaller --noconfirm LocalDrop.spec
```

Result: `dist/LocalDrop.exe`.

## 🔒 Honest notes

- 📡 Plain HTTP on your own LAN — that is the trade for working on an iPad 2.
  Don't use it on a network you don't trust; rotate the pairing code when you
  move networks.
- 🧱 Only devices on private subnets can connect; admin actions answer only to
  the PC itself.
- 📂 Files land in `data\incoming` next to the exe. Partial files are kept
  until you cancel or delete.

## 🧪 Tests

134 automated cases — `python -m unittest discover -s tests`

## 📄 License

MIT — see [LICENSE](LICENSE).
