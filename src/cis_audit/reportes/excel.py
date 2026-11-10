"""Matriz Excel de trazabilidad y evidencias (desde el JSON de resultados): Parte 1 / Parte 2 / prerrequisitos.

Funciona también con resultados históricos de la Parte 2 sin campos de parte (se interpretan como PARTE_2/CIS
en memoria; el JSON no se modifica).
"""

import json
from pathlib import Path
from typing import Dict

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.utils import get_column_letter

from ..motor import partes
from ..motor.porcentaje import CATEGORIAS, resumir

_ENC = PatternFill("solid", fgColor="1F3864")
_FENC = Font(bold=True, color="FFFFFF")
_ADV = PatternFill("solid", fgColor="FFD8A8")
_COL = {"CUMPLE": "C6EFCE", "NO CUMPLE": "FFC7CE", "CUMPLE PARCIALMENTE": "FFEB9C", "PARCIAL": "FFEB9C",
        "REQUIERE VALIDACIÓN MANUAL": "FFF2CC", "REQUIERE EVIDENCIA ORGANIZACIONAL": "FFF2CC",
        "NO APLICA": "D9D9D9", "FUERA DE ALCANCE": "D9D9D9", "NINGUNA": "EDEDED", "RETIRADO EN CIS ACTUAL": "D9D9D9",
        "REQUIERE CONFIRMACIÓN": "FFF2CC", "PENDIENTE DE CONFIRMACIÓN": "FFF2CC", "PENDIENTE DE DEFINICIÓN": "FFF2CC",
        "ERROR DE AUDITORÍA": "E4D7F5", "CONFLICTO DE BASELINE": "D6EEF8", "CONTROL EXCLUIDO": "BFBFBF",
        "APLICADA": "C6EFCE", "NO APLICADA": "FFC7CE", "NO VERIFICADA": "D9D9D9", "REQUIERE REMEDIACIÓN MANUAL": "FFEB9C",
        "REQUIERE AJUSTE MANUAL": "FFEB9C", "OMITIDO": "BFBFBF"}

