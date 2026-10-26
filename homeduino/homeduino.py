"""Connection with a Homeduino over a serial port.

Sends and receives RF messages, reads and writes digital and analog pins, and reads
DHT sensors.
"""

import asyncio
from asyncio.transports import BaseTransport
from collections import deque
from collections.abc import Callable
from datetime import datetime
from enum import IntEnum
import logging
import re
import time
from types import ModuleType
from typing import Any, Final, cast

from rfcontrol import controller
import serialx
from serialx import BaseSerialTransport, Parity, SerialException, StopBits

logger = logging.getLogger(__name__)

DEFAULT_BAUD_RATE: Final = 115200
BAUD_RATES: Final = [57600, DEFAULT_BAUD_RATE]
DEFAULT_RECEIVE_PIN: Final = 2
DEFAULT_SEND_PIN: Final = 4
DEFAULT_REPEATS: Final = 7

_RESPONSE_TIMEOUT = 2
_READY_TIMEOUT = 5
_BUSY_TIMEOUT = 1
_RF_SEND_DELAY = 0.2
_PING_INTERVAL = 5
_ALLOWED_FAILED_PINGS = 5
_DHT_READ_DELAY = 2

# The pins of the Arduino Nano that can be used for inputs. Digital pins 0 and 1 are
# used for the serial connection.
_DIGITAL_IOS = range(2, 14)
_ANALOG_INPUTS = range(8)

RfReceiveCallback = Callable[[dict[str, Any]], None]
DigitalReadCallback = Callable[[bool], None]
AnalogReadCallback = Callable[[int], None]
DhtReadCallback = Callable[[float, float], None]


class HomeduinoError(Exception):
    """Generic Homeduino error."""


class HomeduinoResponseTimeoutError(HomeduinoError):
    """Response timeout error.

    If the response takes to long to receive.
    """


class HomeduinoDisconnectedError(HomeduinoError):
    """Homeduino Disconnected error."""


class HomeduinoNotReadyError(HomeduinoError):
    """Homeduino not ready error."""


class HomeduinoTooBusyError(HomeduinoError):
    """Homeduino Disconnected error."""


