"""Local network interface discovery (no third-party dependencies)."""

import ipaddress
import socket

# Loopback, the three RFC 1918 ranges and link-local: the only places a
# LocalDrop client can come from.
_LAN_NETS = [ipaddress.ip_network(cidr) for cidr in
             ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
              "169.254.0.0/16")]


def _default_route_ip():
    """Interface holding the default route, revealed without sending traffic."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 53))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def lan_addresses(include_loopback=False):
    """Candidate IPv4 addresses for this machine, most-usable first."""
    found = []
    seen = set()

    def add(ip):
        if not ip or ip in seen:
            return
        if ip.startswith("127.") and not include_loopback:
            return
        seen.add(ip)
        found.append(ip)

    try:
        add(_default_route_ip())
    except OSError:
        pass

    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except (OSError, UnicodeError):
        infos = []
    for info in infos:
        add(info[4][0])

    return found


def primary_address():
    addrs = lan_addresses()
    return addrs[0] if addrs else "127.0.0.1"


def is_local_network_ip(ip):
    """True only for addresses that can legitimately reach a LAN-only server.

    `ipaddress.is_private` is deliberately not used: it also covers the
    documentation and benchmarking ranges, which are not local networks.
    """
    try:
        addr = ipaddress.ip_address(str(ip))
    except ValueError:
        return False
    if addr.version != 4:
        return False
    return any(addr in net for net in _LAN_NETS)
