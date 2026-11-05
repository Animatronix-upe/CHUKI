"""
serial_controller.py - Comunicación USB con Arduino

Protocolo definido:
  POS,90\n   → Mover servo a 90 grados
  CENTER\n   → Mover al centro
  STOP\n     → Detener y mantener posición
  PING\n     → Verificar conexión (Arduino responde PONG)
  STATUS\n   → Arduino responde POS,<ángulo_actual>
"""

import serial
import serial.tools.list_ports
import threading
import time
import logging

logger = logging.getLogger(__name__)


class SerialController:

    def __init__(self, config):
        self.config = config
        self.port = config.SERIAL_PORT
        self.baudrate = config.SERIAL_BAUDRATE
        self.connection = None
        self.connected = False
        self._lock = threading.Lock()
        self._reconnect_thread = None
        self._stop_reconnect = threading.Event()

        # Puerto secundario para mandíbula (segundo Arduino)
        self.jaw_port = getattr(config, 'JAW_SERIAL_PORT', None)
        self.jaw_connection = None
        self.jaw_connected = False
        self._jaw_lock = threading.Lock()

    # ----------------------------------------------------------
    # Conexión
    # ----------------------------------------------------------

    def connect(self) -> bool:
        """Intenta conectar con Arduino. Devuelve True si tiene éxito."""
        try:
            conn = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.config.SERIAL_TIMEOUT,
                dsrdtr=False,   # No bajar DTR → no resetear otros Arduinos en el hub
                rtscts=False,
            )
            # Arduino UNO/Nano se reinician al abrir el puerto; esperar
            time.sleep(2.5)

            # Verificar con PING
            conn.reset_input_buffer()
            conn.write(b"PING\n")
            response = conn.readline().decode("utf-8", errors="ignore").strip()

            with self._lock:
                self.connection = conn
                self.connected = True

            if response == "PONG":
                logger.info(f"Arduino conectado en {self.port} (PONG recibido)")
            else:
                logger.warning(
                    f"Arduino en {self.port} respondió: '{response}' (esperaba PONG)"
                )
            return True

        except serial.SerialException as e:
            logger.error(f"No se pudo conectar a {self.port}: {e}")
            with self._lock:
                self.connected = False
            return False

    def connect_jaw(self) -> bool:
        """Conecta con el Arduino de la mandíbula (segundo puerto).
        El sketch de boca usa Serial.parseInt() — no responde a PING."""
        if not self.jaw_port:
            return False
        try:
            conn = serial.Serial(
                port=self.jaw_port,
                baudrate=self.baudrate,
                timeout=self.config.SERIAL_TIMEOUT,
                dsrdtr=False,   # No bajar DTR → no resetear otros Arduinos en el hub
                rtscts=False,
            )
            # Dar tiempo al Arduino de boca para que arranque
            time.sleep(2.5)
            conn.reset_input_buffer()
            # Cerrar boca al iniciar
            conn.write(b"0\n")
            with self._jaw_lock:
                self.jaw_connection = conn
                self.jaw_connected = True
            logger.info(f"Arduino boca conectado en {self.jaw_port}")
            return True
        except serial.SerialException as e:
            logger.error(f"No se pudo conectar Arduino boca en {self.jaw_port}: {e}")
            with self._jaw_lock:
                self.jaw_connected = False
            return False

    def disconnect(self):
        """Desconecta de Arduino de forma segura."""
        self._stop_reconnect.set()
        with self._lock:
            if self.connection and self.connection.is_open:
                try:
                    self.connection.write(b"CENTER\n")
                    time.sleep(0.1)
                    self.connection.close()
                except Exception:
                    pass
            self.connected = False
        with self._jaw_lock:
            if self.jaw_connection and self.jaw_connection.is_open:
                try:
                    self.jaw_connection.write(b"0\n")
                    time.sleep(0.1)
                    self.jaw_connection.close()
                except Exception:
                    pass
            self.jaw_connected = False
        logger.info("Arduino desconectado")

    # ----------------------------------------------------------
    # Envío de comandos
    # ----------------------------------------------------------

    def send_command(self, command: str) -> bool:
        """Envía un comando al Arduino. Devuelve True si se envió."""
        with self._lock:
            if not self.connected or self.connection is None:
                return False
            try:
                data = (command.strip() + "\n").encode("utf-8")
                self.connection.write(data)
                return True
            except serial.SerialException as e:
                logger.error(f"Error al escribir en serial: {e}")
                self.connected = False

        # Fuera del lock para evitar deadlock en el hilo de reconexión
        self._start_reconnect()
        return False

    def query_command(self, command: str, timeout: float = 1.0) -> str:
        """Envía un comando y devuelve la respuesta del Arduino, o '' si falla."""
        with self._lock:
            if not self.connected or self.connection is None:
                return ""
            try:
                self.connection.reset_input_buffer()
                self.connection.write((command.strip() + "\n").encode("utf-8"))
                self.connection.timeout = timeout
                response = self.connection.readline().decode("utf-8", errors="ignore").strip()
                self.connection.timeout = self.config.SERIAL_TIMEOUT
                return response
            except serial.SerialException as e:
                logger.error(f"Error en query_command: {e}")
                self.connected = False
        self._start_reconnect()
        return ""

    def send_position(self, angle: int) -> bool:
        """Envía POS,<angle> (servo ojos)."""
        angle = max(self.config.SERVO_MIN_ANGLE,
                    min(self.config.SERVO_MAX_ANGLE, int(angle)))
        return self.send_command(f"POS,{angle}")

    def send_neck_position(self, angle: int) -> bool:
        """Envía NECK,<angle> (servo cuello)."""
        angle = max(getattr(self.config, 'NECK_MIN_ANGLE', 0),
                    min(getattr(self.config, 'NECK_MAX_ANGLE', 180), int(angle)))
        return self.send_command(f"NECK,{angle}")

    def send_jaw(self, angle: int) -> bool:
        """Envía ángulo de mandíbula.
        - Puerto jaw separado (sketch parseInt): envía solo '45\n'
        - Sin puerto jaw / sketch combinado:      envía 'JAW,45\n' al puerto principal."""
        angle = max(0, min(90, int(angle)))
        with self._jaw_lock:
            if self.jaw_connected and self.jaw_connection:
                try:
                    self.jaw_connection.write(f"{angle}\n".encode("utf-8"))
                    return True
                except serial.SerialException as e:
                    logger.error(f"Error en Arduino boca: {e}")
                    self.jaw_connected = False
        # Sin puerto jaw separado → sketch combinado en el puerto principal
        return self.send_command(f"JAW,{angle}")

    def send_neck_center(self) -> bool:
        return self.send_command("NECK_CENTER")

    def send_center(self) -> bool:
        return self.send_command("CENTER")

    def send_stop(self) -> bool:
        return self.send_command("STOP")

    # ----------------------------------------------------------
    # Reconexión automática
    # ----------------------------------------------------------

    def _start_reconnect(self):
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        self._stop_reconnect.clear()
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, daemon=True, name="serial-reconnect"
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self):
        delay = self.config.SERIAL_RECONNECT_DELAY
        while not self._stop_reconnect.is_set() and not self.connected:
            logger.info(f"Intentando reconectar en {delay:.0f}s...")
            self._stop_reconnect.wait(timeout=delay)
            if not self._stop_reconnect.is_set():
                self.connect()

    # ----------------------------------------------------------
    # Utilidades
    # ----------------------------------------------------------

    @staticmethod
    def list_ports() -> list:
        """Devuelve lista de puertos serie disponibles."""
        result = []
        for p in serial.tools.list_ports.comports():
            result.append({
                "port": p.device,
                "description": p.description,
                "hwid": p.hwid
            })
        return result
