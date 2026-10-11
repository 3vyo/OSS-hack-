"""Forms, misleading links, resource references."""

import re
from collections.abc import Callable
from dataclasses import replace
from urllib.parse import urlsplit

import url_checks
from snapshot import Finding, Snapshot

# Sign-in services that other sites legitimately send passwords to (single sign-on).
SSO_DOMAINS = ("microsoftonline.com", "login.live.com", "accounts.google.com", "appleid.apple.com",
               "okta.com", "auth0.com", "onelogin.com")

# Link text that reads like a web address, e.g. "www.paypal.com/help".
_ADDRESS_TEXT = re.compile(r"^(?:https?://)?((?:[\w-]+\.)+[a-z]{2,})(?:[/:?#]\S*)?$", re.IGNORECASE)


def _form_findings(snapshot: Snapshot) -> list[Finding]:
    findings = []
    page = urlsplit(snapshot.final_url)
    page_host = page.hostname or ""
    for form in snapshot.forms:
        if not form.has_password:
            continue
        action = urlsplit(form.action)
        action_host = action.hostname or page_host
        if page.scheme == "http" or action.scheme == "http":
            findings.append(Finding(
                "", "password-over-http", "danger",
                f"A password form would send what you type without encryption (to {form.action}).",
                "Anyone on the same network could read the password."))
        if url_checks.site_of(action_host) == url_checks.site_of(page_host):
            findings.append(Finding(
                "", "password-form", "info",
                f"This page asks for a password (the form sends to {action_host}).",
                "Phishing pages imitate pages that ask for passwords, so check the address carefully."))
        elif url_checks.host_in(action_host, SSO_DOMAINS):
            findings.append(Finding(
                "", "sso-form", "info",
                f"The sign-in form sends your password to {action_host}, a known sign-in service.",
                "Sending passwords to a central sign-in service (single sign-on) is normal for many organisations."))
        else:
            findings.append(Finding(
                "", "cross-site-form", "danger",
                f"The password form sends what you type to {action_host}, a different site from {page_host}.",
                "Login forms normally send passwords to their own site. Sending them elsewhere is how "
                "phishing pages collect them."))
    return findings


def _brand_title_findings(snapshot: Snapshot) -> list[Finding]:
    if not any(form.has_password for form in snapshot.forms):
        return []
    host = urlsplit(snapshot.final_url).hostname or ""
    title = snapshot.title.lower()
    findings = []
    for brand in url_checks.BRANDS:
        if not re.search(rf"\b{re.escape(brand.name.lower())}\b", title):
            continue
        if url_checks.is_official(host, brand):
            findings.append(Finding(
                "", "brand-official", "info",
                f"The page title names {brand.name}, and {host} is one of {brand.name}'s official sites.",
                "The address matches the brand the page claims to be. That doesn't prove the page is safe."))
        else:
            findings.append(Finding(
                "", "brand-title", "danger",
                f"The page title names {brand.name} and asks for a password, but {host} isn't one of "
                f"{brand.name}'s sites ({', '.join(brand.domains)}).",
                "Phishing pages copy a brand's name and look; the address is what gives them away."))
    return findings


def _link_findings(snapshot: Snapshot) -> list[Finding]:
    findings = []
    for link in snapshot.links:
        match = _ADDRESS_TEXT.match(link.text)
        real_host = urlsplit(link.url).hostname
        if not match or not real_host or not url_checks.has_public_suffix(match.group(1)):
            continue
        shown_host = match.group(1).lower()
        # "GOV.UK" linking to www.gov.uk is consistent; "paypal.com" linking to paypal.com.evil.test is not.
        consistent = (url_checks.host_in(real_host, (shown_host,))
                      or url_checks.site_of(shown_host) == url_checks.site_of(real_host))
        if not consistent:
            shown, real = url_checks.host_forms(real_host)
            destination = shown if shown == real else f"{shown} (really {real})"
            findings.append(Finding(
                "", "link-text-mismatch", "warning",
                f"A link says ‘{link.text}’ but really goes to {destination}.",
                "The words on a link can say anything; only where it really goes matters."))
    return findings


def _js_form_findings(snapshot: Snapshot, other_findings: list[Finding]) -> list[Finding]:
    """Password forms in the final page that weren't in the HTML the server sent (Chromium mode)."""
    if snapshot.mode != "chromium" or not snapshot.dom_comparable:
        return []
    original = {(form.action, form.method) for form in snapshot.original_forms if form.has_password}
    added = [form for form in snapshot.forms if form.has_password and (form.action, form.method) not in original]
    # On its own this is common (many web apps build their forms with JavaScript), so it only
    # becomes a warning alongside a danger finding.
    severity = "warning" if any(f.severity == "danger" for f in other_findings) else "info"
    return [Finding(
        "", "js-added-form", severity,
        f"A password form (sending to {urlsplit(form.action).hostname}) only appears after the page's JavaScript "
        f"runs; it isn't in the HTML the server sent.",
        "Phishing kits often add their login form with JavaScript so that scanners reading only the HTML miss it. "
        "Many ordinary web apps do this too.") for form in added]


# known_bad(url, resource_type, source_url) -> (list name, rule) or None, from blocking.Filters.
KnownBad = Callable[..., tuple[str, str] | None]


def known_bad_findings(urls: list[str], known_bad: KnownBad | None) -> list[Finding]:
    """Addresses on uBlock Origin's lists of known phishing and malware sites (Badware risks, URLhaus)."""
    findings = []
    for url in urls if known_bad else []:
        if (match := known_bad(url)) is not None:
            name, rule = match
            findings.append(Finding(
                "", "known-bad", "danger",
                f"{urlsplit(url).hostname} is on {name}, a list of known phishing and malware sites (rule {rule}).",
                "Security researchers keep these lists from reports of real attacks. An entry can be out of "
                "date, but treat the site as dangerous."))
    return findings


def _known_bad_resource_findings(snapshot: Snapshot, known_bad: KnownBad | None) -> list[Finding]:
    findings, seen = [], set()
    for record in snapshot.requests if known_bad else []:
        host = urlsplit(record.url).hostname
        if record.resource_type in ("document", "connection") or not record.url.startswith("http") or host in seen:
            continue
        if (match := known_bad(record.url, record.resource_type, snapshot.final_url)) is not None:
            seen.add(host)
            verb = "names" if record.decision == "reference" else "loads"
            findings.append(Finding(
                "", "known-bad-resource", "danger",
                f"The page {verb} a {record.resource_type} from {host}, which is on {match[0]}, a list of known "
                f"phishing and malware sites.",
                "Compromised and malicious pages often pull their payload in from another site."))
    return findings


def analyse(snapshot: Snapshot, known_bad: KnownBad | None = None) -> Snapshot:
    """Attach URL and page findings, with evidence IDs, to a snapshot."""
    urls = [hop.url for hop in snapshot.redirects] + [snapshot.final_url]
    url_findings = [finding for url in urls for finding in url_checks.check(url)]
    url_findings += known_bad_findings(urls, known_bad)
    page_findings = [] if snapshot.error else (
        _form_findings(snapshot) + _brand_title_findings(snapshot) + _link_findings(snapshot)
        + _known_bad_resource_findings(snapshot, known_bad))
    page_findings += _js_form_findings(snapshot, url_findings + page_findings)
    findings = url_checks.numbered(url_findings, "U") + url_checks.numbered(page_findings, "P")
    return replace(snapshot, findings=tuple(findings))
