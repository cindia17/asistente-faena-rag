"""Chat por terminal.

Uso:
    python -m asistente.cli                      # mecánico del área mina
    python -m asistente.cli --token tok-contratista
    python -m asistente.cli --pregunta "El camión 14 marca alta temperatura, ¿qué reviso primero?"
"""
from __future__ import annotations

import argparse
import sys

from .contexto import MemoriaSesion
from .grafo import AsistenteFaena
from .herramientas import registrar_reporte


def mostrar(r: dict, detalle: bool) -> None:
    print("\n" + r["respuesta"])
    if detalle and r.get("id"):
        print(f"\n  intención={r['intencion']} | equipo={r['equipo']} | generador={r['generador']} "
              f"| verificación={(r.get('verificacion') or {}).get('veredicto')} | abstención={r['abstencion']} "
              f"| {r['tiempos']['total']} s")
        for d in r["recuperacion"]:
            print(f"    - {d['id']:<35} puntaje={d['puntaje']}")
    print()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Asistente de Faena (prototipo)")
    p.add_argument("--token", default="tok-mecanico-mina")
    p.add_argument("--pregunta")
    p.add_argument("--detalle", action="store_true", help="muestra fragmentos, puntajes y tiempos")
    a = p.parse_args()

    asistente = AsistenteFaena()
    print(f"Asistente de Faena | embeddings: {asistente.embedder.nombre} | "
          f"generador: {'llm:' + asistente.llm.modelo if asistente.llm else 'extractivo (offline)'}")
    memoria = MemoriaSesion()
    if a.pregunta:
        mostrar(asistente.consultar(a.pregunta, a.token, memoria), a.detalle)
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


if __name__ == "__main__":
    main()
