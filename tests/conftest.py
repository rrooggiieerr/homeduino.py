"""Shared fixtures for the Homeduino tests."""

from collections.abc import AsyncIterator
import json
from pathlib import Path

import pytest

from homeduino import Homeduino

_SETTINGS_JSON = Path(__file__).parent / "settings.json"


@pytest.fixture
def serial_port() -> str:
    """Serial port of the test Homeduino, as configured in settings.json.

    Skips the test if the serial port doesn't exist, e.g. when no Homeduino is
    connected.
    """
    with _SETTINGS_JSON.open(encoding="utf8") as settings_file:
        serial_port: str = json.load(settings_file)["serial_port"]
    if serial_port.startswith("/dev/") and not Path(serial_port).exists():
        pytest.skip(f"No Homeduino connected to {serial_port}")
    return serial_port


@pytest.fixture
async def homeduino(serial_port: str) -> AsyncIterator[Homeduino]:
    """Homeduino that is connected for the duration of the test."""
    homeduino = Homeduino(serial_port)
    assert await homeduino.connect()
    yield homeduino
    await homeduino.disconnect()
