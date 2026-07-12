from iot_tester.app import IotTesterApp
from iot_tester.screens.display import ASSETS_DIR, EMOTES, DisplayScreen


def test_every_emote_has_a_face_asset() -> None:
    for name in EMOTES:
        single = ASSETS_DIR / f"{name}.png"
        multi = list(ASSETS_DIR.glob(f"{name}_*.png"))
        assert single.exists() or multi, f"no asset found for emote {name!r}"


def test_display_screen_composes_without_error() -> None:
    screen = DisplayScreen()
    widgets = list(screen.compose())
    assert len(widgets) > 0


async def test_connect_button_shows_friendly_error_without_hardware() -> None:
    """On this dev machine there's no luma.oled, so clicking Connect must hit
    the try/except and show a friendly message instead of crashing."""
    app = IotTesterApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(DisplayScreen())
        await pilot.pause()
        await pilot.click("#connect-btn")
        await pilot.pause()
        panel = app.screen.query_one("#panel-area")
        texts = [str(s.render()) for s in panel.query("Static")]
        assert any("Could not open the OLED display" in t for t in texts)
