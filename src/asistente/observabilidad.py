"""Observabilidad, monitoreo y evaluación con LangSmith (opcional).

Se activa solo si están las dos variables:
    LANGSMITH_TRACING=true
    LANGSMITH_API_KEY=lsv2_...
Opcionales:
    LANGSMITH_PROJECT=asistente-faena      (proyecto donde quedan las trazas)
    LANGSMITH_ENDPOINT=https://...         (instancia autoalojada o región EU)

Si falta cualquiera de las dos, o el paquete langsmith no está instalado, el
asistente funciona igual y NO sale ningún dato del equipo (RNF-02): las trazas
siguen quedando en trazas/*.jsonl como antes.

Qué se envía cuando está activo
  - Una traza por consulta con el grafo de LangGraph completo (cada nodo con su
    latencia), la recuperación híbrida como paso "retriever" con sus documentos,
    las llamadas al LLM y a SAP PM, y el veredicto del verificador.
  - Las métricas de la respuesta (rendimiento y calidad) como feedback de la
    traza, para graficarlas en el monitoreo del proyecto.

Qué NO se envía
  - El token SSO del usuario ni la memoria de la sesión: no entran al estado del
    grafo (ver AsistenteFaena._sesiones) y se filtran de las entradas de cada paso.
  - El nombre de usuario: solo su seudónimo, igual que en las trazas locales.
"""
from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Callable
from typing import Any

from asistente import config  # noqa: F401  (carga el .env si existe)

try:  # langsmith es opcional: sin él, los decoradores no hacen nada
    import langsmith as _ls
    _SDK = True
except ImportError:  # pragma: no cover - depende del entorno
    _ls = None
    _SDK = False

log = logging.getLogger(__name__)
_cliente = None
_hilos: list[threading.Thread] = []

# Métricas que se envían como feedback (clave en LangSmith -> sección en metricas.calcular)
CLAVES_FEEDBACK = {
    "fidelidad": "calidad", "relevancia": "calidad", "precision_contexto": "calidad",
    "tiempo_total_s": "rendimiento", "tiempo_recuperacion_s": "rendimiento",
    "tiempo_generacion_s": "rendimiento", "docs_recuperados": "rendimiento",
}


def sdk_instalado() -> bool:
    return _SDK


def activa() -> bool:
    """True solo si hay SDK, LANGSMITH_TRACING=true y una API key."""
    bandera = os.getenv("LANGSMITH_TRACING") or os.getenv("LANGCHAIN_TRACING_V2") or ""
    clave = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")
    return _SDK and bandera.strip().lower() == "true" and bool(clave)


def proyecto() -> str:
    return os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT") or "default"


def estado() -> str:
    if not _SDK:
        return "desactivado (paquete langsmith no instalado)"
    if not activa():
        return "desactivado (faltan LANGSMITH_TRACING=true y LANGSMITH_API_KEY)"
    return f"activo · proyecto '{proyecto()}'"


# ---------------------------------------------------------------- trazas
def trazar(nombre: str, tipo: str = "chain", entradas: Callable[[dict], dict] | None = None,
           salidas: Callable[[Any], dict] | None = None, metadata: dict | None = None):
    """Decorador: registra el paso en LangSmith cuando está activo; si no, no hace nada.

    `entradas` y `salidas` deciden qué se envía (p. ej. quitar el token).
    """
    def deco(fn):
        if not _SDK:
            return fn
        return _ls.traceable(run_type=tipo, name=nombre, process_inputs=entradas,
                             process_outputs=salidas, metadata=metadata)(fn)
    return deco


def contexto_traza():
    """Activa o apaga el envío según activa(), aunque las variables de LangSmith digan otra cosa.

    Así, con LANGSMITH_TRACING=true pero sin API key, no se intenta enviar nada.
    """
    if not _SDK:
        return contextlib.nullcontext()
    return _ls.tracing_context(enabled=activa(), project_name=proyecto())


