"""Tests for the serial protocol, these don't need a connected Homeduino."""

import asyncio
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import Mock

import pytest
from rfcontrol import controller
import serialx
from serialx import BaseSerialTransport

from homeduino import (
    Homeduino,
    HomeduinoResponseTimeoutError,
    homeduino as homeduino_module,
)
from homeduino.homeduino import HomeduinoProtocol

_SWITCH1_PULSE_LENGTHS = "268 1282 2632 10168 0 0 0 0"
_SWITCH1_PULSE_SEQUENCE = (
    "02000100010001000100010001000100010001010001000001000100010001000100010100"
    "0100010000010001010001000001010000010100000101000001000103"
)
_SWITCH1_VALUES = {"id": 98765, "unit": 4, "all": False, "state": True}


class FakeTransport:
    """Transport that answers every written command with a fixed response."""

    def __init__(self, protocol: HomeduinoProtocol, response: str = "ACK") -> None:
        self.protocol = protocol
        self.response = response
        self.written: list[str] = []

    def write(self, data: bytes) -> None:
        self.written.append(data.decode())
        asyncio.get_running_loop().call_soon(
            self.protocol.data_received, f"{self.response}\r\n".encode()
        )

    def close(self) -> None:
        self.protocol.connection_lost(None)


def connect(protocol: HomeduinoProtocol, response: str = "ACK") -> FakeTransport:
    transport = FakeTransport(protocol, response)
    protocol.connection_made(cast(BaseSerialTransport, transport))
    return transport


def test_init() -> None:
    protocol = HomeduinoProtocol()
    assert isinstance(protocol, HomeduinoProtocol)


def test_ready() -> None:
    protocol = HomeduinoProtocol()
    protocol.data_received(b"ready\r\n")
    assert protocol.ready
    assert protocol.last_message_received is not None


def test_rf_receive() -> None:
    protocol = HomeduinoProtocol()
    received: list[dict[str, Any]] = []
    protocol.add_rf_receive_callback(received.append)

    # A line can be split over several chunks of data.
    line = f"RF receive {_SWITCH1_PULSE_LENGTHS} {_SWITCH1_PULSE_SEQUENCE}\r\n"
    protocol.data_received(line[:20].encode())
    assert received == []
    protocol.data_received(line[20:].encode())

    assert {"protocol": "switch1", "values": _SWITCH1_VALUES} in received


def test_unknown_rf_receive() -> None:
    protocol = HomeduinoProtocol()
    received: list[dict[str, Any]] = []
    protocol.add_rf_receive_callback(received.append)

    protocol.data_received(b"RF receive 100 200 0 0 0 0 0 0 0101\r\n")
    assert received == []


async def test_send() -> None:
    protocol = HomeduinoProtocol()
    transport = connect(protocol, "PING test")

    assert await protocol.send("PING test") == "PING test"
    assert transport.written == ["PING test\n"]


class SilentTransport:
    """Transport that never answers."""

    def write(self, data: bytes) -> None:
        pass


async def test_send_after_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(homeduino_module, "_RESPONSE_TIMEOUT", 0.05)
    protocol = HomeduinoProtocol()
    protocol.connection_made(cast(BaseSerialTransport, SilentTransport()))

    with pytest.raises(HomeduinoResponseTimeoutError):
        await protocol.send("PING 1")

    # A response that arrives too late isn't returned for the next command.
    protocol.data_received(b"PING 1\r\n")
    connect(protocol, "PING 2")
    assert await protocol.send("PING 2") == "PING 2"


async def test_send_disconnected() -> None:
    protocol = HomeduinoProtocol()
    with pytest.raises(Exception, match="not connected"):
        await protocol.send("PING test")


async def test_rf_send() -> None:
    homeduino = Homeduino("/dev/null")
    homeduino.protocol = HomeduinoProtocol()
    transport = connect(homeduino.protocol)

    assert await homeduino.rf_send("switch1", _SWITCH1_VALUES, 3)
    assert transport.written == [
        f"RF send 4 3 {_SWITCH1_PULSE_LENGTHS} {_SWITCH1_PULSE_SEQUENCE}\n"
    ]


async def test_rf_send_raw_protocol() -> None:
    if getattr(controller, "rawswitch", None) is None:
        pytest.skip("rfcontrolpy doesn't have the rawswitch protocol")

    homeduino = Homeduino("/dev/null")
    homeduino.protocol = HomeduinoProtocol()
    transport = connect(homeduino.protocol)

    # The pulse lengths of the raw protocols are set by encode().
    values = {
        "pulse_lengths": [300, 900, 9000],
        "pulses_on": "0101010102",
        "pulses_off": "0110101002",
        "state": True,
    }
    assert await homeduino.rf_send("rawswitch", values, 3)
    assert transport.written == ["RF send 4 3 300 900 9000 0 0 0 0 0 0101010102\n"]


async def test_analog_write() -> None:
    homeduino = Homeduino("/dev/null")
    homeduino.protocol = HomeduinoProtocol()
    transport = connect(homeduino.protocol)

    assert await homeduino.analog_write(5, 128)
    assert transport.written == ["AW 5 128\n"]


async def test_dht_read() -> None:
    homeduino = Homeduino("/dev/null")
    homeduino.protocol = HomeduinoProtocol()
    connect(homeduino.protocol, "ACK 21.5 45.0")

    assert await homeduino.dht_read(22, 5) == (21.5, 45.0)


def test_get_protocols() -> None:
    protocols = Homeduino.get_protocols()
    assert "switch1" in protocols
    assert protocols.index("switch2") < protocols.index("switch10")


async def test_connect_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(homeduino_module, "_READY_TIMEOUT", 0.05)
    monkeypatch.setattr(homeduino_module, "_RESPONSE_TIMEOUT", 0.05)

    # A serial connection to a device that never answers.
    protocols: list[HomeduinoProtocol] = []
    transport = Mock()
    transport.close.side_effect = lambda: protocols[0].connection_lost(None)

    async def create_serial_connection(
        _loop: asyncio.AbstractEventLoop,
        protocol_factory: Callable[[], HomeduinoProtocol],
        **_kwargs: Any,
    ) -> tuple[Mock, HomeduinoProtocol]:
        protocol = protocol_factory()
        protocol.connection_made(transport)
        protocols.append(protocol)
        return transport, protocol

    monkeypatch.setattr(serialx, "create_serial_connection", create_serial_connection)

    homeduino = Homeduino("/dev/fake")
    with pytest.raises(HomeduinoResponseTimeoutError):
        await homeduino.connect(ping_interval=0)
    # The serial port is closed again.
    transport.close.assert_called_once()
    assert homeduino.protocol is None
