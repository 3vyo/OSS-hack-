"""httpx static fetch."""

import asyncio
import dataclasses
from urllib.parse import urljoin

import httpx

import policy
import render
from snapshot import Redirect, Snapshot

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
HTML_TYPES = {"", "text/html", "application/xhtml+xml"}
STATIC_NOTE = "static mode: scripts were not run, so anything added by JavaScript is missing"


def _fixture_transport() -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        fixture = policy.fixture_for(str(request.url))
        return httpx.Response(fixture.status, headers=fixture.headers, content=fixture.body)
    return httpx.MockTransport(handle)


async def _read_limited(response: httpx.Response) -> tuple[bytes, bool]:
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size > policy.MAX_BODY_BYTES:
            return b"".join(chunks)[:policy.MAX_BODY_BYTES], True
    return b"".join(chunks), False


def _decode(body: bytes, response: httpx.Response) -> str:
    try:
        return body.decode(response.charset_encoding or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _build(page_id: int, url: str, response: httpx.Response, body: bytes, truncated: bool,
           redirects: list[Redirect]) -> Snapshot:
    final_url = str(response.url)
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    coverage = [STATIC_NOTE]
    if truncated:
        coverage.append(f"page larger than {policy.MAX_BODY_BYTES // 1_000_000} MB; only the start was inspected")
    html = _decode(body, response)
    if content_type not in HTML_TYPES:
        return Snapshot(page_id=page_id, mode="static", requested_url=url, final_url=final_url,
                        status=response.status_code, content_type=content_type, redirects=tuple(redirects),
                        markdown=f"*This is not a web page ({content_type}), so it is not shown.*",
                        coverage=tuple(coverage + [f"not an HTML page ({content_type})"]))
    page = render.parse(html, final_url)
    return Snapshot(
        page_id=page_id, mode="static", requested_url=url, final_url=final_url,
        status=response.status_code, content_type=content_type, redirects=tuple(redirects),
        original_html=html, title=page.title, base_url=page.base_url, markdown=page.markdown,
        links=page.links, forms=page.forms, coverage=tuple(coverage) + page.notes,
        cookie_choices=page.cookie_choices, needs_javascript=page.needs_javascript,
    )


async def _fetch(url: str, page_id: int, redirects: list[Redirect]) -> Snapshot:
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=10, headers={"user-agent": USER_AGENT},
        mounts={"all://*.test": _fixture_transport()},
    ) as client:  # a new client per inspection, so no cookies outlive it
        for _ in range(policy.MAX_REDIRECTS + 1):
            current = _next_url(url, redirects)
            policy.check_url(current)
            await policy.check_destination(current)
            async with client.stream("GET", current) as response:
                if response.is_redirect:
                    redirects.append(Redirect(current, response.status_code,
                                              location=urljoin(current, response.headers["location"])))
                    continue
                body, truncated = await _read_limited(response)
                return _build(page_id, url, response, body, truncated, redirects)
    raise policy.PolicyError(f"stopped after {policy.MAX_REDIRECTS} redirects")


def _next_url(url: str, redirects: list[Redirect]) -> str:
    return redirects[-1].location if redirects else url


async def fetch(url: str, page_id: int) -> Snapshot:
    redirects: list[Redirect] = []
    try:
        async with asyncio.timeout(policy.TIMEOUT_SECONDS):
            return await _fetch(url, page_id, redirects)
    except TimeoutError:
        error = f"the page took longer than {policy.TIMEOUT_SECONDS} s to load"
    except policy.PolicyError as exc:
        error = str(exc)
    except httpx.HTTPError as exc:
        error = f"could not load the page ({type(exc).__name__}: {exc})"
    snapshot = Snapshot.failed(page_id, "static", url, render.safe_text(error), tuple(redirects))
    # Show where the inspection stopped, e.g. the private address a redirect pointed at.
    return dataclasses.replace(snapshot, final_url=_next_url(url, redirects))
