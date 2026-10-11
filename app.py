"""Textual UI, navigation state, findings/coverage/AI/filtering status."""

import argparse
import asyncio
import dataclasses
import subprocess
import sys
import webbrowser
from urllib.parse import quote_plus, unquote, urldefrag, urljoin, urlsplit

from markdown_it import MarkdownIt
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Footer, Input, Markdown, Static

import blocking
import clipboard
import designs
import fetch_static
import llm
import page_checks
import policy
import url_checks
from browser import Browser
from designs import Colours
from llm import Analysis
import render
from render import safe_text
from snapshot import Finding, Snapshot

HOME_URL = "https://home.test/"
# DuckDuckGo's results page works without JavaScript; Google's doesn't.
SEARCH_URL = "https://html.duckduckgo.com/html/?q={}"
SEARCH_HOST = urlsplit(SEARCH_URL).hostname
SEARCH_NOTE = ("search results are always fetched without JavaScript: DuckDuckGo's no-JavaScript results page "
               "refuses headless browsers. Results you open still use JavaScript mode")
WELCOME = f"""\
# linkinspector

Paste a link in the bar above and press **Enter**, or type some words to search the web
(with DuckDuckGo). Pages are read as static HTML without running any JavaScript, and links
only open through linkinspector, never in another browser.

**JavaScript mode:** press **ctrl+r** to load pages with their JavaScript in a sandboxed headless
Chromium. Trackers are blocked, every connection is checked against the network rules, and
cookies are thrown away after each page. Press **ctrl+r** again to go back to static mode.

**Copy and paste:** paste a link with **ctrl+v**. To copy, select text with the mouse and press
**ctrl+c**; with nothing selected, **ctrl+c** copies the page's address.

[Open the demo pages]({HOME_URL})
"""
NEEDS_JAVASCRIPT = """\
> **This page needs JavaScript, which static mode doesn't run,** so what's below may be only menus or a
> fallback message. Press **ctrl+r** to load it with JavaScript in a sandboxed, filtered headless Chromium.
> To search the web, you can also type words into the address bar.

---

"""


def open_externally(url: str) -> bool:
    """Open an http(s) URL in the user's normal browser, quietly so the terminal UI isn't garbled."""
    if urlsplit(url).scheme not in policy.ALLOWED_SCHEMES:
        return False
    if sys.platform == "darwin":
        command = ["open", url]
    elif sys.platform.startswith("win"):
        return webbrowser.open(url)
    else:
        command = ["xdg-open", url]
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        return webbrowser.open(url)
    return True


def markdown_parser() -> MarkdownIt:
    """Textual's parser without auto-linking: only text the page itself linked becomes a link."""
    return MarkdownIt("gfm-like").disable("linkify")


def address_to_url(text: str) -> str:
    """What the user typed in the address bar: a link, a bare domain, or words to search for."""
    text = text.strip()
    if "://" in text:
        return text
    if " " in text or "." not in text:
        return SEARCH_URL.format(quote_plus(text))
    return f"https://{text}"


class AddressBar(Input):
    """The URL bar. ctrl+v pastes from the desktop clipboard, not just Textual's own."""

    def action_paste(self) -> None:
        text = clipboard.paste()
        if text is None:
            super().action_paste()
            return
        start, end = self.selection
        self.replace(" ".join(text.split()), start, end)  # a link copied with line breaks


SEVERITY_ORDER = {"danger": 0, "warning": 1, "info": 2}
CHECK_LABELS = {
    "userinfo": "Hidden real address",
    "ip-host": "Bare IP address",
    "idn": "Non-Latin characters",
    "mixed-script": "Mixed alphabets",
    "lookalike": "Lookalike domain",
    "brand-in-subdomain": "Misleading subdomain",
    "brand-in-domain": "Brand name in domain",
    "password-over-http": "Unencrypted password",
    "password-form": "Asks for a password",
    "sso-form": "Single sign-on",
    "cross-site-form": "Password sent to another site",
    "brand-title": "Brand named in title",
    "brand-official": "Official brand site",
    "link-text-mismatch": "Misleading link text",
    "js-added-form": "Form added by JavaScript",
    "known-bad": "Known phishing/malware site",
    "known-bad-resource": "Uses a known malware site",
}


