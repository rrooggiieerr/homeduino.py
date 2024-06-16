try:
    from ._version import __version__
except ModuleNotFoundError:
    pass
from .homeduino import (
    BAUD_RATES,
    DEFAULT_BAUD_RATE,
    DEFAULT_RECEIVE_PIN,
    DEFAULT_SEND_PIN,
    DisconnectedError,
    Homeduino,
    HomeduinoError,
    NotReadyError,
    ResponseTimeoutError,
    TooBusyError,
)
