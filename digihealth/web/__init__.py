import os, time, threading, logging, subprocess, wave, re, signal
import multiprocessing
import queue as _queue_mod
from datetime import datetime
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
# Costanti
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
# Stato globale Flask — aggiornato dal queue-reader (main process)
# ---------------------------------------------------------------------------
state = {
    "active":         False,
    "mode":           "IDLE",
    "countdown":      0,
    "level":          0.0,
    "th_tol":   float(_ac_cfg.get('tolerance_threshold', 45.0)),
    "th_crit":  float(_ac_cfg.get('critical_threshold',  65.0)),
    "volume":         0.5,
    "spectrum":       [0] * NUM_BARS,
    "comfort_mode":   "pink_noise",
    "audio_file":     "",
    "air_quality":    {"temp": "--", "humidity": "--", "co2": "--", "iaqi": "--"},
    "noise_detected": False,
    "fft_active":     True,
}

# Canali IPC con il processo audio
_cmd_queue:  multiprocessing.Queue = None   # main → audio: comandi
_data_queue: multiprocessing.Queue = None   # audio → main: pacchetti status
_audio_proc: multiprocessing.Process = None
_cal_result = {"status": "idle"}

# ---------------------------------------------------------------------------
# Bande FFT logaritmiche
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
# Pink Noise — generato via filtro 1/f (NumPy), 10s, 16kHz mono
# ---------------------------------------------------------------------------
def _generate_pink_noise_wav():
    path = '/tmp/pink.wav'
    if os.path.isfile(path):
        return path
    n     = RATE * 10
    white = np.random.randn(n)
    fft_w = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n)
    freqs[0] = 1e-6
    pink  = np.fft.irfft(fft_w / np.sqrt(np.abs(freqs)), n=n)
    pcm   = (pink / (np.max(np.abs(pink)) + 1e-9) * 0.15 * 32767).astype(np.int16)
    with wave.open(path, 'w') as wf:
        wf.setnchannels(1); wf.setsampwidth(2)
        wf.setframerate(RATE); wf.writeframes(pcm.tobytes())
    logger.info("Pink noise generato: /tmp/pink.wav")
    return path

# ---------------------------------------------------------------------------
# Helpers output audio — girano SOLO nel processo audio
# ---------------------------------------------------------------------------
_file_proc   = None
_file_lock   = threading.Lock()
_output_alsa = None


def _mpg123_cmd(filepath, volume=0.5):
    vol = int(32768 * volume)
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


def _start_audio_output(filepath, volume=0.5):
    global _file_proc
    with _file_lock:
        _stop_audio_output_unsafe()
        if not os.path.isfile(filepath):
            logger.error(f"File non trovato: {filepath}")
            return
        for cmd in [_mpg123_cmd(filepath, volume), _aplay_loop_cmd(filepath)]:
            try:
                _file_proc = subprocess.Popen(
                    cmd, stderr=subprocess.DEVNULL, start_new_session=True
                )
                logger.info(f"Audio output avviato: {os.path.basename(filepath)}")
                return
            except FileNotFoundError:
                continue
        logger.error("mpg123/aplay non disponibili")


def _stop_audio_output_unsafe():
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


def _stop_audio_output():
    with _file_lock:
        _stop_audio_output_unsafe()


def _detect_output_alsa(pa, out_idx):
    try:
        info = pa.get_device_info_by_index(int(out_idx))
        m = re.search(r'hw:(\d+,\d+)', info.get('name', ''))
        if m:
            return f'plughw:{m.group(1)}'
    except Exception:
        pass
    return None

