# pylint: disable=missing-module-docstring
# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring

import logging
import unittest

from homeduino.homeduino import HomeduinoProtocol

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


class TestHomeduinoProtocol(unittest.TestCase):
    def test_init(self) -> None:
        protocol = HomeduinoProtocol()
        self.assertIsInstance(protocol, HomeduinoProtocol)
