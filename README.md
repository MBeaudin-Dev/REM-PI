# REM-PI

Firmware for the Raspberry Pis used by Robot Event Manager. A Pi runs in one
of two roles, assigned by an admin when it is paired:

- **Kiosk**: shows a display page in full-screen Chromium.
- **Field node**: shows the field display *and* drives the relay HAT that
  enables, disables and switches the mode of the robots on its field.

This README covers how the relay HAT works. The code is in
[`pi_firmware/hardware.py`](pi_firmware/hardware.py).

## The relay HAT

The HAT has two relays, each driven by one GPIO pin on the Pi. Together they
reproduce the two control lines of the VEX V5 legacy Competition Port:

| Relay  | Physical pin | BCM GPIO | Controls                        |
| ------ | ------------ | -------- | ------------------------------- |
| Enable | 11           | GPIO17   | Robots enabled or disabled      |
| Mode   | 29           | GPIO5    | Autonomous or driver (user) control |

The code uses `GPIO.BOARD` numbering, so the pins are referred to by their
physical header position (11 and 29), not their BCM numbers.

Setting a pin HIGH energizes its relay; setting it LOW de-energizes it.

## Robot states

The firmware only ever puts the HAT into one of three states:

| State          | Enable pin (11)     | Mode pin (29)       | Method                |
| -------------- | ------------------- | ------------------- | --------------------- |
| **Disabled**   | LOW (de-energized)  | LOW (de-energized)  | `disable()`           |
| **Autonomous** | HIGH (energized)    | LOW (de-energized)  | `enable_autonomous()` |
| **Driver**     | HIGH (energized)    | HIGH (energized)    | `enable_driver()`     |

With both relays de-energized the robots are disabled, so a crash, power loss
or unconfigured GPIO leaves the field in a safe state.

### Enable vs. disable

The **enable relay** decides whether robots may move at all.

- Enable pin **HIGH**: robots are **enabled**, in whichever mode the mode
  relay selects.
- Enable pin **LOW**: robots are **disabled**.

### Autonomous vs. driver

The **mode relay** decides which program the robots run while enabled.

- Mode pin **HIGH**: **driver (user) control**.
- Mode pin **LOW**: **autonomous**.

The mode pin only matters while the enable pin is HIGH. In the disabled state
the mode pin is left LOW.

## What triggers each state

The field node receives commands from the server over a WebSocket
([`pi_firmware/ws_client.py`](pi_firmware/ws_client.py)):

| Event                                        | Result     |
| -------------------------------------------- | ---------- |
| `phase_change` with `phase: "autonomous"`    | Autonomous |
| `phase_change` with `phase: "driver"`        | Driver     |
| `phase_change` with any other phase          | Disabled   |
| `estop`                                      | Disabled   |

The Pi also disables the robots on its own, without a command from the
server:

- **On startup**, as soon as the relay pins are set up.
- **If the server goes quiet** for 20 seconds (4 missed heartbeats), even if
  the connection still looks open.
- **If the connection to the server drops**, before each reconnect attempt.
- **On shutdown**, before releasing the GPIO pins.

