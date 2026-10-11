"""uBlock Origin's lists: format conversion, known phishing and malware sites, and their findings."""

import asyncio

import pytest

import blocking
import page_checks
from app import ConfirmBar, LinkInspector
from snapshot import RequestRecord, Snapshot


@pytest.mark.parametrize("line, bare_hosts, expected", [
    # $all means every request type, pages included; rules with a path need a $document copy.
    ("||evil.example/x.exe^$all", False, ["||evil.example/x.exe^", "||evil.example/x.exe^$document"]),
    ("||evil.example^$all", False, ["||evil.example^"]),
    ("||ads.example^$all,third-party", False, ["||ads.example^$third-party"]),
    ("example.com##+js(set, flag, $all)", False, ["example.com##+js(set, flag, $all)"]),  # cosmetic: untouched
    # URLhaus's bare entries are host names, as uBlock Origin reads them.
    ("1.2.3.4", True, ["||1.2.3.4^"]),
    ("evil.example/payload.exe", True, ["||evil.example/payload.exe", "||evil.example/payload.exe$document"]),
    ("1.2.3.4", False, ["1.2.3.4"]),
])
def test_ublock_syntax_is_rewritten_for_the_engine(line, bare_hosts, expected):
    assert blocking._normalise(line, bare_hosts).split("\n") == expected


@pytest.fixture
def security_lists(tmp_path):
    (tmp_path / "urlhaus.txt").write_text(
        "! Title: Online Malicious URL Blocklist\n! Updated: 2026-10-11T12:24:33Z\n"
        "1.2.3.4\nmalware.example\n||evil.example/umbrella/^$all\n")
    (tmp_path / "ublock-badware.txt").write_text(
        "! Title: uBlock filters – Badware risks\n! Last modified: Sun, 11 Oct 2026 07:47:42 +0000\n"
        "||badsite.example^$doc\n")
    return blocking.load(tmp_path)


def test_known_bad_sites_name_the_list_that_flagged_them(security_lists):
    assert security_lists.known_bad("http://1.2.3.4/login") == ("URLhaus malicious URLs", "||1.2.3.4^")
    assert security_lists.known_bad("http://evil.example/umbrella/")[0] == "URLhaus malicious URLs"
    assert security_lists.known_bad("http://evil.example/umbrella/", "script", "https://a.example/")
    assert security_lists.known_bad("https://badsite.example/") == ("uBlock Badware risks", "||badsite.example^$doc")
    assert security_lists.known_bad("https://badsite.example/x.js", "script", "https://a.example/") is None
    assert security_lists.known_bad("https://notmalware.example/") is None  # no substring matches
    assert "Known phishing and malware sites: uBlock Badware risks 2026-10-11, URLhaus malicious URLs 2026-10-11" \
        in security_lists.summary


def test_known_bad_sites_become_findings(security_lists):
    snapshot = Snapshot(page_id=1, mode="static", requested_url="https://news.example/",
                        final_url="https://news.example/",
                        requests=(RequestRecord("http://malware.example/payload.js", "script", "reference"),))
    found = page_checks.analyse(snapshot, security_lists.known_bad).findings
    assert [(f.check, f.severity) for f in found] == [("known-bad-resource", "danger")]
    assert "names a script from malware.example" in found[0].observation
    redirected = Snapshot(page_id=2, mode="static", requested_url="https://short.example/x",
                          final_url="https://badsite.example/")
    found = page_checks.analyse(redirected, security_lists.known_bad).findings
    assert [(f.id, f.check) for f in found] == [("U1", "known-bad")]


def test_confirm_bar_warns_about_a_known_bad_site_before_fetching():
    async def main():
        app = LinkInspector(ai_host="http://127.0.0.1:9")
        async with app.run_test() as pilot:
            await app.filters_ready.wait()
            await pilot.click("#url")
            await pilot.press(*"prize.test", "enter")
            await pilot.pause()
            confirm = app.query_one(ConfirmBar)
            assert confirm.display and "Known phishing/malware site" in str(confirm.render())
            assert app.history == []  # nothing fetched yet
    asyncio.run(main())