class HomeduinoProtocol(asyncio.Protocol):
    """Protocol for the serial connection with a Homeduino."""

    transport: BaseSerialTransport | None = None

    ready = False
    # Monotonic time of the last RF send.
    _last_rf_send: float | None = None
    last_message_received: datetime | None = None
    # Monotonic time of the last received message.
    last_message_time: float | None = None

    _str_buffer = ""
    _awaiting_response = False
    _response_timeout_counter = 0

    def __init__(
        self,
        **_kwargs: Any,
    ):
        """Initialize class."""
        self._rf_receive_callbacks: list[RfReceiveCallback] = []
        self.response_buffer: deque[str] = deque()
        self._send_lock = asyncio.Lock()

    def busy(self) -> bool:
        """Return True while a command is waiting for its response."""
        return self._send_lock.locked()

    async def set_rf_receive_interrupt(self, rf_receive_interrupt: int) -> bool:
        """Enable RF receiving on the given interrupt."""
        if rf_receive_interrupt is not None:
            response = await self.send(f"RF receive {rf_receive_interrupt}")
            return response == "ACK"
        return False

    def add_rf_receive_callback(
        self, rf_receive_callback: RfReceiveCallback | None
    ) -> None:
        """Add a callback for decoded RF messages."""
        if rf_receive_callback is not None:
            self._rf_receive_callbacks.append(rf_receive_callback)

    def connection_made(self, transport: BaseTransport) -> None:
        """Store the transport of the new connection."""
        # The transport is created by serialx.create_serial_connection().
        self.transport = cast(BaseSerialTransport, transport)

    def data_received(self, data: bytes) -> None:
        """Split the received data into lines and handle them."""
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
                self.last_message_time = time.monotonic()

    def handle_ready(self) -> None:
        """Mark the Homeduino as ready."""
        self.ready = True
        logger.info("Homeduino is connected")

    def handle_rf_receive(self, line: str) -> None:
        """Decode a received RF message and pass it to the callbacks."""
        logger.debug(line)

        # The first 8 numbers are the pulse lengths and the last string of numbers is the pulse
        # sequence.
        parts = line.split(" ")

        try:
            pulse_lengths = [int(i) for i in parts[2:10]]
            # logger.debug("pulse lengths: %s", pulse_lengths)
            pulse_sequence = parts[10]
        except IndexError, ValueError:
            # Can happen when the line is garbled, e.g. when the Homeduino restarted.
            logger.warning("Invalid RF receive message '%s'", line)
            return

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
        """Handle a key press, currently ignored."""
        logger.debug(line)
        # Ignoring key presses for now

    async def send(self, packet: str) -> str:
        """Encode and put packet string onto write buffer."""

        if not self.transport:
            raise HomeduinoDisconnectedError("Homeduino is not connected")

        is_rf_send = packet.startswith("RF send ")
        if is_rf_send and self._last_rf_send is not None:
            # Allow some time between rf send commands to prevent flooding
            delay = self._last_rf_send + _RF_SEND_DELAY - time.monotonic()
            if delay > 0:
                logger.debug("RF send delay")
                await asyncio.sleep(delay)

        try:
            data = packet + "\n"
            async with self._send_lock:
                # Drop responses that arrived too late for an earlier command.
                self.response_buffer.clear()
                self._awaiting_response = True
                try:
                    logger.debug("Writing data: %s", repr(data))
                    self.transport.write(data.encode())

                    timeout = time.monotonic() + _RESPONSE_TIMEOUT
                    while len(self.response_buffer) == 0:
                        if time.monotonic() > timeout:
                            self._response_timeout_counter += 1
                            logger.error(
                                "Timeout while waiting for command response %i consecutive times",
                                self._response_timeout_counter,
                            )
                            raise HomeduinoResponseTimeoutError(
                                "Timeout while waiting for command response"
                            )
                        logger.debug("Waiting for command response")
                        await asyncio.sleep(0.01)
                finally:
                    # Responses that arrive after a timeout aren't stored.
                    self._awaiting_response = False

                self._response_timeout_counter = 0
                response = self.response_buffer.popleft()

            logger.debug("Command response received: %s", response)
            return response.strip()
        finally:
            if is_rf_send:
                # Set last RF send timestamp
                self._last_rf_send = time.monotonic()

    def connection_lost(self, exc: Exception | None) -> None:
        """Clear the transport when the connection is lost."""
        logger.info("Port closed")
        if exc:
            logger.error("Disconnected due to exception: %s", exc)
        else:
            logger.info("Disconnected because of close/abort")

        self.transport = None
        self.ready = False


class HomeduinoPinMode(IntEnum):
    """Pin modes of the Homeduino."""

    INPUT = 0x00
    OUTPUT = 0x01
    INPUT_PULLUP = 0x02


