import asyncio

import fetch_static
import render


def fetch(url):
    return asyncio.run(fetch_static.fetch(url, 1))


def test_redirect_chain_is_followed_and_recorded():
    snapshot = fetch("https://short.test/abc")
    assert snapshot.final_url == "https://news.test/"
    assert [(r.status, r.url) for r in snapshot.redirects] == [
        (301, "https://short.test/abc"), (302, "https://hop.test/next")]


def test_extensionless_page_is_html_with_password_form():
    snapshot = fetch("https://paypa1-secure.test/login")
    assert snapshot.content_type == "text/html"
    assert snapshot.title == "PayPal: Log in to your account"
    assert [(f.action, f.has_password) for f in snapshot.forms] == [("https://collect.test/p", True)]


def test_non_http_schemes_are_refused():
    snapshot = fetch("file:///etc/passwd")
    assert snapshot.error.startswith("only http and https")
    assert snapshot.original_html == ""


def test_links_and_forms_resolve_against_base():
    html = ('<html><head><base href="https://cdn.example/app/"><title>t</title></head><body>'
            '<a href="next">Next</a><form action="login"><input type="password"></form></body></html>')
    page = render.parse(html, "https://site.example/page")
    assert page.base_url == "https://cdn.example/app/"
    assert page.links[0].url == "https://cdn.example/app/next"
    assert page.forms[0].action == "https://cdn.example/app/login"


def test_control_and_bidi_characters_are_stripped():
    page = render.parse("<title>Pay\x1b]0;owned\x07‮pal</title><p>Hello\x1b[31m red</p>", "https://a.example/")
    assert page.title == "Pay]0;ownedpal"
    assert "\x1b" not in page.markdown
