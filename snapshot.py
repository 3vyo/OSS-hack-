"""Snapshot and evidence types shared by both modes."""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Redirect:
    url: str
    status: int | None  # None for a JavaScript redirect
    kind: str = "http"  # "http" or "javascript"
    location: str = ""  # where this step pointed next


@dataclass(frozen=True)
class Link:
    text: str
    href: str  # attribute as written in the page
    url: str  # resolved against the document base


@dataclass(frozen=True)
class FormField:
    name: str
    type: str


@dataclass(frozen=True)
class Form:
    action: str  # resolved destination
    method: str
    fields: tuple[FormField, ...]

    @property
    def has_password(self) -> bool:
        return any(f.type == "password" for f in self.fields)


@dataclass(frozen=True)
class Finding:
    id: str
    check: str
    severity: str  # "info", "warning" or "danger"
    observation: str
    why: str


@dataclass(frozen=True)
class RequestRecord:
    url: str
    resource_type: str
    decision: str  # "fixture", "allowed", "blocked" or "refused"
    rule: str = ""


@dataclass(frozen=True)
class Snapshot:
    page_id: int
    mode: str  # "static" or "chromium"
    requested_url: str
    final_url: str
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: int | None = None
    content_type: str = ""
    redirects: tuple[Redirect, ...] = ()
    original_html: str = ""
    dom_html: str | None = None  # Chromium mode only
    title: str = ""
    base_url: str = ""
    markdown: str = ""
    links: tuple[Link, ...] = ()
    forms: tuple[Form, ...] = ()
    requests: tuple[RequestRecord, ...] = ()
    findings: tuple[Finding, ...] = ()
    coverage: tuple[str, ...] = ()
    error: str = ""

    @classmethod
    def failed(cls, page_id: int, mode: str, url: str, error: str,
               redirects: tuple[Redirect, ...] = ()) -> "Snapshot":
        return cls(page_id=page_id, mode=mode, requested_url=url, final_url=url,
                   redirects=redirects, error=error)
