import os
import json
from mistralai import Mistral
from dotenv import load_dotenv

load_dotenv()

_client = Mistral(api_key=os.environ.get("MISTRAL_API_KEY", "unset"))

def set_api_key(key: str):
    global _client
    _client = Mistral(api_key=key)

def classify_intent(user_input: str) -> dict:
    """
    POC mode: always returns CHAT mode.
    """
    return {"mode": "CHAT", "topic": "general conversation"}


def route(user_input: str) -> tuple[str, str]:
    """
    Convenience wrapper. Returns (session_mode, topic).
    """
    result = classify_intent(user_input)
    session_mode = result["mode"]
    topic = result["topic"]
    return session_mode, topic
