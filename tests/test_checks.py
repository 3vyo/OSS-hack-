import asyncio

import pytest

import fetch_static
import page_checks
import policy
import url_checks


def checks(url):
    return {(f.check, f.severity) for f in url_checks.check(url)}


def page(url):
    snapshot = page_checks.analyse(asyncio.run(fetch_static.fetch(url, 1)))
    return snapshot, {f.check for f in snapshot.findings}


@pytest.mark.parametrize("url", [
    "https://www.paypal.com/signin", "https://www.hmrc.gov.uk/", "https://foo.github.io/",
    "https://pineapple.com/", "https://apply.com/", "https://northbank.test/",
])
def test_ordinary_addresses_have_no_findings(url):
    assert checks(url) == set()


@pytest.mark.parametrize("url, expected", [
    ("https://paypa1-secure.test/login", ("lookalike", "danger")),
    ("https://rnicrosoft.com/", ("lookalike", "danger")),
    ("https://app1e.com/", ("lookalike", "danger")),
    ("https://steamcommunlty.com/", ("lookalike", "danger")),
    ("https://pаypal.test/", ("mixed-script", "danger")),
    ("https://paypal.com.account-verify.test/", ("brand-in-subdomain", "danger")),
    ("https://paypal-secure-login.com/", ("brand-in-domain", "warning")),
    ("https://paypal.com@evil.example/", ("userinfo", "danger")),
    ("http://192.168.1.1/", ("ip-host", "warning")),
])
def test_suspicious_addresses_are_flagged(url, expected):
    assert expected in checks(url)


def test_cyrillic_host_shows_real_ascii_form():
    findings = url_checks.check("https://pаypal.test/")
    assert any(f.check == "idn" and "xn--pypal-4ve.test" in f.observation for f in findings)


def test_private_suffix_tenants_are_different_sites():
    assert url_checks.site_of("foo.github.io") != url_checks.site_of("bar.github.io")


def test_lookalike_login_page_findings():
    _, found = page("https://paypa1-secure.test/login")
    assert {"lookalike", "cross-site-form", "brand-title", "link-text-mismatch"} <= found


def test_single_sign_on_is_informational_only():
    snapshot, found = page("https://intranet.test/")
    assert found == {"sso-form"}
    assert all(f.severity == "info" for f in snapshot.findings)


def test_evidence_ids_are_numbered_by_kind():
    snapshot, _ = page("https://paypa1-secure.test/login")
    ids = [f.id for f in snapshot.findings]
    assert ids[0] == "U1" and "P1" in ids and len(ids) == len(set(ids))


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://10.0.0.1/", "http://169.254.169.254/", "http://[::1]/",
    "http://[::ffff:127.0.0.1]/", "http://localhost/", "http://2130706433/", "http://224.0.0.1/",
])
def test_private_and_local_destinations_are_refused(url):
    with pytest.raises(policy.PolicyError):
        asyncio.run(policy.check_destination(url))


def test_public_ip_literal_is_allowed():
    asyncio.run(policy.check_destination("https://93.184.215.14/"))


def test_non_standard_ports_are_refused():
    with pytest.raises(policy.PolicyError):
        policy.check_url("http://example.com:8080/")


def test_redirect_to_loopback_is_stopped_and_recorded():
    snapshot, found = page("https://bounce.test/local")
    assert "private or local address" in snapshot.error
    assert snapshot.final_url == "http://127.0.0.1/"
    assert [r.url for r in snapshot.redirects] == ["https://bounce.test/local"]
    assert "ip-host" in found
