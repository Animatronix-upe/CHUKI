/*
 * animatronico.ino - Control del servomotor SG90 y LEDs
 *
 * Hardware confirmado:
 *   - Arduino UNO R3 (clon)
 *   - Servo SG90: naranja=señal (pin 9), rojo=5V Arduino, marrón=GND
 *   - LEDs en ojos: ánodo → 220Ω → pin 12, cátodo → GND
 *
 * Protocolo serial (9600 baud, \n como terminador):
 *   POS,90    → mover servo a 90 grados  →  OK,90
 *   CENTER    → mover al centro (90°)    →  OK,CENTER
 *   STOP      → mantener posición actual →  OK,STOP
 *   LED,1     → encender LEDs            →  OK,LED,1
 *   LED,0     → apagar LEDs              →  OK,LED,0
 *   STATUS    → estado actual            →  POS,<ángulo>,LED,<0/1>
 *   PING      → verificar conexión       →  PONG
 *
 * Librería requerida: Servo (incluida en Arduino IDE, sin instalar nada)
 */

#include <Servo.h>

// ================================================================
// PINES — Arduino UNO R3 confirmado
// ================================================================
const int SERVO_PIN = 9;    // Pin PWM (~) — señal al SG90 naranja
const int LED_PIN   = 12;   // LEDs de los ojos (con resistencia 220Ω por LED)

// ================================================================
// LÍMITES DEL SG90 — rango físico completo
// Si el mecanismo choca, reduce estos valores desde Arduino IDE
// ================================================================
const int SERVO_MIN    = 0;    // Tope físico izquierdo SG90
const int SERVO_MAX    = 180;  // Tope físico derecho  SG90
const int SERVO_CENTER = 90;   // Ojos al frente

// ================================================================
// VELOCIDAD DE MOVIMIENTO
// ================================================================
const int MOVE_SPEED         = 2;   // grados por paso
const unsigned long MOVE_MS  = 15;  // ms entre pasos (~67 pasos/seg)

// ================================================================
// TIMEOUT: si no llegan comandos en N ms → volver al centro
// Cambia a 0 para desactivar
// ================================================================
const unsigned long TIMEOUT_MS = 8000;

// ================================================================
// Variables internas
// ================================================================
Servo servo;

int currentPos  = SERVO_CENTER;
int targetPos   = SERVO_CENTER;
bool ledState   = true;  // Los LEDs arrancan encendidos

unsigned long lastMoveTime    = 0;
unsigned long lastCommandTime = 0;
bool timeoutArmed             = false;

String inputBuffer = "";

void setup() {
  Serial.begin(9600);

  // Servo
  servo.attach(SERVO_PIN);
  servo.write(SERVO_CENTER);
  currentPos = SERVO_CENTER;
  targetPos  = SERVO_CENTER;

  // LEDs
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);  // encendidos al iniciar

  lastCommandTime = millis();
  Serial.println("READY");
}

void loop() {
  readSerial();
  checkTimeout();
  moveStep();
}

// ================================================================
// Lectura serial sin bloqueo
// ================================================================
void readSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n') {
      processCommand(inputBuffer);
      inputBuffer = "";
    } else if (c != '\r') {
      if (inputBuffer.length() < 32) inputBuffer += c;
    }
  }
}

// ================================================================
// Procesamiento de comandos
// ================================================================
void processCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  if (cmd.startsWith("POS,")) {
    int angle = cmd.substring(4).toInt();
    // Rechaza si el resultado del toInt() es 0 y el carácter no es '0'
    if (angle == 0 && cmd.charAt(4) != '0') {
      Serial.println("ERR,FORMATO");
      return;
    }
    angle = constrain(angle, SERVO_MIN, SERVO_MAX);
    targetPos = angle;
    lastCommandTime = millis();
    timeoutArmed = true;
    Serial.print("OK,"); Serial.println(angle);

  } else if (cmd == "CENTER") {
    targetPos = SERVO_CENTER;
    lastCommandTime = millis();
    timeoutArmed = true;
    Serial.println("OK,CENTER");

  } else if (cmd == "STOP") {
    targetPos = currentPos;
    timeoutArmed = false;
    Serial.println("OK,STOP");

  } else if (cmd == "LED,1") {
    ledState = true;
    digitalWrite(LED_PIN, HIGH);
    Serial.println("OK,LED,1");

  } else if (cmd == "LED,0") {
    ledState = false;
    digitalWrite(LED_PIN, LOW);
    Serial.println("OK,LED,0");

  } else if (cmd == "STATUS") {
    Serial.print("POS,");
    Serial.print(currentPos);
    Serial.print(",LED,");
    Serial.println(ledState ? 1 : 0);

  } else if (cmd == "PING") {
    Serial.println("PONG");

  } else {
    Serial.print("ERR,DESCONOCIDO:"); Serial.println(cmd);
  }
}

// ================================================================
// Timeout: volver al centro si Python deja de enviar
// ================================================================
void checkTimeout() {
  if (TIMEOUT_MS > 0 && timeoutArmed) {
    if (millis() - lastCommandTime > TIMEOUT_MS) {
      targetPos = SERVO_CENTER;
      timeoutArmed = false;
    }
  }
}

// ================================================================
// Movimiento suavizado paso a paso
// ================================================================
void moveStep() {
  if (millis() - lastMoveTime < MOVE_MS) return;
  lastMoveTime = millis();

  if (currentPos < targetPos) {
    currentPos = min(currentPos + MOVE_SPEED, targetPos);
    servo.write(currentPos);
  } else if (currentPos > targetPos) {
    currentPos = max(currentPos - MOVE_SPEED, targetPos);
    servo.write(currentPos);
  }
}
