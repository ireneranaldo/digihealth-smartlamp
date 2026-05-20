"""Dispatch delle azioni locali a partire dagli alert ricevuti.

VERSIONE v1 (STUB): non aziona ancora nessun dispositivo. Determina quali
attuatori sarebbero coinvolti (leggendoli da config.actuators) e logga
l'azione che verrebbe eseguita. Il wiring reale verso le API dei dispositivi
sara' aggiunto in un secondo momento, agganciandolo a `_execute`.
"""
from typing import Dict, List
from ..config import config
from ..logger import logger
from .schemas import NormalizedAlert


class ActionDispatcher:
    """Mappa un alert -> azione su uno o piu' dispositivi locali configurati."""

    def __init__(self):
        # Dispositivi disponibili: presi dalla configurazione (config.actuators).
        self.devices: Dict[str, dict] = {}
        for name in ("neopixel", "shelly"):
            cfg = getattr(config.actuators, name, {}) or {}
            if cfg.get("enabled", False):
                self.devices[name] = cfg
        logger.info(f"ActionDispatcher: dispositivi configurati = {list(self.devices.keys())}")

    def _targets_for(self, alert: NormalizedAlert) -> List[str]:
        """Quali dispositivi coinvolgere per questo alert.

        v1: coinvolge tutti i dispositivi abilitati. La mappatura fine
        action_code -> dispositivo verra' definita col wiring reale.
        """
        return list(self.devices.keys())

    def dispatch(self, alert: NormalizedAlert, alert_id: int) -> dict:
        """Gestisce l'alert. In v1 logga soltanto, senza azionare nulla."""
        targets = self._targets_for(alert)
        logger.info(
            "[STUB azione] alert id=%s code=%s level=%s metrica=%s valore=%s "
            "-> azione: %r | dispositivi target: %s (NON azionati - v1)",
            alert_id,
            alert.action_code,
            alert.level,
            alert.trigger_metric,
            alert.trigger_value,
            alert.recommended_action,
            targets or "(nessuno configurato)",
        )
        return {"dispatched": False, "stub": True, "targets": targets}

    def _execute(self, device: str, alert: NormalizedAlert):
        """Punto di aggancio futuro per l'azionamento reale del dispositivo.

        Qui andra' la chiamata all'API locale (es. Shelly via HTTP usando
        config.actuators.shelly.ip, o effetto sui NeoPixel). Non implementato in v1.
        """
        raise NotImplementedError("Azionamento dispositivi non ancora implementato (v1 stub)")
