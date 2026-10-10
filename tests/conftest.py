"""Fixtures compartidas.

Las pruebas usan el modo offline (generador extractivo, sin LLM) para ser
deterministas y rápidas. Si Ollama tiene bge-m3, se usa; si no, el embedder de
respaldo. Las pruebas de seguridad deben pasar en ambos casos.
"""
import pytest

from asistente import config
from asistente.embeddings import crear_embedder
from asistente.grafo import AsistenteFaena

MECANICO = "tok-mecanico-mina"
SUPERVISOR = "tok-supervisor"
CONTRATISTA = "tok-contratista"


@pytest.fixture(scope="session")
def asistente(tmp_path_factory):
    config.DIR_TRAZAS = tmp_path_factory.mktemp("trazas")
    config.ARCHIVO_REPORTES = tmp_path_factory.mktemp("reportes") / "reportes.jsonl"
    return AsistenteFaena(llm=None, embedder=crear_embedder())
