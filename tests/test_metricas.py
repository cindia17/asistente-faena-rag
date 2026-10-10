"""Pruebas de las métricas por respuesta y de la privacidad de la observabilidad."""
import os

from asistente import observabilidad
from asistente.contexto import MemoriaSesion
from asistente.metricas import (calcular, cuerpo_respuesta, precision_contexto, precision_contexto_con_referencia,
                                precision_promedio, relevancia)
from asistente.prompts import cita
from conftest import MECANICO

PREGUNTA_EJEMPLO = "El camión 14 marca alta temperatura. ¿Qué reviso primero?"
CLAVES_RENDIMIENTO = {"tiempo_total_s", "tiempo_recuperacion_s", "tiempo_generacion_s", "tiempo_verificacion_s",
                      "docs_recuperados", "documentos_unicos"}
CLAVES_CALIDAD = {"fidelidad", "relevancia", "precision_contexto"}


def _frag(doc, seccion):
    return {"doc_id": doc, "seccion": seccion, "revision": 1, "vigencia": "vigente", "texto": "x"}


# ---------------------------------------------------------------- Precisión del contexto (fórmula de RAGAS)
def test_precision_premia_utiles_arriba():
    assert precision_promedio([True, True, False, False, False]) == 1.0
    assert precision_promedio([True, False, True]) == round((1 / 1 + 2 / 3) / 2, 3)
    assert precision_promedio([False, False, False, True, True]) == 0.325
    assert precision_promedio([False, False]) == 0.0
    assert precision_promedio([]) is None


def test_precision_sin_referencia_usa_las_citas():
    frags = [_frag("A", "1"), _frag("B", "2"), _frag("C", "3")]
    respuesta = f"Paso uno. {cita(frags[0])}\nPaso dos. {cita(frags[2])}"
    assert precision_contexto(frags, respuesta) == round((1 + 2 / 3) / 2, 3)
    # Respuesta apoyada solo en el dato exacto de SAP PM: no hay ranking que evaluar.
    assert precision_contexto(frags, "Falló en julio. [SAP-PM | OT-1 | 2026-07-14 | dato operacional]") is None
    assert precision_contexto([], "lo que sea") is None


def test_precision_con_referencia_usa_docs_esperados():
    frags = [_frag("A", "1"), _frag("B", "2"), _frag("A", "3")]
    assert precision_contexto_con_referencia(frags, ["B"]) == 0.5
    assert precision_contexto_con_referencia(frags, []) is None


# ---------------------------------------------------------------- Relevancia
def test_relevancia_cubre_conceptos_de_la_pregunta():
    assert relevancia("¿Qué EPP uso para manipular refrigerante?",
                      "Usar guantes de nitrilo y EPP al manipular el refrigerante. [HDS | 8 | rev. 2 | vigente]") == 1.0
    assert relevancia("¿Qué EPP uso para manipular refrigerante?", "El turno parte a las ocho.") == 0.0
    assert relevancia("¿Qué hago primero?", "Revisar el radiador.") is None    # sin conceptos núcleo


def test_relevancia_reconoce_el_manual_en_ingles():
    r = relevancia("¿Cómo reviso el radiador?", '"Check the radiator fins for dust." [MAN | 4 | rev. 7 | vigente]')
    assert r == 1.0


def test_relevancia_ignora_verbos_auxiliares():
    assert relevancia("¿Cuáles son los pasos de bloqueo?", "Aplicar el bloqueo con candado personal.") == 1.0


def test_cuerpo_respuesta_quita_citas_y_notas():
    texto = "Revisar la correa. [PETS | 4 | rev. 5 | vigente]\nNota: el asistente no autoriza intervenciones."
    assert cuerpo_respuesta(texto) == "Revisar la correa. "


