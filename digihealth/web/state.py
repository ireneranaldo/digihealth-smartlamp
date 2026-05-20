"""Stato condiviso fra il thread sensori (scrittura) e il thread web (lettura).

Protetto da lock perche' aggiornato dal loop sensori e letto dalle richieste HTTP.
"""
import threading

_lock = threading.Lock()

_state = {
    "active": False,
    "mode": "IDLE",
    "countdown": 0,
    "level": 0,
    "th_tol": 40,
    "th_crit": 70,
    "volume": 0.5,
    "spectrum": [0] * 20,
    "air_quality": {
        "temp": "--",
        "humidity": "--",
        "co2": "--",
        "iaqi": "--",
    },
}


def snapshot() -> dict:
    """Copia coerente dello stato per la risposta /status."""
    with _lock:
        return dict(_state)


def update(**kwargs) -> None:
    with _lock:
        _state.update(kwargs)


def get(key, default=None):
    with _lock:
        return _state.get(key, default)
