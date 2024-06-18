# pylint: disable=missing-module-docstring
# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring

import logging
import unittest

from homeduino import Homeduino

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


class TestHomeduino(unittest.IsolatedAsyncioTestCase):
    async def test_connect(self) -> None:
        hd = Homeduino("/dev/tty.usbserial-110")
        success = await hd.connect()
        self.assertTrue(success)
        await hd.disconnect()

    async def test_connect_non_existing_tty(self) -> None:
        hd = Homeduino("/dev/tty.nonexisting")
        success = await hd.connect()
        self.assertFalse(success)
