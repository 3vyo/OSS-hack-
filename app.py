"""Textual UI, navigation state, findings/coverage/AI/filtering status."""

import sys
from urllib.parse import unquote, urldefrag, urljoin

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Input, Markdown, Static

import fetch_static
from render import safe_text
from snapshot import Snapshot

HOME_URL = "https://home.test/"
WELCOME = f"""\
# linkinspector

Paste a link in the bar above and press **Enter**. Pages are fetched without running
any JavaScript, and links only open through linkinspector, never in another browser.

[Open the demo pages]({HOME_URL})
"""


def page_info(snapshot: Snapshot) -> Text:
    """The side panel. Built from Text objects, so page content is never parsed as markup."""
    info = Text()

    def row(label: str, value: str, style: str = "") -> None:
        info.append(f"{label}\n", style="bold")
        info.append(f"{safe_text(value)}\n\n", style=style)

    row("Mode", "static (no JavaScript)" if snapshot.mode == "static" else "Chromium")
    if snapshot.status is not None:
        row("Response", f"{snapshot.status} {snapshot.content_type}".strip())
    row("Address", snapshot.final_url)
    if snapshot.redirects:
        hops = [f"{r.status or 'JS'} {r.url}" for r in snapshot.redirects]
        row(f"Redirects ({len(snapshot.redirects)})", "\n".join(hops + [f"→ {snapshot.final_url}"]), "yellow")
    if not snapshot.error:
        with_password = sum(form.has_password for form in snapshot.forms)
        row("Links", str(len(snapshot.links)))
        row("Forms", f"{len(snapshot.forms)}" + (f" ({with_password} with a password field)" if with_password else ""),
            "red" if with_password else "")
    if snapshot.coverage:
        row("Coverage", "\n".join(f"• {note}" for note in snapshot.coverage), "dim")
    return info


class LinkInspector(App):
    CSS = """
    #main { height: 1fr; }
    #reader { width: 1fr; padding: 0 1; }
    #title { padding: 1 1 0 2; }
    #side { width: 44; border-left: solid $panel; padding: 1 1; }
    #status { height: 1; background: $panel; padding: 0 1; }
    """
    BINDINGS = [
        Binding("ctrl+l", "focus_url", "Address"),
        Binding("alt+left", "back", "Back"),
        Binding("alt+right", "forward", "Forward"),
        Binding("b", "back", show=False),
        Binding("f", "forward", show=False),
        Binding("escape", "focus_page", show=False),
    ]

    def __init__(self, start_url: str | None = None) -> None:
        super().__init__()
        self.start_url = start_url
        self.history: list[Snapshot] = []
        self.position = -1
        self.next_page_id = 0
        self.pending_page_id: int | None = None

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Paste a link and press Enter", id="url")
        with Horizontal(id="main"):
            with VerticalScroll(id="reader"):
                yield Static(id="title")
                yield Markdown(WELCOME, id="content", open_links=False)
            with VerticalScroll(id="side"):
                yield Static(id="info")
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        if self.start_url:
            self.navigate(self.start_url)

    @property
    def current(self) -> Snapshot | None:
        return self.history[self.position] if self.position >= 0 else None

    def set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(Text(safe_text(message)))

    @on(Input.Submitted, "#url")
    def url_submitted(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if url:
            self.navigate(url if "://" in url else f"https://{url}")

    @on(Markdown.LinkClicked)
    def link_clicked(self, event: Markdown.LinkClicked) -> None:
        url = self.resolve_link(event.href)
        if self.current and urldefrag(url).url == urldefrag(self.current.final_url).url:
            self.set_status("That link points to another part of this page.")
            return
        self.navigate(url)

    def resolve_link(self, href: str) -> str:
        """Map a clicked href back to the page's link table, so the real destination is used."""
        snapshot = self.current
        if snapshot is None:
            return href
        for link in snapshot.links:
            if unquote(link.href) == href:
                return link.url
        return urljoin(snapshot.base_url or snapshot.final_url, href)

    def navigate(self, url: str) -> None:
        self.next_page_id += 1
        self.pending_page_id = self.next_page_id
        self.set_status(f"Loading {url} …")
        self.load(url, self.next_page_id)

    @work(exclusive=True, group="navigation")
    async def load(self, url: str, page_id: int) -> None:
        snapshot = await fetch_static.fetch(url, page_id)
        if page_id != self.pending_page_id:
            return  # the user has moved on; never show a stale page
        self.pending_page_id = None
        del self.history[self.position + 1:]
        self.history.append(snapshot)
        self.position = len(self.history) - 1
        await self.show(snapshot)

    async def show(self, snapshot: Snapshot) -> None:
        self.query_one("#url", Input).value = snapshot.final_url
        self.query_one("#title", Static).update(Text(safe_text(snapshot.title or snapshot.final_url), style="bold"))
        if snapshot.error:
            body = f"## Could not open this page\n\n{snapshot.error}"
        else:
            body = snapshot.markdown or "*This page has no readable text.*"
        await self.query_one("#content", Markdown).update(body)
        self.query_one("#reader", VerticalScroll).scroll_home(animate=False)
        self.query_one("#info", Static).update(page_info(snapshot))
        where = f"page {self.position + 1} of {len(self.history)}"
        self.set_status(f"Could not open the page · {where}" if snapshot.error else where)

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


if __name__ == "__main__":
    LinkInspector(sys.argv[1] if len(sys.argv) > 1 else None).run()
