"""Embeddings densos.

- OllamaEmbedder: bge-m3 servido localmente por Ollama (multilingüe: pregunta en
  español sobre un manual en inglés; Chen et al., 2024). Es la opción del informe.
- HashingEmbedder: respaldo sin red ni GPU basado en n-gramas de caracteres.
  Solo se usa si Ollama no está disponible (por ejemplo, en CI o en las pruebas).
"""
from __future__ import annotations

import hashlib

import numpy as np
import requests

from . import config
from .texto import sin_tildes


class OllamaEmbedder:
    def __init__(self, modelo: str = config.MODELO_EMBEDDINGS, url: str = config.OLLAMA_URL):
        self.modelo = modelo
        self.url = url
        self.nombre = f"ollama:{modelo}"

    def embed(self, textos: list[str]) -> np.ndarray:
        resp = requests.post(f"{self.url}/api/embed", json={"model": self.modelo, "input": textos}, timeout=300)
        resp.raise_for_status()
        m = np.array(resp.json()["embeddings"], dtype=np.float32)
        return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)


class HashingEmbedder:
    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.nombre = f"hashing:{dim}"

    def _vector(self, texto: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        t = f" {sin_tildes(texto.lower())} "
        for n in (3, 4, 5):
            for i in range(len(t) - n + 1):
                h = int(hashlib.md5(t[i:i + n].encode()).hexdigest()[:8], 16)
                v[h % self.dim] += 1.0
        return v / (np.linalg.norm(v) + 1e-9)

    def embed(self, textos: list[str]) -> np.ndarray:
        return np.vstack([self._vector(t) for t in textos])


def ollama_disponible(modelo: str) -> bool:
    try:
        r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=2)
        nombres = {m["name"] for m in r.json().get("models", [])}
        return modelo in nombres or f"{modelo}:latest" in nombres
    except Exception:
        return False


def crear_embedder():
    modo = config.MODO_EMBEDDINGS
    if modo == "ollama" or (modo == "auto" and ollama_disponible(config.MODELO_EMBEDDINGS)):
        return OllamaEmbedder()
    return HashingEmbedder()
