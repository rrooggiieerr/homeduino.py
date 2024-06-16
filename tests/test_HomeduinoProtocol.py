import logging
import unittest

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

from homeduino.homeduino import HomeduinoProtocol


class TestHomeduinoProtocol(unittest.TestCase):
    def test_init(self) -> None:
        protocol = HomeduinoProtocol()
        self.assertIsInstance(protocol, HomeduinoProtocol)
