import time
import datetime
import math
import board
import neopixel
from typing import Dict, Any
from ..logger import logger

class NeoPixelController:
    """Controls NeoPixel LED strip for IAQI and circadian lighting."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.pin = getattr(board, f"D{config.get('pin', 12)}")
        self.num_pixels = config.get('num_pixels', 144)
        self.iaqi_range = config.get('iaqi_range', [0, 79])
        self.circadian_range = config.get('circadian_range', [80, 143])

        self.pixels = neopixel.NeoPixel(
            self.pin,
            self.num_pixels,
            brightness=1.0,
            auto_write=False,
            pixel_order=neopixel.GRB
        )

        self.start_time = time.time()

    def update(self, data: Dict[str, Any]):
        """Update LED display based on sensor data."""
        try:
            iaqi = data.get('IAQI', 0)
            lux = data.get('lux-IntensitaLuminosa', 0)

            # Check time constraints
            if not self._is_active_time():
                self.pixels.fill((0, 0, 0))
                self.pixels.show()
                return

            # IAQI breathing effect
            color = self._get_iaqi_color(iaqi)
            self._set_iaqi_breathing(color)

            # Circadian lighting
            temp_k, brightness = self._calculate_circadian_light(lux)
            rgb = self._kelvin_to_rgb(temp_k)
            self._set_circadian_segment(rgb, brightness)

            self.pixels.show()

        except Exception as e:
            logger.error(f"Error updating NeoPixel: {e}")

    def _is_active_time(self) -> bool:
        """Check if current time is within active hours."""
        now = datetime.datetime.now()
        current_minutes = now.hour * 60 + now.minute
        start_minutes = 8 * 60 + 10  # 08:10
        end_minutes = 18 * 60 + 40  # 18:40
        return start_minutes <= current_minutes < end_minutes

    def _get_iaqi_color(self, iaqi: int) -> tuple:
        """Get color based on IAQI value."""
        if iaqi <= 25:
            return (0, 180, 255)  # Azzurro
        elif iaqi <= 50:
            return (0, 255, 0)    # Verde
        elif iaqi <= 100:
            return (255, 255, 0)  # Giallo
        elif iaqi <= 150:
            return (255, 140, 0)  # Arancio
        elif iaqi <= 170:
            return (255, 165, 0)  # Arancio scuro
        else:
            return (255, 0, 0)    # Rosso

    def _set_iaqi_breathing(self, color: tuple):
        """Set IAQI segment with breathing effect."""
        r, g, b = color
        t = time.time() - self.start_time
        factor = (math.sin(t * 0.05) + 1) / 2
        factor = 0.2 + 0.8 * factor
        limiter = 0.3

        for i in range(self.iaqi_range[0], self.iaqi_range[1] + 1):
            self.pixels[i] = (
                int(r * factor * limiter),
                int(g * factor * limiter),
                int(b * factor * limiter)
            )

    def _kelvin_to_rgb(self, temp_k: int) -> tuple:
        """Convert Kelvin temperature to RGB."""
        if temp_k >= 5000:
            return (255, 255, 255)  # Luce fredda
        else:
            return (255, 180, 100)  # Luce calda

    def _calculate_circadian_light(self, lux: float) -> tuple:
        """Calculate circadian light parameters."""
        hour = datetime.datetime.now().hour
        temp_k = 6500 if 7 <= hour < 16 else 2700
        brightness = 10  # Fixed for now
        return temp_k, brightness

    def _set_circadian_segment(self, rgb: tuple, brightness: float):
        """Set circadian lighting segment."""
        r, g, b = rgb
        factor = brightness / 100

        for i in range(self.circadian_range[0], self.circadian_range[1] + 1):
            self.pixels[i] = (
                int(r * factor),
                int(g * factor),
                int(b * factor)
            )

