"""Console entry point: `python -m localdrop`.

Prints the address devices should open, the pairing code and a QR code, then serves
until Ctrl+C. No dependencies beyond the Python standard library.
"""

import argparse
import os
import sys
import webbrowser

from . import config as config_module
from . import logging_util, netinfo, qr, server as server_module


def banner(app, port):
    lines = []
    addresses = netinfo.lan_addresses() or ["127.0.0.1"]
    lines.append("")
    lines.append("  LocalDrop %s" % _version())
    lines.append("  " + "-" * 52)
    lines.append("  Open on this PC      : http://127.0.0.1:%d" % port)
    for ip in addresses:
        lines.append("  Open on another device: http://%s:%d" % (ip, port))
    lines.append("")
    lines.append("  Pairing code         : %s" % app.registry.pin())
    lines.append("  Files are stored in  : %s" % app.config.incoming)
    lines.append("  Log                  : %s" % app.config.log_path)
    lines.append("")
    lines.append("  Windows will ask whether to allow LocalDrop on the network.")
    lines.append("  Choose Private networks / Allow, otherwise devices cannot connect.")
    lines.append("  Press Ctrl+C to stop.")
    lines.append("  " + "-" * 52)
    return "\n".join(lines)


def terminal_qr(url, level="M"):
    """Print the symbol as full-block characters; terminals that cannot show it
    still have the plain address above."""
    symbol = qr.encode(url, level=level)
    rows = []
    quiet = "  " * 2
    for row in symbol.modules:
        line = []
        for dark in row:
            line.append("\u2588\u2588" if dark else "  ")
        rows.append(quiet + "".join(line) + quiet)
    return "\n".join([""] + rows + [""])


def _version():
    from . import __version__
    return __version__


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="localdrop", description="Local network file transfer for iPad and PC")
    parser.add_argument("--port", type=int, help="TCP port (default from config)")
    parser.add_argument("--host", help="interface to bind (default 0.0.0.0)")
    parser.add_argument("--data", help="data directory override")
    parser.add_argument("--no-qr", action="store_true", help="do not draw the QR code")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--url-only", action="store_true", help="print the URL and exit")
    parser.add_argument("--pin", action="store_true", help="print the pairing code and exit")
    parser.add_argument("--rotate-pin", action="store_true", help="make a new pairing code")
    args = parser.parse_args(argv)

    if args.data:
        os.environ["LOCALDROP_DATA"] = args.data
    config = config_module.Config()
    log = logging_util.configure(config.log_path, config.get("log_level"))

    if args.rotate_pin:
        app = _build(config, log)
        print(app.registry.pin(rotate=True))
        return 0
    if args.pin:
        app = _build(config, log)
        print(app.registry.pin())
        return 0

    port = int(args.port or config.get("port"))
    if args.url_only:
        print("http://%s:%d" % (netinfo.primary_address(), port))
        return 0

    def on_ready(httpd, app):
        url = "http://127.0.0.1:%d" % app.port()
        print(banner(app, app.port()))
        sys.stdout.flush()
        if not args.no_qr:
            try:
                print(terminal_qr(app.access_url()))
            except UnicodeEncodeError:
                print("  (this terminal cannot draw the QR code - use the browser page)")
            sys.stdout.flush()
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass

    try:
        httpd = server_module.run(port=port, host=args.host, log=log, on_ready=on_ready)
    except OSError as exc:
        log.error("could not start: %s", exc)
        print("LocalDrop could not start: %s" % exc)
        print("Try another port with:  python -m localdrop --port 8123")
        return 1
    print("\nLocalDrop stopped.")
    return 0


def _build(config, log):
    from .app import Application
    app = Application(config=config, log=log)
    app.transfers.reconcile()
    return app


if __name__ == "__main__":
    sys.exit(main())
