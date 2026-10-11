"""adblock engine, filter list cache and --update-filters, list attribution."""

import os
import re
from dataclasses import dataclass, replace
from email.utils import parsedate_to_datetime
from pathlib import Path

import adblock
import httpx

from snapshot import RequestRecord, Snapshot

LIST_DIR = Path.home() / ".cache" / "linkinspector" / "filters"
FILTERS_DIR = Path(__file__).resolve().parent / "filters"
DEMO_RULES = FILTERS_DIR / "demo.txt"
DEMO_BADWARE = FILTERS_DIR / "demo-badware.txt"  # "known bad" demo sites, for the .test pages only

_UASSETS = "https://ublockorigin.github.io/uAssets"


@dataclass(frozen=True)
class ListSource:
    filename: str
    name: str
    url: str
    security: bool = False  # a list of known phishing and malware sites, also used for warnings
    bare_hosts: bool = False  # entries without filter syntax are host names (URLhaus)


# uBlock Origin's default lists, downloaded at runtime and never copied into the repo
# (credited in the README).
LISTS = (
    ListSource("easylist.txt", "EasyList", "https://easylist.to/easylist/easylist.txt"),
    ListSource("easyprivacy.txt", "EasyPrivacy", "https://easylist.to/easylist/easyprivacy.txt"),
    ListSource("ublock-filters.txt", "uBlock filters", f"{_UASSETS}/filters/filters.txt"),
    ListSource("ublock-privacy.txt", "uBlock Privacy", f"{_UASSETS}/filters/privacy.txt"),
    ListSource("ublock-unbreak.txt", "uBlock Unbreak", f"{_UASSETS}/filters/unbreak.txt"),
    ListSource("ublock-quick-fixes.txt", "uBlock Quick fixes", f"{_UASSETS}/filters/quick-fixes.txt"),
    ListSource("peter-lowe.txt", "Peter Lowe's list",
               "https://pgl.yoyo.org/adservers/serverlist.php?hostformat=adblockplus&showintro=1&mimetype=plaintext"),
    ListSource("easylist-cookie.txt", "EasyList Cookie Notices", f"{_UASSETS}/thirdparties/easylist-cookies.txt"),
    ListSource("ublock-badware.txt", "uBlock Badware risks", f"{_UASSETS}/filters/badware.txt", security=True),
    ListSource("urlhaus.txt", "URLhaus malicious URLs",
               "https://malware-filter.gitlab.io/urlhaus-filter/urlhaus-filter-online.txt",
               security=True, bare_hosts=True),
)

# Playwright resource types that adblock names differently.
REQUEST_TYPES = {"xhr": "xmlhttprequest", "fetch": "xmlhttprequest", "eventsource": "other",
                 "manifest": "other", "texttrack": "other", "subdocument": "subdocument"}

_BARE_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z0-9-]+(?::\d+)?(?:/\S*)?")
_ALL_OPTION = re.compile(r"\$(?:[^$#]*,)?all(?:,|$)")


def _normalise(text: str, bare_hosts: bool) -> str:
    """Rewrite uBlock Origin syntax the engine silently ignores.

    `$all` means every request type, pages included. In URLhaus, bare entries such as `1.2.3.4` or
    `evil.example/x.exe` are host names, as uBlock Origin reads them: `||1.2.3.4^`, `||evil.example/x.exe`.
    The engine applies a rule with a path to pages only if it says `$document`, so those also get a
    `$document` copy; bare-host rules already cover pages.
    """
    lines = []
    for line in text.splitlines():
        line = line.strip()
        every_type = False
        if "#" not in line and _ALL_OPTION.search(line):
            pattern, _, options = line.rpartition("$")
            kept = [option for option in options.split(",") if option != "all"]
            line, every_type = pattern + (f"${','.join(kept)}" if kept else ""), True
        elif bare_hosts and _BARE_HOST.fullmatch(line):
            line, every_type = f"||{line}" + ("" if "/" in line else "^"), True
        lines.append(line)
        pattern, _, options = line.partition("$")
        if every_type and "/" in pattern.removeprefix("||"):
            lines.append(f"{pattern}$document" + (f",{options}" if options else ""))
    return "\n".join(lines)


def _engine(texts: list[str]) -> adblock.Engine:
    filter_set = adblock.FilterSet(debug=True)  # debug keeps rule text for the request log
    for text in texts:
        filter_set.add_filter_list(text)
    # optimize=False keeps one rule per match, so the log shows the exact rule.
    return adblock.Engine(filter_set, optimize=False)


