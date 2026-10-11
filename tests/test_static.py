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


def test_cookie_banners_are_removed_from_reader_text_only():
    snapshot = fetch("https://cookies.test/")
    assert "tram line to the airport" in snapshot.markdown
    assert "analytics cookies" not in snapshot.markdown and "Accept All Cookies" not in snapshot.markdown
    assert snapshot.cookie_choices == ("Accept analytics cookies", "Reject analytics cookies", "Accept All Cookies")
    assert [form.action for form in snapshot.forms] == ["https://cookies.test/cookies"]  # checks still see it


def test_article_about_baking_cookies_is_kept():
    html = ('<html><body><article class="cookie-recipe"><h1>Chocolate chip cookies</h1>'
            '<p>Cream the butter and sugar, then fold in the chocolate chips and bake for twelve minutes '
            'until the edges of the cookies turn golden.</p></article></body></html>')
    page = render.parse(html, "https://bakes.example/")
    assert "fold in the chocolate chips" in page.markdown
    assert not any("cookie-consent" in note for note in page.notes)


def test_consent_wall_is_reported():
    snapshot = fetch("https://video.test/watch")
    assert snapshot.final_url.startswith("https://consent.test/")
    assert render.CONSENT_WALL_NOTE in snapshot.coverage


def test_unnamed_banner_is_found_by_its_buttons():
    # Like Google's: meaningless class names, no dialog role, buttons that only work with JavaScript.
    html = ('<html><head><title>Search</title></head><body><div class="xY1z"><div class="Kq9">'
            '<h1>Before you continue</h1><p>We use cookies and data to deliver and maintain our services '
            'and measure audience engagement.</p></div><div class="bb2"><button>Reject all</button>'
            '<button>Accept all</button><a href="/more">More options</a></div></div>'
            '<div class="page"><p>Search the web, find images, and read the news from around the world '
            'every single day with one simple box at the top of the page.</p></div></body></html>')
    page = render.parse(html, "https://search.example/")
    assert page.cookie_choices == ("Reject all", "Accept all")
    assert "We use cookies" not in page.markdown and "Search the web" in page.markdown


def test_small_page_wrapper_is_not_removed_with_its_banner():
    html = ('<html><body><div id="page"><p>Opening times for the library are nine until five on '
            'weekdays, and ten until four on Saturdays.</p><div class="x1"><p>This site uses cookies.</p>'
            '<button>Accept</button><button>Reject</button></div></div></body></html>')
    page = render.parse(html, "https://library.example/")
    assert "Opening times for the library" in page.markdown
    assert "uses cookies" not in page.markdown


def test_robot_check_is_recognised():
    assert render.is_robot_check(429, "Our systems have detected unusual traffic from your computer network.")
    assert not render.is_robot_check(200, "Our systems have detected unusual traffic")


def test_long_article_with_a_noscript_notice_is_readable_without_javascript():
    article = "".join(f"<p>Paragraph {n}: the council confirmed new tram times for the winter months, with more "
                      f"services at weekends and later trams on the airport line.</p>" for n in range(15))
    html = (f"<html><body><noscript>Please enable JavaScript to play this video.</noscript>"
            f"<article><h1>Tram times</h1>{article}</article><script>player()</script></body></html>")
    page = render.parse(html, "https://news.example/story")
    assert not page.needs_javascript
    assert any("parts of this page" in note for note in page.notes)
