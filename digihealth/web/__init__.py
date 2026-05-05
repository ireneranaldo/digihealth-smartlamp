import os, threading, logging
import multiprocessing
import queue as _q
from flask import Flask, render_template, jsonify, request
from ..config import config
from ..logger import logger
from ..audio_worker import audio_process_fn

logging.getLogger('werkzeug').setLevel(logging.ERROR)

base_dir     = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(base_dir, 'templates')
AUDIO_DIR    = os.path.normpath(os.path.join(base_dir, '..', '..', 'audio'))

app = Flask(__name__, template_folder=template_dir)

# ── Config audio ──────────────────────────────────────────────────────────────
_ac_cfg = config.processors.audio_comfort
NUM_BARS = 48

# ── Stato Flask (aggiornato dal queue-reader) ─────────────────────────────────
state = {
    "active":         False,
    "mode":           "IDLE",
    "countdown":      0,
    "level":          0.0,
    "th_tol":         float(_ac_cfg.get('tolerance_threshold', 45.0)),
    "th_crit":        float(_ac_cfg.get('critical_threshold',  65.0)),
    "volume":         0.5,
    "spectrum":       [0] * NUM_BARS,
    "comfort_mode":   "pink_noise",
    "audio_file":     "",
    "air_quality":    {"temp": "--", "humidity": "--", "co2": "--", "iaqi": "--"},
    "noise_detected": False,
    "fft_active":     True,
}

# ── IPC ───────────────────────────────────────────────────────────────────────
_cmd_queue:  multiprocessing.Queue   = None
_data_queue: multiprocessing.Queue   = None
_audio_proc: multiprocessing.Process = None
_cal_result = {"status": "idle"}


# ── Queue reader (thread nel processo principale) ─────────────────────────────
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
        except (_q.Empty, Exception):
            pass


def _send_cmd(cmd):
    if _cmd_queue is not None:
        try:
            _cmd_queue.put_nowait(cmd)
        except _q.Full:
            logger.warning(f"cmd_queue piena: {cmd}")


# ── Route Flask ───────────────────────────────────────────────────────────────
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
        logger.info("▶ AVVIA")
    else:
        _send_cmd("stop")
        logger.info("■ FERMA")
    return jsonify({"status": "ok"})


@app.route('/calibrate')
def calibrate():
    global _cal_result
    _cal_result = {"status": "running"}
    _send_cmd("calibrate")
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


# ── WebManager ────────────────────────────────────────────────────────────────
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
            mic_cfg = config.sensors.microphone

            # spawn: il figlio parte come interprete Python pulito.
            # Nessun lock ereditato dai thread sensori/comunicatore del padre.
            # PyAudio viene inizializzato dentro audio_process_fn, mai qui.
            _mp_ctx     = multiprocessing.get_context('spawn')
            _cmd_queue  = _mp_ctx.Queue()
            _data_queue = _mp_ctx.Queue(maxsize=20)

            worker_cfg = {
                "th_tol":        state["th_tol"],
                "th_crit":       state["th_crit"],
                "in_idx":        mic_cfg.get('device_index', None),
                "out_idx":       mic_cfg.get('output_device_index', None),
                "audio_dir":     AUDIO_DIR,
                "tempo_check":   int(_ac_cfg.get('check_duration',   10)),
                "tempo_comfort": int(_ac_cfg.get('comfort_duration', 300)),
                "idle_wait":     60,
                "cal_secs":      10,
            }

            _audio_proc = _mp_ctx.Process(
                target=audio_process_fn,
                args=(_cmd_queue, _data_queue, worker_cfg),
                daemon=True,
                name="AudioWorker",
            )
            _audio_proc.start()
            logger.info(f"AudioWorker avviato (pid={_audio_proc.pid})")

            threading.Thread(target=_queue_reader, daemon=True,
                             name="QueueReader").start()

        app.run(host=self.host, port=self.port,
                debug=False, use_reloader=False, threaded=True)