def _host(url: str) -> str:
    return urlsplit(url).hostname or ""


def _shorten(url: str, limit: int = 90) -> str:
    """Long sign-in redirects carry huge tokens; the side panel only needs where they went."""
    return url if len(url) <= limit else url[:limit - 1] + "…"


def append_findings(text: Text, findings: tuple[Finding, ...] | list[Finding], colours: Colours,
                    explain: bool = True) -> None:
    for finding in sorted(findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.id)):
        colour = {"danger": colours.danger, "warning": colours.warning, "info": colours.info}[finding.severity]
        text.append("● ", style=colour)
        text.append(f"{finding.id} {CHECK_LABELS.get(finding.check, finding.check)}\n", style=f"bold {colour}")
        text.append(f"{safe_text(finding.observation)}\n")
        if explain:
            text.append(f"{safe_text(finding.why)}\n", style="dim italic")
        text.append("\n")


def risk_badge(level: int, colours: Colours) -> tuple[str, str]:
    return {2: ("Risk: HIGH", colours.danger_badge), 1: ("Risk: MEDIUM", colours.warning_badge),
            0: ("No warning signs found", "bold")}[level]


def cookie_summary(choices: tuple[str, ...]) -> str:
    always = "None stored, so every visit works like ‘Reject all’."
    if not choices:
        return always
    offered = [choice for choice in choices if choice != "(no buttons)"]
    hidden = f"Cookie notice hidden (it offered: {', '.join(offered)})." if offered else "Cookie notice hidden."
    return f"{hidden} Nothing was accepted. {always}"


def _first_per_host(records, limit: int = 8):
    by_host = {}
    for record in records:
        by_host.setdefault(_host(record.url) or record.url, record)
    return list(by_host.values())[:limit]


def request_summary(snapshot: Snapshot) -> str:
    """Tracker references (static), or what the filter and network rules did (JavaScript mode)."""
    if snapshot.mode == "static":
        references = [r for r in snapshot.requests if r.decision == "reference"]
        trackers = [r for r in references if r.rule]
        lines = [f"{len(trackers)} known tracker{'s' * (len(trackers) != 1)} among {len(references)} resources "
                 f"named in the HTML. Static mode loads none of them."]
        lines += [f"• {_host(r.url)}  ({r.rule})" for r in _first_per_host(trackers)]
        return "\n".join(lines)
    let_through = [r for r in snapshot.requests if r.decision in ("fixture", "allowed")]
    blocked = [r for r in snapshot.requests if r.decision == "blocked"]
    refused = [r for r in snapshot.requests if r.decision == "refused"]
    lines = [f"{len(let_through)} let through · {len(blocked)} blocked by filter rules · "
             f"{len(refused)} refused by the network rules"]
    lines += [f"× {_host(r.url)}  ({r.rule})" for r in _first_per_host(blocked)]
    lines += [f"! {r.url}  ({r.rule})" for r in refused[:4]]
    lines.append("“Let through” means the rules allowed it, not that the server answered.")
    return "\n".join(lines)


def is_risky(snapshot: Snapshot) -> bool:
    return not snapshot.error and any(f.severity in ("warning", "danger") for f in snapshot.findings)


