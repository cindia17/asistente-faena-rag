"""Orquestador con LangGraph (capa 2 del diagrama; Figura 2 del informe).

    entrada -> reescritura -> clasificacion ─┬─> reporte ──────────────────────────────┐
                                             └─> recuperacion -> [umbral] ─┬─> abstencion ─┤
                                                                           └─> generacion   │
                                     generacion -> verificacion ─┬─> respuesta ────────────┤
                                                                 ├─> reintento (extractivo)│
                                                                 └─> abstencion ───────────┴─> registro -> FIN

El grafo explícito hace que el flujo sea auditable: cada nodo deja su huella en
la traza y los reintentos tienen un límite fijo.

Privacidad de la traza: el token SSO y la memoria de la sesión NO viajan en el
estado del grafo (que LangSmith registra completo). El estado solo lleva un id
de sesión opaco; el token y la memoria quedan en `self._sesiones` mientras dura
la consulta.
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime
from typing import Any, TypedDict

import requests
from langgraph.graph import END, START, StateGraph

from . import config, metricas, observabilidad, trazas
from .agentes import (MENSAJE_BIENVENIDA, NOTA_SUPERVISOR, TIPOS_POR_AGENTE, borrador_reporte, clasificar_intencion,
                      evaluar_respondibilidad, generar_con_llm, generar_extractivo, mensaje_abstencion,
                      implica_intervencion, pide_historial)
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
    sesion: str            # id opaco; el token y la memoria quedan fuera del estado
    usuario: dict          # seudónimo y rol, nunca el nombre ni el token
    pregunta_limpia: str
    intento_manipulacion: bool
    consulta: str
    equipo: str | None
    equipo_tipo: str | None
    hereda_contexto: bool
    intencion: str
    interviene: bool
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
        self._sesiones: dict[str, dict] = {}   # id de sesión -> {"token", "memoria"} durante la consulta
        self.enviar_feedback = True            # métricas como feedback en LangSmith (si está activo)

    # ---------------------------------------------------------------- nodos
    def _medir(self, estado: Estado, etapa: str, inicio: float) -> dict:
        tiempos = dict(estado.get("tiempos", {}))
        tiempos[etapa] = round(time.perf_counter() - inicio, 3)
        return tiempos

    def _token(self, e: Estado) -> str:
        return self._sesiones[e["sesion"]]["token"]

    def _memoria(self, e: Estado) -> MemoriaSesion:
        return self._sesiones[e["sesion"]]["memoria"]

    def n_entrada(self, e: Estado) -> dict:
        t0 = time.perf_counter()
        u = autenticar(self._token(e))                 # SSO simulado: sin token válido no hay consulta
        usuario = {"usuario": trazas.seudonimo(u["usuario"]), "rol": u["rol"]}
        g = revisar_entrada(e["pregunta"])
        avisos = []
        if g["intento_manipulacion"]:
            avisos.append("Atención: la consulta incluye instrucciones dirigidas al asistente; se ignoran y se responde solo con documentos.")
        return {"usuario": usuario, "pregunta_limpia": g["pregunta_limpia"],
                "intento_manipulacion": g["intento_manipulacion"], "avisos": avisos,
                "intentos": 0, "tiempos": self._medir(e, "entrada", t0)}

    def n_reescritura(self, e: Estado) -> dict:
        t0 = time.perf_counter()
        r = reescribir_consulta(e["pregunta_limpia"], self._memoria(e))
        return {**r, "tiempos": self._medir(e, "reescritura", t0)}

    def n_clasificacion(self, e: Estado) -> dict:
        intencion = clasificar_intencion(e["consulta"])
        return {"intencion": intencion, "interviene": implica_intervencion(e["consulta"], intencion)}

    def n_saludo(self, e: Estado) -> dict:
        return {"respuesta": MENSAJE_BIENVENIDA, "abstencion": False, "citas": []}

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
        if e.get("interviene") and not any("bloque" in f["texto"].lower() for f in fragmentos):
            extra = self.recuperador.buscar("bloqueo de energías antes de intervenir el equipo",
                                            tipos={"procedimiento"}, equipo_tipo=e.get("equipo_tipo"), top_k=1)
            if extra.fragmentos:
                fragmentos = extra.fragmentos + fragmentos[: config.TOP_K - 1]
                detalle = extra.detalle + detalle[: config.TOP_K - 1]
        # Datos exactos: por herramienta, con el token del usuario (no se vectorizan).
        historial, avisos = None, list(e.get("avisos", []))
        if e.get("equipo") and intencion in ("mantenimiento", "mixta"):
            try:
                historial = consultar_historial(self._token(e), e["equipo"])
            except PermisoDenegado as err:
                avisos.append(f"Atención: no se consultó el historial de {e['equipo']}: {err}")
        respondibilidad = evaluar_respondibilidad(e["pregunta_limpia"], fragmentos)
        # Si se pide el historial y SAP PM lo entrega, la respuesta se respalda en ese
        # dato exacto (herramienta), aunque ningún documento hable del equipo.
        if historial and historial.get("ordenes") and pide_historial(e["pregunta_limpia"]):
            respondibilidad = dict(respondibilidad, respondible=True, fuente="SAP PM")
        return {"fragmentos": fragmentos, "detalle_recuperacion": detalle, "mejor_puntaje": res.mejor_puntaje,
                "respondibilidad": respondibilidad,
                "historial": historial, "avisos": avisos, "tiempos": self._medir(e, "recuperacion", t0)}

    def n_generacion(self, e: Estado) -> dict:
        t0 = time.perf_counter()
        requiere_bloqueo = e.get("interviene", False)
        # Primer intento con LLM (si hay); el reintento usa el generador extractivo.
        borrador, avisos = None, list(e.get("avisos", []))
        if self.llm is not None and e.get("intentos", 0) == 0:
            try:
                borrador = generar_con_llm(self.llm, e["intencion"], e["pregunta_limpia"], e["fragmentos"],
                                           e.get("historial"), self._memoria(e).como_texto())
                generador = f"llm:{self.llm.modelo}"
            except requests.RequestException:
                # RNF-03: si el modelo no responde (caído o saturado), la consulta no se
                # cae: se responde con el generador extractivo, que es fiel por construcción.
                avisos.append("Atención: el modelo de lenguaje no respondió a tiempo; "
                              "la respuesta se arma con citas textuales de los documentos.")
        if borrador is None:
            borrador = generar_extractivo(e["consulta"], e["fragmentos"], e.get("historial"), requiere_bloqueo)
            generador = "extractivo"
        return {"borrador": borrador, "generador": generador, "intentos": e.get("intentos", 0) + 1,
                "avisos": avisos, "tiempos": self._medir(e, f"generacion_{e.get('intentos', 0) + 1}", t0)}

    def n_verificacion(self, e: Estado) -> dict:
        t0 = time.perf_counter()
        v = self.verificador.verificar(e["borrador"], e["fragmentos"], e.get("historial"))
        return {"verificacion": v, "tiempos": self._medir(e, f"verificacion_{e['intentos']}", t0)}

    def n_respuesta(self, e: Estado) -> dict:
        partes = list(e.get("avisos", [])) + [e["borrador"]]
        if e.get("interviene"):
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
        return e["intencion"] if e["intencion"] in ("reporte", "saludo") else "recuperacion"

    @staticmethod
    def r_umbral(e: Estado) -> str:
        ok = e["fragmentos"] and e["mejor_puntaje"] >= config.UMBRAL_ABSTENCION and e["respondibilidad"]["respondible"]
        return "generacion" if ok else "abstencion"

    @staticmethod
    def r_verificacion(e: Estado) -> str:
        if e["verificacion"]["veredicto"] == "RESPALDADA":
            return "respuesta"
        # Reintentar solo sirve si el borrador vino del LLM: el reintento usa el
        # generador extractivo, y repetirlo daría exactamente el mismo texto.
        if e["generador"].startswith("llm:") and e["intentos"] <= config.MAX_REINTENTOS_VERIFICACION:
            return "generacion"
        return "abstencion"

    def _construir_grafo(self):
        g = StateGraph(Estado)
        for nombre in ("entrada", "reescritura", "clasificacion", "reporte", "saludo", "recuperacion",
                       "generacion", "verificacion", "respuesta", "abstencion"):
            g.add_node(nombre, getattr(self, f"n_{nombre}"))
        g.add_edge(START, "entrada")
        g.add_edge("entrada", "reescritura")
        g.add_edge("reescritura", "clasificacion")
        g.add_conditional_edges("clasificacion", self.r_clasificacion, ["reporte", "saludo", "recuperacion"])
        g.add_conditional_edges("recuperacion", self.r_umbral, ["generacion", "abstencion"])
        g.add_edge("generacion", "verificacion")
        g.add_conditional_edges("verificacion", self.r_verificacion, ["respuesta", "generacion", "abstencion"])
        for fin in ("reporte", "saludo", "respuesta", "abstencion"):
            g.add_edge(fin, END)
        return g.compile()

    # ---------------------------------------------------------------- API
    def consultar(self, pregunta: str, token: str, memoria: MemoriaSesion | None = None) -> dict:
        """Responde una consulta. Si LangSmith está activo, la deja trazada con sus métricas."""
        memoria = memoria if memoria is not None else MemoriaSesion()
        run_id = uuid.uuid4()
        with observabilidad.contexto_traza():
            resultado = observabilidad.ejecutar(
                self._consultar, pregunta, token, memoria, run_id=run_id,
                metadata={"embedder": self.embedder.nombre, "umbral_abstencion": config.UMBRAL_ABSTENCION,
                          "generador_disponible": f"llm:{self.llm.modelo}" if self.llm else "extractivo"})
        if resultado.get("id"):
            resultado["traza_langsmith"] = str(run_id) if observabilidad.activa() else None
            if self.enviar_feedback:
                observabilidad.enviar_feedback(run_id, resultado["metricas"], resultado["abstencion"])
        return resultado

    @observabilidad.trazar("asistente_faena", entradas=observabilidad.entradas_consulta,
                           salidas=observabilidad.salidas_consulta)
    def _consultar(self, pregunta: str, token: str, memoria: MemoriaSesion) -> dict:
        t0 = time.perf_counter()
        sesion = uuid.uuid4().hex
        self._sesiones[sesion] = {"token": token, "memoria": memoria}
        try:
            e = self.grafo.invoke({"pregunta": pregunta, "sesion": sesion})
        except PermisoDenegado as err:
            return {"respuesta": f"Acceso denegado: {err}", "abstencion": True, "citas": [], "id": None}
        finally:
            self._sesiones.pop(sesion, None)
        total = round(time.perf_counter() - t0, 3)
        memoria.agregar(e.get("pregunta_limpia", pregunta), e["respuesta"], e.get("equipo"))
        tiempos = dict(e.get("tiempos", {}), total=total)
        resultado = {
            "id": uuid.uuid4().hex[:12],
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "usuario": e["usuario"]["usuario"],          # ya viene seudonimizado desde n_entrada
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
            "tiempos": tiempos,
            "metricas": metricas.calcular(
                pregunta=e.get("pregunta_limpia", pregunta), respuesta=e["respuesta"], tiempos=tiempos,
                fragmentos=e.get("fragmentos"), verificacion=e.get("verificacion"),
                abstencion=e.get("abstencion", False), embedder=self.embedder),
            "embedder": self.embedder.nombre,
        }
        trazas.registrar(resultado)
        resultado["fragmentos"] = e.get("fragmentos", [])
        return resultado
