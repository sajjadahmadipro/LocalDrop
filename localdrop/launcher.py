"""Desktop window for LocalDrop: start, stop, the addresses to open, a QR code,
firewall guidance and the log.

    python localdrop/launcher.py                 window, server stopped
    python localdrop/launcher.py --start         window, server running
    python localdrop/launcher.py --minimized     iconified (used by sign-in start)

Standard library only. tkinter ships with the python.org Windows installer; if
it is missing, run the server from a terminal with `python -m localdrop`.
"""

import os
import socket
import sys
import webbrowser

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    # Running this file directly puts localdrop/ on sys.path, not its parent.
    sys.path.insert(0, _ROOT)

import tkinter as tk
from tkinter import font as tkfont

from localdrop import firewall, logging_util, netinfo, qr, startup
from localdrop.app import Application
from localdrop.config import Config
from localdrop.server import LocalDropServer

VERSION = "1.0"
QR_SIDE = 188
LOG_LINES = 250

# "still water" palette — the same tokens the browser wears (web/app.css), so the
# window and the page read as one product. Deep teal on cool porcelain.
PAPER = "#eef2f3"        # window background
CARD = "#ffffff"         # panel surface
INK = "#102a2c"          # primary text
MUTED = "#5f7477"        # secondary text
FAINT = "#8a9a9c"        # captions, eyebrows
LINE = "#dce6e7"         # hairline borders
PRIMARY = "#0c6b62"      # deep teal accent
PRIMARY_DOWN = "#095a52"  # pressed primary
SOFT = "#e0f0ee"         # tinted secondary surface
SOFT_DOWN = "#d2e9e6"    # pressed secondary
NEUTRAL = "#e7edee"      # stopped pill / quiet chip
FIELD = "#f6f9f9"        # log + entry inset
OK = "#1a7f4e"           # success
BAD = "#c0392b"          # error


class Engine(object):
    """The server this window owns, plus the config the widgets read."""

    def __init__(self):
        self.httpd = None
        self._config = None

    @property
    def running(self):
        return self.httpd is not None

    def config(self):
        """One Config object for the window's life: reading it rewrites the file."""
        if self.httpd is not None:
            return self.httpd.app.config
        if self._config is None:
            self._config = Config()
        return self._config

    def port(self):
        if self.httpd is not None:
            return int(self.httpd.port)
        return int(self.config().get("port"))

    def pin(self):
        if self.httpd is not None:
            return self.httpd.app.registry.pin()
        return self.config().get("pin") or ""

    def start(self, host=None, port=None):
        """Returns (ok, message)."""
        if self.httpd is not None:
            return True, "Already running on port %d." % self.httpd.port
        config = self.config()
        log = logging_util.configure(config.log_path, config.get("log_level"))
        app = Application(config=config, log=log)
        app.transfers.reconcile()
        bind = host or config.get("bind")
        wanted = int(port or config.get("port"))
        try:
            httpd = LocalDropServer(bind, wanted, app=app, log=log)
        except OSError as exc:
            neighbour = find_localdrop(wanted)
            if neighbour:
                return False, ("Port %d is already used by LocalDrop at %s. Close that "
                               "window first, or set a different port in config.json."
                               % (wanted, neighbour))
            return False, "Cannot listen on %s:%d - %s" % (bind, wanted, exc)
        httpd.serve(blocking=False)
        log.info("launcher started LocalDrop on port %d", httpd.port)
        self.httpd = httpd
        return True, "Listening on port %d." % httpd.port

    def stop(self):
        if self.httpd is None:
            return False, "LocalDrop is not running."
        port = self.httpd.port
        log = self.httpd.app.log
        self.httpd.stop()
        try:
            self.httpd.app.store.close()
        except Exception:
            pass
        log.info("launcher stopped LocalDrop on port %d", port)
        self.httpd = None
        return True, "Stopped. Devices can no longer reach port %d." % port


