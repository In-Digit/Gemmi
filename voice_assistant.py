#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
/home/mrak/gemini_companion/voice_assistant.py

Голосовой движок Джемми (Voice Assistant Engine v2.0).
Обеспечивает:
1. Непрерывное фоновое прослушивание микрофона и детекцию wake-word («computer») через OpenWakeWord.
2. Поддержку экстренного перехвата микрофона без ключевого слова по флагу voice_force_listen.flag.
3. Распознавание речи (STT) через мультимодальный API Gemini с автосохранением в voice_input.json.
4. Синтез речи (TTS) для озвучивания ответов из voice_response.json через Gemini TTS / paplay.
5. Динамическое переключение между 4 режимами (текст/голос) на основе voice_config.json.
"""

import os
import sys
import time
import json
import fcntl
import wave
import queue
import tempfile
import threading
import subprocess
import base64
from typing import Dict, Any, List, Optional

import numpy as np
import pyaudio
import requests

# Попытка импорта OpenWakeWord
try:
    from openwakeword.model import Model
except ImportError:
    Model = None

# --- Пути к директориям и файлам обмена данными ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.expanduser("~/gemini_companion/data")
CONFIG_DIR = os.path.expanduser("~/gemini_companion/config")

VOICE_CONFIG_FILE = os.path.join(CONFIG_DIR, "voice_config.json")
VOICE_INPUT_FILE = os.path.join(DATA_DIR, "voice_input.json")
VOICE_RESPONSE_FILE = os.path.join(DATA_DIR, "voice_response.json")
VOICE_ERROR_FILE = os.path.join(DATA_DIR, "voice_error.json")
VOICE_STOP_FLAG = os.path.join(DATA_DIR, "voice_stop.flag")
FORCE_LISTEN_FLAG = os.path.join(DATA_DIR, "voice_force_listen.flag")
RECORDINGS_DIR = os.path.join(DATA_DIR, "recordings")
LOCK_FILE_PATH = "/tmp/jemi_voice.lock"

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(RECORDINGS_DIR, exist_ok=True)

# --- Параметры захвата звука (под формат OpenWakeWord) ---
CHUNK_SIZE = 1280          # 80 мс буфер при 16 кГц
AUDIO_FORMAT = pyaudio.paInt16
AUDIO_CHANNELS = 1
SAMPLE_RATE = 16000        # 16 кГц строго для моделей OpenWakeWord
SILENCE_RMS_THRESHOLD = 250 # Порог тишины (RMS 16-битного PCM)
SILENCE_LIMIT_SEC = 3.5    # Секунд тишины для автоматического завершения фразы
MAX_RECORD_TIME_SEC = 120  # Лимит одной реплики (до 2 минут)

# --- Настройки моделей по умолчанию ---
DEFAULT_STT_MODELS = [
    "gemini-2.5-flash",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
    "gemini-flash-latest"
]

DEFAULT_TTS_MODELS = [
    "gemini-2.5-flash-preview-tts",
    "gemini-3.1-flash-tts-preview"
]

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_BASE = os.getenv("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta").rstrip('/')


def acquire_voice_lock():
    """Блокировка единственного экземпляра голосового сервиса через fcntl."""
    try:
        lock_fd = os.open(LOCK_FILE_PATH, os.O_CREAT | os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(lock_fd, 0)
        os.write(lock_fd, f"{os.getpid()}\n".encode("utf-8"))
        return lock_fd
    except (BlockingIOError, OSError):
        print("[Voice] Сервис уже запущен на этой системе. Выход.")
        sys.exit(0)


def load_voice_config() -> Dict[str, Any]:
    """Загружает актуальную конфигурацию режимов и моделей."""
    default_cfg = {
        "mode": "text_text",  # "text_text", "text_voice", "voice_voice", "voice_text"
        "wake_word": "computer",
        "voice_name": "Aoede", # Голос Gemini: Aoede, Charon, Fenrir, Kore, Puck
        "stt_models": DEFAULT_STT_MODELS,
        "tts_models": DEFAULT_TTS_MODELS,
        "silence_threshold": SILENCE_RMS_THRESHOLD,
        "silence_limit_sec": SILENCE_LIMIT_SEC
    }
    if os.path.exists(VOICE_CONFIG_FILE):
        try:
            with open(VOICE_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_cfg.update(data)
        except Exception as e:
            print(f"[Voice] Ошибка чтения {VOICE_CONFIG_FILE}: {e}")
    return default_cfg


def is_silence(raw_data: bytes, threshold: int) -> bool:
    """Вычисляет среднеквадратичную амплитуду (RMS) фрейма и сравнивает с порогом тишины."""
    try:
        audio_array = np.frombuffer(raw_data, dtype=np.int16)
        if len(audio_array) == 0:
            return True
        rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
        return rms < threshold
    except Exception:
        return True


def save_wav_file(frames: List[bytes], target_path: str):
    """Сохраняет PCM фреймы в валидный WAV-файл."""
    wf = wave.open(target_path, 'wb')
    wf.setnchannels(AUDIO_CHANNELS)
    wf.setsampwidth(pyaudio.PyAudio().get_sample_size(AUDIO_FORMAT))
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(b''.join(frames))
    wf.close()


def request_gemini_stt(wav_bytes: bytes, model_name: str) -> Optional[str]:
    """Отправляет аудиозапись в Gemini STT для распознавания русской речи."""
    if not GEMINI_API_KEY:
        return None

    url = f"{GEMINI_API_BASE}/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    audio_b64 = base64.b64encode(wav_bytes).decode("utf-8")

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": "audio/wav",
                            "data": audio_b64
                        }
                    },
                    {
                        "text": "Распознай русскую речь в аудиозаписи с высокой точностью. "
                                "Верни только итоговый текст без пояснений, кавычек и форматирования."
                    }
                ]
            }
        ]
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text_result = "".join([p.get("text", "") for p in parts if "text" in p]).strip()
                return text_result
    except Exception as e:
        print(f"[Voice STT] Сбой модели {model_name}: {e}")
    return None


def request_gemini_tts(text: str, model_name: str, voice_name: str) -> Optional[bytes]:
    """Генерирует аудио речи через Gemini TTS API."""
    if not GEMINI_API_KEY:
        return None

    url = f"{GEMINI_API_BASE}/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": text}]
            }
        ],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_name
                    }
                }
            }
        }
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                for p in parts:
                    if "inlineData" in p and "data" in p["inlineData"]:
                        return base64.b64decode(p["inlineData"]["data"])
    except Exception as e:
        print(f"[Voice TTS] Сбой модели {model_name}: {e}")
    return None


def play_audio(audio_bytes: bytes):
    """Воспроизводит сгенерированный аудиопоток через mpv без создания окон."""
    if not audio_bytes:
        return
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        subprocess.run(["mpv", "--no-video", "--volume=100", tmp_path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# --- Фоновый слушатель микрофона и Wake-Word ---

class VoiceListenerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.running = True
        self.oww_model = None
        self._init_wakeword()

    def _init_wakeword(self):
        """Инициализация модели OpenWakeWord с поддержкой современных версий библиотеки."""
        if Model is None:
            print("[Voice] Библиотека openwakeword не установлена. Распознавание wake-word отключено.")
            return
        try:
            cfg = load_voice_config()
            target_word = cfg.get("wake_word", "computer")
            # Новый синтаксис OpenWakeWord: создаем модель и явно загружаем таргет
            self.oww_model = Model(wakeword_models=[target_word], inference_framework="onnx")
            print(f"[Voice] Детектор ключевого слова '{target_word}' инициализирован.")
        except Exception as e:
            try:
                # Фоллбэк на альтернативный вызов
                self.oww_model = Model()
                self.oww_model.load_models(wakeword_models=[target_word])
                print(f"[Voice] Детектор ключевого слова '{target_word}' загружен через fallback API.")
            except Exception as ex:
                print(f"[Voice] Не удалось инициализировать OpenWakeWord: {ex}")
                self.oww_model = None

    def run(self):
        p = pyaudio.PyAudio()
        try:
            stream = p.open(
                format=AUDIO_FORMAT,
                channels=AUDIO_CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE
            )
        except Exception as e:
            print(f"[Voice] Ошибка открытия микрофона: {e}")
            return

        print("[Voice] Поток прослушивания микрофона активен.")

        while self.running and not os.path.exists(VOICE_STOP_FLAG):
            cfg = load_voice_config()
            mode = cfg.get("mode", "text_text")
            wake_word = cfg.get("wake_word", "computer")
            threshold = cfg.get("silence_threshold", SILENCE_RMS_THRESHOLD)
            silence_limit = cfg.get("silence_limit_sec", SILENCE_LIMIT_SEC)

            # Проверяем, есть ли флаг принудительной экстренной записи от проактивного движка
            force_listen = os.path.exists(FORCE_LISTEN_FLAG)

            # Слушаем микрофон только в режимах с голосовым вводом или при экстренном оклике
            is_voice_input_enabled = mode in ["voice_voice", "voice_text"] or force_listen

            if not is_voice_input_enabled:
                time.sleep(0.5)
                continue

            try:
                audio_data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            except Exception:
                continue

            trigger_activated = False

            if force_listen:
                print("[Voice] Обнаружен флаг экстренного слушания. Начинаю запись реплики...")
                try:
                    os.remove(FORCE_LISTEN_FLAG)
                except Exception:
                    pass
                trigger_activated = True
            elif self.oww_model is not None:
                # Передаем аудиофрейм в OpenWakeWord
                audio_np = np.frombuffer(audio_data, dtype=np.int16)
                prediction = self.oww_model.predict(audio_np)
                score = prediction.get(wake_word, 0.0)
                if score > 0.5:
                    print(f"[Voice] Ключевое слово '{wake_word}' обнаружено (уверенность: {score:.2f})!")
                    # Звуковой сигнал готовности слушать
                    subprocess.run(["paplay", "/usr/share/sounds/freedesktop/stereo/message.oga"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    trigger_activated = True

            if trigger_activated:
                self._record_and_process_phrase(stream, threshold, silence_limit, cfg)

        try:
            stream.stop_stream()
            stream.close()
            p.terminate()
        except Exception:
            pass

    def _record_and_process_phrase(self, stream, threshold: int, silence_limit: float, cfg: Dict[str, Any]):
        """Записывает реплику до наступления тишины и передает в Gemini STT."""
        recorded_frames = []
        record_start = time.time()
        last_voice_time = time.time()

        print("[Voice] Запись речи началась...")

        while time.time() - record_start < MAX_RECORD_TIME_SEC:
            if os.path.exists(VOICE_STOP_FLAG):
                break
            try:
                chunk = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                recorded_frames.append(chunk)

                if not is_silence(chunk, threshold):
                    last_voice_time = time.time()
                else:
                    # Если тишина длится дольше порога — фраза закончена
                    if time.time() - last_voice_time > silence_limit:
                        print("[Voice] Детектор зафиксировал окончание фразы.")
                        break
            except Exception:
                break

        if not recorded_frames:
            return

        timestamp = int(time.time())
        wav_path = os.path.join(RECORDINGS_DIR, f"voice_{timestamp}.wav")
        save_wav_file(recorded_frames, wav_path)

        with open(wav_path, "rb") as f:
            wav_bytes = f.read()

        stt_models = cfg.get("stt_models", DEFAULT_STT_MODELS)
        recognized_text = None

        for model in stt_models:
            print(f"[Voice] Попытка STT через модель: {model}...")
            recognized_text = request_gemini_stt(wav_bytes, model)
            if recognized_text:
                print(f"[Voice STT Успех]: «{recognized_text}»")
                break

        if recognized_text:
            # Записываем распознанный текст для Джемми
            payload = {
                "text": recognized_text,
                "timestamp": datetime.now().isoformat()
            }
            with open(VOICE_INPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            # Чистим временный wav-файл
            if os.path.exists(wav_path):
                os.remove(wav_path)
        else:
            print("[Voice STT Ошибка]: Ни одна модель не смогла распознать аудио.")
            err_data = {
                "wav_path": wav_path,
                "timestamp": datetime.now().isoformat(),
                "error": "STT_RECOGNITION_FAILED"
            }
            with open(VOICE_ERROR_FILE, "w", encoding="utf-8") as f:
                json.dump(err_data, f, ensure_ascii=False, indent=2)


# --- Фоновый поток озвучивания ответов (TTS) ---

class VoiceSpeakerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.running = True

    def run(self):
        print("[Voice] Поток воспроизведения ответов (TTS) активен.")
        while self.running and not os.path.exists(VOICE_STOP_FLAG):
            if os.path.exists(VOICE_RESPONSE_FILE):
                try:
                    with open(VOICE_RESPONSE_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    text_to_speak = data.get("text", "").strip()

                    # Удаляем файл ответа сразу, чтобы не зациклить проигрывание
                    if os.path.exists(VOICE_RESPONSE_FILE):
                        os.remove(VOICE_RESPONSE_FILE)

                    if text_to_speak:
                        cfg = load_voice_config()
                        mode = cfg.get("mode", "text_text")
                        voice_name = cfg.get("voice_name", "Aoede")
                        tts_models = cfg.get("tts_models", DEFAULT_TTS_MODELS)

                        # Озвучиваем только если в режиме разрешен голосовой выход
                        is_tts_allowed = mode in ["text_voice", "voice_voice"]

                        if is_tts_allowed:
                            print(f"[Voice TTS] Синтезирую ответ: «{text_to_speak[:50]}...»")
                            audio_bytes = None
                            for model in tts_models:
                                audio_bytes = request_gemini_tts(text_to_speak, model, voice_name)
                                if audio_bytes:
                                    play_audio(audio_bytes)
                                    break

                            if not audio_bytes:
                                print("[Voice TTS] Ошибка синтеза во всех моделях. Даю системный сигнал.")
                                subprocess.run(["paplay", "/usr/share/sounds/freedesktop/stereo/bell.oga"],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception as e:
                    print(f"[Voice TTS] Ошибка обработки ответа: {e}")

            time.sleep(0.2)


def main():
    lock_fd = acquire_voice_lock()

    if os.path.exists(VOICE_STOP_FLAG):
        os.remove(VOICE_STOP_FLAG)

    print("=== [Джеми: Голосовой ассистент запущен] ===")
    cfg = load_voice_config()
    print(f"Текущий режим: {cfg.get('mode', 'text_text')}")
    print(f"Wake-word: {cfg.get('wake_word', 'computer')}")

    listener = VoiceListenerThread()
    speaker = VoiceSpeakerThread()

    listener.start()
    speaker.start()

    try:
        while not os.path.exists(VOICE_STOP_FLAG):
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[Voice] Остановка по Ctrl+C...")
    finally:
        listener.running = False
        speaker.running = False
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            if os.path.exists(LOCK_FILE_PATH):
                os.remove(LOCK_FILE_PATH)
        except Exception:
            pass
        print("[Voice] Голосовой сервис остановлен.")


if __name__ == "__main__":
    from datetime import datetime
    main()
