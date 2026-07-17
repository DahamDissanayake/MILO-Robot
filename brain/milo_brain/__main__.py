"""Entry point: ``python -m milo_brain`` (TUI) or ``--headless``."""

from __future__ import annotations

import argparse
import asyncio
import logging

from .config import BrainConfig
from .llm.token_rate import TokenRateTracker
from .server import BrainServer, RobotHandler


async def _headless_request_pin(robot_name: str) -> str | None:
    print(f"\nRobot '{robot_name}' wants to pair. Enter the PIN shown on its face.")
    return await asyncio.to_thread(input, "PIN: ")


def _build_handler(cfg: BrainConfig, rate_tracker: TokenRateTracker) -> RobotHandler:
    try:  # full cognition pipeline; falls back to the debug handler without it
        from .session import CognitionSessionFactory

        return CognitionSessionFactory(cfg, rate_tracker=rate_tracker).handle
    except ImportError:
        from .server import default_handler

        return default_handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="milo-brain")
    parser.add_argument("--headless", action="store_true", help="run without the TUI")
    parser.add_argument("--pairing", action="store_true", help="start with pairing mode on")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = BrainConfig.load()
    rate_tracker = TokenRateTracker()
    handler = _build_handler(cfg, rate_tracker)

    server = BrainServer(cfg, handler=handler, request_pin=_headless_request_pin)
    if args.pairing:
        server.advertiser.pairing = True

    if args.headless:
        asyncio.run(server.serve_forever())
        return

    from .tui.app import MiloBrainApp

    MiloBrainApp(server, cfg, rate_tracker).run()


if __name__ == "__main__":
    main()
