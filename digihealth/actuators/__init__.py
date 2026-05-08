from typing import Dict, Any
from ..config import config
from ..logger import logger

class ActuatorManager:
    """Manages all actuators."""

    def __init__(self):
        self.actuators = []
        self._load_actuators()

    def _load_actuators(self):
        """Load available actuators."""
        if config.actuators.neopixel.get('enabled', True):
            try:
                from .neopixel_controller import NeoPixelController
                self.actuators.append(NeoPixelController(config.actuators.neopixel))
                logger.info("NeoPixel actuator loaded")
            except Exception as e:
                logger.warning(f"NeoPixel actuator not available: {e}")

        if config.actuators.shelly.get('enabled', False):
            try:
                from .shelly_controller import ShellyController
                self.actuators.append(ShellyController(config.actuators.shelly))
                logger.info("Shelly actuator loaded")
            except Exception as e:
                logger.warning(f"Shelly actuator not available: {e}")

        if config.actuators.tuya_purifier.get('enabled', False):
            try:
                from .tuya_purifier import TuyaPurifier
                self.actuators.append(TuyaPurifier(config.actuators.tuya_purifier))
                logger.info("Tuya purifier actuator loaded")
            except Exception as e:
                logger.warning(f"Tuya purifier actuator not available: {e}")

    def update(self, data: Dict[str, Any]):
        """Update actuators based on sensor data."""
        for actuator in self.actuators:
            actuator.update(data)