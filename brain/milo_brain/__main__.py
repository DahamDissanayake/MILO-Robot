"""Entry point: ``python -m milo_brain`` (tray UI) or ``--headless``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import threading

from .config import BrainConfig
from .server import BrainServer


async def _headless_request_pin(robot_name: str) -> str | None:
    print(f"\nRobot '{robot_name}' wants to pair. Enter the PIN shown on its face.")
    return await asyncio.to_thread(input, "PIN: ")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="milo-brain")
    parser.add_argument("--headless", action="store_true", help="run without the tray UI")
    parser.add_argument("--pairing", action="store_true", help="start with pairing mode on")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = BrainConfig.load()

    try:  # full cognition pipeline; falls back to the debug handler without it
        from .session import CognitionSessionFactory

        handler = CognitionSessionFactory(cfg).handle
    except ImportError:
        from .server import default_handler as handler

    server = BrainServer(cfg, handler=handler, request_pin=_headless_request_pin)
    if args.pairing:
        server.advertiser.pairing = True

    if args.headless:
        asyncio.run(server.serve_forever())
        return

    try:
        from .ui.tray import run_tray
    except ImportError:
        print("PyQt6 not installed — running headless (pip install PyQt6 for the tray UI)")
        asyncio.run(server.serve_forever())
        return

    loop = asyncio.new_event_loop()
    server_task: dict = {}

    def run_server() -> None:
        asyncio.set_event_loop(loop)
        server_task["task"] = loop.create_task(server.serve_forever())
        loop.run_forever()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    def on_quit() -> None:
        loop.call_soon_threadsafe(loop.stop)

    run_tray(server, on_quit=on_quit, loop=loop)


if __name__ == "__main__":
    main()
