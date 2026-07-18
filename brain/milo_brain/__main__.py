"""Entry point: ``python -m milo_brain`` (TUI) or ``--headless``."""

from __future__ import annotations

import argparse
import asyncio
import logging

from .config import BrainConfig
from .llm.token_rate import TokenRateTracker
from .logbuf import RingBufferLogHandler
from .net.connector import RobotConnectorManager, RobotHandler


async def _headless_request_pin(robot_name: str) -> str | None:
    print(f"\nRobot '{robot_name}' wants to pair. Enter the PIN shown on its face.")
    return await asyncio.to_thread(input, "PIN: ")


def _build_handler(cfg: BrainConfig, rate_tracker: TokenRateTracker):
    """Returns (factory_or_none, handler). factory is None in the
    ImportError fallback (full pipeline deps not installed) -- the
    dashboard's pipeline-status panel is omitted in that case."""
    try:  # full cognition pipeline; falls back to the debug handler without it
        from .session import CognitionSessionFactory

        factory = CognitionSessionFactory(cfg, rate_tracker=rate_tracker)
        return factory, factory.handle
    except ImportError:
        from .net.connector import default_handler

        return None, default_handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="milo-brain")
    parser.add_argument("--headless", action="store_true", help="run without the TUI")
    args = parser.parse_args(argv)

    # The ring buffer is what powers the TUI's Logs screen ("l" key) --
    # background task errors (a failed handshake, a dropped connection,
    # zeroconf noise) would otherwise be invisible once Textual has taken
    # over the terminal, since a plain StreamHandler writing to stderr
    # corrupts/vanishes into its alternate screen buffer instead of
    # appearing anywhere the user can read. --headless has no TUI to view
    # it in, so it also gets a normal stderr handler for a plain terminal.
    log_buffer = RingBufferLogHandler()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(log_buffer)
    if args.headless:
        stderr_handler = logging.StreamHandler()
        stderr_handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
        root_logger.addHandler(stderr_handler)

    cfg = BrainConfig.load()
    rate_tracker = TokenRateTracker()
    factory, session_handler = _build_handler(cfg, rate_tracker)

    connector = RobotConnectorManager(cfg, request_pin=_headless_request_pin, session_handler=session_handler)

    if args.headless:
        asyncio.run(connector.run_forever())
        return

    from .tui.app import MiloBrainApp

    MiloBrainApp(connector, cfg, rate_tracker, log_buffer, factory).run()


if __name__ == "__main__":
    main()
