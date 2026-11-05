"""
motion_detection.py - Detección de movimiento con sustracción de fondo

LIMITACIONES:
  - No identifica personas, sólo áreas de cambio en la imagen.
  - Sensible a cambios de iluminación (nubes, lámparas intermitentes).
  - Si hay varios objetos en movimiento, se sigue el área mayor.
  - Requiere unos segundos iniciales para aprender el fondo.
"""

import cv2
import numpy as np
import time
import logging
from collections import deque

logger = logging.getLogger(__name__)


class MotionDetector:

    def __init__(self, config):
        self.config = config
        self._reset_bg()

        self.motion_detected = False
        self.motion_center_x = 0.5   # 0.0=izq … 1.0=der
        self.motion_area = 0.0
        self._last_motion_time = 0.0
        self._pos_history = deque(maxlen=6)

    # ----------------------------------------------------------
    # Procesamiento de frame
    # ----------------------------------------------------------

    def process_frame(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        annotated = frame.copy()

        # Sustracción de fondo
        fg_mask = self._bg_subtractor.apply(frame)

        # Reducir ruido morfológicamente
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
        fg_mask = cv2.dilate(fg_mask, kernel, iterations=2)

        # Contornos
        contours, _ = cv2.findContours(
            fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        valid = [c for c in contours
                 if cv2.contourArea(c) > self.config.MOTION_MIN_AREA]

        if valid:
            largest = max(valid, key=cv2.contourArea)
            self.motion_area = cv2.contourArea(largest)
            M = cv2.moments(largest)
            if M["m00"] > 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])

                self._pos_history.append(cx / w)
                self.motion_center_x = float(np.mean(self._pos_history))
                self.motion_detected = True
                self._last_motion_time = time.time()

                # Dibujar contornos y centro
                cv2.drawContours(annotated, valid, -1, (0, 200, 100), 1)
                cv2.drawContours(annotated, [largest], -1, (0, 255, 0), 2)
                cv2.circle(annotated, (cx, cy), 8, (0, 0, 255), -1)
        else:
            timeout = self.config.MOTION_RETURN_TIMEOUT
            if time.time() - self._last_motion_time > timeout:
                self.motion_detected = False
                self.motion_center_x = 0.5

        # Líneas de referencia
        cv2.line(annotated, (w // 2, 0), (w // 2, h), (80, 80, 80), 1)
        cv2.line(annotated, (w // 3, 0), (w // 3, h), (50, 50, 50), 1)
        cv2.line(annotated, (2 * w // 3, 0), (2 * w // 3, h), (50, 50, 50), 1)

        # Texto de estado
        if self.motion_detected:
            direction = self._direction()
            color = (0, 255, 150)
            cv2.putText(annotated,
                        f"Movimiento: {direction}  (area={self.motion_area:.0f}px)",
                        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
            ix = int(self.motion_center_x * w)
            cv2.circle(annotated, (ix, h - 18), 9, (0, 255, 255), -1)
        else:
            cv2.putText(annotated, "Sin movimiento",
                        (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (100, 100, 100), 2)

        return annotated

    # ----------------------------------------------------------
    # Conversión a ángulo de servo
    # ----------------------------------------------------------

    def get_servo_angle(self) -> float:
        cfg = self.config
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
            "motion_detected": self.motion_detected,
            "motion_center_x": round(self.motion_center_x, 3),
            "motion_area": int(self.motion_area),
            "direction": self._direction() if self.motion_detected else "NINGUNO",
            "servo_angle": self.get_servo_angle() if self.motion_detected else None
        }

    def reset(self):
        """Reinicia el modelo de fondo."""
        self._reset_bg()
        self._pos_history.clear()
        logger.info("Detector de movimiento reiniciado")

    # ----------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------

    def _reset_bg(self):
        self._bg_subtractor = cv2.createBackgroundSubtractorMOG2(
            history=150, varThreshold=50, detectShadows=False
        )

    def _direction(self) -> str:
        if self.motion_center_x < 0.35:
            return "IZQUIERDA"
        elif self.motion_center_x > 0.65:
            return "DERECHA"
        return "CENTRO"
