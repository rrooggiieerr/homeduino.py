"""Tests for the commands, these need a connected Homeduino."""

from homeduino import Homeduino

_SWITCH1_PULSES = (
    "268 1282 2632 10168 0 0 0 0 "
    "02000100010001000100010001000100010001010001000001000100010001000100010100"
    "0100010000010001010001000001010000010100000101000001000103"
)


async def test_ping(homeduino: Homeduino) -> None:
    assert await homeduino.ping()


async def test_send(homeduino: Homeduino) -> None:
    assert await homeduino.send("PING test") == "PING test"


async def test_rf_send(homeduino: Homeduino) -> None:
    assert await homeduino.rf_send(
        "switch1", {"id": 98765, "unit": 4, "all": False, "state": True}
    )


async def test_rf_send_3repeats(homeduino: Homeduino) -> None:
    assert await homeduino.rf_send(
        "switch1", {"id": 98765, "unit": 4, "all": False, "state": True}, 3
    )


async def test_raw_rf_send(homeduino: Homeduino) -> None:
    assert await homeduino.raw_rf_send(_SWITCH1_PULSES)


async def test_raw_rf_send_3repeats(homeduino: Homeduino) -> None:
    assert await homeduino.raw_rf_send(_SWITCH1_PULSES, 3)
