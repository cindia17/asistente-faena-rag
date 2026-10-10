"""Control de contexto conversacional.

- Memoria de sesión acotada a los últimos N turnos (no crece sin límite).
- Reescritura de la consulta con el historial: resuelve elipsis ("¿y la correa?")
  heredando el equipo y el tema del turno anterior, y expande siglas y jerga.
- Detección del equipo mencionado ("camión 14" -> CAEX-14) para filtrar por
  metadatos y para consultar su historial exacto en SAP PM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config
from .texto import expandir_glosario, sin_tildes, tokenizar

_RE_CAEX = re.compile(r"\b(?:camion|caex)[\s\-]*(?:n[°ºo.]?\s*)?(\d{1,3})\b")
_RE_CHANC = re.compile(r"\bchancador")


def detectar_equipo(texto: str) -> str | None:
    t = sin_tildes(texto.lower())
    if m := _RE_CAEX.search(t):
        return f"CAEX-{int(m.group(1)):02d}"
    if _RE_CHANC.search(t):
        return "CHANC-01"
    return None


def tipo_de_equipo(equipo: str | None) -> str | None:
    if not equipo:
        return None
    return {"CAEX": "CAEX", "CHANC": "CHANCADOR", "PALA": "PALA"}.get(equipo.split("-")[0])


@dataclass
class Turno:
    pregunta: str
    respuesta: str
    equipo: str | None


@dataclass
class MemoriaSesion:
    turnos: list[Turno] = field(default_factory=list)

    def agregar(self, pregunta: str, respuesta: str, equipo: str | None) -> None:
        self.turnos.append(Turno(pregunta, respuesta, equipo))
        self.turnos = self.turnos[-config.TURNOS_HISTORIAL:]

    def ultimo_equipo(self) -> str | None:
        for t in reversed(self.turnos):
            if t.equipo:
                return t.equipo
        return None

    def como_texto(self) -> str:
        if not self.turnos:
            return "(sin historial)"
        return "\n".join(f"Trabajador: {t.pregunta}\nAsistente: {t.respuesta[:300]}" for t in self.turnos)


def reescribir_consulta(pregunta: str, memoria: MemoriaSesion) -> dict:
    equipo = detectar_equipo(pregunta)
    heredado = False
    consulta = pregunta
    # Consulta elíptica: pocas palabras de contenido y sin equipo propio -> hereda contexto.
    if memoria.turnos and len(tokenizar(pregunta)) <= 4:
        previa = memoria.turnos[-1].pregunta
        consulta = f"{pregunta} (contexto previo: {previa})"
        heredado = True
    if not equipo and memoria.ultimo_equipo() and (heredado or re.search(r"\b(el mismo|ese equipo|este equipo)\b", pregunta.lower())):
        equipo = memoria.ultimo_equipo()
        heredado = True
    return {
        "consulta": expandir_glosario(consulta),
        "equipo": equipo,
        "equipo_tipo": tipo_de_equipo(equipo),
        "hereda_contexto": heredado,
    }
