import asyncio
import json
from types import SimpleNamespace

from textual.widgets import Static

import llm
from app import ConfirmBar, LinkInspector
from snapshot import Finding, Snapshot

DANGER = Finding("U1", "lookalike", "danger", "looks like PayPal", "why")


class FakeClient:
    """Stands in for ollama.AsyncClient. Optionally waits on a gate before answering."""

    def __init__(self, answer, gate: asyncio.Event | None = None):
        self.answer = answer
        self.gate = gate
        self.calls = []

    async def generate(self, **kwargs):
        return None

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.gate:
            await self.gate.wait()
        return SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.answer)))


PHISHING = {"assessment": "likely_phishing", "claimed_brand": "PayPal", "limitations": "Only the evidence.",
            "reasons": [{"evidence_ids": ["U1", "P1"], "explanation": "Lookalike domain and off-site form."}]}


def test_reasons_citing_unknown_evidence_are_dropped():
    answer = dict(PHISHING, reasons=[{"evidence_ids": ["U9"], "explanation": "made up"},
                                     {"evidence_ids": ["PAGE"], "explanation": "urgent deadline"}])
    analysis = llm.parse_answer(json.dumps(answer), 1, {"U1"}, 1.0)
    assert [r.evidence_ids for r in analysis.reasons] == [("PAGE",)]
    assert analysis.notes


def test_invalid_answers_fail_cleanly():
    assert llm.parse_answer("{not json", 1, set(), 1.0).status == "failed"
    assert llm.parse_answer(json.dumps({"assessment": "safe"}), 1, set(), 1.0).status == "failed"


def test_gemma_can_raise_but_never_lower_the_risk():
    snapshot = Snapshot(page_id=1, mode="static", requested_url="u", final_url="u", findings=(DANGER,))
    calm = llm.Analysis(1, "done", assessment="no_extra_concerns")
    assert llm.overall_level(snapshot, calm) == 2
    quiet = Snapshot(page_id=2, mode="static", requested_url="u", final_url="u")
    assert llm.overall_level(quiet, llm.Analysis(2, "done", assessment="likely_phishing")) == 2


def test_packet_lists_evidence_ids_and_marks_truncation():
    snapshot = Snapshot(page_id=1, mode="static", requested_url="u", final_url="https://x.test/",
                        findings=(DANGER,), markdown="word " * 500)
    packet, truncated = llm.evidence_packet(snapshot)
    assert "[U1] (danger) looks like PayPal" in packet and truncated


def run_app(client, scenario):
    async def main():
        app = LinkInspector(ai_client=client)
        async with app.run_test() as pilot:
            await scenario(app, pilot)
    asyncio.run(main())


async def settle(app, pilot):
    await pilot.pause()
    navigation = [w for w in app.workers if w.group == "navigation"]
    if navigation:  # an empty list would mean "wait for every worker", including Gemma's queue
        await app.workers.wait_for_complete(navigation)
    await pilot.pause()


async def open_confirmed(app, pilot, url):
    app.request_navigation(url, from_link=False)
    await pilot.pause()
    if app.query_one(ConfirmBar).display:
        await pilot.press("enter")
    await settle(app, pilot)


def test_risky_page_is_analysed_automatically():
    client = FakeClient(PHISHING)

    async def scenario(app, pilot):
        await open_confirmed(app, pilot, "https://paypa1-secure.test/login")
        for _ in range(20):
            await pilot.pause(0.05)
        analysis = app.analyses[app.current.page_id]
        assert analysis.status == "done" and analysis.assessment == "likely_phishing"
        assert "Likely phishing" in str(app.query_one("#info", Static).render())
        assert len(client.calls) == 1

    run_app(client, scenario)


def test_ordinary_page_is_not_analysed_automatically():
    client = FakeClient(PHISHING)

    async def scenario(app, pilot):
        await open_confirmed(app, pilot, "https://home.test/")
        await pilot.pause(0.2)
        assert client.calls == [] and app.current.page_id not in app.analyses

    run_app(client, scenario)


def test_late_answer_is_stored_for_its_own_page_only():
    gate = asyncio.Event()
    client = FakeClient(PHISHING, gate)

    async def scenario(app, pilot):
        await open_confirmed(app, pilot, "https://paypa1-secure.test/login")
        first = app.current.page_id
        await pilot.pause(0.1)
        await open_confirmed(app, pilot, "https://home.test/")
        gate.set()  # the slow answer arrives after the user moved on
        for _ in range(20):
            await pilot.pause(0.05)
        assert app.analyses[first].status == "done"
        assert app.current.page_id not in app.analyses
        assert "Likely phishing" not in str(app.query_one("#info", Static).render())

    run_app(client, scenario)


def test_app_works_with_ollama_switched_off():
    async def main():
        app = LinkInspector(ai_host="http://127.0.0.1:9")  # nothing listens here
        async with app.run_test() as pilot:
            await open_confirmed(app, pilot, "https://paypa1-secure.test/login")
            for _ in range(40):
                await pilot.pause(0.05)
            assert app.current.title == "PayPal: Log in to your account"
            assert app.analyst.available is False
            assert "Gemma isn't" in str(app.query_one("#info", Static).render())
    asyncio.run(main())
