"""
Created on 23 Nov 2022

@author: Rogier van Staveren
"""
import argparse
import asyncio
import json
import logging
import sys
import time

from serial.serialutil import SerialException

from homeduino import Homeduino

_LOGGER = logging.getLogger(__name__)


def rf_receive_callback(decoded):
    _LOGGER.info("%s %s", decoded["protocol"], json.dumps(decoded["values"]))


if __name__ == "__main__":
    # Read command line arguments
    argparser = argparse.ArgumentParser()
    argparser.add_argument("port")
    argparser.add_argument("receive_pin", type=int)
    argparser.add_argument("send_pin", type=int)
    argparser.add_argument("action", choices=["listen", "send"])
    argparser.add_argument("protocol")
    argparser.add_argument("values")
    argparser.add_argument("--debug", dest="debugLogging", action="store_true")

    args = argparser.parse_args()

    if args.debugLogging:
        logging.basicConfig(
            format="%(asctime)s %(levelname)-8s %(message)s", level=logging.DEBUG
        )
    else:
        logging.basicConfig(format="%(message)s", level=logging.INFO)

    if args.action == "listen":
        try:
            loop = asyncio.new_event_loop()
            homeduino = Homeduino(args.port, args.receive_pin, args.send_pin, loop)
            _LOGGER.info("Connecting to Homeduino")
            if not homeduino.connect():
                _LOGGER.error("Failed to connect to Homeduino")
                sys.exit(1)

            homeduino.add_rf_receive_callback(rf_receive_callback)

            loop.run_forever()
        except SerialException as e:
            _LOGGER.error("Failed to connect to Homeduino, reason: %s", e)
            sys.exit(1)
        except KeyboardInterrupt:
            # Handle keyboard interrupt
            pass
        finally:
            _LOGGER.debug("Closing Loop")
            loop.close()
            _LOGGER.info("Disconnecting from Homeduino")
            homeduino.disconnect()
    elif args.action == "send":
        try:
            loop = asyncio.new_event_loop()
            homeduino = Homeduino(args.port, args.receive_pin, args.send_pin, loop)
            _LOGGER.info("Connecting to Homeduino")
            if not homeduino.connect():
                _LOGGER.error("Failed to connect to Homeduino")
                sys.exit(1)

            protocol = args.protocol
            _LOGGER.debug("Protocol: %s", protocol)

            values = json.loads(args.values)
            _LOGGER.debug("Values: %s", values)

            homeduino.rf_send(protocol, values)

            # loop.run_until_complete(asyncio.sleep(1))
        except SerialException as e:
            _LOGGER.error("Failed to connect to Homeduino, reason: %s", e)
            sys.exit(1)
        except KeyboardInterrupt:
            # Handle keyboard interrupt
            pass
        finally:
            _LOGGER.debug("Closing Loop")
            loop.close()
            _LOGGER.info("Disconnecting from Homeduino")
            homeduino.disconnect()

    sys.exit(0)
