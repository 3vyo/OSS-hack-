import asyncio

import pytest

import app as app_module
import render
from app import LinkInspector, address_to_url

DDG_RESULTS = """<html><head><title>manchester trams at DuckDuckGo</title></head><body>
<div class="result results_links result--ad"><a class="result__a" href="https://ads.example/">Advert</a></div>
<div class="result results_links"><h2><a class="result__a"
  href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.tfgm.com%2Fpublic%2Dtransport%2Ftram&amp;rut=abc">Trams | Bee Network</a></h2>
  <a class="result__snippet" href="#">Plan your journey on the Metrolink tram network.</a></div>
</body></html>"""

GOOGLE_LIKE = """<html><head><title>Google</title><noscript><meta content="0;url=/?gbv=1" http-equiv="refresh">
<div style="display:block">Please click <a href="/?gbv=1">here</a> if you are not redirected within a few seconds.</div>
</noscript></head><body><a href="/about">About</a> <a href="/store">Store</a><script>start()</script></body></html>"""


@pytest.mark.parametrize("typed, expected", [
    ("https://news.test/", "https://news.test/"),
    ("bbc.co.uk/news", "https://bbc.co.uk/news"),
    ("manchester trams", "https://html.duckduckgo.com/html/?q=manchester+trams"),
    ("metrolink", "https://html.duckduckgo.com/html/?q=metrolink"),
])
def test_address_bar_accepts_links_and_searches(typed, expected):
    assert address_to_url(typed) == expected


def test_search_engine_tracking_links_are_unwrapped():
    assert render.unwrap_redirect("https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.tfgm.com%2F&rut=x") == \
        "https://www.tfgm.com/"
    assert render.unwrap_redirect("https://www.google.com/url?q=https://example.org/&sa=U") == "https://example.org/"
    assert render.unwrap_redirect("https://duckduckgo.com/l/?uddg=javascript:alert(1)").startswith("https://duckduckgo")


def test_duckduckgo_results_are_listed_with_real_destinations():
    page = render.parse(DDG_RESULTS, "https://html.duckduckgo.com/html/?q=manchester+trams")
    assert "[Trams | Bee Network](<https://www.tfgm.com/public-transport/tram>)" in page.markdown
    assert "Metrolink tram network" in page.markdown and "Advert" not in page.markdown
    assert page.links[1].url == "https://www.tfgm.com/public-transport/tram"


def test_page_that_needs_javascript_is_flagged_and_its_redirect_text_dropped():
    page = render.parse(GOOGLE_LIKE, "https://www.google.com/")
    assert page.needs_javascript
    assert "not redirected" not in page.markdown


@pytest.fixture
def fake_clipboard(monkeypatch):
    store = {"text": "https://home.test/"}
    monkeypatch.setattr(app_module.clipboard, "paste", lambda: store["text"])
    monkeypatch.setattr(app_module.clipboard, "copy", lambda text: store.update(text=text) or True)
    return store


def test_ctrl_v_pastes_from_the_desktop_clipboard(fake_clipboard):
    async def scenario():
        app = LinkInspector(ai_host="http://127.0.0.1:9")
        async with app.run_test() as pilot:
            await pilot.click("#url")
            await pilot.press("ctrl+v")
            assert app.query_one("#url").value == "https://home.test/"
    asyncio.run(scenario())


def test_ctrl_c_with_nothing_selected_copies_the_page_address(fake_clipboard):
    async def scenario():
        app = LinkInspector("https://news.test/", ai_host="http://127.0.0.1:9")
        async with app.run_test() as pilot:
            for _ in range(40):
                await pilot.pause(0.05)
                if app.current:
                    break
            app.action_focus_page()
            fake_clipboard["text"] = ""
            await pilot.press("ctrl+c")
            assert fake_clipboard["text"] == "https://news.test/"
    asyncio.run(scenario())


def test_bare_domains_in_page_text_are_not_turned_into_links():
    tokens = app_module.markdown_parser().parse("Visit www.tfgm.com or [the map](https://tfgm.com/map).")
    kinds = [token.type for token in tokens[1].children]
    assert kinds.count("link_open") == 1
