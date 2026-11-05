/*
  jaw_fix.ino  —  Arduino boca (COM16) — VERSIÓN CORREGIDA

  CORRECCIÓN CLAVE: Serial.setTimeout(50) en setup()
  Sin esto, Serial.parseInt() bloquea 1000ms por comando,
  lo que hace que la mandíbula apenas se mueva durante la animación.
  Con 50ms, recibe ~20 comandos/segundo y la animación es fluida.

  Pin 9 → Servo mandíbula (SG90)
  Rango: 0 (boca cerrada) → 90 (boca abierta)
  Recibe: "45\n" (número entero, sin prefijo)
*/

#include <Servo.h>

Servo servo1;

void setup() {
  Serial.begin(9600);
  Serial.setTimeout(50);   // ← CORRECCIÓN: 50ms en lugar de 1000ms por defecto
  servo1.attach(9);
  servo1.write(0);
}

void loop() {
  if (Serial.available()) {
    int angulo = Serial.parseInt();
    if (angulo >= 0 && angulo <= 90) {
      servo1.write(angulo);
    }
  }
}
