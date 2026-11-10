"""Cálculo del porcentaje de cumplimiento (fórmulas: docs/Modelo_Resultados_y_Cumplimiento.pdf).

Pregunta que responde: "De todo lo que el proveedor indicó como implementado, ¿cuánto está
correctamente configurado?". Unidad: fila del Excel con propuesta YES, por host.

    Evaluables = CUMPLE + NO CUMPLE + CUMPLE PARCIALMENTE   (excluye conflicto de baseline)
    % cumplimiento CIS = CUMPLE / Evaluables × 100

Se muestran por separado: CUMPLE, NO CUMPLE, CUMPLE PARCIALMENTE, NO APLICA,
REQUIERE VALIDACIÓN MANUAL, REQUIERE EVIDENCIA ORGANIZACIONAL, ERROR DE AUDITORÍA,
FUERA DE ALCANCE (filas NO; no forman parte del universo) y CONFLICTO DE BASELINE.
"""

import collections
from typing import Callable, Dict, Iterable, List

from .base import CUMPLE, ERROR_R, FUERA_R, MANUAL_R, NO_APLICA, NO_CUMPLE, ORG_R, PARCIAL

ETIQUETA_CUMPLIMIENTO = "Cumplimiento CIS técnico"
ETIQUETA_COBERTURA = "Cobertura técnica CIS"
ETIQUETA_FINAL = "Cumplimiento final de línea base"
ETIQUETA_VALIDACION = "Cobertura de validación"

FORMULA = (
    "CUMPLIMIENTO CIS TÉCNICO = CUMPLE / (CUMPLE + NO CUMPLE + CUMPLE PARCIALMENTE) × 100, sobre resultado_cis "
    "(Resultado CIS técnico; no lo afectan exclusiones, instrumentación, CONTROL EXCLUIDO ni el resultado final). "
    "COBERTURA TÉCNICA CIS = (CUMPLE + NO CUMPLE + CUMPLE PARCIALMENTE) / filas YES × 100, sobre resultado_cis. "
    "Ambos indicadores se presentan siempre juntos. "
    "Unidad: fila del Excel con propuesta YES, por host. Fuera del denominador de cumplimiento (informados aparte): "
    "NO APLICA, ERROR DE AUDITORÍA, REQUIERE VALIDACIÓN MANUAL, REQUIERE EVIDENCIA ORGANIZACIONAL y CONFLICTO DE BASELINE. "
    "Las filas NO (FUERA DE ALCANCE) no forman parte del cálculo."
)

FORMULA_FINAL = (
    "CUMPLIMIENTO FINAL DE LÍNEA BASE = CUMPLE / (CUMPLE + NO CUMPLE + CUMPLE PARCIALMENTE + REQUIERE REMEDIACIÓN MANUAL + "
    "REQUIERE AJUSTE MANUAL) × 100, sobre resultado_final (incluye exclusiones de Infraestructura y excepciones aprobadas; los "
    "estados manuales son incumplimientos con acción pendiente; CONTROL EXCLUIDO y OMITIDO quedan fuera del denominador y nunca "
    "cuentan como CUMPLE). COBERTURA DE VALIDACIÓN = filas concluidas / filas YES × 100; concluida = resultado_final CUMPLE, "
    "NO CUMPLE, CUMPLE PARCIALMENTE, REQUIERE REMEDIACIÓN/AJUSTE MANUAL, NO APLICA, CONTROL EXCLUIDO u OMITIDO con evidencia/motivo trazable. "
    "Todas las filas YES permanecen en el denominador. No es el porcentaje CIS técnico: nunca se mezclan numeradores ni denominadores."
)

CONTROL_EXCLUIDO = "CONTROL EXCLUIDO"
OMITIDO = "OMITIDO"
REMEDIACION = "REQUIERE REMEDIACIÓN MANUAL"
AJUSTE = "REQUIERE AJUSTE MANUAL"
CATEGORIAS = [CUMPLE, NO_CUMPLE, PARCIAL, NO_APLICA, MANUAL_R, ORG_R, ERROR_R, FUERA_R, "CONFLICTO DE BASELINE"]
CATEGORIAS_FINAL = [CUMPLE, NO_CUMPLE, PARCIAL, REMEDIACION, AJUSTE, NO_APLICA, CONTROL_EXCLUIDO, OMITIDO, MANUAL_R, ORG_R,
                    ERROR_R, "CONFLICTO DE BASELINE"]


def _fmt(v):
    return "n/d" if v is None else f"{v:.1f} %".replace(".", ",")


def _pct(a, b):
    return round(100.0 * a / b, 1) if b else None


def categoria(r: Dict) -> str:
    """Categoría CIS técnica: exclusivamente resultado_cis (la capa final nunca interviene)."""
    return "CONFLICTO DE BASELINE" if r.get("conflicto_baseline") else r["resultado_cis"]


def categoria_final(r: Dict) -> str:
    return "CONFLICTO DE BASELINE" if r.get("conflicto_baseline") else (r.get("resultado_final") or r["resultado_cis"])


