import tinytuya
from typing import Dict, Any, Optional
from ..logger import logger


class TuyaAC:
    """Controls a Tuya-based portable AC (Solight DAC-12000 / Portable AC-WBR3) via local API.

    Turns on when temperature exceeds `temp_on`; turns off when it drops below `temp_off`.
    Hysteresis between the two thresholds avoids rapid on/off cycling.

    DPS map verificata su Portable AC-WBR3 (product asbtgrmtbjt5nubd):
      1  → power (bool)
      2  → set temperature (int, °C)
      3  → current temperature read by AC (int, °C)
      5  → fan speed (str: "low" | "middle" | "high" | "auto")
      19 → mode (str: "c"=cold | "h"=heat | "d"=dry | "f"=fan | "a"=auto)
    """

    # DPS keys
    DPS_POWER = '1'
    DPS_TEMP_SET = '2'
    DPS_TEMP_CURRENT = '3'
    DPS_FAN = '5'
    DPS_MODE = '19'

    def __init__(self, config: Dict[str, Any]):
        self.temp_on = config.get('temp_on', 26)
        self.temp_off = config.get('temp_off', 24)
        self.temp_target = config.get('temp_target', 22)
        self.mode = config.get('mode', 'cold')
        self.fan_speed = config.get('fan_speed', 'auto')
        self.temp_key = config.get('temp_key', 'TEMP-[C]')
        self._is_on: Optional[bool] = None
        self._last_temp: Optional[float] = None

        try:
            self.device = tinytuya.OutletDevice(
                config['device_id'],
                config['ip'],
                config['local_key']
            )
            self.device.set_version(3.4)
            logger.info(
                f"TuyaAC: pronto ({config['ip']}) "
                f"— ON>{self.temp_on}°C OFF<{self.temp_off}°C target={self.temp_target}°C"
            )
        except Exception as e:
            self.device = None
            logger.warning(f"TuyaAC: init fallito: {e}")

    def update(self, data: Dict[str, Any]):
        """Accende/spegne il climatizzatore in base alla temperatura ambiente."""
        if self.device is None:
            return

        temp = data.get(self.temp_key)
        if temp is None:
            logger.debug(f"TuyaAC: chiave '{self.temp_key}' non trovata nei dati sensore")
            return
        self._last_temp = temp

        if self._is_on is None:
            deve_accendersi = temp > self.temp_on
        elif self._is_on:
            deve_accendersi = temp >= self.temp_off  # rimane acceso fino a temp_off
        else:
            deve_accendersi = temp > self.temp_on

        self._log_device_status()

        if deve_accendersi == self._is_on:
            return

        try:
            if deve_accendersi:
                self._turn_on()
                logger.info(f"TuyaAC: ACCESO — temp={temp}°C")
            else:
                self._turn_off()
                logger.info(f"TuyaAC: SPENTO — temp={temp}°C")
            self._is_on = deve_accendersi
        except Exception as e:
            logger.warning(f"TuyaAC: errore comando: {e}")

    def get_status(self) -> dict:
        return {
            'is_on': self._is_on or False,
            'temp': self._last_temp,
        }

    def _turn_on(self):
        self.device.set_multiple_values({
            self.DPS_POWER: True,
            self.DPS_MODE: self.mode,
            self.DPS_FAN: self.fan_speed,
            self.DPS_TEMP_SET: self.temp_target,
        })

    def _turn_off(self):
        self.device.set_value(self.DPS_POWER, False)

    def _log_device_status(self):
        try:
            status = self.device.status()
            dps = status.get('dps', {})
            if dps:
                power = "ON" if dps.get(self.DPS_POWER) else "OFF"
                mode = dps.get(self.DPS_MODE, '?')
                fan = dps.get(self.DPS_FAN, '?')
                t_set = dps.get(self.DPS_TEMP_SET, '?')
                t_cur = dps.get(self.DPS_TEMP_CURRENT, '?')
                logger.debug(
                    f"TuyaAC stato: power={power} mode={mode} fan={fan} "
                    f"set={t_set}°C current={t_cur}°C"
                )
        except Exception:
            logger.debug("TuyaAC: stato non disponibile")
