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

import serial_asyncio_fast as serial_asyncio
from rfcontrol import controller
from serial.serialutil import SerialException
from serial_asyncio_fast import SerialTransport

logger = logging.getLogger(__name__)

DEFAULT_BAUD_RATE: Final = 115200
BAUD_RATES: Final = [57600, DEFAULT_BAUD_RATE]
DEFAULT_RECEIVE_PIN: Final = 2
DEFAULT_SEND_PIN: Final = 4

_RESPONSE_TIMEOUT = 2
_READY_TIMEOUT = 5
_BUSY_TIMEOUT = 1
_RF_SEND_DELAY = 0.2
_PING_INTERVAL = 5
_ALLOWED_FAILED_PINGS = 1

background_tasks = set()


def _add_background_task(task: asyncio.Task) -> None:
    # Add task to the set. This creates a strong reference.
    background_tasks.add(task)

    # To prevent keeping references to finished tasks forever, make each task remove its own
    # reference from the set after completion:
    task.add_done_callback(background_tasks.discard)


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
    transport: SerialTransport = None

    ready = False
    _last_rf_send = None

    _str_buffer = ""
    _awaiting_response = False

    def __init__(
        self,
        **_kwargs: Any,
    ):
        """Initialize class."""
        self.rf_receive_callbacks = []
        self.response_buffer = deque()
        self._send_lock = asyncio.Lock()

    def busy(self):
        return self._send_lock.locked()

    async def set_rf_receive_interrupt(self, rf_receive_interrupt: int) -> bool:
        if rf_receive_interrupt is not None:
            response = await self.send(f"RF receive {rf_receive_interrupt}")
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
                elif line != "" and self._awaiting_response:
                    self.response_buffer.append(line)
                elif line.startswith("PING "):
                    logger.warning("Unhandled data received '%s'", line)
                elif line != "":
                    logger.error("Unhandled data received '%s'", line)

    def handle_ready(self) -> None:
        self.ready = True
        logger.info("Homeduino is connected")

    def handle_rf_receive(self, line: str) -> None:
        logger.debug(line)

        # The first 8 numbers are the pulse lengths and the last string of numbers is the pulse
        # sequence.
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

    async def send(self, packet: str) -> str:
        """Encode and put packet string onto write buffer."""

        if not self.transport:
            raise DisconnectedError("Homeduino is not connected")

        is_rf_send = packet.startswith("RF send ")
        if is_rf_send and self._last_rf_send is not None:
            # Allow some time between rf send commands to prevent flooding
            while (
                datetime.now() - self._last_rf_send
            ).total_seconds() <= _RF_SEND_DELAY:
                logger.debug("RF send delay")
                await asyncio.sleep(0.01)

        try:
            data = packet + "\n"
            logger.debug("Writing data: %s", repr(data))
            # type ignore: transport from create_connection is documented to be
            # implementation specific bidirectional, even though typed as
            # BaseTransport
            async with self._send_lock:
                self._awaiting_response = True
                self.transport.write(data.encode())  # type: ignore

                timeout = time.time() + _RESPONSE_TIMEOUT
                while len(self.response_buffer) == 0:
                    if time.time() > timeout:
                        logger.error("Timeout while waiting for command response")
                        raise ResponseTimeoutError(
                            "Timeout while waiting for command response"
                        )
                    logger.debug("Waiting for command response")
                    await asyncio.sleep(0.1)

                self._awaiting_response = False

            response = self.response_buffer.pop()
            logger.debug("Command response received: %s", response)
            return response.strip()
        finally:
            if is_rf_send:
                # Set last RF send timestamp
                self._last_rf_send = datetime.now()

        return None

    def connection_lost(self, exc: Optional[Exception]) -> None:
        logger.info("Port closed")
        if exc:
            logger.error("Disconnected due to exception: %s", exc)
        else:
            logger.info("Disconnected because of close/abort")

        self.transport = None
        self.ready = False


