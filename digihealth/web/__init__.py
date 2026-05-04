import os, time, threading, logging, subprocess, wave, re, signal
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
# Costanti — lette dal config YAML
# ---------------------------------------------------------------------------
_ac_cfg           = config.processors.audio_comfort
TEMPO_CHECK_SEC   = int(_ac_cfg.get('check_duration',   10))
TEMPO_COMFORT_SEC = int(_ac_cfg.get('comfort_duration', 300))
IDLE_WAIT_SEC     = 60
CALIBRATION_SECS  = 10
RATE              = 16000
CHUNK             = 1024
NUM_BARS          = 48

# ---------------------------------------------------------------------------
# Stato globale condiviso con Flask
# ---------------------------------------------------------------------------
state = {
    "active":         False,
    "mode":           "IDLE",        # IDLE | CALIBRATING | CHECK | IDLE_WAIT | COMFORT
    "countdown":      0,
    "level":          0.0,
    "th_tol":   float(_ac_cfg.get('tolerance_threshold', 45.0)),
    "th_crit":  float(_ac_cfg.get('critical_threshold',  65.0)),
    "volume":         0.5,
    "spectrum":       [0] * NUM_BARS,
    "comfort_mode":   "pink_noise",  # pink_noise | file
    "audio_file":     "",
    "air_quality":    {"temp": "--", "humidity": "--", "co2": "--", "iaqi": "--"},
    "noise_detected": False,
    "fft_active":     True,
}

