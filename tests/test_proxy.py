import asyncio

from proxy import PolicyProxy


async def _request(proxy, data: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(data)
    await writer.drain()
    response = await asyncio.wait_for(reader.read(4096), 5)
    writer.close()
    return response


async def _server(handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def test_private_addresses_and_odd_ports_are_refused():
    async def main():
        proxy = PolicyProxy()
        await proxy.start()
        try:
            assert (await _request(proxy, b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n")).startswith(b"HTTP/1.1 403")
            assert (await _request(proxy, b"CONNECT localhost:443 HTTP/1.1\r\n\r\n")).startswith(b"HTTP/1.1 403")
            assert (await _request(proxy, b"GET http://10.0.0.1/ HTTP/1.1\r\nHost: 10.0.0.1\r\n\r\n")).startswith(
                b"HTTP/1.1 403")
            assert (await _request(proxy, b"CONNECT example.com:8080 HTTP/1.1\r\n\r\n")).startswith(b"HTTP/1.1 403")
        finally:
            await proxy.close()
        reasons = [event.detail for event in proxy.events]
        assert not any(event.allowed for event in proxy.events)
        assert "private or local address (127.0.0.1)" in reasons[0] and "private or local" in reasons[1]
        assert reasons[3] == "port 8080 isn't a standard web port"
    asyncio.run(main())


def test_allowed_tunnel_carries_data_both_ways():
    async def echo(reader, writer):
        writer.write(await reader.read(100))
        await writer.drain()
        writer.close()

    async def main():
        server, port = await _server(echo)
        # Loopback is allowed here only so the test can run without the internet.
        proxy = PolicyProxy(allowed_ports={port}, is_forbidden=lambda ip: False)
        await proxy.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            writer.write(f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\n\r\n".encode())
            assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 200")
            writer.write(b"ping")
            assert await asyncio.wait_for(reader.read(100), 5) == b"ping"
            writer.close()
        finally:
            await proxy.close()
            server.close()
        assert proxy.events[0].allowed and proxy.events[0].detail == "127.0.0.1"
    asyncio.run(main())


def test_plain_http_is_forwarded_in_origin_form_without_proxy_headers():
    received = []

    async def web(reader, writer):
        received.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        writer.close()

    async def main():
        server, port = await _server(web)
        proxy = PolicyProxy(allowed_ports={port}, is_forbidden=lambda ip: False)
        await proxy.start()
        try:
            response = await _request(proxy, f"GET http://127.0.0.1:{port}/page?x=1 HTTP/1.1\r\n"
                                              f"Host: 127.0.0.1:{port}\r\nProxy-Connection: keep-alive\r\n\r\n".encode())
        finally:
            await proxy.close()
            server.close()
        assert response.endswith(b"ok")
        head = received[0].decode()
        assert head.startswith("GET /page?x=1 HTTP/1.1") and "Connection: close" in head
        assert "Proxy-Connection" not in head
    asyncio.run(main())
