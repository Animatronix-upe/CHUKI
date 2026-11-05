"""
gaze_tracking.py - Estimación de dirección de mirada con MediaPipe

Compatible con:
  - mediapipe 0.10.x  → API legacy (mp.solutions.face_mesh)
  - mediapipe 1.x     → Tasks API  (FaceLandmarker + modelo .task)
    El modelo (~29 MB) se descarga automáticamente en python/face_landmarker.task
    la primera vez que se ejecuta.
"""

import cv2
import numpy as np
import json
import os
import logging
from collections import deque

logger = logging.getLogger(__name__)

# URL del modelo para mediapipe 1.x
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
)
_MODEL_PATH = os.path.join(os.path.dirname(__file__), "face_landmarker.task")


class GazeTracker:

    # Índices de landmarks de iris (478 puntos, mismos en ambas APIs)
    LEFT_IRIS  = [468, 469, 470, 471, 472]
    RIGHT_IRIS = [473, 474, 475, 476, 477]

    LEFT_EYE_OUTER  = 33
    LEFT_EYE_INNER  = 133
    RIGHT_EYE_INNER = 362
    RIGHT_EYE_OUTER = 263

    def __init__(self, config):
        self.config = config
        self._new_api = False

        import mediapipe as mp
        self._mp = mp

        # Intentar API legacy (mediapipe 0.10.x)
        try:
            _fm = mp.solutions.face_mesh
            self.face_mesh = _fm.FaceMesh(
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5
            )
            logger.info("MediaPipe: usando API legacy (0.10.x)")

        except AttributeError:
            # mediapipe 1.x — Tasks API
            self._new_api = True
            self._init_tasks_api(mp)

        self.face_detected = False
        self.gaze_x = 0.0
        self._history = deque(maxlen=config.GAZE_HISTORY_LENGTH)

        self.calibration = {
            "center": 0.0,
            "left": -0.3,
            "right": 0.3,
            "calibrated": False
        }
        self._load_calibration()

        self.calibrating = False
        self.calib_step = 0
        self._calib_raw = {"center": [], "left": [], "right": []}
        self._CALIB_KEYS = ["center", "left", "right"]

    # ----------------------------------------------------------
    # Inicialización mediapipe 1.x
    # ----------------------------------------------------------

    def _init_tasks_api(self, mp):
        """Configura FaceLandmarker de mediapipe 1.x. Descarga el modelo si falta."""
        if not os.path.exists(_MODEL_PATH):
            self._download_model()

        from mediapipe.tasks import python as mp_tasks
        from mediapipe.tasks.python import vision

        base_options = mp_tasks.BaseOptions(model_asset_path=_MODEL_PATH)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5
        )
        self.face_mesh = vision.FaceLandmarker.create_from_options(options)
        logger.info("MediaPipe: usando Tasks API 1.x con face_landmarker.task")

    @staticmethod
    def _download_model():
        import urllib.request
        logger.info(f"Descargando modelo (~29 MB) en {_MODEL_PATH} ...")
        print(f"[GazeTracker] Descargando modelo mediapipe (~29 MB), espera un momento...")
        try:
            urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
            print("[GazeTracker] Modelo descargado correctamente.")
            logger.info("Modelo descargado correctamente")
        except Exception as e:
            logger.error(f"Error descargando modelo: {e}")
            raise RuntimeError(
                f"No se pudo descargar el modelo mediapipe.\n"
                f"Descárgalo manualmente de:\n  {_MODEL_URL}\n"
                f"y colócalo en: {_MODEL_PATH}"
            ) from e

    # ----------------------------------------------------------
    # Procesamiento de frame
    # ----------------------------------------------------------

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        if self._new_api:
            # mediapipe 1.x
            mp_image = self._mp.Image(
                image_format=self._mp.ImageFormat.SRGB, data=rgb
            )
            result = self.face_mesh.detect(mp_image)
            face_list = result.face_landmarks  # list of list[NormalizedLandmark]
        else:
            # mediapipe 0.10.x legacy
            rgb.flags.writeable = False
            result = self.face_mesh.process(rgb)
            rgb.flags.writeable = True
            face_list = result.multi_face_landmarks  # list of FaceLandmarks

        annotated = frame.copy()

        if face_list:
            self.face_detected = True

            # Normalizar landmarks a lista de (px, py)
            if self._new_api:
                lms_raw = face_list[0]           # list[NormalizedLandmark]
            else:
                lms_raw = face_list[0].landmark  # RepeatedContainer

            lms = [(int(lm.x * w), int(lm.y * h)) for lm in lms_raw]

            left_iris  = self._center(lms, self.LEFT_IRIS)
            right_iris = self._center(lms, self.RIGHT_IRIS)

            left_ratio  = self._eye_ratio(lms, left_iris,
                                          self.LEFT_EYE_OUTER, self.LEFT_EYE_INNER)
            right_ratio = self._eye_ratio(lms, right_iris,
                                          self.RIGHT_EYE_INNER, self.RIGHT_EYE_OUTER)

            raw = ((left_ratio + right_ratio) / 2.0) - 0.5
            gaze = self._apply_calibration(raw) * self.config.GAZE_SENSITIVITY

            self._history.append(gaze)
            self.gaze_x = float(np.mean(self._history))

            if self.calibrating and self.calib_step < 3:
                key = self._CALIB_KEYS[self.calib_step]
                self._calib_raw[key].append(raw)

            self._draw(annotated, lms, left_iris, right_iris, w, h)
        else:
            self.face_detected = False
            cv2.putText(annotated, "Rostro no detectado",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                        0.7, (0, 0, 200), 2)

        return annotated

    # ----------------------------------------------------------
    # Conversión a ángulo de servo
    # ----------------------------------------------------------

    def get_servo_angle(self) -> float:
        cfg = self.config
        center = float(cfg.SERVO_CENTER_ANGLE)
        left   = float(cfg.SERVO_LEFT_ANGLE)
        right  = float(cfg.SERVO_RIGHT_ANGLE)
        dz     = cfg.DEAD_ZONE_RATIO

        if abs(self.gaze_x) < dz:
            return center
        if self.gaze_x < 0:
            ratio = min(abs(self.gaze_x), 1.0)
            return center + (left - center) * ratio
        else:
            ratio = min(self.gaze_x, 1.0)
            return center + (right - center) * ratio

    # ----------------------------------------------------------
    # Calibración
    # ----------------------------------------------------------

    def start_calibration(self):
        self.calibrating = True
        self.calib_step = 0
        self._calib_raw = {"center": [], "left": [], "right": []}
        logger.info("Calibración iniciada")

    def advance_calibration(self) -> tuple:
        if not self.calibrating:
            return False, "La calibración no está activa"
        if self.calib_step >= 3:
            return False, "Calibración ya completada"

        key = self._CALIB_KEYS[self.calib_step]
        measurements = self._calib_raw[key]

        if len(measurements) < 5:
            return False, "Mantén la posición unos segundos más"

        self.calib_step += 1

        if self.calib_step >= 3:
            return self._finish_calibration()

        next_key = self._CALIB_KEYS[self.calib_step]
        return True, f"Ahora mira hacia la {next_key}"

    def _finish_calibration(self) -> tuple:
        try:
            c = float(np.mean(self._calib_raw["center"]))
            l = float(np.mean(self._calib_raw["left"]))
            r = float(np.mean(self._calib_raw["right"]))
            self.calibration.update({"center": c, "left": l, "right": r, "calibrated": True})
            self.calibrating = False
            self._save_calibration()
            logger.info(f"Calibración completada: {self.calibration}")
            return True, "Calibración guardada correctamente"
        except Exception as e:
            logger.error(f"Error al finalizar calibración: {e}")
            return False, str(e)

    def cancel_calibration(self):
        self.calibrating = False
        self.calib_step = 0
        self._calib_raw = {"center": [], "left": [], "right": []}

    def _save_calibration(self):
        try:
            with open(self.config.CALIBRATION_FILE, "w", encoding="utf-8") as f:
                json.dump(self.calibration, f, indent=2)
        except Exception as e:
            logger.error(f"No se pudo guardar calibración: {e}")

    def _load_calibration(self):
        try:
            if os.path.exists(self.config.CALIBRATION_FILE):
                with open(self.config.CALIBRATION_FILE, "r", encoding="utf-8") as f:
                    self.calibration.update(json.load(f))
                logger.info("Calibración cargada desde archivo")
        except Exception as e:
            logger.warning(f"No se pudo cargar calibración: {e}")

    # ----------------------------------------------------------
    # Estado
    # ----------------------------------------------------------

    def get_state(self) -> dict:
        return {
            "face_detected": self.face_detected,
            "gaze_x": round(self.gaze_x, 3),
            "calibrated": self.calibration["calibrated"],
            "calibrating": self.calibrating,
            "calib_step": self.calib_step,
            "servo_angle": self.get_servo_angle() if self.face_detected else None
        }

    def shutdown(self):
        if not self._new_api:
            self.face_mesh.close()

    # ----------------------------------------------------------
    # Helpers internos
    # ----------------------------------------------------------

    def _center(self, lms: list, indices: list) -> tuple:
        pts = [lms[i] for i in indices]
        return (int(np.mean([p[0] for p in pts])),
                int(np.mean([p[1] for p in pts])))

    def _eye_ratio(self, lms, iris_center, outer_idx, inner_idx) -> float:
        outer = lms[outer_idx]
        inner = lms[inner_idx]
        width = inner[0] - outer[0]
        if abs(width) < 1:
            return 0.5
        return max(0.0, min(1.0, (iris_center[0] - outer[0]) / width))

    def _apply_calibration(self, raw: float) -> float:
        c = self.calibration["center"]
        adj = raw - c
        if not self.calibration["calibrated"]:
            return adj
        l = self.calibration["left"]  - c
        r = self.calibration["right"] - c
        if adj < 0:
            span = abs(l) if abs(l) > 0 else 1.0
            return adj / span
        else:
            span = abs(r) if abs(r) > 0 else 1.0
            return adj / span

    def _draw(self, frame, lms, left_iris, right_iris, w, h):
        cv2.circle(frame, left_iris,  5, (0, 255, 0), -1)
        cv2.circle(frame, right_iris, 5, (0, 255, 0), -1)

        for idx in [self.LEFT_EYE_OUTER, self.LEFT_EYE_INNER,
                    self.RIGHT_EYE_INNER, self.RIGHT_EYE_OUTER]:
            cv2.circle(frame, lms[idx], 3, (255, 80, 0), -1)

        cx, cy = w // 2, h - 25
        ex = int(cx + self.gaze_x * 70)
        cv2.arrowedLine(frame, (cx, cy), (ex, cy), (0, 255, 255), 2, tipLength=0.3)

        if abs(self.gaze_x) < self.config.DEAD_ZONE_RATIO:
            direction, color = "CENTRO",    (0, 255, 200)
        elif self.gaze_x < 0:
            direction, color = "IZQUIERDA", (0, 200, 255)
        else:
            direction, color = "DERECHA",   (255, 200, 0)

        cv2.putText(frame, f"Mirada: {direction}  ({self.gaze_x:+.2f})",
                    (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)

        if self.calibrating:
            labels = ["CENTRO", "IZQUIERDA", "DERECHA"]
            label = labels[self.calib_step] if self.calib_step < 3 else "COMPLETO"
            meds = len(self._calib_raw.get(self._CALIB_KEYS[min(self.calib_step, 2)], []))
            cv2.putText(frame, f"CALIBRANDO: mira {label}  ({meds} muestras)",
                        (10, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)
