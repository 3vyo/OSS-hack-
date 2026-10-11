"""Readable Markdown, safe terminal text, resolved link table."""

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import lxml.etree
import lxml.html
import trafilatura

from snapshot import Form, FormField, Link

# C0/C1 control characters except tab and newline, plus bidirectional overrides,
# which can make a URL display differently from what it really is.
_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")


def safe_text(text: str) -> str:
    """Strip characters that could control the terminal or disguise text."""
    return _UNSAFE.sub("", text)


def _squash(text: str) -> str:
    return safe_text(" ".join(text.split()))


@dataclass(frozen=True)
class Page:
    title: str
    base_url: str
    links: tuple[Link, ...]
    forms: tuple[Form, ...]
    markdown: str
    notes: tuple[str, ...]


def _document(html: str):
    try:
        return lxml.html.document_fromstring(html)
    except ValueError:
        # lxml refuses str input that carries an XML encoding declaration.
        return lxml.html.document_fromstring(html.encode("utf-8"))


def _base_url(doc, url: str) -> str:
    for base in doc.iter("base"):
        href = (base.get("href") or "").strip()
        if href:
            resolved = urljoin(url, href)
            if urlsplit(resolved).scheme in ("http", "https"):
                return resolved
            break
    return url


def _forms(doc, url: str, base: str) -> tuple[Form, ...]:
    forms = []
    for form in doc.iter("form"):
        action = (form.get("action") or "").strip()
        fields = []
        for element in form.iter("input", "select", "textarea"):
            kind = (element.get("type") or "text").lower() if element.tag == "input" else element.tag
            fields.append(FormField(safe_text(element.get("name") or ""), kind))
        forms.append(Form(
            action=urljoin(base, action) if action else url,
            method=(form.get("method") or "get").upper(),
            fields=tuple(fields),
        ))
    return tuple(forms)


def _fallback_text(doc) -> str:
    for element in doc.iter("script", "style", "noscript", "template"):
        element.drop_tree()
    lines = (" ".join(line.split()) for line in doc.text_content().splitlines())
    return "\n\n".join(line for line in lines if line)


def parse(html: str, url: str) -> Page:
    try:
        doc = _document(html)
    except lxml.etree.ParserError:
        return Page("", url, (), (), "", ("the page is empty or could not be parsed as HTML",))

    base = _base_url(doc, url)
    links = tuple(
        Link(_squash(a.text_content()), href.strip(), urljoin(base, href.strip()))
        for a in doc.iter("a") if (href := a.get("href")) is not None
    )
    notes = []
    markdown = trafilatura.extract(
        html, url=url, output_format="markdown", include_links=True, include_formatting=True,
        include_tables=True, include_images=False, favor_recall=True, with_metadata=False,
    )
    if not markdown:
        markdown = _fallback_text(doc)
        notes.append("no main article found; showing all page text")
    return Page(
        title=_squash(doc.findtext(".//title") or ""),
        base_url=base,
        links=links,
        forms=_forms(doc, url, base),
        markdown=safe_text(markdown),
        notes=tuple(notes),
    )
