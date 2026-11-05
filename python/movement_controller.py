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
from collections import deque

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

        # Cuello
        self.neck_target         = float(getattr(config, 'NECK_CENTER_ANGLE', 90))
        self.current_neck_angle  = float(getattr(config, 'NECK_CENTER_ANGLE', 90))
        self._last_sent_neck     = -999
        self.neck_sync           = getattr(config, 'NECK_SYNC_WITH_EYES', True)
        self._neck_override_until = 0.0

        # Preferencias de usuario para cuello (se aplican en modo manual/rest)
        self._user_neck_sync      = self.neck_sync
        self._user_eye_neck_delay = 0.0  # Se actualiza tras leer NECK_DELAY_MS

        # Tiempo del último objetivo recibido en modos automáticos
        self._last_target_time = time.time()

        # Retardo ojo→cuello: buffer circular con (timestamp, angle)
        # El cuello ve el ángulo del ojo tal como era hace eye_neck_delay segundos
        self.eye_neck_delay = float(getattr(config, 'NECK_DELAY_MS', 300)) / 1000.0
        self._user_eye_neck_delay = self.eye_neck_delay  # guardar preferencia del usuario
        _center = float(config.SERVO_CENTER_ANGLE)
        self._eye_history = deque([(time.time(), _center)])  # 1 entry inicial

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

            # Comportamiento del cuello según modo:
            if mode == "gaze":
                # Solo ojos — cuello se queda quieto donde está
                self.neck_sync = False
                self.neck_target = self.current_neck_angle
            elif mode == "motion":
                # Detección — ojos primero, cuello sigue con 2 segundos de retardo
                self.neck_sync = True
                self.eye_neck_delay = 2.0
                self._neck_override_until = 0.0
            else:
                # Manual / rest — restaurar preferencias del usuario
                self.neck_sync = self._user_neck_sync
                self.eye_neck_delay = self._user_eye_neck_delay
                self._neck_override_until = 0.0

        logger.info(f"Modo: {mode}")
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
        """Posición directa desde el control manual (ojos)."""
        with self._lock:
            angle = self._clamp(angle)
            self._mode_targets["manual"] = angle
            if self.mode == "manual":
                self.target_angle = angle
                self._last_target_time = time.time()

    def set_neck_manual_position(self, angle: float):
        """Posición directa del cuello. Activa override de sync por 3 segundos."""
        with self._lock:
            neck_min = float(getattr(self.config, 'NECK_MIN_ANGLE', 0))
            neck_max = float(getattr(self.config, 'NECK_MAX_ANGLE', 180))
            self.neck_target = max(neck_min, min(neck_max, float(angle)))
            self._neck_override_until = time.time() + 3.0  # ignora sync 3s

    def set_neck_sync(self, enabled: bool):
        """Activa/desactiva sincronización cuello→ojos (guarda preferencia de usuario)."""
        with self._lock:
            self._user_neck_sync = enabled
            # Solo aplica si no estamos en un modo automático
            if self.mode not in ("gaze", "motion"):
                self.neck_sync = enabled

    def get_neck_angle(self) -> int:
        with self._lock:
            if self.neck_sync:
                return int(round(self.current_angle))
            return int(round(self.neck_target))

    def center(self):
        """Centra ojos y cuello inmediatamente."""
        center = float(self.config.SERVO_CENTER_ANGLE)
        with self._lock:
            self.target_angle = center
            self.neck_target  = center
            self._last_target_time = time.time()
        self.serial.send_center()

    def stop(self):
        """Parada de emergencia: detiene ojos y cuello."""
        with self._lock:
            self.mode = "rest"
            self._mode_targets["rest"] = self.current_angle
            self.target_angle = self.current_angle
            self.neck_target  = float(self._last_sent_neck) if self._last_sent_neck >= 0 else float(getattr(self.config, 'NECK_CENTER_ANGLE', 90))
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
            if "eye_neck_delay_ms" in settings:
                val = max(0.0, min(2000.0, float(settings["eye_neck_delay_ms"]))) / 1000.0
                self._user_eye_neck_delay = val
                # Solo aplica si no estamos en un modo automático con delay propio
                if self.mode not in ("gaze", "motion"):
                    self.eye_neck_delay = val

    def get_status(self) -> dict:
        with self._lock:
            neck_angle = int(round(self.current_angle)) if self.neck_sync else int(round(self.neck_target))
            return {
                "mode": self.mode,
                "current_angle": int(round(self.current_angle)),
                "target_angle": int(round(self.target_angle)),
                "neck_angle": neck_angle,
                "neck_sync": self.neck_sync,
                "neck_mode": ("gaze_locked" if self.mode == "gaze"
                              else "motion_delayed" if self.mode == "motion"
                              else "user"),
                "connected": self.serial.connected,
                "smoothing": self.smoothing,
                "dead_zone": self.dead_zone,
                "servo_min": self.config.SERVO_MIN_ANGLE,
                "servo_max": self.config.SERVO_MAX_ANGLE,
                "servo_center": self.config.SERVO_CENTER_ANGLE,
                "eye_neck_delay_ms": int(round(self.eye_neck_delay * 1000)),
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
            time.sleep(0.02)   # ~50 Hz

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

            # Ojos — enviar si el cambio supera el umbral mínimo
            angle_int = int(round(self.current_angle))
            if abs(angle_int - self._last_sent_angle) >= self.min_movement:
                if self.serial.connected:
                    self.serial.send_position(angle_int)
                    self._last_sent_angle = angle_int

            # Buffer histórico para retardo ojo→cuello
            now_t = time.time()
            self._eye_history.append((now_t, float(angle_int)))
            # Purgar entradas más viejas que delay + 1s de margen
            cutoff = now_t - self.eye_neck_delay - 1.0
            while len(self._eye_history) > 1 and self._eye_history[0][0] < cutoff:
                self._eye_history.popleft()

            # Ángulo del ojo tal como estaba hace eye_neck_delay segundos
            delayed_eye = self._eye_history[0][1]  # fallback: la entrada más antigua
            for ts, a in self._eye_history:
                if now_t - ts >= self.eye_neck_delay:
                    delayed_eye = a  # la entrada más reciente que ya tiene la edad mínima
                else:
                    break

            # Cuello — calcular objetivo, suavizar, enviar
            neck_min = float(getattr(self.config, 'NECK_MIN_ANGLE', 0))
            neck_max = float(getattr(self.config, 'NECK_MAX_ANGLE', 110))
            neck_smooth = float(getattr(self.config, 'NECK_SMOOTHING_FACTOR', 0.88))
            neck_min_mv = int(getattr(self.config, 'NECK_MIN_MOVEMENT', 2))
            in_override = time.time() < self._neck_override_until
            if in_override:
                neck_raw = float(self.neck_target)
            elif self.neck_sync:
                # Remap eye angle [SERVO_MIN..SERVO_MAX] → neck angle [neck_min..neck_max]
                eye_servo_min = float(getattr(self.config, 'SERVO_MIN_ANGLE', 0))
                eye_servo_max = float(getattr(self.config, 'SERVO_MAX_ANGLE', 180))
                eye_range = max(1.0, eye_servo_max - eye_servo_min)
                neck_ratio = (delayed_eye - eye_servo_min) / eye_range
                if getattr(self.config, 'NECK_INVERT', False):
                    neck_ratio = 1.0 - neck_ratio
                neck_raw = neck_min + neck_ratio * (neck_max - neck_min)
            else:
                neck_raw = float(self.neck_target)
            # Clamp al rango permitido
            neck_raw = max(neck_min, min(neck_max, neck_raw))
            # Suavizado exponencial propio del cuello
            self.current_neck_angle = (
                self.current_neck_angle * neck_smooth + neck_raw * (1.0 - neck_smooth)
            )
            neck_int = int(round(self.current_neck_angle))
            if abs(neck_int - self._last_sent_neck) >= neck_min_mv:
                if self.serial.connected:
                    self.serial.send_neck_position(neck_int)
                    self._last_sent_neck = neck_int

    # ----------------------------------------------------------
    # Utilidades privadas
    # ----------------------------------------------------------

    def _clamp(self, angle: float) -> float:
        return max(float(self.config.SERVO_MIN_ANGLE),
                   min(float(self.config.SERVO_MAX_ANGLE), angle))
