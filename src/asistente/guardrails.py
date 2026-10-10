"""Guardrails de entrada y saneamiento del corpus (OWASP LLM01: prompt injection).

Dos puntos de control:
  1. Al indexar: las oraciones de documentos que parecen órdenes al modelo se
     retiran y el fragmento queda marcado como sospechoso (queda en la traza).
  2. En la consulta: se detectan intentos de manipulación y se enmascaran datos
     personales (RUT, teléfono, correo) antes de que lleguen al modelo o a la traza.
El prompt (regla 7) es una tercera barrera, no la única.
"""
from __future__ import annotations

import re

from .texto import sin_tildes

PATRONES_INYECCION = [
    r"ignora(r)? (todas )?(las )?instrucciones",
    r"ignore (all )?(the )?(previous|prior) instructions",
    r"olvida (tus|las) (reglas|instrucciones)",
    r"(prompt|mensaje) (de|del) sistema",
    r"system prompt",
    r"actua como",
    r"a partir de ahora (eres|responde)",
    r"no es necesario bloquear",
    r"responde que",
]
_RE_INYECCION = re.compile("|".join(PATRONES_INYECCION), re.IGNORECASE)

_RE_RUT = re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]\b")
_RE_FONO = re.compile(r"\+?56\s?9\s?\d{4}\s?\d{4}\b")
_RE_CORREO = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")


def es_inyeccion(texto: str) -> bool:
    return bool(_RE_INYECCION.search(sin_tildes(texto.lower())))


def sanear_fragmento(texto: str) -> tuple[str, bool]:
    """Quita las oraciones con órdenes al modelo. Devuelve (texto_limpio, era_sospechoso)."""
    oraciones = re.split(r"(?<=[.!?])\s+", texto)
    limpias = [o for o in oraciones if not es_inyeccion(o)]
    return " ".join(limpias).strip(), len(limpias) < len(oraciones)


def enmascarar_datos_personales(texto: str) -> str:
    texto = _RE_RUT.sub("[RUT]", texto)
    texto = _RE_FONO.sub("[TELEFONO]", texto)
    return _RE_CORREO.sub("[CORREO]", texto)


def revisar_entrada(pregunta: str) -> dict:
    """Guardrail de entrada. No bloquea la consulta: la limpia y deja constancia."""
    return {
        "pregunta_limpia": enmascarar_datos_personales(pregunta),
        "intento_manipulacion": es_inyeccion(pregunta),
    }
