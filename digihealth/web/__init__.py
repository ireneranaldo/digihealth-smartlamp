import os
import time
import threading
import logging
import subprocess
import numpy as np

from flask import Flask, render_template, jsonify, request
from ..config import config
from ..logger import logger

logging.getLogger('werkzeug').setLevel(logging.ERROR)

base_dir     = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(base_dir, 'templates')
# Cartella audio relativa alla root del progetto
AUDIO_DIR    = os.path.join(os.path.dirname(base_dir), '..', 'audio')
AUDIO_DIR    = os.path.normpath(AUDIO_DIR)

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
    "comfort_mode":  "white_noise",   # "white_noise" | "file"
    "audio_file":    "",              # nome file selezionato
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
# Helpers microfono
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
            # Avvia la sorgente audio scelta
            if state["comfort_mode"] == "file" and state["audio_file"]:
                _start_audio_file()
            else:
                _start_white_noise_thread()

            for i in range(TEMPO_COMFORT_SEC, 0, -1):
                if not state["active"]:
                    _stop_all_audio()
                    state["mode"] = "IDLE"; state["countdown"] = 0; return
                state["countdown"] = i
                _update_audio_state()
                time.sleep(1)

            _stop_all_audio()

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
        kw = dict(format=pyaudio.paInt16, channels=1, rate=16000,
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
# Riproduzione file audio (mpg123 in loop)
# ---------------------------------------------------------------------------
_audio_process = None

def _start_audio_file():
    global _audio_process
    _stop_audio_file()
    filename = state.get("audio_file", "")
    if not filename:
        return
    filepath = os.path.join(AUDIO_DIR, filename)
    if not os.path.isfile(filepath):
        logger.error(f"File audio non trovato: {filepath}")
        return
    try:
        # mpg123 --loop -1 = loop infinito
        # -q = quiet (nessun output su stdout)
        cmd = ["mpg123", "-q", "--loop", "-1", filepath]
        _audio_process = subprocess.Popen(cmd)
        logger.info(f"File audio avviato: {filename}")
    except FileNotFoundError:
        # mpg123 non installato, fallback su aplay per WAV o omxplayer
        try:
            cmd = ["cvlc", "--loop", "--quiet", filepath]
            _audio_process = subprocess.Popen(cmd)
            logger.info(f"File audio avviato con vlc: {filename}")
        except Exception as e2:
            logger.error(f"Impossibile riprodurre audio: {e2}")

def _stop_audio_file():
    global _audio_process
    if _audio_process and _audio_process.poll() is None:
        _audio_process.terminate()
        try:
            _audio_process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _audio_process.kill()
        _audio_process = None
        logger.info("File audio fermato")

def _stop_all_audio():
    _stop_white_noise()
    _stop_audio_file()

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
        _stop_all_audio()
        logger.info("AudioComfort OFF")
    return jsonify({"status": "ok", "active": state["active"]})

# --- Calibrazione asincrona ---
_cal_result = {"status": "idle"}

@app.route('/calibrate')
def calibrate():
    global _cal_result
    _cal_result = {"status": "running"}

    def _do_calibrate():
        global _cal_result
        was_active = state["active"]
        state["active"] = False
        _stop_all_audio()
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

        _cal_result = {"status": "ok", "avg": round(avg, 1),
                       "new_tol": new_tol, "new_crit": new_crit}

    threading.Thread(target=_do_calibrate, daemon=True).start()
    return jsonify({"status": "started"})

@app.route('/calibrate/result')
def calibrate_result():
    return jsonify(_cal_result)

# --- Impostazioni comfort ---
@app.route('/set_comfort_mode', methods=['POST'])
def set_comfort_mode():
    data = request.get_json(silent=True) or {}
    mode = data.get('mode', 'white_noise')
    if mode in ('white_noise', 'file'):
        state["comfort_mode"] = mode
        return jsonify({"status": "ok", "comfort_mode": mode})
    return jsonify({"status": "error"}), 400

@app.route('/set_audio_file', methods=['POST'])
def set_audio_file():
    data = request.get_json(silent=True) or {}
    filename = data.get('filename', '')
    filepath = os.path.join(AUDIO_DIR, filename)
    if filename and os.path.isfile(filepath):
        state["audio_file"] = filename
        return jsonify({"status": "ok", "audio_file": filename})
    return jsonify({"status": "error", "message": "File non trovato"}), 404

@app.route('/audio_files')
def audio_files():
    """Restituisce la lista dei file MP3/WAV nella cartella audio."""
    os.makedirs(AUDIO_DIR, exist_ok=True)
    files = [
        f for f in os.listdir(AUDIO_DIR)
        if f.lower().endswith(('.mp3', '.wav', '.ogg', '.flac'))
    ]
    files.sort()
    return jsonify({"files": files})

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
        os.makedirs(AUDIO_DIR, exist_ok=True)
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

        app.run(host=self.host, port=self.port, debug=False,
                use_reloader=False, threaded=True)
