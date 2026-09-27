"""Agentes especializados (capa 3 del diagrama) y clasificación del Orquestador.

  - Orquestador:      clasifica la intención y decide a qué agentes delegar.
  - Procedimientos:   RAG normativo (PETS, DS 132, hojas de seguridad).
  - Mantenimiento:    manuales OEM + boletines + historial exacto de SAP PM.
  - Reportes:         pre-llena incidentes/cuasi accidentes; registra solo con confirmación.
  - Verificador:      ver verificador.py.

Cada agente tiene su propio filtro de tipos de documento y sus propios permisos:
ninguno tiene permisos de escritura salvo Reportes, y solo tras confirmación.
"""
from __future__ import annotations

import re

from . import config
from .herramientas import cita_ot
from .prompts import (PLANTILLA_USUARIO, PROMPT_MANTENIMIENTO, PROMPT_PROCEDIMIENTOS,
                      PROMPT_REPORTES, cita, formatear_fragmento)
from .texto import GLOSARIO_ES_EN, SIGLAS, dividir_oraciones, raiz, sin_tildes, tokenizar

TIPOS_POR_AGENTE = {
    "procedimientos": {"procedimiento", "normativa", "hoja_seguridad", "reporte_incidente"},
    "mantenimiento": {"manual", "boletin", "procedimiento", "ficha_proveedor"},
}

ESCALAR_A = {
    "procedimientos": "Consulta al supervisor de turno o a Prevención de Riesgos.",
    "mantenimiento": "Consulta al planificador de mantenimiento.",
    "mixta": "Consulta al supervisor de turno y al planificador de mantenimiento.",
    "fuera_de_alcance": "Consulta al supervisor de turno.",
}

_PALABRAS = {
    "reporte": r"\b(reportar|reporte|informar un|cuasi ?accidente|incidente que|casi me|se me cay|tuve un)\b",
    "mantenimiento": r"\b(falla|fallo|alarma|temperatura|marca|diagnos|historial|aceite|correa|radiador|manual|repuesto|fuga|termostato|refrigerante|hidraulic|ventilador|revis|boletin)\w*",
    "procedimientos": r"\b(procedimiento|bloque|epp|ds ?132|reglamento|norma|permiso|seguridad|candado|riesgo|primeros auxilios|ingest|piel|capacitaci|energia)\w*",
}
_INTERVENCION = r"\b(revis|interven|cambi|reparar|desmont|abrir|limpi)\w*"


def clasificar_intencion(pregunta: str) -> str:
    t = sin_tildes(pregunta.lower())
    if re.search(_PALABRAS["reporte"], t):
        return "reporte"
    mant = bool(re.search(_PALABRAS["mantenimiento"], t))
    proc = bool(re.search(_PALABRAS["procedimientos"], t))
    if mant and (proc or re.search(_INTERVENCION, t)):
        return "mixta"
    if mant:
        return "mantenimiento"
    if proc:
        return "procedimientos"
    return "fuera_de_alcance"


# ---------------------------------------------------------------------------
# Respondibilidad: ¿los fragmentos contestan lo que se preguntó?
# ---------------------------------------------------------------------------
GENERICOS = set("""camion caex equipo marca alta alto bajo revis reviso revisar hacer hago cada cuanto cuanta
numero primero debo puedo necesito quiero dime sabes uso usar segun como cambia cambiar cambio se hay tengo
dice sobre cual cuales pasos paso antes despues trabajador mecanico""".split())
_RE_PIDE_FRECUENCIA = re.compile(r"cada cu[aá]nto|frecuencia|intervalo|cu[aá]ntas horas|cada cu[aá]ntas")
_RE_FRECUENCIA = re.compile(r"\b(every|cada)\s+\d+|\d+\s*(horas|hours|dias|days|meses|months|km)\b|\b(daily|diari)", re.I)


def grupos_nucleo(pregunta: str) -> list[set[str]]:
    grupos = []
    for p in tokenizar(pregunta, stem=False):
        if p in GENERICOS or p.isdigit():
            continue
        g = {raiz(p)}
        extra = GLOSARIO_ES_EN.get(p, "") + " " + SIGLAS.get(p, "")
        g |= set(tokenizar(extra))
        grupos.append(g)
    return grupos


def evaluar_respondibilidad(pregunta: str, fragmentos: list[dict]) -> dict:
    """Segunda condición de la abstención, además del umbral del reranker.

    Un fragmento puede parecerse a la pregunta sin contestarla (p. ej. "check the
    hydraulic tank level daily" ante "¿cada cuánto se cambia el aceite hidráulico?").
    Se exige que algún fragmento cubra los conceptos núcleo de la pregunta y, si se
    pide una frecuencia, que la contenga en la misma oración que esos conceptos.
    """
    grupos = grupos_nucleo(pregunta)
    if not grupos or not fragmentos:
        return {"respondible": bool(fragmentos), "cobertura_nucleo": 0.0}
    mejor = 0.0
    for f in fragmentos:
        tokens = set(tokenizar(f"{f['titulo']} {f['seccion']} {f['texto']}"))
        mejor = max(mejor, sum(bool(g & tokens) for g in grupos) / len(grupos))
    respondible = mejor >= 0.6
    if respondible and _RE_PIDE_FRECUENCIA.search(sin_tildes(pregunta.lower())):
        respondible = any(
            _RE_FRECUENCIA.search(o) and sum(bool(g & set(tokenizar(o))) for g in grupos) / len(grupos) >= 0.6
            for f in fragmentos for o in dividir_oraciones(f["texto"])
        )
    return {"respondible": respondible, "cobertura_nucleo": round(mejor, 3)}


