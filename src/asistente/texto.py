"""Utilidades de texto compartidas: normalización, tokenización y glosario de faena."""
from __future__ import annotations

import re
import unicodedata

STOPWORDS = set("""
a al algo ante antes con como cual cuales cuando de del desde donde e el en entre es esa ese eso esta este esto
la las le les lo los mas me mi muy no o para pero por que qué se si sin sobre su sus tambien te tiene tu un una uno unos y ya
hay debo debe hacer hago tengo cada cuanto cuánto primero
the a an and are as at be by for from if in into is it of on or the to with when while then these this that
""".split())

# Expansión de jerga y siglas de faena (sección 3.3: "se expanden las siglas de faena").
SIGLAS = {
    "caex": "camión de extracción",
    "loto": "bloqueo etiquetado",
    "epp": "equipo de protección personal",
    "hds": "hoja de datos de seguridad",
    "pets": "procedimiento de trabajo seguro",
    "ot": "orden de trabajo",
    "ds132": "reglamento de seguridad minera",
}

# Glosario español -> inglés para consultar manuales OEM en inglés (RF-02).
# bge-m3 ya es multilingüe; el glosario ayuda al índice léxico BM25.
GLOSARIO_ES_EN = {
    "temperatura": "temperature", "refrigerante": "coolant", "radiador": "radiator",
    "correa": "belt", "ventilador": "fan", "termostato": "thermostat", "aceite": "oil",
    "hidraulico": "hydraulic", "tapa": "cap", "nivel": "level", "estanque": "tank",
    "frenos": "brakes", "direccion": "steering", "alarma": "alarm", "motor": "engine",
    "aletas": "fins", "polvo": "dust", "desgaste": "wear", "tension": "tension",
    "revisar": "check inspect", "reviso": "check inspect", "cambio": "replace", "boletin": "bulletin",
    "horas": "hours", "sobretemperatura": "high temperature",
}


def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def raiz(palabra: str) -> str:
    """Stemming mínimo por prefijo: bloquear/bloqueo -> 'bloqu'. Suficiente para el prototipo."""
    return palabra[:5] if len(palabra) > 5 else palabra


def tokenizar(texto: str, stem: bool = True) -> list[str]:
    texto = sin_tildes(texto.lower())
    palabras = re.findall(r"[a-z0-9]+", texto)
    palabras = [p for p in palabras if p not in STOPWORDS and len(p) > 1]
    return [raiz(p) for p in palabras] if stem else palabras


def expandir_glosario(texto: str) -> str:
    """Agrega los equivalentes en inglés y las siglas expandidas al final de la consulta."""
    extras = []
    for palabra in tokenizar(texto, stem=False):
        if palabra in GLOSARIO_ES_EN:
            extras.append(GLOSARIO_ES_EN[palabra])
        if palabra in SIGLAS:
            extras.append(SIGLAS[palabra])
    return texto if not extras else f"{texto} {' '.join(extras)}"


def dividir_oraciones(texto: str) -> list[str]:
    partes = re.split(r"(?<=[.!?])\s+|\n+", texto)
    return [p.strip() for p in partes if len(p.strip().split()) >= 4]


def cobertura(consulta: str, texto: str) -> float:
    """Fracción de términos de la consulta que aparecen en el texto (0..1)."""
    q = set(tokenizar(consulta))
    if not q:
        return 0.0
    return len(q & set(tokenizar(texto))) / len(q)
