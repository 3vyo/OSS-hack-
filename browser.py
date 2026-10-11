"""Chromium lifecycle, per-inspection context, capture, page-change check, screenshots."""

import asyncio
import dataclasses
import ipaddress
import secrets
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Request, Response, Route
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

import fetch_static
import policy
import render
from blocking import Filters
from proxy import REFUSED_HEADER, PolicyProxy
from snapshot import Redirect, RequestRecord, Snapshot

LOAD_TIMEOUT_MS = 15_000
LOAD_EVENT_MS = 5_000
SETTLE_SECONDS = 1.0
LAUNCH_ARGS = [
    "--disable-quic",  # QUIC is UDP and would skip the proxy
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",  # no WebRTC UDP outside the proxy
]
REFUSED_BROWSER_NOTE = ("the site answered HTTP {status} with almost no content: it may refuse automated "
                        "browsers, which linkinspector doesn't try to get around. Static mode (ctrl+r) sometimes works")
JAVASCRIPT_NOTE = ("JavaScript mode: the page's scripts ran in a headless Chromium; requests were filtered "
                   "and every connection went through the network rules")


def _literal_forbidden(host: str) -> bool:
    """Obviously local hosts are refused straight away; the proxy checks names after resolving them."""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return policy.is_forbidden_address(ipaddress.ip_address(host))
    except ValueError:
        return False


def _is_top_document(request: Request) -> bool:
    try:
        return request.is_navigation_request() and request.frame.parent_frame is None
    except PlaywrightError:
        return False


def _source_url(request: Request) -> str:
    try:
        frame_url = request.frame.url
    except PlaywrightError:
        frame_url = ""
    return frame_url if frame_url.startswith("http") else request.url


async def _mark(page: Page, token: str) -> None:
    try:
        await page.evaluate("token => { window.__linkinspector = token }", token)
    except PlaywrightError:
        pass  # the page is navigating; the document count will show it


async def _has_mark(page: Page, token: str) -> bool:
    try:
        return await page.evaluate("token => window.__linkinspector === token", token)
    except PlaywrightError:
        return False


async def _wait_quietly(page: Page) -> None:
    try:
        await page.wait_for_load_state("load", timeout=LOAD_EVENT_MS)
    except PlaywrightError:  # includes timeouts: some pages never finish loading
        pass
    await asyncio.sleep(SETTLE_SECONDS)


async def _http_hops(response: Response) -> list[Redirect]:
    chain = []
    request = response.request.redirected_from
    while request is not None:
        chain.append(request)
        request = request.redirected_from
    chain.reverse()
    hops = []
    for index, request in enumerate(chain):
        following = chain[index + 1].url if index + 1 < len(chain) else response.url
        hop_response = await request.response()
        hops.append(Redirect(request.url, hop_response.status if hop_response else None, "http", following))
    return hops


def _is_redirect(response: Response) -> bool:
    return 300 <= response.status < 400 and bool(response.headers.get("location"))


def _chain_start(response: Response) -> str:
    request = response.request
    while request.redirected_from is not None:
        request = request.redirected_from
    return request.url


async def _redirect_chain(documents: list[Response]) -> list[Redirect]:
    """HTTP redirects behind each document, and the client-side (JavaScript) jumps between documents."""
    hops = []
    for index, document in enumerate(documents):
        hops += await _http_hops(document)
        if index + 1 < len(documents):
            hops.append(Redirect(document.url, None, "javascript", _chain_start(documents[index + 1])))
    return hops


def _follow_demo_redirects(url: str) -> tuple[list[Redirect], str]:
    """The demo pages' own HTTP redirects (local files), followed here.

    Playwright never routes the next step of a redirect it was handed, so Chromium can't be
    served the next demo page; real sites' redirects go through the proxy as normal.
    """
    hops = []
    while len(hops) < policy.MAX_REDIRECTS and (fixture := policy.fixture_for(url)) is not None \
            and fixture.status in (301, 302, 303, 307, 308):
        location = urljoin(url, fixture.headers["location"])
        hops.append(Redirect(url, fixture.status, "http", location))
        url = location
    return hops, url


