"""Asking Gemma about the page (ctrl+t), with a fake Ollama client that streams its answer."""

import asyncio
import json
from types import SimpleNamespace

import llm
import render
from app import LinkInspector
from snapshot import Finding, Snapshot

REPLY = 'Paragraph 2 says: "Organisers expect around twenty thousand visitors." (¶2)'


class StreamingFake:
    """Stands in for ollama.AsyncClient: streams chat answers word by word."""

    def __init__(self, reply=REPLY):
        self.reply = reply
        self.calls = []

    async def generate(self, **kwargs):
        return None

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            async def parts():
                for word in self.reply.split(" "):
                    yield SimpleNamespace(message=SimpleNamespace(content=word + " "), done=False)
            return parts()
        if "format" in kwargs:  # a security analysis
            answer = {"assessment": "suspicious", "reasons": [], "claimed_brand": None, "limitations": ""}
            return SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))
        return SimpleNamespace(message=SimpleNamespace(content="ready"))  # reading the page


def test_reader_and_gemma_use_the_same_paragraph_numbers():
    markdown = "# Title\n\nFirst para.\n\n- an item\n\n> a quote\n\nSecond [link](<https://a.example/>) para."
    assert render.with_paragraph_numbers(markdown).split("\n\n") == [
        "# Title", "`¶1` First para.", "- `¶2` an item", "> a quote", "`¶3` Second [link](<https://a.example/>) para."]
    snapshot = Snapshot(page_id=1, mode="static", requested_url="u", final_url="https://a.example/",
                        title="T", markdown=markdown,
                        findings=(Finding("P1", "password-form", "info", "asks for a password", "why"),))
    page = llm.chat_page(snapshot)
    assert "¶1 First para." in page and "¶3 Second link para." in page  # link targets left out
    assert "P1: asks for a password" in page and "not part of the page" in page


def test_long_pages_are_cut_and_gemma_is_told_where():
    markdown = "\n\n".join(f"Paragraph {n} " + "word " * 100 for n in range(1, 40))
    page = llm.chat_page(Snapshot(page_id=1, mode="static", requested_url="u", final_url="u", markdown=markdown))
    assert len(page) < llm.CHAT_PAGE_BUDGET + 1000
    assert "The rest of the page was cut here after ¶" in page


def test_security_analysis_runs_before_a_waiting_chat_question():
    async def main():
        client = StreamingFake()
        analyst = llm.Analyst(lambda analysis: None, lambda page_id: True, client=client)
        snapshot = Snapshot(page_id=1, mode="static", requested_url="u", final_url="https://a.example/",
                            markdown="Hello.")
        analyst.prime(snapshot)
        analyst.request(snapshot)
        runner = asyncio.create_task(analyst.run())
        for _ in range(50):
            await asyncio.sleep(0.01)
        runner.cancel()
        kinds = ["security" if "format" in call else "chat" for call in client.calls]
        assert kinds == ["security", "chat"]
    asyncio.run(main())


async def _open_page(app, pilot):
    for _ in range(60):
        await pilot.pause(0.05)
        if app.current:
            break
    app.action_focus_page()


async def _wait_until_answered(app, pilot):
    for _ in range(100):
        await pilot.pause(0.05)
        if not app.chat_busy:
            break


def test_ask_about_a_paragraph_and_follow_up():
    client = StreamingFake()

    async def main():
        app = LinkInspector("https://news.test/", ai_client=client)
        async with app.run_test() as pilot:
            await _open_page(app, pilot)
            await pilot.press("ctrl+t")
            await pilot.pause(0.2)
            assert app.chat_open and "`¶2` Organisers expect" in app.reader_body(app.current)
            for question in ("what does paragraph 2 say?", "and paragraph 1?"):
                await pilot.press(*question, "enter")
                await _wait_until_answered(app, pilot)
            page_id = app.current.page_id
            assert [q for q, _ in app.chats[page_id]] == ["what does paragraph 2 say?", "and paragraph 1?"]
            assert app.chats[page_id][0][1] == REPLY

            streamed = [call for call in client.calls if call.get("stream")]
            first, follow_up = streamed[0]["messages"], streamed[1]["messages"]
            assert "¶2 Organisers expect" in first[0]["content"]  # the page, with the reader's numbers
            assert first[0] == follow_up[0]  # identical page prompt, so Ollama can reuse its work
            assert [m["role"] for m in follow_up] == ["system", "user", "assistant", "user"]
            primes = [call for call in client.calls if call.get("options", {}).get("num_predict") == 1]
            assert len(primes) == 1  # Gemma read the page once, when the chat opened

            await pilot.press("escape")
            await pilot.pause(0.2)
            assert not app.chat_open and "¶" not in app.reader_body(app.current)
    asyncio.run(main())


def test_chat_explains_when_gemma_is_not_running():
    async def main():
        app = LinkInspector("https://news.test/", ai_host="http://127.0.0.1:9")
        async with app.run_test() as pilot:
            await _open_page(app, pilot)
            for _ in range(40):
                await pilot.pause(0.05)
                if app.analyst.available is False:
                    break
            await pilot.press("ctrl+t")
            await pilot.pause(0.2)
            await pilot.press(*"what is this?", "enter")
            await _wait_until_answered(app, pilot)
            log = " ".join(str(widget.render()) for widget in app.query("#chat-log Static"))
            assert "Gemma isn't running" in log or "couldn't answer" in log
    asyncio.run(main())


def test_a_page_finishing_loading_does_not_steal_focus_from_the_chat(monkeypatch):
    import app as app_module
    loaded = asyncio.Event()
    real_fetch = app_module.fetch_static.fetch

    async def slow_fetch(url, page_id):
        await loaded.wait()  # the page finishes loading only once the chat is open
        return await real_fetch(url, page_id)

    async def main():
        app = LinkInspector("https://news.test/", ai_client=StreamingFake())
        async with app.run_test() as pilot:
            app.history.append(await real_fetch("https://home.test/", 99))  # something to chat about
            app.position = 0
            await pilot.press("ctrl+t")
            await pilot.pause(0.2)
            assert app.focused.id == "chat-input"
            monkeypatch.setattr(app_module.fetch_static, "fetch", slow_fetch)
            app.navigate("https://news.test/")
            await pilot.pause(0.1)
            loaded.set()
            for _ in range(40):
                await pilot.pause(0.05)
                if app.current.final_url == "https://news.test/":
                    break
            await pilot.pause(0.2)
            assert app.focused.id == "chat-input"
    asyncio.run(main())
