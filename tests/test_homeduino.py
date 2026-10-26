"""Tests for connecting to a Homeduino."""

from homeduino import Homeduino


async def test_connect(serial_port: str) -> None:
    homeduino = Homeduino(serial_port)
    assert await homeduino.connect()
    await homeduino.disconnect()


async def test_connect_non_existing_tty() -> None:
    homeduino = Homeduino("/dev/tty.nonexisting")
    assert not await homeduino.connect()
