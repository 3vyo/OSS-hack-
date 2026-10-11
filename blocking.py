"""adblock engine, filter list cache and --update-filters, list attribution."""

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

import adblock
import httpx

from snapshot import RequestRecord, Snapshot

LIST_DIR = Path.home() / ".cache" / "linkinspector" / "filters"
DEMO_RULES = Path(__file__).resolve().parent / "filters" / "demo.txt"

# Downloaded at runtime and never copied into the repo; credited in the README.
LISTS = {
    "easylist.txt": ("EasyList", "https://easylist.to/easylist/easylist.txt"),
    "easyprivacy.txt": ("EasyPrivacy", "https://easylist.to/easylist/easyprivacy.txt"),
    # EasyList's cookie-notice list, from uBlock Origin's up-to-date mirror. Blocks consent-tool scripts.
    "easylist-cookie.txt": ("EasyList Cookie Notices",
                            "https://ublockorigin.github.io/uAssets/thirdparties/easylist-cookies.txt"),
}

# Playwright resource types that adblock names differently.
REQUEST_TYPES = {"xhr": "xmlhttprequest", "fetch": "xmlhttprequest", "eventsource": "other",
                 "manifest": "other", "texttrack": "other", "subdocument": "subdocument"}


@dataclass(frozen=True)
class FilterList:
    name: str
    version: str  # the list's "Version" or "Last modified" header


def _header(text: str, name: str) -> str:
    match = re.search(rf"^! {name}:\s*(.+)$", text[:4000], re.MULTILINE)
    return match.group(1).strip() if match else ""


class Filters:
    """The downloaded lists plus our demo rules, in one engine that reports the matching rule."""

    def __init__(self, lists: tuple[FilterList, ...], texts: list[str]) -> None:
        self.lists = lists
        filter_set = adblock.FilterSet(debug=True)  # debug keeps rule text for the request log
        for text in texts:
            filter_set.add_filter_list(text)
        # optimize=False keeps one rule per match, so the log shows the exact rule.
        self.engine = adblock.Engine(filter_set, optimize=False)

    @property
    def available(self) -> bool:
        """True when real lists are loaded; demo rules alone don't count."""
        return bool(self.lists)

    @property
    def summary(self) -> str:
        if not self.available:
            return ("unavailable: no filter lists are downloaded, so only the demo rules apply. "
                    "Run: python app.py --update-filters")
        return ", ".join(f"{item.name} ({item.version})" for item in self.lists) + ", demo rules"

    def check(self, url: str, source_url: str, resource_type: str) -> str | None:
        """The rule that blocks this request, or None."""
        kind = REQUEST_TYPES.get(resource_type, resource_type)
        result = self.engine.check_network_urls(url, source_url, kind)
        return (result.filter or "matched a filter rule") if result.matched else None

    def label_references(self, snapshot: Snapshot) -> Snapshot:
        """Mark which resources referenced in a static page's HTML are known trackers."""
        records = tuple(
            replace(record, rule=self.check(record.url, snapshot.final_url, record.resource_type) or "")
            if record.decision == "reference" else record
            for record in snapshot.requests)
        return replace(snapshot, requests=records)


def load(list_dir: Path = LIST_DIR) -> Filters:
    lists, texts = [], [DEMO_RULES.read_text(encoding="utf-8")]
    for filename, (name, _) in LISTS.items():
        path = list_dir / filename
        if not path.is_file() or path.stat().st_size == 0:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        texts.append(text)
        lists.append(FilterList(name, _header(text, "Version") or _header(text, "Last modified") or "unknown version"))
    return Filters(tuple(lists), texts)


def update(list_dir: Path = LIST_DIR) -> list[str]:
    """Download every list; keep the old copy if a download fails."""
    list_dir.mkdir(parents=True, exist_ok=True)
    report = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for filename, (name, url) in LISTS.items():
            try:
                response = client.get(url)
                response.raise_for_status()
                if "!" not in response.text[:200] and "[Adblock" not in response.text[:200]:
                    raise ValueError("this doesn't look like a filter list")
            except (httpx.HTTPError, ValueError) as exc:
                report.append(f"{name}: download failed ({exc}); keeping the previous copy if there is one")
                continue
            temporary = list_dir / f".{filename}.part"
            temporary.write_text(response.text, encoding="utf-8")
            os.replace(temporary, list_dir / filename)
            version = _header(response.text, "Version") or _header(response.text, "Last modified")
            report.append(f"{name}: {version or 'downloaded'}")
    return report
