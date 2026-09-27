"""Servidor MCP sobre las herramientas de SAP PM (capa de herramientas del informe).

Expone el mismo contrato de `herramientas.py` por Model Context Protocol, para que
cualquier agente compatible (LangGraph, Claude, etc.) lo use sin acoplarse a SAP.
La autorización sigue ocurriendo dentro de cada herramienta, con el token del usuario.

Opcional:  pip install mcp   y luego   python -m asistente.mcp_servidor
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import herramientas

servidor = FastMCP("sap-pm-faena")


@servidor.tool()
def consultar_historial(token: str, equipo: str, meses: int = 24) -> dict:
    """Historial exacto de órdenes de trabajo de un equipo (p. ej. CAEX-14)."""
    try:
        return herramientas.consultar_historial(token, equipo, meses)
    except herramientas.PermisoDenegado as err:
        return {"error": "permiso_denegado", "detalle": str(err)}


@servidor.tool()
def registrar_reporte(token: str, borrador: dict, confirmado: bool) -> dict:
    """Registra un reporte de incidente SOLO si el usuario confirmó."""
    try:
        return herramientas.registrar_reporte(token, borrador, confirmado)
    except herramientas.PermisoDenegado as err:
        return {"error": "permiso_denegado", "detalle": str(err)}


if __name__ == "__main__":
    servidor.run()
