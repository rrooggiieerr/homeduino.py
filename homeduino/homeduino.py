import asyncio
import logging
import os
import re
import sys
import time
from asyncio.transports import BaseTransport
from collections import deque
from datetime import datetime
from functools import partial
from typing import Any, Final, Optional

import serial_asyncio
from rfcontrol import controller
from serial.serialutil import SerialException
from serial_asyncio import SerialTransport

logger = logging.getLogger(__name__)

DEFAULT_BAUD_RATE: Final = 115200
BAUD_RATES: Final = [57600, DEFAULT_BAUD_RATE]
DEFAULT_RECEIVE_PIN: Final = 2
DEFAULT_SEND_PIN: Final = 4

_RESPONSE_TIMEOUT = 1
_READY_TIMEOUT = 5
_BUSY_TIMEOUT = 1


class HomeduinoError(Exception):
    """Generic Homeduino error."""


class ResponseTimeoutError(HomeduinoError):
    """
    Response timeout error.

    If the response takes to long to receive.
    """


class DisconnectedError(HomeduinoError):
    """Homeduino Disconnected error."""


class NotReadyError(HomeduinoError):
    """Homeduino not ready error."""


class TooBusyError(HomeduinoError):
    """Homeduino Disconnected error."""


class HomeduinoProtocol(asyncio.Protocol):
    rf_receive_callbacks = []

    transport: SerialTransport = None

    ready = False
    _tx_busy_since = None
    _ack = None

    _str_buffer = ""
    str_buffer = deque()

    def __init__(
        self,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        **_kwargs: Any,
    ):
        """Initialize class."""
        if loop:
            self.loop = loop
        else:
            self.loop = asyncio.get_event_loop()

    async def set_receive_interrupt(self, receive_interrupt: int) -> bool:
        if receive_interrupt is not None:
            response = await self.send(f"RF receive {receive_interrupt}")
            return response == "ACK"
        return False

    def add_rf_receive_callback(self, rf_receive_callback) -> None:
        if rf_receive_callback is not None:
            self.rf_receive_callbacks.append(rf_receive_callback)

    def connection_made(self, transport: BaseTransport) -> None:
        self.transport = transport

    def data_received(self, data) -> None:
        try:
            decoded_data = data.decode()
        except UnicodeDecodeError:
            invalid_data = data.decode(errors="replace")
            logger.warning(
                "Error during decode of data, invalid data: %s", invalid_data
            )
        else:
            logger.debug("Received data: %s", decoded_data.strip())
            self._str_buffer += decoded_data
            while "\r\n" in self._str_buffer:
                line, self._str_buffer = self._str_buffer.split("\r\n", 1)
                line = line.strip()
                if line == "ready":
                    self.handle_ready()
                elif line.startswith("RF receive "):
                    self.handle_rf_receive(line)
                elif line.startswith("KP "):
                    self.handle_key_press(line)
                elif line != "" and self.busy():
                    self.str_buffer.append(line)
                elif line != "":
                    logger.error("Unhandled data received '%s'", line)

    def handle_ready(self) -> None:
        self.ready = True
        logger.info("Homeduino is connected")

    def handle_rf_receive(self, line: str) -> None:
        logger.debug(line)

        # The first 8 numbers are the pulse lengths and the last string of numbers is the pulse sequence
        parts = line.split(" ")

        pulse_lengths = [int(i) for i in parts[2:10]]
        # logger.debug("pulse lengths: %s", pulse_lengths)
        pulse_sequence = parts[10]

        # Match pulse sequence to a protocol
        decoded = controller.decode_pulses(pulse_lengths, pulse_sequence)

        if len(decoded) == 0:
            logger.warning("No protocol for %s %s", pulse_lengths, pulse_sequence)
        elif len(self.rf_receive_callbacks) == 0:
            logger.debug("No receive callbacks configured")
        else:
            for protocol in decoded:
                logger.debug("Forwarding RF protocol to receive callbacks")
                for rf_receive_callback in self.rf_receive_callbacks:
                    rf_receive_callback(protocol)

    def handle_key_press(self, line: str) -> None:
        logger.debug(line)
        # Ignoring key presses for now

    async def send(self, packet: str, ignore_ready: bool = False) -> str:
        """Encode and put packet string onto write buffer."""

        if not self.transport:
            raise DisconnectedError("Homeduino is not connected")

        if not ignore_ready and not self.ready:
            logger.error("Not ready")
            raise NotReadyError("Homeduino is not ready")

        while self.busy():
            if (datetime.now() - self._tx_busy_since).total_seconds() > _BUSY_TIMEOUT:
                logger.error("Too busy to send %s", packet)
                raise TooBusyError("Homeduino is too busy to send a command")
            logger.debug("Busy")
            await asyncio.sleep(0.01)
        self._tx_busy_since = datetime.now()

        try:
            data = packet + "\n"
            logger.debug("Writing data: %s", repr(data))
            # type ignore: transport from create_connection is documented to be
            # implementation specific bidirectional, even though typed as
            # BaseTransport
            self.transport.write(data.encode())  # type: ignore

            # Wait for response
            while len(self.str_buffer) == 0:
                if (
                    datetime.now() - self._tx_busy_since
                ).total_seconds() > _RESPONSE_TIMEOUT:
                    logger.error("Timeout while waiting for command response")
                    raise ResponseTimeoutError(
                        "Timeout while waiting for command response"
                    )
                logger.debug("Waiting for command response")
                await asyncio.sleep(0.01)

            response = self.str_buffer.pop()
            logger.debug("Command response received: %s", response)
            return response.strip()
        finally:
            self._tx_busy_since = None

        return None

    def busy(self):
        return self._tx_busy_since is not None

    def connection_lost(self, exc: Optional[Exception]) -> None:
        logger.info("Port closed")
        if exc:
            logger.exception("Disconnected due to exception")
        else:
            logger.info("Disconnected because of close/abort")

        self.transport = None
        self.ready = False


