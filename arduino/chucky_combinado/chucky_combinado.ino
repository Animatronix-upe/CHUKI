/*
  chucky_combinado.ino
  Un solo Arduino controla: ojos, cuello, mandíbula y LEDs.

  Conexiones:
    Pin 9  → Servo OJOS   (SG90)
    Pin 10 → Servo CUELLO (SG90)
    Pin 11 → Servo MANDÍBULA (SG90)
    Pin 12 → LEDs ojos (con resistencia 220Ω)

  Protocolo serie (9600 baud):
    POS,55      → mover servo ojos a 55°
    NECK,55     → mover servo cuello a 55°
    JAW,45      → mover mandíbula a 45°
    LED,1       → encender LEDs
    LED,0       → apagar LEDs
    CENTER      → centrar ojos y cuello (55°)
    STOP        → detener (mantener posición actual)
    PING        → responde PONG
    STATUS      → responde POS,<angulo_ojos>

  Movimiento: suave y no bloqueante — el loop mueve 1° cada VELOCIDAD ms,
  nunca bloquea el serial.
*/

#include <Servo.h>

Servo servoOjos;
Servo servoCuello;
Servo servoMandibula;

const int PIN_OJOS       = 9;
const int PIN_CUELLO     = 10;
const int PIN_MANDIBULA  = 11;
const int PIN_LED        = 12;

const int CENTRO_OJOS    = 55;
const int CENTRO_CUELLO  = 55;

// Velocidad suave: 1 grado cada N milisegundos
const int VELOCIDAD = 8;

// Posiciones actuales y objetivos
int anguloOjos      = CENTRO_OJOS;
int anguloCuello    = CENTRO_CUELLO;
int anguloMandibula = 0;

int targetOjos      = CENTRO_OJOS;
int targetCuello    = CENTRO_CUELLO;
int targetMandibula = 0;

// Control de tiempo para movimiento suave no bloqueante
unsigned long ultimoMovOjos    = 0;
unsigned long ultimoMovCuello  = 0;
unsigned long ultimoMovMandib  = 0;

String inputBuffer = "";

void setup() {
  Serial.begin(9600);
  Serial.setTimeout(50);

  servoOjos.attach(PIN_OJOS);
  servoCuello.attach(PIN_CUELLO);
  servoMandibula.attach(PIN_MANDIBULA);

  pinMode(PIN_LED, OUTPUT);

  servoOjos.write(CENTRO_OJOS);
  servoCuello.write(CENTRO_CUELLO);
  servoMandibula.write(0);
  digitalWrite(PIN_LED, HIGH);

  delay(500);
  Serial.println("READY");
}

void loop() {
  unsigned long ahora = millis();

  // --- Movimiento suave no bloqueante ---
  if (anguloOjos != targetOjos && ahora - ultimoMovOjos >= VELOCIDAD) {
    anguloOjos += (anguloOjos < targetOjos) ? 1 : -1;
    servoOjos.write(anguloOjos);
    ultimoMovOjos = ahora;
  }

  if (anguloCuello != targetCuello && ahora - ultimoMovCuello >= VELOCIDAD) {
    anguloCuello += (anguloCuello < targetCuello) ? 1 : -1;
    servoCuello.write(anguloCuello);
    ultimoMovCuello = ahora;
  }

  if (anguloMandibula != targetMandibula && ahora - ultimoMovMandib >= (VELOCIDAD / 2)) {
    anguloMandibula += (anguloMandibula < targetMandibula) ? 1 : -1;
    servoMandibula.write(anguloMandibula);
    ultimoMovMandib = ahora;
  }

  // --- Lectura serial ---
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (inputBuffer.length() > 0) {
        procesarComando(inputBuffer);
        inputBuffer = "";
      }
    } else {
      inputBuffer += c;
      if (inputBuffer.length() > 32) inputBuffer = "";
    }
  }
}

void procesarComando(String cmd) {
  cmd.trim();

  if (cmd == "PING") {
    Serial.println("PONG");
    return;
  }

  if (cmd == "CENTER") {
    targetOjos   = CENTRO_OJOS;
    targetCuello = CENTRO_CUELLO;
    Serial.println("OK");
    return;
  }

  if (cmd == "STOP") {
    targetOjos   = anguloOjos;
    targetCuello = anguloCuello;
    Serial.println("OK");
    return;
  }

  if (cmd.startsWith("STATUS")) {
    Serial.print("POS,");
    Serial.println(anguloOjos);
    return;
  }

  if (cmd.startsWith("POS,")) {
    int ang = cmd.substring(4).toInt();
    targetOjos = constrain(ang, 0, 110);
    return;
  }

  if (cmd.startsWith("NECK,")) {
    int ang = cmd.substring(5).toInt();
    targetCuello = constrain(ang, 0, 110);
    return;
  }

  if (cmd.startsWith("JAW,")) {
    int ang = cmd.substring(4).toInt();
    targetMandibula = constrain(ang, 0, 90);
    return;
  }

  if (cmd.startsWith("LED,")) {
    int estado = cmd.substring(4).toInt();
    digitalWrite(PIN_LED, estado ? HIGH : LOW);
    Serial.println("OK");
    return;
  }

  if (cmd == "NECK_CENTER") {
    targetCuello = CENTRO_CUELLO;
    return;
  }
}
