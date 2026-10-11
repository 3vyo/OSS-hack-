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
    resources: tuple[tuple[str, str], ...] = ()  # (url, request type) of scripts, images, frames… in the HTML


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
ROBOT_CHECK_NOTE = ("the site showed a robot check (CAPTCHA) instead of the page; linkinspector doesn't try to "
                    "get around these. To search, type words into the address bar (DuckDuckGo)")
_ROBOT_CHECK = re.compile(r"unusual traffic|not a robot|are you a robot|captcha|verify you are (?:a )?human",
                          re.IGNORECASE)


def is_robot_check(status: int | None, text: str) -> bool:
    return status in (403, 429, 503) and bool(_ROBOT_CHECK.search(text))


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


# Elements that make a browser load something, and the request type each one makes.
_RESOURCE_TAGS = (("script", "src", "script"), ("img", "src", "image"), ("iframe", "src", "subdocument"),
                  ("frame", "src", "subdocument"), ("audio", "src", "media"), ("video", "src", "media"),
                  ("source", "src", "media"), ("embed", "src", "object"), ("object", "data", "object"))


def _resources(doc, base: str) -> tuple[tuple[str, str], ...]:
    found = []
    for tag, attribute, kind in _RESOURCE_TAGS:
        for element in doc.iter(tag):
            if value := (element.get(attribute) or "").strip():
                found.append((urljoin(base, value), kind))
    for link in doc.iter("link"):
        rel = (link.get("rel") or "").lower().split()
        if (href := (link.get("href") or "").strip()) and {"stylesheet", "preload", "prefetch", "modulepreload"} & set(rel):
            found.append((urljoin(base, href), "stylesheet" if "stylesheet" in rel else "other"))
    return tuple(dict.fromkeys(item for item in found if urlsplit(item[0]).scheme in ("http", "https")))


def parse_forms(html: str, url: str) -> tuple[Form, ...]:
    """Just the forms, e.g. of the HTML the server sent before any JavaScript ran."""
    try:
        doc = _document(html)
    except lxml.etree.ParserError:
        return ()
    return _forms(doc, url, _base_url(doc, url))


# Our own HTML → Markdown for pages that aren't articles (sign-in pages, web apps), keeping the layout:
# one line per block, real links only, and form controls shown so you can see what a page asks for.
HIDDEN_MARK = "data-linkinspector-hidden"  # set by JavaScript mode on elements hidden by CSS
_BLOCK_TAGS = {"address", "article", "aside", "blockquote", "dd", "details", "dialog", "div", "dl", "dt",
               "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
               "hr", "legend", "li", "main", "nav", "ol", "p", "pre", "section", "summary", "table", "tr", "ul"}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head", "iframe", "object", "embed", "canvas",
              "video", "audio", "map", "img", "picture"}
_PREFIXES = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "##### ", "h6": "###### ", "li": "- "}
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>#|~])")


