"""Phase 1 spike: headless Chromium with context routing, adblock filtering and .test fixtures."""

import time
from pathlib import Path
from urllib.parse import urlsplit

import adblock
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = (ROOT / "fixtures").resolve()
OUT = Path(__file__).resolve().parent / "out"
LIST_DIR = Path.home() / ".cache" / "linkinspector" / "filters"
PAGES = ["https://news.test/", "https://northbank.test/"]

# Playwright resource types that adblock names differently.
REQUEST_TYPES = {"xhr": "xmlhttprequest", "fetch": "xmlhttprequest", "eventsource": "other",
                 "manifest": "other", "texttrack": "other"}


def build_engine():
    started = time.perf_counter()
    filters = adblock.FilterSet(debug=True)  # debug keeps rule text for the request log
    lists = [ROOT / "filters" / "demo.txt", *sorted(LIST_DIR.glob("*.txt"))]
    for path in lists:
        filters.add_filter_list(path.read_text(encoding="utf-8"))
    engine = adblock.Engine(filters, optimize=False)  # exact rule text in the log
    print(f"engine: {len(lists)} lists loaded in {time.perf_counter() - started:.2f}s")
    return engine


def fixture_path(url):
    parts = urlsplit(url)
    host_dir = (FIXTURES / (parts.hostname or "")).resolve()
    candidate = (host_dir / parts.path.lstrip("/")).resolve()
    if candidate.is_dir():
        candidate = candidate / "index.html"
    if not candidate.is_relative_to(host_dir) or not candidate.is_file():
        return None
    return candidate


def make_handler(engine, log):
    def handle(route):
        request = route.request
        try:
            source = request.frame.url
        except Exception:
            source = ""
        if not source.startswith("http"):
            source = request.url
        kind = REQUEST_TYPES.get(request.resource_type, request.resource_type)
        result = engine.check_network_urls(request.url, source, kind)
        host = urlsplit(request.url).hostname or ""
        if result.matched:
            log.append(("blocked", request.resource_type, request.url, result.filter))
            route.abort("blockedbyclient")
        elif host.endswith(".test") and (path := fixture_path(request.url)):
            log.append(("fixture", request.resource_type, request.url, ""))
            route.fulfill(path=path)
        else:
            # The spike is offline-only: anything that isn't a fixture is refused.
            log.append(("refused", request.resource_type, request.url, ""))
            route.abort("blockedbyclient")
    return handle


def mem_available_gb():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1024 / 1024
    return float("nan")


def check(label, ok):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def main():
    OUT.mkdir(exist_ok=True)
    engine = build_engine()
    ga = engine.check_network_urls("https://www.google-analytics.com/analytics.js", "https://news.test/", "script")
    results = [check(f"downloaded lists block google-analytics.com ({ga.filter})", ga.matched)]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for url in PAGES:
            log = []
            context = browser.new_context(accept_downloads=False, service_workers="block")
            context.route("**/*", make_handler(engine, log))  # before any page exists
            page = context.new_page()
            started = time.perf_counter()
            response = page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            page.wait_for_timeout(500)
            original = response.text()
            dom = page.content()
            shot = OUT / f"{urlsplit(url).hostname}.png"
            page.screenshot(path=shot)
            print(f"\n{url}  {time.perf_counter() - started:.2f}s  title={page.title()!r}")
            for decision, kind, req_url, rule in log:
                print(f"  {decision:8} {kind:10} {req_url}  {rule}")
            in_original = 'type="password"' in original
            in_dom = 'type="password"' in dom
            print(f"  password field: original HTML={in_original}, final DOM={in_dom}")
            print(f"  screenshot: {shot.relative_to(ROOT)}")
            print(f"  MemAvailable with Chromium open: {mem_available_gb():.1f} GB")
            if url == "https://news.test/":
                blocked = {u for d, _, u, _ in log if d == "blocked"}
                served = {u for d, _, u, _ in log if d == "fixture"}
                results += [
                    check("tracker.test script and pixel blocked",
                          {"https://tracker.test/t.js", "https://tracker.test/pixel.gif"} <= blocked),
                    check("first-party app.js served from fixtures", "https://news.test/app.js" in served),
                    check("form added by JavaScript seen only in the final DOM", in_dom and not in_original),
                ]
            context.close()
        browser.close()

    print("\nall checks passed" if all(results) else "\nSOME CHECKS FAILED")


if __name__ == "__main__":
    main()
