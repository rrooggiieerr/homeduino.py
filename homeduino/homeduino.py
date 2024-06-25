import asyncio
import logging
import os
import re
import sys
import time
from asyncio.transports import BaseTransport
from collections import deque
from datetime import datetime, timedelta
from enum import IntEnum
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
_DHT_READ_DELAY = timedelta(seconds=2)

background_tasks = set()


def _add_background_task(task: asyncio.Task) -> None:
    # Add task to the set. This creates a strong reference.
    background_tasks.add(task)

    # To prevent keeping references to finished tasks forever, make each task remove its own
    # reference from the set after completion:
    task.add_done_callback(background_tasks.discard)


class HomeduinoError(Exception):
    """Generic Homeduino error."""


class HomeduinoResponseTimeoutError(HomeduinoError):
    """
    Response timeout error.

    If the response takes to long to receive.
    """


class HomeduinoDisconnectedError(HomeduinoError):
    """Homeduino Disconnected error."""


class HomeduinoNotReadyError(HomeduinoError):
    """Homeduino not ready error."""


class HomeduinoTooBusyError(HomeduinoError):
    """Homeduino Disconnected error."""


class HomeduinoProtocol(asyncio.Protocol):
    transport: SerialTransport = None

    ready = False
    _last_rf_send = None
    last_message_received = None

    _str_buffer = ""
    _awaiting_response = False

    def __init__(
        self,
        **_kwargs: Any,
    ):
        """Initialize class."""
        self._rf_receive_callbacks = []
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
            self._rf_receive_callbacks.append(rf_receive_callback)

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
                self.last_message_received = datetime.now()

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
        elif len(self._rf_receive_callbacks) == 0:
            logger.debug("No receive callbacks configured")
        else:
            for protocol in decoded:
                logger.debug("Forwarding RF protocol to receive callbacks")
                for rf_receive_callback in self._rf_receive_callbacks:
                    rf_receive_callback(protocol)

    def handle_key_press(self, line: str) -> None:
        logger.debug(line)
        # Ignoring key presses for now

    async def send(self, packet: str) -> str:
        """Encode and put packet string onto write buffer."""

        if not self.transport:
            raise HomeduinoDisconnectedError("Homeduino is not connected")

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
            # type ignore: transport from create_connection is documented to be
            # implementation specific bidirectional, even though typed as
            # BaseTransport
            async with self._send_lock:
                self._awaiting_response = True
                logger.debug("Writing data: %s", repr(data))
                self.transport.write(data.encode())  # type: ignore

                timeout = time.time() + _RESPONSE_TIMEOUT
                while len(self.response_buffer) == 0:
                    if time.time() > timeout:
                        logger.error("Timeout while waiting for command response")
                        raise HomeduinoResponseTimeoutError(
                            "Timeout while waiting for command response"
                        )
                    logger.debug("Waiting for command response")
                    await asyncio.sleep(0.01)

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


class HomeduinoPinMode(IntEnum):
    INPUT = 0x00
    OUTPUT = 0x01
    INPUT_PULLUP = 0x02


