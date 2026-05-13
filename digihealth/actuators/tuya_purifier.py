import tinytuya
from typing import Dict, Any, Optional
from ..logger import logger


class TuyaPurifier:
    """Controls a Tuya-based air purifier (PNI PTA200) via local API.

    Turns on when PM2.5 or CO2 exceeds configured limits,
    turns off when both are below. Avoids redundant commands
    by tracking current state.

    DPS map for PNI PTA200:
      1  → power (bool)
      3  → fan speed (str)
      5  → internal PM2.5 sensor (int)
    """

    def __init__(self, config: Dict[str, Any]):
        self.pm25_limit = config.get('pm25_limit', 25)
        self.co2_limit = config.get('co2_limit', 800)
        self._is_on: Optional[bool] = None
        self._last_pm25: Optional[float] = None
        self._last_co2: Optional[float] = None

        try:
            self.device = tinytuya.OutletDevice(
                config['device_id'],
                config['ip'],
                config['local_key']
            )
            self.device.set_version(3.3)
            logger.info(
                f"TuyaPurifier: pronto ({config['ip']}) "
                f"— soglie PM2.5>{self.pm25_limit} CO2>{self.co2_limit}"
            )
        except Exception as e:
            self.device = None
            logger.warning(f"TuyaPurifier: init fallito: {e}")

    def update(self, data: Dict[str, Any]):
        """Accende/spegne il purificatore in base a PM2.5 e CO2."""
        if self.device is None:
            return

        pm25 = data.get('PM2_5-Particolato-[µg/m^3]', 0)
        co2 = data.get('CO2-AnidrideCarbonica-[ppm]', 0)
        self._last_pm25 = pm25
        self._last_co2 = co2
        deve_accendersi = pm25 > self.pm25_limit or co2 > self.co2_limit

        # Legge lo stato reale dal dispositivo e logga i dati interni
        self._log_device_status()

        if deve_accendersi == self._is_on:
            return  # nessun cambio necessario

        try:
            if deve_accendersi:
                self.device.turn_on()
                logger.info(f"TuyaPurifier: ACCESO — PM2.5={pm25} CO2={co2}")
            else:
                self.device.turn_off()
                logger.info(f"TuyaPurifier: SPENTO — PM2.5={pm25} CO2={co2}")
            self._is_on = deve_accendersi
        except Exception as e:
            logger.warning(f"TuyaPurifier: errore comando: {e}")

    def get_status(self) -> dict:
        return {
            'is_on': self._is_on or False,
            'pm25': self._last_pm25,
            'co2': self._last_co2,
        }

    def _log_device_status(self):
        """Legge e logga lo stato interno del purificatore (opzionale)."""
        try:
            status = self.device.status()
            dps = status.get('dps', {})
            if dps:
                power = "ON" if dps.get('1') else "OFF"
                speed = dps.get('3', '?')
                pm25_interno = dps.get('5', '?')
                logger.debug(
                    f"TuyaPurifier stato: power={power} speed={speed} "
                    f"PM2.5_interno={pm25_interno}"
                )
        except Exception:
            logger.debug("TuyaPurifier: stato non disponibile")
