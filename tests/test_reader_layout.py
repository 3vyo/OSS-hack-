"""The reader's own layout for pages that aren't articles (sign-in pages, web apps)."""

import asyncio

import blocking
import policy
import render
from app import markdown_parser
from browser import Browser

SIGN_IN = """<html><head><title>Sign in</title></head><body><div class="x1">
<h2>Get started</h2><div><label for="e">Email</label><div><input id="e" type="email"></div></div>
<div><button>Continue</button></div><div><span>OR</span></div><div><button>Continue with Google</button></div>
<p>By clicking you agree to the <a href="/agreement">Customer Agreement</a>, and the <a href="https://x.example/p">Privacy Notice</a>.</p>
<div style="display:none">Hidden error text</div><div aria-hidden="true">Decoration</div>
<p>[fake](javascript:alert(1)) and *stars*</p><footer><a href="/terms">Terms</a> <span>|</span></footer>
</div></body></html>"""


def lines(markdown):
    return [line for line in markdown.split("\n\n") if line]


def test_sign_in_page_keeps_its_layout_and_shows_its_controls():
    page = render.parse(SIGN_IN, "https://signin.example/login")
    assert lines(page.markdown)[:6] == ["## Get started", "Email", "`[ ______ ]`", "`[ Continue ]`", "OR",
                                        "`[ Continue with Google ]`"]
    assert ("By clicking you agree to the [Customer Agreement](<https://signin.example/agreement>), "
            "and the [Privacy Notice](<https://x.example/p>).") in page.markdown
    assert "Hidden error text" not in page.markdown and "Decoration" not in page.markdown
    assert not any(line.strip("\\| ") == "" for line in lines(page.markdown))  # no separator-only lines


def test_page_text_never_becomes_markdown_syntax():
    page = render.parse(SIGN_IN, "https://signin.example/login")
    tokens = markdown_parser().parse(page.markdown)
    links = [token.attrs.get("href") for block in tokens if block.children
             for token in block.children if token.type == "link_open"]
    assert "javascript:alert(1)" not in links
    assert set(links) == {"https://signin.example/agreement", "https://x.example/p", "https://signin.example/terms"}


def test_article_keeps_the_text_extractor_and_lists_password_forms_underneath():
    paragraphs = "".join(f"<p>{sentence}</p>" for sentence in (
        "The council confirmed the new tram timetable for the winter months today, with services running "
        "every twelve minutes on the main lines and every twenty minutes late in the evening.",
        "Engineering works at Cornbrook will close two platforms for three weekends in November, and "
        "replacement buses will run between Cornbrook and Pomona while the track is renewed.",
        "Passengers are advised to check the journey planner before travelling, because the new timetable "
        "also changes the first and last trams on the airport line from the start of next month.",
        "The operator said contactless fares will stay the same, and that the extra trams at weekends "
        "are expected to ease crowding around the stadium on match days throughout the season."))
    html = (f"<html><body><article><h1>Winter timetable</h1>{paragraphs}</article>"
            "<form action='/login'><input name='user'><input type='password' name='pw'><button>Sign in</button>"
            "</form></body></html>")
    page = render.parse(html, "https://news.example/story")
    article, forms = page.markdown.split("**Forms on this page**")
    assert "new tram timetable" in article
    assert "`[ •••••• ]`" in forms and "`[ Sign in ]`" in forms


def test_lookalike_page_shows_the_password_box_it_asks_for():
    page = render.parse(policy.fixture_for("https://paypa1-secure.test/login").body.decode(),
                        "https://paypa1-secure.test/login")
    assert "`[ Email: ______ ]` `[ Password: •••••• ]` `[ Log in ]`" in page.markdown


def test_javascript_mode_skips_elements_hidden_by_css(monkeypatch):
    html = (b"<html><head><style>.alert{display:none}</style></head><body><h1>Sign in to Example</h1>"
            b"<div class='alert'>You signed in with another tab or window.</div>"
            b"<form><input name='login'><input type='password' name='pw'></form></body></html>")
    real_fixture_for = policy.fixture_for
    monkeypatch.setattr(policy, "fixture_for", lambda url: policy.Fixture(200, {"content-type": "text/html"}, html)
                        if url.startswith("https://hidden.test/") else real_fixture_for(url))

    async def main():
        browser = Browser(lambda: blocking.load())
        try:
            return await browser.inspect("https://hidden.test/", 1)
        finally:
            await browser.close()
    snapshot = asyncio.run(main())
    assert "Sign in to Example" in snapshot.markdown and "`[ •••••• ]`" in snapshot.markdown
    assert "another tab or window" not in snapshot.markdown


# Regression tests for the cloud review's findings.

ARTICLE = "".join(f"<p>{sentence}</p>" for sentence in (
    "The council confirmed the new tram timetable for the winter months today, with services running "
    "every twelve minutes on the main lines and every twenty minutes late in the evening.",
    "Engineering works at Cornbrook will close two platforms for three weekends in November, and "
    "replacement buses will run between Cornbrook and Pomona while the track is renewed.",
    "Passengers are advised to check the journey planner before travelling, because the new timetable "
    "also changes the first and last trams on the airport line from the start of next month.",
    "The operator said contactless fares will stay the same, and that the extra trams at weekends "
    "are expected to ease crowding around the stadium on match days throughout the season."))


def test_password_form_inside_a_hidden_popup_is_not_listed():
    html = (f"<html><body><article><h1>Winter timetable</h1>{ARTICLE}</article>"
            "<div class='modal' style='display:none'><form action='/login'><input type='password'></form></div>"
            "</body></html>")
    page = render.parse(html, "https://news.example/story")
    assert "new tram timetable" in page.markdown and "Forms on this page" not in page.markdown


def test_empty_heading_does_not_turn_the_next_text_into_a_heading():
    page = render.parse("<html><body><h2></h2>Stray text<p>Paragraph</p></body></html>", "https://a.example/")
    assert lines(page.markdown) == ["Stray text", "Paragraph"]


def test_list_item_keeps_its_bullet_when_its_text_is_in_a_div():
    page = render.parse("<html><body><ul><li><div>First</div></li><li>Second</li></ul></body></html>",
                        "https://a.example/")
    assert lines(page.markdown) == ["- First", "- Second"]


def test_hidden_text_inside_links_and_buttons_is_left_out():
    html = ("<html><body><p><a href='/x'>Visible<span style='display:none'>Hidden</span></a></p>"
            "<p><button>Go<span aria-hidden='true'>icon-glyph</span><script>var x</script></button></p></body></html>")
    page = render.parse(html, "https://a.example/")
    assert "[Visible](<https://a.example/x>)" in page.markdown and "`[ Go ]`" in page.markdown
    assert "Hidden" not in page.markdown and "icon-glyph" not in page.markdown and "var x" not in page.markdown
