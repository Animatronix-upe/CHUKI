"""
app.py - Servidor Flask y coordinador central del Animatrónico

Inicia todos los módulos, sirve la interfaz web y expone la API REST.

Ejecutar:
    cd animatronico
    python python/app.py
"""

import os
import sys
import threading
import time
import logging
import json
import socket
import urllib.request
import urllib.parse

from flask import Flask, render_template, Response, jsonify, request, stream_with_context, send_file

# Asegura que los módulos Python del proyecto sean importables
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
from serial_controller   import SerialController
from movement_controller import MovementController
from gaze_tracking       import GazeTracker
from motion_detection    import MotionDetector
from camera              import CameraManager
from chucky_voice        import ChuckyVoice

# ----------------------------------------------------------------
# Logging
# ----------------------------------------------------------------
os.makedirs(os.path.dirname(cfg.LOG_FILE) or ".", exist_ok=True)
logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(cfg.LOG_FILE, encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------
# Flask
# ----------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "..", "templates"),
    static_folder=os.path.join(BASE_DIR, "..", "static"),
)

@app.after_request
def add_no_cache(response):
    """Evita que el navegador cachee la interfaz — siempre carga la versión más reciente."""
    if response.content_type and "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ----------------------------------------------------------------
# Componentes del sistema
# ----------------------------------------------------------------
serial   = SerialController(cfg)
movement = MovementController(cfg, serial)
gaze     = GazeTracker(cfg)
motion   = MotionDetector(cfg)
camera   = CameraManager(cfg)

chucky = ChuckyVoice(serial, cfg)

# Conectar cámara con procesadores y controlador de movimiento
camera.gaze_tracker    = gaze
camera.motion_detector = motion
camera.on_gaze_position   = lambda angle: movement.set_target(angle, "gaze")
camera.on_motion_position = lambda angle: movement.set_target(angle, "motion")

# ----------------------------------------------------------------
# Config persistente del usuario (guarda URL del ESP32, etc.)
# ----------------------------------------------------------------
_USER_CFG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_config.json")

