"""Readable Markdown, safe terminal text, resolved link table."""

import copy
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit

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
    cookie_choices: tuple[str, ...] = ()  # what a hidden cookie notice offered
    needs_javascript: bool = False


# Search engines wrap result links in a tracking redirect; the real destination is a parameter.
_REDIRECTORS = {"duckduckgo.com": ("/l/", "uddg"), "www.google.com": ("/url", "q"), "google.com": ("/url", "q")}


def unwrap_redirect(url: str) -> str:
    """The real destination of a search engine's tracking link, e.g. duckduckgo.com/l/?uddg=…"""
    parts = urlsplit(url)
    path, parameter = _REDIRECTORS.get(parts.hostname or "", ("", ""))
    if parameter and parts.path.startswith(path):
        target = parse_qs(parts.query).get(parameter, [""])[0]
        if urlsplit(target).scheme in ("http", "https"):
            return target
    return url


_NEEDS_JS_TEXT = re.compile(r"enable javascript|javascript is (?:disabled|required|not enabled)|turn on javascript|"
                            r"not redirected", re.IGNORECASE)


def _tells_to_enable_javascript(noscript) -> bool:
    """A <noscript> block that sends browsers without JavaScript elsewhere, like Google's."""
    refresh = any("refresh" in (meta.get("http-equiv") or "").lower() for meta in noscript.iter("meta"))
    return refresh or bool(_NEEDS_JS_TEXT.search(noscript.text_content()))


def _duckduckgo_results(doc, base: str) -> str | None:
    """DuckDuckGo's no-JavaScript results page as a Markdown list (the text extractor drops the results)."""
    items = []
    for result in doc.find_class("result"):
        if "result--ad" in (result.get("class") or ""):
            continue
        anchors = result.find_class("result__a")
        if not anchors or not anchors[0].get("href"):
            continue
        target = unwrap_redirect(urljoin(base, anchors[0].get("href")))
        title = _squash(anchors[0].text_content()).replace("[", "(").replace("]", ")")
        snippets = result.find_class("result__snippet")
        snippet = _squash(snippets[0].text_content()) if snippets else ""
        items.append(f"**[{title}](<{target}>)**  \n{urlsplit(target).hostname}\n\n{snippet}")
    return "\n\n".join(items) or None


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


# Cookie-consent banners are found two ways: by the names consent tools give their elements
# (or a dialog role), and by their buttons ("Reject all", "Accept all"…) for banners with
# meaningless class names, like Google's. An element is only removed when it is small and talks
# about cookies and choices, so an article about baking cookies stays.
_CONSENT_NAMES = re.compile(
    r"cookie|consent|gdpr|onetrust|ot-sdk|cookiebot|didomi|qc-cmp|quantcast|truste|usercentrics|"
    r"sp_message|fc-consent|cc-window|cc-banner|osano|iubenda|termly|klaro|tarteaucitron", re.IGNORECASE)
_CONSENT_WORDS = re.compile(r"\b(accept|agree|consent|reject|decline|preferences|manage|settings|partners)\b",
                            re.IGNORECASE)
_CONSENT_ACTION = re.compile(r"^(accept|reject|allow|deny|decline|refuse|agree|ok)\b", re.IGNORECASE)
_CONSENT_MAX_TEXT = 3000
_NEVER_REMOVE = {"html", "body", "main", "article"}
CONSENT_WALL_NOTE = ("this page seems to be only a cookie-consent notice; linkinspector never accepts cookies, "
                     "so the real content may be missing")


def _label(element) -> str:
    return " ".join((element.text_content() or element.get("value") or "").split())


def _choices(element) -> list[str]:
    """Labels of the cookie choices a banner offers, e.g. ["Reject all", "Accept all"]."""
    labels = (_label(b) for b in element.iter("button", "a", "input"))
    return list(dict.fromkeys(label for label in labels if len(label) <= 40 and _CONSENT_ACTION.match(label)))


def _is_named_like_consent(element) -> bool:
    names = " ".join(element.get(attribute) or "" for attribute in ("id", "class", "aria-label"))
    return bool(_CONSENT_NAMES.search(names)) or element.tag == "dialog" or element.get("role") in (
        "dialog", "alertdialog")


def _is_consent_banner(element) -> bool:
    if not isinstance(element.tag, str) or element.tag in _NEVER_REMOVE:
        return False
    text = " ".join(element.itertext())  # spaced, so adjacent buttons don't run together
    if len(text) > _CONSENT_MAX_TEXT or "cookie" not in text.lower():
        return False
    if len(_choices(element)) >= 2:
        return True
    return bool(_CONSENT_WORDS.search(text)) and _is_named_like_consent(element)


def _remove_consent_banners(doc) -> list[str]:
    """Drop consent banners from the tree. Returns the choices they offered (empty list if none found)."""
    banners = {e for e in doc.iter() if isinstance(e.tag, str) and _is_named_like_consent(e) and _is_consent_banner(e)}
    # Unnamed banners: the smallest element around the buttons that qualifies, never a wrapper
    # around the whole of a small page.
    for button in doc.iter("button", "a", "input"):
        if not _CONSENT_ACTION.match(_label(button)):
            continue
        for ancestor in button.iterancestors():
            if ancestor.tag in _NEVER_REMOVE or len(ancestor.text_content()) > _CONSENT_MAX_TEXT:
                break
            if _is_consent_banner(ancestor):
                banners.add(ancestor)
                break
    outermost = [e for e in doc.iter() if e in banners and not any(a in banners for a in e.iterancestors())]
    choices = [choice for banner in outermost for choice in _choices(banner)] or (["(no buttons)"] if outermost else [])
    for banner in outermost:
        banner.drop_tree()
    return list(dict.fromkeys(choices))


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
        Link(_squash(a.text_content()), href.strip(), unwrap_redirect(urljoin(base, href.strip())))
        for a in doc.iter("a") if (href := a.get("href")) is not None
    )
    notes = []
    # Links and forms above come from the full page; only the reader text drops consent banners
    # and "please enable JavaScript" redirects meant for browsers like this one.
    readable = copy.deepcopy(doc)
    cookie_choices = _remove_consent_banners(readable)
    js_notices = [noscript for noscript in readable.iter("noscript") if _tells_to_enable_javascript(noscript)]
    for noscript in js_notices:
        noscript.drop_tree()
    markdown = None
    if urlsplit(url).hostname == "html.duckduckgo.com":
        markdown = _duckduckgo_results(readable, base)
    markdown = markdown or trafilatura.extract(
        lxml.html.tostring(readable, encoding="unicode"), url=url, output_format="markdown",
        include_links=True, include_formatting=True, include_tables=True, include_images=False,
        favor_recall=True, with_metadata=False,
    )
    if not markdown:
        markdown = _fallback_text(readable)
        notes.append("no main article found; showing all page text")
    if (cookie_choices and len(markdown) < 200) or (urlsplit(url).hostname or "").startswith("consent."):
        notes.append(CONSENT_WALL_NOTE)
    needs_javascript = bool(js_notices) or (len(markdown) < 300 and doc.find(".//script") is not None)
    if needs_javascript:
        notes.append("this page needs JavaScript to show its content, which static mode doesn't run")
    return Page(
        title=_squash(doc.findtext(".//title") or ""),
        base_url=base,
        links=links,
        forms=_forms(doc, url, base),
        markdown=safe_text(markdown),
        notes=tuple(notes),
        cookie_choices=tuple(safe_text(choice) for choice in cookie_choices),
        needs_javascript=needs_javascript,
    )
