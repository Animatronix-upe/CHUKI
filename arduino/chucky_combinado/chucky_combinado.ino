/*
  chucky_combinado.ino
  Un solo Arduino controla: ojos, cuello, mandíbula y LEDs.
  Elimina el problema de interferencia DTR entre dos Arduinos.

  Conexiones:
    Pin 9  → Servo OJOS   (SG90)
    Pin 10 → Servo CUELLO (SG90)
    Pin 11 → Servo MANDÍBULA (SG90)
    Pin 12 → LEDs ojos (con resistencia 220Ω)

  Protocolo serie (9600 baud):
    POS,90      → mover servo ojos a 90°
    NECK,55     → mover servo cuello a 55°
    JAW,45      → mover mandíbula a 45°
    LED,1       → encender LEDs
    LED,0       → apagar LEDs
    CENTER      → centrar ojos y cuello
    STOP        → detener (mantener posición actual)
    PING        → responde PONG
    STATUS      → responde POS,<angulo_ojos>
*/

#include <Servo.h>

Servo servoOjos;
Servo servoCuello;
Servo servoMandibula;

const int PIN_OJOS       = 9;
const int PIN_CUELLO     = 10;
const int PIN_MANDIBULA  = 11;
const int PIN_LED        = 12;

const int CENTRO_OJOS    = 90;
const int CENTRO_CUELLO  = 55;

int anguloOjos      = CENTRO_OJOS;
int anguloCuello    = CENTRO_CUELLO;
int anguloMandibula = 0;

String inputBuffer = "";

void setup() {
  Serial.begin(9600);
  Serial.setTimeout(50);   // ← clave: parseInt() espera solo 50ms (no 1000ms)

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
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (inputBuffer.length() > 0) {
        procesarComando(inputBuffer);
        inputBuffer = "";
      }
    } else {
      inputBuffer += c;
      if (inputBuffer.length() > 32) inputBuffer = "";  // protección overflow
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
    anguloOjos   = CENTRO_OJOS;
    anguloCuello = CENTRO_CUELLO;
    servoOjos.write(anguloOjos);
    servoCuello.write(anguloCuello);
    Serial.println("OK");
    return;
  }

  if (cmd == "STOP") {
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
    ang = constrain(ang, 0, 180);
    anguloOjos = ang;
    servoOjos.write(anguloOjos);
    return;
  }

  if (cmd.startsWith("NECK,")) {
    int ang = cmd.substring(5).toInt();
    ang = constrain(ang, 0, 110);
    anguloCuello = ang;
    servoCuello.write(anguloCuello);
    return;
  }

  if (cmd.startsWith("JAW,")) {
    int ang = cmd.substring(4).toInt();
    ang = constrain(ang, 0, 90);
    anguloMandibula = ang;
    servoMandibula.write(anguloMandibula);
    return;
  }

  if (cmd.startsWith("LED,")) {
    int estado = cmd.substring(4).toInt();
    digitalWrite(PIN_LED, estado ? HIGH : LOW);
    Serial.println("OK");
    return;
  }

  if (cmd == "NECK_CENTER") {
    anguloCuello = CENTRO_CUELLO;
    servoCuello.write(anguloCuello);
    return;
  }
}
