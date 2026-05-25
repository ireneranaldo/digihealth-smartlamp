import time
import tinytuya
from typing import Dict, Any, Optional
from ..logger import logger


class TuyaAC:
    """Controls a Tuya-based air conditioner via local API.

    Turns on when temperature exceeds temp_on, turns off when it drops
    below temp_off (hysteresis). Avoids redundant commands by tracking
    current state.

    DPS map (common Tuya AC IR controllers):
      1  → power (bool)
      2  → temp_target (int, °C)
      5  → fan speed (str: 'auto','low','mid','high')
      19 → mode (str: 'c'=cool, 'h'=heat, 'd'=dry, 'f'=fan, 'a'=auto)
    """

    def __init__(self, config: Dict[str, Any]):
        self.temp_on = float(config.get('temp_on', 26))
        self.temp_off = float(config.get('temp_off', 24))
        self.temp_target = int(config.get('temp_target', 22))
        self.mode = config.get('mode', 'c')
        self.fan_speed = config.get('fan_speed', 'auto')
        self.temp_key = config.get('temp_key', 'TEMP-[C]')
        self._ip = config.get('ip', '?')
        self._is_on: Optional[bool] = None
        self._last_temp: Optional[float] = None
        self._override_until: float = 0.0  # forzatura da alert

        try:
            self.device = tinytuya.OutletDevice(
                config['device_id'],
                config['ip'],
                config['local_key']
            )
            self.device.set_version(3.4)
            logger.info(
                f"TuyaAC[{self._ip}]: pronto "
                f"— accensione>{self.temp_on}°C spegnimento<{self.temp_off}°C"
            )
        except Exception as e:
            self.device = None
            logger.warning(f"TuyaAC[{self._ip}]: init fallito: {e}")

    def force_on(self, hold_seconds: float):
        """Forza l'AC ON (in raffrescamento) da un alert, sospendendo il
        controllo autonomo per hold_seconds (vedi guardia in update())."""
        self._override_until = time.time() + max(0.0, hold_seconds)
        if self.device is None:
            return
        try:
            self.device.set_status({
                '1': True, '19': self.mode, '5': self.fan_speed, '2': self.temp_target,
            })
            self._is_on = True
            logger.info(f"TuyaAC[{self._ip}]: FORZATO ON da alert per {hold_seconds:.0f}s")
        except Exception as e:
            logger.warning(f"TuyaAC[{self._ip}]: errore force_on: {e}")

    def update(self, data: Dict[str, Any]):
        """Accende/spegne l'AC in base alla temperatura rilevata dai sensori."""
        if self.device is None:
            return

        # Override da alert attivo: non toccare, lascia il dispositivo forzato.
        if time.time() < self._override_until:
            return

        raw = data.get(self.temp_key)
        if raw is None:
            logger.debug(f"TuyaAC[{self._ip}]: chiave '{self.temp_key}' non trovata nei dati sensori")
            return

        try:
            temp = float(raw)
        except (ValueError, TypeError):
            return

        self._last_temp = temp

        # Isteresi: la soglia dipende dallo stato corrente
        if self._is_on:
            deve_accendersi = temp > self.temp_off
        else:
            deve_accendersi = temp > self.temp_on

        if deve_accendersi == self._is_on:
            return

        try:
            if deve_accendersi:
                payload = {
                    '1': True,
                    '19': self.mode,
                    '5': self.fan_speed,
                    '2': self.temp_target,
                }
                self.device.set_status({str(k): v for k, v in payload.items()})
                logger.info(f"TuyaAC[{self._ip}]: ACCESO — temp={temp}°C (>{self.temp_on}°C)")
            else:
                self.device.set_value('1', False)
                logger.info(f"TuyaAC[{self._ip}]: SPENTO — temp={temp}°C (<{self.temp_off}°C)")
            self._is_on = deve_accendersi
        except Exception as e:
            logger.warning(f"TuyaAC[{self._ip}]: errore comando: {e}")

    def get_status(self) -> dict:
        return {
            'is_on': self._is_on or False,
            'temp': self._last_temp,
            'temp_on': self.temp_on,
            'temp_off': self.temp_off,
            'temp_target': self.temp_target,
            'mode': self.mode,
            'override_active': time.time() < self._override_until,
        }
