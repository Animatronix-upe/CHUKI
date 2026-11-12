#include <Servo.h>

Servo miServo;

// --- VALORES DE TU CONFIGURACIÓN ---
const int ANGULO_IZQ = 0;    // Extremo izquierdo
const int CENTRO     = 55;   // Punto medio (tu centro real)
const int ANGULO_DER = 110;  // Extremo derecho

// Control de velocidad (a mayor número, más lento y suave el movimiento)
const int VELOCIDAD = 15; // Milisegundos entre cada grado

// Variable para rastrear la posición actual del servo
int posicionActual = CENTRO;

// Función para mover el servo de forma suave grado por grado
void moverSuave(int anguloDestino) {
  if (posicionActual < anguloDestino) {
    for (int pos = posicionActual; pos <= anguloDestino; pos++) {
      miServo.write(pos);
      delay(VELOCIDAD);
    }
  } else {
    for (int pos = posicionActual; pos >= anguloDestino; pos--) {
      miServo.write(pos);
      delay(VELOCIDAD);
    }
  }
  posicionActual = anguloDestino;
}

void setup() {
  miServo.attach(10); // Pin 10 = Servo CUELLO

  // Mueve suavemente al centro al encender
  miServo.write(CENTRO);
  posicionActual = CENTRO;
  delay(1000);
}

void loop() {
  // 1. Izquierda (0°)
  moverSuave(ANGULO_IZQ);
  delay(1000);

  // 2. Centro (55°)
  moverSuave(CENTRO);
  delay(800);

  // 3. Derecha (110°)
  moverSuave(ANGULO_DER);
  delay(1000);

  // 4. Centro (55°)
  moverSuave(CENTRO);
  delay(800);
}
