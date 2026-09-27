"""Pipeline de ingesta (capa 6 del diagrama).

Extracción -> chunking estructural -> metadatos -> embeddings e indexación dual.

- La segmentación sigue la estructura del documento ("## " = artículo o sección),
  no un tamaño fijo: en normativa la unidad es el artículo; en manuales, el subsistema.
- Cada fragmento guarda documento, sección, revisión, vigencia, idioma y equipos,
  lo que permite filtrar ANTES de buscar.
- Las revisiones históricas se indexan marcadas como tales y el filtro de vigencia
  las excluye (RNF-04). Se conservan para auditoría.
- En producción la extracción la hace Docling con OCR; en el prototipo el corpus
  simulado ya viene en Markdown.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import yaml

from . import config
from .guardrails import sanear_fragmento


def leer_documento(ruta: Path) -> tuple[dict, str]:
    contenido = ruta.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", contenido, re.DOTALL)
    if not m:
        raise ValueError(f"{ruta.name}: falta el encabezado de metadatos")
    return yaml.safe_load(m.group(1)), m.group(2)


def segmentar(meta: dict, cuerpo: str) -> list[dict]:
    fragmentos = []
    for bloque in re.split(r"^## ", cuerpo, flags=re.MULTILINE):
        bloque = bloque.strip()
        if not bloque:
            continue
        seccion, _, texto = bloque.partition("\n")
        texto_limpio, sospechoso = sanear_fragmento(" ".join(texto.split()))
        if not texto_limpio:
            continue
        fragmentos.append({
            "id": f"{meta['doc_id']}#r{meta['revision']}#{seccion.split('.')[0].strip()}",
            "doc_id": meta["doc_id"],
            "titulo": meta["titulo"],
            "seccion": seccion.strip(),
            "revision": meta["revision"],
            "vigencia": meta["vigencia"],
            "fecha": str(meta["fecha"]),
            "idioma": meta["idioma"],
            "equipos": meta.get("equipos", []),
            "tipo": meta["tipo"],
            "origen": meta["origen"],
            "prioridad": meta.get("prioridad", "media"),
            "sospechoso": sospechoso,
            "texto": texto_limpio,
        })
    return fragmentos


def cargar_corpus(directorio: Path = config.DIR_CORPUS) -> list[dict]:
    fragmentos = []
    for ruta in sorted(directorio.rglob("*.md")):
        meta, cuerpo = leer_documento(ruta)
        fragmentos.extend(segmentar(meta, cuerpo))
    return fragmentos


def construir_indice(embedder, directorio: Path = config.DIR_CORPUS, destino: Path = config.DIR_INDICE) -> dict:
    """Indexa el corpus y guarda fragmentos + embeddings en disco."""
    fragmentos = cargar_corpus(directorio)
    # Se vectoriza título + sección + texto para que el fragmento sea autocontenido.
    textos = [f"{f['titulo']}. {f['seccion']}. {f['texto']}" for f in fragmentos]
    matriz = embedder.embed(textos)
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "fragmentos.json").write_text(json.dumps(fragmentos, ensure_ascii=False, indent=1), encoding="utf-8")
    np.save(destino / "embeddings.npy", matriz)
    resumen = {
        "embedder": embedder.nombre,
        "documentos": len({(f["doc_id"], f["revision"]) for f in fragmentos}),
        "fragmentos": len(fragmentos),
        "vigentes": sum(f["vigencia"] == "vigente" for f in fragmentos),
        "historicos": sum(f["vigencia"] != "vigente" for f in fragmentos),
        "sospechosos": [f["id"] for f in fragmentos if f["sospechoso"]],
    }
    (destino / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")
    return resumen


def carpeta_indice(embedder) -> Path:
    """Un índice por modelo de embeddings: los vectores de modelos distintos no son comparables."""
    return config.DIR_INDICE / embedder.nombre.replace(":", "_")


def cargar_indice(embedder, destino: Path | None = None) -> tuple[list[dict], np.ndarray]:
    """Carga el índice; lo reconstruye si no existe o si cambió el modelo de embeddings."""
    destino = destino or carpeta_indice(embedder)
    resumen = destino / "resumen.json"
    if not resumen.exists() or json.loads(resumen.read_text(encoding="utf-8"))["embedder"] != embedder.nombre:
        construir_indice(embedder, destino=destino)
    fragmentos = json.loads((destino / "fragmentos.json").read_text(encoding="utf-8"))
    return fragmentos, np.load(destino / "embeddings.npy")


if __name__ == "__main__":
    from .embeddings import crear_embedder

    emb = crear_embedder()
    print(json.dumps(construir_indice(emb, destino=carpeta_indice(emb)), ensure_ascii=False, indent=2))
