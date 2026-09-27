"""Trazabilidad (RNF-06): una línea JSON por consulta.

Sustituto local de Langfuse para el prototipo: guarda la consulta, la reescritura,
los fragmentos con sus puntajes, las llamadas a herramientas, el veredicto del
verificador y la latencia de cada etapa. El usuario se registra seudonimizado.
Las consultas sin respaldo alimentan el backlog de curaduría documental.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from . import config


def seudonimo(usuario: str) -> str:
    return "u-" + hashlib.sha256(usuario.encode()).hexdigest()[:10]


def registrar(traza: dict, directorio: Path | None = None) -> None:
    directorio = directorio or config.DIR_TRAZAS
    directorio.mkdir(parents=True, exist_ok=True)
    archivo = directorio / f"trazas_{datetime.now():%Y-%m-%d}.jsonl"
    with archivo.open("a", encoding="utf-8") as f:
        f.write(json.dumps(traza, ensure_ascii=False) + "\n")


def leer_trazas(directorio: Path | None = None) -> list[dict]:
    directorio = directorio or config.DIR_TRAZAS
    trazas = []
    for archivo in sorted(directorio.glob("trazas_*.jsonl")):
        trazas += [json.loads(l) for l in archivo.read_text(encoding="utf-8").splitlines() if l.strip()]
    return trazas
