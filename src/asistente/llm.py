"""Cliente del modelo de lenguaje local (Ollama).

En producción el generador es un LLM abierto de 70B servido con vLLM on-premise;
en el prototipo se usa un modelo pequeño vía Ollama, con la misma interfaz.
temperature=0 para que las respuestas sean reproducibles y auditables.
"""
from __future__ import annotations

import json
import re

import requests

from . import config
from .embeddings import ollama_disponible


class OllamaLLM:
    def __init__(self, modelo: str = config.MODELO_GENERADOR):
        self.modelo = modelo

    def chat(self, sistema: str, usuario: str, formato_json: bool = False) -> str:
        cuerpo = {
            "model": self.modelo,
            "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "num_ctx": 4096},
        }
        if formato_json:
            cuerpo["format"] = "json"
        r = requests.post(f"{config.OLLAMA_URL}/api/chat", json=cuerpo, timeout=300)
        r.raise_for_status()
        texto = r.json()["message"]["content"]
        return re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL).strip()

    def chat_json(self, sistema: str, usuario: str) -> dict:
        try:
            return json.loads(self.chat(sistema, usuario, formato_json=True))
        except (json.JSONDecodeError, requests.RequestException):
            return {}


def crear_llm() -> OllamaLLM | None:
    """Devuelve el LLM o None (modo offline: generador extractivo)."""
    modo = config.MODO_LLM
    if modo == "offline":
        return None
    if modo == "ollama" or ollama_disponible(config.MODELO_GENERADOR):
        return OllamaLLM()
    return None
