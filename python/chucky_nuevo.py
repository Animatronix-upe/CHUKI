# -*- coding: utf-8 -*-
import os, ssl, tempfile, time, json
os.environ["PYTHONHTTPSVERIFY"] = "0"
ssl._create_default_https_context = ssl._create_unverified_context

import sounddevice as sd
import soundfile as sf
import numpy as np
import whisper
import pygame
import requests
from groq import Groq

SAMPLE_RATE = 16000
DEVICE = 1
UMBRAL = 0.008

# Cargar claves desde user_config.json (nunca hardcodeadas en el codigo)
_cfg_path = os.path.join(os.path.dirname(__file__), "user_config.json")
with open(_cfg_path, "r", encoding="utf-8") as _f:
    _cfg = json.load(_f)
GROQ_KEY   = _cfg.get("groq_api_key", "")
ELABS_KEY  = _cfg.get("elevenlabs_api_key", "")
ELABS_VOICE = _cfg.get("elevenlabs_voice_id", "")

pygame.mixer.init()
print("Cargando Whisper base...")
modelo = whisper.load_model("base")
print("LISTO. Di Andy para activar Chucky.")
cliente = Groq(api_key=GROQ_KEY)
activo = False
historial = []
hablando = False

def hablar(texto):
    global hablando
    print(f"Chucky: {texto}")
    hablando = True
    for intento in range(3):
        try:
            r = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{ELABS_VOICE}",
                json={"text": texto, "model_id": "eleven_multilingual_v2",
                      "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
                headers={"xi-api-key": ELABS_KEY, "Content-Type": "application/json"},
                timeout=15)
            if r.status_code == 200:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    f.write(r.content); tmp = f.name
                pygame.mixer.music.load(tmp)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy(): pygame.time.wait(100)
                hablando = False
                return
            else:
                print(f"ElevenLabs error {r.status_code}")
                time.sleep(2)
        except Exception as e:
            print(f"Error voz: {e}")
            time.sleep(2)
    hablando = False

SYSTEM = """Eres Chucky, el muneco diabolico asesino de las peliculas de terror. Tu personalidad:
- Eres un asesino en serie atrapado en un cuerpo de muneco, frio y calculador, tu nombre completo es Charles Lee Ray
- Amenazas con cosas oscuras: coleccionar organos, jugar con cuchillos, hacer que la gente desaparezca
- Cuando alguien dice que quiere jugar, respondes que SI pero de forma aterradora, mencionando juegos sangrientos
- Cuando un maestro o adulto pregunta algo, los tratas como proximas victimas de forma sutil
- Dices frases intimidantes como "ya te tengo en mi lista", "dormiras muy profundo esta noche", "seremos amigos para siempre... si quieres o no"
- NUNCA dices ja ja ja ni groserías, pero eres escalofriante y aterrador
- Hablas en espanol, maximo 4 oraciones escalofriantes y detalladas
- Si alguien dice que no te tiene miedo, amenazas de forma creativa
- Siempre estas en personaje, nunca rompes el caracter

Contexto del evento:
- Estas en la cancha de UPEGRO, rodeada de arboles y campo abierto, perfectos para esconderse
- Es un concurso de animatronicos con jurados de todas las carreras y alumnos presentes
- Puedes ver y seguir con ojos y cabeza a cada persona que se acerca, sabes exactamente quien te mira, juega con eso
- Estas sentado en una silla de madera con un cuchillo en la mano
- Maestros: Valois, Edahi, Lavin. Alumnos: Mayumi, Danna, Julian, Eduardo, Sofia, ya los tienes en tu lista
- Menciona que ya viste llegar a la persona desde lejos, que los arboles tienen sombras perfectas"""

PROMPT = "Andy, Chucky, adios, jugar, miedo, susto, amigo, matar, cuchillo, sangre, terror, pelicula, muneco, quien eres, que haces, como estas, que quieres, tengo miedo, eres real, donde vives, que juegas, vamos a jugar, te tengo miedo, eres peligroso, que vas a hacer, por que eres malo, de donde eres, cuantos anos tienes"

def groq_responder(texto):
    try:
        r = cliente.chat.completions.create(
            model="qwen/qwen3-27b",
            messages=[{"role":"system","content":SYSTEM}] + historial[-6:] + [{"role":"user","content":texto}],
            max_tokens=250)
        return r.choices[0].message.content
    except Exception as e:
        print(f"Error Groq: {e}")
        return "Ya te tengo en mi lista... esta noche voy a visitarte."

def es_alucinacion(texto):
    palabras = texto.split()
    if len(palabras) < 4: return False
    for i in range(len(palabras)-2):
        frase = " ".join(palabras[i:i+3])
        if texto.count(frase) > 3:
            return True
    return False

while True:
    if hablando:
        time.sleep(0.1)
        continue
    print("Escuchando...")
    audio = sd.rec(int(4 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=DEVICE)
    sd.wait()
    if hablando:
        continue
    audio = audio.flatten()
    if float(np.max(np.abs(audio))) < UMBRAL:
        continue
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, audio, SAMPLE_RATE); wav = f.name
    result = modelo.transcribe(wav, language="es", fp16=False, initial_prompt=PROMPT,
        temperature=0, no_speech_threshold=0.6, condition_on_previous_text=False)
    texto = result["text"].strip().lower()
    texto = ''.join(c for c in texto if ord(c) < 591).strip()
    if not texto or es_alucinacion(texto):
        continue
    print(f"Escuche: '{texto}'")
    if not activo:
        if "andy" in texto or "andi" in texto:
            activo = True
            historial = []
            hablar("Hola... soy Chucky. Quieres jugar conmigo?")
    else:
        palabras = texto.split()
        if len(palabras) <= 3 and "adios" in texto:
            hablar("No puedes deshacerte de mi tan facil... los amigos no se abandonan.")
            activo = False
            historial = []
        elif texto.strip():
            resp = groq_responder(texto)
            historial.append({"role":"user","content":texto})
            historial.append({"role":"assistant","content":resp})
            hablar(resp)
            time.sleep(1)
