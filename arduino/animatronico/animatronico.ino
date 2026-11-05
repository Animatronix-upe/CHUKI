/*
 * animatronico.ino — Ojos + Cuello (CabezaOficial) + LEDs
 *
 * Hardware:
 *   - Arduino UNO R3
 *   - Servo ojos      SG90: señal → pin 9,  VCC → 5V, GND → GND
 *   - Servo cuello    SG90: señal → pin 10, VCC → 5V, GND → GND
 *   - Servo mandíbula SG90: señal → pin 11, VCC → 5V, GND → GND  (Chucky Parlante)
 *   - LEDs ojos:   ánodo → 220Ω → pin 12, cátodo → GND
 *
 * Cuello: ángulos y velocidad tomados de CabezaOficial.ino
 *   ANGULO_IZQ = 0  |  CENTRO = 55  |  ANGULO_DER = 110
 *   Velocidad: 15 ms por grado (movimiento suave, igual a CabezaOficial)
 *
 * Protocolo serial (9600 baud, \n terminador):
 *   POS,<ang>     → mover ojos               → OK,<ang>
 *   NECK,<ang>    → mover cuello              → OK,NECK,<ang>
 *   CENTER        → centrar ojos Y cuello     → OK,CENTER
 *   NECK_CENTER   → centrar solo cuello       → OK,NECK_CENTER
 *   STOP          → mantener posición         → OK,STOP
 *   LED,1 / LED,0 → encender / apagar LEDs    → OK,LED,1
 *   STATUS        → estado actual             → POS,<ojo>,NECK,<cuello>,LED,<0/1>
 *   PING          → verificar conexión        → PONG
 */

#include <Servo.h>

// ================================================================
// PINES
// ================================================================
const int EYE_PIN  = 9;    // Servo ojos
const int NECK_PIN = 10;   // Servo cuello
const int JAW_PIN  = 11;   // Servo mandíbula (Chucky Parlante)
const int LED_PIN  = 12;   // LEDs ojos

// ================================================================
// LÍMITES MANDÍBULA (Chucky Parlante)
// ================================================================
const int JAW_MIN  = 0;    // Boca cerrada
const int JAW_MAX  = 90;   // Boca abierta (igual que chucky_servo.py)

// ================================================================
// LÍMITES OJOS
// ================================================================
const int EYE_MIN    = 0;
const int EYE_MAX    = 180;
const int EYE_CENTER = 90;

// ================================================================
// LÍMITES CUELLO — de CabezaOficial.ino (no modificar sin prueba física)
// ================================================================
const int NECK_IZQ    = 0;    // Extremo izquierdo
const int NECK_CENTRO = 55;   // Centro exacto
const int NECK_DER    = 110;  // Extremo derecho

// ================================================================
// VELOCIDAD DE MOVIMIENTO
// ================================================================
// Ojos: 2 grados por paso, 15 ms → rápidos y suaves
const int          EYE_STEP_DEG = 2;
const unsigned long EYE_STEP_MS = 15;

// Cuello: 1 grado por paso, 15 ms → igual a CabezaOficial (VELOCIDAD=15)
const int          NECK_STEP_DEG = 1;
const unsigned long NECK_STEP_MS = 15;

// ================================================================
// Timeout: si Python deja de enviar comandos, volver al centro
// ================================================================
const unsigned long TIMEOUT_MS = 8000;  // 0 = desactivado

// ================================================================
// Variables
// ================================================================
Servo servoEye;
Servo servoNeck;
Servo servoJaw;

int eyePos   = EYE_CENTER;
int eyeTarget = EYE_CENTER;

int neckPos   = NECK_CENTRO;
int neckTarget = NECK_CENTRO;

int jawPos = JAW_MIN;

bool ledState = true;

unsigned long lastEyeStep  = 0;
unsigned long lastNeckStep = 0;
unsigned long lastCmdTime  = 0;
bool timeoutArmed          = false;

String inputBuffer = "";

// ================================================================
// Setup
// ================================================================
void setup() {
  Serial.begin(9600);

  servoEye.attach(EYE_PIN);
  servoEye.write(EYE_CENTER);

  servoNeck.attach(NECK_PIN);
  servoNeck.write(NECK_CENTRO);

  servoJaw.attach(JAW_PIN);
  servoJaw.write(JAW_MIN);

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);

  lastCmdTime = millis();
  Serial.println("READY");
}

// ================================================================
// Loop — no bloqueante
// ================================================================
void loop() {
  readSerial();
  checkTimeout();
  stepEye();
  stepNeck();
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
    eyeTarget = constrain(angle, EYE_MIN, EYE_MAX);
    lastCmdTime = millis(); timeoutArmed = true;
    Serial.print("OK,"); Serial.println(eyeTarget);

  } else if (cmd.startsWith("NECK,")) {
    int angle = cmd.substring(5).toInt();
    if (angle == 0 && cmd.charAt(5) != '0') { Serial.println("ERR,FORMATO"); return; }
    neckTarget = constrain(angle, NECK_IZQ, NECK_DER);
    lastCmdTime = millis(); timeoutArmed = true;
    Serial.print("OK,NECK,"); Serial.println(neckTarget);

  } else if (cmd == "CENTER") {
    eyeTarget  = EYE_CENTER;
    neckTarget = NECK_CENTRO;
    lastCmdTime = millis(); timeoutArmed = true;
    Serial.println("OK,CENTER");

  } else if (cmd == "NECK_CENTER") {
    neckTarget = NECK_CENTRO;
    lastCmdTime = millis(); timeoutArmed = true;
    Serial.println("OK,NECK_CENTER");

  } else if (cmd == "STOP") {
    eyeTarget  = eyePos;
    neckTarget = neckPos;
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
    Serial.print("POS,");   Serial.print(eyePos);
    Serial.print(",NECK,"); Serial.print(neckPos);
    Serial.print(",LED,");  Serial.println(ledState ? 1 : 0);

  } else if (cmd.startsWith("JAW,")) {
    int angle = cmd.substring(4).toInt();
    jawPos = constrain(angle, JAW_MIN, JAW_MAX);
    servoJaw.write(jawPos);
    Serial.print("OK,JAW,"); Serial.println(jawPos);

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
    if (millis() - lastCmdTime > TIMEOUT_MS) {
      eyeTarget  = EYE_CENTER;
      neckTarget = NECK_CENTRO;
      timeoutArmed = false;
    }
  }
}

// ================================================================
// Movimiento ojos — no bloqueante, 2°/15ms
// ================================================================
void stepEye() {
  if (millis() - lastEyeStep < EYE_STEP_MS) return;
  lastEyeStep = millis();
  if (eyePos < eyeTarget) {
    eyePos = min(eyePos + EYE_STEP_DEG, eyeTarget);
    servoEye.write(eyePos);
  } else if (eyePos > eyeTarget) {
    eyePos = max(eyePos - EYE_STEP_DEG, eyeTarget);
    servoEye.write(eyePos);
  }
}

// ================================================================
// Movimiento cuello — no bloqueante, 1°/15ms (= CabezaOficial)
// ================================================================
void stepNeck() {
  if (millis() - lastNeckStep < NECK_STEP_MS) return;
  lastNeckStep = millis();
  if (neckPos < neckTarget) {
    neckPos = min(neckPos + NECK_STEP_DEG, neckTarget);
    servoNeck.write(neckPos);
  } else if (neckPos > neckTarget) {
    neckPos = max(neckPos - NECK_STEP_DEG, neckTarget);
    servoNeck.write(neckPos);
  }
}
