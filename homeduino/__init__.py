"""Python library for the Homeduino.

Sends and receives RF messages, reads and writes digital and analog pins, and reads
DHT sensors.
"""

import contextlib

# _version.py is generated when the package is built.
with contextlib.suppress(ModuleNotFoundError):
    from ._version import __version__ as __version__

from .homeduino import (
    BAUD_RATES,
    DEFAULT_BAUD_RATE,
    DEFAULT_RECEIVE_PIN,
    DEFAULT_REPEATS,
    DEFAULT_SEND_PIN,
    Homeduino,
    HomeduinoDisconnectedError,
    HomeduinoError,
    HomeduinoNotReadyError,
    HomeduinoPinMode,
    HomeduinoResponseTimeoutError,
    HomeduinoTooBusyError,
)

__all__ = [
    "BAUD_RATES",
    "DEFAULT_BAUD_RATE",
    "DEFAULT_RECEIVE_PIN",
    "DEFAULT_REPEATS",
    "DEFAULT_SEND_PIN",
    "Homeduino",
    "HomeduinoDisconnectedError",
    "HomeduinoError",
    "HomeduinoNotReadyError",
    "HomeduinoPinMode",
    "HomeduinoResponseTimeoutError",
    "HomeduinoTooBusyError",
]
