# pylint: disable=missing-module-docstring
# pylint: disable=missing-class-docstring
# pylint: disable=missing-function-docstring

import json
import logging
import unittest

from homeduino import Homeduino

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

_SETTINGS_JSON = "settings.json"


class TestHomeduino(unittest.IsolatedAsyncioTestCase):
    serial_port: str = None

    async def asyncSetUp(self):
        with open(_SETTINGS_JSON, encoding="utf8") as settings_file:
            settings = json.load(settings_file)
            self.serial_port = settings.get("serial_port")

    async def test_connect(self) -> None:
        hd = Homeduino(self.serial_port)
        success = await hd.connect()
        self.assertTrue(success)
        await hd.disconnect()

    async def test_connect_non_existing_tty(self) -> None:
        hd = Homeduino("/dev/tty.nonexisting")
        success = await hd.connect()
        self.assertFalse(success)
