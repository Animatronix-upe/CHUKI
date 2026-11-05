"""
camera.py - Gestión de la cámara y el bucle de captura

Captura frames en un hilo separado para no bloquear Flask.
Para streams ESP32 usa doble hilo: captura rápida + procesamiento separado,
lo que evita que el buffer se llene de frames viejos.
"""

import cv2
import threading
import time
import logging
import numpy as np

logger = logging.getLogger(__name__)


class CameraManager:

    def __init__(self, config):
        self.config = config
        self.cap = None
        self.connected = False
        self._source = None

        self._frame = None          # frame crudo más reciente (captura)
        self._raw_latest = None     # copia sin lock para el hilo de procesamiento
        self._annotated = None      # frame procesado/anotado para el stream Flask
        self._lock = threading.Lock()
        self._proc_lock = threading.Lock()
        self._running = False
        self._thread = None
        self._proc_thread = None    # hilo separado de procesamiento (solo para ESP32)

        # Se asignan desde app.py
        self.gaze_tracker = None
        self.motion_detector = None
        self.active_processor = None   # 'gaze' | 'motion' | None

        # Callbacks → llaman a movement_controller.set_target()
        self.on_gaze_position = None
        self.on_motion_position = None

    # ----------------------------------------------------------
    # Ciclo de vida
    # ----------------------------------------------------------

    def start(self, url: str = None) -> bool:
        """Inicia captura desde webcam local o URL de stream (ESP32-CAM)."""
        if url is None:
            url = getattr(self.config, "ESP32_CAMERA_URL", "")

        source = url if url else self.config.CAMERA_INDEX
        is_url = isinstance(source, str) and source.startswith("http")

        try:
            if is_url:
                # Verificar que el ESP32 responde a nivel TCP (sin abrir el stream MJPEG completo)
                try:
                    import socket
                    from urllib.parse import urlparse
                    p = urlparse(source)
                    host = p.hostname or ""
                    port = p.port or 80
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(5)
                    s.connect((host, port))
                    s.close()
                    logger.info(f"ESP32 accesible en {host}:{port}")
                except Exception as e:
                    logger.error(f"ESP32 no accesible en {source}: {e}")
                    return False

            # En Windows usar DirectShow para cámaras locales; FFMPEG para streams HTTP
            if is_url:
                cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
            else:
                cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)

            if is_url:
                # Configurar timeouts y reducir buffer para minimizar latencia
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                try:
                    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)
                except Exception:
                    pass  # Algunos backends no soportan estos flags
                time.sleep(0.5)
            else:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.config.CAMERA_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.CAMERA_HEIGHT)
                cap.set(cv2.CAP_PROP_FPS,          self.config.CAMERA_FPS)
                # Descartar frames negros iniciales (calentamiento USB)
                for _ in range(5):
                    cap.read()

            if not cap.isOpened():
                logger.error(f"Fuente de cámara no disponible: {source}")
                return False

            ret, _ = cap.read()
            if not ret:
                logger.error(f"No se pudo leer el primer frame de: {source}")
                cap.release()
                return False

            self.cap = cap
            self.connected = True
            self._running = True
            self._source = source
            self._thread = threading.Thread(
                target=self._capture_loop, daemon=True, name="camera-cap"
            )
            self._thread.start()

            # Para streams de red: hilo de procesamiento separado
            # La captura corre a máxima velocidad; el procesamiento no bloquea la captura
            if is_url:
                self._proc_thread = threading.Thread(
                    target=self._process_loop, daemon=True, name="camera-proc"
                )
                self._proc_thread.start()

            src_label = url if is_url else f"índice {source}"
            logger.info(f"Cámara iniciada: {src_label}")
            return True

        except Exception as e:
            logger.error(f"Error al iniciar cámara: {e}")
            return False

    def restart(self, url: str = None) -> bool:
        """Detiene la cámara actual y la reinicia con una nueva fuente.
        Para URLs de red, la conexion se intenta en un hilo con timeout de 6s."""
        self.stop()
        time.sleep(0.3)
        if url is not None:
            self.config.ESP32_CAMERA_URL = url   # "" limpia el ESP32, URL lo establece

        source = url if url else self.config.CAMERA_INDEX
        is_url = isinstance(source, str) and source.startswith("http")

        if not is_url:
            # Pasar "" (no None) para que start() use CAMERA_INDEX en vez de releer ESP32 URL
            return self.start("")

        # Para streams de red: correr en hilo con timeout
        result = [False]
        def _start():
            result[0] = self.start(url)

        t = threading.Thread(target=_start, daemon=True)
        t.start()
        t.join(timeout=8)   # maximo 8 segundos
        return result[0]

    def stop(self):
        self._running = False
        if self.cap:
            self.cap.release()
            self.cap = None
        self.connected = False
        self._raw_latest = None
        logger.info("Cámara detenida")

    # ----------------------------------------------------------
    # Bucle de captura (hilo)
    # ----------------------------------------------------------

    def _capture_loop(self):
        """
        Hilo de captura: corre a máxima velocidad.
        Para ESP32 (is_url=True) solo almacena el frame más fresco —
        el hilo _process_loop lo consume por separado sin bloquear la captura.
        Para webcam local el procesamiento sigue siendo inline.
        """
        is_url = isinstance(self._source, str) and str(self._source).startswith("http")
        consecutive_failures = 0

        while self._running:
            if not self.cap or not self.cap.isOpened():
                self.connected = False
                if is_url and self._running:
                    logger.warning("Stream perdido, reconectando en 3s...")
                    time.sleep(3)
                    self._try_reconnect()
                else:
                    break
                continue

            ret, frame = self.cap.read()

            if not ret or frame is None:
                consecutive_failures += 1
                if is_url and consecutive_failures >= 5 and self._running:
                    logger.warning("Demasiados fallos de lectura, reconectando stream...")
                    if self.cap:
                        self.cap.release()
                        self.cap = None
                    self.connected = False
                    consecutive_failures = 0
                    time.sleep(2)
                    self._try_reconnect()
                else:
                    time.sleep(0.02)
                continue

            consecutive_failures = 0

            # Voltear horizontalmente — corrige el efecto espejo de la cámara
            frame = cv2.flip(frame, 1)

            if is_url:
                # Para ESP32: guardar frame crudo; el hilo de proceso lo consumirá
                with self._proc_lock:
                    self._raw_latest = frame
                # Actualizar annotated con el raw SIEMPRE para que el stream sea fluido.
                # _process_loop lo sobreescribirá con anotaciones cuando termine.
                with self._lock:
                    self._frame = frame
                    self._annotated = frame
            else:
                # Para webcam local: saltar frames oscuros (calentamiento USB)
                if frame.mean() < 4:
                    time.sleep(0.033)
                    continue
                annotated = self._process(frame)
                with self._lock:
                    self._frame = frame
                    self._annotated = annotated

    def _process_loop(self):
        """
        Hilo de procesamiento separado (solo para streams ESP32).
        Toma el frame más reciente, lo procesa con gaze/motion y actualiza _annotated.
        Si el procesamiento tarda más que la captura, nunca bloquea la cámara.
        """
        while self._running:
            with self._proc_lock:
                frame = self._raw_latest

            if frame is None:
                time.sleep(0.02)
                continue

            if frame.mean() < 2:
                time.sleep(0.02)
                continue

            annotated = self._process(frame)
            with self._lock:
                self._annotated = annotated

            time.sleep(0.02)  # ~50Hz máximo de procesamiento

    def _try_reconnect(self):
        """Intenta reconectar al stream de la cámara."""
        try:
            is_url = isinstance(self._source, str) and self._source.startswith("http")
            cap = cv2.VideoCapture(self._source, cv2.CAP_FFMPEG) if is_url else cv2.VideoCapture(self._source, cv2.CAP_DSHOW)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                try:
                    cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000)
                    cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000)
                except Exception:
                    pass
                time.sleep(0.5)
                ret, _ = cap.read()
                if ret:
                    self.cap = cap
                    self.connected = True
                    logger.info(f"Stream reconectado: {self._source}")
                    return True
            cap.release()
        except Exception as e:
            logger.error(f"Error en reconexión: {e}")
        logger.warning("Reconexión fallida, reintentando en el próximo ciclo...")
        return False

    def _process(self, frame: np.ndarray) -> np.ndarray:
        """Aplica el procesador activo y dispara callbacks."""
        try:
            if self.active_processor == "gaze" and self.gaze_tracker:
                ann = self.gaze_tracker.process_frame(frame)
                if self.gaze_tracker.face_detected and self.on_gaze_position:
                    self.on_gaze_position(self.gaze_tracker.get_servo_angle())
                return ann

            elif self.active_processor == "motion" and self.motion_detector:
                ann = self.motion_detector.process_frame(frame)
                if self.motion_detector.motion_detected and self.on_motion_position:
                    self.on_motion_position(self.motion_detector.get_servo_angle())
                return ann

            else:
                return frame

        except Exception as e:
            logger.error(f"Error procesando frame: {e}")
            return frame.copy()

    # ----------------------------------------------------------
    # Acceso a frames
    # ----------------------------------------------------------

    def get_annotated_frame(self):
        with self._lock:
            return self._annotated.copy() if self._annotated is not None else None

    def get_jpeg(self, quality: int = 80) -> bytes | None:
        frame = self.get_annotated_frame()
        if frame is None:
            return None
        try:
            ret, buf = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality]
            )
            return buf.tobytes() if ret else None
        except Exception as e:
            logger.error(f"Error codificando JPEG: {e}")
            return None

    # ----------------------------------------------------------
    # Control del procesador activo
    # ----------------------------------------------------------

    def set_processor(self, processor):
        """'gaze', 'motion', o None para desactivar el procesamiento."""
        self.active_processor = processor