def append_analysis(info: Text, snapshot: Snapshot, analysis: Analysis | None, available: bool | None,
                    colours: Colours) -> None:
    info.append("Gemma (local AI)\n", style="bold underline")
    status = analysis.status if analysis else None
    if status == "done":
        level = llm.ASSESSMENT_LEVEL[analysis.assessment]
        info.append(f"{llm.ASSESSMENTS[analysis.assessment]}\n", style=f"bold {(colours.info, colours.warning, colours.danger)[level]}")
        for reason in analysis.reasons:
            info.append(f"• [{', '.join(reason.evidence_ids)}] ", style="bold")
            info.append(f"{reason.explanation}\n")
        if analysis.claimed_brand:
            info.append(f"Presents itself as: {analysis.claimed_brand}\n")
        for note in (analysis.limitations, *analysis.notes):
            if note:
                info.append(f"{note}\n", style="dim italic")
        info.append(f"Answered in {analysis.seconds:.0f} s. It can raise the risk, never lower it.\n\n", style="dim")
        return
    message = {
        "queued": "Waiting for Gemma…",
        "analysing": "Gemma is reading the evidence (usually 20–30 s on this laptop)…",
        "skipped": "Not run, because you moved to another page. Press ctrl+g to ask.",
        "unavailable": "Gemma isn't available, so there's no explanation. Start Ollama, then press ctrl+g. "
                       "The code findings above still apply.",
        "failed": f"Gemma's answer couldn't be used ({analysis.error if analysis else ''}). Press ctrl+g to try again.",
    }.get(status)
    if message is None:
        if available is False:
            message = "Gemma isn't running. Start Ollama, then press ctrl+g. The code findings above still apply."
        elif snapshot.error:
            message = "Not run: there's no page content to explain."
        else:
            message = "Not run (nothing above needs explaining). Press ctrl+g for Gemma's verdict, or ctrl+t to ask it about the page."
    info.append(f"{message}\n\n", style="dim" if status is None else "")


def page_info(snapshot: Snapshot, analysis: Analysis | None = None, available: bool | None = None,
              filtering: str = "", colours: Colours = designs.BY_NAME[designs.DEFAULT].colours) -> Text:
    """The side panel. Built from Text objects, so page content is never parsed as markup."""
    info = Text()

    def row(label: str, value: str, style: str = "") -> None:
        info.append(f"{label}\n", style="bold")
        info.append(f"{safe_text(value)}\n\n", style=style)

    level = llm.overall_level(snapshot, analysis)
    label, style = risk_badge(level, colours)
    info.append(f" {label} ", style=style)
    if level > llm.overall_level(snapshot, None):
        info.append("  raised by Gemma", style="dim")
    info.append("\n\n")
    append_analysis(info, snapshot, analysis, available, colours)
    info.append(f"Findings ({len(snapshot.findings)})\n", style="bold underline")
    if snapshot.findings:
        append_findings(info, snapshot.findings, colours)
    else:
        info.append("No findings in inspected content.\n\n")
    row("Mode", "static (no JavaScript)" if snapshot.mode == "static" else
        "JavaScript (sandboxed headless Chromium, filtered)")
    if snapshot.status is not None:
        row("Response", f"{snapshot.status} {snapshot.content_type}".strip())
    shown, real = url_checks.host_forms(_host(snapshot.final_url))
    row("Address", snapshot.final_url + (f"\n(really {real})" if shown != real else ""))
    if snapshot.redirects:
        hops = [f"{'JS' if r.kind == 'javascript' else r.status or '→'} {_shorten(r.url)}" for r in snapshot.redirects]
        row(f"Redirects ({len(snapshot.redirects)})", "\n".join(hops + [f"→ {_shorten(snapshot.final_url)}"]),
            colours.warning)
    if snapshot.requests:
        row("Requests", request_summary(snapshot))
    if filtering:
        row("Filtering", filtering, colours.warning if filtering.startswith("unavailable") else "")
    if not snapshot.error:
        with_password = sum(form.has_password for form in snapshot.forms)
        row("Cookies", cookie_summary(snapshot.cookie_choices))
        row("Links", str(len(snapshot.links)))
        row("Forms", f"{len(snapshot.forms)}" + (f" ({with_password} with a password field)" if with_password else ""),
            colours.danger if with_password else "")
    if snapshot.coverage:
        row("Coverage", "\n".join(f"• {note}" for note in snapshot.coverage), "dim")
    return info


