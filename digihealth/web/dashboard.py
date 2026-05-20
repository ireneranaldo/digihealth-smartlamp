"""Blueprint della dashboard locale (kiosk).

Gli endpoint che modificano lo stato sono in POST (non piu' GET) per evitare
attivazioni accidentali via URL/crawler. Questi endpoint sono pensati per la
LAN/kiosk: il Cloudflare Tunnel deve esporre pubblicamente solo /api/* (vedi
docker/cloudflared o systemd/cloudflared). L'endpoint /shutdown_kiosk e' stato
rimosso perche' eseguiva pkill senza alcun controllo.
"""
from flask import Blueprint, render_template, jsonify, request
from ..logger import logger
from . import state

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def index():
    return render_template("dashboard.html")


@dashboard_bp.route("/status")
def get_status():
    return jsonify(state.snapshot())


@dashboard_bp.route("/toggle", methods=["POST"])
def toggle():
    active = not state.get("active", False)
    state.update(active=active, mode="CHECK" if active else "IDLE")
    logger.info(f"Switch Web: {'ON' if active else 'OFF'}")
    return jsonify({"status": "ok", "active": active})


@dashboard_bp.route("/calibrate", methods=["POST"])
def calibrate():
    state.update(mode="CALIBRATION")
    logger.info("Richiesta calibrazione da interfaccia Web")
    return jsonify({"status": "ok"})


@dashboard_bp.route("/set_volume", methods=["POST"])
def set_volume():
    data = request.get_json(silent=True) or {}
    vol = data.get("volume", request.args.get("level", type=float))
    if vol is None:
        return jsonify({"status": "error", "message": "Valore mancante"}), 400
    state.update(volume=vol)
    logger.info(f"Volume impostato via Web a: {vol}")
    return jsonify({"status": "ok", "volume": vol})
