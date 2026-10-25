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

from flask import Flask, render_template, Response, jsonify, request, stream_with_context

# Asegura que los módulos Python del proyecto sean importables
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
from serial_controller   import SerialController
from movement_controller import MovementController
from gaze_tracking       import GazeTracker
from motion_detection    import MotionDetector
from camera              import CameraManager

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

# ----------------------------------------------------------------
# Componentes del sistema
# ----------------------------------------------------------------
serial   = SerialController(cfg)
movement = MovementController(cfg, serial)
gaze     = GazeTracker(cfg)
motion   = MotionDetector(cfg)
camera   = CameraManager(cfg)

# Conectar cámara con procesadores y controlador de movimiento
camera.gaze_tracker    = gaze
camera.motion_detector = motion
camera.on_gaze_position   = lambda angle: movement.set_target(angle, "gaze")
camera.on_motion_position = lambda angle: movement.set_target(angle, "motion")

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
        log_event(f"Arduino conectado en {cfg.SERIAL_PORT}", "success")
    else:
        log_event(
            f"Arduino no encontrado en {cfg.SERIAL_PORT}. "
            "El servo no responderá hasta que se conecte.", "warning"
        )

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
    return jsonify({
        "arduino": {
            "connected": serial.connected,
            "port": cfg.SERIAL_PORT,
        },
        "camera": {
            "connected": camera.connected,
            "index": cfg.CAMERA_INDEX,
        },
        "movement": movement.get_status(),
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
        log_event(f"Modo → {mode}")
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
    """Configura o consulta la fuente de cámara ESP32-CAM."""
    if request.method == "GET":
        url = getattr(cfg, "ESP32_CAMERA_URL", "")
        return jsonify({
            "url": url,
            "connected": camera.connected,
            "source": "esp32" if url else "local",
            "source_label": url if url else f"Webcam local (índice {cfg.CAMERA_INDEX})",
        })

    data = request.get_json(force=True)
    url = data.get("url", "").strip()

    ok = camera.restart(url if url else None)
    if ok:
        source_label = f"ESP32: {url}" if url else "Webcam local"
        log_event(f"Fuente de cámara → {source_label}", "success")
        return jsonify({"success": True, "source": "esp32" if url else "local"})
    else:
        err = f"No se pudo conectar a {url}" if url else "No se pudo iniciar webcam"
        log_event(err, "error")
        return jsonify({"success": False, "error": err})


@app.route("/api/phone_frame", methods=["POST"])
def api_phone_frame():
    """Recibe un frame JPEG en base64 desde la cámara del teléfono y lo procesa."""
    import base64
    import numpy as np
    import cv2

    data = request.get_json(force=True)
    frame_b64 = data.get("frame", "")

    if "base64," in frame_b64:
        frame_b64 = frame_b64.split("base64,", 1)[1]

    try:
        img_bytes = base64.b64decode(frame_b64)
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return jsonify({"error": "Frame inválido"}), 400
        camera.inject_frame(frame)
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error en phone_frame: {e}")
        return jsonify({"error": str(e)}), 400


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
        while True:
            jpeg = camera.get_jpeg(quality=70)
            if jpeg:
                last_jpeg = jpeg
            if last_jpeg:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + last_jpeg + b"\r\n"
                )
            time.sleep(0.05)   # ~20 fps — reduce carga en el navegador

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