class Homeduino:
    """Homeduino connected to a serial port.

    Sends and receives RF messages, reads and writes digital and analog pins, and
    reads DHT sensors. Callbacks are called on received RF messages and on changes
    of the inputs.
    """

    rf_receive_interrupt: int | None = None

    protocol: HomeduinoProtocol | None = None

    _ping_and_read_task: asyncio.Task[None] | None = None
    _loop: asyncio.AbstractEventLoop | None = None

    _ping_failed_counter = 0

    def __init__(
        self,
        path: str,
        baud_rate: int = DEFAULT_BAUD_RATE,
        rf_receive_pin: int | None = DEFAULT_RECEIVE_PIN,
        rf_send_pin: int | None = DEFAULT_SEND_PIN,
    ):
        """Initialize Homeduino."""
        self._path = path
        self._baud_rate = baud_rate
        if rf_receive_pin is not None:
            self.rf_receive_interrupt = rf_receive_pin - 2
        self.rf_send_pin = rf_send_pin

        self._rf_receive_callbacks: list[RfReceiveCallback] = []
        self._digital_read_callbacks: dict[int, list[DigitalReadCallback]] = {}
        self._analog_read_callbacks: dict[int, list[AnalogReadCallback]] = {}
        self._dht_read_callbacks: dict[int, tuple[int, list[DhtReadCallback]]] = {}

    async def _connect(self) -> bool:
        if not self.connected():
            if self._loop is None:
                self._loop = asyncio.get_event_loop()

            logger.info("Connecting to %s", self._path)
            try:
                # Create the protocol here, create_serial_connection() returns it as
                # asyncio.Protocol.
                protocol = HomeduinoProtocol()
                await serialx.create_serial_connection(
                    self._loop,
                    protocol_factory=lambda: protocol,
                    url=self._path,
                    baudrate=self._baud_rate,
                    byte_size=8,
                    parity=Parity.NONE,
                    stopbits=StopBits.ONE,
                    # timeout=1,
                )
                self.protocol = protocol

                timeout = time.monotonic() + _READY_TIMEOUT
                while self.protocol is not None and not self.protocol.ready:
                    if time.monotonic() > timeout:
                        break
                    logger.debug("Waiting for Homeduino to become ready")
                    await asyncio.sleep(0.1)

                if self.protocol is None:
                    logger.warning("Homeduino disconnected while connecting")
                    return False

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
            except OSError, SerialException:
                logger.exception("Failed to connect to %s", self._path)
                # Close the serial port if it was opened.
                await self._disconnect()
            except HomeduinoError:
                # Close the serial port, the caller handles the error.
                await self._disconnect()
                raise
            else:
                return True

        return False

    async def connect(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
        ping_interval: float = _PING_INTERVAL,
    ) -> bool:
        """Connect to the Homeduino."""
        self._loop = loop

        if not self.connected() and await self._connect():
            if ping_interval > 0:
                self._ping_and_read_task = asyncio.create_task(
                    self._ping_and_read_coroutine(ping_interval)
                )

            return True

        return False

    def connected(self) -> bool:
        """True if there is a connection with the Homeduino."""
        return self.protocol is not None and self.protocol.transport is not None

    async def _disconnect(self) -> bool:
        if self.protocol is not None:
            logger.debug("Disconnecting Homeduino")
            if self.protocol.transport is not None:
                self.protocol.transport.close()

                timeout = time.monotonic() + _READY_TIMEOUT
                while self.protocol.transport is not None:
                    if time.monotonic() > timeout:
                        logger.error(
                            "Timeout while waiting for Homeduino to disconnect"
                        )
                        raise HomeduinoResponseTimeoutError(
                            "Timeout while waiting for Homeduino to disconnect"
                        )
                    logger.debug("Waiting for Homeduino to disconnect")
                    await asyncio.sleep(0.01)

            self.protocol = None
            logger.debug("Homeduino disconnected")

        return not self.connected()

    async def disconnect(self) -> bool:
        """Disconnect from the Homeduino."""
        await self._cancel_ping_and_read()
        return await self._disconnect()

    async def _reconnect(self) -> bool:
        await self._disconnect()
        return await self._connect()

    async def _ping(self) -> bool:
        logger.debug("Pinging Homeduino")
        message = f"PING {time.time()}"

        if self.protocol is None:
            raise HomeduinoDisconnectedError("Homeduino is not connected")

        response = await self.protocol.send(message)
        if response == message:
            self._ping_failed_counter = 0
            logger.debug("Pinging Homeduino successful")
            return True

        self._ping_failed_counter += 1
        logger.error(
            "Pinging Homeduino failed %i consecutive times", self._ping_failed_counter
        )
        return False

    async def ping(self) -> bool:
        """Ping the Homeduino."""
        if not self.connected() or self.protocol is None:
            raise HomeduinoDisconnectedError("Homeduino is not connected")

        if self.protocol.busy():
            return True

        return await self._ping()

    def add_rf_receive_callback(self, rf_receive_callback: RfReceiveCallback) -> None:
        """Add a callback for decoded RF messages."""
        self._rf_receive_callbacks.append(rf_receive_callback)
        if self.connected() and self.protocol is not None:
            self.protocol.add_rf_receive_callback(rf_receive_callback)

    async def add_digital_read_callback(
        self, digital_io: int, digital_read_callback: DigitalReadCallback
    ) -> None:
        """Add a callback for changes of a digital input."""
        if digital_io not in _DIGITAL_IOS:
            raise ValueError(f"Invalid digital I/O {digital_io}")
        if self.connected():
            await self.pin_mode(digital_io, HomeduinoPinMode.INPUT_PULLUP)
        if digital_io in self._digital_read_callbacks:
            self._digital_read_callbacks[digital_io].append(digital_read_callback)
        else:
            self._digital_read_callbacks[digital_io] = [digital_read_callback]

    def add_analog_read_callback(
        self, analog_input: int, analog_read_callback: AnalogReadCallback
    ) -> None:
        """Add a callback for changes of an analog input."""
        if analog_input not in _ANALOG_INPUTS:
            raise ValueError(f"Invalid analog input {analog_input}")
        if analog_input in self._analog_read_callbacks:
            self._analog_read_callbacks[analog_input].append(analog_read_callback)
        else:
            self._analog_read_callbacks[analog_input] = [analog_read_callback]

    async def add_dht_read_callback(
        self, dht_type: int, digital_io: int, dht_read_callback: DhtReadCallback
    ) -> None:
        """Add a callback for changes of a DHT sensor."""
        if digital_io not in _DIGITAL_IOS:
            raise ValueError(f"Invalid digital I/O {digital_io}")
        if self.connected():
            await self.pin_mode(digital_io, HomeduinoPinMode.INPUT)
        if digital_io in self._dht_read_callbacks:
            self._dht_read_callbacks[digital_io][1].append(dht_read_callback)
        else:
            self._dht_read_callbacks[digital_io] = (dht_type, [dht_read_callback])

    def supports_rf_send(self) -> bool:
        """Return True if an RF send pin is configured."""
        return self.rf_send_pin is not None

    async def raw_rf_send(self, command: str, repeats: int = DEFAULT_REPEATS) -> bool:
        """Send raw RF sequence."""
        if not self.connected() or self.protocol is None:
            raise HomeduinoDisconnectedError("Homeduino is not connected")

        if self.supports_rf_send():
            packet = f"RF send {self.rf_send_pin} {repeats} {command}"

            response = await self.protocol.send(packet)
            return response == "ACK"

        return False

    async def rf_send(
        self, rf_protocol: str, values: dict[str, Any], repeats: int = DEFAULT_REPEATS
    ) -> bool:
        """Send RF protocol sequence."""
        if not self.connected() or self.protocol is None:
            raise HomeduinoDisconnectedError("Homeduino is not connected")

        if self.supports_rf_send():
            protocol: ModuleType = getattr(controller, rf_protocol)
            logger.debug(protocol)

            # Encode first, the raw protocols set their pulse lengths in encode().
            pulses = protocol.encode(**values)

            packet = f"RF send {self.rf_send_pin} {repeats} "

            for pulse_length in protocol.pulse_lengths:
                packet += f"{pulse_length} "

            i = len(protocol.pulse_lengths)
            while i < 8:
                packet += "0 "
                i += 1

            packet += pulses

            response = await self.protocol.send(packet)
            return response == "ACK"

        return False

    async def send(self, command: str) -> str:
        """Send command to Homeduino."""
        if not self.connected() or self.protocol is None:
            raise HomeduinoDisconnectedError("Homeduino is not connected")

        return await self.protocol.send(command)

    async def pin_mode(self, digital_io: int, mode: HomeduinoPinMode) -> bool:
        """Set pin mode."""
        response = await self.send(f"PM {digital_io} {mode}")
        return response == "ACK"

    async def digital_write(self, digital_io: int, value: bool) -> bool:
        """Write digital output."""
        response = await self.send(f"DW {digital_io} {1 if value else 0}")
        return response == "ACK"

    async def digital_read(self, digital_io: int) -> bool:
        """Read digital output."""
        response = await self.send(f"DR {digital_io}")
        return response == "ACK 1"

    async def analog_write(self, digital_io: int, value: int) -> bool:
        """Analog (PWM) write."""
        if digital_io not in (3, 5, 6, 9, 10, 11):
            return False

        response = await self.send(f"AW {digital_io} {value}")
        return response == "ACK"

    async def analog_read(self, analog_input: int) -> int:
        """Read analog input."""
        response = await self.send(f"AR {analog_input}")
        response = response.split(" ")[1]
        return int(response)

    async def dht_read(
        self, dht_type: int, digital_io: int
    ) -> tuple[float, float] | tuple[None, None]:
        """Read DHT sensor."""
        response = await self.send(f"DHT {dht_type} {digital_io}")
        parts = response.split(" ")
        try:
            temperature = float(parts[1])
            humidity = float(parts[2])
        except ValueError:
            return (None, None)

        return (temperature, humidity)

    @staticmethod
    def get_protocols() -> list[str]:
        """Returns the supported protocols in natural sorted order."""

        def convert(text: str) -> int | str:
            return int(text) if text.isdigit() else text.lower()

        def alphanum_key(key: str) -> list[int | str]:
            return [convert(c) for c in re.split("([0-9]+)", key)]

        protocol_names = [protocol.name for protocol in controller.get_all_protocols()]
        return sorted(protocol_names, key=alphanum_key)

    async def _cancel_ping_and_read(self) -> bool:
        if self._ping_and_read_task is not None and not (
            self._ping_and_read_task.done() or self._ping_and_read_task.cancelled()
        ):
            return self._ping_and_read_task.cancel()

        return True

    async def _ping_and_read_coroutine(self, ping_interval: float) -> None:
        """Ping the Homeduino and read its inputs.

        To test the connection for availability a ping message can be sent every interval if no
        other messages where received during the interval.
        """
        failed_pings = 0
        digital_io_values: list[bool | None] = [None] * 14
        analog_input_values: list[int | None] = [None] * 8
        dht_values: list[tuple[float | None, float | None]] = [(None, None)] * 14
        last_dht_read: float | None = None
        sleep_time = 0.1
        while True:
            try:
                if not self.connected():
                    await self._reconnect()

                if self.protocol is None:
                    break

                if not self.connected():
                    await asyncio.sleep(sleep_time)
                    continue

                for (
                    digital_io,
                    digital_read_callbacks,
                ) in self._digital_read_callbacks.copy().items():
                    sleep_time = 0.01
                    if self.protocol.busy():
                        continue
                    digital_value = await self.digital_read(digital_io)
                    if digital_value == digital_io_values[digital_io]:
                        continue
                    logger.debug("Digital %i: %s", digital_io, digital_value)
                    for digital_read_callback in digital_read_callbacks:
                        digital_read_callback(digital_value)
                    digital_io_values[digital_io] = digital_value

                for (
                    analog_input,
                    analog_read_callbacks,
                ) in self._analog_read_callbacks.copy().items():
                    sleep_time = 0.01
                    if self.protocol.busy():
                        continue
                    analog_value = await self.analog_read(analog_input)
                    if analog_value == analog_input_values[analog_input]:
                        continue
                    logger.debug("Analog %i: %s", analog_input, analog_value)
                    for analog_read_callback in analog_read_callbacks:
                        analog_read_callback(analog_value)
                    analog_input_values[analog_input] = analog_value

                if (
                    last_dht_read is None
                    or time.monotonic() > last_dht_read + _DHT_READ_DELAY
                ):
                    for (
                        digital_io,
                        (dht_type, dht_read_callbacks),
                    ) in self._dht_read_callbacks.copy().items():
                        if self.protocol.busy():
                            continue
                        temperature, humidity = await self.dht_read(
                            dht_type, digital_io
                        )
                        last_dht_read = time.monotonic()
                        # Both are None if the DHT couldn't be read.
                        if (
                            temperature is None
                            or humidity is None
                            or (temperature, humidity) == dht_values[digital_io]
                        ):
                            continue
                        logger.debug(
                            "DHT %i: %s°, %s%%", digital_io, temperature, humidity
                        )
                        for dht_read_callback in dht_read_callbacks:
                            dht_read_callback(temperature, humidity)
                        dht_values[digital_io] = (temperature, humidity)

                last_message_time = self.protocol.last_message_time
                if (
                    last_message_time is None
                    or time.monotonic() > last_message_time + ping_interval
                ):
                    if await self.ping():
                        failed_pings = 0
                    else:
                        failed_pings += 1

                await asyncio.sleep(sleep_time)
            except HomeduinoResponseTimeoutError:
                failed_pings += 1
                if failed_pings > _ALLOWED_FAILED_PINGS:
                    logger.exception("Unable to ping Homeduino")
                    await self._reconnect()
            except asyncio.CancelledError:
                logger.debug("Ping and read coroutine was canceled")
                break
            except Exception:
                logger.exception("Unexpected error")

        self._ping_and_read_task = None
        logger.debug("Ping and read coroutine stopped")
