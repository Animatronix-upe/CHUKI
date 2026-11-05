"""
chucky_voice.py  —  Chucky Parlante
Reconocimiento de voz (Vosk) + IA (Groq) + TTS (gTTS) + servo mandíbula.
Adaptado de chucky_servo.py (rama main).
"""

import os
import re
import random
import unicodedata
import time
import tempfile
import threading
import logging
import json
import numpy as np

logger = logging.getLogger(__name__)

# Deshabilita verificación SSL (igual que el original)
os.environ.setdefault("PYTHONHTTPSVERIFY", "0")
try:
    import urllib3
    urllib3.disable_warnings()
except Exception:
    pass

SAMPLE_RATE = 16000

# Frases aterradoras predefinidas (modo automático)
_FRASES_CHUCKY = [
    "Ya vengo por ti...",
    "Seré tu mejor amigo... hasta el final.",
    "¿Crees que puedes escapar de Chucky?",
    "Los niños buenos no duermen esta noche.",
    "No puedes matarme, porque yo nunca muero.",
    "Voy a encontrarte. No importa a dónde vayas.",
    "Esta noche... seremos amigos para siempre.",
    "Alguien va a tener pesadillas.",
    "Ya sé dónde estás.",
    "Mi cuchillo tiene mucha hambre esta noche.",
    "Nunca deberías haber venido aquí.",
    "Qué oscuro está... perfecto para mí.",
    "No te muevas. No hagas ruido. Chucky está cerca.",
    "¿Escuchas eso? Soy yo... y ya llegué.",
    "Juega conmigo... o lo lamentarás.",
]

_SYSTEM_PROMPT = (
    "Eres Chucky, el muñeco diabólico de Child's Play. "
    "Responde en español, máximo 2 oraciones cortas y oscuras. "
    "Solo responde, sin pensamientos ni explicaciones."
)


def _sin_acentos(texto):
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def _limpiar(texto):
    texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL)
    texto = re.sub(r"<[^>]+>.*?</[^>]+>", "", texto, flags=re.DOTALL)
    texto = re.sub(r"<[^>]+>", "", texto)
    return texto.strip()


