"""Agente Verificador (capa 2 del diagrama, sección 2.3 del informe).

Separa generar de verificar: el modelo que quiere completar la respuesta no es
el que juzga si tiene respaldo.

El verificador determinista se ejecuta siempre y es la barrera principal:
  1. Toda afirmación debe cerrar con una cita (R2).
  2. La cita debe corresponder a un fragmento recuperado o a un registro de la
     herramienta (no se aceptan citas inventadas).
  3. El contenido de la afirmación debe estar en el fragmento citado
     (solapamiento léxico o similitud semántica con bge-m3 si está disponible).
Si hay un LLM disponible y VERIFICADOR_LLM=1, se agrega su veredicto y se
queda el más conservador de los dos.
"""
from __future__ import annotations

import os
import re

import numpy as np

from .config import FRASE_ABSTENCION
from .prompts import PLANTILLA_VERIFICADOR, PROMPT_VERIFICADOR, cita, formatear_fragmento
from .texto import GLOSARIO_ES_EN, tokenizar

_RE_CITA = re.compile(r"\[([^\[\]]+\|[^\[\]]+)\]")
UMBRAL_SOLAPAMIENTO = 0.5
UMBRAL_SEMANTICO = 0.62
ORDEN_VEREDICTO = {"RESPALDADA": 2, "PARCIAL": 1, "SIN_RESPALDO": 0}


def _normalizar_cita(texto: str) -> str:
    return " | ".join(p.strip() for p in texto.strip("[] ").split("|"))


def extraer_afirmaciones(respuesta: str) -> list[str]:
    lineas = [l.strip() for l in respuesta.splitlines() if l.strip()]
    # Las notas de sistema (R6) y la frase de abstención no son afirmaciones del corpus.
    return [l for l in lineas if not l.lower().startswith(("nota:", "atención:", "atencion:")) and FRASE_ABSTENCION not in l]


def _tokens_con_traduccion(texto: str) -> set[str]:
    base = tokenizar(texto, stem=False)
    tokens = set(tokenizar(texto))
    for p in base:
        if p in GLOSARIO_ES_EN:
            tokens |= set(tokenizar(GLOSARIO_ES_EN[p]))
    return tokens


def solapamiento(afirmacion: str, fuente: str) -> float:
    a = _tokens_con_traduccion(afirmacion)
    return len(a & set(tokenizar(fuente))) / len(a) if a else 0.0


class Verificador:
    def __init__(self, embedder=None, llm=None):
        self.embedder = embedder
        self.llm = llm if os.getenv("VERIFICADOR_LLM") == "1" else None
        self.semantico = embedder is not None and embedder.nombre.startswith("ollama")

    def _fuentes(self, fragmentos: list[dict], historial: dict | None) -> dict[str, str]:
        fuentes = {_normalizar_cita(cita(f)): f["texto"] for f in fragmentos}
        for o in (historial or {}).get("ordenes", []):
            clave = _normalizar_cita(f"[SAP-PM | {o['ot']} | {o['fecha']} | dato operacional]")
            fuentes[clave] = f"{o['ot']} {o['fecha']} {o['sintoma']} {o['causa']} {o['accion']} {o['horas_detencion']}"
        return fuentes

    def verificar(self, respuesta: str, fragmentos: list[dict], historial: dict | None = None) -> dict:
        fuentes = self._fuentes(fragmentos, historial)
        afirmaciones = extraer_afirmaciones(respuesta)
        detalle, sin_respaldo = [], []
        for af in afirmaciones:
            citas = [_normalizar_cita(c) for c in _RE_CITA.findall(af)]
            cuerpo = _RE_CITA.sub("", af)
            if not citas:
                estado, motivo, puntaje = False, "sin cita", 0.0
            elif not all(c in fuentes for c in citas):
                estado, motivo, puntaje = False, "cita que no corresponde a ningún fragmento recuperado", 0.0
            else:
                fuente = " ".join(fuentes[c] for c in citas)
                puntaje = solapamiento(cuerpo, fuente)
                if puntaje < UMBRAL_SOLAPAMIENTO and self.semantico:
                    v = self.embedder.embed([cuerpo, fuente])
                    puntaje = max(puntaje, float(v[0] @ v[1]))
                    estado = puntaje >= UMBRAL_SEMANTICO
                else:
                    estado = puntaje >= UMBRAL_SOLAPAMIENTO
                motivo = "respaldada" if estado else "el fragmento citado no respalda la afirmación"
            detalle.append({"afirmacion": af, "respaldada": estado, "motivo": motivo, "puntaje": round(puntaje, 3)})
            if not estado:
                sin_respaldo.append(af)

        if not afirmaciones:
            veredicto = "SIN_RESPALDO"
        elif not sin_respaldo:
            veredicto = "RESPALDADA"
        elif len(sin_respaldo) < len(afirmaciones):
            veredicto = "PARCIAL"
        else:
            veredicto = "SIN_RESPALDO"

        fidelidad = (len(afirmaciones) - len(sin_respaldo)) / len(afirmaciones) if afirmaciones else 0.0

        if self.llm is not None and afirmaciones:
            veredicto = self._combinar_con_llm(veredicto, respuesta, fragmentos, sin_respaldo)
        return {"veredicto": veredicto, "sin_respaldo": sin_respaldo, "fidelidad": round(fidelidad, 3), "detalle": detalle}

    def _combinar_con_llm(self, veredicto: str, respuesta: str, fragmentos: list[dict], sin_respaldo: list[str]) -> str:
        juicio = self.llm.chat_json(PROMPT_VERIFICADOR, PLANTILLA_VERIFICADOR.format(
            respuesta=respuesta, fragmentos="\n".join(formatear_fragmento(f) for f in fragmentos)))
        v_llm = juicio.get("veredicto", "SIN_RESPALDO")
        if v_llm not in ORDEN_VEREDICTO:
            return veredicto
        sin_respaldo.extend(x for x in juicio.get("sin_respaldo", []) if isinstance(x, str) and x not in sin_respaldo)
        return min(veredicto, v_llm, key=ORDEN_VEREDICTO.get)
