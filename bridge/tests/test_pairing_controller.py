import asyncio

from milo_bridge.net.pairing import PairingController


class NullAdvertiser:
    busy = False
    pairing = False

    def update(self, **kw):
        for key, value in kw.items():
            if value is not None:
                setattr(self, key, value)


class FakeDisplay:
    def __init__(self):
        self.shown_pins: list[str] = []
        self.idle = False

    async def show_pin(self, pin):
        self.shown_pins.append(pin)

    def stop_idle(self):
        self.idle = False

    def start_idle(self):
        self.idle = True


def test_enter_pairing_mode_shows_pin_and_flips_advertiser_flag():
    advertiser, display = NullAdvertiser(), FakeDisplay()
    ctl = PairingController(advertiser, display)

    pin = asyncio.run(ctl.enter_pairing_mode())

    assert display.shown_pins == [pin]
    assert len(pin) == 4 and pin.isdigit()
    assert advertiser.pairing is True
    assert ctl.current_pin == pin


def test_exit_pairing_mode_clears_pin_and_flag():
    advertiser, display = NullAdvertiser(), FakeDisplay()
    ctl = PairingController(advertiser, display)
    asyncio.run(ctl.enter_pairing_mode())

    asyncio.run(ctl.exit_pairing_mode())

    assert ctl.current_pin is None
    assert advertiser.pairing is False
    assert display.idle is True  # returned to its normal idle face


def test_pin_for_incoming_is_none_while_pairing_mode_is_off():
    advertiser, display = NullAdvertiser(), FakeDisplay()
    ctl = PairingController(advertiser, display)
    assert ctl.pin_for_incoming() is None

    pin = asyncio.run(ctl.enter_pairing_mode())
    assert ctl.pin_for_incoming() == pin

    asyncio.run(ctl.exit_pairing_mode())
    assert ctl.pin_for_incoming() is None


def test_pin_for_incoming_tracks_the_advertiser_flag_not_just_the_stored_pin():
    # If something external flips advertiser.pairing off without going
    # through exit_pairing_mode(), pin_for_incoming() must still gate on
    # the live flag -- never leak a PIN that's no longer supposed to be valid.
    advertiser, display = NullAdvertiser(), FakeDisplay()
    ctl = PairingController(advertiser, display)
    asyncio.run(ctl.enter_pairing_mode())

    advertiser.pairing = False
    assert ctl.pin_for_incoming() is None
