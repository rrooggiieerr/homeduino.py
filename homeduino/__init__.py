# pylint: disable=missing-module-docstring

try:
    from ._version import __version__
except ModuleNotFoundError:
    pass
from .homeduino import (
    BAUD_RATES,
    DEFAULT_BAUD_RATE,
    DEFAULT_RECEIVE_PIN,
    DEFAULT_SEND_PIN,
    Homeduino,
    HomeduinoDisconnectedError,
    HomeduinoError,
    HomeduinoNotReadyError,
    HomeduinoPinMode,
    HomeduinoResponseTimeoutError,
    HomeduinoTooBusyError,
)
