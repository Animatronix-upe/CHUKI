"""
motion_detection.py - Detección de CARAS con MediaPipe

Sigue ÚNICAMENTE rostros humanos — ignora manos, objetos y movimiento de fondo.
Cuando hay varias personas, sigue a la cara más grande (= más cercana a la cámara).
Cuando no hay cara visible, mantiene la última posición 2.5 segundos antes de volver
al centro.
"""

import cv2
import numpy as np
import time
import logging
from collections import deque

logger = logging.getLogger(__name__)

# Resolución de procesado (más pequeño = más rápido)
_PROC_W = 320
_PROC_H = 240


class MotionDetector:

    def __init__(self, config):
        self.config = config

        self.motion_detected  = False
        self.motion_center_x  = 0.5    # 0.0=izq … 1.0=der
        self.motion_area      = 0      # área relativa de la cara seguida (px²)
        self._last_face_time  = 0.0
        self._hold_secs       = 2.5    # segundos que se mantiene la posición al perder la cara
        self._pos_history     = deque(maxlen=8)

        self._detector = None
        self._init_detector()

    # ----------------------------------------------------------
    # Inicialización
    # ----------------------------------------------------------

    def _init_detector(self):
        try:
            import mediapipe as mp
            # model_selection=1 → detecta caras hasta ~5 metros (ideal para presentaciones)
            self._detector = mp.solutions.face_detection.FaceDetection(
                model_selection=1,
                min_detection_confidence=0.55,
            )
            logger.info("MotionDetector: usando MediaPipe FaceDetection")
        except Exception as e:
            logger.error("MediaPipe FaceDetection no disponible: %s — usando Haar cascade", e)
            self._detector = None

    def _init_haar(self):
        """Fallback: Haar cascade de OpenCV si MediaPipe no está disponible."""
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._haar = cv2.CascadeClassifier(cascade_path)

    # ----------------------------------------------------------
    # Procesamiento de frame
    # ----------------------------------------------------------

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        annotated = frame.copy()

        # Reducir resolución para rapidez
        small = cv2.resize(frame, (_PROC_W, _PROC_H))
        sx = w / _PROC_W
        sy = h / _PROC_H

        faces = self._detect_faces(small, sx, sy, w, h)

        if faces:
            # Ordenar por área descendente → la más grande es la más cercana
            faces.sort(key=lambda f: f["area"], reverse=True)
            best = faces[0]

            self._last_face_time = time.time()
            self._pos_history.append(best["cx"] / w)
            self.motion_center_x = float(np.mean(self._pos_history))
            self.motion_area      = int(best["area"])
            self.motion_detected  = True

            # Dibujar: cara principal en verde brillante, resto en gris
            for i, f in enumerate(faces):
                color     = (0, 255, 0) if i == 0 else (80, 80, 80)
                thickness = 2 if i == 0 else 1
                cv2.rectangle(annotated, (f["x1"], f["y1"]),
                              (f["x2"], f["y2"]), color, thickness)
                if i == 0:
                    cv2.circle(annotated, (f["cx"], f["cy"]), 8, (0, 0, 255), -1)
                    label = f"Cara  ({self._direction()})"
                    cv2.putText(annotated, label,
                                (f["x1"], max(f["y1"] - 8, 15)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
                else:
                    cv2.putText(annotated, "ignorada",
                                (f["x1"], max(f["y1"] - 5, 15)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (80, 80, 80), 1)
        else:
            elapsed = time.time() - self._last_face_time
            if elapsed < self._hold_secs:
                # Cara brevemente perdida → mantener posición
                self.motion_detected = True
                secs_left = int(self._hold_secs - elapsed) + 1
                cv2.putText(annotated, f"Buscando cara… ({secs_left}s)",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 165, 255), 2)
            else:
                self.motion_detected  = False
                self.motion_center_x  = 0.5
                self._pos_history.clear()
                cv2.putText(annotated, "Sin cara detectada",
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (100, 100, 100), 2)

        # Líneas de referencia
        cv2.line(annotated, (w // 2, 0), (w // 2, h), (60, 60, 60), 1)
        cv2.line(annotated, (w // 3, 0), (w // 3, h), (40, 40, 40), 1)
        cv2.line(annotated, (2 * w // 3, 0), (2 * w // 3, h), (40, 40, 40), 1)

        return annotated

    def _detect_faces(self, small, sx, sy, orig_w, orig_h):
        """Detecta caras y devuelve lista de dicts con coordenadas en frame original."""
        faces = []

        if self._detector is not None:
            # --- MediaPipe FaceDetection ---
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            results = self._detector.process(rgb)
            rgb.flags.writeable = True

            if results.detections:
                for det in results.detections:
                    bb = det.location_data.relative_bounding_box
                    x1 = int(max(0, bb.xmin) * _PROC_W * sx)
                    y1 = int(max(0, bb.ymin) * _PROC_H * sy)
                    bw = int(bb.width  * _PROC_W * sx)
                    bh = int(bb.height * _PROC_H * sy)
                    x2 = min(orig_w - 1, x1 + bw)
                    y2 = min(orig_h - 1, y1 + bh)
                    faces.append({
                        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                        "cx": x1 + bw // 2,
                        "cy": y1 + bh // 2,
                        "area": bw * bh,
                    })
        else:
            # --- Fallback: Haar cascade ---
            if not hasattr(self, "_haar"):
                self._init_haar()
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            detected = self._haar.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30)
            )
            for (fx, fy, fw, fh) in (detected if len(detected) > 0 else []):
                x1 = int(fx * sx)
                y1 = int(fy * sy)
                x2 = int((fx + fw) * sx)
                y2 = int((fy + fh) * sy)
                faces.append({
                    "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                    "cx": x1 + int(fw * sx) // 2,
                    "cy": y1 + int(fh * sy) // 2,
                    "area": int(fw * sx * fh * sy),
                })

        return faces

    # ----------------------------------------------------------
    # Conversión a ángulo de servo
    # ----------------------------------------------------------

    def get_servo_angle(self) -> float:
        cfg    = self.config
        center = float(cfg.SERVO_CENTER_ANGLE)
        left   = float(cfg.SERVO_LEFT_ANGLE)
        right  = float(cfg.SERVO_RIGHT_ANGLE)
        dz     = cfg.DEAD_ZONE_RATIO * 0.5

        pos = self.motion_center_x  # 0.0–1.0

        if abs(pos - 0.5) < dz:
            return center
        if pos < 0.5:
            ratio = min((0.5 - pos) / 0.5, 1.0)
            return center + (left - center) * ratio
        else:
            ratio = min((pos - 0.5) / 0.5, 1.0)
            return center + (right - center) * ratio

    # ----------------------------------------------------------
    # Estado
    # ----------------------------------------------------------

    def get_state(self) -> dict:
        return {
            "motion_detected":  self.motion_detected,
            "motion_center_x":  round(self.motion_center_x, 3),
            "motion_area":      self.motion_area,
            "direction":        self._direction() if self.motion_detected else "NINGUNO",
            "servo_angle":      self.get_servo_angle() if self.motion_detected else None,
        }

    def reset(self):
        """Reinicia el detector."""
        if self._detector is not None:
            self._init_detector()
        self._pos_history.clear()
        self._last_face_time = 0.0
        logger.info("FaceDetector reiniciado")

    # ----------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------

    def _direction(self) -> str:
        if self.motion_center_x < 0.35:
            return "IZQUIERDA"
        elif self.motion_center_x > 0.65:
            return "DERECHA"
        return "CENTRO"
