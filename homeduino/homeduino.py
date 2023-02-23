import asyncio
import io
import logging
import os
import sys
import time
from asyncio.transports import BaseTransport
from collections import deque
from functools import partial
from typing import Any, Optional

import serial_asyncio
from rfcontrol import controller
from serial_asyncio import SerialTransport
from datetime import datetime

logger = logging.getLogger(__name__)

_RESPONSE_TIMEOUT = 2.0


class HomeduinoProtocol(asyncio.Protocol):
    rf_receive_callbacks = []

    transport: SerialTransport = None

    ready = False
    _tx_busy = False
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
            logger.debug("received data: %s", decoded_data.strip())
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
                elif line != "":
                    self.str_buffer.append(line)

    def handle_ready(self):
        self.ready = True
        logger.info("Homeduino is connected")

    def handle_rf_receive(self, line: str):
        logger.debug(line)

        # The first 8 numbers are the pulse lengths and the last string of numbers is the pulse sequence
        parts = line.split(" ")

        pulse_lengths = [int(i) for i in parts[2:9]]
        # logger.debug("pulse lengths: %s", pulse_lengths)
        pulse_sequence = parts[10]

        # Match pulse sequence to a protocol
        decoded = controller.decode_pulses(pulse_lengths, pulse_sequence)

        if len(decoded) == 0:
            logger.warn("No protocol for %s %s", pulse_lengths, pulse_sequence)
        elif len(self.rf_receive_callbacks) == 0:
            logger.debug("No receive callbacks configured")
        else:
            for protocol in decoded:
                logger.debug("Forwarding RF protocol to receive callbacks")
                for rf_receive_callback in self.rf_receive_callbacks:
                    rf_receive_callback(protocol)

    def handle_key_press(self, line: str):
        logger.debug(line)
        # Ignoring key presses for now

    async def send(self, packet: str) -> str:
        """Encode and put packet string onto write buffer."""

        if not self.transport:
            logger.error("Not connected")
            return None

        if not self.ready:
            logger.error("Not ready")
            return None

        while self._tx_busy is True:
            logger.info("Too busy to transmit %s", packet)
            await asyncio.sleep(0.1)
        self._tx_busy = True

        try:
            data = packet + "\n"
            logger.debug("writing data: %s", repr(data))
            # type ignore: transport from create_connection is documented to be
            # implementation specific bidirectional, even though typed as
            # BaseTransport
            self.transport.write(data.encode())  # type: ignore

            # Wait for response
            start_time = datetime.now()
            while (datetime.now() - start_time).total_seconds() < _RESPONSE_TIMEOUT:
                if len(self.str_buffer) > 0:
                    response = self.str_buffer.pop()
                    logger.debug(response)
                    return response.strip()
            # else:
            #     raise TimeoutError("Timeout while waiting for command response")
        finally:
            self._tx_busy = False

        return None

    def connection_lost(self, exc: Optional[Exception]) -> None:
        logger.info("port closed")
        if exc:
            logger.exception("disconnected due to exception")
        else:
            logger.info("disconnected because of close/abort.")

        self.transport = None
        self.ready = False


class Homeduino:
    transport: SerialTransport = None
    protocol: HomeduinoProtocol = None
    rf_receive_callbacks = []

    def __init__(
        self,
        serial_port: str,
        receive_pin: int,
        send_pin: int,
        dht_pin: int = None,
        loop=None,
    ):
        # Test if the device exists
        if not os.path.exists(serial_port):
            logger.warn("No such file or directory: '%s'", serial_port)

        self.serial_port = serial_port
        # self.receive_pin = receive_pin
        self.receive_interrupt = receive_pin - 2
        self.send_pin = send_pin
        self.dht_pin = dht_pin

        if loop is None:
            loop = asyncio.get_event_loop()
        self.loop = loop

    async def connect(self) -> bool:
        if self.transport is None:
            protocol_factory = partial(HomeduinoProtocol, loop=self.loop)

            (
                self.transport,
                self.protocol,
            ) = await serial_asyncio.create_serial_connection(
                self.loop,
                protocol_factory,
                self.serial_port,
                baudrate=115200,
                bytesize=serial_asyncio.serial.EIGHTBITS,
                parity=serial_asyncio.serial.PARITY_NONE,
                stopbits=serial_asyncio.serial.STOPBITS_ONE,
            )

            while not self.protocol.ready:
                await asyncio.sleep(0.1)

            await self.protocol.set_receive_interrupt(self.receive_interrupt)

            for rf_receive_callback in self.rf_receive_callbacks:
                self.protocol.add_rf_receive_callback(rf_receive_callback)

            return True

        return False

    def disconnect(self):
        if self.transport is not None:
            logger.debug("Disconnecting Homeduino")
            self.transport.close()

    async def ping(self) -> bool:
        if self.protocol is not None:
            logger.debug("Pinging Homeduino")
            message = f"PING {time.time()}"
            response = await self.protocol.send(message)
            return response == message

        return False

    def add_rf_receive_callback(self, rf_receive_callback):
        if self.protocol is not None:
            self.protocol.add_rf_receive_callback(rf_receive_callback)
        else:
            self.rf_receive_callbacks.append(rf_receive_callback)

    async def rf_send(self, rf_protocol: str, values) -> bool:
        if self.protocol is not None and self.send_pin is not None:
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
        if self.protocol is not None:
            return await self.protocol.send(command)
        return None
