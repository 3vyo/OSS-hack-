"""Forms, misleading links, resource references."""

import re
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
                "", "password-form", "warning",
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
        if re.search(rf"\b{re.escape(brand.name.lower())}\b", title) and not url_checks.is_official(host, brand):
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
        if url_checks.site_of(match.group(1)) != url_checks.site_of(real_host):
            shown, real = url_checks.host_forms(real_host)
            destination = shown if shown == real else f"{shown} (really {real})"
            findings.append(Finding(
                "", "link-text-mismatch", "warning",
                f"A link says ‘{link.text}’ but really goes to {destination}.",
                "The words on a link can say anything; only where it really goes matters."))
    return findings


def analyse(snapshot: Snapshot) -> Snapshot:
    """Attach URL and page findings, with evidence IDs, to a snapshot."""
    urls = [hop.url for hop in snapshot.redirects] + [snapshot.final_url]
    url_findings = [finding for url in urls for finding in url_checks.check(url)]
    page_findings = [] if snapshot.error else (
        _form_findings(snapshot) + _brand_title_findings(snapshot) + _link_findings(snapshot))
    findings = url_checks.numbered(url_findings, "U") + url_checks.numbered(page_findings, "P")
    return replace(snapshot, findings=tuple(findings))
