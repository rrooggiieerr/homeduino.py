import asyncio
import logging
import os
import sys
from asyncio.transports import BaseTransport
from functools import partial
from typing import Any, Optional

import serial_asyncio
from rfcontrol import controller
from serial_asyncio import SerialTransport

logger = logging.getLogger(__name__)


class HomeduinoProtocol(asyncio.Protocol):
    rf_receive_callbacks = []

    transport: SerialTransport = None

    ready = False
    _tx_busy = False
    _ack = None

    def __init__(
        self,
        receive_interrupt: int,
        send_pin: int,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        **_kwargs: Any,
    ):
        """Initialize class."""
        self.buffer = ""

        self.receive_interrupt = receive_interrupt
        self.send_pin = send_pin

        if loop:
            self.loop = loop
        else:
            self.loop = asyncio.get_event_loop()

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
            # logger.debug("received data: %s", decoded_data.strip())
            self.buffer += decoded_data
            while "\r\n" in self.buffer:
                line, self.buffer = self.buffer.split("\r\n", 1)
                line = line.strip()
                # logger.debug("received data: %s", line)
                self.handle_line(line)

    def handle_line(self, line: str):
        if line == "ready":
            self.handle_ready()
        elif line == "ACK":
            self.handle_ack(line)
            self._ack = line
        elif line.startswith("ACK "):
            self.handle_ack(line)
            self._ack = line
        elif line.startswith("ERR "):
            self.handle_error(line)
            self._ack = line
        elif line.startswith("PING "):
            self.handle_ping(line)
            self._ack = line
        elif line.startswith("RF receive "):
            self.handle_rf_receive(line)
        elif line.startswith("KP "):
            self.handle_key_press(line)
        else:
            logger.error("Unsupported command '%s'", line)

    def handle_ready(self):
        self.ready = True
        logger.info("Homeduino is connected")
        if self.receive_interrupt is not None:
            self.send_raw_packet(f"RF receive {self.receive_interrupt}")

    def handle_ack(self, line: str):
        logger.debug(line)
        # Ignoring the acknowledge response for now

    def handle_error(self, line: str):
        _, error = line.split(" ", 1)
        logger.error(error)

    def handle_ping(self, line: str):
        logger.debug(line)
        # Ignoring the ping response for now

    def handle_rf_receive(self, line: str):
        logger.debug(line)

        # The first 8 numbers are the pulse lengths and the last string of numbers is the pulse sequence
        parts = line.split(" ")

        pulse_lengths = parts[2:9]
        pulse_lengths = [int(i) for i in pulse_lengths]
        # logger.debug("pulse lengths: %s", pulse_lengths)
        pulse_sequence = parts[10]

        # Filter out 0 length pulses
        pulse_lengths = [i for i in pulse_lengths if i > 0]
        # logger.debug("pulse lengths: %s", pulse_lengths)

        # Sort the pulses from short to long and update indices in pulse sequence
        sorted_indices = [
            i for i, _ in sorted(enumerate(pulse_lengths), key=lambda x: x[1])
        ]
        # logger.debug("sorted indices: %s", sorted_indices)
        reindexed_pulse_sequence = ""
        for i, c in enumerate(pulse_sequence):
            reindexed_pulse_sequence += str(sorted_indices.index(int(c)))
        # logger.debug("reindexed pulse sequence: %s", reindexed_pulse_sequence)

        pulse_lengths.sort()
        pulse_sequence = reindexed_pulse_sequence

        # pulse_count = len(pulse_sequence)
        # logger.debug("pulse count: %s", pulse_count)

        # Match puls sequence to a protocol
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

    def ping(self):
        self.send_raw_packet("PING message")

    def rf_send(self, protocol: str, values) -> bool:
        if self.send_pin == None:
            return False

        # protocol = controller.get_protocol(protocol)
        protocol = getattr(sys.modules[controller.__name__], protocol)
        logger.debug(protocol)

        packet = f"RF send {self.send_pin} 3 "

        for pulse_length in protocol.pulse_lengths:
            packet += f"{pulse_length} "

        i = len(protocol.pulse_lengths)
        while i < 8:
            packet += "0 "
            i += 1

        packet += protocol.encode(**values)

        return self.send_raw_packet(packet)

    def send_raw_packet(self, packet: str) -> None:
        """Encode and put packet string onto write buffer."""

        while self._tx_busy is True:
            logger.info("Too busy to transmit %s", packet)
            self._sleep(0.1)
        self._tx_busy = True

        try:
            data = packet + "\n"
            logger.debug("writing data: %s", repr(data))
            # type ignore: transport from create_connection is documented to be
            # implementation specific bidirectional, even though typed as
            # BaseTransport
            self.transport.write(data.encode())  # type: ignore

            self._ack = None
            # while not self._ack:
            #     pass
            #     # asyncio.run(asyncio.sleep(0.01))
            #     # self.loop.run_until_complete()
            # return self._ack
            return True
        finally:
            self._tx_busy = False

        return False

    def connection_lost(self, exc: Optional[Exception]) -> None:
        logger.info("port closed")
        if exc:
            logger.exception("disconnected due to exception")
        else:
            logger.info("disconnected because of close/abort.")


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

    async def connect(self):
        if self.transport is None:
            protocol_factory = partial(
                HomeduinoProtocol, self.receive_interrupt, self.send_pin, loop=self.loop
            )

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

            for rf_receive_callback in self.rf_receive_callbacks:
                self.protocol.add_rf_receive_callback(rf_receive_callback)

        return True

    def disconnect(self):
        if self.transport is not None:
            logger.debug("Disconnecting Homeduino")
            self.transport.close()

    def add_rf_receive_callback(self, rf_receive_callback):
        if self.protocol is not None:
            self.protocol.add_rf_receive_callback(rf_receive_callback)
        else:
            self.rf_receive_callbacks.append(rf_receive_callback)

    def rf_send(self, protocol: str, values):
        if self.protocol is not None:
            return self.protocol.rf_send(protocol, values)
        return False

    def send_command(self, command):
        if self.protocol is not None:
            return self.protocol.send_raw_packet(command)
        return False