_ID = [("_etiqueta", "Etiqueta"), ("parte_proyecto", "Parte Proyecto"), ("tipo_control", "Tipo Control"), ("_fuente", "Fuente Requisito")]
# Orden: lo principal primero (Resultado cerca del inicio); secundario después; técnico/raw al final.
COLUMNAS = [
    ("_etiqueta", "Parte"), ("control_excel", "Control Excel"), ("control_cis", "Control CIS"), ("_descripcion", "Descripción"),
    ("criticidad", "Criticidad"), ("resultado_final", "Resultado"), ("tratamiento", "Tratamiento"), ("evidencia_motivo", "Evidencia / Motivo"),
    ("_ficha", "Evidencia organizada (resultado, CIS, dónde se buscó, qué se encontró, exclusión, qué falta)"),
    ("_donde", "Dónde se buscó"), ("_exc", "Exclusión de Infraestructura"), ("_validacion", "Validación de la exclusión en el servidor"),
    ("detalle_exclusion_texto", "Detalle de exclusión"),
    ("resultado_cis_tecnico", "Resultado CIS técnico"), ("tipo_cumplimiento", "Tipo de cumplimiento"), ("instrumentacion_texto", "Instrumentación"),
    ("hallazgos_no_cubiertos", "Hallazgos no cubiertos"), ("_politica", "Fuente de política"), ("_comparacion", "Comparación"),
    ("observacion", "Observación (línea base)"), ("que_falta", "Qué falta"), ("_componentes", "Componentes evaluados"),
    ("_omision", "Exclusión / omisión"), ("hallazgos_cubiertos", "Hallazgos cubiertos"),
    ("host", "Host"), ("sistema_operativo", "Sistema operativo"), ("version", "Versión"), ("benchmark", "Benchmark"),
    ("seccion_excel", "Sección"), ("titulo_excel", "Título Excel"),
    ("titulo_cis", "Título CIS"), ("propuesta_proveedor", "Propuesta proveedor / playbook"), ("valor_esperado_cis", "Requisito esperado (CIS)"),
    ("encontrado", "Valor encontrado"), ("referencia_cis", "Referencia CIS"), ("hallazgo", "Hallazgo técnico"),
    ("motivo_resultado", "Código interno"), ("tipo_parcial", "Tipo parcial"), ("conflicto_baseline", "Conflicto baseline"),
    ("computa_porcentaje", "Computa en % CIS técnico"), ("computa_porcentaje_final", "Computa en % final"),
    ("_excl_texto", "Exclusión (texto de la fuente)"), ("resultado_exclusion_infraestructura", "Estado de la condición de exclusión"),
    ("_detalle_exclusion", "Detalle técnico de la exclusión"), ("_instr", "Instrumentación (técnico)"),
    ("componentes_fuera_de_alcance", "Componentes fuera de alcance"), ("observaciones", "Observaciones del auditor"),
    ("adaptacion_procedimiento_auditoria", "Adaptación procedimiento Audit"), ("_adaptacion", "Detalle adaptación"),
    ("version_cis", "Versión CIS"), ("pagina_cis", "Página CIS"), ("origen_timestamp_timeout", "Origen timestamp_timeout"),
    ("valor_timestamp_timeout", "Valor timestamp_timeout"), ("ruta_evidencia", "Evidencia (ruta)"), ("_evidence_ids", "evidence_id"),
] + [("parte_proyecto", "Parte Proyecto"), ("tipo_control", "Tipo Control"), ("_fuente", "Fuente Requisito")]
COLUMNAS_PRE = [
    ("control_excel", "ID"), ("titulo_excel", "Descripción"), ("resultado_final", "Resultado"), ("evidencia_motivo", "Evidencia / Motivo"),
    ("fuente_evidencia", "Fuente de evidencia"), ("_etiqueta", "Parte"), ("criterio", "Criterio"), ("host", "Host"), ("benchmark", "Benchmark"),
    ("_evidence_ids", "evidence_id"), ("observaciones", "Observaciones"),
] + [("parte_proyecto", "Parte Proyecto"), ("tipo_control", "Tipo Control"), ("_fuente", "Fuente Requisito")]
COLUMNAS_EXC = [
    ("_etiqueta", "Parte"), ("control_excel", "Control Excel"), ("control_cis", "Control CIS"), ("_descripcion", "Descripción"),
    ("_definida", "Exclusión de Infraestructura"), ("_validacion", "Resultado de validación"), ("_advertencias_val", "Advertencias"), ("_tipo", "Tipo"),
    ("_esperado_val", "Exclusión esperada"), ("_encontrado_val", "Configuración real encontrada"),
    ("_elementos_val", "Detalle por elemento (usuario / regla)"),
    ("_observacion_val", "Observación"), ("_autorizados", "Elementos autorizados"),
    ("_hallazgos", "Hallazgos encontrados"), ("_cubiertos", "Hallazgos cubiertos por la exclusión"), ("_no_cubiertos", "Hallazgos no cubiertos"),
    ("_motivo_exc", "Motivo"), ("host", "Host"), ("parte_proyecto", "Parte Proyecto"), ("tipo_control", "Tipo Control"), ("_fuente", "Fuente Requisito"),
]
COLUMNAS_PEND = [
    ("_etiqueta", "Parte"), ("_categoria", "Clasificación"), ("control_excel", "Control Excel"), ("control_cis", "Control CIS"),
    ("_descripcion", "Descripción"), ("criticidad", "Criticidad"), ("resultado_final", "Resultado"), ("evidencia_motivo", "Evidencia / Motivo"),
    ("que_falta", "Qué falta"), ("host", "Host"), ("motivo_resultado", "Código interno"),
    ("parte_proyecto", "Parte Proyecto"), ("tipo_control", "Tipo Control"), ("_fuente", "Fuente Requisito"),
]


def _plano(v):
    if isinstance(v, bool):
        return "SÍ" if v else "NO"
    if isinstance(v, (list, dict)):
        return " || ".join(map(str, v)) if isinstance(v, list) else json.dumps(v, ensure_ascii=False)
    return v


