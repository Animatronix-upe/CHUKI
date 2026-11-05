/*
 * animatronico.ino - Control de ojos (SG90 pin 9), cuello (SG90 pin 10) y LEDs
 *
 * Hardware:
 *   - Arduino UNO R3
 *   - Servo ojos  SG90: señal → pin 9,  VCC → 5V, GND → GND
 *   - Servo cuello SG90: señal → pin 10, VCC → 5V, GND → GND
 *   - LEDs ojos: ánodo → 220Ω → pin 12, cátodo → GND
 *
 * Protocolo serial (9600 baud, \n terminador):
 *   POS,90        → mover ojos a 90°          → OK,90
 *   NECK,90       → mover cuello a 90°         → OK,NECK,90
 *   CENTER        → centrar ojos Y cuello       → OK,CENTER
 *   NECK_CENTER   → centrar solo cuello         → OK,NECK_CENTER
 *   STOP          → mantener posición actual    → OK,STOP
 *   LED,1 / LED,0 → encender / apagar LEDs      → OK,LED,1
 *   STATUS        → estado actual               → POS,<ojo>,NECK,<cuello>,LED,<0/1>
 *   PING          → verificar conexión          → PONG
 */

#include <Servo.h>

// ================================================================
// PINES
// ================================================================
const int EYE_PIN  = 9;    // Servo ojos   (señal naranja)
const int NECK_PIN = 10;   // Servo cuello (señal naranja)
const int LED_PIN  = 12;   // LEDs de los ojos

// ================================================================
// LÍMITES — ajusta si el mecanismo choca físicamente
// ================================================================
const int SERVO_MIN    = 0;
const int SERVO_MAX    = 180;
const int SERVO_CENTER = 90;

// ================================================================
// VELOCIDAD
// ================================================================
const int          MOVE_SPEED  = 2;    // grados por paso
const unsigned long MOVE_MS    = 15;   // ms entre pasos
const unsigned long TIMEOUT_MS = 8000; // ms sin comando → volver al centro (0 = desactivado)

// ================================================================
// Variables
// ================================================================
Servo servoEye;
Servo servoNeck;

int eyeCurrentPos  = SERVO_CENTER;
int eyeTargetPos   = SERVO_CENTER;
int neckCurrentPos = SERVO_CENTER;
int neckTargetPos  = SERVO_CENTER;
bool ledState      = true;

unsigned long lastMoveTime    = 0;
unsigned long lastCommandTime = 0;
bool timeoutArmed             = false;

String inputBuffer = "";

// ================================================================
// Setup
// ================================================================
void setup() {
  Serial.begin(9600);

  servoEye.attach(EYE_PIN);
  servoEye.write(SERVO_CENTER);

  servoNeck.attach(NECK_PIN);
  servoNeck.write(SERVO_CENTER);

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);

  lastCommandTime = millis();
  Serial.println("READY");
}

// ================================================================
// Loop
// ================================================================
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
    if (angle == 0 && cmd.charAt(4) != '0') { Serial.println("ERR,FORMATO"); return; }
    eyeTargetPos = constrain(angle, SERVO_MIN, SERVO_MAX);
    lastCommandTime = millis();
    timeoutArmed = true;
    Serial.print("OK,"); Serial.println(eyeTargetPos);

  } else if (cmd.startsWith("NECK,")) {
    int angle = cmd.substring(5).toInt();
    if (angle == 0 && cmd.charAt(5) != '0') { Serial.println("ERR,FORMATO"); return; }
    neckTargetPos = constrain(angle, SERVO_MIN, SERVO_MAX);
    lastCommandTime = millis();
    timeoutArmed = true;
    Serial.print("OK,NECK,"); Serial.println(neckTargetPos);

  } else if (cmd == "CENTER") {
    eyeTargetPos  = SERVO_CENTER;
    neckTargetPos = SERVO_CENTER;
    lastCommandTime = millis();
    timeoutArmed = true;
    Serial.println("OK,CENTER");

  } else if (cmd == "NECK_CENTER") {
    neckTargetPos = SERVO_CENTER;
    lastCommandTime = millis();
    timeoutArmed = true;
    Serial.println("OK,NECK_CENTER");

  } else if (cmd == "STOP") {
    eyeTargetPos  = eyeCurrentPos;
    neckTargetPos = neckCurrentPos;
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
    Serial.print("POS,");  Serial.print(eyeCurrentPos);
    Serial.print(",NECK,"); Serial.print(neckCurrentPos);
    Serial.print(",LED,");  Serial.println(ledState ? 1 : 0);

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
      eyeTargetPos  = SERVO_CENTER;
      neckTargetPos = SERVO_CENTER;
      timeoutArmed = false;
    }
  }
}

// ================================================================
// Movimiento suavizado paso a paso (ojos y cuello)
// ================================================================
void moveStep() {
  if (millis() - lastMoveTime < MOVE_MS) return;
  lastMoveTime = millis();

  // Ojos
  if (eyeCurrentPos < eyeTargetPos) {
    eyeCurrentPos = min(eyeCurrentPos + MOVE_SPEED, eyeTargetPos);
    servoEye.write(eyeCurrentPos);
  } else if (eyeCurrentPos > eyeTargetPos) {
    eyeCurrentPos = max(eyeCurrentPos - MOVE_SPEED, eyeTargetPos);
    servoEye.write(eyeCurrentPos);
  }

  // Cuello
  if (neckCurrentPos < neckTargetPos) {
    neckCurrentPos = min(neckCurrentPos + MOVE_SPEED, neckTargetPos);
    servoNeck.write(neckCurrentPos);
  } else if (neckCurrentPos > neckTargetPos) {
    neckCurrentPos = max(neckCurrentPos - MOVE_SPEED, neckTargetPos);
    servoNeck.write(neckCurrentPos);
  }
}
