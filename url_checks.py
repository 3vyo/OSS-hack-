"""Code-based URL checks (no network)."""

import ipaddress
import re
from dataclasses import dataclass, replace
from functools import cache
from urllib.parse import urlsplit

import tldextract
from confusable_homoglyphs import confusables
from rapidfuzz.distance import Levenshtein

from render import safe_text
from snapshot import Finding

# Offline: the bundled Public Suffix List, private suffixes such as github.io included,
# and .test for the demo pages.
_extract = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True, extra_suffixes=("test",))


@dataclass(frozen=True)
class Brand:
    name: str
    tokens: tuple[str, ...]  # how the name appears inside domain names
    domains: tuple[str, ...]  # official domains; their subdomains count too


BRANDS = (
    Brand("PayPal", ("paypal",), ("paypal.com", "paypal.me")),
    Brand("Microsoft", ("microsoft", "office365", "outlook", "onedrive"),
          ("microsoft.com", "microsoftonline.com", "live.com", "office.com", "outlook.com", "sharepoint.com")),
    Brand("Google", ("google", "gmail"), ("google.com", "google.co.uk", "gmail.com", "youtube.com")),
    Brand("Apple", ("apple", "icloud", "appleid"), ("apple.com", "icloud.com")),
    Brand("Amazon", ("amazon",), ("amazon.com", "amazon.co.uk")),
    Brand("Netflix", ("netflix",), ("netflix.com",)),
    Brand("Facebook", ("facebook",), ("facebook.com", "fb.com")),
    Brand("Instagram", ("instagram",), ("instagram.com",)),
    Brand("WhatsApp", ("whatsapp",), ("whatsapp.com",)),
    Brand("LinkedIn", ("linkedin",), ("linkedin.com",)),
    Brand("Steam", ("steamcommunity", "steampowered"), ("steamcommunity.com", "steampowered.com")),
    Brand("Coinbase", ("coinbase",), ("coinbase.com",)),
    Brand("DHL", ("dhl",), ("dhl.com", "dhl.co.uk")),
    Brand("Royal Mail", ("royalmail",), ("royalmail.com", "royalmail.co.uk")),
    Brand("Evri", ("evri",), ("evri.com",)),
    Brand("DPD", ("dpd",), ("dpd.co.uk", "dpd.com")),
    Brand("HMRC", ("hmrc",), ("gov.uk",)),
    Brand("DVLA", ("dvla",), ("gov.uk",)),
    Brand("Monzo", ("monzo",), ("monzo.com",)),
    Brand("Barclays", ("barclays",), ("barclays.co.uk", "barclays.com")),
    Brand("HSBC", ("hsbc",), ("hsbc.co.uk", "hsbc.com")),
    Brand("Lloyds Bank", ("lloyds", "lloydsbank"), ("lloydsbank.com", "lloydsbank.co.uk")),
    Brand("NatWest", ("natwest",), ("natwest.com",)),
    Brand("Santander", ("santander",), ("santander.co.uk", "santander.com")),
    Brand("Nationwide", ("nationwide",), ("nationwide.co.uk",)),
    Brand("Northbank", ("northbank",), ("northbank.test",)),  # made-up bank for the demo pages
)

# Character swaps that make a name read like another one.
_SWAPS = str.maketrans({"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b"})


def _normalise(token: str) -> str:
    return token.translate(_SWAPS).replace("rn", "m").replace("vv", "w")


@cache
def _latin_lookalike(char: str) -> str:
    if char.isascii():
        return char
    for item in confusables.is_confusable(char, preferred_aliases=["latin"]) or []:
        for glyph in item["homoglyphs"]:
            if glyph["c"].isascii():
                return glyph["c"].lower()
    return char


def skeleton(text: str) -> str:
    """Replace letters from other alphabets with the Latin letters they look like."""
    return "".join(_latin_lookalike(char) for char in text)


def host_forms(host: str) -> tuple[str, str]:
    """The host as people see it (Unicode) and as it really is (ASCII/punycode)."""
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError:
        ascii_host = host
    try:
        unicode_host = ascii_host.encode("ascii").decode("idna")
    except UnicodeError:
        unicode_host = host
    return unicode_host, ascii_host


def site_of(host: str) -> str:
    """The registrable domain that decides who controls a host, e.g. paypal.com for www.paypal.com."""
    ascii_host = host_forms(host)[1]
    return _extract(ascii_host).top_domain_under_public_suffix or ascii_host


def has_public_suffix(host: str) -> bool:
    return bool(_extract(host_forms(host)[1]).suffix)


