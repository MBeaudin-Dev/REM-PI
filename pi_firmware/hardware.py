"""GPIO driver for the field-control relay board."""


class RelayHardware:
    """Drives the two-relay board: RELAY_ENABLE_PIN (enabled/disabled) and
    RELAY_MODE_PIN (autonomous/driver), matching the V5 legacy Competition
    Port's signal lines.

    HIGH energizes a relay. 
    
    Enable pin HIGH = enabled, LOW = disabled.
    MODE pin HIGH = Usercontrol, LOW = auto.

    `RPi.GPIO` here is provided by rpi-lgpio (see setup.sh), which also works
    on the Pi 5."""

    # Physical header pins (BOARD mode), not BCM numbers.
    RELAY_ENABLE_PIN = 11
    RELAY_MODE_PIN = 29

    def __init__(self):
        # Imported here so importing this module doesn't need a Pi.
        import RPi.GPIO as GPIO

        self._gpio = GPIO
        self._gpio.setmode(GPIO.BOARD)
        self._gpio.setup(self.RELAY_ENABLE_PIN, GPIO.OUT)
        self._gpio.setup(self.RELAY_MODE_PIN, GPIO.OUT)
        # Start in the safe state.
        self.disable()

    def enable_autonomous(self):
        self._gpio.output(self.RELAY_ENABLE_PIN, self._gpio.HIGH)
        self._gpio.output(self.RELAY_MODE_PIN, self._gpio.LOW)
        print("AUTO, RelayEnable ON, RelayMode OFF/AUTO")

    def enable_driver(self):
        self._gpio.output(self.RELAY_ENABLE_PIN, self._gpio.HIGH)
        self._gpio.output(self.RELAY_MODE_PIN, self._gpio.HIGH)
        print("USERCONTROL, RelayEnable ON, RelayMode ON/USERCONTROL")

    def disable(self):
        self._gpio.output(self.RELAY_ENABLE_PIN, self._gpio.LOW)
        self._gpio.output(self.RELAY_MODE_PIN, self._gpio.LOW)
        print("STOP, RelayEnable OFF And RelayMode OFF")

    def cleanup(self):
        self.disable()
        # Releases the pins. With HIGH = enabled, released pins leave the
        # relays de-energized, so the robots stay disabled.
        self._gpio.cleanup()
