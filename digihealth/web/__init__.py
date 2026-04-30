import os
import time
import threading
import logging
import numpy as np

from flask import Flask, render_template, jsonify, request
from ..config import config
from ..logger import logger

logging.getLogger('werkzeug').setLevel(logging.ERROR)

base_dir     = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(base_dir, 'templates')
app = Flask(__name__, template_folder=template_dir)

# ---------------------------------------------------------------------------
# Stato globale
# ---------------------------------------------------------------------------
state = {
    "active":        False,
    "mode":          "IDLE",
    "countdown":     0,
    "level":         0.0,
    "th_tol":        45.0,
    "th_crit":       65.0,
    "volume":        0.5,
    "spectrum":      [0] * 48,
    "needs_comfort": False,
    "air_quality": {
        "temp":     "--",
        "humidity": "--",
        "co2":      "--",
        "iaqi":     "--",
    },
}

_comfort_event = threading.Event()
_logic_thread  = None
_mic_sensor    = None

_ac_cfg           = config.processors.audio_comfort
TEMPO_CHECK_SEC   = int(_ac_cfg.get('check_duration',   10))
TEMPO_COMFORT_SEC = int(_ac_cfg.get('comfort_duration', 300))
CALIBRATION_SECS  = 10

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read_mic():
    if _mic_sensor is not None:
        try:
            return _mic_sensor.read()
        except Exception as e:
            logger.warning(f"Errore lettura microfono: {e}")
    return {"audio_db": 0.0, "audio_spectrum": [0] * 48}

def _update_audio_state():
    data = _read_mic()
    state["level"]    = data.get("audio_db", 0.0)
    state["spectrum"] = data.get("audio_spectrum", [0] * 48)

# ---------------------------------------------------------------------------
# Thread logica CHECK / COMFORT
# ---------------------------------------------------------------------------
def _logic_loop():
    while True:
        if not state["active"]:
            time.sleep(0.5)
            continue

        state["mode"]          = "CHECK"
        state["needs_comfort"] = False
        _comfort_event.clear()

        for i in range(TEMPO_CHECK_SEC, 0, -1):
            if not state["active"]:
                state["mode"] = "IDLE"; state["countdown"] = 0; return
            state["countdown"] = i
            _update_audio_state()
            if state["level"] >= state["th_crit"]:
                state["needs_comfort"] = True
                _comfort_event.set()
            time.sleep(1)

        if not state["active"]:
            continue

        if _comfort_event.is_set():
            state["mode"] = "COMFORT"
            _start_white_noise_thread()
            for i in range(TEMPO_COMFORT_SEC, 0, -1):
                if not state["active"]:
                    _stop_white_noise()
                    state["mode"] = "IDLE"; state["countdown"] = 0; return
                state["countdown"] = i
                _update_audio_state()
                time.sleep(1)
            _stop_white_noise()

def _start_logic_thread():
    global _logic_thread
    if _logic_thread and _logic_thread.is_alive():
        return
    _logic_thread = threading.Thread(target=_logic_loop, daemon=True)
    _logic_thread.start()

# ---------------------------------------------------------------------------
# Rumore bianco
# ---------------------------------------------------------------------------
_noise_running = False
_noise_thread  = None

def _white_noise_loop():
    global _noise_running
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        CHUNK = 1024
        out_idx = config.sensors.microphone.get('output_device_index', None)
        kw = dict(format=pyaudio.paInt16, channels=1, rate=44100,
                  output=True, frames_per_buffer=CHUNK)
        if out_idx is not None:
            kw['output_device_index'] = int(out_idx)
        stream = p.open(**kw)
        logger.info("Rumore bianco avviato")
        while _noise_running and state["active"]:
            amp   = 0.08 * state["volume"]
            noise = np.random.uniform(-amp, amp, CHUNK)
            stream.write((noise * 32767).astype(np.int16).tobytes())
        stream.stop_stream(); stream.close(); p.terminate()
        logger.info("Rumore bianco fermato")
    except Exception as e:
        logger.error(f"Errore rumore bianco: {e}")

def _start_white_noise_thread():
    global _noise_thread, _noise_running
    if _noise_thread and _noise_thread.is_alive():
        return
    _noise_running = True
    _noise_thread  = threading.Thread(target=_white_noise_loop, daemon=True)
    _noise_thread.start()

