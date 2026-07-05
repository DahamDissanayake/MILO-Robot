"""Hardware bring-up: sweep each servo channel 60->120 degrees, one at a time.

Run on the Pi with servos on battery power (Phase A.3):
    python bridge/tools/servo_sweep.py           # all channels in order
    python bridge/tools/servo_sweep.py R3        # a single servo by name

Watch each leg: motion must match the printed name (R1 front-right hip, etc.).
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from milo_bridge.drivers.servos import SERVO_CHANNELS, ServoDriver  # noqa: E402


async def sweep(driver: ServoDriver, name: str) -> None:
    print(f"sweeping {name} (channel {SERVO_CHANNELS[name]}) 60 -> 120 -> 90")
    for angle in (60, 120, 90):
        driver.set_angle(name, angle)
        await asyncio.sleep(0.6)


async def main() -> None:
    driver = ServoDriver.from_hardware()
    names = [sys.argv[1]] if len(sys.argv) > 1 else list(SERVO_CHANNELS)
    for name in names:
        await sweep(driver, name)
    print("sweep complete — verify every leg moved on the correct side")


if __name__ == "__main__":
    asyncio.run(main())
