import os, time, threading, logging, subprocess, wave, struct, random, signal
import numpy as np
from flask import Flask, render_template, jsonify, request
from ..config import config
from ..logger import logger

logging.getLogger('werkzeug').setLevel(logging.ERROR)

base_dir     = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(base_dir, 'templates')
AUDIO_DIR    = os.path.normpath(os.path.join(base_dir, '..', '..', 'audio'))

app = Flask(__name__, template_folder=template_dir)

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
_ac_cfg           = config.processors.audio_comfort
TEMPO_CHECK_SEC   = int(_ac_cfg.get('check_duration',   10))
TEMPO_COMFORT_SEC = int(_ac_cfg.get('comfort_duration', 300))
CALIBRATION_SECS  = 10
RATE              = 16000
CHUNK             = 1024
NUM_BARS          = 48

# ---------------------------------------------------------------------------
# Stato globale
# ---------------------------------------------------------------------------
state = {
    "active":       False,
    "mode":         "IDLE",
    "countdown":    0,
    "level":        0.0,
    "th_tol":       45.0,
    "th_crit":      65.0,
    "volume":       0.5,
    "spectrum":     [0] * NUM_BARS,
    "comfort_mode": "white_noise",
    "audio_file":   "",
    "air_quality":  {"temp": "--", "humidity": "--", "co2": "--", "iaqi": "--"},
}

# ---------------------------------------------------------------------------
# Audio processor — UN SOLO STREAM pyaudio (input+output), come il vecchio codice
# ---------------------------------------------------------------------------
_audio_thread  = None
_audio_running = False
_stream        = None
_pa            = None
_mic_sensor    = None   # usato solo per lettura esterna se stream non disponibile

# Bande FFT logaritmiche
def _log_bins(n, rate, chunk):
    fmin, fmax = 40.0, rate / 2.0 * 0.9
    edges = np.logspace(np.log10(fmin), np.log10(fmax), n + 1)
    freqs = np.fft.rfftfreq(chunk, d=1.0 / rate)
    bins  = []
    for i in range(n):
        lo = int(np.searchsorted(freqs, edges[i]))
        hi = int(np.searchsorted(freqs, edges[i + 1]))
        hi = max(hi, lo + 1)
        bins.append((min(lo, len(freqs)-1), min(hi, len(freqs))))
    return bins

_BINS = _log_bins(NUM_BARS, RATE, CHUNK)

def _audio_loop(device_index):
    """
    Thread unico: legge microfono e scrive rumore bianco sullo stesso stream.
    Durante COMFORT con file audio, scrive silenzio (casse gestite da mpg123).
    """
    global _audio_running, _stream, _pa
    try:
        import pyaudio
        _pa = pyaudio.PyAudio()
        kw  = dict(format=pyaudio.paInt16, channels=1, rate=RATE,
                   input=True, output=False, frames_per_buffer=CHUNK)
        if device_index is not None:
            kw['input_device_index'] = int(device_index)
        _stream = _pa.open(**kw)
        logger.info(f"Stream audio aperto (device={device_index}, rate={RATE})")
    except Exception as e:
        logger.error(f"Impossibile aprire stream audio: {e}")
        _audio_running = False
        return

    while _audio_running:
        try:
            # 1. Leggi microfono
            raw  = _stream.read(CHUNK, exception_on_overflow=False)
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)

            # 2. Calcola dB
            rms = np.sqrt(np.mean(data ** 2))
            db  = float(20 * np.log10(rms / 32768.0) + 95) if rms > 0 else 0.0
            state["level"] = round(max(0.0, db), 1)

            # 3. FFT spettro
            fft_mag  = np.abs(np.fft.rfft(data * np.hanning(len(data))))
            spectrum = []
            for lo, hi in _BINS:
                val = float(np.mean(fft_mag[lo:hi]))
                val = 0.0 if (np.isnan(val) or np.isinf(val)) else val
                spectrum.append(min(100, int(val / 400)))
            state["spectrum"] = spectrum

            # Output audio gestito da aplay/mpg123 — nessuna scrittura su stream

        except Exception as e:
            logger.warning(f"Audio loop: {e}")
            time.sleep(0.05)

    # cleanup
    try:
        _stream.stop_stream()
        _stream.close()
        _pa.terminate()
        logger.info("Stream audio chiuso")
    except Exception:
        pass

def _start_audio_thread(device_index=None):
    global _audio_thread, _audio_running
    if _audio_thread and _audio_thread.is_alive():
        return
    _audio_running = True
    _audio_thread  = threading.Thread(
        target=_audio_loop, args=(device_index,), daemon=True, name="AudioLoop"
    )
    _audio_thread.start()

