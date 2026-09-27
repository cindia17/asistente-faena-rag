"""Recuperación híbrida (capa 4 del diagrama, sección 3.3 del informe).

filtro por metadatos -> BM25 (léxica) + densa (kNN) -> fusión RRF -> reranking -> top-k -> umbral

- Híbrida porque las consultas mezclan lenguaje coloquial (lo resuelve la densa)
  con identificadores exactos como "CAEX-14" o "PETS-MEC-012" (lo resuelve BM25).
- RRF evita calibrar pesos entre puntajes no comparables (Cormack et al., 2009).
- El reranker del prototipo combina similitud semántica y cobertura de términos;
  en producción se reemplaza por el cross-encoder bge-reranker-v2-m3.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from rank_bm25 import BM25Okapi

from . import config
from .texto import cobertura, tokenizar

BONO_PRIORIDAD = {"alta": 0.05, "media": 0.0, "baja": -0.05}


@dataclass
class ResultadoRecuperacion:
    fragmentos: list[dict]
    mejor_puntaje: float
    supera_umbral: bool
    candidatos: int
    detalle: list[dict] = field(default_factory=list)


class RecuperadorHibrido:
    def __init__(self, fragmentos: list[dict], embeddings: np.ndarray, embedder):
        self.fragmentos = fragmentos
        self.embeddings = embeddings
        self.embedder = embedder
        self.bm25 = BM25Okapi([tokenizar(f"{f['titulo']} {f['seccion']} {f['texto']}") for f in fragmentos])

    # -- 1. Filtro por metadatos -------------------------------------------------
    def _filtrar(self, tipos: set[str] | None, equipo_tipo: str | None) -> np.ndarray:
        idx = []
        for i, f in enumerate(self.fragmentos):
            if f["vigencia"] != "vigente":          # RNF-04: nunca revisiones superadas
                continue
            if tipos and f["tipo"] not in tipos:
                continue
            if equipo_tipo and f["equipos"] and equipo_tipo not in f["equipos"]:
                continue
            idx.append(i)
        return np.array(idx, dtype=int)

    # -- 2. Búsquedas -------------------------------------------------------------
    def _bm25(self, consulta: str, permitidos: np.ndarray, n: int) -> list[int]:
        puntajes = self.bm25.get_scores(tokenizar(consulta))[permitidos]
        orden = np.argsort(-puntajes)
        return [int(permitidos[i]) for i in orden[:n] if puntajes[i] > 0]

    def _densa(self, q_vec: np.ndarray, permitidos: np.ndarray, n: int) -> list[int]:
        sims = self.embeddings[permitidos] @ q_vec
        orden = np.argsort(-sims)
        return [int(permitidos[i]) for i in orden[:n]]

    # -- 3. Fusión RRF ------------------------------------------------------------
    @staticmethod
    def rrf(rankings: list[list[int]], k: int = config.K_RRF) -> dict[int, float]:
        puntajes: dict[int, float] = {}
        for ranking in rankings:
            for pos, i in enumerate(ranking):
                puntajes[i] = puntajes.get(i, 0.0) + 1.0 / (k + pos + 1)
        return puntajes

    # -- 4. Reranking ---------------------------------------------------------------
    def _rerank(self, consulta: str, q_vec: np.ndarray, candidatos: list[int]) -> list[tuple[int, float, float, float]]:
        salida = []
        for i in candidatos:
            f = self.fragmentos[i]
            semantica = float(self.embeddings[i] @ q_vec)
            cob = cobertura(consulta, f"{f['titulo']} {f['seccion']} {f['texto']}")
            puntaje = 0.6 * semantica + 0.4 * cob + BONO_PRIORIDAD.get(f["prioridad"], 0.0)
            salida.append((i, puntaje, semantica, cob))
        return sorted(salida, key=lambda t: -t[1])

    def buscar(self, consulta: str, tipos: set[str] | None = None, equipo_tipo: str | None = None,
               top_k: int = config.TOP_K) -> ResultadoRecuperacion:
        permitidos = self._filtrar(tipos, equipo_tipo)
        if len(permitidos) == 0:
            return ResultadoRecuperacion([], 0.0, False, 0)
        q_vec = self.embedder.embed([consulta])[0]
        n = config.CANDIDATOS_POR_INDICE
        fusion = self.rrf([self._bm25(consulta, permitidos, n), self._densa(q_vec, permitidos, n)])
        rerank = self._rerank(consulta, q_vec, list(fusion))[:top_k]
        detalle, elegidos = [], []
        for i, puntaje, sem, cob in rerank:
            frag = dict(self.fragmentos[i], puntaje=round(puntaje, 4))
            elegidos.append(frag)
            detalle.append({"id": frag["id"], "rrf": round(fusion[i], 4), "semantica": round(sem, 3),
                            "cobertura": round(cob, 3), "puntaje": round(puntaje, 4)})
        mejor = elegidos[0]["puntaje"] if elegidos else 0.0
        return ResultadoRecuperacion(elegidos, mejor, mejor >= config.UMBRAL_ABSTENCION, len(fusion), detalle)
