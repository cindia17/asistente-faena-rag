"""Evaluación del asistente en LangSmith: experimentos sobre el conjunto dorado.

Qué hace
  1. Sube el conjunto dorado (eval/golden_set.jsonl) como dataset de LangSmith, una sola vez.
  2. Ejecuta el asistente sobre cada pregunta y la califica con los mismos evaluadores
     que la evaluación local (eval/evaluadores.py): fidelidad, relevancia, precisión del
     contexto, context recall, abstención correcta, resistencia a manipulación, solo
     vigentes, y los tiempos de recuperación, generación y total.
  3. Cada corrida queda como un "experimento" que se puede comparar con otros en la
     interfaz de LangSmith (offline vs LLM, distintos umbrales o modelos).

Uso (PowerShell)
    $env:LANGSMITH_API_KEY = "lsv2_..."
    $env:LANGSMITH_PROJECT = "asistente-faena"
    python eval/evaluar_langsmith.py --offline          # generador extractivo
    python eval/evaluar_langsmith.py                    # con el LLM local (Ollama)
    python eval/evaluar_langsmith.py --solo-dataset     # solo sube el dataset
    python eval/evaluar_langsmith.py --reemplazar       # vuelve a subir el dataset

Sin cuenta de LangSmith
    python eval/evaluar_langsmith.py --local --offline  # mismos evaluadores, no envía nada;
                                                        # deja la tabla en evidencias/
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from asistente import config, observabilidad  # noqa: E402
from asistente.contexto import MemoriaSesion  # noqa: E402
from evaluadores import EVALUADORES, evaluar_caso, salida_para_evaluar  # noqa: E402

DATASET = os.getenv("LANGSMITH_DATASET", "asistente-faena-golden")
TOKEN = "tok-mecanico-mina"
CAMPOS_REFERENCIA = ("docs_esperados", "debe_abstenerse", "prohibido", "ot_esperada")
COLUMNAS = [("tiempo_total_s", "Total s"), ("tiempo_recuperacion_s", "Recup. s"),
            ("tiempo_generacion_s", "Gener. s"), ("docs_recuperados", "Docs"),
            ("fidelidad", "Fidel."), ("relevancia", "Relev."), ("precision_contexto", "Prec.ctx")]


def cargar_set() -> list[dict]:
    texto = (RAIZ / "eval" / "golden_set.jsonl").read_text(encoding="utf-8")
    return [json.loads(l) for l in texto.splitlines() if l.strip()]


def crear_asistente(offline: bool):
    from asistente.grafo import AsistenteFaena
    a = AsistenteFaena(llm=None) if offline else AsistenteFaena()
    a.enviar_feedback = False    # en un experimento, los puntajes los ponen los evaluadores
    return a


def nombre_generador(asistente) -> str:
    return f"llm:{asistente.llm.modelo}" if asistente.llm else "extractivo"


# ---------------------------------------------------------------- LangSmith
def subir_dataset(cliente, reemplazar: bool) -> None:
    if cliente.has_dataset(dataset_name=DATASET):
        if not reemplazar:
            print(f"Dataset '{DATASET}' ya existe en LangSmith: se reutiliza.")
            return
        cliente.delete_dataset(dataset_name=DATASET)
    ds = cliente.create_dataset(DATASET, description="Conjunto dorado del Asistente de Faena (Chuquicamata, simulado)")
    casos = cargar_set()
    for caso in casos:
        cliente.create_example(inputs={"pregunta": caso["pregunta"]},
                               outputs={k: caso.get(k) for k in CAMPOS_REFERENCIA},
                               metadata={"id": caso["id"], "tipo": caso["tipo"]}, dataset_id=ds.id)
    print(f"Dataset '{DATASET}' creado con {len(casos)} ejemplos.")


def como_evaluador_langsmith(fn):
    """Adapta un evaluador local (salida, referencia) a la firma (run, example) de LangSmith."""
    def evaluador(run, example):
        return fn(run.outputs or {}, example.outputs or {})
    evaluador.__name__ = fn.__name__
    return evaluador


def resumen_experimento(resultados) -> dict[str, float]:
    """Promedio de cada evaluador en el experimento (ignora los 'no aplica')."""
    puntajes: dict[str, list[float]] = {}
    for fila in resultados:
        for res in fila["evaluation_results"]["results"]:
            if res.score is not None:
                puntajes.setdefault(res.key, []).append(float(res.score))
    return {k: round(statistics.mean(v), 3) for k, v in puntajes.items()}


def evaluar_en_langsmith(offline: bool, reemplazar: bool, solo_dataset: bool) -> int:
    if not observabilidad.sdk_instalado():
        print("Falta el paquete langsmith: pip install -r requirements.txt")
        return 2
    if not (os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")):
        print("Falta LANGSMITH_API_KEY. Créala en https://smith.langchain.com (Settings > API Keys).\n"
              "Para evaluar sin cuenta: python eval/evaluar_langsmith.py --local --offline")
        return 2
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    import langsmith
    cliente = langsmith.Client()
    subir_dataset(cliente, reemplazar)
    if solo_dataset:
        return 0

    asistente = crear_asistente(offline)
    generador = nombre_generador(asistente)

    def objetivo(inputs: dict) -> dict:
        r = asistente.consultar(inputs["pregunta"], TOKEN, MemoriaSesion())
        return salida_para_evaluar(r)

    evaluate = getattr(langsmith, "evaluate", None)
    if evaluate is None:  # versiones antiguas del SDK
        from langsmith.evaluation import evaluate
    print(f"Evaluando en LangSmith | dataset={DATASET} | embeddings={asistente.embedder.nombre} | generador={generador}")
    resultados = evaluate(
        objetivo, data=DATASET,
        evaluators=[como_evaluador_langsmith(fn) for fn in EVALUADORES],
        experiment_prefix=f"asistente-faena-{'offline' if generador == 'extractivo' else 'llm'}",
        description="Métricas de rendimiento y calidad sobre el conjunto dorado",
        metadata={"embedder": asistente.embedder.nombre, "generador": generador,
                  "umbral_abstencion": config.UMBRAL_ABSTENCION, "top_k": config.TOP_K},
        max_concurrency=1,
    )
    print(f"\nExperimento: {getattr(resultados, 'experiment_name', '(ver en LangSmith)')}")
    try:
        for clave, valor in resumen_experimento(resultados).items():
            print(f"  {clave:<26} {valor}")
    except Exception as err:  # el resumen es opcional; el experimento ya quedó en LangSmith
        print(f"  (resumen no disponible: {err})")
    observabilidad.esperar()
    return 0


# ---------------------------------------------------------------- local
def fmt(v) -> str:
    return "—" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def evaluar_local(offline: bool) -> int:
    os.environ["LANGSMITH_TRACING"] = "false"      # garantiza que no se envíe nada
    asistente = crear_asistente(offline)
    generador = nombre_generador(asistente)
    print(f"Evaluación local (no se envía nada) | embeddings={asistente.embedder.nombre} | generador={generador}\n")
    filas = []
    for caso in cargar_set():
        r = asistente.consultar(caso["pregunta"], TOKEN, MemoriaSesion())
        filas.append({"id": caso["id"], "tipo": caso["tipo"], "pregunta": caso["pregunta"],
                      "abstencion": r["abstencion"], **evaluar_caso(salida_para_evaluar(r), caso)})

    encabezado = f"{'ID':<4} {'Tipo':<13} {'Abst.':<5} " + " ".join(f"{t:>8}" for _, t in COLUMNAS)
    lineas = [encabezado, "-" * len(encabezado)]
    for f in filas:
        lineas.append(f"{f['id']:<4} {f['tipo']:<13} {'sí' if f['abstencion'] else 'no':<5} "
                      + " ".join(f"{fmt(f[k]):>8}" for k, _ in COLUMNAS))
    promedios = {}
    for clave in [k for k in filas[0] if k not in ("id", "tipo", "pregunta", "abstencion")]:
        valores = [f[clave] for f in filas if f[clave] is not None]
        promedios[clave] = round(float(statistics.mean(valores)), 3) if valores else None
    lineas += ["-" * len(encabezado),
               f"{'Promedio':<24}" + " ".join(f"{fmt(promedios[k]):>8}" for k, _ in COLUMNAS), "",
               "Promedio de cada evaluador (los 'no aplica' no cuentan):"]
    lineas += [f"  {k:<26} {fmt(v)}" for k, v in promedios.items()]
    texto = "\n".join(lineas)
    print(texto)

    salida = RAIZ / "evidencias"
    salida.mkdir(exist_ok=True)
    sufijo = "offline" if generador == "extractivo" else "llm"
    informe = {"fecha": datetime.now().isoformat(timespec="seconds"), "embedder": asistente.embedder.nombre,
               "generador": generador, "promedios": promedios, "casos": filas}
    (salida / f"metricas_por_respuesta_{sufijo}.json").write_text(
        json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
    (salida / f"metricas_por_respuesta_{sufijo}.txt").write_text(
        f"Embeddings: {asistente.embedder.nombre} | generador: {generador}\n\n{texto}\n", encoding="utf-8")
    print(f"\nGuardado en evidencias/metricas_por_respuesta_{sufijo}.txt y .json")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--offline", action="store_true", help="sin LLM: generador extractivo")
    p.add_argument("--local", action="store_true", help="no usa LangSmith; imprime y guarda la tabla")
    p.add_argument("--solo-dataset", action="store_true", help="solo sube el dataset a LangSmith")
    p.add_argument("--reemplazar", action="store_true", help="vuelve a crear el dataset en LangSmith")
    a = p.parse_args()
    if a.local:
        return evaluar_local(a.offline)
    return evaluar_en_langsmith(a.offline, a.reemplazar, a.solo_dataset)


if __name__ == "__main__":
    raise SystemExit(main())
