/*
 * jaw_controller.ino — Control de mandíbula (Chucky)
 *
 * Hardware:
 *   - Arduino UNO/Nano
 *   - Servo mandíbula SG90: señal → pin 9, VCC → 5V, GND → GND
 *
 * Protocolo serial (9600 baud, \n terminador):
 *   JAW,<0-90>  → mover mandíbula (0=cerrada, 90=abierta)  → OK,JAW,<ang>
 *   JAW,0       → cerrar boca                              → OK,JAW,0
 *   PING        → verificar conexión                       → PONG
 *   STATUS      → estado actual                            → JAW,<ang>
 */

#include <Servo.h>

const int JAW_PIN = 9;
const int JAW_MIN = 0;    // Boca cerrada
const int JAW_MAX = 90;   // Boca abierta

Servo servoJaw;
int jawPos = JAW_MIN;
String inputBuffer = "";

void setup() {
  Serial.begin(9600);
  servoJaw.attach(JAW_PIN);
  servoJaw.write(JAW_MIN);
  Serial.println("READY");
}

void loop() {
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

void processCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  if (cmd.startsWith("JAW,")) {
    int angle = cmd.substring(4).toInt();
    jawPos = constrain(angle, JAW_MIN, JAW_MAX);
    servoJaw.write(jawPos);
    Serial.print("OK,JAW,"); Serial.println(jawPos);

  } else if (cmd == "PING") {
    Serial.println("PONG");

  } else if (cmd == "STATUS") {
    Serial.print("JAW,"); Serial.println(jawPos);

  } else {
    Serial.print("ERR,DESCONOCIDO:"); Serial.println(cmd);
  }
}
