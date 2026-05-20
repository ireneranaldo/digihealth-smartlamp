"""Modulo web: app factory Flask + WebManager.

Composto da blueprint separati:
  - dashboard.py : UI kiosk locale (/, /status, /toggle, /calibrate, /set_volume)
  - api.py       : ingestion esposta via Cloudflare Tunnel (/api/health, /api/alerts)
"""
import os
import logging
from flask import Flask
from ..config import config
from ..logger import logger
from . import state, storage

# Silenzia i log tecnici di Flask/werkzeug.
logging.getLogger("werkzeug").setLevel(logging.ERROR)

_base_dir = os.path.dirname(os.path.abspath(__file__))
_template_dir = os.path.join(_base_dir, "templates")


def create_app() -> Flask:
    app = Flask(__name__, template_folder=_template_dir)

    storage.init_db()

    from .dashboard import dashboard_bp
    from .api import api_bp
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp)

    return app


class WebManager:
    def __init__(self):
        self.host = config.web.host
        self.port = config.web.port
        self.app = create_app()

    def update_data(self, processed_data):
        """Aggiorna lo stato condiviso letto dalla dashboard."""
        try:
            air_quality = {
                "temp": round(processed_data.get("TEMP-[C]", 0), 1)
                if processed_data.get("TEMP-[C]") is not None else "--",
                "humidity": processed_data.get("HUM-[%]", "--"),
                "co2": processed_data.get("CO2-AnidrideCarbonica-[ppm]", "--"),
                "iaqi": processed_data.get("IAQI", "--"),
            }
            updates = {
                "level": processed_data.get("audio_level", 0),
                "air_quality": air_quality,
            }
            if "spectrum" in processed_data:
                updates["spectrum"] = processed_data["spectrum"]
            state.update(**updates)
        except Exception as e:
            logger.error(f"Errore nell'aggiornamento dei dati web: {e}")

    def run(self):
        self.app.run(
            host=self.host, port=self.port, debug=False,
            use_reloader=False, threaded=True,
        )
