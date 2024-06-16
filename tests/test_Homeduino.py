import logging
import unittest

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

from homeduino import Homeduino


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