def _fila(r: Dict) -> Dict:
    d = partes.normalizar_fila(r)
    d["_etiqueta"] = partes.etiqueta(d)
    from . import presentacion as pres
    d["_fuente"] = d.get("fuente_requisito") or pres.LINEA_BASE.get(d.get("parte_proyecto"))
    ex = r.get("exclusion") or {}
    d["_excl_texto"], d["_excl_tipo"], d["_excl_que_falta"] = pres.texto_exclusion(ex.get("texto")) or None, ex.get("tipo"), ex.get("que_falta")
    if d.get("exclusion_infraestructura"):
        d["exclusion_infraestructura"] = pres.texto_exclusion(d["exclusion_infraestructura"])
    a = r.get("adaptacion")
    d["_adaptacion"] = (f"p.{a.get('pagina')} | original: {a['procedimiento_original']} | adaptación: {a['adaptacion']} | "
                        f"justificación: {a['justificacion']}") if a else None
    ins = r.get("instrumentacion_auditoria") or {}
    d["_instr"] = (f"detectada={'SÍ' if ins.get('instrumentacion_auditoria_detectada') else 'NO'}; "
                   f"excluida_del_resultado_proveedor={'SÍ' if ins.get('instrumentacion_excluida_del_resultado_proveedor') else 'NO'}; "
                   f"archivo={ins.get('archivo')}; temporal=SÍ"
                   f"; sha256_sudoers_archivo encontrado={ins.get('sudoers_sha256_archivo_encontrado')} aprobado={ins.get('sudoers_sha256_archivo_aprobado')}"
                   f"; sha256_sudoers_semantico encontrado={ins.get('sudoers_sha256_semantico_encontrado')}"
                   + (f"; motivo: {ins['motivo_no_excluida']}" if ins.get("motivo_no_excluida") else "")
                   ) if ins else None
    cub = ((r.get("detalle_exclusion") or {}).get("elementos_por_instrumentacion")) or []
    if cub:
        d["_instr"] = (d["_instr"] or "") + "; INSTRUMENTACION_AUDITORIA: " + ", ".join(
            f"{e.get('usuario')} ({e.get('archivo')}:{e.get('linea')})" for e in cub)
    d["_evidence_ids"] = r.get("evidence_ids")
    d["resultado_cis_tecnico"] = r.get("resultado_cis_tecnico") or r.get("resultado_cis")
    d["resultado_final"] = r.get("resultado_final") or r.get("resultado_cis")
    d["_descripcion"] = r.get("titulo_cis") or r.get("titulo_excel")
    cb = (r.get("mecanismo") or {}).get("comparacion_banner") or {}
    from .html import _ESTADO_COMP, _omision, fuente_politica
    d["_politica"], d["_comparacion"] = fuente_politica(r) or None, cb.get("comparacion")
    d["_componentes"] = "\n".join(f"{c.get('descripcion')}: {_ESTADO_COMP.get(c.get('estado'), c.get('estado'))} — {c.get('encontrado')}"
                                  for c in r.get("componentes") or [])[:32000] or None
    d["_omision"] = _omision(r)
    d["_exc"] = "Definida" if r.get("exclusion_definida") else "—"
    d["_validacion"] = (r.get("validacion_exclusion") or {}).get("estado") if r.get("exclusion_definida") else None
    d["_detalle_exclusion"] = json.dumps(pres.sin_referencias(r["detalle_exclusion"]), ensure_ascii=False) if r.get("detalle_exclusion") else None
    d["_ficha"], d["_donde"] = pres.ficha_texto(r), pres.donde_texto(r) or None
    d["tipo_cumplimiento"] = pres.tipo_cumplimiento(r) or None
    d["evidencia_motivo"] = pres.texto_lista(r.get("evidencia_motivo")) or r.get("evidencia_motivo")
    return d


def _mapeo_destacado(ws, titulo: str) -> None:
    """'Mapeo CIS: X — …' con 'Mapeo CIS: X' en negrilla y subrayado (texto enriquecido en la celda)."""
    col = next((c.column for c in ws[1] if c.value == titulo), None)
    if not col:
        return
    for (c,) in ws.iter_rows(min_row=2, min_col=col, max_col=col):
        v = c.value
        if isinstance(v, str) and v.startswith("Mapeo CIS: ") and " — " in v:
            cab, resto = v.split(" — ", 1)
            c.value = CellRichText(TextBlock(InlineFont(b=True, u="single"), cab), " — " + resto)


