"""Capa de herramientas (function calling) sobre SAP PM y el registro de reportes.

Decisión de seguridad del informe: la autorización se valida AQUÍ, en cada
llamada, con el token del usuario, y no se delega al modelo. Aunque alguien
manipule el prompt, la herramienta no entrega datos fuera de sus permisos.

Los datos exactos (fechas, OT, horas) se devuelven tal cual: no se vectorizan.
El mismo contrato se expone como servidor MCP en `mcp_servidor.py`.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from . import config


class PermisoDenegado(Exception):
    pass


def _usuarios() -> dict:
    return json.loads(config.ARCHIVO_USUARIOS.read_text(encoding="utf-8"))


def autenticar(token: str) -> dict:
    datos = _usuarios()
    usuario = datos["tokens"].get(token)
    if not usuario:
        raise PermisoDenegado("Token inválido o sesión expirada.")
    return dict(usuario, permisos=datos["permisos_por_rol"][usuario["rol"]])


def consultar_historial(token: str, equipo: str, meses: int = 24, hoy: date | None = None) -> dict:
    """Historial de órdenes de trabajo de un equipo en los últimos `meses`."""
    usuario = autenticar(token)
    if "leer_historial" not in usuario["permisos"]:
        raise PermisoDenegado(f"El rol '{usuario['rol']}' no puede consultar historial de SAP PM.")
    sap = json.loads(config.ARCHIVO_SAP.read_text(encoding="utf-8"))
    if equipo not in sap["equipos"]:
        return {"equipo": equipo, "encontrado": False, "ordenes": []}
    area = sap["equipos"][equipo]["area"]
    if area not in usuario["areas"]:
        raise PermisoDenegado(f"El usuario no tiene acceso a equipos del área '{area}'.")
    desde = (hoy or date.today()) - timedelta(days=30 * meses)
    ordenes = [o for o in sap["ordenes_trabajo"]
               if o["equipo"] == equipo and date.fromisoformat(o["fecha"]) >= desde]
    return {"equipo": equipo, "encontrado": True, "ficha": sap["equipos"][equipo],
            "ordenes": sorted(ordenes, key=lambda o: o["fecha"], reverse=True)}


def registrar_reporte(token: str, borrador: dict, confirmado: bool) -> dict:
    """RF-05: solo registra si el usuario confirmó explícitamente."""
    usuario = autenticar(token)
    if "crear_reporte" not in usuario["permisos"]:
        raise PermisoDenegado("El rol no puede registrar reportes.")
    if not confirmado:
        return {"registrado": False, "motivo": "Falta la confirmación del usuario."}
    registro = dict(borrador, reportado_por=usuario["usuario"], fecha_registro=datetime.now().isoformat(timespec="seconds"))
    with config.ARCHIVO_REPORTES.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")
    return {"registrado": True, "registro": registro}


def cita_ot(orden: dict) -> str:
    return f"[SAP-PM | {orden['ot']} | {orden['fecha']} | dato operacional]"
