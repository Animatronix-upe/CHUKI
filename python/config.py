"""
config.py - Configuración central del Animatrónico

VALORES PROVISIONALES marcados con [PROVISIONAL].
Ajústalos antes de encender el sistema.
"""

import os

# ================================================================
# SERIAL / ARDUINO
# ================================================================
# Pines Arduino confirmados:
#   Pin 9  → Servo ojos   (SG90)
#   Pin 10 → Servo cuello (SG90)
#   Pin 12 → LEDs ojos
# Hardware confirmado: Arduino UNO R3 (clon)
# [PROVISIONAL] Identifica tu puerto en Arduino IDE > Herramientas > Puerto
SERIAL_PORT = "COM16"
SERIAL_BAUDRATE = 9600
SERIAL_TIMEOUT = 1.0        # segundos de espera en lectura
SERIAL_RECONNECT_DELAY = 3.0  # segundos entre intentos de reconexión

# ================================================================
# SERVO CUELLO — pin 10 Arduino
# ================================================================
NECK_MIN_ANGLE    = 0
NECK_MAX_ANGLE    = 180
NECK_CENTER_ANGLE = 90
NECK_SYNC_WITH_EYES = True  # True = cuello sigue a los ojos automáticamente

# ================================================================
# CÁMARA
# ================================================================
# [PROVISIONAL] 0 = cámara integrada, 1 = primera cámara externa
CAMERA_INDEX = 0
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30

# ================================================================
# CÁMARA ESP32-CAM (opcional — deja vacío para usar webcam del PC)
# ================================================================
# Si se configura, reemplaza la webcam del PC por el stream del ESP32.
# Formato: "http://<IP_ESP32>:81/stream"
# Ejemplo: "http://192.168.1.105:81/stream"
ESP32_CAMERA_URL = ""

# ================================================================
# SERVOMOTOR SG90 — LÍMITES MECÁNICOS
# ================================================================
# Hardware confirmado: SG90 (9g, 4.8-6V, cable: naranja=señal, rojo=VCC, marrón=GND)
# El mecanismo usa una varilla corta: el recorrido real del servo es limitado.
# MANTÉN estos límites conservadores hasta verificar que el mecanismo
# no choca en los extremos. Amplía de 5 en 5 grados desde la web (Ajustes).
SERVO_MIN_ANGLE = 0      # Tope físico izquierdo del SG90
SERVO_MAX_ANGLE = 180    # Tope físico derecho  del SG90
SERVO_CENTER_ANGLE = 90  # Posición central (ojos al frente)
SERVO_LEFT_ANGLE = 5     # Posición izquierda para seguimiento
SERVO_RIGHT_ANGLE = 175  # Posición derecha para seguimiento

# ================================================================
# LEDs DE LOS OJOS
# ================================================================
# Hardware confirmado: LEDs instalados en los ojos
# Conectar al pin 12 del Arduino UNO con resistencia de 220Ω por LED
LED_PIN = 12          # Pin digital del Arduino para los LEDs
LED_ON_AT_START = True  # Encender LEDs al iniciar

# ================================================================
# CONTROL DE MOVIMIENTO
# ================================================================
# Suavizado exponencial: 0.0 = sin suavizado, 0.95 = muy lento
SMOOTHING_FACTOR = 0.25
# Zona muerta: fracción del ancho de imagen donde no hay movimiento
DEAD_ZONE_RATIO = 0.05
# Mínimo de grados para enviar un nuevo comando al servo
MOVEMENT_MIN_DEGREES = 2
# Segundos sin detección antes de volver al centro
RETURN_TO_CENTER_TIMEOUT = 3.0

# ================================================================
# SEGUIMIENTO DE MIRADA
# ================================================================
# Número de frames para promediar (mayor = más suave pero más lento)
GAZE_HISTORY_LENGTH = 8
# Multiplicador de sensibilidad (1.0 = normal)
GAZE_SENSITIVITY = 2.5

# ================================================================
# DETECCIÓN DE MOVIMIENTO
# ================================================================
# Área mínima en píxeles para considerar movimiento real
MOTION_MIN_AREA = 1000
# Tamaño del kernel de desenfoque (debe ser impar)
MOTION_BLUR_SIZE = 21
# Umbral de diferencia de píxeles
MOTION_THRESHOLD = 25
# Segundos sin movimiento para volver al centro
MOTION_RETURN_TIMEOUT = 2.0

# ================================================================
# ARCHIVOS
# ================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CALIBRATION_FILE = os.path.join(BASE_DIR, "..", "calibration.json")

# ================================================================
# SERVIDOR WEB
# ================================================================
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000
FLASK_DEBUG = False

# ================================================================
# LOG
# ================================================================
LOG_LEVEL = "INFO"
LOG_FILE = os.path.join(BASE_DIR, "..", "animatronico.log")