def _escape(text: str) -> str:
    """Page text never becomes Markdown syntax: no fake links, headings or emphasis."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def _is_hidden(element) -> bool:
    style = (element.get("style") or "").replace(" ", "").lower()
    return (element.get(HIDDEN_MARK) is not None or element.get("hidden") is not None
            or element.get("aria-hidden") == "true" or "display:none" in style or "visibility:hidden" in style
            or (element.tag == "input" and (element.get("type") or "").lower() == "hidden"))


def _is_shown(element) -> bool:
    """Neither the element nor any of its containers is hidden or skipped."""
    return not any(_is_hidden(e) or (isinstance(e.tag, str) and e.tag in _SKIP_TAGS)
                   for e in (element, *element.iterancestors()))


def _visible_text(element) -> str:
    """Like text_content(), but leaving out hidden, script and other skipped descendants."""
    parts = []

    def collect(node) -> None:
        if not isinstance(node.tag, str) or node.tag in _SKIP_TAGS or _is_hidden(node):
            return  # comments, scripts, hidden elements; their tail text belongs to the parent
        if node.text:
            parts.append(node.text)
        for child in node:
            collect(child)
            if child.tail:
                parts.append(child.tail)

    collect(element)
    return " ".join(" ".join(parts).split())


def _control_label(text: str) -> str:
    return " ".join(safe_text(text).replace("`", "'").split())[:40]


def _control(element) -> str | None:
    """A form control as inline code, e.g. `[ Email: ______ ]` or `[ Continue ]`."""
    tag = element.tag
    if tag == "button":
        label = (_visible_text(element) or element.get("aria-label") or element.get("title")
                 or element.get("value") or "Button")  # icon-only buttons often say what they do in a title
        return f"`[ {_control_label(label)} ]`"
    if tag == "select":
        return f"`[ {_control_label(element.get('aria-label') or element.get('name') or 'choose')} ▾ ]`"
    if tag == "textarea":
        return f"`[ {_control_label(element.get('placeholder') or element.get('aria-label') or '')} …… ]`"
    if tag != "input":
        return None
    kind = (element.get("type") or "text").lower()
    if kind in ("submit", "button", "reset", "image"):
        return f"`[ {_control_label(element.get('value') or element.get('aria-label') or 'Submit')} ]`"
    if kind in ("checkbox", "radio"):
        return "`[ ]`"
    label = _control_label(element.get("placeholder") or element.get("aria-label") or "")
    blank = "••••••" if kind == "password" else "______"
    return f"`[ {label + ': ' if label else ''}{blank} ]`"


def structured_markdown(root, base: str) -> str:
    blocks: list[str] = []
    pieces: list[str] = []
    pending_prefix = [""]  # the heading or bullet marker of the block being filled, used once

    def flush() -> None:
        line = re.sub(r" ([,.;:!?)])", r"\1", " ".join(" ".join(pieces).split()))  # no space before punctuation
        pieces.clear()
        if line and not re.fullmatch(r"(\\?[|·•/~*-]|–|—)+", line.replace(" ", "")):  # skip separator-only lines
            prefix, pending_prefix[0] = pending_prefix[0], ""
            if not prefix and re.match(r"[-+]\s|\d+[.)]\s", line):
                line = "\\" + line  # page text that would read as a list item
            blocks.append(prefix + line)

    def walk(element) -> None:
        tag = element.tag if isinstance(element.tag, str) else ""
        if tag and tag not in _SKIP_TAGS and not _is_hidden(element):
            if tag in _BLOCK_TAGS:
                flush()
                if tag in _PREFIXES:
                    pending_prefix[0] = _PREFIXES[tag]
            control = _control(element)
            if control is not None:
                pieces.append(control)
            elif tag == "a" and (href := (element.get("href") or "").strip()):
                label = _escape(_visible_text(element))
                url = unwrap_redirect(urljoin(base, href))
                if label and urlsplit(url).scheme in ("http", "https") and not re.search(r"[\s<>]", url):
                    pieces.append(f"[{label}](<{url}>)")
                elif label:
                    pieces.append(label)
            elif tag == "br":
                flush()
            else:
                if element.text:
                    pieces.append(_escape(element.text))
                for child in element:
                    walk(child)
            if tag in _BLOCK_TAGS:
                flush()
                if tag in _PREFIXES:
                    pending_prefix[0] = ""  # an empty heading or list item: don't pass its marker on
        if element.tail:
            pieces.append(_escape(element.tail))

    walk(root)
    flush()
    return "\n\n".join(blocks)


def _password_forms(readable, base: str) -> str:
    """Forms that ask for a password, shown under an article, which the text extractor leaves out."""
    forms = [form for form in readable.iter("form") if _is_shown(form) and any(
        (field.get("type") or "").lower() == "password" for field in form.iter("input"))]
    if not forms:
        return ""
    return "\n\n---\n\n**Forms on this page**\n\n" + "\n\n".join(structured_markdown(form, base) for form in forms)


def link_text(markdown: str) -> str:
    """Markdown links replaced by their text: [Privacy](<https://…>) → Privacy."""
    return re.sub(r"\[([^\]]*)\]\(<?[^)>]*>?\)", r"\1", markdown)


def plain_text(markdown: str) -> str:
    """Markdown without link targets or control brackets, e.g. for measuring how much a page says."""
    return re.sub(r"`\[[^`]*\]`", " ", link_text(markdown))


# Paragraph numbers (¶1, ¶2, …), shown in the reader while the chat is open and given to Gemma, so
# "what does ¶3 say?" means the same block to both. Headings, quotes, rules and tables aren't numbered.
_UNNUMBERED = ("#", ">", "|", "---", "```")
_LIST_ITEM = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)")


def numbered_blocks(markdown: str) -> list[tuple[int | None, str]]:
    blocks, number = [], 0
    for block in markdown.split("\n\n"):
        if not block.strip():
            continue
        if block.lstrip().startswith(_UNNUMBERED):
            blocks.append((None, block))
        else:
            number += 1
            blocks.append((number, block))
    return blocks


def with_paragraph_numbers(markdown: str) -> str:
    parts = []
    for number, block in numbered_blocks(markdown):
        if number is not None:
            marker = f"`¶{number}` "
            block = (_LIST_ITEM.sub(lambda m: m.group(1) + marker, block, count=1) if _LIST_ITEM.match(block)
                     else marker + block)
        parts.append(block)
    return "\n\n".join(parts)


def parse(html: str, url: str, scripts_ran: bool = False) -> Page:
    """scripts_ran: html is a DOM captured after JavaScript ran (Chromium mode)."""
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
    body = readable.find(".//body")
    structured = structured_markdown(body if body is not None else readable, base)
    if urlsplit(url).hostname == "html.duckduckgo.com" and (results := _duckduckgo_results(readable, base)):
        markdown = results
    else:
        article = trafilatura.extract(
            lxml.html.tostring(readable, encoding="unicode"), url=url, output_format="markdown",
            include_links=True, include_formatting=True, include_tables=True, include_images=False,
            favor_recall=True, with_metadata=False,
        ) or ""
        # The text extractor is built for articles; it drops sign-in pages and web apps, or keeps
        # the wrong part of them. Use it only when it found a real article.
        if len(article) >= 500 and len(article) >= len(plain_text(structured)) / 4:
            markdown = article + _password_forms(readable, base)
        else:
            markdown = structured
    if (cookie_choices and len(plain_text(markdown)) < 200) or (urlsplit(url).hostname or "").startswith("consent."):
        notes.append(CONSENT_WALL_NOTE)
    # A <noscript> "enable JavaScript" notice only means the page needs it if there's little else to read:
    # news sites carry one for their video players next to a full article.
    text_length = len(plain_text(markdown))
    needs_javascript = not scripts_ran and (
        (bool(js_notices) and text_length < 1500) or (text_length < 300 and doc.find(".//script") is not None))
    if needs_javascript:
        notes.append("this page needs JavaScript to show its content, which static mode doesn't run")
    elif js_notices and not scripts_ran:
        notes.append("parts of this page, such as videos or interactive features, need JavaScript (ctrl+r)")
    return Page(
        title=_squash(doc.findtext(".//title") or ""),
        base_url=base,
        links=links,
        forms=_forms(doc, url, base),
        markdown=safe_text(markdown),
        notes=tuple(notes),
        cookie_choices=tuple(safe_text(choice) for choice in cookie_choices),
        needs_javascript=needs_javascript,
        resources=_resources(doc, base),
    )