async def _settle(page: Page, documents: list[Response]) -> bool:
    """Wait for the page to settle; True if it is still the same document afterwards.

    A random marker set in the page plus the count of main-frame documents: a page that
    navigates loses the marker and adds a document, and it can't hide the new document from
    Playwright. One retry, then give up on comparing.
    """
    token = secrets.token_hex(8)
    for _ in range(2):
        seen = len(documents)
        await _mark(page, token)
        await _wait_quietly(page)
        if len(documents) == seen and await _has_mark(page, token):
            return True
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=LOAD_TIMEOUT_MS)
        except PlaywrightError:
            pass
    return False


async def _content(page: Page) -> str | None:
    for _ in range(3):
        try:
            return await page.content()
        except PlaywrightError:  # the page is navigating
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=LOAD_EVENT_MS)
            except PlaywrightError:
                pass
    return None


class Browser:
    """One headless Chromium for the app's lifetime; a fresh, throwaway context for every inspection."""

    def __init__(self, get_filters: Callable[[], Filters | None], proxy: PolicyProxy | None = None) -> None:
        self.get_filters = get_filters
        self.proxy = proxy or PolicyProxy()
        self._playwright = None
        self._browser = None
        self._lock = asyncio.Lock()

    async def _start(self) -> None:
        if self._browser is not None and self._browser.is_connected():
            return
        if self.proxy.port is None:
            await self.proxy.start()
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        # "<-loopback>" stops Chromium skipping the proxy for 127.0.0.1 and localhost.
        self._browser = await self._playwright.chromium.launch(
            proxy={"server": self.proxy.url, "bypass": "<-loopback>"}, args=LAUNCH_ARGS)

    async def close(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except PlaywrightError:
                pass
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        await self.proxy.close()

    async def inspect(self, url: str, page_id: int) -> Snapshot:
        try:
            policy.check_url(url)
        except policy.PolicyError as exc:
            return Snapshot.failed(page_id, "chromium", url, str(exc))
        async with self._lock:  # one inspection at a time
            try:
                await self._start()
            except PlaywrightError as exc:
                return Snapshot.failed(page_id, "chromium", url, f"Chromium could not start ({exc.message})")
            return await self._inspect(url, page_id)

    async def _route(self, route: Route, filters: Filters | None, records: list[RequestRecord]) -> None:
        request = route.request
        url, kind = request.url, request.resource_type
        try:
            try:
                policy.check_url(url)
                host = urlsplit(url).hostname or ""
                if _literal_forbidden(host):
                    raise policy.PolicyError(f"{host} is a private or local address, which is never opened")
            except policy.PolicyError as exc:
                records.append(RequestRecord(url, kind, "refused", str(exc)))
                await route.abort("blockedbyclient")
                return
            # The page itself is never filtered, only what it loads (iframes included).
            if filters is not None and not _is_top_document(request):
                if rule := filters.check(url, _source_url(request), kind):
                    records.append(RequestRecord(url, kind, "blocked", rule))
                    await route.abort("blockedbyclient")
                    return
            if (fixture := policy.fixture_for(url)) is not None:
                records.append(RequestRecord(url, kind, "fixture"))
                await route.fulfill(status=fixture.status, headers=fixture.headers, body=fixture.body)
                return
            records.append(RequestRecord(url, kind, "allowed"))
            await route.continue_()
        except PlaywrightError:
            pass  # the page or context closed while the request was waiting

    def _proxy_refusals(self, since: int) -> list[RequestRecord]:
        return [RequestRecord(f"{event.host}:{event.port}", "connection", "refused", event.detail)
                for event in self.proxy.events[since:] if not event.allowed]

    def _failure(self, page_id: int, url: str, message: str, records: list[RequestRecord], since: int,
                 redirects: list[Redirect] = (), final_url: str | None = None) -> Snapshot:
        refusals = [r for r in records if r.decision == "refused"] + self._proxy_refusals(since)
        if refusals:
            last = refusals[-1]
            message = f"the network rules refused {last.url}: {last.rule}"
            if final_url is None and last.resource_type == "document":
                final_url = last.url  # show where the inspection stopped
        elif "Download is starting" in message:
            message = "this link starts a file download, which linkinspector never allows"
        else:
            message = f"Chromium could not load the page ({message.splitlines()[0]})"
        snapshot = Snapshot.failed(page_id, "chromium", url, render.safe_text(message), tuple(redirects))
        return dataclasses.replace(snapshot, final_url=final_url or url,
                                   requests=tuple(records + self._proxy_refusals(since)))

    async def _inspect(self, url: str, page_id: int) -> Snapshot:
        filters = self.get_filters()
        records: list[RequestRecord] = []
        since = len(self.proxy.events)
        demo_hops, start = _follow_demo_redirects(url)
        context = await self._browser.new_context(
            accept_downloads=False, service_workers="block", user_agent=fetch_static.USER_AGENT,
            locale="en-GB", viewport={"width": 1280, "height": 800})
        popups: list[Page] = []
        try:
            async def handle(route: Route) -> None:
                await self._route(route, filters, records)

            await context.route("**/*", handle)  # before any page exists, so popups are covered too
            page = await context.new_page()
            context.on("page", lambda opened: popups.append(opened) if opened is not page else None)
            documents: list[Response] = []  # every main-frame document, in order (redirect responses aside)
            page.on("response", lambda r: documents.append(r) if r.request.resource_type == "document"
                    and r.frame == page.main_frame and not _is_redirect(r) else None)
            try:
                response = await page.goto(start, wait_until="domcontentloaded", timeout=LOAD_TIMEOUT_MS)
            except PlaywrightTimeout:
                return self._failure(page_id, url, f"the page took longer than {LOAD_TIMEOUT_MS // 1000} s",
                                     records, since, demo_hops)
            except PlaywrightError as exc:
                return self._failure(page_id, url, exc.message, records, since, demo_hops)
            if response is None:
                return self._failure(page_id, url, "no response", records, since, demo_hops)
            stable = await _settle(page, documents)
            for popup in popups:
                try:
                    await popup.close()
                except PlaywrightError:
                    pass
            documents = documents or [response]
            current = documents[-1]
            redirects = demo_hops + await _redirect_chain(documents)
            if refused := current.headers.get(REFUSED_HEADER):
                records.append(RequestRecord(current.url, "document", "refused", refused))
                return self._failure(page_id, url, refused, records, since, redirects, current.url)
            if (dom_html := await _content(page)) is None:
                return self._failure(page_id, url, "the page kept navigating, so it couldn't be captured",
                                     records, since, redirects)
            try:
                original_html = await current.text()
            except PlaywrightError:
                original_html = ""
            final_url = page.url
            page_data = render.parse(dom_html, final_url, scripts_ran=True)
            coverage = [JAVASCRIPT_NOTE]
            if filters is None or not filters.available:
                coverage.append("filtering unavailable: only the demo rules were applied")
            if not stable:
                coverage.append("the page kept loading new documents while it was captured, so forms added "
                                "by JavaScript can't be judged")
            if not original_html:
                coverage.append("the HTML the server sent couldn't be read")
            if popups:
                coverage.append(f"closed {len(popups)} popup window{'s' * (len(popups) > 1)} the page opened")
            if render.is_robot_check(current.status, page_data.markdown):
                coverage.append(render.ROBOT_CHECK_NOTE)
            elif current.status in (401, 403, 429) and len(page_data.markdown) < 400:
                coverage.append(REFUSED_BROWSER_NOTE.format(status=current.status))
            return Snapshot(
                page_id=page_id, mode="chromium", requested_url=url, final_url=final_url, status=current.status,
                content_type=(current.headers.get("content-type") or "").split(";")[0].strip().lower(),
                redirects=tuple(redirects), original_html=original_html, dom_html=dom_html,
                original_forms=render.parse_forms(original_html, current.url) if original_html else (),
                dom_comparable=stable and bool(original_html),
                title=page_data.title or render.safe_text(await page.title()), base_url=page_data.base_url,
                markdown=page_data.markdown, links=page_data.links, forms=page_data.forms,
                requests=tuple(records + self._proxy_refusals(since)),
                coverage=tuple(coverage) + page_data.notes, cookie_choices=page_data.cookie_choices,
            )
        finally:
            try:
                await context.close()
            except PlaywrightError:
                pass
