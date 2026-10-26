"""Tests for the command line interface."""

import pytest

from homeduino.__main__ import add_default_pins, create_argument_parser


def test_listen() -> None:
    args = create_argument_parser().parse_args(["/dev/ttyUSB0", "listen"])
    assert args.port == "/dev/ttyUSB0"
    assert "protocol" not in args


def test_send() -> None:
    args = create_argument_parser().parse_args(
        [
            "/dev/ttyUSB0",
            "2",
            "4",
            "send",
            "switch1",
            '{"id": 98765, "unit": 4, "all": false, "state": true}',
        ]
    )
    assert args.protocol == "switch1"
    assert args.values == {"id": 98765, "unit": 4, "all": False, "state": True}


@pytest.mark.parametrize(
    "arguments",
    [
        [],  # No protocol and values
        ["switch1"],  # No values
        ["switch1", "{"],  # Invalid JSON
        ["switch1", "[1, 2]"],  # Not a JSON object
    ],
)
def test_send_invalid_arguments(arguments: list[str]) -> None:
    # Invalid arguments give a usage error instead of an exception.
    with pytest.raises(SystemExit) as exc_info:
        create_argument_parser().parse_args(
            ["/dev/ttyUSB0", "2", "4", "send", *arguments]
        )
    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    ("arguments", "receive_pin", "send_pin"),
    [
        (["/dev/ttyUSB0", "listen"], 2, 4),
        (["/dev/ttyUSB0", "3", "listen"], 3, 4),
        (["/dev/ttyUSB0", "3", "5", "listen"], 3, 5),
        (["/dev/ttyUSB0", "--debug", "listen"], 2, 4),
        (["/dev/ttyUSB0", "send", "switch1", "{}"], 2, 4),
        (["/dev/ttyUSB0", "3", "send", "switch1", "{}"], 3, 4),
        (["/dev/ttyUSB0", "3", "5", "send", "switch1", "{}"], 3, 5),
    ],
)
def test_default_pins(arguments: list[str], receive_pin: int, send_pin: int) -> None:
    # The pins are optional for both commands.
    args = create_argument_parser().parse_args(add_default_pins(arguments))
    assert args.port == "/dev/ttyUSB0"
    assert args.receive_pin == receive_pin
    assert args.send_pin == send_pin
