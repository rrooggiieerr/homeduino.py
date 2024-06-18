# pylint: disable=missing-module-docstring
# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring

import logging
import unittest

from homeduino import Homeduino

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


class TestHomeduinoCommands(unittest.IsolatedAsyncioTestCase):
    homeduino: Homeduino = None

    async def asyncSetUp(self):
        self.homeduino = Homeduino("/dev/tty.usbserial-110")
        await self.homeduino.connect()

    async def asyncTearDown(self):
        await self.homeduino.disconnect()

    async def test_ping(self) -> None:
        result = await self.homeduino.ping()
        self.assertTrue(result)

    async def test_send(self):
        result = await self.homeduino.send("PING test")
        self.assertEqual("PING test", result)

    async def test_rf_send(self):
        result = await self.homeduino.rf_send(
            "switch1", {"id": 98765, "unit": 4, "all": False, "state": True}
        )
        self.assertTrue(result)