# ---------------------------------------------------------------------------
# Generación
# ---------------------------------------------------------------------------
def _mejor_oracion(consulta: str, frag: dict, preferir: str | None = None) -> str | None:
    oraciones = dividir_oraciones(frag["texto"])
    if preferir:
        preferidas = [o for o in oraciones if preferir in sin_tildes(o.lower())]
        oraciones = preferidas or oraciones
    if not oraciones:
        return None
    q = set(tokenizar(consulta))
    return max(oraciones, key=lambda o: len(q & set(tokenizar(o))))


def generar_extractivo(consulta: str, fragmentos: list[dict], historial: dict | None, requiere_bloqueo: bool) -> str:
    """Generador sin LLM: arma la respuesta con oraciones textuales de los fragmentos.

    Es fiel por construcción (cada línea es una cita literal), por eso se usa en el
    modo offline y como reintento cuando el borrador del LLM no pasa la verificación.
    """
    lineas: list[str] = []
    usados = set()
    orden = list(fragmentos)
    if requiere_bloqueo:   # R5: el bloqueo de energías va primero
        con_bloqueo = [f for f in orden if "bloque" in sin_tildes(f["texto"].lower())]
        if con_bloqueo:
            f = con_bloqueo[0]
            if o := _mejor_oracion("bloquear energias antes de tocar", f, preferir="bloque"):
                lineas.append(f"{o} {cita(f)}")
                usados.add(f["id"])
    q = set(tokenizar(consulta))
    # El historial exacto se reserva primero para que el límite de palabras no lo deje fuera.
    lineas_historial = [f"Historial del equipo: el {o['fecha']} registró \"{o['sintoma']}\"; "
                        f"causa: {o['causa']}; acción: {o['accion']}. {cita_ot(o)}"
                        for o in _ordenes_pertinentes(consulta, historial)]
    presupuesto = config.MAX_PALABRAS - sum(len(l.split()) for l in lineas + lineas_historial)
    for f in orden:
        if f["id"] in usados or len(lineas) >= 4:
            continue
        o = _mejor_oracion(consulta, f)
        if not o or not (q & set(tokenizar(o))):
            continue
        if f["idioma"] == "en":
            o = f"Manual/boletín del fabricante (texto original en inglés): \"{o}\""
        linea = f"{o} {cita(f)}"
        if len(linea.split()) > presupuesto:     # R8: máximo MAX_PALABRAS
            continue
        presupuesto -= len(linea.split())
        lineas.append(linea)
        usados.add(f["id"])
    lineas += lineas_historial
    return "\n".join(f"{i}. {l}" for i, l in enumerate(lineas, 1))


_RE_PIDE_HISTORIAL = re.compile(r"historial|fallas? anteriores|ordenes|\bot\b|intervenciones")


def _ordenes_pertinentes(consulta: str, historial: dict | None, maximo: int = 3) -> list[dict]:
    """Órdenes de SAP PM que vienen al caso: todas (hasta `maximo`) si se pide el
    historial; si no, solo las que comparten términos con la consulta."""
    ordenes = (historial or {}).get("ordenes", [])
    if _RE_PIDE_HISTORIAL.search(sin_tildes(consulta.lower())):
        return ordenes[:maximo]
    q = set(tokenizar(consulta))
    return [o for o in ordenes if q & set(tokenizar(f"{o['sintoma']} {o['causa']}"))][:maximo]


def generar_con_llm(llm, agente: str, pregunta: str, fragmentos: list[dict], historial: dict | None, memoria_txt: str) -> str:
    sistema = PROMPT_MANTENIMIENTO if agente in ("mantenimiento", "mixta") else PROMPT_PROCEDIMIENTOS
    bloques = [formatear_fragmento(f) for f in fragmentos]
    for o in (historial or {}).get("ordenes", []):
        bloques.append(f"<fragmento cita=\"{cita_ot(o)}\" idioma=\"es\">\n{o['fecha']}: {o['sintoma']}. "
                       f"Causa: {o['causa']}. Acción: {o['accion']}.\n</fragmento>")
    usuario = PLANTILLA_USUARIO.format(contexto="\n".join(bloques), historial=memoria_txt, pregunta=pregunta)
    return llm.chat(sistema, usuario)


def mensaje_abstencion(intencion: str) -> str:
    return f"{config.FRASE_ABSTENCION}. {ESCALAR_A.get(intencion, ESCALAR_A['fuera_de_alcance'])}"


NOTA_SUPERVISOR = ("Nota: el asistente no autoriza intervenciones. El supervisor de turno debe verificar "
                   "el bloqueo y autorizar el inicio del trabajo.")


# ---------------------------------------------------------------------------
# Agente de Reportes
# ---------------------------------------------------------------------------
def borrador_reporte(pregunta: str, equipo: str | None, llm=None) -> dict:
    tipo = "cuasi_accidente" if re.search(r"cuasi|casi|evit", sin_tildes(pregunta.lower())) else "incidente"
    base = {"equipo": equipo or "POR COMPLETAR", "ubicacion": "POR COMPLETAR",
            "descripcion": pregunta.strip(), "tipo": tipo, "medidas_inmediatas": "POR COMPLETAR"}
    if llm is not None:
        propuesta = llm.chat_json(PROMPT_REPORTES, pregunta)
        # Solo se aceptan los campos del esquema; el resto se descarta.
        for campo in base:
            if isinstance(propuesta.get(campo), str) and propuesta[campo].strip() and base[campo] == "POR COMPLETAR":
                base[campo] = propuesta[campo].strip()
    return base