class ConfirmBar(Static, can_focus=True):
    """Shows a link's real destination and any URL warnings before anything is fetched."""

    BINDINGS = [
        Binding("enter,y", "decide(True)", "Open"),
        Binding("escape,n", "decide(False)", "Don't open"),
    ]

    class Decided(Message):
        def __init__(self, url: str, confirmed: bool, external: bool) -> None:
            super().__init__()
            self.url = url
            self.confirmed = confirmed
            self.external = external  # open in the user's normal browser, not in linkinspector

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.url = ""
        self.findings: list[Finding] = []
        self.external = False

    def ask(self, url: str, findings: list[Finding], external: bool = False) -> None:
        self.url, self.findings, self.external = url, findings, external
        self.redraw()
        self.display = True
        self.focus()

    def redraw(self) -> None:
        url, colours = self.url, self.app.colours
        text = Text()
        text.append("Open this page in your normal browser?  " if self.external else "Open this link?  ", style="bold")
        text.append(safe_text(url), style="underline")
        shown, real = url_checks.host_forms(_host(url))
        if shown != real:
            text.append(f"  (really {real})", style=f"bold {colours.warning}")
        text.append("\n\n")
        append_findings(text, self.findings, colours, explain=False)
        if self.external:
            text.append("Your browser will run the page's scripts, keep its cookies and allow downloads.\n",
                        style=f"bold {colours.warning}")
        text.append("Enter: open    Esc: don't open", style="dim")
        self.update(text)

    def action_decide(self, confirmed: bool) -> None:
        self.display = False
        self.post_message(self.Decided(self.url, confirmed, self.external))


class ChatInput(Input):
    BINDINGS = [Binding("escape", "app.close_chat", "Close chat", show=False)]


CHAT_INTRO = ("Ask anything about this page, e.g. “what does ¶3 say?” or “what does the form ask for?”. "
              "Paragraph numbers are shown in the page while this is open. Gemma starts reading the page now; "
              "the first answer can take up to a minute on a laptop, follow-ups are much quicker. Gemma is a small "
              "model and can get details wrong, so check the ¶ it cites.")


class CloseButton(Static):
    def on_click(self) -> None:
        self.app.exit()


