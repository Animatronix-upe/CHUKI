"""
camera.py - Gestión de la cámara y el bucle de captura

Captura frames en un hilo separado para no bloquear Flask.
Llama a los procesadores (gaze_tracker o motion_detector)
y notifica al movementController via callbacks.
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

        self._frame = None
        self._annotated = None
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

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
            cap = cv2.VideoCapture(source)

            if is_url:
                # Reducir buffer para minimizar latencia en stream de red
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                time.sleep(1.5)   # esperar a que el stream se establezca
            else:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.config.CAMERA_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.CAMERA_HEIGHT)
                cap.set(cv2.CAP_PROP_FPS,          self.config.CAMERA_FPS)
                # Descartar frames negros iniciales (calentamiento USB)
                for _ in range(10):
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
            src_label = url if is_url else f"índice {source}"
            logger.info(f"Cámara iniciada: {src_label}")
            return True

        except Exception as e:
            logger.error(f"Error al iniciar cámara: {e}")
            return False

    def restart(self, url: str = None) -> bool:
        """Detiene la cámara actual y la reinicia con una nueva fuente."""
        self.stop()
        time.sleep(0.5)
        if url is not None:
            self.config.ESP32_CAMERA_URL = url
        return self.start(url)

    def stop(self):
        self._running = False
        if self.cap:
            self.cap.release()
            self.cap = None
        self.connected = False
        logger.info("Cámara detenida")

    # ----------------------------------------------------------
    # Bucle de captura (hilo)
    # ----------------------------------------------------------

    def _capture_loop(self):
        while self._running:
            if not self.cap or not self.cap.isOpened():
                self.connected = False
                break

            ret, frame = self.cap.read()
            if not ret or frame is None:
                time.sleep(0.033)
                continue

            # Saltar frames muy oscuros (calentamiento de cámara USB)
            if frame.mean() < 4:
                time.sleep(0.033)
                continue

            annotated = self._process(frame)

            with self._lock:
                self._frame = frame
                self._annotated = annotated

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
                ann = frame.copy()
                h, w = ann.shape[:2]
                cv2.putText(ann, "Camara activa - sin procesamiento",
                            (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (80, 80, 80), 1)
                return ann

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

    # ----------------------------------------------------------
    # Inyección de frame externo (cámara del teléfono)
    # ----------------------------------------------------------

    def inject_frame(self, frame) -> None:
        """Procesa un frame externo (teléfono) sin usar la webcam."""
        try:
            annotated = self._process(frame)
            with self._lock:
                self._frame = frame
                self._annotated = annotated
        except Exception as e:
            logger.error(f"Error inyectando frame externo: {e}")
