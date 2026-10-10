"""Chat por terminal.

Uso:
    python -m asistente.cli                      # mecánico del área mina
    python -m asistente.cli --token tok-contratista
    python -m asistente.cli --pregunta "El camión 14 marca alta temperatura, ¿qué reviso primero?"
"""
from __future__ import annotations

import argparse
import sys

from . import observabilidad
from .contexto import MemoriaSesion
from .grafo import AsistenteFaena
from .herramientas import registrar_reporte


def _v(v, fmt: str = "{:.3f}") -> str:
    return "—" if v is None else fmt.format(v)


def mostrar_metricas(r: dict) -> None:
    m = r.get("metricas")
    if not m:
        return
    rend, cal = m["rendimiento"], m["calidad"]
    print(f"  rendimiento: total={_v(rend['tiempo_total_s'])} s | recuperación={_v(rend['tiempo_recuperacion_s'])} s"
          f" | generación={_v(rend['tiempo_generacion_s'])} s | docs recuperados={rend['docs_recuperados']}"
          f" ({rend['documentos_unicos']} documentos)")
    nota = "  (no aplica: se abstuvo)" if r["abstencion"] else ""
    print(f"  calidad:     fidelidad={_v(cal['fidelidad'])} | relevancia={_v(cal['relevancia'])}"
          f" | precisión del contexto={_v(cal['precision_contexto'])}{nota}")
    if r.get("traza_langsmith"):
        print(f"  LangSmith:   traza {r['traza_langsmith']} (proyecto {observabilidad.proyecto()})")


def mostrar(r: dict, detalle: bool) -> None:
    print("\n" + r["respuesta"])
    if detalle and r.get("id"):
        print(f"\n  intención={r['intencion']} | equipo={r['equipo']} | generador={r['generador']} "
              f"| verificación={(r.get('verificacion') or {}).get('veredicto')} | abstención={r['abstencion']} "
              f"| {r['tiempos']['total']} s")
        for d in r["recuperacion"]:
            print(f"    - {d['id']:<35} puntaje={d['puntaje']}")
        mostrar_metricas(r)
    print()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Asistente de Faena (prototipo)")
    p.add_argument("--token", default="tok-mecanico-mina")
    p.add_argument("--pregunta")
    p.add_argument("--detalle", action="store_true", help="muestra fragmentos, puntajes, tiempos y métricas")
    a = p.parse_args()

    asistente = AsistenteFaena()
    print(f"Asistente de Faena | embeddings: {asistente.embedder.nombre} | "
          f"generador: {'llm:' + asistente.llm.modelo if asistente.llm else 'extractivo (offline)'} | "
          f"LangSmith: {observabilidad.estado()}")
    memoria = MemoriaSesion()
    if a.pregunta:
        mostrar(asistente.consultar(a.pregunta, a.token, memoria), a.detalle)
        observabilidad.esperar()
        return
    print("Escribe tu consulta (o 'salir').")
    while (pregunta := input("> ").strip()).lower() not in ("salir", "exit", "q"):
        if not pregunta:
            continue
        r = asistente.consultar(pregunta, a.token, memoria)
        mostrar(r, a.detalle)
        if r.get("reporte_borrador"):
            print(r["reporte_borrador"])
            if input("¿Confirmas el registro? (s/n) ").strip().lower() == "s":
                print(registrar_reporte(a.token, r["reporte_borrador"], confirmado=True))
    observabilidad.esperar()


if __name__ == "__main__":
    main()
