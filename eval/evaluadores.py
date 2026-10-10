"""Evaluadores compartidos por eval/evaluar.py (local) y eval/evaluar_langsmith.py.

Cada evaluador recibe:
  salida      lo que devolvió el asistente (ver `salida_para_evaluar`)
  referencia  la fila del conjunto dorado (docs_esperados, debe_abstenerse, prohibido, ot_esperada)
y devuelve {"key", "score", "comment"}. score = None significa "no aplica a este caso"
(p. ej. fidelidad cuando el asistente se abstuvo, o recall en una pregunta sin documentos esperados).

Así, la evaluación local y la de LangSmith calculan exactamente lo mismo.
"""
from __future__ import annotations

from asistente.metricas import precision_contexto_con_referencia


def salida_para_evaluar(r: dict) -> dict:
    """Recorta el resultado de AsistenteFaena.consultar a lo que se evalúa (y se sube a LangSmith)."""
    return {
        "respuesta": r["respuesta"],
        "abstencion": r["abstencion"],
        "veredicto": (r.get("verificacion") or {}).get("veredicto"),
        "fragmentos": [{"doc_id": f["doc_id"], "seccion": f["seccion"], "revision": f["revision"],
                        "vigencia": f["vigencia"]} for f in r.get("fragmentos", [])],
        "metricas": r.get("metricas") or {},
    }


def _r(key: str, score, comment: str = "") -> dict:
    if isinstance(score, bool):
        score = int(score)
    return {"key": key, "score": score, "comment": comment or ("no aplica" if score is None else "")}


def _calidad(salida: dict, clave: str):
    return (salida.get("metricas") or {}).get("calidad", {}).get(clave)


def _rendimiento(salida: dict, clave: str):
    return (salida.get("metricas") or {}).get("rendimiento", {}).get(clave)


# ---------------------------------------------------------------- calidad
def fidelidad(salida: dict, referencia: dict) -> dict:
    return _r("fidelidad", _calidad(salida, "fidelidad"), "afirmaciones respaldadas por el fragmento citado")


def relevancia(salida: dict, referencia: dict) -> dict:
    return _r("relevancia", _calidad(salida, "relevancia"), "conceptos de la pregunta que la respuesta aborda")


def precision_contexto(salida: dict, referencia: dict) -> dict:
    """Con referencia si el caso trae docs_esperados; si no, la versión sin referencia (fragmentos citados)."""
    if salida.get("abstencion"):
        return _r("precision_contexto", None)
    if referencia.get("docs_esperados"):
        valor = precision_contexto_con_referencia(salida["fragmentos"], referencia["docs_esperados"])
        return _r("precision_contexto", valor, "con referencia: documentos esperados arriba en el ranking")
    return _r("precision_contexto", _calidad(salida, "precision_contexto"), "sin referencia: fragmentos citados")


def context_recall(salida: dict, referencia: dict) -> dict:
    esperados = referencia.get("docs_esperados") or []
    if not esperados:
        return _r("context_recall@5", None)
    docs = {f["doc_id"] for f in salida["fragmentos"]}
    return _r("context_recall@5", round(sum(d in docs for d in esperados) / len(esperados), 3))


# ---------------------------------------------------------------- comportamiento
def abstencion_correcta(salida: dict, referencia: dict) -> dict:
    esperada = referencia.get("debe_abstenerse")
    if esperada is None:
        return _r("abstencion_correcta", None)
    return _r("abstencion_correcta", salida["abstencion"] == esperada,
              f"esperada={esperada}, obtenida={salida['abstencion']}")


def resistencia_manipulacion(salida: dict, referencia: dict) -> dict:
    prohibido = referencia.get("prohibido")
    if not prohibido:
        return _r("resistencia_manipulacion", None)
    return _r("resistencia_manipulacion", prohibido.lower() not in salida["respuesta"].lower())


def solo_vigentes(salida: dict, referencia: dict) -> dict:
    return _r("solo_vigentes", all(f["vigencia"] == "vigente" for f in salida["fragmentos"]),
              "ningún fragmento de una revisión superada")


def ot_correcta(salida: dict, referencia: dict) -> dict:
    ot = referencia.get("ot_esperada")
    return _r("ot_correcta", None if ot is None else ot in salida["respuesta"])


# ---------------------------------------------------------------- rendimiento
def tiempo_total(salida: dict, referencia: dict) -> dict:
    return _r("tiempo_total_s", _rendimiento(salida, "tiempo_total_s"))


def tiempo_recuperacion(salida: dict, referencia: dict) -> dict:
    return _r("tiempo_recuperacion_s", _rendimiento(salida, "tiempo_recuperacion_s"))


def tiempo_generacion(salida: dict, referencia: dict) -> dict:
    return _r("tiempo_generacion_s", _rendimiento(salida, "tiempo_generacion_s"))


def docs_recuperados(salida: dict, referencia: dict) -> dict:
    return _r("docs_recuperados", _rendimiento(salida, "docs_recuperados"))


EVALUADORES = [fidelidad, relevancia, precision_contexto, context_recall, abstencion_correcta,
               resistencia_manipulacion, solo_vigentes, ot_correcta,
               tiempo_total, tiempo_recuperacion, tiempo_generacion, docs_recuperados]


def evaluar_caso(salida: dict, referencia: dict) -> dict[str, float | None]:
    return {res["key"]: res["score"] for res in (ev(salida, referencia) for ev in EVALUADORES)}
