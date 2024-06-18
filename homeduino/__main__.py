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


async def main(
    homeduino: Homeduino, action: str, protocol: str = None, values: str = None
):
    try:
        _LOGGER.info("Connecting to Homeduino")
        if not await homeduino.connect():
            _LOGGER.error("Failed to connect to Homeduino")
            return 1

        if action == "listen":
            homeduino.add_rf_receive_callback(rf_receive_callback)
            while True:
                await asyncio.sleep(1)
        elif action == "send":
            _LOGGER.debug("Protocol: %s", protocol)
            _LOGGER.debug("Values: %s", values)
            homeduino.rf_send(protocol, values)

    except SerialException as e:
        _LOGGER.error("Failed to connect to Homeduino, reason: %s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        # Handle keyboard interrupt
        pass
    finally:
        _LOGGER.info("Disconnecting from Homeduino")
        await homeduino.disconnect()

    return 0


if __name__ == "__main__":
    # Read command line arguments
    argparser = argparse.ArgumentParser()
    argparser.add_argument("port")
    argparser.add_argument(
        "receive_pin", nargs="?", type=int, default=DEFAULT_RECEIVE_PIN
    )
    argparser.add_argument("send_pin", nargs="?", type=int, default=DEFAULT_SEND_PIN)
    argparser.add_argument("action", choices=["listen", "send"])
    argparser.add_argument("protocol", nargs="?")
    argparser.add_argument("values", nargs="?")
    argparser.add_argument("--debug", dest="debugLogging", action="store_true")

    args = argparser.parse_args()

    if args.debugLogging:
        logging.basicConfig(
            format="%(asctime)s %(levelname)-8s %(filename)s:%(lineno)d %(message)s",
            level=logging.DEBUG,
        )
    else:
        logging.basicConfig(format="%(message)s", level=logging.INFO)

    try:
        loop = asyncio.new_event_loop()
        homeduino = Homeduino(
            args.port, receive_pin=args.receive_pin, send_pin=args.send_pin
        )
        sys.exit(
            loop.run_until_complete(
                main(homeduino, args.action, args.protocol, args.values)
            )
        )
    finally:
        _LOGGER.debug("Closing Loop")
        loop.close()

    sys.exit(0)
