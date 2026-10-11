"""URL/network rules, redirects, limits, .test mapping, route handler."""

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urldefrag, urlsplit

ALLOWED_SCHEMES = {"http", "https"}
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


def is_demo_host(host: str | None) -> bool:
    return bool(host) and host.endswith(".test")


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
