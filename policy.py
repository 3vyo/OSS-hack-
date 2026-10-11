"""URL/network rules, redirects, limits, .test mapping, route handler."""

import asyncio
import ipaddress
import mimetypes
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urldefrag, urlsplit

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {None, 80, 443}
MAX_REDIRECTS = 5
MAX_BODY_BYTES = 2_000_000
TIMEOUT_SECONDS = 15

FIXTURES = (Path(__file__).resolve().parent / "fixtures").resolve()


class PolicyError(Exception):
    """A URL or response the rules refuse."""


def check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise PolicyError(f"only http and https links are opened, not {parts.scheme or 'a link without a scheme'}")
    if not parts.hostname:
        raise PolicyError("the link has no host name")
    try:
        port = parts.port
    except ValueError:
        raise PolicyError("the link has an invalid port number") from None
    if port not in ALLOWED_PORTS and not is_demo_host(parts.hostname):
        raise PolicyError(f"only the standard web ports are opened, not port {port}")


def is_demo_host(host: str | None) -> bool:
    return bool(host) and host.endswith(".test")


def is_forbidden_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Private, loopback, link-local, reserved and multicast addresses are never contacted."""
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


async def check_destination(url: str) -> None:
    """Refuse hosts that are, or resolve to, a non-public address.

    Static mode still has a DNS-rebinding gap: httpx resolves the name again when it connects.
    """
    host = urlsplit(url).hostname or ""
    if is_demo_host(host):
        return
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError):
            raise PolicyError(f"could not find an address for {host}") from None
        addresses = {ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos}
    for ip in addresses:
        if is_forbidden_address(ip):
            raise PolicyError(f"{host} points to a private or local address ({ip}), which is never opened")


@dataclass(frozen=True)
class Fixture:
    status: int
    headers: dict[str, str]
    body: bytes


def _load_redirects() -> dict[str, tuple[int, str]]:
    redirects = {}
    path = FIXTURES / "_redirects.txt"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                url, status, location = line.split()
                redirects[url] = (int(status), location)
    return redirects


REDIRECTS = _load_redirects()


def fixture_for(url: str) -> Fixture | None:
    """Answer a .test URL from fixtures/, or None for any other host."""
    parts = urlsplit(url)
    if not is_demo_host(parts.hostname):
        return None
    url = urldefrag(url).url
    if url in REDIRECTS:
        status, location = REDIRECTS[url]
        return Fixture(status, {"location": location}, b"")
    host_dir = (FIXTURES / parts.hostname).resolve()
    candidate = (host_dir / parts.path.lstrip("/")).resolve()
    if candidate.is_dir():
        candidate = candidate / "index.html"
    if not candidate.is_relative_to(host_dir) or not candidate.is_file():
        return Fixture(404, {"content-type": "text/plain"}, b"Not found")
    # Extensionless demo paths such as /login are pages.
    content_type = mimetypes.guess_type(candidate.name)[0] or (
        "text/html" if not candidate.suffix else "application/octet-stream")
    if content_type.startswith("text/") or content_type.endswith("javascript"):
        content_type += "; charset=utf-8"
    return Fixture(200, {"content-type": content_type}, candidate.read_bytes())
