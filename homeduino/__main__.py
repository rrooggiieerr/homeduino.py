"""Command line interface to listen to or send RF messages with a Homeduino.

Usage:
    python3 -m homeduino <port> [<receive_pin> [<send_pin>]] listen
    python3 -m homeduino <port> [<receive_pin> [<send_pin>]] send <protocol> <values>

<values> is a JSON object with the values to send, for example
'{"id": 98765, "unit": 4, "all": false, "state": true}' for switch1.
"""

import argparse
import asyncio
import json
import logging
import sys
from typing import Any

from serialx import SerialException

from homeduino import DEFAULT_RECEIVE_PIN, DEFAULT_SEND_PIN, Homeduino

_LOGGER = logging.getLogger(__name__)


def rf_receive_callback(decoded: dict[str, Any]) -> None:
    """Log a decoded RF message."""
    _LOGGER.info("%s %s", decoded["protocol"], json.dumps(decoded["values"]))


async def listen(homeduino_: Homeduino) -> int:
    """Log the RF messages received by the Homeduino."""
    try:
        _LOGGER.info("Connecting to Homeduino")
        if not await homeduino_.connect():
            _LOGGER.error("Failed to connect to Homeduino")
            return 1

        homeduino_.add_rf_receive_callback(rf_receive_callback)
        # Listen until interrupted.
        await asyncio.Event().wait()

    except SerialException:
        _LOGGER.exception("Failed to connect to Homeduino")
        sys.exit(1)
    finally:
        _LOGGER.info("Disconnecting from Homeduino")
        await homeduino_.disconnect()

    return 0


async def send(homeduino_: Homeduino, protocol: str, values: str) -> int:
    """Send an RF message with the Homeduino."""
    try:
        _LOGGER.info("Connecting to Homeduino")
        if not await homeduino_.connect(ping_interval=0):
            _LOGGER.error("Failed to connect to Homeduino")
            return 1

        _LOGGER.debug("Protocol: %s", protocol)
        _LOGGER.debug("Values: %s", values)
        await homeduino_.rf_send(protocol, json.loads(values))

    except SerialException:
        _LOGGER.exception("Failed to connect to Homeduino")
        sys.exit(1)
    finally:
        _LOGGER.info("Disconnecting from Homeduino")
        await homeduino_.disconnect()

    return 0


if __name__ == "__main__":
    # Read command line arguments
    argparser = argparse.ArgumentParser()
    argparser.add_argument("port")
    argparser.add_argument(
        "receive_pin", nargs="?", type=int, default=DEFAULT_RECEIVE_PIN
    )
    argparser.add_argument("send_pin", nargs="?", type=int, default=DEFAULT_SEND_PIN)

    subparsers = argparser.add_subparsers()

    listen_parser = subparsers.add_parser("listen")

    send_parser = subparsers.add_parser("send")
    send_parser.add_argument("protocol", nargs="?")
    send_parser.add_argument("values", nargs="?")

    argparser.add_argument("--debug", dest="debugLogging", action="store_true")

    args = argparser.parse_args()

    if args.debugLogging:
        logging.basicConfig(
            format="%(asctime)s %(levelname)-8s %(filename)s:%(lineno)d %(message)s",
            level=logging.DEBUG,
        )
    else:
        logging.basicConfig(format="%(message)s", level=logging.INFO)

    homeduino = Homeduino(
        args.port, rf_receive_pin=args.receive_pin, rf_send_pin=args.send_pin
    )

    loop = asyncio.new_event_loop()

    try:
        if "protocol" in args:
            asyncio.run(send(homeduino, args.protocol, args.values))
        else:
            asyncio.run(listen(homeduino))
    except KeyboardInterrupt:
        # Handle keyboard interrupt
        pass
    finally:
        loop.close()

    sys.exit(0)
