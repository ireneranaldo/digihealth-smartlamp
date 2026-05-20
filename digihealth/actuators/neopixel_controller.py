import time
import datetime
import math
from typing import Dict, Any
from ..logger import logger

class NeoPixelController:

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.num_pixels = config.get('num_pixels', 144)
        self.iaqi_range = config.get('iaqi_range', [0, 79])
        self.circadian_range = config.get('circadian_range', [80, 143])
        self.start_time = time.time()
        self.pixels = None
        self._last_color_hex = '#000000'
        self._last_iaqi = 0
        self._active = False
        self._alert_until: float = 0.0          # forzatura colore da alert
        self._alert_color: tuple = (255, 0, 0)

        try:
            import board
            import neopixel
            pin = getattr(board, f"D{config.get('pin', 12)}")
            self.pixels = neopixel.NeoPixel(
                pin, self.num_pixels,
                brightness=1.0, auto_write=False,
                pixel_order=neopixel.GRB
            )
            logger.info("NeoPixel inizializzato correttamente")
        except Exception as e:
            logger.error(f"NeoPixel non disponibile (permessi?): {e}")
            logger.warning("LED disabilitati — il resto del sistema continua normalmente")

    def set_alert(self, color: tuple, hold_seconds: float):
        """Forza un colore di allarme da un alert, per hold_seconds.
        L'effetto IAQI/circadiano riprende alla scadenza (vedi update())."""
        self._alert_color = color
        self._alert_until = time.time() + max(0.0, hold_seconds)
        self._render_alert()
        logger.info(f"NeoPixel: ALERT colore {self._alert_color} per {hold_seconds:.0f}s")

    def _render_alert(self):
        if self.pixels is None:
            return
        try:
            self.pixels.fill(self._alert_color)
            self.pixels.show()
            self._active = True
            self._last_color_hex = '#{:02x}{:02x}{:02x}'.format(*self._alert_color)
        except Exception as e:
            logger.error(f"NeoPixel set_alert: {e}")

    def update(self, data: Dict[str, Any]):
        if self.pixels is None:
            return  # nessun crash, sistema continua

        # Override da alert attivo: mostra il colore di allarme e salta il resto.
        if time.time() < self._alert_until:
            self._render_alert()
            return

        try:
            iaqi = data.get('IAQI', 0)
            lux  = data.get('lux-IntensitaLuminosa', 0)
            self._last_iaqi = iaqi

            if not self._is_active_time():
                self.pixels.fill((0, 0, 0))
                self.pixels.show()
                self._active = False
                self._last_color_hex = '#000000'
                return

            color = self._get_iaqi_color(iaqi)
            self._active = True
            self._last_color_hex = '#{:02x}{:02x}{:02x}'.format(*color)
            self._set_iaqi_breathing(color)

            temp_k, brightness = self._calculate_circadian_light(lux)
            rgb = self._kelvin_to_rgb(temp_k)
            self._set_circadian_segment(rgb, brightness)

            self.pixels.show()

        except Exception as e:
            logger.error(f"Error updating NeoPixel: {e}")

    def _is_active_time(self) -> bool:
        now = datetime.datetime.now()
        current_minutes = now.hour * 60 + now.minute
        start_minutes = 8 * 60 + 10   # 08:10
        end_minutes   = 18 * 60 + 40  # 18:40
        return start_minutes <= current_minutes < end_minutes

    def _get_iaqi_color(self, iaqi: int) -> tuple:
        if iaqi <= 25:  return (0, 180, 255)
        elif iaqi <= 50:  return (0, 255, 0)
        elif iaqi <= 100: return (255, 255, 0)
        elif iaqi <= 150: return (255, 140, 0)
        elif iaqi <= 170: return (255, 165, 0)
        else:             return (255, 0, 0)

    def _set_iaqi_breathing(self, color: tuple):
        r, g, b = color
        factor = 0.2 + 0.8 * (math.sin((time.time() - self.start_time) * 0.05) + 1) / 2
        limiter = 0.3
        for i in range(self.iaqi_range[0], self.iaqi_range[1] + 1):
            self.pixels[i] = (int(r*factor*limiter), int(g*factor*limiter), int(b*factor*limiter))

    def _kelvin_to_rgb(self, temp_k: int) -> tuple:
        return (255, 255, 255) if temp_k >= 5000 else (255, 180, 100)

    def _calculate_circadian_light(self, lux: float) -> tuple:
        temp_k = 6500 if 7 <= datetime.datetime.now().hour < 16 else 2700
        return temp_k, 10

    def get_status(self) -> dict:
        return {
            'available': self.pixels is not None,
            'active': self._active,
            'color_hex': self._last_color_hex,
            'iaqi': self._last_iaqi,
        }

    def _set_circadian_segment(self, rgb: tuple, brightness: float):
        r, g, b = rgb
        factor = brightness / 100
        for i in range(self.circadian_range[0], self.circadian_range[1] + 1):
            self.pixels[i] = (int(r*factor), int(g*factor), int(b*factor))
