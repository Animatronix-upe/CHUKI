import os, re, unicodedata, time, tempfile
os.environ["PYTHONHTTPSVERIFY"] = "0"
import requests
from urllib3.exceptions import InsecureRequestWarning
import urllib3
urllib3.disable_warnings(InsecureRequestWarning)
original_request = requests.Session.request
def patched_request(self, method, url, **kwargs):
    kwargs["verify"] = False
    return original_request(self, method, url, **kwargs)
requests.Session.request = patched_request

import sounddevice as sd
import soundfile as sf
import numpy as np
import json
import pygame
import serial
from gtts import gTTS
from vosk import Model, KaldiRecognizer
from groq import Groq

SAMPLE_RATE = 16000
PUERTO_ARDUINO = "COM11"
UMBRAL_VOLUMEN = 0.02
modelo_vosk = Model("vosk-model-small-es-0.42")
cliente = Groq(api_key="TU_API_KEY_AQUI")
pygame.mixer.init()

try:
    arduino = serial.Serial(PUERTO_ARDUINO, 9600, timeout=1)
    time.sleep(2)
    print(f"Arduino conectado en {PUERTO_ARDUINO}")
except:
    arduino = None
    print("Arduino no encontrado, continuando sin servo")

def sin_acentos(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')

def nuevo_historial():
    return [{"role": "system", "content": "Eres Chucky, el muneco diabolico de Child's Play. Responde en espanol, maximo 2 oraciones cortas y oscuras. Solo responde, sin pensamientos ni explicaciones."}]

historial = nuevo_historial()

def limpiar(texto):
    texto = re.sub(r'<think>.*?</think>', '', texto, flags=re.DOTALL)
    texto = re.sub(r'<[^>]+>.*?</[^>]+>', '', texto, flags=re.DOTALL)
    texto = re.sub(r'<[^>]+>', '', texto)
    return texto.strip()

def hablar(texto):
    archivo_mp3 = tempfile.mktemp(suffix=".mp3")
    tts = gTTS(text=texto, lang='es', slow=False)
    tts.save(archivo_mp3)

    audio_data, sr = sf.read(archivo_mp3)
    if len(audio_data.shape) > 1:
        audio_data = audio_data.mean(axis=1)

    pygame.mixer.music.load(archivo_mp3)
    pygame.mixer.music.play()

    intervalo = 0.05
    muestras = int(sr * intervalo)
    for i in range(0, len(audio_data), muestras):
        fragmento = audio_data[i:i + muestras]
        if len(fragmento) == 0:
            break
        volumen = np.sqrt(np.mean(fragmento ** 2))
        angulo = int(min(90, volumen * 1200))
        if arduino:
            arduino.write(f"{angulo}\n".encode())
        time.sleep(intervalo)

    if arduino:
        arduino.write(b"0\n")
    while pygame.mixer.music.get_busy():
        time.sleep(0.05)
    try:
        os.unlink(archivo_mp3)
    except:
        pass

def escuchar(segundos):
    audio = sd.rec(int(segundos * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16")
    sd.wait()
    return audio

def transcribir(audio):
    try:
        audio_float = audio.astype(np.float32) / 32768.0
        energia = np.sqrt(np.mean(audio_float ** 2))
        if energia < UMBRAL_VOLUMEN:
            return ""
        rec = KaldiRecognizer(modelo_vosk, SAMPLE_RATE)
        rec.AcceptWaveform(audio.tobytes())
        return json.loads(rec.FinalResult()).get("text", "")
    except:
        return ""

def conversar(pregunta):
    historial.append({"role": "user", "content": pregunta})
    respuesta = cliente.chat.completions.create(
        model="qwen/qwen3.8-27b",
        max_tokens=200,
        messages=historial
    )
    texto = limpiar(respuesta.choices[0].message.content)
    historial.append({"role": "assistant", "content": texto})
    return texto

print("=" * 40)
print("  CHUCKY PARLANTE - UN SERVO")
print("=" * 40)
print("Di 'Andy' para activar a Chucky.")
print("Di 'adios' para terminar.\n")

conversando = False

while True:
    audio = escuchar(3)
    texto = transcribir(audio)
    if not texto:
        continue
    print(f"[Escuche: {texto}]")
    texto_lower = sin_acentos(texto.lower())
    if "adios" in texto_lower:
        if conversando:
            conversando = False
            historial = nuevo_historial()
            hablar("Nos vemos en tus pesadillas")
            print("\nConversacion terminada. Di 'Andy' para volver.\n")
        continue
    if not conversando:
        if "andy" in texto_lower:
            conversando = True
            hablar("Hola, soy Chucky. Quieres jugar?")
    else:
        respuesta = conversar(texto)
        print(f"Chucky: {respuesta}\n")
        hablar(respuesta)
