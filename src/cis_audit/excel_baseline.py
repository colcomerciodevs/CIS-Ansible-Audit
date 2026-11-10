"""Lectura de la Línea Base de Seguridad (Excel) en modo solo lectura.

Las columnas se identifican por su encabezado normalizado, no por posición,
para tolerar espacios, saltos de línea, acentos y sufijos como "RH_ORA V8".
El valor original de cada celda se conserva sin modificar.
"""

import re
from pathlib import Path
from typing import Dict, Optional

import openpyxl

from .normalizacion import clave, texto

# Campo interno -> función que reconoce el encabezado normalizado (clave()).
COLUMNAS = {
    "orden": lambda h: h == "ORDEN",
    "control_excel": lambda h: h == "NIVEL",
    "titulo_excel": lambda h: h.startswith("CATEGORIA"),
    "cis_id_excel": lambda h: h.startswith("NUMERO DE CIS_CONTROL"),
    "cis_titulo_excel": lambda h: h.startswith("TITULO CIS_CONTROL"),
    "descripcion": lambda h: h.startswith("DESCRIPCION DEL CONTROL"),
    "comentarios": lambda h: h.startswith("COMENTARIOS"),
    "riesgos_seguridad": lambda h: h.startswith("RIESGOS SEGURIDAD"),
    "riesgos_operativos": lambda h: h.startswith("RIESGOS OPERATIVO"),
    "criticidad": lambda h: h.startswith("CRITICIDAD"),
    "analisis_impacto": lambda h: h.startswith("ANALISIS IMPACTO"),
    "validacion_excel": lambda h: h.startswith("VALIDACION DEL PARAMETRO"),
    "novedades_numeracion": lambda h: h.startswith("NOVEDADES AJUSTE NUMERACION"),
    "exclusion_infraestructura": lambda h: h.startswith("EXCLUSIONES"),
    "propuesta_proveedor": lambda h: h.startswith("PROPUESTA DE") and "IMPLEMENTACION" in h,
    "observaciones_seguridad": lambda h: h.startswith("OBSERVACIONES SEGURIDAD"),
}
OBLIGATORIAS = [
    "control_excel",
    "titulo_excel",
    "cis_id_excel",
    "cis_titulo_excel",
    "criticidad",
    "validacion_excel",
    "novedades_numeracion",
    "exclusion_infraestructura",
    "propuesta_proveedor",
]

# Hoja -> perfil de auditoría y benchmarks aplicables.
PERFILES = [
    (re.compile(r"RHEL_ORA_8"), "RHEL_ORACLE_8", ["RHEL8", "ORACLE8"]),
    (re.compile(r"RHEL_ORA_9"), "RHEL_ORACLE_9", ["RHEL9", "ORACLE9"]),
    (re.compile(r"SUSE"), "SLES_15", ["SLES15"]),
]

_NIVELES_CRITICIDAD = {"ALTO", "MEDIO", "BAJO"}


class ErrorEstructuraExcel(Exception):
    pass


def perfil_de_hoja(nombre: str):
    for patron, perfil, benchmarks in PERFILES:
        if nombre.startswith("LBS") and patron.search(nombre):
            return perfil, benchmarks
    return None, []


def _mapear_columnas(ws) -> Dict[str, int]:
    mapa: Dict[str, int] = {}
    for col in range(1, ws.max_column + 1):
        h = clave(ws.cell(1, col).value)
        if not h:
            continue
        for campo, reconoce in COLUMNAS.items():
            if campo not in mapa and reconoce(h):
                mapa[campo] = col
                break
    faltantes = [c for c in OBLIGATORIAS if c not in mapa]
    if faltantes:
        raise ErrorEstructuraExcel(f"Hoja '{ws.title}': faltan columnas {faltantes}")
    return mapa


def normalizar_criticidad(valor) -> str:
    k = clave(valor)
    if k in _NIVELES_CRITICIDAD:
        return k
    if "CONSULTAR" in k:
        return "NO DEFINIDA (remite a NOVEDADES)"
    return "NO DEFINIDA" if not k else f"NO RECONOCIDA ({k})"


def leer_linea_base(ruta: Path) -> Dict:
    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=False)
    hojas = []
    for ws in wb.worksheets:
        perfil, benchmarks = perfil_de_hoja(ws.title)
        if not perfil:
            continue
        mapa = _mapear_columnas(ws)
        filas = []
        for r in range(2, ws.max_row + 1):
            valores = {campo: ws.cell(r, col).value for campo, col in mapa.items()}
            if not texto(valores.get("control_excel")).strip() and not texto(
                valores.get("cis_id_excel")
            ).strip():
                continue
            fila = {campo: (texto(v) if v is not None else None) for campo, v in valores.items()}
            fila["fila_excel"] = r
            filas.append(fila)
        hojas.append(
            {
                "hoja": ws.title,
                "perfil": perfil,
                "benchmarks": benchmarks,
                "columnas": {
                    campo: {
                        "columna": openpyxl.utils.get_column_letter(col),
                        "encabezado_original": texto(ws.cell(1, col).value),
                    }
                    for campo, col in mapa.items()
                },
                "filas": filas,
            }
        )
    return {"archivo": ruta.name, "hojas": hojas, "todas_las_hojas": wb.sheetnames}


def localizar_excel(dir_baseline: Path, nombre: Optional[str] = None) -> Optional[Path]:
    """Excel de la línea base de la Parte 2. Con varias partes en input/baseline se usa el
    nombre configurado en config/audit.yml (partes_proyecto.PARTE_2.linea_base)."""
    if nombre:
        ruta = dir_baseline / Path(nombre).name
        if not ruta.exists():
            raise ErrorEstructuraExcel(f"No existe la línea base configurada {ruta}")
        return ruta
    candidatos = sorted(p for p in dir_baseline.glob("*.xlsx") if not p.name.startswith("~$"))
    if len(candidatos) > 1:
        raise ErrorEstructuraExcel(
            f"Se encontró más de un .xlsx en {dir_baseline}; dejar solo la línea base vigente"
        )
    return candidatos[0] if candidatos else None
