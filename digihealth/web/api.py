"""Blueprint API di ingestion, esposto su internet via Cloudflare Tunnel.

Riceve gli alert HTTP POST dal sistema esterno (predizione qualita' aria / CRM),
li autentica (API key), li valida, li salva su SQLite ed esegue l'azione locale
sugli attuatori tramite il dispatcher.
"""
import threading
from flask import Blueprint, jsonify, request
from ..logger import logger
from .auth import require_api_key
from .schemas import parse_alert, ValidationError
from . import storage
from .dispatcher import ActionDispatcher

api_bp = Blueprint("api", __name__, url_prefix="/api")

# Istanza condivisa: WebManager.set_actuator_manager() la collega agli attuatori.
dispatcher = ActionDispatcher()

# ID hardcoded della lampada gestita da questo Raspberry. Gli alert con
# campo "lampada" diverso vengono salvati ma non azionano gli attuatori.
EXPECTED_LAMPADA = "AS00000046"


@api_bp.route("/health")
def health():
    """Health check senza autenticazione (per monitor Cloudflare/uptime)."""
    return jsonify({"status": "ok"}), 200


@api_bp.route("/alerts", methods=["POST"])
@require_api_key
def receive_alert():
    """Riceve un alert, lo logga su SQLite e accoda l'azione locale."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "JSON body richiesto"}), 400

    try:
        alert = parse_alert(payload)
    except ValidationError as e:
        logger.warning(f"Alert non valido da {request.remote_addr}: {e}")
        return jsonify({"status": "error", "message": "Payload non valido", "details": e.errors()}), 400

    alert_id = storage.save_alert(alert)

    # Filtro dispositivo: ignoriamo gli alert destinati ad altre lampade.
    # Se il payload non specifica "lampada" lo lasciamo passare (compatibilita').
    incoming = (alert.lampada or "").strip()
    if incoming and incoming.upper() != EXPECTED_LAMPADA.upper():
        storage.mark_processed(alert_id, action_taken=f"ignored:device_mismatch({incoming})")
        logger.info(
            f"Alert id={alert_id} ignorato: lampada={incoming!r} "
            f"!= atteso {EXPECTED_LAMPADA!r}"
        )
        return jsonify({
            "status": "ignored",
            "reason": "device_mismatch",
            "id": alert_id,
        }), 200

    # Azione sugli attuatori in background: i comandi ai device Tuya via LAN
    # possono richiedere qualche secondo, ma al mittente rispondiamo subito 2xx.
    threading.Thread(
        target=dispatcher.dispatch, args=(alert, alert_id),
        daemon=True, name=f"dispatch-{alert_id}",
    ).start()

    logger.info(
        f"Alert ricevuto id={alert_id} type={alert.event_type} "
        f"level={alert.level} action={alert.action_code} da {request.remote_addr}"
    )
    return jsonify({"status": "received", "id": alert_id}), 200