def _hoja(wb, nombre, columnas, filas):
    ws = wb.create_sheet(nombre)
    ws.append([t for _, t in columnas])
    for c in ws[1]:
        c.fill, c.font = _ENC, _FENC
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for f in filas:
        ws.append([_plano(f.get(k)) for k, _ in columnas])
    largos = {"_ficha": 95, "_donde": 55, "_motivo_legible": 70, "_autorizados": 40, "_hallazgos": 45, "_cubiertos": 45, "_no_cubiertos": 35, "evidencia_motivo": 70, "_motivo_exc": 70, "_descripcion": 45, "titulo_excel": 45, "titulo_cis": 45, "detalle_exclusion_texto": 40,
              "_definido": 40, "_encontrado": 40, "hallazgos_no_cubiertos": 45, "observacion": 40, "que_falta": 45, "valor_esperado_cis": 40,
              "encontrado": 50, "hallazgo": 50, "observaciones": 50, "_excl_texto": 45, "_adaptacion": 40, "_detalle_exclusion": 40,
              "_origen": 35, "_instr": 35, "_esperado_val": 45, "_encontrado_val": 50, "_elementos_val": 60,
              "_observacion_val": 50, "_evidencia_val": 25, "_advertencias_val": 40, "tratamiento": 22}
    for i, (k, t) in enumerate(columnas, start=1):
        ws.column_dimensions[get_column_letter(i)].width = largos.get(k, 14 if k in ("_etiqueta", "control_excel", "control_cis", "criticidad", "fila_excel") else 18)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    for i, (k, _) in enumerate(columnas, start=1):
        if k == "_advertencias_val":  # advertencia (recomendación no cumplida): naranja
            for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                if row[0].value:
                    row[0].fill, row[0].font = _ADV, Font(color="9C4A00", bold=True)
    if filas:
        ws.auto_filter.ref = ws.dimensions
        for i, (k, _) in enumerate(columnas, start=1):
            if k in ("resultado_cis", "resultado_exclusion_infraestructura", "resultado_tecnico_observado", "resultado_linea_base_interna",
                     "resultado", "resultado_cis_tecnico", "resultado_final", "_validacion", "tratamiento"):
                L = get_column_letter(i)
                for v, color in _COL.items():
                    ws.conditional_formatting.add(f"{L}2:{L}{ws.max_row}", FormulaRule(
                        formula=[f'{L}2="{v}"'], fill=PatternFill("solid", fgColor=color)))
    return ws


def _cabecera(ws, valores):
    ws.append(valores)
    for c in ws[ws.max_row]:
        c.fill, c.font = _ENC, _FENC


def _tabla_resumen(ws, titulo: str, grupos: Dict[str, Dict]):
    ws.append([titulo])
    ws[f"A{ws.max_row}"].font = Font(bold=True)
    _cabecera(ws, ["Grupo", "Filas en alcance"] + CATEGORIAS + ["Cumplimiento CIS técnico (%)", "Cobertura técnica CIS (%)",
                                                                 "% con parciales"])
    for k, v in grupos.items():
        ws.append([k, v["filas_yes_auditadas"]] + [v["conteo"].get(c, 0) for c in CATEGORIAS]
                  + [v["porcentaje_cumplimiento"], v["cobertura_tecnica"], v["porcentaje_con_parciales"]])
    ws.append([])


def indicadores_de(datos: Dict) -> Dict:
    """Bloques por parte (los resultados históricos sin este bloque se calculan en memoria)."""
    r = datos["resumen"]
    if r.get("por_parte"):
        return r["por_parte"]
    return partes.indicadores(datos["resultados"], resumir, datos.get("fuera_de_alcance") or [])


def unicos_de(datos: Dict) -> Dict:
    r = datos["resumen"]
    if r.get("controles_cis_unicos"):
        return r["controles_cis_unicos"]
    return partes.controles_cis_unicos([x for x in datos["resultados"] if partes.normalizar_fila(x)["tipo_control"] == partes.CIS])


