"""Run the demo script (DEMO.md) through the real app and check each beat.

    .venv/bin/python scripts/rehearse_demo.py ai-on     # with Gemma (about 1 minute)
    .venv/bin/python scripts/rehearse_demo.py ai-off    # without Gemma
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from textual.widgets import Static
from app import ConfirmBar, LinkInspector

AI = len(sys.argv) < 2 or sys.argv[1] != "ai-off"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else None
results = []

def check(beat, ok, detail=""):
    results.append((beat, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {beat}  {detail}", flush=True)

async def wait_nav(app, pilot):
    await pilot.pause()
    navigation = [w for w in app.workers if w.group == "navigation"]
    if navigation:
        await app.workers.wait_for_complete(navigation)
    await pilot.pause(0.3)

async def wait_gemma(app, pilot, limit=120):
    started = time.perf_counter()
    while time.perf_counter() - started < limit:
        analysis = app.analyses.get(app.current.page_id)
        if analysis and analysis.status not in ("queued", "analysing"):
            return analysis
        await pilot.pause(0.5)
    return app.analyses.get(app.current.page_id)

async def open_link(app, pilot, url):
    app.request_navigation(url, from_link=True)
    await pilot.pause(0.3)
    bar = app.query_one(ConfirmBar)
    text = " ".join(str(bar.render()).split()) if bar.display else ""
    if bar.display:
        await pilot.press("enter")
    await wait_nav(app, pilot)
    return text

async def main():
    app = LinkInspector("https://home.test/", **({} if AI else {"ai_host": "http://127.0.0.1:9"}))
    async with app.run_test(size=(140, 46)) as pilot:
        await app.filters_ready.wait()
        await wait_nav(app, pilot)
        for _ in range(60):
            if app.analyst.available is not None:
                break
            await pilot.pause(0.5)
        check("app starts on the demo pages", app.current.title == "linkinspector demo pages",
              f"Gemma available: {app.analyst.available}")

        bar = await open_link(app, pilot, "https://paypa1-secure.test/login")
        check("2 confirm bar before fetching", "looks like PayPal" in bar)
        info = str(app.query_one("#info", Static).render())
        check("2 risk HIGH and findings", "Risk: HIGH" in info and "Password sent to another site" in info)
        check("2 reader shows the password box", "Password: ••••••" in app.current.markdown)
        if AI:
            analysis = await wait_gemma(app, pilot)
            check("2 Gemma says likely phishing", analysis and analysis.assessment == "likely_phishing",
                  f"{analysis.assessment if analysis else None} in {analysis.seconds:.0f} s" if analysis else "")
            if OUT:
                (OUT / "screenshot.svg").write_text(app.export_screenshot())

        app.request_navigation("https://pаypal.test/", from_link=False)
        await pilot.pause(0.3)
        bar = " ".join(str(app.query_one(ConfirmBar).render()).split())
        check("3 Cyrillic link shown with its real form", "really xn--pypal-4ve.test" in bar)
        await pilot.press("escape")

        bar = await open_link(app, pilot, "https://prize.test/")
        check("4 known bad before opening", "Known phishing/malware site" in bar)
        check("4 hidden AI instructions visible in the reader", "Ignore previous instructions" in app.current.markdown)
        if AI:
            analysis = await wait_gemma(app, pilot)
            check("4 Gemma not fooled by the hidden instructions",
                  analysis and analysis.assessment == "likely_phishing",
                  f"{analysis.assessment if analysis else None} in {analysis.seconds:.0f} s" if analysis else "")

        await open_link(app, pilot, "https://news.test/")
        app.action_focus_page()
        await pilot.press("ctrl+r")
        await wait_nav(app, pilot)
        blocked = sum(r.decision == "blocked" for r in app.current.requests)
        check("5 JavaScript mode: trackers blocked", app.current.mode == "chromium" and blocked == 2, f"{blocked} blocked")
        check("5 form added by JavaScript", any(f.check == "js-added-form" for f in app.current.findings))

        await open_link(app, pilot, "https://intranet.test/")
        info = str(app.query_one("#info", Static).render())
        check("6 single sign-on: no false alarm", "No warning signs found" in info,
              str([f.check for f in app.current.findings]))

        await open_link(app, pilot, "https://bounce.test/local")
        check("7 redirect to this computer refused", "refused" in app.current.error, app.current.error[:70])

    print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} beats passed ({'AI on' if AI else 'AI off'})")

asyncio.run(main())