# ---------------------------------------------------------------------------
# File audio (mpg123) — solo per comfort_mode == "file"
# ---------------------------------------------------------------------------
_file_proc = None
_file_lock = threading.Lock()

def _make_white_noise_wav():
    """Genera /tmp/wn.wav una volta sola — 10s rumore bianco 16kHz mono."""
    path = '/tmp/wn.wav'
    if os.path.isfile(path):
        return path
    rate, dur = 16000, 10
    amp = int(32767 * 0.20)
    samples = [random.randint(-amp, amp) for _ in range(rate * dur)]
    with wave.open(path, 'w') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f'<{len(samples)}h', *samples))
    logger.info("File rumore bianco generato: /tmp/wn.wav")
    return path

def _start_file_audio():
    global _file_proc
    with _file_lock:
        _stop_file_audio_unsafe()
        fn = state.get("audio_file", "")
        fp = os.path.join(AUDIO_DIR, fn)
        if not fn or not os.path.isfile(fp):
            logger.error(f"File audio non trovato: {fp}")
            return
        vol_factor = int(32768 * state.get("volume", 0.5))
        for cmd in [["mpg123", "-q", "--loop", "-1", "-f", str(vol_factor), fp],
                    ["cvlc", "--loop", "--quiet", fp]]:
            try:
                _file_proc = subprocess.Popen(
                    cmd, stderr=subprocess.DEVNULL, start_new_session=True
                )
                logger.info(f"File audio avviato: {fn}")
                return
            except FileNotFoundError:
                continue
        logger.error("mpg123/vlc non trovati — installa: sudo apt install mpg123")

def _start_white_noise():
    global _file_proc
    with _file_lock:
        _stop_file_audio_unsafe()
        wav = _make_white_noise_wav()
        vol_factor = int(32768 * state.get("volume", 0.5))
        # Prima prova mpg123 (loop nativo, volume controllabile, processo singolo)
        try:
            _file_proc = subprocess.Popen(
                ["mpg123", "-q", "--loop", "-1", "-f", str(vol_factor), wav],
                stderr=subprocess.DEVNULL, start_new_session=True
            )
            logger.info("Rumore bianco avviato (mpg123)")
            return
        except FileNotFoundError:
            pass
        # Fallback: bash loop con aplay — start_new_session per killare tutto il process group
        try:
            _file_proc = subprocess.Popen(
                ["bash", "-c", f"while true; do aplay -q '{wav}'; done"],
                stderr=subprocess.DEVNULL, start_new_session=True
            )
            logger.info("Rumore bianco avviato (aplay loop)")
        except Exception as e:
            logger.error(f"Impossibile avviare rumore bianco: {e}")

