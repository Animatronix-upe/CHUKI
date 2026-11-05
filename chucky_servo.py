import os, re, unicodedata, time, tempfile, json as json_module
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
import whisper
from groq import Groq

SAMPLE_RATE = 16000
PUERTO_ARDUINO = "COM11"
UMBRAL_VOLUMEN = 0.01
ARCHIVO_MEMORIA = "chucky_memoria.json"
ELEVENLABS_KEY = "TU_API_KEY_ELEVENLABS"
ELEVENLABS_VOICE = "3568tfDGv8UxmLxe0fc1"

print("Cargando Whisper...")
modelo_whisper = whisper.load_model("tiny")
print("Whisper listo.")

cliente = Groq(api_key="TU_API_KEY_GROQ")
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

PROMPT = "Eres Chucky, el muneco diabolico de Child's Play. Recuerdas conversaciones anteriores. Responde en espanol, maximo 2 oraciones cortas y oscuras. Solo responde, sin pensamientos ni explicaciones."

def guardar_memoria(historial):
    try:
        with open(ARCHIVO_MEMORIA, "w", encoding="utf-8") as f:
            json_module.dump(historial, f, ensure_ascii=False, indent=2)
    except:
        pass

def cargar_memoria():
    base = [{"role": "system", "content": PROMPT}]
    try:
        if os.path.exists(ARCHIVO_MEMORIA):
            with open(ARCHIVO_MEMORIA, "r", encoding="utf-8") as f:
                guardado = json_module.load(f)
            mensajes = [m for m in guardado if m["role"] != "system"]
            mensajes = mensajes[-6:]
            print(f"[Memoria cargada: {len(mensajes)} mensajes anteriores]")
            return base + mensajes
    except:
        pass
    return base

def limpiar(texto):
    texto = re.sub(r'<think>.*?</think>', '', texto, flags=re.DOTALL)
    texto = re.sub(r'<[^>]+>.*?</[^>]+>', '', texto, flags=re.DOTALL)
    texto = re.sub(r'<[^>]+>', '', texto)
    texto = texto.strip()
    if not texto:
        texto = "Ja ja ja... no tienes idea de lo que te espera."
    return texto

def texto_a_voz(texto):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE}"
    headers = {"xi-api-key": ELEVENLABS_KEY, "Content-Type": "application/json"}
    cuerpo = {
        "text": texto,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.35, "similarity_boost": 0.85, "style": 0.5}
    }
    r = requests.post(url, json=cuerpo, headers=headers)
    if r.status_code != 200:
        print(f"Error ElevenLabs {r.status_code}, usando voz normal")
        return None
    archivo = tempfile.mktemp(suffix=".mp3")
    with open(archivo, "wb") as f:
        f.write(r.content)
    return archivo

def hablar(texto):
    archivo_mp3 = texto_a_voz(texto)
    if archivo_mp3 is None:
        from gtts import gTTS
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
    audio = sd.rec(int(segundos * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    return audio

def transcribir(audio):
    try:
        energia = np.sqrt(np.mean(audio ** 2))
        if energia < UMBRAL_VOLUMEN:
            return ""
        archivo_wav = tempfile.mktemp(suffix=".wav")
        sf.write(archivo_wav, audio, SAMPLE_RATE)
        resultado = modelo_whisper.transcribe(archivo_wav, language="es")
        os.unlink(archivo_wav)
        return resultado["text"].strip()
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
    guardar_memoria(historial)
    time.sleep(4)
    return texto

print("=" * 40)
print("  CHUCKY CON WHISPER + VOZ REAL")
print("=" * 40)
print("Di 'Andy' para activar a Chucky.")
print("Di 'adios' para terminar.\n")

historial = cargar_memoria()
conversando = False

while True:
    audio = escuchar(4)
    texto = transcribir(audio)
    if not texto:
        continue
    print(f"[Escuche: {texto}]")
    texto_lower = sin_acentos(texto.lower())
    if "adios" in texto_lower:
        if conversando:
            conversando = False
            guardar_memoria(historial)
            hablar("Nos vemos en tus pesadillas")
            print("\nConversacion guardada. Di 'Andy' para volver.\n")
        continue
    if not conversando:
        if "andy" in texto_lower:
            conversando = True
            historial = cargar_memoria()
            hablar("Hola, soy Chucky. Quieres jugar?")
    else:
        respuesta = conversar(texto)
        print(f"Chucky: {respuesta}\n")
        hablar(respuesta)
