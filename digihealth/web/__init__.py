import os 
from flask import Flask, render_template, jsonify, request
from ..config import config
from ..logger import logger
import logging

# Silenzia i log tecnici di Flask
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# Il resto del codice che abbiamo scritto prima...
base_dir = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(base_dir, 'templates')

# Inizializza Flask specificando esplicitamente dove sono i template
app = Flask(__name__, template_folder=template_dir)


# Stato globale che verrà consumato dall'HTML tramite le chiamate /status
state = {
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
        "iaqi": "--"
    }
}

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/status')
def get_status():
    return jsonify(state)

@app.route('/toggle')
def toggle():
    state["active"] = not state["active"]
    state["mode"] = "CHECK" if state["active"] else "IDLE"
    logger.info(f"Switch Web: {'ON' if state['active'] else 'OFF'}")
    return jsonify({"status": "ok", "active": state["active"]})

@app.route('/calibrate')
def calibrate():
    state["mode"] = "CALIBRATION"
    logger.info("Richiesta calibrazione da interfaccia Web")
    return jsonify({"status": "ok"})

@app.route('/set_volume')
def set_volume():
    # Prende il valore 'level' dall'URL (es: /set_volume?level=50)
    vol = request.args.get('level', type=int)
    if vol is not None:
        state["volume"] = vol
        logger.info(f"Volume impostato via Web a: {vol}")
        return jsonify({"status": "ok", "volume": vol})
    return jsonify({"status": "error", "message": "Valore mancante"}), 400



class WebManager:
    def __init__(self):
        self.host = config.web.host
        self.port = config.web.port

    def update_data(self, processed_data):
            try:
                # Livello audio principale
                state["level"] = processed_data.get('audio_level', 0)
                
                # Estraiamo i dati usando i nomi esatti che arrivano dal sensore
                state["air_quality"] = {
                    "temp": round(processed_data.get('TEMP-[C]', '--'),1),
                    "humidity": processed_data.get('HUM-[%]', '--'),
                    "co2": processed_data.get('CO2-AnidrideCarbonica-[ppm]', '--'),
                    "iaqi": processed_data.get('IAQI', 200)
                }
                
                # Aggiorna lo spettro (se hai i dati, altrimenti lascialo vuoto o simulato)
                if 'spectrum' in processed_data:
                    state["spectrum"] = processed_data['spectrum']
                    
            except Exception as e:
                logger.error(f"Errore nell'aggiornamento dei dati web: {e}")

    def run(self):
        app.run(host=self.host, port=self.port, debug=False, use_reloader=False)