def _load_user_cfg() -> dict:
    try:
        with open(_USER_CFG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_user_cfg(data: dict):
    try:
        existing = _load_user_cfg()
        existing.update(data)
        with open(_USER_CFG_FILE, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        logger.error("Error guardando user_config: %s", e)

# ----------------------------------------------------------------
# Registro de eventos (últimos 60)
# ----------------------------------------------------------------
_event_log = []
_event_lock = threading.Lock()

# Estado de los LEDs (sincronizado con el Arduino)
_led_state = cfg.LED_ON_AT_START


def log_event(message: str, level: str = "info"):
    with _event_lock:
        _event_log.insert(0, {
            "time": time.strftime("%H:%M:%S"),
            "message": message,
            "level": level
        })
        if len(_event_log) > 60:
            _event_log.pop()
    logger.info("[EVENT] %s", message)


# ----------------------------------------------------------------
# Inicialización
# ----------------------------------------------------------------
def initialize():
    log_event("Iniciando sistema Animatrónico...")

    if serial.connect():
        jaw_label = " + mandíbula" if not getattr(cfg, 'JAW_SERIAL_PORT', None) else ""
        log_event(f"Arduino conectado en {cfg.SERIAL_PORT} (ojos/cuello{jaw_label})", "success")
    else:
        log_event(
            f"Arduino no encontrado en {cfg.SERIAL_PORT}. "
            "Los servos no responderán hasta que se conecte.", "warning"
        )

    # Conectar Arduino de la boca (segundo puerto, si está configurado)
    jaw_port = getattr(cfg, 'JAW_SERIAL_PORT', None)
    if jaw_port:
        # Esperar antes de abrir el segundo puerto para evitar interferencia DTR
        time.sleep(3.0)
        if serial.connect_jaw():
            log_event(f"Arduino boca conectado en {jaw_port}", "success")
        else:
            log_event(
                f"Arduino boca no encontrado en {jaw_port}. "
                "La mandíbula no responderá.", "warning"
            )

    # Cargar config guardada (ESP32 URL, Groq API key, etc.)
    saved = _load_user_cfg()
    if saved.get("esp32_url"):
        cfg.ESP32_CAMERA_URL = saved["esp32_url"]
        logger.info("URL ESP32 cargada: %s", cfg.ESP32_CAMERA_URL)
    if saved.get("groq_api_key"):
        chucky.set_config(groq_key=saved["groq_api_key"])
        logger.info("Groq API key cargada")
    if saved.get("elevenlabs_api_key"):
        chucky.set_config(elevenlabs_key=saved["elevenlabs_api_key"])
        logger.info("ElevenLabs API key cargada")
    if saved.get("elevenlabs_voice_id"):
        chucky.set_config(elevenlabs_voice_id=saved["elevenlabs_voice_id"])
        logger.info("ElevenLabs Voice ID cargado: %s", saved["elevenlabs_voice_id"])

    if camera.start():
        log_event(f"Cámara {cfg.CAMERA_INDEX} lista", "success")
    else:
        log_event(
            f"Cámara {cfg.CAMERA_INDEX} no disponible. "
            "Los modos automáticos no funcionarán.", "error"
        )

    # Enviar estado inicial de los LEDs al Arduino
    led_cmd = "LED,1" if cfg.LED_ON_AT_START else "LED,0"
    serial.send_command(led_cmd)
    log_event(f"LEDs {'encendidos' if cfg.LED_ON_AT_START else 'apagados'} al iniciar")

    log_event("Sistema listo")


# ================================================================
# Rutas de la API
# ================================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    mv = movement.get_status()
    return jsonify({
        "arduino": {
            "connected": serial.connected,
            "port": cfg.SERIAL_PORT,
        },
        "camera": {
            "connected": camera.connected,
            "index": cfg.CAMERA_INDEX,
            "esp32_url": getattr(cfg, "ESP32_CAMERA_URL", ""),
        },
        "movement": mv,
        "neck": {
            "angle": mv.get("neck_angle", 90),
            "sync": mv.get("neck_sync", True),
            "mode": mv.get("neck_mode", "user"),
        },
        "gaze": gaze.get_state(),
        "motion": motion.get_state(),
        "mode": movement.mode,
        "led_on": _led_state,
    })


@app.route("/api/mode", methods=["POST"])
def api_mode():
    data = request.get_json(force=True)
    mode = data.get("mode", "rest")

    if mode not in MovementController.MODES:
        return jsonify({"error": f"Modo inválido: {mode}"}), 400

    # Asignar procesador de cámara
    camera.set_processor("gaze" if mode == "gaze" else
                         "motion" if mode == "motion" else None)

    ok = movement.set_mode(mode)
    if ok:
        log_event(f"Modo: {mode}")
    return jsonify({"success": ok, "mode": mode})


@app.route("/api/position", methods=["POST"])
def api_position():
    data = request.get_json(force=True)
    try:
        angle = int(data.get("angle", cfg.SERVO_CENTER_ANGLE))
    except (ValueError, TypeError):
        return jsonify({"error": "Ángulo inválido"}), 400

    angle = max(cfg.SERVO_MIN_ANGLE, min(cfg.SERVO_MAX_ANGLE, angle))
    movement.set_manual_position(angle)
    return jsonify({"success": True, "angle": angle})


@app.route("/api/center", methods=["POST"])
def api_center():
    movement.center()
    log_event("Ojos centrados")
    return jsonify({"success": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    movement.stop()
    log_event("PARADA DE EMERGENCIA activada", "warning")
    return jsonify({"success": True})


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify({
            "serial_port":         cfg.SERIAL_PORT,
            "camera_index":        cfg.CAMERA_INDEX,
            "servo_min":           cfg.SERVO_MIN_ANGLE,
            "servo_max":           cfg.SERVO_MAX_ANGLE,
            "servo_center":        cfg.SERVO_CENTER_ANGLE,
            "servo_left":          cfg.SERVO_LEFT_ANGLE,
            "servo_right":         cfg.SERVO_RIGHT_ANGLE,
            "smoothing":           movement.smoothing,
            "dead_zone":           movement.dead_zone,
            "min_movement":        movement.min_movement,
            "return_timeout":      movement.return_timeout,
            "gaze_sensitivity":    cfg.GAZE_SENSITIVITY,
            "motion_min_area":     cfg.MOTION_MIN_AREA,
            "motion_return_timeout": cfg.MOTION_RETURN_TIMEOUT,
            "eye_neck_delay_ms":    int(round(movement.eye_neck_delay * 1000)),
        })

    data = request.get_json(force=True)
    movement.update_settings(data)

    if "gaze_sensitivity" in data:
        cfg.GAZE_SENSITIVITY = float(data["gaze_sensitivity"])
        gaze.config.GAZE_SENSITIVITY = cfg.GAZE_SENSITIVITY
    if "motion_min_area" in data:
        cfg.MOTION_MIN_AREA = int(data["motion_min_area"])
        motion.config.MOTION_MIN_AREA = cfg.MOTION_MIN_AREA
    if "motion_return_timeout" in data:
        cfg.MOTION_RETURN_TIMEOUT = float(data["motion_return_timeout"])
        motion.config.MOTION_RETURN_TIMEOUT = cfg.MOTION_RETURN_TIMEOUT

    log_event("Configuración actualizada")
    return jsonify({"success": True})


@app.route("/api/calibrate", methods=["POST"])
def api_calibrate():
    data = request.get_json(force=True)
    action = data.get("action", "")

    if action == "start":
        gaze.start_calibration()
        return jsonify({"success": True, "message": "Calibración iniciada. Mira al centro."})

    elif action == "next":
        ok, msg = gaze.advance_calibration()
        if ok:
            log_event(f"Calibración paso {gaze.calib_step}/3")
        return jsonify({"success": ok, "message": msg, "step": gaze.calib_step})

    elif action == "cancel":
        gaze.cancel_calibration()
        return jsonify({"success": True, "message": "Calibración cancelada"})

    return jsonify({"error": "Acción desconocida"}), 400


@app.route("/api/ports")
def api_ports():
    return jsonify({"ports": SerialController.list_ports()})


@app.route("/api/connect_serial", methods=["POST"])
def api_connect_serial():
    data = request.get_json(force=True)
    port = data.get("port", cfg.SERIAL_PORT)

    serial.disconnect()
    cfg.SERIAL_PORT = port
    serial.port = port

    if serial.connect():
        log_event(f"Arduino conectado en {port}", "success")
        return jsonify({"success": True})

    log_event(f"No se pudo conectar a {port}", "error")
    return jsonify({"success": False, "error": f"No se pudo conectar a {port}"})


@app.route("/api/events")
def api_events():
    with _event_lock:
        return jsonify({"events": list(_event_log)})


@app.route("/api/neck_position", methods=["POST"])
def api_neck_position():
    """Mueve el cuello a un ángulo específico (modo independiente)."""
    data = request.get_json(force=True)
    try:
        angle = int(data.get("angle", 90))
    except (ValueError, TypeError):
        return jsonify({"error": "Ángulo inválido"}), 400
    angle = max(cfg.NECK_MIN_ANGLE, min(cfg.NECK_MAX_ANGLE, angle))
    movement.set_neck_manual_position(angle)
    return jsonify({"success": True, "angle": angle})


@app.route("/api/neck_sync", methods=["POST"])
def api_neck_sync():
    """Activa o desactiva la sincronización del cuello con los ojos."""
    data = request.get_json(force=True)
    enabled = bool(data.get("enabled", True))
    movement.set_neck_sync(enabled)
    cfg.NECK_SYNC_WITH_EYES = enabled
    log_event(f"Cuello {'sincronizado con ojos' if enabled else 'control independiente'}")
    return jsonify({"success": True, "neck_sync": enabled})


@app.route("/api/reset_motion", methods=["POST"])
def api_reset_motion():
    motion.reset()
    log_event("Detector de movimiento reiniciado")
    return jsonify({"success": True})


@app.route("/api/led", methods=["POST"])
def api_led():
    """Controlar los LEDs de los ojos."""
    global _led_state
    data = request.get_json(force=True)
    state = bool(data.get("state", True))
    cmd = "LED,1" if state else "LED,0"
    response = serial.query_command(cmd)
    ok = response.startswith("OK")
    if not ok and response == "":
        # sin respuesta — intento de envío sin verificar
        ok = serial.send_command(cmd)
    if ok:
        _led_state = state
        log_event(f"LEDs {'encendidos' if state else 'apagados'} (Arduino: {response or 'sin respuesta'})")
    else:
        log_event(f"LEDs: Arduino respondió '{response}'", "warning")
    return jsonify({"success": ok, "state": _led_state, "arduino_response": response})


@app.route("/api/arduino_status")
def api_arduino_status():
    """Consulta STATUS directamente al Arduino y devuelve la respuesta cruda."""
    response = serial.query_command("STATUS", timeout=2.0)
    return jsonify({"raw": response, "connected": serial.connected})


@app.route("/api/esp32_camera", methods=["GET", "POST"])
def api_esp32_camera():
    """Configura o consulta la fuente de camara ESP32-CAM."""
    if request.method == "GET":
        url = getattr(cfg, "ESP32_CAMERA_URL", "")
        return jsonify({
            "url": url,
            "connected": camera.connected,
            "source": "esp32" if url else "local",
            "source_label": url if url else f"Webcam local (indice {cfg.CAMERA_INDEX})",
        })

    data = request.get_json(force=True)
    url = data.get("url", "").strip()

    if url:
        cfg.ESP32_CAMERA_URL = url
        _save_user_cfg({"esp32_url": url})
    else:
        cfg.ESP32_CAMERA_URL = ""         # borrar URL al volver a webcam local
        _save_user_cfg({"esp32_url": ""})

    # Conectar en segundo plano para no bloquear Flask
    def _connect():
        ok = camera.restart(url if url else None)
        label = f"ESP32: {url}" if url else "Webcam local"
        if ok:
            log_event(f"Camara conectada: {label}", "success")
        else:
            log_event(f"No se pudo conectar: {label}", "error")

    threading.Thread(target=_connect, daemon=True, name="esp32-connect").start()
    return jsonify({"success": True, "connecting": True})


@app.route("/api/esp32_wifi", methods=["POST"])
def api_esp32_wifi():
    """Envia nuevas credenciales WiFi al ESP32-CAM (requiere sketch actualizado)."""
    data = request.get_json(force=True)
    ssid     = data.get("ssid", "").strip()
    password = data.get("password", "")

    if not ssid:
        return jsonify({"success": False, "error": "SSID requerido"}), 400

    esp32_url = getattr(cfg, "ESP32_CAMERA_URL", "")
    if not esp32_url:
        return jsonify({"success": False, "error": "Primero conecta el ESP32-CAM"}), 400

    # Derivar URL base desde la URL del stream
    base_url = esp32_url.split("/stream")[0].split("/capture")[0]
    wifi_endpoint = f"{base_url}/wifi"

    # Incluir la IP de Flask para que el ESP32 sepa donde avisar al reconectarse
    # Usar la IP real de red (no localhost) para que el ESP32 pueda alcanzar Flask
    try:
        _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _s.connect(("8.8.8.8", 80))
        _real_ip = _s.getsockname()[0]
        _s.close()
    except Exception:
        _real_ip = request.host.split(":")[0]
    flask_ip = f"{_real_ip}:5000"
    body = urllib.parse.urlencode({"ssid": ssid, "pass": password, "flask": flask_ip}).encode()
    req_obj = urllib.request.Request(wifi_endpoint, data=body, method="POST")
    req_obj.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        resp = urllib.request.urlopen(req_obj, timeout=5)
        resp.read()
        _save_user_cfg({"esp32_ssid": ssid})
        log_event(f"WiFi ESP32 actualizado: {ssid}", "success")
        return jsonify({"success": True, "message": f"ESP32 reiniciando con red: {ssid}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/esp32_announce", methods=["POST"])
def api_esp32_announce():
    """El ESP32 avisa su nueva IP tras reconectarse a una red WiFi."""
    data = request.get_json(force=True)
    ip   = data.get("ip", "").strip()
    port = int(data.get("port", 81))

    if not ip:
        return jsonify({"success": False, "error": "IP requerida"}), 400

    new_url = f"http://{ip}:{port}/stream"
    cfg.ESP32_CAMERA_URL = new_url
    _save_user_cfg({"esp32_url": new_url, "esp32_ip": ip})
    log_event(f"ESP32 anuncio: nueva IP {ip} — stream: {new_url}", "success")

    # Reconectar camara con la nueva URL en segundo plano
    def _reconnect():
        ok = camera.restart(new_url)
        if ok:
            log_event(f"Camara reconectada: {new_url}", "success")
        else:
            log_event(f"No se pudo reconectar camara: {new_url}", "error")
    threading.Thread(target=_reconnect, daemon=True, name="esp32-reconnect").start()

    return jsonify({"success": True, "url": new_url})



# ================================================================
# Rutas Chucky Parlante
# ================================================================

@app.route("/api/chucky/status")
def chucky_status():
    return jsonify(chucky.get_status())


@app.route("/api/chucky/start", methods=["POST"])
def chucky_start():
    ok = chucky.start()
    if ok:
        log_event("Chucky Parlante: escuchando", "success")
    return jsonify({"success": ok, "error": chucky.error_msg})


@app.route("/api/chucky/stop", methods=["POST"])
def chucky_stop():
    chucky.stop()
    log_event("Chucky Parlante: detenido")
    return jsonify({"success": True})


@app.route("/api/chucky/speak", methods=["POST"])
def chucky_speak():
    data = request.get_json(force=True)
    texto = (data.get("text") or "").strip()
    if not texto:
        return jsonify({"error": "text requerido"}), 400
    chucky.speak_text(texto)
    log_event(f"Chucky dice: {texto[:60]}")
    return jsonify({"success": True})


@app.route("/api/chucky/config", methods=["POST"])
def chucky_config():
    data = request.get_json(force=True)
    chucky.set_config(
        groq_key            = data.get("groq_api_key"),
        wake_word           = data.get("wake_word"),
        end_word            = data.get("end_word"),
        elevenlabs_key      = data.get("elevenlabs_api_key"),
        elevenlabs_voice_id = data.get("elevenlabs_voice_id"),
        elevenlabs_model    = data.get("elevenlabs_model"),
    )
    # Persistir claves API
    save_data = {}
    if data.get("groq_api_key") is not None:
        save_data["groq_api_key"] = data["groq_api_key"]
    if data.get("elevenlabs_api_key") is not None:
        save_data["elevenlabs_api_key"] = data["elevenlabs_api_key"]
    if data.get("elevenlabs_voice_id") is not None:
        save_data["elevenlabs_voice_id"] = data["elevenlabs_voice_id"]
    if save_data:
        _save_user_cfg(save_data)
    log_event("Config Chucky actualizada")
    return jsonify({"success": True})


@app.route("/api/chucky/auto_frases", methods=["GET", "POST"])
def chucky_auto_frases():
    if request.method == "GET":
        return jsonify({
            "auto_active":   chucky.auto_active,
            "auto_interval": chucky.auto_interval,
            "scary_voice":   chucky.scary_voice,
            "auto_phrases":  chucky.auto_phrases,
        })

    data = request.get_json(force=True)
    action = data.get("action")

    if action == "start":
        chucky.set_auto_config(
            interval = data.get("interval"),
            phrases  = data.get("phrases"),
            scary    = data.get("scary_voice"),
        )
        ok = chucky.start_auto_phrases()
        if ok:
            log_event(f"Auto-frases iniciado (cada {chucky.auto_interval}s)", "success")
        return jsonify({"success": True, "auto_active": chucky.auto_active})

    elif action == "stop":
        chucky.stop_auto_phrases()
        log_event("Auto-frases detenido")
        return jsonify({"success": True, "auto_active": False})

    elif action == "config":
        chucky.set_auto_config(
            interval = data.get("interval"),
            phrases  = data.get("phrases"),
            scary    = data.get("scary_voice"),
        )
        log_event("Config auto-frases actualizada")
        return jsonify({"success": True,
                        "auto_interval": chucky.auto_interval,
                        "scary_voice":   chucky.scary_voice})

    return jsonify({"error": "Acción desconocida"}), 400


@app.route("/api/chucky/download_model", methods=["POST"])
def chucky_download_model():
    if chucky.model_ready:
        return jsonify({"success": True, "message": "Modelo ya listo"})
    chucky.download_model()
    return jsonify({"success": True, "message": "Descarga iniciada"})


@app.route("/download_app")
def download_app():
    """Descarga el APK de Android para instalar en el teléfono."""
    apk_candidates = [
        os.path.normpath(os.path.join(BASE_DIR, "..", "..", "app_android", "ChuckyController.apk")),
        r"C:\Users\mayum\Desktop\Chucky_Sistema\app_android\ChuckyController.apk",
        r"C:\Users\mayum\Downloads\ChuckyController.apk",
    ]
    for apk_path in apk_candidates:
        if os.path.exists(apk_path):
            return send_file(
                apk_path,
                as_attachment=True,
                download_name="ChuckyController.apk",
                mimetype="application/vnd.android.package-archive"
            )
    return "APK no encontrado. Compila primero desde la PC.", 404


@app.route("/sw.js")
def service_worker():
    """Sirve el Service Worker desde la raíz (requerido para el scope PWA)."""
    resp = app.send_static_file("sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ----------------------------------------------------------------
# Streaming de vídeo (MJPEG)
# ----------------------------------------------------------------

@app.route("/video_feed")
def video_feed():
    def generate():
        last_jpeg = None
        last_sent = None
        while True:
            jpeg = camera.get_jpeg(quality=70)
            if jpeg and jpeg is not last_jpeg:
                last_jpeg = jpeg
            if last_jpeg and last_jpeg is not last_sent:
                last_sent = last_jpeg
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + last_jpeg + b"\r\n"
                )
            time.sleep(0.033)   # ~30 fps cap

    return Response(
        stream_with_context(generate()),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


# ================================================================
# Punto de entrada
# ================================================================

if __name__ == "__main__":
    try:
        initialize()
        print(f"\n{'='*50}")
        print(f"  Animatrónico Control System")
        print(f"  Abre en tu navegador: http://localhost:{cfg.FLASK_PORT}")
        print(f"{'='*50}\n")
        app.run(
            host=cfg.FLASK_HOST,
            port=cfg.FLASK_PORT,
            debug=cfg.FLASK_DEBUG,
            threaded=True,
            use_reloader=False,   # evita doble inicialización
        )
    except KeyboardInterrupt:
        logger.info("Apagado solicitado por el usuario")
    finally:
        logger.info("Cerrando sistema...")
        camera.stop()
        movement.shutdown()
        if gaze:
            gaze.shutdown()
        logger.info("Sistema cerrado correctamente")
