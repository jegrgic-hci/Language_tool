import os
import uuid
import tempfile
import speech_recognition as sr
import pygame
from gtts import gTTS


def speak_french(text: str) -> None:
    tmp_path = os.path.join(tempfile.gettempdir(), f"french_tts_{uuid.uuid4().hex}.mp3")
    try:
        tts = gTTS(text=text, lang="fr")
        tts.save(tmp_path)

        pygame.mixer.init()
        pygame.mixer.music.load(tmp_path)
        pygame.mixer.music.play()

        while pygame.mixer.music.get_busy():
            pygame.time.Clock().tick(10)

        pygame.mixer.music.unload()
        pygame.mixer.quit()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def listen_french() -> str:
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Listening...")
        try:
            audio = recognizer.listen(source, timeout=7, phrase_time_limit=15)
            return recognizer.recognize_google(audio, language="fr-FR")
        except (sr.UnknownValueError, sr.WaitTimeoutError):
            print("Could not hear you, please type: ", end="")
            return input()
