"""
Homeduino Unit Tests.
"""

import asyncio
import logging

logging.basicConfig(
    format="%(asctime)s %(levelname)-8s %(filename)s:%(lineno)d %(message)s",
    level=logging.DEBUG,
)


def async_test(coro):
    "Creates an event loop wrapper around an async test case"

    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro(*args, **kwargs))
        finally:
            loop.close()

    return wrapper
