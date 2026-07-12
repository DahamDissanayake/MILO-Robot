from iot_tester.app import IotTesterApp, MainMenu
from iot_tester.screens.camera import CameraScreen
from iot_tester.screens.display import DisplayScreen
from iot_tester.screens.i2c_scan import I2cScanScreen
from iot_tester.screens.imu import ImuScreen
from iot_tester.screens.microphones import MicScreen
from iot_tester.screens.results import ResultsScreen
from iot_tester.screens.servos import ServoScreen
from iot_tester.screens.speaker import SpeakerScreen
from iot_tester.screens.wiring import WiringScreen


async def test_app_boots_to_main_menu() -> None:
    app = IotTesterApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, MainMenu)


async def test_every_screen_pushes_and_pops_without_crashing() -> None:
    app = IotTesterApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screens = [
            WiringScreen(),
            I2cScanScreen(app.recorder),
            ServoScreen(app.recorder),
            DisplayScreen(app.recorder),
            ImuScreen(app.recorder),
            CameraScreen(app.recorder),
            MicScreen(app.recorder),
            SpeakerScreen(app.recorder),
            ResultsScreen(app.recorder),
        ]
        for screen in screens:
            app.push_screen(screen)
            await pilot.pause()
            assert app.screen is screen
            app.pop_screen()
            await pilot.pause()
        assert isinstance(app.screen, MainMenu)