# ---------------------------------------------------------------------------
# Bande FFT logaritmiche (48 barre, 40 Hz – 7.2 kHz)
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Pink Noise — generato via filtro 1/f nel dominio delle frequenze (NumPy)
# Più profondo e naturale del rumore bianco, ideale per mascherare il rumore.
# ---------------------------------------------------------------------------
def _generate_pink_noise_wav():
    """Genera /tmp/pink.wav (10s, 16 kHz, mono, int16) usando filtro 1/f FFT."""
    path = '/tmp/pink.wav'
    if os.path.isfile(path):
        return path
    n     = RATE * 10
    white = np.random.randn(n)
    fft_w = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n)
    freqs[0] = 1e-6                              # evita boost infinito alla DC
    pink  = np.fft.irfft(fft_w / np.sqrt(np.abs(freqs)), n=n)
    pcm   = (pink / (np.max(np.abs(pink)) + 1e-9) * 0.15 * 32767).astype(np.int16)
    with wave.open(path, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(pcm.tobytes())
    logger.info("Pink noise generato: /tmp/pink.wav")
    return path

# ---------------------------------------------------------------------------
# Gestione processo audio di output (mpg123 / aplay in loop)
# ---------------------------------------------------------------------------
_file_proc   = None
_file_lock   = threading.Lock()
_output_alsa = None   # es. "plughw:0,0" — rilevato all'avvio del processor


def _mpg123_cmd(filepath):
    vol = int(32768 * state.get("volume", 0.5))
    cmd = ["mpg123", "-q", "--loop", "-1", "-f", str(vol)]
    if _output_alsa:
        cmd += ["-o", "alsa", "-a", _output_alsa]
    cmd.append(filepath)
    return cmd


def _aplay_loop_cmd(filepath):
    parts = ["aplay", "-q"]
    if _output_alsa:
        parts += ["-D", _output_alsa]
    parts.append(f"'{filepath}'")
    return ["bash", "-c", f"while true; do {' '.join(parts)}; done"]


def _start_audio_output(filepath):
    """Avvia la riproduzione in loop. filepath può essere pink.wav o un file utente."""
    global _file_proc
    with _file_lock:
        _stop_audio_output_unsafe()
        if not os.path.isfile(filepath):
            logger.error(f"File non trovato: {filepath}")
            return
        for cmd in [_mpg123_cmd(filepath), _aplay_loop_cmd(filepath)]:
            try:
                _file_proc = subprocess.Popen(
                    cmd, stderr=subprocess.DEVNULL, start_new_session=True
                )
                logger.info(f"Audio output avviato: {os.path.basename(filepath)}")
                return
            except FileNotFoundError:
                continue
        logger.error("mpg123 e aplay non disponibili — sudo apt install mpg123")


def _stop_audio_output_unsafe():
    """Killa l'intero process group (bash non propaga SIGTERM ai figli)."""
    global _file_proc
    if _file_proc and _file_proc.poll() is None:
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
        logger.info("Audio output fermato")


def _stop_audio_output():
    with _file_lock:
        _stop_audio_output_unsafe()


# ---------------------------------------------------------------------------
# Audio Processor — FSM non-bloccante con time.time()
#
# Grafo degli stati:
#
#   IDLE ──[toggle ON]──────────────> CHECK
#        ──[calibrate]───────────────> CALIBRATING ──[10s]──> IDLE
#
#   CHECK ──[noise_detected]─────────> COMFORT ──[5min]──> CHECK
#         ──[silenzioso dopo 10s]─────> IDLE_WAIT ──[1min]──> CHECK
#
#   FFT: OFF in CALIBRATING e CHECK (max precisione dB)
#        ON  in IDLE, IDLE_WAIT, COMFORT (feedback visivo)
# ---------------------------------------------------------------------------
_proc_thread  = None
_proc_running = False
_cal_samples: list = []
_cal_result         = {"status": "idle"}


def _detect_output_alsa(pa, out_idx):
    try:
        info = pa.get_device_info_by_index(int(out_idx))
        m = re.search(r'hw:(\d+,\d+)', info.get('name', ''))
        if m:
            return f'plughw:{m.group(1)}'
    except Exception:
        pass
    return None


def audio_processor():
    """
    Thread principale audio. Loop bloccante a ~64ms (CHUNK/RATE).
    Gestisce mic input, calcolo dB/FFT, FSM comfort e output audio.
    """
    global _proc_running, _output_alsa, _cal_result, _cal_samples

    import pyaudio
    pa = pyaudio.PyAudio()

    # Rileva ALSA output device per mpg123/aplay
    mic_cfg = config.sensors.microphone
    out_idx = mic_cfg.get('output_device_index', None)
    if out_idx is not None:
        _output_alsa = _detect_output_alsa(pa, out_idx)
        logger.info(f"Output ALSA: {_output_alsa or 'non rilevato, uso default'}")

    # Apri stream microfono (input-only, blocking)
    in_idx = mic_cfg.get('device_index', None)
    in_kw  = dict(format=pyaudio.paInt16, channels=1, rate=RATE,
                  input=True, output=False, frames_per_buffer=CHUNK)
    if in_idx is not None:
        in_kw['input_device_index'] = int(in_idx)

    in_stream = None
    try:
        in_stream = pa.open(**in_kw)
        logger.info(f"Microfono aperto (device={in_idx}, rate={RATE})")
    except Exception as e:
        logger.error(f"Errore apertura microfono: {e}")
        _proc_running = False
        pa.terminate()
        return

    # Pre-genera il file pink noise (numpy, veloce)
    try:
        _generate_pink_noise_wav()
    except Exception as e:
        logger.warning(f"Generazione pink noise fallita: {e}")

    # ── Variabili locali FSM ───────────────────────────────────────────────
    last_mode   = None          # stato precedente per rilevare le transizioni
    phase_start = time.time()   # inizio della fase corrente
    noise_seen  = False         # flag: rumore rilevato durante CHECK

    while _proc_running:
        mode = state["mode"]
        now  = time.time()

        # ── Gestione transizioni di stato ──────────────────────────────────
        if mode != last_mode:
            phase_start = now
            noise_seen  = False

            # Lasciamo COMFORT → fermiamo l'audio output
            if last_mode == "COMFORT":
                _stop_audio_output()

            # Entriamo in COMFORT → avviamo l'audio output
            if mode == "COMFORT":
                comfort_file = '/tmp/pink.wav'
                if state["comfort_mode"] == "file":
                    fn = state.get("audio_file", "")
                    fp = os.path.join(AUDIO_DIR, fn)
                    if fn and os.path.isfile(fp):
                        comfort_file = fp
                    else:
                        logger.warning(f"File non trovato ({fn}), uso pink noise")
                _start_audio_output(comfort_file)

            logger.info(f"FSM: {last_mode} → {mode}")
            last_mode = mode

        elapsed = now - phase_start

        # ── Lettura microfono ──────────────────────────────────────────────
        db = 0.0
        try:
            raw  = in_stream.read(CHUNK, exception_on_overflow=False)
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            rms  = np.sqrt(np.mean(data ** 2))
            db   = float(20 * np.log10(rms / 32768.0) + 95) if rms > 0 else 0.0
            db   = round(max(0.0, db), 1)
            state["level"] = db

            # FFT spettro — disabilitata durante CHECK e CALIBRATING
            if state["fft_active"]:
                fft_mag  = np.abs(np.fft.rfft(data * np.hanning(len(data))))
                spectrum = []
                for lo, hi in _BINS:
                    val = float(np.mean(fft_mag[lo:hi]))
                    val = 0.0 if np.isnan(val) or np.isinf(val) else val
                    spectrum.append(min(100, int(val / 400)))
                state["spectrum"] = spectrum

        except Exception as e:
            logger.warning(f"Mic read: {e}")

        # ── FSM — logica degli stati ───────────────────────────────────────

        if mode == "IDLE":
            state["fft_active"] = True
            state["countdown"]  = 0

        elif mode == "CALIBRATING":
            # FFT OFF — massima priorità al calcolo dB preciso
            state["fft_active"]  = False
            state["countdown"]   = max(0, int(CALIBRATION_SECS - elapsed))
            if db > 0:
                _cal_samples.append(db)
            if elapsed >= CALIBRATION_SECS:
                if _cal_samples:
                    arr      = np.array(_cal_samples)
                    avg      = float(np.mean(arr))
                    new_tol  = round(avg + 10.0, 1)   # +10 dB sopra la media
                    new_crit = round(avg + 20.0, 1)   # +20 dB sopra la media
                    state["th_tol"]  = new_tol
                    state["th_crit"] = new_crit
                    logger.info(f"Calibra OK: media={avg:.1f} tol={new_tol} crit={new_crit}")
                    _cal_result = {"status": "ok", "avg": round(avg, 1),
                                   "new_tol": new_tol, "new_crit": new_crit}
                else:
                    logger.warning("Calibra: nessun campione ricevuto")
                    _cal_result = {"error": "Nessun campione audio ricevuto"}
                _cal_samples.clear()
                state["mode"]   = "IDLE"
                state["active"] = False

        elif mode == "CHECK":
            # FFT OFF — massima priorità al calcolo dB preciso
            state["fft_active"] = False
            state["countdown"]  = max(0, int(TEMPO_CHECK_SEC - elapsed))
            if db >= state["th_crit"]:
                noise_seen = True
            if elapsed >= TEMPO_CHECK_SEC:
                if noise_seen:
                    logger.info(f"CHECK: rumore rilevato ({db:.1f} dB ≥ {state['th_crit']} dB)")
                    state["noise_detected"] = True
                    state["mode"]           = "COMFORT"
                else:
                    logger.info("CHECK: ambiente silenzioso → IDLE_WAIT")
                    state["noise_detected"] = False
                    state["mode"]           = "IDLE_WAIT"

        elif mode == "IDLE_WAIT":
            # FFT ON — feedback visivo durante la pausa
            state["fft_active"] = True
            state["countdown"]  = max(0, int(IDLE_WAIT_SEC - elapsed))
            if elapsed >= IDLE_WAIT_SEC:
                logger.info("IDLE_WAIT terminato → CHECK")
                state["mode"] = "CHECK"

        elif mode == "COMFORT":
            # FFT ON — mostra lo spettro del rumore emesso/ambientale
            state["fft_active"] = True
            state["countdown"]  = max(0, int(TEMPO_COMFORT_SEC - elapsed))
            if elapsed >= TEMPO_COMFORT_SEC:
                logger.info("COMFORT terminato → CHECK")
                state["noise_detected"] = False
                state["mode"]           = "CHECK"

        # ── Stop forzato dall'utente (preme "Ferma") ──────────────────────
        if not state["active"] and mode not in ("IDLE", "CALIBRATING"):
            state["mode"]           = "IDLE"
            state["noise_detected"] = False
            state["countdown"]      = 0

    # ── Cleanup alla chiusura del thread ──────────────────────────────────
    _stop_audio_output()
    try:
        in_stream.stop_stream()
        in_stream.close()
        pa.terminate()
    except Exception:
        pass
    logger.info("Audio processor STOP")


def _start_processor():
    global _proc_thread, _proc_running
    if _proc_thread and _proc_thread.is_alive():
        return
    _proc_running = True
    _proc_thread  = threading.Thread(
        target=audio_processor, daemon=True, name="AudioProc"
    )
    _proc_thread.start()

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
        state["mode"]   = "CHECK"
        logger.info("▶ AVVIATO")
    else:
        state["active"] = False
        # La FSM rileverà active=False e transizionerà a IDLE al prossimo ciclo
        logger.info("■ FERMATO")
    return jsonify({"status": "ok", "active": state["active"]})


@app.route('/calibrate')
def calibrate():
    global _cal_result, _cal_samples
    _cal_samples.clear()
    _cal_result    = {"status": "running"}
    state["active"] = False   # ferma qualsiasi fase attiva
    state["mode"]   = "CALIBRATING"
    logger.info("Calibrazione avviata")
    return jsonify({"status": "started"})


@app.route('/calibrate/result')
def calibrate_result():
    return jsonify(_cal_result)


@app.route('/set_comfort_mode', methods=['POST'])
def set_comfort_mode():
    d = request.get_json(silent=True) or {}
    m = d.get('mode', 'pink_noise')
    if m in ('pink_noise', 'file'):
        state["comfort_mode"] = m
        return jsonify({"status": "ok"})
    return jsonify({"status": "error", "message": "Modalità non valida"}), 400


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

    def set_mic_sensor(self, _):
        pass  # mic gestito internamente da audio_processor

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
        if config.sensors.microphone.get('enabled', False):
            _start_processor()
        app.run(host=self.host, port=self.port,
                debug=False, use_reloader=False, threaded=True)
