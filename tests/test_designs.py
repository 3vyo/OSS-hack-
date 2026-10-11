import asyncio

import pytest
from textual.widgets import Static

import designs
from app import LinkInspector


@pytest.fixture(autouse=True)
def settings(tmp_path, monkeypatch):
    """Never touch the real ~/.config/linkinspector/settings.json."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(designs, "SETTINGS", path)
    return path


def run(app, scenario):
    async def main():
        async with app.run_test(size=(120, 36)) as pilot:
            await scenario(app, pilot)
    asyncio.run(main())


def titlebar(app):
    return str(app.query_one("#titlebar-text", Static).render())


def test_f2_cycles_through_the_designs_and_remembers_the_choice(settings):
    async def scenario(app, pilot):
        assert app.theme == designs.DEFAULT and not settings.exists()  # the default isn't written out
        app.action_focus_page()
        seen = []
        for _ in designs.DESIGNS:
            await pilot.press("f2")
            await pilot.pause()
            seen.append(app.theme)
        assert seen == [d.name for d in designs.DESIGNS[1:]] + [designs.DESIGNS[0].name]
        await pilot.press("f2")
        await pilot.pause()
        assert designs.load_choice() == app.theme == designs.DESIGNS[1].name
    run(LinkInspector(ai_host="http://127.0.0.1:9"), scenario)


def test_saved_choice_is_used_next_time_and_design_option_wins(settings):
    designs.save_choice("windows-95")

    async def saved(app, pilot):
        assert app.theme == "windows-95" and "▤ linkinspector" in titlebar(app)
    run(LinkInspector(ai_host="http://127.0.0.1:9"), saved)

    async def requested(app, pilot):
        assert app.theme == "windows-xp"
    run(LinkInspector(ai_host="http://127.0.0.1:9", design="windows-xp"), requested)
    assert designs.load_choice() == "windows-95"  # starting with --design doesn't overwrite it


def test_side_panel_uses_the_designs_colours_and_any_theme_works():
    async def scenario(app, pilot):
        app.request_navigation("https://paypa1-secure.test/login", from_link=False)
        await pilot.pause(0.3)
        await pilot.press("enter")
        for _ in range(50):
            await pilot.pause(0.1)
            if app.current:
                break
        assert "PayPal: Log in to your account" in titlebar(app)
        panel = app.query_one("#info", Static).render()
        badge = designs.BY_NAME["windows-95"].colours.danger_badge.split(" on #")[1]  # "c00000"
        rgb = "rgb({},{},{})".format(*(int(badge[i:i + 2], 16) for i in (0, 2, 4)))
        assert any(f"on {rgb}" in str(span.style) for span in panel.spans)  # Textual stores colours as rgb()
        app.theme = "nord"  # a built-in Textual theme, e.g. picked from the command palette
        await pilot.pause()
        assert designs.colours_for(app.current_theme).danger == app.current_theme.error
        assert "Risk: HIGH" in str(app.query_one("#info", Static).render())
    run(LinkInspector(ai_host="http://127.0.0.1:9", design="windows-95"), scenario)


def test_every_design_has_readable_finding_colours():
    """WCAG contrast of at least 4.5:1 between each finding colour and the design's side-panel background."""
    def luminance(hex_colour):
        channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    too_low = []
    for design in designs.DESIGNS:
        background = luminance(design.theme.panel)
        for name in ("danger", "warning", "info", "ok"):
            colour = luminance(getattr(design.colours, name))
            ratio = (max(colour, background) + 0.05) / (min(colour, background) + 0.05)
            if ratio < 4.5:
                too_low.append(f"{design.name} {name}: {ratio:.1f}:1")
    assert not too_low
