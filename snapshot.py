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
    # Chromium mode: "fixture", "allowed", "blocked" (filter rule) or "refused" (network rules).
    # Static mode: "reference", a resource named in the HTML but never loaded.
    decision: str
    rule: str = ""  # the matching filter rule, or why the network rules refused it


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
    original_forms: tuple[Form, ...] = ()  # forms in original_html, to spot ones added by JavaScript
    dom_comparable: bool = False  # original_html and dom_html are the same document
    title: str = ""
    base_url: str = ""
    markdown: str = ""
    links: tuple[Link, ...] = ()
    forms: tuple[Form, ...] = ()
    requests: tuple[RequestRecord, ...] = ()
    findings: tuple[Finding, ...] = ()
    coverage: tuple[str, ...] = ()
    cookie_choices: tuple[str, ...] = ()  # what a hidden cookie notice offered, e.g. ("Reject all", "Accept all")
    needs_javascript: bool = False
    error: str = ""

    @classmethod
    def failed(cls, page_id: int, mode: str, url: str, error: str,
               redirects: tuple[Redirect, ...] = ()) -> "Snapshot":
        return cls(page_id=page_id, mode=mode, requested_url=url, final_url=url,
                   redirects=redirects, error=error)
