"""Decouples PIN generation/display from the handshake itself.

The PIN is shown on the OLED the instant pairing mode is entered (from the
webapp's "Enter Pairing Mode" button) -- *before* any brain has connected --
rather than reactively mid-handshake the way the old brain-discovers-robot
flow generated it. ``RobotServer._on_connection`` reads
``pin_for_incoming()`` and passes it straight into
``robot_handshake(..., pending_pin=...)``.
"""

from __future__ import annotations

import asyncio

from milo_common import auth


class PairingController:
    def __init__(self, advertiser, display):
        self._advertiser = advertiser
        self._display = display
        self.current_pin: str | None = None

    async def enter_pairing_mode(self) -> str:
        """Generates+shows the PIN immediately and flips the mDNS pairing
        flag. Stays on until exit_pairing_mode() or a brain successfully
        connects while it's active (see RobotServer._on_connection).
        Returns the PIN -- callers must never forward it anywhere the
        webapp/network can observe it."""
        pin = auth.generate_pin()
        self.current_pin = pin
        await self._display.show_pin(pin)
        # Advertiser.update() is zeroconf's synchronous API -- calling it
        # directly here (this coroutine's own event loop thread, same as
        # RobotServer.serve_forever()) would deadlock exactly like the bug
        # documented on brain/milo_brain/server.py's Advertiser.start/stop
        # and MiloBrainApp.action_toggle_pairing. Same fix: hop to a worker
        # thread.
        await asyncio.to_thread(self._advertiser.update, pairing=True)
        return pin

    async def exit_pairing_mode(self) -> None:
        self.current_pin = None
        await asyncio.to_thread(self._advertiser.update, pairing=False)
        self._display.stop_idle()
        self._display.start_idle()

    def pin_for_incoming(self) -> str | None:
        """What RobotServer._on_connection passes as robot_handshake's
        pending_pin -- None whenever pairing mode is off, so an unpaired
        brain connecting outside pairing mode is refused exactly as
        before."""
        return self.current_pin if self._advertiser.pairing else None
