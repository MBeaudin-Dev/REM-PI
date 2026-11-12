# REM-PI

REM-PI turns a Raspberry Pi into a field control box and display for Robot
Event Manager (REM), a system for running VEX robotics competitions. With its
relay board wired to the robots on a field, the Pi lets REM start autonomous,
switch to driver control and stop the robots for each match. A Pi can also run
as a plain display that shows a REM screen on a TV or monitor.

**What you need**

- A Raspberry Pi (works on the Pi 5) running Raspberry Pi OS Lite, with a
  screen attached
- A REM server on the same network
- For field control: the REM-PI relay board. Its KiCad design, Gerber files
  and enclosure CAD files are in this repo.

**How it works**

1. Run `pi_firmware/setup.sh` once. It installs what the Pi needs and starts
   the firmware every time the Pi boots.
2. The Pi shows its name and IP address on screen and appears in REM's
   Devices tab under Discovered Field Devices. An admin pairs it there and
   picks its role:
   - **Kiosk**: shows a REM display page full screen.
   - **Field node**: shows the field display *and* drives the relay board
     that enables, disables and switches the mode of the robots on its field.
3. If discovery doesn't work on your network, pair by hand with
   `main.py --server <url> --pair <code>`, using the code from the Devices tab.

If anything goes wrong (the server stops answering, the network drops, or the
Pi crashes or loses power), the robots are left disabled.

The rest of this README explains how the relay board works. The code is in
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