# ---------------------------------------------------------------- Integración con el asistente
def test_cada_respuesta_trae_sus_metricas(asistente):
    r = asistente.consultar(PREGUNTA_EJEMPLO, MECANICO, MemoriaSesion())
    m = r["metricas"]
    assert set(m["rendimiento"]) == CLAVES_RENDIMIENTO and set(m["calidad"]) == CLAVES_CALIDAD
    rend, cal = m["rendimiento"], m["calidad"]
    assert rend["tiempo_total_s"] >= rend["tiempo_recuperacion_s"] >= 0
    assert rend["tiempo_generacion_s"] is not None and rend["tiempo_verificacion_s"] is not None
    assert 1 <= rend["docs_recuperados"] <= 5 and rend["docs_recuperados"] == len(r["fragmentos"])
    assert cal["fidelidad"] == r["verificacion"]["fidelidad"] == 1.0
    assert cal["relevancia"] >= 0.6
    assert 0 < cal["precision_contexto"] <= 1


def test_abstencion_no_tiene_metricas_de_calidad(asistente):
    r = asistente.consultar("¿Cuál es el sueldo de un mecánico?", MECANICO, MemoriaSesion())
    assert r["abstencion"]
    assert all(v is None for v in r["metricas"]["calidad"].values())
    assert r["metricas"]["rendimiento"]["tiempo_generacion_s"] is None      # no se llegó a generar
    assert r["metricas"]["rendimiento"]["tiempo_total_s"] is not None


def test_metricas_quedan_en_la_traza_local(asistente):
    from asistente import trazas
    r = asistente.consultar(PREGUNTA_EJEMPLO, MECANICO, MemoriaSesion())
    registrada = next(t for t in trazas.leer_trazas() if t["id"] == r["id"])
    assert registrada["metricas"] == r["metricas"]


def test_calcular_saludo_sin_recuperacion():
    m = calcular(pregunta="hola", respuesta="Hola", tiempos={"total": 0.01}, fragmentos=None,
                 verificacion=None, abstencion=False)
    assert m["rendimiento"]["docs_recuperados"] == 0 and m["calidad"]["fidelidad"] is None


# ---------------------------------------------------------------- Observabilidad (LangSmith)
def test_langsmith_apagado_por_defecto(asistente):
    for v in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        os.environ.pop(v, None)
    assert observabilidad.activa() is False
    r = asistente.consultar(PREGUNTA_EJEMPLO, MECANICO, MemoriaSesion())
    assert r["traza_langsmith"] is None


def test_sin_api_key_no_se_activa():
    anterior = {v: os.environ.get(v) for v in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGCHAIN_API_KEY")}
    try:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ.pop("LANGSMITH_API_KEY", None)
        os.environ.pop("LANGCHAIN_API_KEY", None)
        assert observabilidad.activa() is False
    finally:
        for v, valor in anterior.items():
            if valor is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = valor


def test_feedback_lleva_las_metricas_pedidas():
    metricas = {"rendimiento": {"tiempo_total_s": 4.9, "tiempo_recuperacion_s": 1.2, "tiempo_generacion_s": 3.1,
                                "docs_recuperados": 5},
                "calidad": {"fidelidad": 1.0, "relevancia": 0.8, "precision_contexto": None}}
    pares = dict(observabilidad.feedback_de_metricas(metricas, abstencion=False))
    assert pares == {"fidelidad": 1.0, "relevancia": 0.8, "tiempo_total_s": 4.9, "tiempo_recuperacion_s": 1.2,
                     "tiempo_generacion_s": 3.1, "docs_recuperados": 5.0, "abstencion": 0.0}


def test_el_token_no_entra_al_estado_del_grafo(asistente):
    """LangGraph envía el estado completo a LangSmith: el token SSO no puede estar ahí."""
    vistos = []
    original = asistente.grafo

    class Espia:
        def invoke(self, estado, *a, **k):
            vistos.append(dict(estado))
            final = original.invoke(estado, *a, **k)
            vistos.append(final)
            return final

    asistente.grafo = Espia()
    try:
        asistente.consultar(PREGUNTA_EJEMPLO, MECANICO, MemoriaSesion())
    finally:
        asistente.grafo = original
    entrada, final = vistos
    assert "token" not in entrada and "memoria" not in entrada
    assert MECANICO not in repr(entrada) and MECANICO not in repr(final)
    assert "mecanico.mina" not in repr(final)               # solo el seudónimo
    assert not asistente._sesiones                          # la sesión se libera al terminar
