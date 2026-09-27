"""Orquestador con LangGraph (capa 2 del diagrama; Figura 2 del informe).

    entrada -> reescritura -> clasificacion ─┬─> reporte ──────────────────────────────┐
                                             └─> recuperacion -> [umbral] ─┬─> abstencion ─┤
                                                                           └─> generacion   │
                                     generacion -> verificacion ─┬─> respuesta ────────────┤
                                                                 ├─> reintento (extractivo)│
                                                                 └─> abstencion ───────────┴─> registro -> FIN

El grafo explícito hace que el flujo sea auditable: cada nodo deja su huella en
la traza y los reintentos tienen un límite fijo.
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import config, trazas
from .agentes import (NOTA_SUPERVISOR, TIPOS_POR_AGENTE, borrador_reporte, clasificar_intencion,
                      evaluar_respondibilidad, generar_con_llm, generar_extractivo, mensaje_abstencion)
from .contexto import MemoriaSesion, reescribir_consulta
from .embeddings import crear_embedder
from .guardrails import revisar_entrada
from .herramientas import PermisoDenegado, autenticar, consultar_historial
from .ingesta import cargar_indice
from .llm import crear_llm
from .prompts import cita
from .recuperacion import RecuperadorHibrido
from .verificador import Verificador


class Estado(TypedDict, total=False):
    pregunta: str
    token: str
    memoria: MemoriaSesion
    usuario: dict
    pregunta_limpia: str
    intento_manipulacion: bool
    consulta: str
    equipo: str | None
    equipo_tipo: str | None
    hereda_contexto: bool
    intencion: str
    fragmentos: list[dict]
    detalle_recuperacion: list[dict]
    mejor_puntaje: float
    respondibilidad: dict
    historial: dict | None
    avisos: list[str]
    borrador: str
    generador: str
    intentos: int
    verificacion: dict
    respuesta: str
    abstencion: bool
    motivo_abstencion: str
    reporte_borrador: dict | None
    citas: list[str]
    tiempos: dict[str, float]


class AsistenteFaena:
    def __init__(self, llm: Any = "auto", embedder: Any = None):
        self.embedder = embedder or crear_embedder()
        self.llm = crear_llm() if llm == "auto" else llm
        fragmentos, matriz = cargar_indice(self.embedder)
        self.recuperador = RecuperadorHibrido(fragmentos, matriz, self.embedder)
        self.verificador = Verificador(self.embedder, self.llm)
        self.grafo = self._construir_grafo()

    # ---------------------------------------------------------------- nodos
    def _medir(self, estado: Estado, etapa: str, inicio: float) -> dict:
        tiempos = dict(estado.get("tiempos", {}))
        tiempos[etapa] = round(time.perf_counter() - inicio, 3)
        return tiempos

    def n_entrada(self, e: Estado) -> dict:
        t0 = time.perf_counter()
        usuario = autenticar(e["token"])               # SSO simulado: sin token válido no hay consulta
        g = revisar_entrada(e["pregunta"])
        avisos = []
        if g["intento_manipulacion"]:
            avisos.append("Atención: la consulta incluye instrucciones dirigidas al asistente; se ignoran y se responde solo con documentos.")
        return {"usuario": usuario, "pregunta_limpia": g["pregunta_limpia"],
                "intento_manipulacion": g["intento_manipulacion"], "avisos": avisos,
                "intentos": 0, "tiempos": self._medir(e, "entrada", t0)}

    def n_reescritura(self, e: Estado) -> dict:
        t0 = time.perf_counter()
        r = reescribir_consulta(e["pregunta_limpia"], e["memoria"])
        return {**r, "tiempos": self._medir(e, "reescritura", t0)}

    def n_clasificacion(self, e: Estado) -> dict:
        return {"intencion": clasificar_intencion(e["consulta"])}

    def n_reporte(self, e: Estado) -> dict:
        b = borrador_reporte(e["pregunta_limpia"], e.get("equipo"), self.llm)
        texto = ("Preparé un borrador de reporte. Revísalo y confírmalo para registrarlo; "
                 "no se registra nada sin tu confirmación.")
        return {"reporte_borrador": b, "respuesta": texto, "abstencion": False, "citas": []}

    def n_recuperacion(self, e: Estado) -> dict:
        t0 = time.perf_counter()
        intencion = e["intencion"]
        tipos = None if intencion in ("mixta", "fuera_de_alcance") else TIPOS_POR_AGENTE[intencion]
        res = self.recuperador.buscar(e["consulta"], tipos=tipos, equipo_tipo=e.get("equipo_tipo"))
        fragmentos, detalle = res.fragmentos, res.detalle
        # Restricción de dominio (R5): si la consulta implica intervenir, se asegura
        # que el procedimiento de bloqueo esté en el contexto.
        if intencion in ("mixta", "mantenimiento") and not any("bloque" in f["texto"].lower() for f in fragmentos):
            extra = self.recuperador.buscar("bloqueo de energías antes de intervenir el equipo",
                                            tipos={"procedimiento"}, equipo_tipo=e.get("equipo_tipo"), top_k=1)
            fragmentos = extra.fragmentos + fragmentos[: config.TOP_K - 1]
            detalle = extra.detalle + detalle[: config.TOP_K - 1]
        # Datos exactos: por herramienta, con el token del usuario (no se vectorizan).
        historial, avisos = None, list(e.get("avisos", []))
        if e.get("equipo") and intencion in ("mantenimiento", "mixta"):
            try:
                historial = consultar_historial(e["token"], e["equipo"])
            except PermisoDenegado as err:
                avisos.append(f"Atención: no se consultó el historial de {e['equipo']}: {err}")
        return {"fragmentos": fragmentos, "detalle_recuperacion": detalle, "mejor_puntaje": res.mejor_puntaje,
                "respondibilidad": evaluar_respondibilidad(e["pregunta_limpia"], fragmentos),
                "historial": historial, "avisos": avisos, "tiempos": self._medir(e, "recuperacion", t0)}

    def n_generacion(self, e: Estado) -> dict:
        t0 = time.perf_counter()
        requiere_bloqueo = e["intencion"] in ("mixta", "mantenimiento")
        # Primer intento con LLM (si hay); el reintento usa el generador extractivo.
        if self.llm is not None and e.get("intentos", 0) == 0:
            borrador = generar_con_llm(self.llm, e["intencion"], e["pregunta_limpia"], e["fragmentos"],
                                       e.get("historial"), e["memoria"].como_texto())
            generador = f"llm:{self.llm.modelo}"
        else:
            borrador = generar_extractivo(e["consulta"], e["fragmentos"], e.get("historial"), requiere_bloqueo)
            generador = "extractivo"
        return {"borrador": borrador, "generador": generador, "intentos": e.get("intentos", 0) + 1,
                "tiempos": self._medir(e, f"generacion_{e.get('intentos', 0) + 1}", t0)}

    def n_verificacion(self, e: Estado) -> dict:
        t0 = time.perf_counter()
        v = self.verificador.verificar(e["borrador"], e["fragmentos"], e.get("historial"))
        return {"verificacion": v, "tiempos": self._medir(e, f"verificacion_{e['intentos']}", t0)}

    def n_respuesta(self, e: Estado) -> dict:
        partes = list(e.get("avisos", [])) + [e["borrador"]]
        if e["intencion"] in ("mixta", "mantenimiento"):
            partes.append(NOTA_SUPERVISOR)
        citas = sorted({cita(f) for f in e["fragmentos"] if cita(f) in e["borrador"]})
        citas += sorted(set(re.findall(r"\[SAP-PM[^\]]*\]", e["borrador"])))
        return {"respuesta": "\n".join(partes), "abstencion": False, "citas": citas}

    def n_abstencion(self, e: Estado) -> dict:
        if not e.get("fragmentos"):
            motivo = "sin fragmentos vigentes"
        elif e.get("mejor_puntaje", 0) < config.UMBRAL_ABSTENCION:
            motivo = f"mejor puntaje {e.get('mejor_puntaje', 0):.3f} bajo el umbral {config.UMBRAL_ABSTENCION}"
        elif not e.get("respondibilidad", {}).get("respondible", True):
            motivo = "los fragmentos no contienen lo que se pregunta"
        else:
            motivo = f"verificación: {e.get('verificacion', {}).get('veredicto')}"
        texto = "\n".join(list(e.get("avisos", [])) + [mensaje_abstencion(e.get("intencion", "fuera_de_alcance"))])
        return {"respuesta": texto, "abstencion": True, "motivo_abstencion": motivo, "citas": []}

    # ---------------------------------------------------------------- aristas
    @staticmethod
    def r_clasificacion(e: Estado) -> str:
        return "reporte" if e["intencion"] == "reporte" else "recuperacion"

    @staticmethod
    def r_umbral(e: Estado) -> str:
        ok = e["fragmentos"] and e["mejor_puntaje"] >= config.UMBRAL_ABSTENCION and e["respondibilidad"]["respondible"]
        return "generacion" if ok else "abstencion"

    @staticmethod
    def r_verificacion(e: Estado) -> str:
        if e["verificacion"]["veredicto"] == "RESPALDADA":
            return "respuesta"
        if e["intentos"] <= config.MAX_REINTENTOS_VERIFICACION:
            return "generacion"
        return "abstencion"

    def _construir_grafo(self):
        g = StateGraph(Estado)
        for nombre in ("entrada", "reescritura", "clasificacion", "reporte", "recuperacion",
                       "generacion", "verificacion", "respuesta", "abstencion"):
            g.add_node(nombre, getattr(self, f"n_{nombre}"))
        g.add_edge(START, "entrada")
        g.add_edge("entrada", "reescritura")
        g.add_edge("reescritura", "clasificacion")
        g.add_conditional_edges("clasificacion", self.r_clasificacion, ["reporte", "recuperacion"])
        g.add_conditional_edges("recuperacion", self.r_umbral, ["generacion", "abstencion"])
        g.add_edge("generacion", "verificacion")
        g.add_conditional_edges("verificacion", self.r_verificacion, ["respuesta", "generacion", "abstencion"])
        for fin in ("reporte", "respuesta", "abstencion"):
            g.add_edge(fin, END)
        return g.compile()

    # ---------------------------------------------------------------- API
    def consultar(self, pregunta: str, token: str, memoria: MemoriaSesion | None = None) -> dict:
        memoria = memoria if memoria is not None else MemoriaSesion()
        t0 = time.perf_counter()
        try:
            e = self.grafo.invoke({"pregunta": pregunta, "token": token, "memoria": memoria})
        except PermisoDenegado as err:
            return {"respuesta": f"Acceso denegado: {err}", "abstencion": True, "citas": [], "id": None}
        total = round(time.perf_counter() - t0, 3)
        memoria.agregar(e.get("pregunta_limpia", pregunta), e["respuesta"], e.get("equipo"))
        resultado = {
            "id": uuid.uuid4().hex[:12],
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "usuario": trazas.seudonimo(e["usuario"]["usuario"]),
            "rol": e["usuario"]["rol"],
            "pregunta": e["pregunta_limpia"],
            "consulta_reescrita": e.get("consulta"),
            "equipo": e.get("equipo"),
            "hereda_contexto": e.get("hereda_contexto", False),
            "intencion": e.get("intencion"),
            "intento_manipulacion": e.get("intento_manipulacion", False),
            "recuperacion": e.get("detalle_recuperacion", []),
            "mejor_puntaje": e.get("mejor_puntaje"),
            "respondibilidad": e.get("respondibilidad"),
            "historial_sap": [o["ot"] for o in (e.get("historial") or {}).get("ordenes", [])],
            "generador": e.get("generador"),
            "intentos": e.get("intentos", 0),
            "verificacion": e.get("verificacion"),
            "abstencion": e.get("abstencion", False),
            "motivo_abstencion": e.get("motivo_abstencion"),
            "respuesta": e["respuesta"],
            "citas": e.get("citas", []),
            "reporte_borrador": e.get("reporte_borrador"),
            "tiempos": dict(e.get("tiempos", {}), total=total),
            "embedder": self.embedder.nombre,
        }
        trazas.registrar(resultado)
        resultado["fragmentos"] = e.get("fragmentos", [])
        return resultado
