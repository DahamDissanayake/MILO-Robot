"""Idle/standby controller.

Default state whenever no brain/web client is connected: stand at standby
with self-leveling engaged, not asleep -- the robot stays visibly ready
and waiting rather than crouching down and going limp. The deeper
"asleep" state (rest pose, limp servos, sleepy face) and its loud-sound
perk-up are still available via ensure_asleep()/handle_audio_level() for
whatever wires them up (e.g. a future idle timeout); nothing currently
triggers ensure_asleep() automatically.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from .drivers.display import AnimMode

PERK_SECONDS = 3.0


class SleepController:
    def __init__(
        self,
        runner,
        display,
        loud_rms_threshold: float = 2000.0,
        on_perk: Callable[[], None] | None = None,
        servos=None,
        gait=None,
    ):
        self._runner = runner
        self._display = display
        self._servos = servos
        self._gait = gait
        self._threshold = loud_rms_threshold
        self._on_perk = on_perk
        self.asleep = False
        self.standing_by = False
        self._perk_task: asyncio.Task | None = None

    async def ensure_standby(self) -> None:
        """Default idle state whenever no brain/web client is connected:
        stand and stay engaged (self-leveling), not asleep/limp. Replaces
        the old auto-sleep-on-disconnect behavior. Idempotent-guarded like
        ensure_asleep()/ensure_awake() so a handler firing concurrently
        with an in-flight one is harmless."""
        if self.standing_by:
            return
        self.standing_by = True
        self.asleep = False
        self._cancel_perk()
        if self._gait is not None:
            self._gait.set_suspended(False)
        self._runner.abort()
        self._display.stop_idle()
        await self._runner.run("stand")
        self._display.start_idle()

    async def ensure_asleep(self) -> None:
        if self.asleep:
            return
        self.asleep = True
        self.standing_by = False
        self._runner.abort()
        self._display.stop_idle()
        await self._runner.run("rest")
        await self._display.set_face("sleepy", AnimMode.BOOMERANG)
        if self._gait is not None:
            # Must happen before relax() -- otherwise the next gait tick's
            # hold-level self-leveling re-engages the servos we're about to
            # go limp, immediately undoing the power saving.
            self._gait.set_suspended(True)
        if self._servos is not None:
            self._servos.relax()  # limp servos save battery while asleep

    async def ensure_awake(self) -> None:
        self.standing_by = False  # a controller taking over ends any standby hold
        if not self.asleep:
            return
        self.asleep = False
        self._cancel_perk()
        if self._gait is not None:
            self._gait.set_suspended(False)
        await self._runner.run("stand")
        await self._display.set_face("excited", AnimMode.ONCE)
        self._display.start_idle()

    def handle_audio_level(self, rms: float) -> None:
        """Feed mic RMS here; loud sounds while asleep trigger a perk-up."""
        if not self.asleep or rms < self._threshold:
            return
        if self._perk_task is None or self._perk_task.done():
            self._perk_task = asyncio.create_task(self._perk())

    async def _perk(self) -> None:
        await self._display.set_face("surprised", AnimMode.ONCE)
        if self._on_perk is not None:
            self._on_perk()  # e.g. trigger an immediate discovery rescan
        await asyncio.sleep(PERK_SECONDS)
        if self.asleep:
            await self._display.set_face("sleepy", AnimMode.BOOMERANG)

    def _cancel_perk(self) -> None:
        if self._perk_task is not None:
            self._perk_task.cancel()
            self._perk_task = None
