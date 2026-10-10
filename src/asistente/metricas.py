"""Métricas de cada respuesta: rendimiento y calidad.

Se calculan en cada consulta (no solo en la evaluación), quedan en la traza
JSONL, se muestran en la interfaz y, si LangSmith está activo, se envían como
feedback de la traza para monitorearlas en el tiempo.

RENDIMIENTO (medido con time.perf_counter en cada nodo del grafo)
  tiempo_total_s         Desde que entra la pregunta hasta que sale la respuesta.
  tiempo_recuperacion_s  Nodo de recuperación: filtro de vigencia, BM25, búsqueda
                         densa, fusión RRF, reranking y consulta a SAP PM.
  tiempo_generacion_s    Suma de los intentos de generación (LLM o extractivo).
  tiempo_verificacion_s  Suma de las pasadas del verificador.
  docs_recuperados       Fragmentos que llegan al generador (top-k tras el reranking).
  documentos_unicos      Documentos distintos entre esos fragmentos.

CALIDAD (inspiradas en RAGAS, Es et al., 2024; deterministas, sin LLM juez)
  fidelidad              Fracción de afirmaciones de la respuesta respaldadas por el
                         fragmento que citan. La calcula el Verificador.
  relevancia             Fracción de los conceptos núcleo de la pregunta que la
                         respuesta aborda (con el glosario español-inglés).
  precision_contexto     ¿Los fragmentos útiles quedaron arriba en el ranking?
                         Promedio de precision@k en las posiciones útiles (fórmula
                         de RAGAS). Un fragmento es útil si la respuesta lo cita.

Las métricas de calidad solo se calculan sobre respuestas entregadas: si el
asistente se abstiene, valen None ("no aplica"), igual que en eval/evaluar.py.
"""
from __future__ import annotations

import re

from .agentes import grupos_nucleo
from .config import FRASE_ABSTENCION
from .prompts import cita
from .texto import raiz, tokenizar

_RE_CITA = re.compile(r"\[[^\[\]]*\|[^\[\]]*\]")
_PREFIJOS_SISTEMA = ("nota:", "atención:", "atencion:")
# Verbos auxiliares que grupos_nucleo deja pasar y que no son el tema de la pregunta
# ("¿cuáles SON los pasos…?", "¿qué accidentes HAN ocurrido…?").
_AUXILIARES = {raiz(p) for p in "son es ha han fue fueron ser esta estan tiene tienen dicen".split()}


def _suma(tiempos: dict, prefijo: str) -> float | None:
    valores = [v for k, v in tiempos.items() if k == prefijo or k.startswith(prefijo + "_")]
    return round(sum(valores), 3) if valores else None


def cuerpo_respuesta(respuesta: str) -> str:
    """La respuesta sin citas, avisos del sistema, nota del supervisor ni frase de abstención."""
    lineas = []
    for linea in respuesta.splitlines():
        l = linea.strip()
        if not l or l.lower().startswith(_PREFIJOS_SISTEMA) or FRASE_ABSTENCION in l:
            continue
        lineas.append(_RE_CITA.sub("", l))
    return "\n".join(lineas)


# ---------------------------------------------------------------- calidad
def relevancia(pregunta: str, respuesta: str, embedder=None) -> float | None:
    """¿La respuesta aborda lo que se preguntó? (0..1)

    Léxica: fracción de conceptos núcleo de la pregunta presentes en la respuesta.
    Usa los mismos grupos que la respondibilidad (agentes.grupos_nucleo), que ya
    incluyen el equivalente en inglés: si la respuesta cita el manual OEM,
    "radiador" se reconoce como "radiator".

    Semántica (solo con bge-m3): si la cobertura léxica no es completa, se toma el
    máximo con la similitud coseno entre pregunta y respuesta, para reconocer
    paráfrasis ("capacitación" / "instrucción"). Es el mismo criterio del Verificador.
    En modo offline la métrica es solo léxica y tiende a subestimar.

    None si la pregunta no tiene conceptos núcleo (p. ej. un saludo).
    """
    grupos = [g for g in grupos_nucleo(pregunta) if not g <= _AUXILIARES]
    cuerpo = cuerpo_respuesta(respuesta)
    if not grupos or not cuerpo.strip():
        return None
    tokens = set(tokenizar(cuerpo))
    lexica = sum(bool(g & tokens) for g in grupos) / len(grupos)
    if lexica < 1.0 and embedder is not None and getattr(embedder, "nombre", "").startswith("ollama"):
        try:
            v = embedder.embed([pregunta, cuerpo])
            return round(max(lexica, float(v[0] @ v[1])), 3)
        except Exception:   # si Ollama no responde, queda la medida léxica
            pass
    return round(lexica, 3)