class ChuckyVoice:
    MODEL_NAME = "vosk-model-small-es-0.42"

    def __init__(self, serial_controller, config):
        self._serial = serial_controller
        self._config = config

        # Estado público
        self.active        = False
        self.status        = "inactivo"
        self.last_heard    = ""
        self.last_response = ""
        self.jaw_angle     = 0
        self.model_ready   = False
        self.downloading   = False
        self.download_pct  = 0
        self.error_msg     = ""

        # Config
        self.groq_api_key        = getattr(config, "GROQ_API_KEY", "")
        self.wake_word           = getattr(config, "CHUCKY_WAKE_WORD", "andy")
        self.end_word            = getattr(config, "CHUCKY_END_WORD", "adios")
        self.model_path          = getattr(config, "VOSK_MODEL_PATH", self.MODEL_NAME)
        self.elevenlabs_api_key  = getattr(config, "ELEVENLABS_API_KEY", "")
        self.elevenlabs_voice_id = getattr(config, "ELEVENLABS_VOICE_ID", "")
        self.elevenlabs_model    = getattr(config, "ELEVENLABS_MODEL", "eleven_multilingual_v2")
        self.umbral_volumen      = 0.05

        # Voz aterradora
        self.scary_voice = True   # pitch shift grave + eco

        # Modo frases automáticas
        self.auto_active      = False
        self.auto_interval    = 30       # segundos entre frases
        self.auto_phrases     = list(_FRASES_CHUCKY)
        self._auto_stop_evt   = threading.Event()
        self._auto_thread     = None

        # Si ElevenLabs devuelve 401, deshabilitar para no desperdiciar tiempo
        self._elevenlabs_disabled = False

        self._stop_evt   = threading.Event()
        self._speak_lock = threading.Lock()
        self._thread     = None
        self._conversando = False
        self._historial  = self._nuevo_historial()

        self._vosk_model = None
        self._groq_client = None
        self._pygame_ready = False

        self._init_pygame()
        self._load_model()

    # ----------------------------------------------------------------
    # Init helpers
    # ----------------------------------------------------------------

    def _init_pygame(self):
        try:
            import pygame
            pygame.mixer.init()
            self._pygame_ready = True
        except Exception as e:
            logger.warning("pygame no disponible: %s", e)

    def _load_model(self):
        model_dir = self.model_path
        if not os.path.isdir(model_dir):
            # Try relative to this file's directory
            base = os.path.dirname(os.path.abspath(__file__))
            candidate = os.path.join(base, model_dir)
            if os.path.isdir(candidate):
                model_dir = candidate
            else:
                logger.warning("Modelo Vosk no encontrado en: %s", model_dir)
                self.error_msg = f"Modelo Vosk no encontrado: {self.model_path}"
                return
        try:
            from vosk import Model
            self._vosk_model = Model(model_dir)
            self.model_ready = True
            self.error_msg   = ""
            logger.info("Modelo Vosk cargado: %s", model_dir)
        except Exception as e:
            self.error_msg = f"Error cargando modelo Vosk: {e}"
            logger.error(self.error_msg)

    def _ensure_groq(self):
        if self._groq_client and getattr(self._groq_client, "_api_key_set", False):
            return self._groq_client
        key = self.groq_api_key
        if not key:
            return None
        try:
            from groq import Groq
            self._groq_client = Groq(api_key=key)
            self._groq_client._api_key_set = True
            return self._groq_client
        except Exception as e:
            logger.error("Groq init: %s", e)
            return None

    # ----------------------------------------------------------------
    # Control público
    # ----------------------------------------------------------------

    def start_auto_phrases(self) -> bool:
        """Inicia el modo de frases automáticas."""
        if self.auto_active:
            return False
        self._auto_stop_evt.clear()
        self._auto_thread = threading.Thread(
            target=self._auto_phrases_loop, daemon=True, name="chucky-auto"
        )
        self._auto_thread.start()
        self.auto_active = True
        logger.info("Chucky auto-frases iniciado (cada %ds)", self.auto_interval)
        return True

    def stop_auto_phrases(self):
        """Detiene el modo de frases automáticas."""
        self.auto_active = False
        self._auto_stop_evt.set()

    def set_auto_config(self, interval=None, phrases=None, scary=None):
        if interval is not None:
            self.auto_interval = max(5, int(interval))
        if phrases is not None and isinstance(phrases, list) and len(phrases) > 0:
            self.auto_phrases = [str(p).strip() for p in phrases if str(p).strip()]
        if scary is not None:
            self.scary_voice = bool(scary)

    def set_config(self, groq_key=None, wake_word=None, end_word=None,
                   elevenlabs_key=None, elevenlabs_voice_id=None, elevenlabs_model=None):
        if groq_key is not None:
            self.groq_api_key = groq_key
            self._groq_client = None   # fuerza reinit
        if wake_word is not None:
            self.wake_word = wake_word.lower()
        if end_word is not None:
            self.end_word = end_word.lower()
        if elevenlabs_key is not None:
            self.elevenlabs_api_key = elevenlabs_key
        if elevenlabs_voice_id is not None:
            self.elevenlabs_voice_id = elevenlabs_voice_id
        if elevenlabs_model is not None:
            self.elevenlabs_model = elevenlabs_model

    def start(self):
        if self.active:
            return False
        if not self.model_ready:
            self.error_msg = "Modelo Vosk no listo. Descárgalo primero."
            return False
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="chucky-voice")
        self._thread.start()
        return True

    def stop(self):
        self._stop_evt.set()
        self.active  = False
        self.status  = "inactivo"
        self._conversando = False
        self._historial   = self._nuevo_historial()

    def speak_text(self, texto: str):
        """Hace que Chucky diga el texto indicado (sin IA)."""
        threading.Thread(target=self._hablar, args=(texto,), daemon=True).start()

    def download_model(self):
        """Descarga vosk-model-small-es-0.42 en background."""
        if self.downloading or self.model_ready:
            return
        threading.Thread(target=self._do_download, daemon=True, name="vosk-download").start()

    def get_status(self) -> dict:
        return {
            "active":               self.active,
            "status":               self.status,
            "last_heard":           self.last_heard,
            "last_response":        self.last_response,
            "jaw_angle":            self.jaw_angle,
            "model_ready":          self.model_ready,
            "downloading":          self.downloading,
            "download_pct":         self.download_pct,
            "api_key_set":          bool(self.groq_api_key),
            "wake_word":            self.wake_word,
            "end_word":             self.end_word,
            "error":                self.error_msg,
            "elevenlabs_key_set":   bool(self.elevenlabs_api_key),
            "elevenlabs_voice_set": bool(self.elevenlabs_voice_id),
            "tts_engine":           "elevenlabs" if self.elevenlabs_api_key else "gtts",
            "auto_active":          self.auto_active,
            "auto_interval":        self.auto_interval,
            "scary_voice":          self.scary_voice,
            "auto_phrases":         self.auto_phrases,
        }

    # ----------------------------------------------------------------
    # Loop principal de escucha
    # ----------------------------------------------------------------

    def _loop(self):
        self.active = True
        self.status = "escuchando"
        logger.info("Chucky Parlante: loop iniciado")
        try:
            while not self._stop_evt.is_set():
                audio = self._escuchar(3)
                if audio is None:
                    time.sleep(0.2)
                    continue
                texto = self._transcribir(audio)
                if not texto:
                    continue
                self.last_heard = texto
                logger.info("Escuché: %s", texto)
                texto_lower = _sin_acentos(texto.lower())

                if _sin_acentos(self.end_word) in texto_lower:
                    if self._conversando:
                        self._conversando = False
                        self._historial   = self._nuevo_historial()
                        self._hablar("Nos vemos en tus pesadillas")
                        self.status = "escuchando"
                    continue

                if not self._conversando:
                    if _sin_acentos(self.wake_word) in texto_lower:
                        self._conversando = True
                        self._hablar("Hola, soy Chucky. ¿Quieres jugar?")
                else:
                    self.status = "procesando"
                    respuesta = self._conversar(texto)
                    if respuesta:
                        self.last_response = respuesta
                        logger.info("Chucky: %s", respuesta)
                        self._hablar(respuesta)
                    else:
                        self.status = "escuchando"
        except Exception as e:
            logger.error("Error en loop Chucky: %s", e)
            self.error_msg = str(e)
        finally:
            self.active = False
            self.status = "inactivo"
            logger.info("Chucky Parlante: loop terminado")

    # ----------------------------------------------------------------
    # Audio helpers
    # ----------------------------------------------------------------

    def _escuchar(self, segundos):
        try:
            import sounddevice as sd
            audio = sd.rec(
                int(segundos * SAMPLE_RATE),
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16"
            )
            sd.wait()
            return audio
        except Exception as e:
            logger.warning("Error escuchando: %s", e)
            return None

    def _transcribir(self, audio):
        try:
            from vosk import KaldiRecognizer
            audio_float = audio.astype(np.float32) / 32768.0
            energia = float(np.sqrt(np.mean(audio_float ** 2)))
            if energia < self.umbral_volumen:
                return ""
            rec = KaldiRecognizer(self._vosk_model, SAMPLE_RATE)
            rec.AcceptWaveform(audio.tobytes())
            return json.loads(rec.FinalResult()).get("text", "")
        except Exception as e:
            logger.warning("Error transcribiendo: %s", e)
            return ""

    def _hablar(self, texto):
        with self._speak_lock:
            self.status    = "hablando"
            self.jaw_angle = 0
            archivo_mp3  = None
            archivo_play = None
            try:
                archivo_mp3 = tempfile.mktemp(suffix=".mp3")

                if self.elevenlabs_api_key:
                    self._tts_elevenlabs(texto, archivo_mp3)
                else:
                    self._tts_gtts(texto, archivo_mp3)

                # Aplicar efecto de voz aterradora (pitch grave + eco)
                archivo_play = self._apply_scary_effect(archivo_mp3)

                self._reproducir_con_mandibula(archivo_play)

            except Exception as e:
                logger.error("Error en TTS: %s", e)
            finally:
                self.status    = "escuchando" if self.active else "inactivo"
                self.jaw_angle = 0
                self._send_jaw(0)
                for f in {archivo_mp3, archivo_play} - {None}:
                    try:
                        os.unlink(f)
                    except Exception:
                        pass

    def _apply_scary_effect(self, archivo_in: str) -> str:
        """Voz más grave y aterradora: baja ~4 semitones + eco sutil.
        Usa pydub si está disponible. Si falla, devuelve el archivo original."""
        if not self.scary_voice:
            return archivo_in
        try:
            from pydub import AudioSegment
            sound = AudioSegment.from_file(archivo_in)

            # Bajar tono ~4 semitones (y ligeramente más lento — más amenazante)
            octaves   = -0.35                               # ≈ -4.2 semitones
            new_rate  = int(sound.frame_rate * (2.0 ** octaves))
            lowered   = sound._spawn(sound.raw_data, overrides={"frame_rate": new_rate})

            # Eco tenue (delay 180ms, -11 dB)
            echo       = lowered - 11
            with_echo  = lowered.overlay(echo, position=180)

            out = archivo_in.replace(".mp3", "_fx.mp3")
            with_echo.export(out, format="mp3", bitrate="128k")
            return out
        except Exception as e:
            logger.debug("Efecto voz no disponible: %s", e)
            return archivo_in

    def _tts_gtts(self, texto: str, destino: str):
        from gtts import gTTS
        tts = gTTS(text=texto, lang="es", slow=False)
        tts.save(destino)
        logger.info("gTTS OK: %d bytes → '%s'", os.path.getsize(destino), texto[:40])

    def _tts_elevenlabs(self, texto: str, destino: str):
        """Genera audio con ElevenLabs y lo guarda en destino."""
        if self._elevenlabs_disabled:
            self._tts_gtts(texto, destino)
            return
        try:
            from elevenlabs.client import ElevenLabs
            from elevenlabs import VoiceSettings

            client = ElevenLabs(api_key=self.elevenlabs_api_key)
            voice_id = self.elevenlabs_voice_id or "pNInz6obpgDQGcFmaJgB"  # Adam por defecto

            audio_stream = client.text_to_speech.convert(
                voice_id=voice_id,
                text=texto,
                model_id=self.elevenlabs_model,
                voice_settings=VoiceSettings(
                    stability=0.35,
                    similarity_boost=0.85,
                    style=0.45,
                    use_speaker_boost=True,
                ),
            )
            with open(destino, "wb") as f:
                for chunk in audio_stream:
                    if chunk:
                        f.write(chunk)
            if os.path.getsize(destino) == 0:
                raise RuntimeError("ElevenLabs devolvió respuesta vacía (posible 401)")
            logger.info("ElevenLabs TTS OK — voz: %s", voice_id)
        except ImportError:
            logger.warning("elevenlabs no instalado, usando gTTS como fallback")
            self._tts_gtts(texto, destino)
        except Exception as e:
            err_str = str(e)
            if "401" in err_str or "unauthorized" in err_str.lower() or "invalid_api_key" in err_str.lower():
                self._elevenlabs_disabled = True
                logger.warning("ElevenLabs: API key inválida — deshabilitando ElevenLabs, usando gTTS de ahora en adelante")
            else:
                logger.error("ElevenLabs error: %s — usando gTTS como fallback", e)
            self._tts_gtts(texto, destino)

    def _decode_audio(self, archivo: str):
        """Decodifica audio para análisis de volumen. Retorna (samples_float32, sr) o (None, None)."""
        # soundfile: bueno para WAV/FLAC; puede fallar con MP3 según versión de libsndfile
        try:
            import soundfile as sf
            data, sr = sf.read(archivo)
            if len(data.shape) > 1:
                data = data.mean(axis=1)
            return data.astype(np.float32), sr
        except Exception:
            pass

        # pydub: maneja MP3 vía ffmpeg si está disponible
        try:
            from pydub import AudioSegment
            seg = AudioSegment.from_file(archivo)
            samples = np.array(seg.get_array_of_samples(), dtype=np.float32)
            if seg.channels == 2:
                samples = samples.reshape((-1, 2)).mean(axis=1)
            samples /= float(2 ** (seg.sample_width * 8 - 1))
            return samples, seg.frame_rate
        except Exception:
            pass

        return None, None

    def _reproducir_con_mandibula(self, archivo_mp3: str):
        """Reproduce el MP3 y anima la mandíbula SOLO mientras hay audio activo."""
        import math

        if not self._pygame_ready:
            logger.error("pygame mixer no inicializado — no hay audio")
            return

        import pygame

        file_size = os.path.getsize(archivo_mp3) if os.path.exists(archivo_mp3) else 0
        logger.info("pygame: cargando audio (%d bytes)", file_size)
        if file_size == 0:
            logger.error("pygame: archivo MP3 vacío, cancelando reproducción")
            return

        # 1. Decodificar ANTES de reproducir para que la animación no se atrase
        audio_data, sr = self._decode_audio(archivo_mp3)

        # Cerrar boca antes de empezar
        self._send_jaw(0)

        pygame.mixer.music.set_volume(1.0)
        try:
            pygame.mixer.music.load(archivo_mp3)
        except Exception as e:
            logger.error("pygame no pudo cargar el audio: %s", e)
            return

        pygame.mixer.music.play()
        logger.info("pygame: reproduciendo (busy=%s)", pygame.mixer.music.get_busy())

        # 2. Esperar hasta 500ms para que pygame confirme que está reproduciendo
        _t0 = time.time()
        while time.time() - _t0 < 0.5:
            if pygame.mixer.music.get_busy():
                break
            time.sleep(0.02)

        # 3. Animar la mandíbula mientras se reproduce
        if audio_data is not None and sr is not None:
            # Animación sincronizada con volumen real del audio
            intervalo = 0.05
            muestras  = int(sr * intervalo)
            for i in range(0, len(audio_data), muestras):
                if self._stop_evt.is_set():
                    pygame.mixer.music.stop()
                    break
                fragmento = audio_data[i:i + muestras]
                if len(fragmento) == 0:
                    break
                volumen = float(np.sqrt(np.mean(fragmento ** 2)))
                angulo  = int(min(90, volumen * 1200))
                self.jaw_angle = angulo
                self._send_jaw(angulo)
                time.sleep(intervalo)
        else:
            # Respaldo: animación sinusoidal mientras pygame reproduce
            t = 0.0
            while pygame.mixer.music.get_busy() and not self._stop_evt.is_set():
                angulo = int(abs(math.sin(t * 7.0)) * 60)
                self.jaw_angle = angulo
                self._send_jaw(angulo)
                t += 0.05
                time.sleep(0.05)
            if self._stop_evt.is_set():
                pygame.mixer.music.stop()

        # Cerrar boca al terminar
        self.jaw_angle = 0
        self._send_jaw(0)

        # Esperar que pygame termine si la animación terminó antes
        deadline = time.time() + 5.0
        while pygame.mixer.music.get_busy() and time.time() < deadline:
            time.sleep(0.05)

    def _send_jaw(self, angle: int):
        """Envía JAW,angle al Arduino (puerto jaw si está configurado, si no al principal)."""
        if self._serial:
            self._serial.send_jaw(angle)

    # ----------------------------------------------------------------
    # IA
    # ----------------------------------------------------------------

    def _nuevo_historial(self):
        return [{"role": "system", "content": _SYSTEM_PROMPT}]

    def _conversar(self, pregunta: str) -> str:
        cliente = self._ensure_groq()
        if not cliente:
            return "No tengo conexión con mi cerebro..."
        try:
            self._historial.append({"role": "user", "content": pregunta})
            resp = cliente.chat.completions.create(
                model="qwen/qwen3.8-27b",
                max_tokens=200,
                messages=self._historial,
            )
            texto = _limpiar(resp.choices[0].message.content)
            self._historial.append({"role": "assistant", "content": texto})
            if len(self._historial) > 20:
                self._historial = self._historial[:1] + self._historial[-18:]
            return texto
        except Exception as e:
            logger.error("Groq error: %s", e)
            return "Los espíritus no responden..."

    # ----------------------------------------------------------------
    # Descarga del modelo Vosk
    # ----------------------------------------------------------------

    def _auto_phrases_loop(self):
        """Hilo de fondo: dice una frase aleatoria cada auto_interval segundos."""
        logger.info("Auto-frases iniciado (intervalo=%ds)", self.auto_interval)

        # Primera frase inmediatamente, sin esperar el intervalo
        if not self._auto_stop_evt.is_set() and self.status != "hablando":
            frase = random.choice(self.auto_phrases if self.auto_phrases else _FRASES_CHUCKY)
            logger.info("Auto-frase (inmediata): %s", frase)
            self._hablar(frase)

        while not self._auto_stop_evt.is_set():
            # Esperar el intervalo configurado (despertamos cada segundo para revisar stop)
            remaining = self.auto_interval
            while remaining > 0 and not self._auto_stop_evt.is_set():
                time.sleep(min(1.0, remaining))
                remaining -= 1.0
            if self._auto_stop_evt.is_set():
                break
            if self.status != "hablando":
                frase = random.choice(self.auto_phrases if self.auto_phrases else _FRASES_CHUCKY)
                logger.info("Auto-frase: %s", frase)
                self._hablar(frase)
        self.auto_active = False
        logger.info("Auto-frases detenido")

    def _do_download(self):
        self.downloading   = True
        self.download_pct  = 0
        self.error_msg     = ""
        url = "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip"
        dest_dir  = os.path.dirname(os.path.abspath(__file__))
        zip_path  = os.path.join(dest_dir, "vosk-model-small-es-0.42.zip")
        model_dir = os.path.join(dest_dir, self.MODEL_NAME)
        try:
            import urllib.request
            import zipfile

            def _progress(count, block_size, total_size):
                if total_size > 0:
                    self.download_pct = int(count * block_size * 100 / total_size)

            logger.info("Descargando modelo Vosk desde %s …", url)
            urllib.request.urlretrieve(url, zip_path, _progress)
            self.download_pct = 100

            logger.info("Extrayendo modelo Vosk…")
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(dest_dir)
            os.unlink(zip_path)

            self.model_path = model_dir
            self._load_model()
        except Exception as e:
            self.error_msg = f"Error descargando modelo: {e}"
            logger.error(self.error_msg)
        finally:
            self.downloading = False