def generar(datos: Dict, ruta: Path, demo: bool = False) -> None:
    ind = indicadores_de(datos)
    uni = unicos_de(datos)
    res = sorted((_fila(r) for r in datos["resultados"]), key=partes.orden_fila)
    cis = [r for r in res if r["tipo_control"] == partes.CIS]
    p1 = [r for r in cis if r["parte_proyecto"] == partes.PARTE_1]
    p2 = [r for r in cis if r["parte_proyecto"] == partes.PARTE_2]
    pre = [r for r in res if r["tipo_control"] == partes.PRERREQUISITO]

    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen_Global"
    ws.append(["Auditoría Completa de Hardening CIS Linux — matriz de trazabilidad" + (" (DEMOSTRACIÓN — DATOS SINTÉTICOS)" if demo else "")])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Ejecución: {datos['run_id']} · generado {datos['generado']}"])
    ws.append([])
    from .html import pendientes as _pend
    fin = (datos["resumen"].get("final") or {}).get("global")
    g0 = ind["cis_global"]
    _cabecera(ws, ["RESUMEN EJECUTIVO", "Valor"])
    fecha = next((r.get("fecha_evidencia") for r in cis if r.get("fecha_evidencia")), None) or datos.get("generado")
    for h in datos["hosts"]:
        for k, v in (("Host", f"{h['host']} ({h['sistema'].get('hostname') or '—'})"), ("Dirección IP", h["sistema"].get("ip") or "—"), ("Sistema operativo", h["sistema"].get("nombre_completo")),
                     ("Benchmark", h.get("benchmark"))):
            ws.append([k, v])
    filas_rs = [("Fecha de la evidencia", fecha), ("Controles P1", len(p1)), ("Controles P2", len(p2)), ("Total CIS en alcance", len(cis)),
                ("Cumplimiento CIS técnico (%)", f"{g0['porcentaje_cumplimiento']} ({g0['conteo'].get('CUMPLE', 0)}/{g0['evaluables']})"),
                ("Cobertura técnica CIS (%)", f"{g0['cobertura_tecnica']} ({g0['evaluables']}/{g0['filas_yes_auditadas']})")]
    if fin:
        filas_rs += [("Cumplimiento final de línea base (%)", f"{fin['cumplimiento_final_linea_base']} ({fin['conteo'].get('CUMPLE', 0)}/{fin['evaluables_final']})"),
                     ("Cobertura de validación (%)", f"{fin['cobertura_validacion']} ({fin['concluidas']}/{fin['filas_yes']})")]
    filas_rs += [("NO CUMPLE (resultado final)", sum(1 for r in cis if r["resultado_final"] == "NO CUMPLE")), ("Pendientes", len(_pend(cis))),
                 ("REQUIERE REMEDIACIÓN MANUAL", sum(1 for r in cis if r["resultado_final"] == "REQUIERE REMEDIACIÓN MANUAL")),
                 ("REQUIERE AJUSTE MANUAL", sum(1 for r in cis if r["resultado_final"] == "REQUIERE AJUSTE MANUAL")),
                 ("CONTROL EXCLUIDO", sum(1 for r in cis if r["resultado_final"] == "CONTROL EXCLUIDO")),
                 ("OMITIDO", sum(1 for r in cis if r["resultado_final"] == "OMITIDO")),
                 ("Errores de auditoría", sum(1 for r in cis if r["resultado_final"] == "ERROR DE AUDITORÍA"))]
    filas_rs += [(f"{r['control_excel']} (prerrequisito interno, no CIS)", r["resultado_final"]) for r in pre]
    for k, v in filas_rs:
        ws.append([k, v])
    ws.append([])
    _cabecera(ws, ["Indicador", "Cumplimiento CIS técnico (%)", "Cobertura técnica CIS (%)", "Evaluados", "En alcance"])
    for k in ("parte_1", "parte_2", "cis_global"):
        v = ind[k]
        ws.append([ind["etiquetas"][k], v["porcentaje_cumplimiento"], v["cobertura_tecnica"], v["evaluables"], v["filas_yes_auditadas"]])
    ws.append(["Controles CIS únicos evaluables (denominador propio)", uni["porcentaje_cumplimiento"], uni["cobertura_tecnica"], uni["evaluables"],
               uni.get("unicos_evaluables", uni["total"])])
    ws.append(["Controles CIS únicos en alcance / evaluables / conflictos de baseline",
               None, None, None, f"{uni.get('unicos_en_alcance', uni['total'])} / {uni.get('unicos_evaluables', uni['total'])} / "
               f"{uni.get('conflictos_baseline', 0)}"])
    v = ind["global_operativo"]
    ws.append([ind["etiquetas"]["global_operativo"], v["porcentaje_cumplimiento"], v["cobertura_tecnica"], v["evaluables"], v["filas_yes_auditadas"]])
    ws.append([])
    ws.append([f"Fórmula: {ind['cis_global']['formula']}"])
    fin = datos["resumen"].get("final")
    if fin:
        ws.append([])
        _cabecera(ws, ["Resultado final de auditoría (no es CIS técnico)", "Cumplimiento final de línea base (%)", "Cobertura de validación (%)",
                       "CUMPLE / evaluables finales", "Concluidas / filas YES", "CONTROL EXCLUIDO", "OMITIDO"])
        for k, t in (("global", "Final P1 + P2"), ("parte_1", "Final Parte 1"), ("parte_2", "Final Parte 2")):
            v = fin[k]
            ws.append([t, v["cumplimiento_final_linea_base"], v["cobertura_validacion"], f"{v['conteo'].get('CUMPLE', 0)}/{v['evaluables_final']}",
                       f"{v['concluidas']}/{v['filas_yes']}", v["controles_excluidos"], v["omitidos"]])
        ws.append([f"Fórmula: {fin['global']['formula']}"])
    ws.append(["CIS Global y Parte 1/2 = solo controles CIS (resultados por filas del proyecto). 'Controles CIS únicos' cuenta cada control CIS "
               "una vez por host (p. ej. 7.1.11 en P1 y P2). El Cumplimiento Global Operativo incluye prerrequisitos internos y NO es el porcentaje CIS."])
    ws.append([])
    pr = ind["prerrequisitos_parte_1"]
    _cabecera(ws, ["Prerrequisitos Parte 1 (requisito interno, no CIS)"] + list(pr["conteo"]) + ["Requieren excepción de Seguridad"])
    ws.append(["Conteo"] + list(pr["conteo"].values()) + [pr["requieren_excepcion_seguridad"]])
    ws.append([])
    _tabla_resumen(ws, "CIS GLOBAL POR BENCHMARK", datos["resumen"]["por_benchmark"])
    _tabla_resumen(ws, "CIS GLOBAL POR HOST", datos["resumen"]["por_host"])
    ws.append(["Exclusiones", json.dumps(datos["resumen"]["exclusiones"]["conteo"], ensure_ascii=False),
               datos["resumen"]["exclusiones"]["porcentaje_exclusiones_tecnicas"]])
    if datos.get("parte1_no_confirmada"):
        ws.append([f"Parte 1 no confirmada: {len(datos['parte1_no_confirmada'])} filas REQUIERE CONFIRMACIÓN (no incluidas en resultados ni porcentajes)."])
    ws.column_dimensions["A"].width = 60
    for nombre, filas, bloque in (("Resumen_Parte1", p1, "parte_1"), ("Resumen_Parte2", p2, "parte_2")):
        w = wb.create_sheet(nombre)
        w.append([ind["etiquetas"][bloque]])
        w["A1"].font = Font(bold=True, size=13)
        _tabla_resumen(w, "TOTAL", {nombre: ind[bloque]})
        fuera = [partes.normalizar_fila(f) for f in datos.get("fuera_de_alcance") or []]
        fuera = [f for f in fuera if f["parte_proyecto"] == (partes.PARTE_1 if bloque == "parte_1" else partes.PARTE_2)]
        por_b = {}
        for b in sorted({r["benchmark"] for r in filas}):
            por_b[b] = resumir([r for r in filas if r["benchmark"] == b], [f for f in fuera if f["benchmark"] == b])
        _tabla_resumen(w, "POR BENCHMARK", por_b)
        por_h = {h: resumir([r for r in filas if r["host"] == h], [f for f in fuera if f["host"] == h]) for h in sorted({r["host"] for r in filas})}
        _tabla_resumen(w, "POR HOST", por_h)
        if bloque == "parte_1":
            _cabecera(w, ["Prerrequisitos Parte 1 (bloque propio; no CIS)"] + list(pr["conteo"]))
            w.append(["Conteo"] + list(pr["conteo"].values()))
        w.column_dimensions["A"].width = 30
    _hoja(wb, "Parte1_Controles", COLUMNAS, p1)
    _hoja(wb, "Parte1_Prerequisitos", COLUMNAS_PRE, pre)
    _hoja(wb, "Parte2_Controles", COLUMNAS, p2)
    from .html import categoria_pendiente, filas_exclusion, pendientes
    _hoja(wb, "Exclusiones_Infraestructura", COLUMNAS_EXC,
          [{**r, "_definida": "Definida"} for r in filas_exclusion(cis)])
    _hoja(wb, "Pendientes", COLUMNAS_PEND, [{**r, "_categoria": categoria_pendiente(r)} for r in
                                            sorted(pendientes(cis), key=lambda r: (categoria_pendiente(r), partes.orden_fila(r)))])
    _hoja(wb, "Conflictos_Baseline", COLUMNAS, [r for r in cis if r.get("conflicto_baseline")])
    _hoja(wb, "Controles_CIS_Unicos", [("host", "Host"), ("benchmark", "Benchmark"), ("control_cis", "Control CIS"), ("resultado", "Resultado"),
                                       ("partes", "Partes que lo exigen")], uni["detalle"])
    evid = [{**{k: r[k] for k in ("_etiqueta", "parte_proyecto", "tipo_control", "_fuente")}, "host": r["host"],
             "control_excel": r.get("control_excel"), "control_cis": r["control_cis"], "comando_id": e["comando_id"], "evidence_id": partes.evidence_id(r["benchmark"], e["comando_id"], r["host"]),
             "rc": e["rc"], "sudo": e["sudo"], "omitido": e["omitido"], "comando": e["comando"], "ruta": r.get("ruta_evidencia")}
            for r in res for e in r.get("evidencia") or []]
    _hoja(wb, "Evidencias", _ID + [("host", "Host"), ("control_excel", "Control Excel"), ("control_cis", "Control CIS"), ("comando_id", "Comando"),
                                   ("evidence_id", "evidence_id (compartido entre partes)"), ("rc", "rc"), ("sudo", "sudo"),
                                   ("omitido", "Omitido"), ("comando", "Comando ejecutado"), ("ruta", "Ruta evidencia")], evid)
    fuera = []
    for f in sorted(datos["fuera_de_alcance"], key=partes.orden_fila):
        d = partes.normalizar_fila(f)
        from . import presentacion as pres
        d["_etiqueta"], d["_fuente"] = partes.etiqueta(d), pres.LINEA_BASE.get(d.get("parte_proyecto"))
        d["exclusion_infraestructura"] = pres.texto_exclusion(d.get("exclusion_infraestructura")) or None
        d["_motivo_legible"], d["_mapeo_legible"] = pres.motivo_fuera(d), pres.explicar_mapeo(d.get("estado_mapeo"))
        d["resultado_cis"] = pres.estado_fuera(d)
        fuera.append(d)
    hoja_fuera = _hoja(wb, "Fuera_Alcance", _ID + [("host", "Host"), ("benchmark", "Benchmark"),
                                      ("control_excel", "Control Excel"), ("titulo_excel", "Título Excel"), ("control_cis", "Control CIS"),
                                      ("titulo_cis", "Título CIS"), ("propuesta_proveedor", "Propuesta"), ("resultado_cis", "Estado"),
                                      ("estado_mapeo", "Estado mapeo"), ("_motivo_legible", "Motivo"), ("_mapeo_legible", "Significado del estado de mapeo"),
                                      ("exclusion_infraestructura", "Exclusión")], fuera)
    _mapeo_destacado(hoja_fuera, "Motivo")
    errores = [r for r in res if r["resultado_cis"] == "ERROR DE AUDITORÍA"]
    errores += [{"host": h["host"], "titulo_cis": "(host)", "que_falta": "; ".join(h["errores"])} for h in datos["hosts"] if h["errores"]]
    _hoja(wb, "Errores_Auditoria", COLUMNAS, errores)
    orden = ["Resumen_Global", "Resumen_Parte1", "Resumen_Parte2", "Parte1_Controles", "Parte1_Prerequisitos", "Parte2_Controles",
             "Exclusiones_Infraestructura", "Pendientes", "Evidencias", "Errores_Auditoria", "Fuera_Alcance", "Conflictos_Baseline",
             "Controles_CIS_Unicos"]
    wb._sheets.sort(key=lambda w: orden.index(w.title) if w.title in orden else len(orden))
    ruta.parent.mkdir(parents=True, exist_ok=True)
    wb.save(ruta)
