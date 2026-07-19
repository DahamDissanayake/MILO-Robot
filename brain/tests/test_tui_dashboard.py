"""DashboardScreen.refresh_from renders identity/connection/model panels."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.widgets import ProgressBar, Static as TextualStatic

from milo_brain.config import BrainConfig
from milo_brain.llm.token_rate import TokenRateTracker
from milo_brain.tui.dashboard import ConnectionPanel, DashboardScreen, IdentityPanel, ModelPanel


class _FakePeer:
    def __init__(self, name):
        self.name = name


class _FakeConnector:
    def __init__(
        self, connected_robot=None, paired=(), last_connected=None,
        link_state="idle", link_target=None, last_error=None,
        retry_at=None, consecutive_drops=0,
    ):
        self.connected_robot = connected_robot
        self._paired = list(paired)
        self.last_connected = last_connected
        self.link_state = link_state
        self.link_target = link_target
        self.last_error = last_error
        self.retry_at = retry_at
        self.consecutive_drops = consecutive_drops

    def paired_ids(self):
        return self._paired


class _HostApp(App):
    def compose(self) -> ComposeResult:
        yield DashboardScreen()


def test_refresh_from_renders_all_three_panels():
    async def scenario():
        cfg = BrainConfig(
            brain_id="brain-abc", name="my-laptop", tier="small", gpu="RTX 4050",
            llm_model="llama3.2:3b", whisper_model="small", piper_voice="en_US-lessac-medium",
        )
        connector = _FakeConnector(
            connected_robot=_FakePeer("milo-1"), paired=["milo-1"], link_state="connected",
        )
        tracker = TokenRateTracker()
        tracker.record_prompt_eval(100, 200_000_000)  # 500 tok/s

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, tracker)

            identity = str(screen.query_one(IdentityPanel).content)
            connection = str(screen.query_one(ConnectionPanel).content)
            model = str(screen.query_one(ModelPanel).content)

            assert "my-laptop" in identity and "brain-abc" in identity and "RTX 4050" in identity
            assert "milo-1" in connection and "1" in connection  # paired count
            assert "llama3.2:3b" in model and "500.0" in model

    asyncio.run(scenario())


def test_refresh_from_shows_no_robot_connected():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(connected_robot=None, paired=[])
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "no robot connected" in connection

    asyncio.run(scenario())


def test_refresh_from_hints_reconnect_when_a_previous_target_is_known():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(connected_robot=None, paired=[], last_connected=("10.0.0.9", 8765))
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "10.0.0.9:8765" in connection
            assert "r to reconnect" in connection

    asyncio.run(scenario())


def test_refresh_from_omits_reconnect_hint_once_actually_connected():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(
            connected_robot=_FakePeer("milo-1"), paired=["milo-1"], last_connected=("10.0.0.9", 8765),
            link_state="connected",
        )
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "10.0.0.9" not in connection

    asyncio.run(scenario())


def test_refresh_from_shows_connecting_stage():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(link_state="connecting", link_target=("10.0.0.9", 8765))
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "connecting to 10.0.0.9:8765" in connection

    asyncio.run(scenario())


def test_refresh_from_shows_handshaking_stage():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(link_state="handshaking", link_target=("10.0.0.9", 8765))
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "handshaking" in connection
            assert "10.0.0.9:8765" in connection

    asyncio.run(scenario())


def test_refresh_from_shows_retrying_stage_with_countdown_attempt_and_error(monkeypatch):
    import milo_brain.tui.dashboard as dashboard_mod

    # A plain @contextlib.contextmanager (sync __enter__/__exit__) can't be
    # combined with `app.run_test()` in one `async with a, b:` statement --
    # that requires every item to be an async context manager. monkeypatch
    # (a normal pytest fixture, patch applied/reverted outside the async
    # block) sidesteps that entirely.
    monkeypatch.setattr(dashboard_mod.time, "monotonic", lambda: 100.0)

    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(
            link_state="retrying", retry_at=104.0, consecutive_drops=3,
            last_error="OSError: [Errno 11001] getaddrinfo failed",
        )
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "retrying in 4s" in connection
            assert "attempt 3" in connection
            assert "getaddrinfo failed" in connection

    asyncio.run(scenario())


def test_refresh_from_shows_progress_while_pipelines_are_loading():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()

        class _FakeFactory:
            def pipeline_status(self):
                return {
                    "asr": ("ready", None),
                    "tts": ("loading", None),
                    "vision": ("not_loaded", None),
                }

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker(), _FakeFactory())
            bar = screen.query_one("#pipelines-bar", ProgressBar)
            detail = str(screen.query_one("#pipelines-detail", TextualStatic).content)
            assert bar.total == 3
            assert bar.progress == 1  # only asr is resolved (ready)
            assert "1/3 ready" in detail
            assert "loading: TTS" in detail
            assert "pending: VISION" in detail

    asyncio.run(scenario())


def test_refresh_from_shows_all_ready_with_no_errors():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()

        class _FakeFactory:
            def pipeline_status(self):
                return {"asr": ("ready", None), "tts": ("ready", None), "vision": ("ready", None)}

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker(), _FakeFactory())
            bar = screen.query_one("#pipelines-bar", ProgressBar)
            detail = str(screen.query_one("#pipelines-detail", TextualStatic).content)
            assert bar.total == 3
            assert bar.progress == 3
            assert detail == "All pipelines ready"

    asyncio.run(scenario())


def test_refresh_from_shows_ready_with_an_error_called_out():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()

        class _FakeFactory:
            def pipeline_status(self):
                return {
                    "asr": ("ready", None),
                    "tts": ("ready", None),
                    "vision": ("error", "no GPU found"),
                }

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker(), _FakeFactory())
            bar = screen.query_one("#pipelines-bar", ProgressBar)
            detail = str(screen.query_one("#pipelines-detail", TextualStatic).content)
            assert bar.total == 3
            assert bar.progress == 3  # errors count as resolved -- bar still completes
            assert "1 error" in detail
            assert "VISION: error — no GPU found" in detail

    asyncio.run(scenario())


def test_refresh_from_shows_ready_with_multiple_errors_pluralized():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()

        class _FakeFactory:
            def pipeline_status(self):
                return {
                    "asr": ("error", "boom"),
                    "tts": ("ready", None),
                    "vision": ("error", "no GPU found"),
                }

        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker(), _FakeFactory())
            bar = screen.query_one("#pipelines-bar", ProgressBar)
            detail = str(screen.query_one("#pipelines-detail", TextualStatic).content)
            assert bar.total == 3
            assert bar.progress == 3
            assert "2 errors" in detail
            assert "ASR: error — boom" in detail
            assert "VISION: error — no GPU found" in detail

    asyncio.run(scenario())


def test_refresh_from_omits_pipelines_when_factory_is_none():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector()
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            detail = str(screen.query_one("#pipelines-detail", TextualStatic).content)
            assert "unavailable" in detail

    asyncio.run(scenario())


def test_refresh_from_shows_manual_disconnect_distinct_from_idle():
    async def scenario():
        cfg = BrainConfig(brain_id="b", name="n", tier="small")
        connector = _FakeConnector(link_state="disconnected", last_connected=("10.0.0.9", 8765))
        app = _HostApp()
        async with app.run_test():
            screen = app.query_one(DashboardScreen)
            screen.refresh_from(connector, cfg, TokenRateTracker())
            connection = str(screen.query_one(ConnectionPanel).content)
            assert "disconnected" in connection
            assert "no robot connected" not in connection

    asyncio.run(scenario())
