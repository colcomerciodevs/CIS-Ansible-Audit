"""Versión Excel de la matriz de alcance para revisión humana."""

from pathlib import Path
from typing import Callable, Dict, List, Tuple

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

_ENCABEZADO = PatternFill("solid", fgColor="1F3864")
_FUENTE_ENC = Font(bold=True, color="FFFFFF")
_COLORES = {
    "EN ALCANCE": "C6EFCE",
    "FUERA DE ALCANCE": "D9D9D9",
    "INDETERMINADO": "FFEB9C",
    "PENDIENTE_REVISION": "FFEB9C",
    "ID_EXCEL_INCORRECTO": "F8CBAD",
    "NO_ENCONTRADO": "F8CBAD",
    "RETIRADO": "D9D9D9",
}
_ANCHOS = {"Título Excel": 45, "Título CIS": 50, "Alertas": 80, "Observaciones": 60,
           "Exclusión Infraestructura": 50, "Justificación mapeo": 60,
           "Novedades numeración (Excel)": 45, "Verificación técnica de la exclusión": 50,
           "Evidencia organizacional requerida": 50, "Nota validación": 45}


def _hoja_tabla(wb, nombre: str, columnas: List[Tuple[str, str]], filas: List[Dict], plano: Callable):
    ws = wb.create_sheet(nombre)
    ws.append([t for _, t in columnas])
    for c in ws[1]:
        c.fill, c.font = _ENCABEZADO, _FUENTE_ENC
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for f in filas:
        ws.append([plano(f.get(k)) for k, _ in columnas])
    for i, (_, t) in enumerate(columnas, start=1):
        ws.column_dimensions[get_column_letter(i)].width = _ANCHOS.get(t, 16)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    if filas:
        ws.auto_filter.ref = ws.dimensions
        ultima = ws.max_row
        for i, (k, _) in enumerate(columnas, start=1):
            if k in ("estado_alcance", "estado_mapeo"):
                letra = get_column_letter(i)
                for valor, color in _COLORES.items():
                    ws.conditional_formatting.add(
                        f"{letra}2:{letra}{ultima}",
                        FormulaRule(formula=[f'{letra}2="{valor}"'],
                                    fill=PatternFill("solid", fgColor=color)),
                    )
    return ws


def escribir_xlsx(datos: Dict, ruta: Path, columnas, plano) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    ws.append(["Matriz de alcance y mapeo CIS (sin evidencia de servidores)"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Generado: {datos['generado']}"])
    ws.append([])
    ws.append(["Hoja Excel", "Perfil", "Benchmarks", "Controles Excel", "En alcance",
               "Fuera de alcance", "Indeterminado"])
    for c in ws[ws.max_row]:
        c.fill, c.font = _ENCABEZADO, _FUENTE_ENC
    for hoja, r in datos["resumen"]["por_hoja"].items():
        ws.append([hoja, r["perfil"], ", ".join(r["benchmarks"]), r["controles_excel"],
                   r["en_alcance"], r["fuera_de_alcance"], r["indeterminado"]])
    ws.append([])
    ws.append(["Benchmark", "Documento", "Controles CIS únicos a auditar",
               "Cambios de numeración (en alcance)", "Requieren confirmación",
               "Filas en alcance con alertas"])
    for c in ws[ws.max_row]:
        c.fill, c.font = _ENCABEZADO, _FUENTE_ENC
    for b, r in datos["resumen"]["por_benchmark"].items():
        ws.append([b, r["benchmark"], r["controles_cis_unicos_a_auditar"],
                   r["cambio_numeracion_en_alcance"], len(r["requiere_confirmacion"]),
                   r["filas_con_alertas_en_alcance"]])
    ws.append([])
    ws.append(["Fuentes CIS"])
    ws[f"A{ws.max_row}"].font = Font(bold=True)
    for c in datos["fuentes"]["cis"]:
        ws.append([c["clave"], c["titulo"], c["version"], c["fecha"], c["archivo"],
                   c["total_recomendaciones"], c["sha256"]])
    ws.append([])
    ws.append(["Estados de mapeo"])
    ws[f"A{ws.max_row}"].font = Font(bold=True)
    for k, v in datos["estados_mapeo"].items():
        ws.append([k, v])
    for col, ancho in zip("ABCDEFG", (22, 45, 30, 22, 22, 22, 66)):
        ws.column_dimensions[col].width = ancho

    matriz = datos["matriz"]
    _hoja_tabla(wb, "Matriz", columnas, matriz, plano)
    for perfil in ("RHEL_ORACLE_8", "RHEL_ORACLE_9", "SLES_15"):
        _hoja_tabla(wb, perfil, columnas, [f for f in matriz if f["perfil_auditoria"] == perfil], plano)

    en = [f for f in matriz if f["estado_alcance"] == "EN ALCANCE"]
    _hoja_tabla(wb, "Exclusiones", columnas,
                [f for f in en if f["tipo_exclusion"] not in ("NINGUNA",)], plano)
    _hoja_tabla(wb, "Revisar_Mapeo", columnas,
                [f for f in matriz if f["estado_mapeo"] not in ("MAPEADO",) or f["alertas"]], plano)
    _hoja_tabla(wb, "Fuera_de_Alcance", columnas,
                [f for f in matriz if f["estado_alcance"] != "EN ALCANCE"], plano)

    # Controles CIS únicos a auditar por benchmark (base del plan de auditoría)
    unicos = []
    for b, r in datos["resumen"]["por_benchmark"].items():
        for cid in r["lista_controles_cis_a_auditar"]:
            fs = [f for f in en if f["benchmark"] == b and f["control_cis"] == cid]
            f0 = fs[0]
            unicos.append({
                "benchmark": b, "control_cis": cid, "titulo_cis": f0["titulo_cis"],
                "assessment_cis": f0["assessment_cis"], "pagina_cis": f0["pagina_cis"],
                "filas": [f"{f['control_excel']} (fila {f['fila_excel']})" for f in fs],
                "criticidad": sorted({f["criticidad"] for f in fs}),
                "tipo_exclusion": sorted({f["tipo_exclusion"] for f in fs}),
                "dominio_validacion": f0["dominio_validacion"],
                "tipo_validacion": f0["tipo_validacion"], "nota_validacion": f0["nota_validacion"],
            })
    _hoja_tabla(wb, "Controles_CIS_a_Auditar", [
        ("benchmark", "Benchmark"), ("control_cis", "Control CIS"), ("titulo_cis", "Título CIS"),
        ("assessment_cis", "Tipo CIS"), ("pagina_cis", "Página CIS"),
        ("filas", "Controles Excel asociados"), ("criticidad", "Criticidad"),
        ("tipo_exclusion", "Tipo exclusión"), ("dominio_validacion", "Dominio"),
        ("tipo_validacion", "Tipo validación"), ("nota_validacion", "Nota validación"),
    ], unicos, plano)
    wb.save(ruta)
