import asyncio

from textual.widgets import Markdown

from app import ConfirmBar, LinkInspector


async def settle(app, pilot):
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def test_click_through_demo_pages_and_history():
    async def scenario():
        app = LinkInspector("https://home.test/")
        async with app.run_test() as pilot:
            await settle(app, pilot)
            assert app.current.title == "linkinspector demo pages"

            markdown = app.query_one("#content", Markdown)
            markdown.post_message(Markdown.LinkClicked(markdown, "https://short.test/abc"))
            await pilot.pause()
            confirm = app.query_one(ConfirmBar)
            assert confirm.display and confirm.url == "https://short.test/abc"  # leaving the site
            await pilot.press("enter")
            await settle(app, pilot)
            assert not confirm.display
            assert app.current.final_url == "https://news.test/"
            assert len(app.current.redirects) == 2

            markdown.post_message(Markdown.LinkClicked(markdown, "javascript:alert(1)"))
            await settle(app, pilot)
            assert app.current.error.startswith("only http and https")
            assert len(app.history) == 3

            await app.run_action("back")
            assert app.current.final_url == "https://news.test/"
            await app.run_action("back")
            assert app.current.title == "linkinspector demo pages"
            await app.run_action("forward")
            assert app.current.final_url == "https://news.test/"
            assert app.query_one("#url").value == "https://news.test/"

    asyncio.run(scenario())


def test_typed_lookalike_asks_first_and_can_be_cancelled():
    async def scenario():
        app = LinkInspector()
        async with app.run_test() as pilot:
            await pilot.click("#url")
            await pilot.press(*"paypa1-secure.test/login", "enter")
            await pilot.pause()
            confirm = app.query_one(ConfirmBar)
            assert confirm.display and confirm.url == "https://paypa1-secure.test/login"
            await pilot.press("escape")
            await settle(app, pilot)
            assert not confirm.display
            assert app.history == []  # nothing was fetched

    asyncio.run(scenario())