def _stop_white_noise():
    global _noise_running
    _noise_running = False

# ---------------------------------------------------------------------------
# Route Flask
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/status')
def get_status():
    _update_audio_state()
    return jsonify(state)

@app.route('/toggle')
def toggle():
    state["active"] = not state["active"]
    if state["active"]:
        state["mode"] = "CHECK"
        state["needs_comfort"] = False
        _comfort_event.clear()
        _start_logic_thread()
        logger.info("AudioComfort ON")
    else:
        state["mode"] = "IDLE"
        state["countdown"] = 0
        _stop_white_noise()
        logger.info("AudioComfort OFF")
    return jsonify({"status": "ok", "active": state["active"]})

_cal_result = {"status": "idle"}

@app.route('/calibrate')
def calibrate():
    global _cal_result
    _cal_result = {"status": "running"}
    
    def _do_calibrate():
        global _cal_result
        was_active = state["active"]
        state["active"] = False
        _stop_white_noise()
        state["mode"] = "CALIBRATING"

        db_samples = []
        deadline = time.time() + CALIBRATION_SECS
        while time.time() < deadline:
            data = _read_mic()
            db = data.get("audio_db", 0.0)
            if db > 0:
                db_samples.append(db)
            time.sleep(0.1)

        state["mode"] = "IDLE"

        if not db_samples:
            _cal_result = {"error": "Nessun dato audio. Verifica il microfono."}
            return

        arr      = np.array(db_samples, dtype=float)
        avg      = float(np.mean(arr))
        std      = float(np.std(arr))
        new_tol  = round(avg + std * 1.5, 1)
        new_crit = round(avg + std * 3.0, 1)
        state["th_tol"]  = new_tol
        state["th_crit"] = new_crit

        if was_active:
            state["active"] = True
            state["mode"]   = "CHECK"
            _start_logic_thread()

        _cal_result = {"status": "ok", "avg": round(avg,1),
                       "new_tol": new_tol, "new_crit": new_crit}

    threading.Thread(target=_do_calibrate, daemon=True).start()
    return jsonify({"status": "started"})

@app.route('/calibrate/result')
def calibrate_result():
    return jsonify(_cal_result)
    
@app.route('/set_volume', methods=['POST', 'GET'])
def set_volume():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        vol  = data.get('volume')
    else:
        vol = request.args.get('volume', type=float)
    if vol is None:
        vol = request.args.get('level', type=float)
    if vol is not None:
        state["volume"] = max(0.0, min(1.0, float(vol)))
        return jsonify({"status": "ok", "volume": state["volume"]})
    return jsonify({"status": "error", "message": "Parametro 'volume' mancante"}), 400

@app.route('/shutdown_kiosk')
def shutdown_kiosk():
    os.system("pkill chromium")
    return "Closing..."

# ---------------------------------------------------------------------------
# WebManager
# ---------------------------------------------------------------------------
class WebManager:
    def __init__(self):
        self.host = config.web.host
        self.port = config.web.port

    def set_mic_sensor(self, mic):
        global _mic_sensor
        _mic_sensor = mic

    def get_status(self):
        return state

    def update_data(self, processed_data: dict):
        try:
            t = processed_data.get('TEMP-[C]')
            state["air_quality"] = {
                "temp":     round(float(t), 1) if t not in (None, '--') else '--',
                "humidity": processed_data.get('HUM-[%]', '--'),
                "co2":      processed_data.get('CO2-AnidrideCarbonica-[ppm]', '--'),
                "iaqi":     processed_data.get('IAQI', '--'),
            }
        except Exception as e:
            logger.error(f"Errore aggiornamento dati web: {e}")

    def run(self):
        mic_cfg = config.sensors.microphone
        if mic_cfg.get('enabled', False):
            try:
                from ..sensors.microphone import MicrophoneSensor
                mic = MicrophoneSensor(mic_cfg)
                if mic.is_available():
                    mic.start()
                    self.set_mic_sensor(mic)
                    logger.info("MicrophoneSensor avviato")
                else:
                    logger.warning("MicrophoneSensor: dispositivo non trovato")
            except Exception as e:
                logger.error(f"Impossibile avviare MicrophoneSensor: {e}")

        app.run(host=self.host, port=self.port, debug=False, use_reloader=False)
