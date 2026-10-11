"""Local policy proxy: checks every Chromium connection.

Playwright's route handlers only see the first URL of a redirect chain, so the network rules are
enforced here instead, where every connection has to pass: redirect steps, popups, WebSockets.
The proxy resolves each host itself and connects to the exact address it checked, which also
stops DNS rebinding.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import policy

HEAD_TIMEOUT = 30
CONNECT_TIMEOUT = 10
# For plain HTTP, Chromium shows the proxy's 403 as if the site sent it; this header tells them apart.
REFUSED_HEADER = "x-linkinspector-refused"
BAD_REQUEST = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
# Hop-by-hop headers that belong to the proxy connection, not the server.
_DROP_HEADERS = ("proxy-connection:", "proxy-authorization:", "connection:", "keep-alive:")


@dataclass(frozen=True)
class ProxyEvent:
    host: str
    port: int
    allowed: bool
    detail: str  # the address connected to, or why the connection was refused


def _split_host_port(target: str) -> tuple[str, int]:
    host, _, port = target.rpartition(":")
    return host.strip("[]"), int(port)


class PolicyProxy:
    def __init__(self, allowed_ports: set[int] | None = None,
                 is_forbidden: Callable[[ipaddress.IPv4Address | ipaddress.IPv6Address], bool] | None = None,
                 test_hosts: dict[str, tuple[str, int]] | None = None) -> None:
        self.allowed_ports = allowed_ports or {80, 443}
        self.is_forbidden = is_forbidden or policy.is_forbidden_address
        # Test-only exception: these host names go to a fixed local server, skipping the checks.
        self.test_hosts = test_hosts or {}
        self.events: list[ProxyEvent] = []
        self.port: int | None = None
        self._server: asyncio.Server | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self._server is None:
            return
        self._server.close()
        # Open tunnels would otherwise keep running (and Python 3.12's wait_closed waits for them).
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._server.wait_closed()
        self._server = None

    async def _open(self, host: str, port: int):
        """Resolve, check every address, then connect to an address that was checked.

        Returns the open streams, or the reason the connection was refused.
        """
        if host in self.test_hosts:
            address, test_port = self.test_hosts[host]
            self.events.append(ProxyEvent(host, port, True, f"test host {address}:{test_port}"))
            return await asyncio.open_connection(address, test_port)
        if port not in self.allowed_ports:
            return self._refuse(host, port, f"port {port} isn't a standard web port")
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            return self._refuse(host, port, "could not find an address for it")
        addresses = list(dict.fromkeys(info[4][0] for info in infos))
        for address in addresses:
            if self.is_forbidden(ipaddress.ip_address(address.split("%")[0])):
                return self._refuse(host, port, f"it points to a private or local address ({address})")
        for address in addresses:
            try:
                streams = await asyncio.wait_for(asyncio.open_connection(address, port), CONNECT_TIMEOUT)
            except (OSError, TimeoutError):
                continue
            self.events.append(ProxyEvent(host, port, True, address))
            return streams
        return self._refuse(host, port, "could not connect")

    def _refuse(self, host: str, port: int, reason: str) -> str:
        self.events.append(ProxyEvent(host, port, False, reason))
        return reason

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._tasks.add(task)
        upstream_writer = None
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
                request_line, *header_lines = head.decode("latin-1").split("\r\n")
                method, target, version = request_line.split(" ", 2)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, ValueError, ConnectionError):
                return
            if method.upper() == "CONNECT":
                try:
                    host, port = _split_host_port(target)
                except ValueError:
                    writer.write(BAD_REQUEST)
                    return
                upstream = await self._open(host, port)
                if isinstance(upstream, str):
                    writer.write(_forbidden(upstream))
                    return
                upstream_reader, upstream_writer = upstream
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                parts = urlsplit(target)
                if parts.scheme != "http" or not parts.hostname:
                    writer.write(BAD_REQUEST)
                    return
                upstream = await self._open(parts.hostname, parts.port or 80)
                if isinstance(upstream, str):
                    writer.write(_forbidden(upstream))
                    return
                upstream_reader, upstream_writer = upstream
                path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
                # One request per connection keeps this simple: Chromium opens a new one for the next.
                headers = [line for line in header_lines if line and not line.lower().startswith(_DROP_HEADERS)]
                upstream_writer.write(
                    "\r\n".join([f"{method} {path} {version}", *headers, "Connection: close", "", ""]).encode("latin-1"))
            await asyncio.gather(_pipe(reader, upstream_writer), _pipe(upstream_reader, writer))
        finally:
            for stream in (upstream_writer, writer):
                if stream is not None:
                    stream.close()
            self._tasks.discard(task)


def _forbidden(reason: str) -> bytes:
    reason = reason.encode("ascii", "replace").decode().replace("\r", " ").replace("\n", " ")
    return (f"HTTP/1.1 403 Forbidden\r\n{REFUSED_HEADER}: {reason}\r\n"
            f"Content-Length: 0\r\nConnection: close\r\n\r\n").encode("ascii")


async def _pipe(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
    try:
        while data := await source.read(65536):
            destination.write(data)
            await destination.drain()
        if destination.can_write_eof():
            destination.write_eof()  # half-close, so the other direction can finish
    except (ConnectionError, OSError, RuntimeError):
        destination.close()