class Homeduino:
    rf_receive_interrupt: int | None = None

    protocol: HomeduinoProtocol = None

    _ping_and_read_task = None
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

        self._rf_receive_callbacks = []
        self._digital_read_callbacks = {}
        self._analog_read_callbacks = {}
        self._dht_read_callbacks = {}

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
                    if await self._ping():
                        self.protocol.handle_ready()
                    else:
                        raise HomeduinoResponseTimeoutError(
                            "Timeout while waiting for Homeduino to become ready"
                        )

                if self.rf_receive_interrupt is not None:
                    await self.protocol.set_rf_receive_interrupt(
                        self.rf_receive_interrupt
                    )

                for rf_receive_callback in self._rf_receive_callbacks:
                    self.protocol.add_rf_receive_callback(rf_receive_callback)

                return True
            except SerialException as ex:
                logger.error(ex)

        return False

    async def connect(self, loop=None, ping_interval=_PING_INTERVAL) -> bool:
        self._loop = loop

        if not self.connected() and await self._connect():
            if ping_interval > 0:
                self._ping_and_read_task = asyncio.create_task(
                    self._ping_and_read_coroutine(timedelta(seconds=ping_interval))
                )
                _add_background_task(self._ping_and_read_task)

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
                    raise HomeduinoResponseTimeoutError(
                        "Timeout while waiting for Homeduino to disconnect"
                    )
                logger.debug("Waiting for Homeduino to disconnect")
                await asyncio.sleep(0.01)

            self.protocol = None
            logger.debug("Homeduino disconnected")
            return True

        return False

    async def disconnect(self) -> bool:
        await self._cancel_ping_and_read()
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
            raise HomeduinoDisconnectedError("Homeduino is not connected")

        if self.protocol.busy():
            return True

        return await self._ping()

    def add_rf_receive_callback(self, rf_receive_callback) -> None:
        self._rf_receive_callbacks.append(rf_receive_callback)
        if self.connected():
            self.protocol.add_rf_receive_callback(rf_receive_callback)

    async def add_digital_read_callback(
        self, digital_io: int, digital_read_callback
    ) -> None:
        if self.connected():
            await self.pin_mode(digital_io, HomeduinoPinMode.INPUT_PULLUP)
        if digital_io in self._digital_read_callbacks:
            self._digital_read_callbacks[digital_io].append(digital_read_callback)
        else:
            self._digital_read_callbacks[digital_io] = [digital_read_callback]

    def add_analog_read_callback(self, analog_input: int, analog_read_callback) -> None:
        if analog_input in self._analog_read_callbacks:
            self._analog_read_callbacks[analog_input].append(analog_read_callback)
        else:
            self._analog_read_callbacks[analog_input] = [analog_read_callback]

    async def add_dht_read_callback(
        self, dht_type: int, digital_io: int, dht_read_callback
    ):
        if self.connected():
            await self.pin_mode(digital_io, HomeduinoPinMode.INPUT)
        if digital_io in self._dht_read_callbacks:
            self._dht_read_callbacks[digital_io][1].append(dht_read_callback)
        else:
            self._dht_read_callbacks[digital_io] = [dht_type, [dht_read_callback]]

    async def rf_send(self, rf_protocol: str, values) -> bool:
        if not self.connected():
            raise HomeduinoDisconnectedError("Homeduino is not connected")

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
            raise HomeduinoDisconnectedError("Homeduino is not connected")

        return await self.protocol.send(command)

    async def pin_mode(self, digital_io: int, mode: HomeduinoPinMode):
        response = await self.send(f"PM {digital_io} {mode}")
        success = response == "ACK"

        return success

    async def digital_write(self, digital_io: int, value: bool):
        response = await self.send(f"DW {digital_io} {1 if value else 0}")
        return response == "ACK"

    async def digital_read(self, digital_io: int) -> bool:
        response = await self.send(f"DR {digital_io}")
        return response == "ACK 1"

    async def analog_write(self, digital_io: int, value: int):
        if digital_io not in (3, 5, 6, 9, 10, 11):
            return False

        response = await self.send(f"DW {digital_io} {value}")
        return response == "ACK"

    async def analog_read(self, analog_input: int) -> int:
        response = await self.send(f"AR {analog_input}")
        response = response.split(" ")[1]
        return int(response)

    async def dht_read(self, dht_type: int, digital_io: int) -> int:
        response = await self.send(f"DHT {dht_type} {digital_io}")
        response = response.split(" ")
        try:
            temperature = float(response[1])
            humidity = float(response[2])
            return (temperature, humidity)
        except ValueError:
            pass

        return (None, None)

    @staticmethod
    def get_protocols() -> [str]:
        """Returns the supported protocols in natural sorted order"""

        def convert(text):
            return int(text) if text.isdigit() else text.lower()

        def alphanum_key(key: str):
            return [convert(c) for c in re.split("([0-9]+)", key)]

        protocol_names = [protocol.name for protocol in controller.get_all_protocols()]
        return sorted(protocol_names, key=alphanum_key)

    async def _cancel_ping_and_read(self) -> bool:
        if self._ping_and_read_task is not None and not (
            self._ping_and_read_task.done() or self._ping_and_read_task.cancelled()
        ):
            self._ping_and_read_task.cancel()
            try:
                await self._ping_and_read_task
            except asyncio.CancelledError:
                logger.debug("Ping and read task was cancelled")
                self._ping_and_read_task = None

        if self._ping_and_read_task is not None:
            logger.error("Failed to cancel ping and read task")
            logger.debug("Ping and read task: %s", self._ping_and_read_task)
            return False

        return True

    async def _ping_and_read_coroutine(self, ping_interval: timedelta):
        """
        To test the connection for availability a ping message can be sent every interval if no
        other messages where received during the interval.
        """
        failed_pings = 0
        digital_io_values = [None] * 14
        analog_input_values = [None] * 8
        dht_values = [(None, None)] * 14
        last_dht_read = None
        sleep_time = 0.1
        while True:
            try:
                if not self.connected():
                    await self._reconnect()

                if self.connected():
                    for (
                        digital_io,
                        digital_read_callbacks,
                    ) in self._digital_read_callbacks.copy().items():
                        sleep_time = 0.01
                        if not self.protocol.busy():
                            value = await self.digital_read(digital_io)
                            previous_value = digital_io_values[digital_io]
                            if value != previous_value:
                                logger.debug("Digital %i: %s", digital_io, value)
                                for digital_read_callback in digital_read_callbacks:
                                    digital_read_callback(value)
                                digital_io_values[digital_io] = value

                    for (
                        analog_input,
                        analog_read_callbacks,
                    ) in self._analog_read_callbacks.copy().items():
                        sleep_time = 0.01
                        if not self.protocol.busy():
                            value = await self.analog_read(analog_input)
                            previous_value = analog_input_values[analog_input]
                            if value != previous_value:
                                logger.debug("Analog %i: %s", analog_input, value)
                                for analog_read_callback in analog_read_callbacks:
                                    analog_read_callback(value)
                                analog_input_values[analog_input] = value

                    if (
                        last_dht_read is None
                        or datetime.now() > last_dht_read + _DHT_READ_DELAY
                    ):
                        for (
                            digital_io,
                            (dht_type, dht_read_callbacks),
                        ) in self._dht_read_callbacks.copy().items():
                            if not self.protocol.busy():
                                (temperature, humidity) = await self.dht_read(
                                    dht_type, digital_io
                                )
                                (previous_temperature, previous_humidity) = dht_values[
                                    digital_io
                                ]
                                if temperature is not None and (
                                    temperature != previous_temperature
                                    or humidity != previous_humidity
                                ):
                                    logger.debug(
                                        "DHT %i: %s°, %s%%",
                                        digital_io,
                                        temperature,
                                        humidity,
                                    )
                                    for dht_read_callback in dht_read_callbacks:
                                        dht_read_callback(temperature, humidity)
                                    dht_values[digital_io] = (temperature, humidity)
                                last_dht_read = datetime.now()

                    if (
                        datetime.now()
                        > self.protocol.last_message_received + ping_interval
                    ):
                        if await self.ping():
                            failed_pings = 0
                        else:
                            failed_pings += 1

            except HomeduinoResponseTimeoutError:
                failed_pings += 1
                if failed_pings > _ALLOWED_FAILED_PINGS:
                    logger.error("Unable to ping Homeduino")
            except asyncio.CancelledError:
                logger.debug("Ping and read coroutine was canceled")
                break
            except Exception:
                logger.exception("Unexpected error")

            await asyncio.sleep(sleep_time)

        self._ping_and_read_task = None
        logger.debug("Ping and read coroutine stopped")
