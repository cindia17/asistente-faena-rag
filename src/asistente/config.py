"""Configuración central del Asistente de Faena.

Todos los parámetros ajustables viven aquí para que un cambio (umbral, modelo,
top-k) quede en un solo lugar y sea trazable en el control de versiones.
"""
from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DIR_CORPUS = RAIZ / "data" / "corpus"
DIR_INDICE = RAIZ / "data" / "indice"
ARCHIVO_SAP = RAIZ / "data" / "sap_pm_mock.json"
ARCHIVO_USUARIOS = RAIZ / "data" / "usuarios.json"
ARCHIVO_REPORTES = RAIZ / "data" / "reportes_registrados.jsonl"
DIR_TRAZAS = RAIZ / "trazas"

# --- Modelos locales (RNF-02: inferencia on-premise) -------------------------
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODELO_EMBEDDINGS = os.getenv("MODELO_EMBEDDINGS", "bge-m3")
MODELO_GENERADOR = os.getenv("MODELO_GENERADOR", "qwen3.5:0.8b")
MODELO_VERIFICADOR = os.getenv("MODELO_VERIFICADOR", MODELO_GENERADOR)

# "ollama" usa modelos locales; "offline" usa un generador extractivo
# determinista (sin LLM), útil para pruebas automatizadas y equipos sin recursos.
MODO_LLM = os.getenv("MODO_LLM", "auto")          # auto | ollama | offline
MODO_EMBEDDINGS = os.getenv("MODO_EMBEDDINGS", "auto")  # auto | ollama | hashing
# Segundos de espera al LLM; si no responde, se usa el generador extractivo (RNF-03).
TIMEOUT_LLM = float(os.getenv("TIMEOUT_LLM", "300"))

# --- Recuperación (sección 3.3 del informe) ----------------------------------
CANDIDATOS_POR_INDICE = 20   # candidatos que trae BM25 y la búsqueda densa
K_RRF = 60                   # constante de Reciprocal Rank Fusion (Cormack et al., 2009)
TOP_K = 5                    # fragmentos que pasan al generador tras el reranking
# Umbral de abstención: si el mejor fragmento no lo supera, no se genera respuesta.
# Es el parámetro más sensible para la seguridad (ver informe, conclusiones).
UMBRAL_ABSTENCION = float(os.getenv("UMBRAL_ABSTENCION", "0.30"))

# --- Contexto conversacional --------------------------------------------------
TURNOS_HISTORIAL = 4         # turnos previos usados para reescribir la consulta

# --- Salida -------------------------------------------------------------------
MAX_PALABRAS = 200
MAX_REINTENTOS_VERIFICACION = 1

FRASE_ABSTENCION = "No tengo respaldo documental para responder esto"