def find_localdrop(port):
    """The URL of another LocalDrop already listening on this port, or ""."""
    try:
        sock = socket.create_connection(("127.0.0.1", int(port)), timeout=1.5)
    except OSError:
        return ""
    try:
        sock.sendall(b"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        head = sock.recv(2048).decode("latin-1", "replace")
    except OSError:
        return ""
    finally:
        sock.close()
    for line in head.split("\r\n"):
        if line.lower().startswith("server:") and "localdrop" in line.lower():
            return "http://127.0.0.1:%d" % int(port)
    return ""


class Launcher(object):
    def __init__(self, root, engine):
        self.root = root
        self.engine = engine
        self.ticks = 0
        self.shown_qr_url = None
        self.shown_log = None
        self.shown_port = None
        self.shown_addresses = None
        self.shown_paths = None

        root.title("LocalDrop %s" % VERSION)
        # Sizing to content makes an 820px-tall column of cards; the log takes up
        # the slack instead, so the window fits a laptop screen at any scaling.
        root.geometry("760x680")
        root.minsize(620, 560)
        root.configure(bg=PAPER)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        self.icon = app_icon()
        if self.icon is not None:
            root.iconphoto(True, self.icon)

        self.family = tkfont.nametofont("TkDefaultFont").actual("family")
        self.ui = tkfont.Font(family=self.family, size=10)
        self.small = tkfont.Font(family=self.family, size=9)
        self.tiny = tkfont.Font(family=self.family, size=8)
        self.mono = tkfont.nametofont("TkFixedFont").copy()
        try:
            self.mono.configure(family="Consolas", size=10)
        except tk.TclError:
            pass
        self.mono_small = self.mono.copy()
        self.mono_small.configure(size=9)

        self._build()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tick()

    # -- layout --------------------------------------------------------
    def _build(self):
        head = tk.Frame(self.root, bg=PAPER)
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        head.columnconfigure(0, weight=1)
        brand = tk.Frame(head, bg=PAPER)
        brand.grid(row=0, column=0, sticky="w")
        self.brand_img = app_icon(30)
        if self.brand_img is not None:
            tk.Label(brand, image=self.brand_img, bg=PAPER).pack(side="left", padx=(0, 10))
        tk.Label(brand, text="LocalDrop", bg=PAPER, fg=INK,
                 font=(self.family, 18, "bold")).pack(side="left")
        self.lamp = tk.Label(head, text="Stopped", bg=NEUTRAL, fg=MUTED,
                             font=(self.family, 10, "bold"), padx=13, pady=5)
        self.lamp.grid(row=0, column=1, sticky="e")
        self.hint = tk.Label(head, text="", bg=PAPER, fg=MUTED, font=self.ui,
                             anchor="w", justify="left", wraplength=560)
        self.hint.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        body = tk.Frame(self.root, bg=PAPER)
        body.grid(row=1, column=0, sticky="nsew", padx=18, pady=(2, 16))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(1, weight=1)

        left = tk.Frame(body, bg=PAPER)
        left.grid(row=0, column=0, sticky="nw", padx=(0, 14))
        right = tk.Frame(body, bg=PAPER)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)

        self._build_connect(left)
        self._build_controls(right)
        self._build_paths(right)
        self._build_log(body)

    def _card(self, parent, title, row, columnspan=1):
        frame = tk.Frame(parent, bg=CARD, highlightbackground=LINE,
                         highlightthickness=1)
        frame.grid(row=row, column=0, columnspan=columnspan, sticky="nsew", pady=(0, 12))
        inner = tk.Frame(frame, bg=CARD, padx=14, pady=12)
        inner.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        inner.columnconfigure(0, weight=1)
        tk.Label(inner, text=title.upper(), bg=CARD, fg=MUTED,
                 font=(self.family, 8, "bold")).grid(row=0, column=0, sticky="w",
                                                     pady=(0, 7))
        self.row = 1
        self.card = inner
        return inner

    def _next_row(self):
        self.row += 1
        return self.row

    def _build_connect(self, parent):
        card = self._card(parent, "Open on your devices", 0)
        self.addr_frame = tk.Frame(card, bg=CARD)
        self.addr_frame.grid(row=self._next_row(), column=0, sticky="w")

        qr_box = tk.Frame(card, bg=CARD)
        qr_box.grid(row=self._next_row(), column=0, sticky="w", pady=(12, 0))
        self.qr_canvas = tk.Canvas(qr_box, width=QR_SIDE, height=QR_SIDE, bg=CARD,
                                   highlightthickness=1, highlightbackground=LINE)
        self.qr_canvas.pack()
        tk.Label(qr_box, text="Scan it with the iPad camera", bg=CARD,
                 fg=FAINT, font=self.small).pack(anchor="w", pady=(5, 0))

        pin_row = tk.Frame(card, bg=CARD)
        pin_row.grid(row=self._next_row(), column=0, sticky="w", pady=(12, 0))
        tk.Label(pin_row, text="Pairing code", bg=CARD, fg=MUTED,
                 font=self.ui).pack(side="left")
        self.pin_value = tk.Label(pin_row, text="------", bg=CARD, fg=PRIMARY,
                                  font=(self.mono.actual("family"), 15, "bold"))
        self.pin_value.pack(side="left", padx=9)
        tk.Button(pin_row, text="New code", command=self.on_rotate, relief="flat",
                  bg=SOFT, fg=INK, font=self.small, padx=10, pady=4,
                  activebackground=SOFT_DOWN, activeforeground=INK).pack(side="left")

    def _make_button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, relief="flat", pady=7,
                         font=(self.family, 10, "bold"), padx=11,
                         bg=PRIMARY if primary else SOFT,
                         fg="#ffffff" if primary else INK,
                         activebackground=PRIMARY_DOWN if primary else SOFT_DOWN,
                         activeforeground="#ffffff" if primary else INK,
                         disabledforeground=FAINT)

    def _button(self, parent, text, command, primary=False):
        btn = self._make_button(parent, text, command, primary)
        btn.grid(row=self._next_row(), column=0, sticky="we", pady=(0, 6))
        return btn

    def _button_row(self, parent, specs):
        """Two buttons sharing one line, which keeps the window laptop-height."""
        row = tk.Frame(parent, bg=CARD)
        row.grid(row=self._next_row(), column=0, sticky="we", pady=(0, 6))
        buttons = []
        for index, (text, command, primary) in enumerate(specs):
            btn = self._make_button(row, text, command, primary)
            btn.pack(side="left", fill="x", expand=True, padx=(0, 6) if index == 0 else 0)
            buttons.append(btn)
        return buttons

    def _build_controls(self, parent):
        card = self._card(parent, "LocalDrop on this PC", 0)
        self.start_btn, self.stop_btn = self._button_row(
            card, [("Start LocalDrop", self.on_start, True), ("Stop", self.on_stop, False)])
        self._button_row(card, [("Open in browser", self.on_open_page, False),
                                ("Received folder", self.on_open_folder, False)])

        row = tk.Frame(card, bg=CARD)
        row.grid(row=self._next_row(), column=0, sticky="w", pady=(4, 0))
        self.startup_var = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="Start LocalDrop when Windows signs in",
                       variable=self.startup_var, command=self.on_startup_toggle,
                       bg=CARD, fg=INK, activebackground=CARD, activeforeground=INK,
                       selectcolor=CARD, font=self.ui).pack(side="left")

        fw = tk.Frame(card, bg=CARD)
        fw.grid(row=self._next_row(), column=0, sticky="w", pady=(10, 0))
        tk.Button(fw, text="Check firewall", command=self.on_firewall, relief="flat",
                  bg=SOFT, fg=INK, font=self.small, padx=10, pady=4,
                  activebackground=SOFT_DOWN, activeforeground=INK).pack(side="left")
        tk.Button(fw, text="Allow on this network", command=self.on_firewall_allow,
                  relief="flat", bg=SOFT, fg=INK, font=self.small, padx=10, pady=4,
                  activebackground=SOFT_DOWN, activeforeground=INK).pack(side="left", padx=(6, 0))
        self.fw_label = tk.Label(card, text="", bg=CARD, fg=MUTED,
                                 font=self.small, wraplength=340, justify="left",
                                 anchor="w")
        self.fw_label.grid(row=self._next_row(), column=0, sticky="w", pady=(6, 0))
        tk.Label(card, text="Closing this window stops LocalDrop.", bg=CARD,
                 fg=FAINT, font=self.tiny).grid(row=self._next_row(), column=0,
                                                sticky="w", pady=(7, 0))

    def _build_paths(self, parent):
        card = self._card(parent, "Storage", 1)
        self.path_labels = {}
        for label in ("Received", "Partials", "Database", "Log"):
            row = tk.Frame(card, bg=CARD)
            row.grid(row=self._next_row(), column=0, sticky="w", pady=1)
            tk.Label(row, text=label, width=9, anchor="w", bg=CARD,
                     fg=FAINT, font=self.tiny).pack(side="left")
            value = tk.Label(row, text="...", anchor="w", bg=CARD, fg=INK,
                             font=self.mono_small)
            value.pack(side="left")
            self.path_labels[label.lower()] = value

    def _build_log(self, parent):
        card = self._card(parent, "Recent activity", 1, columnspan=2)
        self.log_text = tk.Text(card, height=5, width=72, bg=FIELD, fg=INK,
                                font=self.mono, relief="flat", wrap="none", padx=9, pady=7,
                                highlightthickness=1, highlightbackground=LINE,
                                state="disabled")
        self.log_text.grid(row=self._next_row(), column=0, sticky="nsew")
        card.rowconfigure(self.row, weight=1)
        # A disabled Text still needs the wheel, or the older lines are unreachable.
        self.log_text.bind("<MouseWheel>",
                           lambda event: self.log_text.yview_scroll(-1 * int(event.delta / 120), "units"))

    # -- actions -------------------------------------------------------
    def set_hint(self, message, bad=False):
        self.hint.configure(text=message, fg=BAD if bad else MUTED)

    def on_start(self):
        ok, message = self.engine.start()
        self.set_hint(message, not ok)
        self.refresh(force=True)

    def on_stop(self):
        ok, message = self.engine.stop()
        self.set_hint(message, not ok)
        self.refresh(force=True)

    def on_open_page(self):
        if not self.engine.running:
            self.set_hint("LocalDrop is stopped - press Start first.")
            return
        webbrowser.open("http://127.0.0.1:%d" % self.engine.port())

    def on_open_folder(self):
        path = self.engine.config().incoming
        try:
            if os.name == "nt":
                os.startfile(path)   # noqa: S606 - opens the user's own inbox
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            self.set_hint("Could not open %s - %s" % (path, exc), True)

    def on_rotate(self):
        if not self.engine.running:
            self.set_hint("Start LocalDrop first: a code is only issued while it runs.")
            return
        app = self.engine.httpd.app
        pin = app.registry.pin(rotate=True)
        app.log.info("pairing code rotated from the launcher window")
        self.pin_value.configure(text=pin)
        self.set_hint("New pairing code %s. Paired devices keep working." % pin)

    def on_startup_toggle(self):
        wanted = bool(self.startup_var.get())
        ok, message = startup.apply(wanted)
        self.set_hint("Sign-in start %s." % ("turned on" if wanted else "turned off")
                      if ok else message, not ok)
        self.sync_startup_checkbox()

    def on_firewall(self):
        state, detail = firewall.status(self.engine.port())
        if state == "allowed":
            self._firewall_line("Port %d is allowed for devices on this network."
                                % self.engine.port(), good=True)
        elif state == "mismatch":
            self._firewall_line(detail, bad=True)
        elif state == "program-blocked":
            self._firewall_line("Windows is blocking Python itself - a firewall prompt "
                                "was cancelled once. Run scripts/firewall-open.bat as "
                                "administrator to remove the block rule.", bad=True)
        elif state == "unknown":
            self._firewall_line("Windows would not say: %s" % first_line(detail), bad=True)
        elif state == "not-windows":
            self._firewall_line(detail)
        else:
            self._firewall_line("No rule yet - Windows may be blocking devices. "
                                "Use Allow on this network.", bad=True)

    def on_firewall_allow(self):
        ok, message = firewall.elevate_and_add(self.engine.port())
        if ok:
            # Adding a private-network rule does not mean this network is private.
            self._firewall_line(message + " Press Check firewall to see whether it applies.")
        else:
            self._firewall_line(first_line(message), bad=True)
            logging_util.get_logger("launcher").info("firewall: %s", message)

    def _firewall_line(self, message, good=False, bad=False):
        self.fw_label.configure(text=message, fg=OK if good else
                                (BAD if bad else MUTED))

    def on_close(self):
        if self.engine.running:
            self.engine.stop()
        self.root.destroy()

    def sync_startup_checkbox(self):
        try:
            self.startup_var.set(startup.is_enabled())
        except Exception:
            pass

    # -- refresh -------------------------------------------------------
    def tick(self):
        # Looking up interfaces and folders costs a socket plus four directory
        # calls, so the slow half of the refresh runs every fifth second - but
        # always first, so a new window is complete the moment it appears.
        self.refresh(slow=self.ticks % 5 == 0)
        self.ticks += 1
        self.root.after(1000, self.tick)

    def refresh(self, force=False, slow=True):
        running = self.engine.running
        self.lamp.configure(text="Running" if running else "Stopped",
                            bg=SOFT if running else NEUTRAL,
                            fg=PRIMARY if running else MUTED)
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self._refresh_log()
        if not (force or slow):
            return

        config = self.engine.config()
        port = self.engine.port()
        self.pin_value.configure(text=self.engine.pin() or "------")
        addresses = netinfo.lan_addresses() or ["127.0.0.1"]
        paths = (("received", config.incoming), ("partials", config.temp),
                 ("database", config.db_path), ("log", config.log_path))
        if force or port != self.shown_port or addresses != self.shown_addresses:
            self.shown_port = port
            self.shown_addresses = addresses
            self._rebuild_addresses(port, addresses, running)
        if force or paths != self.shown_paths:
            self.shown_paths = paths
            for key, path in paths:
                self.path_labels[key].configure(text=shorten(path))
            self.sync_startup_checkbox()

        self._draw_qr(running, port, self.shown_addresses or addresses)

    def _rebuild_addresses(self, port, addresses, running):
        for child in self.addr_frame.winfo_children():
            child.destroy()
        self.addr_vars = []
        rows = [("This PC", "http://127.0.0.1:%d" % port)]
        rows += [("Network", "http://%s:%d" % (ip, port)) for ip in addresses[:3]]
        for label, url in rows:
            row = tk.Frame(self.addr_frame, bg=CARD)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, width=7, anchor="w", bg=CARD,
                     fg=MUTED, font=self.tiny).pack(side="left")
            var = tk.StringVar(value=url)
            self.addr_vars.append(var)
            entry = tk.Entry(row, textvariable=var, font=self.mono,
                             relief="flat", width=26,
                             bg=CARD, fg=INK, highlightthickness=1,
                             highlightbackground=LINE, readonlybackground=CARD,
                             state="readonly")
            entry.pack(side="left", padx=(2, 0), ipady=2)
            entry.bind("<Button-1>", lambda event, widget=entry: widget.select_range(0, "end"))
            entry.bind("<Control-c>", lambda event: self.copy(event.widget.get()))
            tk.Button(row, text="Copy", relief="flat", bg=CARD, fg=PRIMARY,
                      font=(self.family, 8, "bold"), padx=5,
                      activebackground=SOFT, activeforeground=PRIMARY,
                      command=lambda u=url: self.copy(u)).pack(side="left", padx=(5, 0))
        if not running:
            tk.Label(self.addr_frame, text="They answer once LocalDrop is running.",
                     bg=CARD, fg=FAINT, font=self.tiny).pack(anchor="w", pady=(3, 0))

    def copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_hint("Copied %s to the clipboard" % text)

    def _draw_qr(self, running, port, addresses):
        url = "http://%s:%d" % (addresses[0], port) if running else ""
        if url == self.shown_qr_url:
            return
        self.shown_qr_url = url
        canvas = self.qr_canvas
        canvas.delete("all")
        if not url:
            canvas.create_text(QR_SIDE // 2, QR_SIDE // 2, width=QR_SIDE - 34,
                               text="Start LocalDrop\nto get a code", font=self.ui,
                               fill=FAINT)
            return
        try:
            symbol = qr.encode(url, level="M")
        except Exception as exc:
            canvas.create_text(QR_SIDE // 2, QR_SIDE // 2, width=QR_SIDE - 24,
                               text="code failed: %s" % exc, font=self.small,
                               fill=BAD)
            return
        quiet = 3
        scale = max(2, QR_SIDE // (symbol.size + quiet * 2))
        offset = (QR_SIDE - scale * (symbol.size + quiet * 2)) // 2
        for row in range(symbol.size):
            modules = symbol.modules[row]
            col = 0
            while col < symbol.size:
                if not modules[col]:
                    col += 1
                    continue
                run = col
                while run < symbol.size and modules[run]:
                    run += 1
                canvas.create_rectangle(offset + (col + quiet) * scale,
                                        offset + (row + quiet) * scale,
                                        offset + (run + quiet) * scale,
                                        offset + (row + quiet + 1) * scale,
                                        fill=INK, outline="")
                col = run

    def _refresh_log(self):
        text = "\n".join(logging_util.recent_lines(LOG_LINES))
        if text == self.shown_log:
            return
        self.shown_log = text
        widget = self.log_text
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")
        widget.see("end")


def app_icon(size=32):
    """The LocalDrop mark, drawn pixel by pixel so no .ico file has to ship."""
    try:
        image = tk.PhotoImage(width=size, height=size)
    except tk.TclError:
        return None
    radius = size / 2.0
    for y in range(size):
        for x in range(size):
            dx = x + 0.5 - radius
            dy = y + 0.5 - radius
            if dx * dx + dy * dy > radius * radius:
                continue
            u, v = x / float(size), y / float(size)
            stem = 0.44 <= u <= 0.56 and 0.20 <= v <= 0.50
            head = 0.50 <= v <= 0.66 and abs(u - 0.5) <= (0.66 - v) * 0.72
            base = 0.30 <= u <= 0.70 and 0.74 <= v <= 0.81
            image.put("#ffffff" if (stem or head or base) else PRIMARY,
                      to=(x, y, x + 1, y + 1))
    return image


def shorten(path, room=34):
    text = str(path)
    home = os.path.expanduser("~")
    if text.startswith(home):
        text = "~" + text[len(home):]
    if len(text) <= room:
        return text
    # Cut the middle: the drive and the last folder are the parts that identify it.
    head = text[:room // 3]
    return head + "\u2026" + text[-(room - len(head) - 1):]


def first_line(text):
    return str(text or "").splitlines()[0] if str(text or "").strip() else ""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    engine = Engine()
    root = tk.Tk()
    window = Launcher(root, engine)
    if "--start" in argv:
        ok, message = engine.start()
        window.set_hint(message, not ok)
        if not ok:
            logging_util.get_logger("launcher").error("start failed: %s", message)
    window.refresh(force=True)
    if "--minimized" in argv:
        root.withdraw()
        root.after(150, root.iconify)
    root.mainloop()
    return 0


if __name__ == "__main__":
    main()