class Homeduino:
    protocol: HomeduinoProtocol = None
    rf_receive_callbacks = []

    def __init__(
        self,
        serial_port: str,
        baud_rate: int = DEFAULT_BAUD_RATE,
        receive_pin: int = DEFAULT_RECEIVE_PIN,
        send_pin: int = DEFAULT_SEND_PIN,
        dht_pin: int = None,
        loop=None,
    ):
        # Test if the device exists
        if not os.path.exists(serial_port):
            logger.warning("No such file or directory: '%s'", serial_port)

        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.receive_interrupt = receive_pin - 2
        self.send_pin = send_pin
        self.dht_pin = dht_pin

        if loop is None:
            loop = asyncio.get_event_loop()
        self.loop = loop

    async def connect(self) -> bool:
        if not self.connected():
            try:
                protocol_factory = partial(HomeduinoProtocol, loop=self.loop)

                (
                    _transport,
                    self.protocol,
                ) = await serial_asyncio.create_serial_connection(
                    self.loop,
                    protocol_factory,
                    self.serial_port,
                    baudrate=self.baud_rate,
                    bytesize=serial_asyncio.serial.EIGHTBITS,
                    parity=serial_asyncio.serial.PARITY_NONE,
                    stopbits=serial_asyncio.serial.STOPBITS_ONE,
                )

                start_time = datetime.now()
                while not self.protocol.ready:
                    if (datetime.now() - start_time).total_seconds() > _READY_TIMEOUT:
                        break
                    logger.debug("Waiting for Homeduino to become ready")
                    await asyncio.sleep(0.01)

                if not self.protocol.ready:
                    logger.error(
                        "Timeout while waiting for Homeduino to become ready, trying to ping instead"
                    )
                    if self._ping(True):
                        self.protocol.handle_ready()
                    else:
                        raise ResponseTimeoutError(
                            "Timeout while waiting for Homeduino to become ready"
                        )

                await self.protocol.set_receive_interrupt(self.receive_interrupt)

                for rf_receive_callback in self.rf_receive_callbacks:
                    self.protocol.add_rf_receive_callback(rf_receive_callback)

                return True
            except SerialException as ex:
                logger.error(ex)

        return False

    def connected(self) -> bool:
        if self.protocol is None or self.protocol.transport is None:
            return False

        return True

    async def disconnect(self) -> bool:
        if self.connected():
            logger.debug("Disconnecting Homeduino")
            self.protocol.transport.close()

            start_time = datetime.now()
            while self.protocol.ready:
                if (datetime.now() - start_time).total_seconds() > _READY_TIMEOUT:
                    logger.error("Timeout while waiting for Homeduino to disconnect")
                    raise ResponseTimeoutError(
                        "Timeout while waiting for Homeduino to disconnect"
                    )
                logger.debug("Waiting for Homeduino to disconnect")
                await asyncio.sleep(0.01)

            self.protocol = None
            logger.debug("Homeduino disconnected")
            return True

        return False

    async def _ping(self, ignore_ready: bool = False) -> bool:
        logger.debug("Pinging Homeduino")
        message = f"PING {time.time()}"
        response = await self.protocol.send(message, ignore_ready)
        if response == message:
            logger.debug("Pinging Homeduino successful")
            return True

        logger.error("Pinging Homeduino failed")
        return False

    async def ping(self) -> bool:
        if not self.connected():
            raise DisconnectedError("Homeduino is not connected")

        if self.protocol.busy():
            return True

        return await self._ping()

    def add_rf_receive_callback(self, rf_receive_callback) -> None:
        self.rf_receive_callbacks.append(rf_receive_callback)
        if self.connected():
            self.protocol.add_rf_receive_callback(rf_receive_callback)

    async def rf_send(self, rf_protocol: str, values) -> bool:
        if not self.connected():
            raise DisconnectedError("Homeduino is not connected")

        if self.send_pin is not None:
            rf_protocol = getattr(sys.modules[controller.__name__], rf_protocol)
            logger.debug(rf_protocol)

            packet = f"RF send {self.send_pin} 3 "

            for pulse_length in rf_protocol.pulse_lengths:
                packet += f"{pulse_length} "

            i = len(rf_protocol.pulse_lengths)
            while i < 8:
                packet += "0 "
                i += 1

            packet += rf_protocol.encode(**values)

            response = await self.protocol.send(packet)
            return response == "ACK"

        return False

    async def send(self, command) -> str:
        if not self.connected():
            raise DisconnectedError("Homeduino is not connected")

        return await self.protocol.send(command)

    @staticmethod
    def get_protocols() -> [str]:
        """Returns the supported protocols in natural sorted order"""

        def convert(text):
            return int(text) if text.isdigit() else text.lower()

        def alphanum_key(key: str):
            return [convert(c) for c in re.split("([0-9]+)", key)]

        protocol_names = [protocol.name for protocol in controller.get_all_protocols()]
        return sorted(protocol_names, key=alphanum_key)