# ---------------------------------------------------------------------------
# Processo Audio isolato — FSM + PyAudio + comunicazione via Queue
#
# Riceve comandi da cmd_q: "start" | "stop" | "calibrate" | ("volume", v)
#                                                            | ("comfort_mode", m)
#                                                            | ("audio_file", f)
# Invia pacchetti a data_q ogni ~100ms:
#   {"db", "spectrum", "mode", "countdown", "active",
#    "th_tol", "th_crit", "noise_detected", "fft_active",
#    "comfort_mode", "cal_done"}
# ---------------------------------------------------------------------------
def _audio_process_fn(cmd_q, data_q, init_th_tol, init_th_crit):
    """
    Processo audio indipendente. Non dipende da Flask né dai sensori.
    FSM: IDLE → CHECK → COMFORT/IDLE_WAIT → CHECK  (loop)
         IDLE → CALIBRATING → IDLE
    """
    global _output_alsa

    import pyaudio

    # ── Stato locale FSM ──────────────────────────────────────────────────
    active       = False
    mode         = "IDLE"
    countdown    = 0
    volume       = 0.5
    comfort_mode = "pink_noise"
    audio_file   = ""
    th_tol       = init_th_tol
    th_crit      = init_th_crit
    noise_det    = False
    fft_active   = True
    spectrum     = [0] * NUM_BARS

    last_mode   = None
    phase_start = time.time()
    noise_seen  = False
    cal_samples: list = []
    last_send   = time.time()
    db          = 0.0

    # ── Setup PyAudio input ───────────────────────────────────────────────
    pa      = pyaudio.PyAudio()
    mic_cfg = config.sensors.microphone
    out_idx = mic_cfg.get('output_device_index', None)
    in_idx  = mic_cfg.get('device_index', None)

    _output_alsa = _detect_output_alsa(pa, out_idx) if out_idx is not None else None
    if _output_alsa:
        logger.info(f"[AudioProc] Output ALSA: {_output_alsa}")

    in_kw = dict(format=pyaudio.paInt16, channels=1, rate=RATE,
                 input=True, output=False, frames_per_buffer=CHUNK)
    if in_idx is not None:
        in_kw['input_device_index'] = int(in_idx)

    in_stream = None
    try:
        in_stream = pa.open(**in_kw)
        logger.info(f"[AudioProc] Microfono aperto (device={in_idx})")
    except Exception as e:
        logger.error(f"[AudioProc] Errore apertura microfono: {e}")
        pa.terminate()
        return

    try:
        _generate_pink_noise_wav()
    except Exception as e:
        logger.warning(f"[AudioProc] Pink noise fallito: {e}")

    # ── Loop principale ───────────────────────────────────────────────────
    while True:

        # 1. Leggi comandi dalla queue (non bloccante)
        try:
            while True:
                cmd = cmd_q.get_nowait()
                if cmd == "start":
                    active = True
                    mode   = "CHECK"
                elif cmd == "stop":
                    active = False
                elif cmd == "calibrate":
                    mode        = "CALIBRATING"
                    active      = False
                    cal_samples = []
                elif isinstance(cmd, tuple) and len(cmd) == 2:
                    k, v = cmd
                    if   k == "volume":       volume       = float(v)
                    elif k == "comfort_mode": comfort_mode = v
                    elif k == "audio_file":   audio_file   = v
        except _queue_mod.Empty:
            pass

        now     = time.time()

        # 2. Rilevamento cambio di stato (gestisce start/stop audio output)
        if mode != last_mode:
            phase_start = now
            noise_seen  = False
            if last_mode == "COMFORT":
                _stop_audio_output()
            if mode == "COMFORT":
                comfort_file = '/tmp/pink.wav'
                if comfort_mode == "file" and audio_file:
                    fp = os.path.join(AUDIO_DIR, audio_file)
                    comfort_file = fp if os.path.isfile(fp) else '/tmp/pink.wav'
                _start_audio_output(comfort_file, volume=volume)
            logger.info(f"[AudioProc] FSM: {last_mode} → {mode}")
            last_mode = mode

        elapsed = now - phase_start

        # 3. Lettura microfono (bloccante ~64ms — cadenza del loop)
        try:
            raw  = in_stream.read(CHUNK, exception_on_overflow=False)
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            rms  = np.sqrt(np.mean(data ** 2))
            db   = float(20 * np.log10(rms / 32768.0) + 95) if rms > 0 else 0.0
            db   = round(max(0.0, db), 1)
            if fft_active:
                fft_mag  = np.abs(np.fft.rfft(data * np.hanning(len(data))))
                spectrum = []
                for lo, hi in _BINS:
                    val = float(np.mean(fft_mag[lo:hi]))
                    val = 0.0 if np.isnan(val) or np.isinf(val) else val
                    spectrum.append(min(100, int(val / 400)))
        except Exception as e:
            logger.warning(f"[AudioProc] Mic read: {e}")

        # 4. FSM
        cal_done = None

        if mode == "IDLE":
            fft_active = True
            countdown  = 0

        elif mode == "CALIBRATING":
            fft_active = False
            countdown  = max(0, int(CALIBRATION_SECS - elapsed))
            if db > 0:
                cal_samples.append(db)
            if elapsed >= CALIBRATION_SECS:
                if cal_samples:
                    arr    = np.array(cal_samples)
                    avg    = float(np.mean(arr))
                    th_tol  = round(avg + 10.0, 1)
                    th_crit = round(avg + 20.0, 1)
                    cal_done = {"status": "ok", "avg": round(avg, 1),
                                "new_tol": th_tol, "new_crit": th_crit}
                    logger.info(f"[AudioProc] Calibra: avg={avg:.1f} tol={th_tol} crit={th_crit}")
                else:
                    cal_done = {"error": "Nessun campione audio"}
                cal_samples = []
                mode   = "IDLE"
                active = False

        elif mode == "CHECK":
            fft_active = False
            countdown  = max(0, int(TEMPO_CHECK_SEC - elapsed))
            if db >= th_crit:
                noise_seen = True
            if elapsed >= TEMPO_CHECK_SEC:
                if noise_seen:
                    noise_det = True
                    mode      = "COMFORT"
                else:
                    noise_det = False
                    mode      = "IDLE_WAIT"

        elif mode == "IDLE_WAIT":
            fft_active = True
            countdown  = max(0, int(IDLE_WAIT_SEC - elapsed))
            if elapsed >= IDLE_WAIT_SEC:
                mode = "CHECK"

        elif mode == "COMFORT":
            fft_active = True
            countdown  = max(0, int(TEMPO_COMFORT_SEC - elapsed))
            if elapsed >= TEMPO_COMFORT_SEC:
                noise_det = False
                mode      = "CHECK"

        # 5. Auto-stop alle 18:00
        if active and datetime.now().hour >= 18:
            logger.info("[AudioProc] Auto-stop: orario ≥ 18:00")
            active = False

        # 6. Force-stop (utente preme Ferma)
        if not active and mode not in ("IDLE", "CALIBRATING"):
            mode      = "IDLE"
            noise_det = False
            countdown = 0

        # 7. Invia pacchetto status al main process (~ogni 100ms)
        if now - last_send >= 0.1:
            try:
                data_q.put_nowait({
                    "db":             db,
                    "spectrum":       spectrum,
                    "mode":           mode,
                    "countdown":      countdown,
                    "active":         active,
                    "th_tol":         th_tol,
                    "th_crit":        th_crit,
                    "noise_detected": noise_det,
                    "fft_active":     fft_active,
                    "comfort_mode":   comfort_mode,
                    "cal_done":       cal_done,
                })
            except _queue_mod.Full:
                pass  # main process lento, scartiamo il frame
            last_send = now