def host_in(host: str, domains: tuple[str, ...]) -> bool:
    host = host_forms(host)[1]
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def is_official(host: str, brand: Brand) -> bool:
    return host_in(host, brand.domains)


def _tokens(label: str) -> set[str]:
    parts = [part for part in re.split(r"[^0-9a-z]+", label) if part]
    return set(parts) | ({"".join(parts)} if len(parts) > 1 else set())


def _match(token: str, brand: Brand) -> str | None:
    """'exact' when the brand name appears as written, 'lookalike' when it is altered."""
    # Substring and fuzzy matches only for longer names: "apple" would otherwise
    # match pineapple.com and apply.com.
    for name in brand.tokens:
        if token == name or (len(name) >= 6 and name in token):
            return "exact"
        normalised, target = _normalise(token), _normalise(name)
        if normalised == target or (
                len(target) >= 6 and Levenshtein.distance(normalised, target) <= (2 if len(target) >= 9 else 1)):
            return "lookalike"
    return None


def _brand_findings(unicode_host: str, ascii_host: str) -> list[Finding]:
    extracted = _extract(ascii_host)
    if not extracted.suffix or not extracted.domain:
        return []
    labels = unicode_host.split(".")
    suffix_length = len(extracted.suffix.split("."))
    domain_label = labels[-suffix_length - 1]
    subdomain = ".".join(labels[:-suffix_length - 1])
    site = ".".join(labels[-suffix_length - 1:])
    disguised = skeleton(domain_label) != domain_label
    domain_tokens = _tokens(skeleton(domain_label))
    sub_tokens = _tokens(skeleton(subdomain))
    findings = []
    for brand in BRANDS:
        if is_official(ascii_host, brand):
            continue
        official = ", ".join(brand.domains)
        matches = {_match(token, brand) for token in domain_tokens} - {None}
        if "lookalike" in matches or ("exact" in matches and disguised):
            findings.append(Finding(
                "", "lookalike", "danger",
                f"‘{site}’ looks like {brand.name}, but {brand.name}'s sites are {official}.",
                "Phishing sites copy a brand's name with small changes, like 1 for l or letters from "
                "another alphabet, so the address looks right at a glance."))
        elif any(_match(token, brand) for token in sub_tokens):
            fakes_domain = any(domain in subdomain for domain in brand.domains)
            findings.append(Finding(
                "", "brand-in-subdomain", "danger" if fakes_domain else "warning",
                f"The address starts with ‘{subdomain}’, but the site is really {site}.",
                "Only the end of a web address decides who owns the site; the start can say anything."))
        elif matches:
            findings.append(Finding(
                "", "brand-in-domain", "warning",
                f"The domain ‘{site}’ contains {brand.name}'s name, but it isn't one of {brand.name}'s sites ({official}).",
                "Anyone can register a domain that contains a brand's name."))
    return findings


def check(url: str) -> list[Finding]:
    """Findings about a URL itself, before anything is fetched."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    findings = []
    if parts.username is not None:
        findings.append(Finding(
            "", "userinfo", "danger",
            f"The link starts with ‘{safe_text(parts.username)}@’, which browsers ignore. The site is really {host}.",
            "Text before an @ in a web address is a login name, not the website, so it can fake a trusted name."))
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        findings.append(Finding(
            "", "ip-host", "warning",
            f"The link points straight to the IP address {ip} instead of a domain name.",
            "Real services almost always use a name. A bare address hides who runs the site."))
        return findings
    unicode_host, ascii_host = host_forms(host)
    if unicode_host != ascii_host:
        findings.append(Finding(
            "", "idn", "info",
            f"The name contains non-Latin characters: it's shown as {unicode_host} but is really {ascii_host}.",
            "Letters from other alphabets can look identical to Latin ones."))
    if any(confusables.is_mixed_script(label) for label in unicode_host.split(".")):
        findings.append(Finding(
            "", "mixed-script", "danger",
            f"‘{unicode_host}’ mixes letters from different alphabets.",
            "Real domain names almost never mix alphabets; it's a classic way to fake a familiar name."))
    return findings + _brand_findings(unicode_host, ascii_host)


def numbered(findings: list[Finding], prefix: str) -> list[Finding]:
    """Give findings stable evidence IDs (U1, U2, … or P1, P2, …), dropping repeats."""
    unique = list(dict.fromkeys(findings))
    return [replace(finding, id=f"{prefix}{index}") for index, finding in enumerate(unique, 1)]