def ejecutar(fn: Callable, *args, run_id=None, metadata: dict | None = None,
             tags: list[str] | None = None, **kwargs):
    """Llama a una función trazada fijando el id de la traza (para luego adjuntarle feedback)."""
    if _SDK and activa():
        extra = {"run_id": run_id, "metadata": metadata or {}, "tags": tags or []}
        return fn(*args, langsmith_extra=extra, **kwargs)
    return fn(*args, **kwargs)


# ---------------------------------------------------------------- feedback
def feedback_de_metricas(metricas: dict, abstencion: bool) -> list[tuple[str, float]]:
    """Convierte las métricas de una respuesta en pares (clave, puntaje) para LangSmith."""
    pares = [(clave, float(v)) for clave, seccion in CLAVES_FEEDBACK.items()
             if (v := metricas.get(seccion, {}).get(clave)) is not None]
    pares.append(("abstencion", 1.0 if abstencion else 0.0))
    return pares


def _cliente_ls():
    global _cliente
    if _cliente is None:
        _cliente = _ls.Client()
    return _cliente


def enviar_feedback(run_id, metricas: dict, abstencion: bool) -> None:
    """Adjunta las métricas a la traza, en segundo plano para no sumar latencia a la respuesta."""
    if not (activa() and run_id):
        return

    def _enviar():
        try:
            cliente = _cliente_ls()
            for clave, puntaje in feedback_de_metricas(metricas, abstencion):
                cliente.create_feedback(run_id, key=clave, score=puntaje,
                                        comment="métrica calculada por el asistente")
        except Exception as err:  # la observabilidad nunca debe botar una consulta
            log.warning("No se pudo enviar el feedback a LangSmith: %s", err)

    hilo = threading.Thread(target=_enviar, daemon=True)
    hilo.start()
    _hilos.append(hilo)


def esperar(timeout: float = 10.0) -> None:
    """Espera a que se envíe todo antes de cerrar un script (CLI, evaluación)."""
    for hilo in list(_hilos):
        hilo.join(timeout)
    _hilos.clear()
    if activa():
        with contextlib.suppress(Exception):
            _cliente_ls().flush()


# ---------------------------------------------------------------- filtros de entradas y salidas
def _crudo(salida):
    """Acepta la salida tal cual o envuelta como {"output": ...}."""
    if isinstance(salida, dict) and set(salida) == {"output"}:
        return salida["output"]
    return salida


def entradas_consulta(entradas: dict) -> dict:
    return {"pregunta": entradas.get("pregunta")}


def salidas_consulta(r: dict) -> dict:
    r = _crudo(r)
    return {k: r.get(k) for k in ("respuesta", "abstencion", "motivo_abstencion", "intencion",
                                  "equipo", "generador", "citas", "metricas")} | {
        "veredicto": (r.get("verificacion") or {}).get("veredicto")}


def entradas_recuperacion(entradas: dict) -> dict:
    tipos = entradas.get("tipos")
    return {"consulta": entradas.get("consulta"), "tipos": sorted(tipos) if tipos else None,
            "equipo_tipo": entradas.get("equipo_tipo"), "top_k": entradas.get("top_k")}


def salidas_recuperacion(res) -> dict:
    """Formato de documentos que LangSmith muestra en los pasos de tipo retriever."""
    res = _crudo(res)
    return {
        "documents": [{"page_content": f["texto"], "type": "Document",
                       "metadata": {k: f.get(k) for k in ("id", "doc_id", "seccion", "revision",
                                                           "vigencia", "idioma", "puntaje")}}
                      for f in res.fragmentos],
        "mejor_puntaje": res.mejor_puntaje,
        "candidatos": res.candidatos,
    }


def entradas_historial(entradas: dict) -> dict:
    return {"equipo": entradas.get("equipo"), "meses": entradas.get("meses")}   # sin token


def entradas_llm(entradas: dict) -> dict:
    return {"messages": [{"role": "system", "content": entradas.get("sistema")},
                         {"role": "user", "content": entradas.get("usuario")}],
            "formato_json": entradas.get("formato_json", False)}


def salidas_llm(texto: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": _crudo(texto)}}]}


def entradas_verificador(entradas: dict) -> dict:
    return {"respuesta": entradas.get("respuesta"), "fragmentos": len(entradas.get("fragmentos") or [])}