# ---------------------------------------------------------------------------
# Queue reader — thread nel main process, aggiorna state da data_queue
# ---------------------------------------------------------------------------
def _queue_reader():
    global _cal_result
    while True:
        try:
            pkt = _data_queue.get(timeout=1.0)
            state["level"]          = pkt["db"]
            state["spectrum"]       = pkt["spectrum"]
            state["mode"]           = pkt["mode"]
            state["countdown"]      = pkt["countdown"]
            state["active"]         = pkt["active"]
            state["th_tol"]         = pkt["th_tol"]
            state["th_crit"]        = pkt["th_crit"]
            state["noise_detected"] = pkt["noise_detected"]
            state["fft_active"]     = pkt["fft_active"]
            state["comfort_mode"]   = pkt["comfort_mode"]
            if pkt.get("cal_done"):
                _cal_result = pkt["cal_done"]
        except (_queue_mod.Empty, Exception):
            pass


def _send_cmd(cmd):
    """Invia un comando al processo audio se è attivo."""
    if _cmd_queue is not None:
        try:
            _cmd_queue.put_nowait(cmd)
        except _queue_mod.Full:
            logger.warning(f"cmd_queue piena, comando {cmd} scartato")

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
        _send_cmd("start")
        logger.info("▶ Comando AVVIA inviato")
    else:
        _send_cmd("stop")
        logger.info("■ Comando FERMA inviato")
    return jsonify({"status": "ok"})


@app.route('/calibrate')
def calibrate():
    global _cal_result
    _cal_result = {"status": "running"}
    _send_cmd("calibrate")
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
        _send_cmd(("comfort_mode", m))
        return jsonify({"status": "ok"})
    return jsonify({"status": "error"}), 400


@app.route('/set_audio_file', methods=['POST'])
def set_audio_file():
    d  = request.get_json(silent=True) or {}
    fn = d.get('filename', '')
    fp = os.path.join(AUDIO_DIR, fn)
    if fn and os.path.isfile(fp):
        _send_cmd(("audio_file", fn))
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
        vol = max(0.0, min(1.0, float(vol)))
        state["volume"] = vol
        _send_cmd(("volume", vol))
        return jsonify({"status": "ok", "volume": vol})
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
        pass

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
        global _cmd_queue, _data_queue, _audio_proc
        os.makedirs(AUDIO_DIR, exist_ok=True)

        if config.sensors.microphone.get('enabled', False):
            _cmd_queue  = multiprocessing.Queue()
            _data_queue = multiprocessing.Queue(maxsize=10)

            # Thread che legge i pacchetti audio e aggiorna state
            threading.Thread(target=_queue_reader, daemon=True,
                             name="QueueReader").start()

            # Processo audio isolato
            _audio_proc = multiprocessing.Process(
                target=_audio_process_fn,
                args=(_cmd_queue, _data_queue,
                      state["th_tol"], state["th_crit"]),
                daemon=True,
                name="AudioProc",
            )
            _audio_proc.start()
            logger.info(f"Processo audio avviato (pid={_audio_proc.pid})")

        app.run(host=self.host, port=self.port,
                debug=False, use_reloader=False, threaded=True)