class LinkInspector(App):
    TITLE = "linkinspector"
    CSS_PATH = "designs.tcss"
    CSS = """
    #main { height: 1fr; }
    #reader { width: 1fr; padding: 0 1; }
    #title { padding: 1 1 0 2; }
    #side { width: 48; border-left: solid $panel; padding: 1 1; }
    #confirm { display: none; height: auto; max-height: 16; padding: 1 2; background: $boost;
               border-top: heavy $warning; }
    #status { height: 1; background: $panel; padding: 0 1; }
    #chat { display: none; height: 16; border-top: tall $primary; }
    #chat-log { height: 1fr; padding: 0 1; }
    #chat-log .chat-question { padding: 1 0 0 0; text-style: bold; }
    #chat-log Markdown { padding: 0; margin: 0; }
    """
    BINDINGS = [
        Binding("ctrl+l", "focus_url", "Address"),
        Binding("alt+left", "back", "Back"),
        Binding("alt+right", "forward", "Forward", show=False),
        Binding("ctrl+t", "toggle_chat", "Ask about page"),
        Binding("ctrl+g", "ask_gemma", "Gemma verdict"),
        Binding("ctrl+r", "toggle_javascript", "JavaScript"),
        Binding("r", "toggle_javascript", show=False),
        # Selected text is copied by the screen's own ctrl+c; this runs when nothing is selected.
        Binding("ctrl+c", "copy_address", "Copy", show=False),
        Binding("b", "back", show=False),
        Binding("f", "forward", show=False),
        Binding("a", "ask_gemma", show=False),
        Binding("escape", "focus_page", show=False),
        Binding("ctrl+o", "open_in_browser", "Open in browser"),
        Binding("f2", "next_design", "Design"),
    ]

    def __init__(self, start_url: str | None = None, ai_host: str | None = None, ai_client=None,
                 design: str | None = None) -> None:
        super().__init__()
        self.start_url = start_url
        self.requested_design = design
        self.history: list[Snapshot] = []
        self.position = -1
        self.next_page_id = 0
        self.pending_page_id: int | None = None
        self.analyses: dict[int, Analysis] = {}
        self.analyst = llm.Analyst(self.analysis_updated, self.is_current_page, host=ai_host, client=ai_client)
        self.filters: blocking.Filters | None = None
        self.filters_ready = asyncio.Event()
        self.browser = Browser(lambda: self.filters)
        self.javascript = False  # JavaScript mode, for this and new pages
        self.chat_open = False
        self.chat_busy = False
        self.chats: dict[int, list[tuple[str, str]]] = {}  # per page: (question, answer)

    def compose(self) -> ComposeResult:
        with Horizontal(id="titlebar"):
            yield Static(id="titlebar-text")
            yield Static("─  □", id="titlebar-buttons")
            yield CloseButton("✕", id="titlebar-close")
        yield AddressBar(placeholder="Paste a link, or type words to search, and press Enter", id="url")
        with Horizontal(id="main"):
            with VerticalScroll(id="reader"):
                yield Static(id="title")
                yield Markdown(WELCOME, id="content", open_links=False, parser_factory=markdown_parser)
            with VerticalScroll(id="side"):
                yield Static(id="info")
        with Vertical(id="chat"):
            yield VerticalScroll(id="chat-log")
            yield ChatInput(placeholder="Ask Gemma about this page, e.g. what does ¶3 say?   Enter: send · Esc: close",
                            id="chat-input")
        yield ConfirmBar(id="confirm")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        for design in designs.DESIGNS:
            self.register_theme(design.theme)
        chosen = self.requested_design or designs.load_choice()
        self.remembered_design = chosen if chosen in self.available_themes else designs.DEFAULT
        self.theme = self.remembered_design
        self.theme_changed_signal.subscribe(self, self.design_changed)
        self.update_titlebar()
        self.run_worker(self.load_filters(), group="filters", exit_on_error=False)
        self.run_worker(self.analyst.run(), group="gemma", exit_on_error=False)
        self.run_worker(self.warm_up(), group="gemma-warm-up", exit_on_error=False)
        if self.start_url:
            self.request_navigation(self.start_url, from_link=False)

    @property
    def colours(self) -> Colours:
        return designs.colours_for(self.current_theme)

    def design_changed(self, theme) -> None:
        """Also runs when a theme is picked in the command palette (ctrl+p, "Change theme")."""
        if theme.name != self.remembered_design:  # remember the user's choice, not the startup one
            self.remembered_design = theme.name
            designs.save_choice(theme.name)
        self.update_titlebar()
        if self.current:
            self.refresh_side()
        confirm = self.query_one(ConfirmBar)
        if confirm.display:
            confirm.redraw()

    def action_next_design(self) -> None:
        names = [design.name for design in designs.DESIGNS]
        following = names[(names.index(self.theme) + 1) % len(names)] if self.theme in names else names[0]
        self.theme = following
        self.notify(f"Design: {designs.BY_NAME[following].label}  (F2 for the next one)")

    def update_titlebar(self) -> None:
        design = designs.BY_NAME.get(self.theme)
        icon = design.icon if design else "◆"
        page = f" — {self.current.title or self.current.final_url}" if self.current else ""
        self.query_one("#titlebar-text", Static).update(Text(safe_text(f"{icon} linkinspector{page}")))

    async def load_filters(self) -> None:
        try:
            self.filters = await asyncio.to_thread(blocking.load)
        finally:
            self.filters_ready.set()
        if self.current:
            self.refresh_side()

    async def on_unmount(self) -> None:
        await self.browser.close()

    async def warm_up(self) -> None:
        await self.analyst.warm_up()
        if self.current:
            self.refresh_side()

    def is_current_page(self, page_id: int) -> bool:
        return self.current is not None and self.current.page_id == page_id

    def analysis_updated(self, analysis: Analysis) -> None:
        """Results are stored per page, so a late answer never lands on the page shown now."""
        self.analyses[analysis.page_id] = analysis
        if self.is_current_page(analysis.page_id):
            self.refresh_side()

    def refresh_side(self) -> None:
        snapshot = self.current
        analysis = self.analyses.get(snapshot.page_id)
        filtering = self.filters.summary if self.filters else "loading the filter lists…"
        self.query_one("#info", Static).update(
            page_info(snapshot, analysis, self.analyst.available, filtering, self.colours))
        count = len(snapshot.findings)
        where = f"{count} finding{'s' * (count != 1)} · page {self.position + 1} of {len(self.history)}"
        if analysis and analysis.status in ("queued", "analysing"):
            where += " · Gemma is analysing…"
        if self.javascript:
            where += " · JavaScript mode on"
        self.set_status(f"Could not open the page · {where}" if snapshot.error else where)

    def action_ask_gemma(self) -> None:
        snapshot = self.current
        if snapshot is None or snapshot.error:
            self.set_status("There's no page content for Gemma to explain.")
        elif self.analyses.get(snapshot.page_id, Analysis(0, "")).status in ("queued", "analysing"):
            self.set_status("Gemma is already working on this page.")
        else:
            self.analyst.request(snapshot)

    @property
    def current(self) -> Snapshot | None:
        return self.history[self.position] if self.position >= 0 else None

    def set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(Text(safe_text(message)))

    def copy_to_clipboard(self, text: str) -> None:
        super().copy_to_clipboard(text)  # Textual's own clipboard, plus the OSC 52 escape code
        clipboard.copy(text)  # the desktop clipboard, for terminals that ignore OSC 52

    def action_copy_address(self) -> None:
        if self.current is None:
            self.notify("There's no page address to copy yet.")
            return
        self.copy_to_clipboard(self.current.final_url)
        self.notify(f"Copied {self.current.final_url}", markup=False)

    @on(Input.Submitted, "#url")
    def url_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.request_navigation(address_to_url(event.value), from_link=False)

    @on(Markdown.LinkClicked)
    def link_clicked(self, event: Markdown.LinkClicked) -> None:
        url = self.resolve_link(event.href)
        if self.current and urldefrag(url).url == urldefrag(self.current.final_url).url:
            self.set_status("That link points to another part of this page.")
            return
        self.request_navigation(url, from_link=True)

    def resolve_link(self, href: str) -> str:
        """Map a clicked href back to the page's link table, so the real destination is used."""
        snapshot = self.current
        if snapshot is None:
            return href
        for link in snapshot.links:
            if unquote(link.href) == href:
                return link.url
        return urljoin(snapshot.base_url or snapshot.final_url, href)

    def request_navigation(self, url: str, from_link: bool) -> None:
        """Check the URL before any request is sent; ask first if it looks risky or leaves the site."""
        if urlsplit(url).scheme not in policy.ALLOWED_SCHEMES:
            self.navigate(url)  # refused by the fetch rules, shown as an error page
            return
        known_bad = self.filters.known_bad if self.filters else None
        findings = url_checks.numbered(url_checks.check(url) + page_checks.known_bad_findings([url], known_bad), "U")
        risky = any(finding.severity != "info" for finding in findings)
        leaving = from_link and self.current is not None and (
            url_checks.site_of(_host(url)) != url_checks.site_of(_host(self.current.final_url)))
        if risky or leaving:
            self.query_one(ConfirmBar).ask(url, findings)
            self.set_status("Check where this link really goes before opening it.")
        else:
            self.navigate(url)

    @on(ConfirmBar.Decided)
    def confirm_decided(self, event: ConfirmBar.Decided) -> None:
        if event.confirmed and event.external:
            opened = open_externally(event.url)
            self.set_status(f"Opened {event.url} in your browser." if opened else
                            "Couldn't start your browser; ctrl+c copies the address instead.")
        elif event.confirmed:
            self.navigate(event.url)
        else:
            self.set_status("Not opened.")
            self.action_focus_page()

    def action_open_in_browser(self) -> None:
        """Hand a page you've inspected to your normal browser, e.g. to download something from it."""
        snapshot = self.current
        if snapshot is None or urlsplit(snapshot.final_url).scheme not in policy.ALLOWED_SCHEMES:
            self.set_status("There's no web page to open in your browser.")
            return
        self.query_one(ConfirmBar).ask(snapshot.final_url, list(snapshot.findings), external=True)

    def action_toggle_javascript(self) -> None:
        self.javascript = not self.javascript
        if self.javascript:
            self.notify("JavaScript mode on: pages run their scripts in a sandboxed, filtered headless Chromium.")
        else:
            self.notify("JavaScript mode off: pages are read as static HTML.")
        if self.current is not None:
            self.navigate(self.current.requested_url)  # a new inspection, which contacts the site again

    def navigate(self, url: str) -> None:
        self.next_page_id += 1
        self.pending_page_id = self.next_page_id
        javascript = self.javascript and _host(url) != SEARCH_HOST
        self.set_status(f"Loading {url} " + ("with JavaScript …" if javascript else "…"))
        self.load(url, self.next_page_id, javascript)

    @work(exclusive=True, group="navigation")
    async def load(self, url: str, page_id: int, javascript: bool) -> None:
        await self.filters_ready.wait()
        if javascript:
            snapshot = await self.browser.inspect(url, page_id)
        else:
            snapshot = await fetch_static.fetch(url, page_id)
            if self.filters is not None:
                snapshot = self.filters.label_references(snapshot)
            if self.javascript and _host(url) == SEARCH_HOST:
                snapshot = dataclasses.replace(snapshot, coverage=snapshot.coverage + (SEARCH_NOTE,))
        snapshot = page_checks.analyse(snapshot, self.filters.known_bad if self.filters else None)
        if page_id != self.pending_page_id:
            return  # the user has moved on; never show a stale page
        self.pending_page_id = None
        del self.history[self.position + 1:]
        self.history.append(snapshot)
        self.position = len(self.history) - 1
        await self.show(snapshot)
        if self.focused is None or self.focused.id == "url":
            self.action_focus_page()  # but never pull focus away from the chat or anything else you're typing in
        if is_risky(snapshot) and self.analyst.available is not False:
            self.analyst.request(snapshot)  # automatic only when the code checks found something

    async def show(self, snapshot: Snapshot) -> None:
        self.query_one("#url", Input).value = snapshot.final_url
        self.query_one("#title", Static).update(Text(safe_text(snapshot.title or snapshot.final_url), style="bold"))
        self.update_titlebar()
        await self.query_one("#content", Markdown).update(self.reader_body(snapshot))
        self.query_one("#reader", VerticalScroll).scroll_home(animate=False)
        self.query_one("#side", VerticalScroll).scroll_home(animate=False)
        self.refresh_side()
        if self.chat_open:
            if snapshot.error:
                self.action_close_chat()
            else:
                await self.render_chat_log()
                self.prime_chat()

    def reader_body(self, snapshot: Snapshot) -> str:
        if snapshot.error:
            return f"## Could not open this page\n\n{snapshot.error}"
        body = snapshot.markdown or "*This page has no readable text.*"
        if self.chat_open and snapshot.markdown:
            body = render.with_paragraph_numbers(snapshot.markdown)  # the numbers Gemma is given
        return NEEDS_JAVASCRIPT + body if snapshot.needs_javascript else body

    async def rerender_reader(self) -> None:
        reader = self.query_one("#reader", VerticalScroll)
        position = reader.scroll_y
        await self.query_one("#content", Markdown).update(self.reader_body(self.current))
        reader.scroll_to(y=position, animate=False)

    # Chat about the current page (ctrl+t).

    def action_toggle_chat(self) -> None:
        if self.chat_open:
            self.action_close_chat()
            return
        if self.current is None or self.current.error:
            self.set_status("Open a page first, then press ctrl+t to ask Gemma about it.")
            return
        self.chat_open = True
        self.query_one("#chat").display = True
        self.query_one("#chat-input", Input).focus()
        self.run_worker(self.open_chat(), group="chat-ui")

    async def open_chat(self) -> None:
        await self.rerender_reader()
        await self.render_chat_log()
        self.prime_chat()

    def prime_chat(self) -> None:
        if self.analyst.available is not False:
            self.analyst.prime(self.current)  # Gemma starts reading the page while you type

    def action_close_chat(self) -> None:
        if not self.chat_open:
            return
        self.chat_open = False
        self.query_one("#chat").display = False
        if self.current is not None:
            self.run_worker(self.rerender_reader(), group="chat-ui")
        self.action_focus_page()

    def chat_note(self, message: str) -> Static:
        return Static(Text(message, style="dim italic"))

    async def render_chat_log(self) -> None:
        log = self.query_one("#chat-log", VerticalScroll)
        await log.remove_children()
        exchanges = self.chats.get(self.current.page_id, [])
        widgets: list[Static | Markdown] = [] if exchanges else [self.chat_note(CHAT_INTRO)]
        if self.analyst.available is False:
            widgets.append(self.chat_note("Gemma isn't running, so it can't answer. Start Ollama and try again."))
        for asked, answered in exchanges:
            widgets += [Static(Text(f"You: {asked}"), classes="chat-question"),
                        Markdown(answered, open_links=False, parser_factory=markdown_parser)]
        await log.mount_all(widgets)
        log.scroll_end(animate=False)

    @on(Input.Submitted, "#chat-input")
    async def chat_submitted(self, event: Input.Submitted) -> None:
        question = " ".join(event.value.split())
        snapshot = self.current
        if not question or snapshot is None:
            return
        if self.chat_busy:
            self.set_status("Gemma is still answering the last question.")
            return
        event.input.value = ""
        self.chat_busy = True
        page_id, history = snapshot.page_id, tuple(self.chats.get(snapshot.page_id, []))
        log = self.query_one("#chat-log", VerticalScroll)
        reading = self.analyst.primed != page_id
        waiting = self.chat_note("Gemma is reading the page…" if reading else "Gemma is thinking…")
        await log.mount_all([Static(Text(f"You: {question}"), classes="chat-question"), waiting])
        log.scroll_end(animate=False)
        parts: list[str] = []
        shown: dict[str, object] = {}  # the answer widget and its stream, once the first words arrive

        async def on_text(fragment: str) -> None:
            parts.append(fragment)
            if not self.is_current_page(page_id):
                return  # you moved on; the answer is still kept for that page
            try:
                if "stream" not in shown:
                    answer = Markdown("", open_links=False, parser_factory=markdown_parser)
                    await waiting.remove()
                    await log.mount(answer)
                    shown["stream"] = Markdown.get_stream(answer)
                await shown["stream"].write(fragment)
                log.scroll_end(animate=False)
            except Exception:  # the chat pane changed under us; the answer is still saved below
                pass

        async def on_done(error: str | None) -> None:
            self.chat_busy = False
            if "stream" in shown:
                await shown["stream"].stop()
            answer = "".join(parts).strip()
            if error or not answer:
                message = f"Gemma couldn't answer ({error}). Is Ollama running?" if error else "Gemma gave no answer."
                if self.is_current_page(page_id):
                    waiting.update(Text(message, style="dim italic"))
                return
            self.chats.setdefault(page_id, []).append((question, answer))

        self.analyst.ask(snapshot, history, question, on_text, on_done)

    def cancel_loading(self) -> None:
        self.pending_page_id = None
        self.workers.cancel_group(self, "navigation")

    async def action_back(self) -> None:
        if self.position > 0:
            self.cancel_loading()
            self.position -= 1
            await self.show(self.current)

    async def action_forward(self) -> None:
        if self.position < len(self.history) - 1:
            self.cancel_loading()
            self.position += 1
            await self.show(self.current)

    def action_focus_url(self) -> None:
        self.query_one("#url", Input).focus()

    def action_focus_page(self) -> None:
        self.query_one("#reader", VerticalScroll).focus()


def main() -> None:
    parser = argparse.ArgumentParser(description="A read-only terminal browser for checking suspicious links.")
    parser.add_argument("url", nargs="?", help="a link to open straight away")
    parser.add_argument("--design", choices=[design.name for design in designs.DESIGNS],
                        help="the look to start with (F2 cycles through them; your last choice is remembered)")
    parser.add_argument("--update-filters", action="store_true",
                        help=f"download the tracker filter lists to {blocking.LIST_DIR} and exit")
    args = parser.parse_args()
    if args.update_filters:
        for line in blocking.update():
            print(line)
        return
    LinkInspector(args.url, design=args.design).run()


if __name__ == "__main__":
    main()