def precision_promedio(utiles: list[bool]) -> float | None:
    """Context precision de RAGAS: sum_k(precision@k * v_k) / número de útiles.

    Premia que los fragmentos útiles estén arriba: útiles en las posiciones 1 y 2
    de 5 da 1,0; en las posiciones 4 y 5 da 0,325.
    """
    if not utiles:
        return None
    total = sum(utiles)
    if total == 0:
        return 0.0
    aciertos, acumulado = 0, 0.0
    for k, util in enumerate(utiles, start=1):
        if util:
            aciertos += 1
            acumulado += aciertos / k
    return round(acumulado / total, 3)


def precision_contexto(fragmentos: list[dict], respuesta: str) -> float | None:
    """Sin referencia (en producción): un fragmento es útil si la respuesta cita su sección.

    None si no se recuperaron fragmentos, o si la respuesta se apoya solo en el
    dato exacto de SAP PM y no en documentos (no hay ranking que evaluar).
    """
    if not fragmentos:
        return None
    utiles = [cita(f) in respuesta for f in fragmentos]
    if not any(utiles) and "[SAP-PM" in respuesta:
        return None
    return precision_promedio(utiles)


def precision_contexto_con_referencia(fragmentos: list[dict], docs_esperados: list[str]) -> float | None:
    """Con referencia (en la evaluación): un fragmento es útil si su documento es uno de los esperados."""
    if not fragmentos or not docs_esperados:
        return None
    return precision_promedio([f["doc_id"] in docs_esperados for f in fragmentos])


# ---------------------------------------------------------------- todo junto
def calcular(*, pregunta: str, respuesta: str, tiempos: dict, fragmentos: list[dict] | None,
             verificacion: dict | None, abstencion: bool, embedder=None) -> dict:
    fragmentos = fragmentos or []
    rendimiento = {
        "tiempo_total_s": tiempos.get("total"),
        "tiempo_recuperacion_s": _suma(tiempos, "recuperacion"),
        "tiempo_generacion_s": _suma(tiempos, "generacion"),
        "tiempo_verificacion_s": _suma(tiempos, "verificacion"),
        "docs_recuperados": len(fragmentos),
        "documentos_unicos": len({f["doc_id"] for f in fragmentos}),
    }
    entregada = not abstencion and verificacion is not None
    calidad = {
        "fidelidad": verificacion.get("fidelidad") if entregada else None,
        "relevancia": relevancia(pregunta, respuesta, embedder) if entregada else None,
        "precision_contexto": precision_contexto(fragmentos, respuesta) if entregada else None,
    }
    return {"rendimiento": rendimiento, "calidad": calidad}


NOMBRES = {
    "tiempo_total_s": "Tiempo total (s)",
    "tiempo_recuperacion_s": "Recuperación (s)",
    "tiempo_generacion_s": "Generación (s)",
    "tiempo_verificacion_s": "Verificación (s)",
    "docs_recuperados": "Docs recuperados",
    "documentos_unicos": "Documentos únicos",
    "fidelidad": "Fidelidad",
    "relevancia": "Relevancia",
    "precision_contexto": "Precisión del contexto",
}
