# Python library for the Homeduino

![Python][python-shield]
[![GitHub Release][releases-shield]][releases]
[![Licence][license-shield]][license]
[![Maintainer][maintainer-shield]][maintainer]  
[![GitHub Sponsors][github-shield]][github]
[![BuyMeCoffee][buymecoffee-shield]][buymecoffee]

## Introduction

Python library for using an Arduino with the homeduino sketch. It sends and receives RF messages
from various 433 MHz devices and sensors, reads and writes digital and analog pins, and reads DHT
sensors.

## Features

- Receive RF messages, decoded with [rfcontrolpy](https://github.com/rrooggiieerr/rfcontrolpy), and
  pass them to callbacks
- Send RF messages, by protocol and values or as a raw pulse sequence
- Read and write digital pins, and set their pin mode
- Read analog inputs and write analog (PWM) outputs
- Read DHT temperature and humidity sensors
- Call callbacks when digital inputs, analog inputs or DHT sensors change
- Ping the Homeduino to check the connection and reconnect if it's lost

## Hardware

An Arduino with the [homeduino](https://github.com/pimatic/homeduino) sketch, connected over a
serial port. By default the 433 MHz receiver is connected to pin 2 and the transmitter to pin 4.
The serial connection uses 115200 baud by default, 57600 baud is also supported.

## Installation

You can install the Python Homeduino library using the Python package manager PIP:

`pip3 install homeduino`

## `homeduino` CLI

You can use the Python Homeduino library directly from the command line to listen to or send
messages.

`python3 -m homeduino <port> <receive_pin> <send_pin> listen`

`python3 -m homeduino <port> <receive_pin> <send_pin> send <protocol> <values>`

### Troubleshooting

You can add the `--debug` flag to the CLI command to get a more details on what's going on. Like so:

`python3 -m homeduino <port> --debug listen`

The `--debug` flag has to be given before `listen` or `send`.

## Support my work

Do you enjoy using this Python library? Then consider supporting my work using one of the following
platforms, your donation is greatly appreciated and keeps me motivated:

[![GitHub Sponsors][github-shield]][github]
[![BuyMeCoffee][buymecoffee-shield]][buymecoffee]

## Hire me

If you're in need of a freelance Python developer for your project please contact me, you can find
my email address on [my GitHub profile](https://github.com/rrooggiieerr).

[python-shield]: https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54
[releases]: https://github.com/rrooggiieerr/homeduino.py/releases
[releases-shield]: https://img.shields.io/github/v/release/rrooggiieerr/homeduino.py?style=for-the-badge
[license]: ./LICENSE.md
[license-shield]: https://img.shields.io/github/license/rrooggiieerr/homeduino.py?style=for-the-badge
[maintainer]: https://github.com/rrooggiieerr
[maintainer-shield]: https://img.shields.io/badge/MAINTAINER-%40rrooggiieerr-41BDF5?style=for-the-badge
[buymecoffee]: https://www.buymeacoffee.com/rrooggiieerr
[buymecoffee-shield]: https://img.shields.io/badge/Buy%20Me%20a%20Coffee-ffdd00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black
[github]: https://github.com/sponsors/rrooggiieerr
[github-shield]: https://img.shields.io/badge/sponsor-30363D?style=for-the-badge&logo=GitHub-Sponsors&logoColor=ea4aaa
