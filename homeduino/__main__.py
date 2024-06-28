"""
Created on 23 Nov 2022

@author: Rogier van Staveren
"""

# pylint: disable=missing-function-docstring

import argparse
import asyncio
import json
import logging
import sys

from serial.serialutil import SerialException

from homeduino import DEFAULT_RECEIVE_PIN, DEFAULT_SEND_PIN, Homeduino

_LOGGER = logging.getLogger(__name__)


def rf_receive_callback(decoded):
    _LOGGER.info("%s %s", decoded["protocol"], json.dumps(decoded["values"]))


async def listen(homeduino_: Homeduino):
    try:
        _LOGGER.info("Connecting to Homeduino")
        if not await homeduino_.connect():
            _LOGGER.error("Failed to connect to Homeduino")
            return 1

        homeduino_.add_rf_receive_callback(rf_receive_callback)
        while True:
            await asyncio.sleep(1)

    except SerialException as e:
        _LOGGER.error("Failed to connect to Homeduino, reason: %s", e)
        sys.exit(1)
    finally:
        _LOGGER.info("Disconnecting from Homeduino")
        await homeduino_.disconnect()


async def send(homeduino_: Homeduino, protocol: str = None, values: str = None):
    try:
        _LOGGER.info("Connecting to Homeduino")
        if not await homeduino.connect(ping_interval=0):
            _LOGGER.error("Failed to connect to Homeduino")
            return 1

        _LOGGER.debug("Protocol: %s", protocol)
        _LOGGER.debug("Values: %s", values)
        await homeduino_.rf_send(protocol, values)

    except SerialException as e:
        _LOGGER.error("Failed to connect to Homeduino, reason: %s", e)
        sys.exit(1)
    finally:
        _LOGGER.info("Disconnecting from Homeduino")
        await homeduino_.disconnect()


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
