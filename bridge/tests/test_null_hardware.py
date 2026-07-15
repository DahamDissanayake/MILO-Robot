"""Off-hardware tests: NullServos/NullDisplay are safe, silent stand-ins for
when the underlying I2C hardware isn't reachable at boot."""
import asyncio

from milo_bridge.drivers.null_hardware import NullDisplay, NullServos


def test_null_servos_every_call_is_a_safe_no_op():
    servos = NullServos()
    servos.set_angle("R1", 90)
    asyncio.run(servos.set_pose({"R1": 90, "R2": 45}))
    assert servos.last_angle("R1") is None
    servos.relax()
    servos.hold()


def test_null_display_every_call_is_a_safe_no_op():
    display = NullDisplay()
    assert display.current_face is None
    asyncio.run(display.set_face("idle"))
    asyncio.run(display.show_pin("123456"))
    asyncio.run(display.show_status({"servos": True}))
    display.start_idle()
    display.start_idle(base_face="confused")
    display.stop_idle()
