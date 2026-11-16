"""Desktop window for LocalDrop: start, stop, the addresses to open, a QR code,
firewall guidance and the log.

    python localdrop/launcher.py                 window, server stopped
    python localdrop/launcher.py --start         window, server running
    python localdrop/launcher.py --minimized     iconified (used by sign-in start)

Standard library only. tkinter ships with the python.org Windows installer; if
it is missing, run the server from a terminal with `python -m localdrop`.

The window opts into Windows DPI awareness before Tk starts: without it Windows
bitmap-stretches the whole window, which is what made every label look soft.
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
LOG_LINES = 250

# The window wears the same tokens as the browser (web/app.css): a dark navy
# rail beside the porcelain page, deep blue accents, light-blue signals.
PAPER = "#eef4f9"          # main background
CARD = "#ffffff"           # panel surface
INK = "#1b262c"            # primary text
MUTED = "#5a6b76"          # secondary text
FAINT = "#8ca3b3"          # captions
LINE = "#d3e2ee"           # hairline borders
PRIMARY = "#0f4c75"        # deep blue accent
PRIMARY_DOWN = "#0c3e60"   # pressed primary
SOFT = "#bbe1fa"           # light-blue signal surface
SOFT_DOWN = "#a9d6f5"      # pressed signal surface
NEUTRAL = "#e6eef4"        # quiet button surface
NEUTRAL_DOWN = "#dbe7f0"   # pressed quiet surface
FIELD = "#f6f9f9"          # entry + log inset
OK = "#1a7f4e"             # success
BAD = "#c0392b"            # error

# The rail (left sidebar) uses the dark theme tokens.
RAIL_BG = "#10181e"        # rail background
RAIL_CARD = "#1e2e38"      # rail button surface
RAIL_CARD_DOWN = "#274050" # pressed rail button
RAIL_LINE = "#33475a"      # rail hairline
RAIL_INK = "#e8f1f8"       # rail primary text
RAIL_MUTED = "#8ca3b3"     # rail secondary text
RAIL_FAINT = "#5f7480"     # rail captions


def _enable_dpi_awareness():
    """Ask Windows to stop bitmap-stretching this process. Must run before the
    first window exists; per-monitor with graceful fallbacks to older calls."""
    if os.name != "nt":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _display_scale():
    """The system display scale as a float (1.0, 1.25, 1.5, ...)."""
    if os.name != "nt":
        return 1.0
    import ctypes
    try:
        return max(1.0, ctypes.windll.shcore.GetScaleFactorForDevice(0) / 100.0)
    except Exception:
        return 1.0


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

        self.s = _display_scale()
        try:
            # Point-sized fonts multiply by Tk's scaling: tell it the real DPI so
            # every text renders at its true size instead of being guessed at 96.
            root.tk.call("tk", "scaling", 96.0 * self.s / 72.0)
        except Exception:
            pass

        root.title("LocalDrop %s" % VERSION)
        root.geometry("%dx%d" % (self.px(940), self.px(680)))
        root.minsize(self.px(780), self.px(580))
        root.configure(bg=PAPER)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        self.icon = app_icon(self.px(34))
        if self.icon is not None:
            root.iconphoto(True, self.icon)

        self.family = tkfont.nametofont("TkDefaultFont").actual("family")
        self.f_title = tkfont.Font(family=self.family, size=17, weight="bold")
        self.f_hero = tkfont.Font(family=self.family, size=16, weight="bold")
        self.f_section = tkfont.Font(family=self.family, size=11, weight="bold")
        self.f_pill = tkfont.Font(family=self.family, size=9, weight="bold")
        self.ui = tkfont.Font(family=self.family, size=10)
        self.small = tkfont.Font(family=self.family, size=9)
        self.tiny = tkfont.Font(family=self.family, size=8)
        self.btn_font = tkfont.Font(family=self.family, size=10, weight="bold")
        self.small_btn = tkfont.Font(family=self.family, size=9, weight="bold")
        mono_family = "Consolas"
        self.mono = tkfont.Font(family=mono_family, size=10)
        self.mono_small = tkfont.Font(family=mono_family, size=9)
        self.pin_font = tkfont.Font(family=mono_family, size=17, weight="bold")

        self._build()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tick()

    def px(self, value):
        return int(round(value * self.s))

    # -- layout --------------------------------------------------------
    def _build(self):
        rail = tk.Frame(self.root, bg=RAIL_BG, width=self.px(250))
        rail.grid(row=0, column=0, sticky="ns")
        rail.pack_propagate(False)
        self._build_rail(rail)

        main = tk.Frame(self.root, bg=PAPER)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        self._build_main(main)

    def _build_rail(self, rail):
        pad = dict(padx=self.px(18))
        head = tk.Frame(rail, bg=RAIL_BG)
        head.pack(fill="x", pady=(self.px(20), 0), **pad)
        brand_img = app_icon(self.px(38), bg=SOFT, glyph=INK)
        if brand_img is not None:
            self._rail_icon = brand_img
            tk.Label(head, image=brand_img, bg=RAIL_BG).pack(side="left",
                                                             padx=(0, self.px(11)))
        name_box = tk.Frame(head, bg=RAIL_BG)
        name_box.pack(side="left")
        tk.Label(name_box, text="LocalDrop", bg=RAIL_BG, fg=RAIL_INK,
                 font=self.f_title).pack(anchor="w")
        tk.Label(name_box, text="Local network file sharing", bg=RAIL_BG,
                 fg=RAIL_FAINT, font=self.tiny).pack(anchor="w")

        self.lamp = tk.Label(rail, text="\u25cf  Stopped", bg=RAIL_CARD,
                             fg=RAIL_MUTED, font=self.f_pill,
                             padx=self.px(12), pady=self.px(6), anchor="w")
        self.lamp.pack(fill="x", pady=(self.px(18), self.px(10)), **pad)

        self.start_btn = self._rail_button(rail, "Start LocalDrop", self.on_start,
                                           "primary")
        self.stop_btn = self._rail_button(rail, "Stop", self.on_stop, "plain")
        self._rail_button(rail, "Open in browser", self.on_open_page, "plain")
        self._rail_button(rail, "Received folder", self.on_open_folder, "plain")

        self.startup_var = tk.BooleanVar(value=False)
        tk.Checkbutton(rail, text="Start when Windows signs in",
                       variable=self.startup_var, command=self.on_startup_toggle,
                       bg=RAIL_BG, fg=RAIL_MUTED, activebackground=RAIL_BG,
                       activeforeground=RAIL_INK, selectcolor=RAIL_CARD,
                       font=self.small, relief="flat", highlightthickness=0,
                       borderwidth=0).pack(anchor="w", pady=(self.px(10), 0), **pad)

        tk.Frame(rail, bg=RAIL_BG).pack(fill="both", expand=True)

        paths = tk.Frame(rail, bg=RAIL_BG)
        paths.pack(fill="x", pady=(0, self.px(6)), **pad)
        self.path_labels = {}
        for label in ("Received", "Partials", "Database", "Log"):
            row = tk.Frame(paths, bg=RAIL_BG)
            row.pack(anchor="w", pady=0)
            tk.Label(row, text=label, width=9, anchor="w", bg=RAIL_BG,
                     fg=RAIL_FAINT, font=self.tiny).pack(side="left")
            value = tk.Label(row, text="...", anchor="w", bg=RAIL_BG,
                             fg=RAIL_MUTED, font=self.mono_small)
            value.pack(side="left")
            self.path_labels[label.lower()] = value

        tk.Label(rail, text="Closing this window stops LocalDrop.", bg=RAIL_BG,
                 fg=RAIL_FAINT, font=self.tiny).pack(anchor="w",
                                                     pady=(0, self.px(16)), **pad)

    def _rail_button(self, rail, text, command, kind):
        primary = kind == "primary"
        btn = tk.Button(rail, text=text, command=command, relief="flat",
                        bg=SOFT if primary else RAIL_CARD,
                        fg=INK if primary else RAIL_INK,
                        font=self.btn_font, padx=self.px(12), pady=self.px(9),
                        activebackground=SOFT_DOWN if primary else RAIL_CARD_DOWN,
                        activeforeground=INK if primary else RAIL_INK,
                        highlightthickness=0 if primary else 1,
                        highlightbackground=RAIL_LINE,
                        disabledforeground="#7fa3ba" if primary else RAIL_FAINT,
                        cursor="hand2", anchor="center")
        hover = SOFT_DOWN if primary else RAIL_CARD_DOWN
        normal = SOFT if primary else RAIL_CARD
        btn.pack(fill="x", pady=(0, self.px(8)), padx=self.px(18))
        btn.bind("<Enter>", lambda event: self._paint(btn, hover)
                 if str(btn["state"]) == "normal" else None)
        btn.bind("<Leave>", lambda event: self._paint(btn, normal))
        return btn

    def _build_main(self, main):
        head = tk.Frame(main, bg=PAPER)
        head.grid(row=0, column=0, sticky="ew",
                  padx=self.px(26), pady=(self.px(22), 0))
        tk.Label(head, text="Share files on your network", bg=PAPER, fg=INK,
                 font=self.f_hero).pack(anchor="w")
        self.hint = tk.Label(head, text="", bg=PAPER, fg=MUTED, font=self.small,
                             anchor="w", justify="left", wraplength=self.px(620))
        self.hint.pack(anchor="w", pady=(self.px(4), 0))

        hero = tk.Frame(main, bg=PAPER)
        hero.grid(row=1, column=0, sticky="nsew", padx=self.px(26),
                  pady=(self.px(14), 0))
        hero.columnconfigure(1, weight=1)
        hero.rowconfigure(0, weight=1)

        tile = tk.Frame(hero, bg=CARD, highlightbackground=LINE,
                        highlightthickness=1)
        tile.grid(row=0, column=0, sticky="nsew", padx=(0, self.px(20)))
        qr_side = self.px(268)
        canvas = tk.Canvas(tile, width=qr_side, height=qr_side, bg=CARD,
                           highlightthickness=0)
        canvas.pack(padx=self.px(16), pady=(self.px(16), self.px(6)))
        self.qr_side = qr_side
        self.qr_canvas = canvas
        tk.Label(tile, text="Scan with a phone or tablet camera", bg=CARD,
                 fg=FAINT, font=self.tiny).pack(pady=(0, self.px(14)))

        side = tk.Frame(hero, bg=PAPER)
        side.grid(row=0, column=1, sticky="nsew")
        self._build_side(side)

        activity = tk.Frame(main, bg=CARD, highlightbackground=LINE,
                            highlightthickness=1)
        activity.grid(row=2, column=0, sticky="ew",
                      padx=self.px(26), pady=(self.px(16), self.px(22)))
        inner = tk.Frame(activity, bg=CARD, padx=self.px(16), pady=self.px(12))
        inner.pack(fill="both", expand=True)
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(1, weight=1)
        tk.Label(inner, text="Recent activity", bg=CARD, fg=INK,
                 font=self.f_section).grid(row=0, column=0, sticky="w",
                                           pady=(0, self.px(8)))
        self.log_text = tk.Text(inner, height=5, bg=FIELD, fg=INK,
                                font=self.mono, relief="flat", wrap="none",
                                padx=self.px(10), pady=self.px(8),
                                highlightthickness=1, highlightbackground=LINE,
                                state="disabled")
        self.log_text.grid(row=1, column=0, sticky="ew")
        # A disabled Text still needs the wheel, or the older lines are unreachable.
        self.log_text.bind("<MouseWheel>",
                           lambda event: self.log_text.yview_scroll(-1 * int(event.delta / 120), "units"))

    def _build_side(self, side):
        self.addr_frame = tk.Frame(side, bg=PAPER)
        self.addr_frame.pack(fill="x")

        tk.Frame(side, bg=LINE, height=1).pack(fill="x", pady=self.px(14))

        pin_row = tk.Frame(side, bg=PAPER)
        pin_row.pack(fill="x")
        tk.Label(pin_row, text="Pairing code", bg=PAPER, fg=MUTED,
                 font=self.ui).pack(side="left")
        self.pin_value = tk.Label(pin_row, text="\u2014\u2014\u2014\u2014\u2014\u2014",
                                  bg=PAPER, fg=PRIMARY, font=self.pin_font)
        self.pin_value.pack(side="left", padx=self.px(10))
        self._side_button(pin_row, "New code", self.on_rotate).pack(side="right")

        tk.Frame(side, bg=LINE, height=1).pack(fill="x", pady=self.px(14))

        fw_row = tk.Frame(side, bg=PAPER)
        fw_row.pack(fill="x")
        tk.Label(fw_row, text="Firewall", bg=PAPER, fg=MUTED,
                 font=self.ui).pack(side="left")
        self._side_button(fw_row, "Check", self.on_firewall).pack(
            side="left", padx=(self.px(12), 0))
        self._side_button(fw_row, "Allow on this network",
                          self.on_firewall_allow).pack(side="left",
                                                       padx=(self.px(8), 0))
        self.fw_label = tk.Label(side, text="", bg=PAPER, fg=MUTED,
                                 font=self.small, wraplength=self.px(430),
                                 justify="left", anchor="w")
        self.fw_label.pack(anchor="w", pady=(self.px(8), 0))

    def _side_button(self, parent, text, command):
        btn = tk.Button(parent, text=text, command=command, relief="flat",
                        bg=PAPER, fg=PRIMARY, font=self.small_btn,
                        padx=self.px(8), pady=self.px(3),
                        activebackground=SOFT, activeforeground=PRIMARY,
                        highlightthickness=0, disabledforeground=FAINT,
                        cursor="hand2")
        btn.bind("<Enter>", lambda event: self._paint(btn, SOFT))
        btn.bind("<Leave>", lambda event: self._paint(btn, PAPER))
        return btn

    def _paint(self, widget, color):
        try:
            widget.configure(background=color)
        except tk.TclError:
            pass

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
            self._firewall_line("Windows is blocking LocalDrop itself - a firewall "
                                "prompt was cancelled once. Run scripts/firewall-open.bat as "
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
        self.lamp.configure(text="\u25cf  Running" if running else "\u25cf  Stopped",
                            fg=SOFT if running else RAIL_MUTED)
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        self._refresh_log()
        if not (force or slow):
            return

        config = self.engine.config()
        port = self.engine.port()
        self.pin_value.configure(text=self.engine.pin() or "\u2014\u2014\u2014\u2014\u2014\u2014")
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
                # Short room: these live in the narrow rail.
                self.path_labels[key].configure(text=shorten(path, room=22))
            self.sync_startup_checkbox()

        self._draw_qr(running, port, self.shown_addresses or addresses)

    def _rebuild_addresses(self, port, addresses, running):
        for child in self.addr_frame.winfo_children():
            child.destroy()
        self.addr_vars = []
        rows = [("This PC", "http://127.0.0.1:%d" % port)]
        rows += [("Network", "http://%s:%d" % (ip, port)) for ip in addresses[:2]]
        for label, url in rows:
            row = tk.Frame(self.addr_frame, bg=PAPER)
            row.pack(fill="x", pady=self.px(3))
            tk.Label(row, text=label, width=8, anchor="w", bg=PAPER,
                     fg=MUTED, font=self.tiny).pack(side="left")
            var = tk.StringVar(value=url)
            self.addr_vars.append(var)
            entry = tk.Entry(row, textvariable=var, font=self.mono,
                             relief="flat", width=28,
                             bg=FIELD, fg=INK, highlightthickness=1,
                             highlightbackground=LINE,
                             readonlybackground=FIELD, state="readonly",
                             insertbackground=INK)
            entry.pack(side="left", padx=(2, 0), ipady=self.px(5))
            entry.bind("<Button-1>", lambda event, widget=entry: widget.select_range(0, "end"))
            entry.bind("<Control-c>", lambda event: self.copy(event.widget.get()))
            copy_btn = tk.Button(row, text="Copy", relief="flat", bg=PAPER,
                                 fg=PRIMARY, font=self.small_btn, padx=self.px(8),
                                 activebackground=SOFT, activeforeground=PRIMARY,
                                 cursor="hand2",
                                 command=lambda u=url: self.copy(u))
            copy_btn.pack(side="right", padx=(self.px(6), 0))
            copy_btn.bind("<Enter>", lambda event, w=copy_btn: self._paint(w, SOFT))
            copy_btn.bind("<Leave>", lambda event, w=copy_btn: self._paint(w, PAPER))
        if not running:
            tk.Label(self.addr_frame, text="They answer once LocalDrop is running.",
                     bg=PAPER, fg=FAINT, font=self.tiny).pack(anchor="w",
                                                              pady=(self.px(3), 0))

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
            canvas.create_text(self.qr_side // 2, self.qr_side // 2,
                               width=self.qr_side - self.px(34),
                               text="Start LocalDrop\nto get a code", font=self.ui,
                               fill=FAINT)
            return
        try:
            symbol = qr.encode(url, level="M")
        except Exception as exc:
            canvas.create_text(self.qr_side // 2, self.qr_side // 2,
                               width=self.qr_side - self.px(24),
                               text="code failed: %s" % exc, font=self.small,
                               fill=BAD)
            return
        # Four clear modules on every side: cameras refuse to lock onto a
        # symbol that runs to its own edge.
        quiet = 4
        scale = max(3, self.qr_side // (symbol.size + quiet * 2))
        total = scale * (symbol.size + quiet * 2)
        offset = (self.qr_side - total) // 2
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


def app_icon(size=32, bg=PRIMARY, glyph="#ffffff"):
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
            image.put(glyph if (stem or head or base) else bg,
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
    _enable_dpi_awareness()
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