def _stop_file_audio_unsafe():
    global _file_proc
    if _file_proc and _file_proc.poll() is None:
        # Killa l'intero process group (fix: bash non propaga SIGTERM ad aplay)
        try:
            os.killpg(os.getpgid(_file_proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            _file_proc.terminate()
        try:
            _file_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(_file_proc.pid), signal.SIGKILL)
            except Exception:
                _file_proc.kill()
        _file_proc = None
        logger.info("File audio fermato")

def _stop_file_audio():
    with _file_lock:
        _stop_file_audio_unsafe()

# ---------------------------------------------------------------------------
# Loop logica CHECK → COMFORT (stesso schema del vecchio codice)
# ---------------------------------------------------------------------------
_logic_thread    = None
_logic_stop_flag = threading.Event()

def _logic_loop():
    logger.info(f"Logic loop START — check={TEMPO_CHECK_SEC}s comfort={TEMPO_COMFORT_SEC}s")

    while not _logic_stop_flag.is_set():

        # ===== CHECK =====
        state["mode"]   = "CHECK"
        needs_comfort   = False

        for i in range(TEMPO_CHECK_SEC, 0, -1):
            if _logic_stop_flag.is_set(): break
            state["countdown"] = i
            if state["level"] >= state["th_crit"]:
                needs_comfort = True
                break  # soglia superata → interrompe subito il CHECK
            time.sleep(1)

        if _logic_stop_flag.is_set(): break

        if not needs_comfort:
            logger.info("CHECK: sotto soglia → riparto")
            continue

        # ===== COMFORT =====
        logger.info(f"COMFORT avviato ({state['comfort_mode']}) per {TEMPO_COMFORT_SEC}s")
        state["mode"] = "COMFORT"

        if state["comfort_mode"] == "file":
            _start_file_audio()
        else:
            _start_white_noise()

        for i in range(TEMPO_COMFORT_SEC, 0, -1):
            if _logic_stop_flag.is_set(): break
            state["countdown"] = i
            time.sleep(1)

        # Ferma sempre l'audio (sia file che white_noise usano _file_proc)
        _stop_file_audio()

        if _logic_stop_flag.is_set(): break

        logger.info("COMFORT terminato → torno CHECK")

    # cleanup
    _stop_file_audio()
    state["mode"]      = "IDLE"
    state["countdown"] = 0
    state["active"]    = False
    logger.info("Logic loop STOP")

def _start_logic():
    global _logic_thread
    _logic_stop_flag.clear()
    if _logic_thread and _logic_thread.is_alive():
        _logic_stop_flag.set()
        _logic_thread.join(timeout=5)
        _logic_stop_flag.clear()
    _logic_thread = threading.Thread(
        target=_logic_loop, daemon=True, name="LogicLoop"
    )
    _logic_thread.start()

def _stop_logic():
    _logic_stop_flag.set()
    _stop_file_audio()

# ---------------------------------------------------------------------------
# Route Flask
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/status')
def get_status():
    return jsonify(state)

@app.route('/toggle')
def toggle():
    if not state["active"]:
        state["active"] = True
        _start_logic()
        logger.info("▶ AVVIATO")
    else:
        state["active"] = False
        _stop_logic()
        logger.info("■ FERMATO")
    return jsonify({"status": "ok", "active": state["active"]})

# ---- Calibrazione asincrona ----
_cal_result = {"status": "idle"}

@app.route('/calibrate')
def calibrate():
    global _cal_result
    _cal_result = {"status": "running"}

    def _do():
        global _cal_result
        was_active = state["active"]
        if was_active:
            state["active"] = False
            _stop_logic()
            time.sleep(0.5)

        state["mode"] = "CALIBRATING"
        samples = []
        t_end   = time.time() + CALIBRATION_SECS
        while time.time() < t_end:
            db = state["level"]
            if db > 0:
                samples.append(db)
            time.sleep(0.1)

        state["mode"] = "IDLE"

        if not samples:
            _cal_result = {"error": "Nessun dato audio ricevuto."}
            return

        arr      = np.array(samples)
        avg      = float(np.mean(arr))
        std      = float(np.std(arr))
        new_tol  = round(avg + std * 1.5, 1)
        new_crit = round(avg + std * 3.0, 1)
        state["th_tol"]  = new_tol
        state["th_crit"] = new_crit
        logger.info(f"Calibrazione OK: avg={avg:.1f} tol={new_tol} crit={new_crit}")

        if was_active:
            state["active"] = True
            _start_logic()

        _cal_result = {"status": "ok", "avg": round(avg, 1),
                       "new_tol": new_tol, "new_crit": new_crit}

    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"status": "started"})

@app.route('/calibrate/result')
def calibrate_result():
    return jsonify(_cal_result)

@app.route('/set_comfort_mode', methods=['POST'])
def set_comfort_mode():
    d = request.get_json(silent=True) or {}
    m = d.get('mode', 'white_noise')
    if m in ('white_noise', 'file'):
        state["comfort_mode"] = m
        return jsonify({"status": "ok"})
    return jsonify({"status": "error"}), 400

@app.route('/set_audio_file', methods=['POST'])
def set_audio_file():
    d  = request.get_json(silent=True) or {}
    fn = d.get('filename', '')
    fp = os.path.join(AUDIO_DIR, fn)
    if fn and os.path.isfile(fp):
        state["audio_file"] = fn
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "File non trovato"}), 404

@app.route('/audio_files')
def audio_files():
    os.makedirs(AUDIO_DIR, exist_ok=True)
    files = sorted([f for f in os.listdir(AUDIO_DIR)
                    if f.lower().endswith(('.mp3', '.wav', '.ogg', '.flac'))])
    return jsonify({"files": files})

@app.route('/set_volume', methods=['POST', 'GET'])
def set_volume():
    vol = None
    if request.method == 'POST':
        vol = (request.get_json(silent=True) or {}).get('volume')
    else:
        vol = request.args.get('volume', type=float)
    if vol is not None:
        state["volume"] = max(0.0, min(1.0, float(vol)))
        return jsonify({"status": "ok", "volume": state["volume"]})
    return jsonify({"status": "error"}), 400

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
            logger.error(f"update_data: {e}")

    def run(self):
        os.makedirs(AUDIO_DIR, exist_ok=True)
        mic_cfg = config.sensors.microphone
        if mic_cfg.get('enabled', False):
            dev_idx = mic_cfg.get('device_index', None)
            _start_audio_thread(device_index=dev_idx)

        app.run(host=self.host, port=self.port,
                debug=False, use_reloader=False, threaded=True)
