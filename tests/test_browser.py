"""JavaScript mode in headless Chromium. These start a real browser, so each takes a second or two."""

import asyncio
import socket

from playwright.async_api import async_playwright
from textual.widgets import Static

import blocking
import page_checks
import policy
from app import LinkInspector
from browser import Browser
from proxy import PolicyProxy


def inspect(url, proxy=None):
    async def main():
        browser = Browser(lambda: blocking.load(), proxy=proxy)
        try:
            return page_checks.analyse(await browser.inspect(url, 1))
        finally:
            await browser.close()
    return asyncio.run(main())


class Listener:
    """A local TCP server that only counts connections: the proof that nothing reached it."""

    def __init__(self):
        self.connections = 0

    async def start(self):
        async def count(reader, writer):
            self.connections += 1
            writer.close()
        self.server = await asyncio.start_server(count, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self


def test_page_built_by_javascript_renders():
    snapshot = inspect("https://spa.test/")
    assert snapshot.mode == "chromium" and not snapshot.error
    assert "Trams from Piccadilly to MediaCityUK" in snapshot.markdown


def test_trackers_are_blocked_and_javascript_added_form_is_flagged():
    snapshot = inspect("https://news.test/")
    blocked = {(r.url, r.rule) for r in snapshot.requests if r.decision == "blocked"}
    assert blocked == {("https://tracker.test/t.js", "||tracker.test^"),
                       ("https://tracker.test/pixel.gif", "||tracker.test^")}
    assert ("https://news.test/app.js", "fixture") in {(r.url, r.decision) for r in snapshot.requests}
    assert snapshot.dom_comparable and snapshot.original_forms == ()
    added = [f for f in snapshot.findings if f.check == "js-added-form"]
    assert len(added) == 1 and added[0].severity == "warning"  # alongside the cross-site form (danger)


def test_demo_redirects_and_javascript_redirects_are_recorded():
    snapshot = inspect("https://short.test/abc")
    assert snapshot.final_url == "https://news.test/"
    assert [(r.kind, r.status) for r in snapshot.redirects] == [("http", 301), ("http", 302)]
    snapshot = inspect("https://jsredirect.test/")
    assert snapshot.final_url == "https://news.test/" and snapshot.dom_comparable
    assert [(r.kind, r.url, r.location) for r in snapshot.redirects] == [
        ("javascript", "https://jsredirect.test/", "https://news.test/")]


def test_page_that_keeps_navigating_is_not_compared():
    snapshot = inspect("https://bouncy.test/")
    assert not snapshot.dom_comparable
    assert any("kept loading new documents" in note for note in snapshot.coverage)
    assert not any(f.check == "js-added-form" for f in snapshot.findings)


def test_redirect_to_loopback_never_connects():
    """A real HTTP redirect, which Playwright's route handler never sees: the proxy must stop it."""
    async def main():
        listener = await Listener().start()

        async def redirect(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(f"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:{listener.port}/\r\n"
                         f"Content-Length: 0\r\nConnection: close\r\n\r\n".encode())
            await writer.drain()
            writer.close()
        redirector = await asyncio.start_server(redirect, "127.0.0.1", 0)
        # Test-only: "redirector.example" stands in for a public site that redirects to this computer.
        proxy = PolicyProxy(test_hosts={"redirector.example": ("127.0.0.1", redirector.sockets[0].getsockname()[1])})
        browser = Browser(lambda: blocking.load(), proxy=proxy)
        try:
            snapshot = await browser.inspect("http://redirector.example/", 1)
        finally:
            await browser.close()
            redirector.close()
        assert listener.connections == 0
        assert "refused" in snapshot.error and snapshot.final_url == f"http://127.0.0.1:{listener.port}/"
        assert [(e.host, e.allowed) for e in proxy.events if e.host == "127.0.0.1"] == [("127.0.0.1", False)]

        # Control: without these protections, the same listener does see the connection.
        async with async_playwright() as playwright:
            plain = await playwright.chromium.launch()
            page = await plain.new_page()
            try:
                await page.goto(f"http://127.0.0.1:{listener.port}/", timeout=5000)
            except Exception:
                pass  # the listener hangs up without answering
            await plain.close()
        assert listener.connections >= 1
        listener.server.close()
    asyncio.run(main())


def test_popup_aimed_at_a_forbidden_address_never_connects(monkeypatch):
    async def main():
        listener = await Listener().start()
        page = (f'<html><body><p>Hello</p><script>window.open("http://127.0.0.1:{listener.port}/")</script>'
                f'</body></html>').encode()
        real_fixture_for = policy.fixture_for

        def fixture_for(url):
            if url.startswith("https://popup.test/"):
                return policy.Fixture(200, {"content-type": "text/html"}, page)
            return real_fixture_for(url)
        monkeypatch.setattr(policy, "fixture_for", fixture_for)
        browser = Browser(lambda: blocking.load())
        try:
            snapshot = await browser.inspect("https://popup.test/", 1)
        finally:
            await browser.close()
        listener.server.close()
        assert listener.connections == 0
        assert any(note.startswith("closed 1 popup window") for note in snapshot.coverage)
        assert any(r.decision == "refused" and "127.0.0.1" in r.url for r in snapshot.requests)
    asyncio.run(main())


def test_name_that_resolves_to_this_computer_is_refused_by_the_proxy():
    hostname = socket.gethostname()  # resolves to 127.0.1.1 or similar through /etc/hosts
    if not policy.is_forbidden_address(__import__("ipaddress").ip_address(socket.gethostbyname(hostname))):
        return  # this machine's name isn't a local address; nothing to test
    snapshot = inspect(f"http://{hostname}/")
    assert "private or local address" in snapshot.error


async def _wait_for_navigation(app, pilot):
    await pilot.pause()
    navigation = [w for w in app.workers if w.group == "navigation"]
    if navigation:  # an empty list would mean "wait for every worker", including Gemma's queue
        await app.workers.wait_for_complete(navigation)
    await pilot.pause()


def test_ctrl_r_reloads_the_page_with_javascript():
    async def main():
        app = LinkInspector("https://news.test/", ai_host="http://127.0.0.1:9")
        async with app.run_test(size=(130, 40)) as pilot:
            await app.filters_ready.wait()
            await _wait_for_navigation(app, pilot)
            assert app.current.mode == "static"
            app.action_focus_page()
            await pilot.press("ctrl+r")
            await _wait_for_navigation(app, pilot)
            panel = str(app.query_one("#info", Static).render())
            assert app.current.mode == "chromium" and app.javascript, (app.current, panel)
            assert "2 blocked by filter rules" in panel, panel
            assert "Form added by JavaScript" in panel, (app.current.coverage, panel)
    asyncio.run(main())


def test_without_filter_lists_filtering_is_reported_unavailable(tmp_path):
    filters = blocking.load(tmp_path)  # an empty folder: no downloaded lists
    assert not filters.available and filters.summary.startswith("unavailable")
    assert filters.check("https://tracker.test/t.js", "https://news.test/", "script") == "||tracker.test^"


def test_with_blocking_off_the_tracker_request_is_let_through():
    async def main():
        browser = Browser(lambda: None)  # no filters at all
        try:
            return await browser.inspect("https://news.test/", 1)
        finally:
            await browser.close()
    snapshot = asyncio.run(main())
    decisions = {r.url: r.decision for r in snapshot.requests}
    # Not blocked: like every .test address it's answered locally (a 404 here), never from the network.
    assert decisions["https://tracker.test/t.js"] == "fixture"
    assert not any(r.decision == "blocked" for r in snapshot.requests)
    assert any("filtering unavailable" in note for note in snapshot.coverage)


def test_downloads_are_never_allowed(monkeypatch):
    real_fixture_for = policy.fixture_for

    def fixture_for(url):
        if url.startswith("https://files.test/"):
            return policy.Fixture(200, {"content-type": "application/zip",
                                        "content-disposition": "attachment; filename=invoice.zip"}, b"PK\x03\x04")
        return real_fixture_for(url)
    monkeypatch.setattr(policy, "fixture_for", fixture_for)
    snapshot = inspect("https://files.test/invoice.zip")
    assert snapshot.error == "this link starts a file download, which linkinspector never allows"


def test_site_refusing_the_headless_browser_is_explained(monkeypatch):
    real_fixture_for = policy.fixture_for

    def fixture_for(url):
        if url.startswith("https://refuses.test/"):
            return policy.Fixture(403, {"content-type": "text/html"}, b"If this persists, please email us.")
        return real_fixture_for(url)
    monkeypatch.setattr(policy, "fixture_for", fixture_for)
    snapshot = inspect("https://refuses.test/")
    assert snapshot.status == 403
    assert any("may refuse automated browsers" in note for note in snapshot.coverage)


def test_searches_use_static_mode_even_with_javascript_on(monkeypatch):
    import app as app_module
    from snapshot import Snapshot

    fetched = []

    async def fake_fetch(url, page_id):
        fetched.append(url)
        return Snapshot(page_id=page_id, mode="static", requested_url=url, final_url=url, status=200,
                        markdown="**[Result](<https://example.org/>)**")

    async def main():
        app = LinkInspector(ai_host="http://127.0.0.1:9")
        async with app.run_test() as pilot:
            await app.filters_ready.wait()
            monkeypatch.setattr(app_module.fetch_static, "fetch", fake_fetch)

            async def no_browser(*args):
                raise AssertionError("searches must not use the headless browser")
            monkeypatch.setattr(app.browser, "inspect", no_browser)
            app.javascript = True
            await pilot.click("#url")
            await pilot.press(*"manchester trams", "enter")
            await _wait_for_navigation(app, pilot)
            assert fetched == ["https://html.duckduckgo.com/html/?q=manchester+trams"]
            assert app.current.mode == "static" and app_module.SEARCH_NOTE in app.current.coverage
    asyncio.run(main())


def test_refused_browser_note_counts_text_not_link_addresses(monkeypatch):
    nav = "".join(f"<a href='https://refuses.test/{'section/' * 6}{name}'>{name}</a> "
                  for name in ("Home", "News", "Sport", "Help", "Terms", "Privacy"))
    html = f"<html><body><nav>{nav}</nav><p>Access denied.</p></body></html>".encode()
    real_fixture_for = policy.fixture_for
    monkeypatch.setattr(policy, "fixture_for", lambda url: policy.Fixture(403, {"content-type": "text/html"}, html)
                        if url == "https://refuses.test/" else real_fixture_for(url))
    snapshot = inspect("https://refuses.test/")
    assert len(snapshot.markdown) >= 400  # long only because of the link addresses
    assert any("may refuse automated browsers" in note for note in snapshot.coverage)