class Homeduino:
    rf_receive_interrupt: int | None = None

    protocol: HomeduinoProtocol = None

    _ping_task = None
    _loop = None

    def __init__(
        self,
        serial_port: str,
        baud_rate: int = DEFAULT_BAUD_RATE,
        rf_receive_pin: int | None = DEFAULT_RECEIVE_PIN,
        rf_send_pin: int | None = DEFAULT_SEND_PIN,
    ):
        # Test if the device exists
        if not os.path.exists(serial_port):
            logger.warning("No such file or directory: '%s'", serial_port)

        self.serial_port = serial_port
        self.baud_rate = baud_rate
        if rf_receive_pin is not None:
            self.rf_receive_interrupt = rf_receive_pin - 2
        self.rf_send_pin = rf_send_pin

        self.rf_receive_callbacks = []

    async def _connect(self) -> bool:
        if not self.connected():
            if self._loop is None:
                self._loop = asyncio.get_event_loop()

            logger.info("Connecting to %s", self.serial_port)
            try:
                protocol_factory = partial(HomeduinoProtocol)

                (
                    _transport,
                    self.protocol,
                ) = await serial_asyncio.create_serial_connection(
                    self._loop,
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
                    await asyncio.sleep(0.1)

                if not self.protocol.ready:
                    logger.warning(
                        "Timeout while waiting for Homeduino to become ready, trying to ping instead"
                    )
                    if await self._ping(True):
                        self.protocol.handle_ready()
                    else:
                        raise ResponseTimeoutError(
                            "Timeout while waiting for Homeduino to become ready"
                        )

                if self.rf_receive_interrupt is not None:
                    await self.protocol.set_rf_receive_interrupt(
                        self.rf_receive_interrupt
                    )

                for rf_receive_callback in self.rf_receive_callbacks:
                    self.protocol.add_rf_receive_callback(rf_receive_callback)

                return True
            except SerialException as ex:
                logger.error(ex)

        return False

    async def connect(self, loop=None, ping_interval=_PING_INTERVAL) -> bool:
        self._loop = loop

        if not self.connected() and await self._connect():
            if ping_interval > 0:
                self._ping_task = asyncio.create_task(
                    self._ping_coroutine(ping_interval)
                )
                _add_background_task(self._ping_task)

            return True

        return False

    def connected(self) -> bool:
        if self.protocol is None or self.protocol.transport is None:
            return False

        return True

    async def _disconnect(self) -> bool:
        if self.protocol is not None:
            logger.debug("Disconnecting Homeduino")
            if self.protocol.transport is not None:
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

    async def disconnect(self) -> bool:
        await self._cancel_ping()
        return await self._disconnect()

    async def _reconnect(self) -> bool:
        await self._disconnect()
        return await self._connect()

    async def _ping(self) -> bool:
        logger.debug("Pinging Homeduino")
        message = f"PING {time.time()}"

        response = await self.protocol.send(message)
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

        if self.rf_send_pin is not None:
            rf_protocol = getattr(sys.modules[controller.__name__], rf_protocol)
            logger.debug(rf_protocol)

            packet = f"RF send {self.rf_send_pin} 3 "

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

    async def _cancel_ping(self) -> bool:
        if self._ping_task is not None and not (
            self._ping_task.done() or self._ping_task.cancelled()
        ):
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                logger.debug("Ping task was cancelled")
                self._ping_task = None

        if self._ping_task is not None:
            logger.error("Failed to cancel ping task")
            logger.debug("Ping task: %s", self._ping_task)
            return False

        return True

    async def _ping_coroutine(self, ping_interval: int):
        """
        To test the connection for availability a ping message can be sent
        30 seconds if no other messages where sent during the last 30 seconds.
        """
        failed_pings = 0
        while True:
            try:
                if not self.connected():
                    await self._reconnect()

                if self.connected():
                    await self.ping()
                    failed_pings = 0

                failed_pings += 1
                await asyncio.sleep(ping_interval)
            except ResponseTimeoutError:
                failed_pings += 1
                if failed_pings > _ALLOWED_FAILED_PINGS:
                    logger.error("Unable to ping Homeduino")
            except asyncio.CancelledError:
                logger.debug("Ping coroutine was canceled")
                break

        self._ping_task = None
        logger.debug("Ping coroutine stopped")
