"""Local network only: the bridge binds a private address and answers private clients."""

import ipaddress
import socket
from urllib.parse import quote


def is_lan_address(value: str) -> bool:
    """A private IPv4 address of this computer: not loopback, not link-local (169.254)."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        address.version == 4
        and address.is_private
        and not address.is_loopback
        and not address.is_link_local
    )


def lan_rank(value: str) -> int:
    """Home Wi-Fi and Ethernet use 192.168/16 and 10/8; VPN tunnels (xray, WireGuard)
    and Docker or WSL switches usually take 172.16/12 and grab the default route."""
    if value.startswith("192.168."):
        return 0
    if value.startswith("10."):
        return 1
    return 2


def is_local_client(value: str) -> bool:
    """Requests from the internet are refused even if a router forwards the port."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_private or address.is_loopback


def lan_addresses() -> list[str]:
    """Private IPv4 addresses of this computer, the likely Wi-Fi / Ethernet one first.

    The default route is not trusted: with a VPN on it points at the tunnel."""
    found: list[str] = []
    try:
        # UDP connect sends nothing; it only picks the interface of the default route.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(str(info[4][0]))
    except OSError:
        pass
    addresses = [address for address in dict.fromkeys(found) if is_lan_address(address)]
    return sorted(addresses, key=lan_rank)


def deep_link(shortcut_name: str, task_url: str) -> str:
    """shortcuts://run-shortcut with the name and the task URL percent-encoded whole."""
    return (
        "shortcuts://run-shortcut?name="
        + quote(shortcut_name, safe="")
        + "&input=text&text="
        + quote(task_url, safe="")
    )