def _header(text: str, *names: str) -> str:
    for name in names:
        if match := re.search(rf"^!\s*{name}:\s*(.+)$", text[:4000], re.MULTILINE):
            return match.group(1).strip()
    return ""


def _date(version: str) -> str:
    """A list's version as a date: 202610111458 or "Sun, 11 Oct 2026 …" or 2026-10-11T… → 2026-10-11."""
    if re.fullmatch(r"\d{12}", version):
        return f"{version[:4]}-{version[4:6]}-{version[6:8]}"
    if re.match(r"\d{4}-\d{2}-\d{2}", version):
        return version[:10]
    try:
        return parsedate_to_datetime(version).date().isoformat()
    except (TypeError, ValueError):
        return version or "unknown date"


@dataclass(frozen=True)
class FilterList:
    name: str
    version: str  # the list's own date
    security: bool = False


class Filters:
    """uBlock Origin's lists plus our demo rules: one engine for blocking, and one per security list
    to say which list flagged a site as a known phishing or malware site."""

    def __init__(self, lists: tuple[FilterList, ...], blocking_texts: list[str],
                 security_texts: list[tuple[str, str]]) -> None:
        self.lists = lists
        # Every list blocks requests; the security lists are also checked on their own.
        self.engine = _engine(blocking_texts + [text for _, text in security_texts])
        self.security_engines = [(name, _engine([text])) for name, text in security_texts]

    @property
    def available(self) -> bool:
        """True when real lists are loaded; demo rules alone don't count."""
        return bool(self.lists)

    @property
    def summary(self) -> str:
        if not self.available:
            return ("unavailable: no filter lists are downloaded, so only the demo rules apply. "
                    "Run: python app.py --update-filters")
        blocking = ", ".join(f"{item.name} {item.version}" for item in self.lists if not item.security)
        security = ", ".join(f"{item.name} {item.version}" for item in self.lists if item.security)
        return f"Blocking: {blocking}, demo rules\nKnown phishing and malware sites: {security or 'none'}"

    def check(self, url: str, source_url: str, resource_type: str) -> str | None:
        """The rule that blocks this request, or None."""
        kind = REQUEST_TYPES.get(resource_type, resource_type)
        result = self.engine.check_network_urls(url, source_url, kind)
        return (result.filter or "matched a filter rule") if result.matched else None

    def known_bad(self, url: str, resource_type: str = "document", source_url: str = "") -> tuple[str, str] | None:
        """(list name, rule) if a security list names this URL as a phishing or malware site."""
        kind = REQUEST_TYPES.get(resource_type, resource_type)
        for name, engine in self.security_engines:
            result = engine.check_network_urls(url, source_url or url, kind)
            if result.matched:
                return name, result.filter or "matched"
        return None

    def label_references(self, snapshot: Snapshot) -> Snapshot:
        """Mark which resources referenced in a static page's HTML are known trackers."""
        records = tuple(
            replace(record, rule=self.check(record.url, snapshot.final_url, record.resource_type) or "")
            if record.decision == "reference" else record
            for record in snapshot.requests)
        return replace(snapshot, requests=records)


def load(list_dir: Path = LIST_DIR) -> Filters:
    lists, blocking_texts = [], [DEMO_RULES.read_text(encoding="utf-8")]
    security_texts = [("the demo list of known bad sites", DEMO_BADWARE.read_text(encoding="utf-8"))]
    for source in LISTS:
        path = list_dir / source.filename
        if not path.is_file() or path.stat().st_size == 0:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        version = _date(_header(text, "Version", "Last modified", "Updated"))
        lists.append(FilterList(source.name, version, source.security))
        text = _normalise(text, source.bare_hosts)
        (security_texts.append((source.name, text)) if source.security else blocking_texts.append(text))
    return Filters(tuple(lists), blocking_texts, security_texts)


def update(list_dir: Path = LIST_DIR) -> list[str]:
    """Download every list; keep the old copy if a download fails."""
    list_dir.mkdir(parents=True, exist_ok=True)
    report = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for source in LISTS:
            try:
                response = client.get(source.url)
                response.raise_for_status()
                if "!" not in response.text[:300] and "[Adblock" not in response.text[:300]:
                    raise ValueError("this doesn't look like a filter list")
            except (httpx.HTTPError, ValueError) as exc:
                report.append(f"{source.name}: download failed ({exc}); keeping the previous copy if there is one")
                continue
            temporary = list_dir / f".{source.filename}.part"
            temporary.write_text(response.text, encoding="utf-8")
            os.replace(temporary, list_dir / source.filename)
            report.append(f"{source.name}: {_date(_header(response.text, 'Version', 'Last modified', 'Updated'))}")
    return report
