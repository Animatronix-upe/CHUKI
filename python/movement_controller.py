"""
movement_controller.py - Controlador central de movimiento

Un único punto de control recibe la posición objetivo del modo activo
y genera comandos seguros para el servo. Ningún otro módulo habla
directamente con serial_controller para mover el servo.

Modos disponibles:
  'gaze'   - Seguimiento de mirada (automático)
  'motion' - Detección de movimiento (automático)
  'manual' - Control desde la web (manual)
  'rest'   - Reposo, ojos centrados
"""

import threading
import time
import logging

logger = logging.getLogger(__name__)


class MovementController:

    MODES = ("gaze", "motion", "manual", "rest")

    def __init__(self, config, serial_controller):
        self.config = config
        self.serial = serial_controller

        self.mode = "rest"
        self._active = True

        # Ángulo suavizado actual y objetivo
        self.current_angle = float(config.SERVO_CENTER_ANGLE)
        self.target_angle = float(config.SERVO_CENTER_ANGLE)
        self._last_sent_angle = -999  # fuerza el primer envío

        # Objetivos por modo (se actualiza según el modo activo)
        self._mode_targets = {m: float(config.SERVO_CENTER_ANGLE) for m in self.MODES}

        # Tiempo del último objetivo recibido en modos automáticos
        self._last_target_time = time.time()

        # Parámetros ajustables desde la web
        self.smoothing = config.SMOOTHING_FACTOR          # 0.0–0.95
        self.dead_zone = config.DEAD_ZONE_RATIO           # fracción
        self.min_movement = config.MOVEMENT_MIN_DEGREES   # grados
        self.return_timeout = config.RETURN_TO_CENTER_TIMEOUT  # segundos

        self._lock = threading.Lock()

        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="movement-ctrl"
        )
        self._thread.start()

    # ----------------------------------------------------------
    # Interfaz pública
    # ----------------------------------------------------------

    def set_mode(self, mode: str) -> bool:
        if mode not in self.MODES:
            logger.error(f"Modo desconocido: {mode}")
            return False
        with self._lock:
            self.mode = mode
            if mode == "rest":
                self._mode_targets["rest"] = float(self.config.SERVO_CENTER_ANGLE)
        logger.info(f"Modo → {mode}")
        return True

    def set_target(self, angle: float, mode: str):
        """
        El modo activo actualiza su objetivo.
        Si el modo no coincide con el activo, se ignora.
        """
        with self._lock:
            if mode != self.mode:
                return
            angle = self._clamp(angle)
            self._mode_targets[mode] = angle
            self._last_target_time = time.time()

    def set_manual_position(self, angle: float):
        """Posición directa desde el control manual."""
        with self._lock:
            angle = self._clamp(angle)
            self._mode_targets["manual"] = angle
            if self.mode == "manual":
                self.target_angle = angle
                self._last_target_time = time.time()

    def center(self):
        """Centra los ojos inmediatamente."""
        center = float(self.config.SERVO_CENTER_ANGLE)
        with self._lock:
            self.target_angle = center
            self._last_target_time = time.time()
        self.serial.send_center()

    def stop(self):
        """Parada de emergencia: detiene el movimiento automático."""
        with self._lock:
            self.mode = "rest"
            # Mantiene la posición actual
            self._mode_targets["rest"] = self.current_angle
            self.target_angle = self.current_angle
        self.serial.send_stop()
        logger.warning("PARADA DE EMERGENCIA")

    def update_settings(self, settings: dict):
        """Actualiza parámetros desde la interfaz web."""
        with self._lock:
            if "smoothing" in settings:
                self.smoothing = max(0.0, min(0.95, float(settings["smoothing"])))
            if "dead_zone" in settings:
                self.dead_zone = max(0.0, min(0.5, float(settings["dead_zone"])))
            if "min_movement" in settings:
                self.min_movement = max(1, min(20, int(settings["min_movement"])))
            if "return_timeout" in settings:
                self.return_timeout = max(0.5, min(30.0, float(settings["return_timeout"])))
            if "servo_min" in settings:
                self.config.SERVO_MIN_ANGLE = int(settings["servo_min"])
            if "servo_max" in settings:
                self.config.SERVO_MAX_ANGLE = int(settings["servo_max"])
            if "servo_center" in settings:
                self.config.SERVO_CENTER_ANGLE = int(settings["servo_center"])
            if "servo_left" in settings:
                self.config.SERVO_LEFT_ANGLE = int(settings["servo_left"])
            if "servo_right" in settings:
                self.config.SERVO_RIGHT_ANGLE = int(settings["servo_right"])

    def get_status(self) -> dict:
        with self._lock:
            return {
                "mode": self.mode,
                "current_angle": int(round(self.current_angle)),
                "target_angle": int(round(self.target_angle)),
                "connected": self.serial.connected,
                "smoothing": self.smoothing,
                "dead_zone": self.dead_zone,
                "servo_min": self.config.SERVO_MIN_ANGLE,
                "servo_max": self.config.SERVO_MAX_ANGLE,
                "servo_center": self.config.SERVO_CENTER_ANGLE,
            }

    def shutdown(self):
        self._active = False
        self.serial.send_center()
        time.sleep(0.15)
        self.serial.disconnect()

    # ----------------------------------------------------------
    # Bucle de control (hilo)
    # ----------------------------------------------------------

    def _loop(self):
        while self._active:
            try:
                self._tick()
            except Exception as e:
                logger.error(f"Error en bucle de control: {e}")
            time.sleep(0.033)  # ~30 Hz

    def _tick(self):
        with self._lock:
            mode = self.mode

            # Retorno al centro por timeout en modos automáticos
            if mode in ("gaze", "motion"):
                if time.time() - self._last_target_time > self.return_timeout:
                    self._mode_targets[mode] = float(self.config.SERVO_CENTER_ANGLE)

            # Selección del objetivo según modo activo
            self.target_angle = self._mode_targets[mode]

            # Suavizado exponencial
            s = self.smoothing
            self.current_angle = self.current_angle * s + self.target_angle * (1.0 - s)

            # Enviar comando solo si el cambio supera el umbral mínimo
            angle_int = int(round(self.current_angle))
            if abs(angle_int - self._last_sent_angle) >= self.min_movement:
                if self.serial.connected:
                    self.serial.send_position(angle_int)
                    self._last_sent_angle = angle_int

    # ----------------------------------------------------------
    # Utilidades privadas
    # ----------------------------------------------------------

    def _clamp(self, angle: float) -> float:
        return max(float(self.config.SERVO_MIN_ANGLE),
                   min(float(self.config.SERVO_MAX_ANGLE), angle))
