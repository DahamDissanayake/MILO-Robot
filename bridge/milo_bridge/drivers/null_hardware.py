"""Null-object stand-ins for ServoDriver/SmoothServos and FaceDisplay when
the underlying I2C hardware isn't reachable at boot -- every call is a
silent no-op so GaitEngine/PoseRunner/MotionService/etc. don't need
special-casing, and the rest of the service (including the web dashboard)
stays up with that one peripheral simply absent.
"""

from __future__ import annotations


class NullServos:
    def set_angle(self, servo: str, angle: float) -> None:
        pass

    async def set_pose(self, angles, stagger: bool = True) -> None:
        pass

    def last_angle(self, servo: str) -> float | None:
        return None

    def relax(self) -> None:
        pass

    def hold(self) -> None:
        pass


class NullDisplay:
    current_face: str | None = None

    async def set_face(self, name: str, mode=None, fps: float = 8.0) -> None:
        pass

    async def show_pin(self, pin: str) -> None:
        pass

    async def show_status(self, status: dict[str, bool], seconds: float = 3.0) -> None:
        pass

    def start_idle(self, base_face: str = "idle") -> None:
        pass

    def stop_idle(self) -> None:
        pass