def resumir_final(resultados: Iterable[Dict]) -> Dict:
    """Cumplimiento final de línea base y cobertura de validación (sobre resultado_final). Indicadores propios."""
    resultados = list(resultados)
    c = collections.Counter(categoria_final(r) for r in resultados)
    ev = c[CUMPLE] + c[NO_CUMPLE] + c[PARCIAL] + c[REMEDIACION] + c[AJUSTE]
    concl = sum(1 for r in resultados if categoria_final(r) in (CUMPLE, NO_CUMPLE, PARCIAL, REMEDIACION, AJUSTE, NO_APLICA,
                                                                CONTROL_EXCLUIDO, OMITIDO)
                and (r.get("evidencia_motivo") or "").strip())
    n = len(resultados)
    return {"filas_yes": n, "conteo": {k: c.get(k, 0) for k in CATEGORIAS_FINAL}, "evaluables_final": ev,
            "cumplimiento_final_linea_base": _pct(c[CUMPLE], ev), "omitidos": c[OMITIDO], "controles_excluidos": c[CONTROL_EXCLUIDO],
            "concluidas": concl, "cobertura_validacion": _pct(concl, n),
            "tipo_cumplimiento": dict(collections.Counter(r.get("tipo_cumplimiento") for r in resultados)),
            "indicadores": f"{ETIQUETA_FINAL}: {_fmt(_pct(c[CUMPLE], ev))} ({c[CUMPLE]}/{ev}) · "
                           f"{ETIQUETA_VALIDACION}: {_fmt(_pct(concl, n))} ({concl}/{n} filas YES)",
            "formula": FORMULA_FINAL}


def resumir(resultados: Iterable[Dict], fuera: Iterable[Dict] = ()) -> Dict:
    """Indicadores CIS técnicos (resultado_cis)."""
    resultados, fuera = list(resultados), list(fuera)
    c = collections.Counter(categoria(r) for r in resultados)
    c[FUERA_R] = len(fuera)
    cumple, no, parc = c[CUMPLE], c[NO_CUMPLE], c[PARCIAL]
    evaluables = cumple + no + parc
    pct = _pct

    motivos = collections.Counter(r.get("motivo_resultado") for r in resultados if r.get("motivo_resultado"))
    return {
        "filas_yes_auditadas": len(resultados),
        "filas_no_fuera_de_alcance": len(fuera),
        "conteo": {k: c.get(k, 0) for k in CATEGORIAS},
        "evaluables": evaluables,
        "porcentaje_cumplimiento": pct(cumple, evaluables),
        "porcentaje_con_parciales": pct(cumple + parc, evaluables),
        "cobertura_tecnica": pct(evaluables, len(resultados)),
        "indicadores": f"{ETIQUETA_CUMPLIMIENTO}: {_fmt(pct(cumple, evaluables))} ({cumple}/{evaluables}) · "
                       f"{ETIQUETA_COBERTURA}: {_fmt(pct(evaluables, len(resultados)))} ({evaluables}/{len(resultados)} filas YES)",
        "no_evaluados_por_falta_de_privilegios": motivos.get("PERMISOS_INSUFICIENTES", 0),
        "pendientes_auditoria_profunda": motivos.get("PENDIENTE_AUDITORIA_PROFUNDA", 0),
        "pendientes_evidencia_organizacional": motivos.get("EVIDENCIA_ORGANIZACIONAL", 0),
        "motivos": dict(motivos),
        "formula": FORMULA,
    }


def resumir_por(resultados: List[Dict], fuera: List[Dict], clave: Callable) -> Dict[str, Dict]:
    g_r, g_f = collections.defaultdict(list), collections.defaultdict(list)
    for r in resultados:
        g_r[clave(r)].append(r)
    for f in fuera:
        g_f[clave(f)].append(f)
    return {k: resumir(g_r.get(k, []), g_f.get(k, [])) for k in sorted(set(g_r) | set(g_f))}


def resumir_exclusiones(resultados: List[Dict]) -> Dict:
    c = collections.Counter(r["exclusion"]["estado"] for r in resultados if r.get("exclusion"))
    tecnicas = c["CUMPLE"] + c["NO CUMPLE"] + c["PARCIAL"]
    val = collections.Counter((r.get("validacion_exclusion") or {}).get("estado") for r in resultados if r.get("exclusion_definida"))
    return {
        "conteo": dict(c),
        "definidas": sum(val.values()),
        "validacion": {k: val.get(k, 0) for k in ("APLICADA", "NO APLICADA", "NO VERIFICADA")},
        "porcentaje_exclusiones_tecnicas": round(100.0 * c["CUMPLE"] / tecnicas, 1) if tecnicas else None,
        "formula": "% exclusiones = CUMPLE / (CUMPLE + NO CUMPLE + PARCIAL) de exclusiones verificables técnicamente. "
                   "Se reporta por separado y nunca se mezcla con el porcentaje CIS.",
    }
