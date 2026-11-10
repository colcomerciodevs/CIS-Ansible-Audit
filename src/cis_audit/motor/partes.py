"""Identificación Parte 1 / Parte 2 del proyecto de hardening.

Toda fila (matriz de alcance, resultado, evidencia, hallazgo) lleva:
    parte_proyecto   PARTE_1 | PARTE_2
    tipo_control     CIS | PRERREQUISITO
    fuente_linea_base / fuente_requisito

Las filas de la matriz de la Parte 2 no traen estos campos: `normalizar_fila` los completa como PARTE_2/CIS en memoria.

Indicadores (nunca se mezclan silenciosamente):
    Cumplimiento/Cobertura Parte 1, Parte 2 y CIS Global  -> solo tipo_control = CIS
    Prerrequisitos Parte 1                                 -> aparte (no son CIS)
    Cumplimiento Global Operativo                          -> CIS + prerrequisitos, etiquetado explícitamente
"""

import hashlib
import re
from typing import Callable, Dict, Iterable, List, Optional, Tuple

PARTE_1, PARTE_2 = "PARTE_1", "PARTE_2"
PARTES = (PARTE_1, PARTE_2)
CIS, PRERREQUISITO = "CIS", "PRERREQUISITO"


def clave_control(identificador) -> Tuple:
    """Clave de orden NUMÉRICO jerárquico de un identificador CIS/Excel: '4.2.9' < '4.2.10' < '4.2.11' < '6.3.3.19'.

    Cada componente separado por puntos se compara como entero; un componente no numérico (p. ej. 'PRE-1', '1.1.x') va
    después de los numéricos del mismo nivel. Con varios controles ('5.4.2.7, 5.4.2.8') ordena por el primero. Un
    identificador vacío va al final. El identificador original no se modifica."""
    s = str(identificador or "").split(",")[0].strip()
    if not s:
        return ((2, 0, ""),)
    return tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"[.\s]+", s) if p)


def orden_fila(r: Dict) -> Tuple:
    """Orden de presentación de una fila: host, parte (P1, P2), tipo (CIS, PRERREQUISITO), control Excel y control CIS en
    orden numérico jerárquico; la fila del Excel solo desempata."""
    try:
        fila = int(r.get("fila_excel") or 0)
    except (TypeError, ValueError):
        fila = 0
    return (r.get("host") or "", r.get("parte_proyecto") or "", r.get("tipo_control") or CIS,
            clave_control(r.get("control_excel")), clave_control(r.get("control_cis")), fila)
TIPOS = (CIS, PRERREQUISITO)
INSIGNIA = {PARTE_1: "P1", PARTE_2: "P2"}
INSIGNIA_TIPO = {CIS: "CIS", PRERREQUISITO: "PRE"}

FUENTE_P1 = "Linea Base - Analisis de Impacto _Parte1.xlsx"
FUENTE_P2 = "Linea Base - Analisis de Impacto _Parte2.xlsx"
REQUISITO_INTERNO = "REQUISITO_INTERNO"
FUENTE_PRE = REQUISITO_INTERNO            # fuente_requisito de los PRE (config/prerrequisitos_parte1.yml)

# Estados propios de los prerrequisitos internos (no son estados CIS).
PENDIENTE_CONFIRMACION = "PENDIENTE DE CONFIRMACIÓN"
PENDIENTE_DEFINICION = "PENDIENTE DE DEFINICIÓN"


def normalizar_fila(fila: Dict) -> Dict:
    """Devuelve la fila con parte_proyecto/tipo_control/fuente. Sin campos = Parte 2 (histórico)."""
    f = dict(fila)
    f.setdefault("parte_proyecto", PARTE_2)
    f.setdefault("tipo_control", CIS)
    if f["parte_proyecto"] not in PARTES:
        raise ValueError(f"parte_proyecto inválida: {f['parte_proyecto']!r}")
    if f["tipo_control"] not in TIPOS:
        raise ValueError(f"tipo_control inválido: {f['tipo_control']!r}")
    if f["tipo_control"] == PRERREQUISITO:
        if f["parte_proyecto"] != PARTE_1:
            raise ValueError("Los prerrequisitos solo existen en la Parte 1")
        f.setdefault("fuente_requisito", FUENTE_PRE)
        f.setdefault("origen_requisito", REQUISITO_INTERNO)
    else:
        f.setdefault("fuente_linea_base", FUENTE_P1 if f["parte_proyecto"] == PARTE_1 else FUENTE_P2)
    return f


def etiqueta(fila: Dict) -> str:
    """Texto explícito (no solo color) para HTML/Excel/exportación: '[P1][CIS] 5.3.1' o '[P1][PRE] PRE-1 — PRERREQUISITO INTERNO'."""
    f = normalizar_fila(fila)
    base = f"[{INSIGNIA[f['parte_proyecto']]}][{INSIGNIA_TIPO[f['tipo_control']]}] {f.get('control_excel') or ''}".rstrip()
    return base + (" — PRERREQUISITO INTERNO" if f["tipo_control"] == PRERREQUISITO else "")


def filtrar(filas: Iterable[Dict], **criterios) -> List[Dict]:
    """Filtro común de reportes. Criterios: parte_proyecto, tipo_control, benchmark, host, perfil_auditoria,
    seccion, control, resultado, requiere_sudo, profunda, fuente (fuente_linea_base o fuente_requisito/origen).
    Un valor lista/tupla/set admite varios valores."""
    alias = {"seccion": "seccion_excel", "control": "control_excel", "resultado": "resultado_cis",
             "profunda": "requiere_filesystem_profundo"}
    res = []
    for fila in filas:
        f = normalizar_fila(fila)
        ok = True
        for k, v in criterios.items():
            if v is None:
                continue
            if k == "fuente":
                valores = {f.get("fuente_linea_base"), f.get("fuente_requisito"), f.get("origen_requisito")}
                ok = bool(valores & (set(v) if isinstance(v, (list, tuple, set)) else {v}))
            else:
                actual = f.get(alias.get(k, k))
                ok = actual in v if isinstance(v, (list, tuple, set)) else actual == v
            if not ok:
                break
        if ok:
            res.append(f)
    return res


ESTADOS_PRE_RESUMEN = ("CUMPLE", "NO CUMPLE", "NO APLICA", "PENDIENTE", "ERROR DE AUDITORÍA")


def resumen_prerrequisitos(filas: Iterable[Dict]) -> Dict:
    import collections
    filas = list(filas)
    c = collections.Counter("PENDIENTE" if f["resultado_cis"] in (PENDIENTE_CONFIRMACION, PENDIENTE_DEFINICION) else f["resultado_cis"]
                            for f in filas)
    return {"total": len(filas), "conteo": {e: c.get(e, 0) for e in ESTADOS_PRE_RESUMEN},
            "requieren_excepcion_seguridad": sum(1 for f in filas if f.get("requiere_excepcion_seguridad")),
            "nota": "Requisitos internos (no CIS). No forman parte del Cumplimiento CIS de la Parte 1 ni del CIS Global."}


def indicadores(resultados: Iterable[Dict], resumir: Callable, fuera: Iterable[Dict] = ()) -> Dict:
    """Indicadores separados. `resumir` es porcentaje.resumir (misma fórmula aprobada para cada bloque).

    - parte_1 / parte_2 / cis_global: solo controles CIS (filas del proyecto).
    - prerrequisitos_parte_1: solo PRERREQUISITO (requisito interno, no CIS), con estados propios.
    - global_operativo: CIS + PRERREQUISITO, etiquetado como 'Cumplimiento Global Operativo' (NO es el porcentaje CIS)."""
    filas = [normalizar_fila(r) for r in resultados]
    fuera = [normalizar_fila(f) for f in fuera]
    cis = [r for r in filas if r["tipo_control"] == CIS]
    pre = [r for r in filas if r["tipo_control"] == PRERREQUISITO]
    return {
        "parte_1": resumir([r for r in cis if r["parte_proyecto"] == PARTE_1], [f for f in fuera if f["parte_proyecto"] == PARTE_1]),
        "parte_2": resumir([r for r in cis if r["parte_proyecto"] == PARTE_2], [f for f in fuera if f["parte_proyecto"] == PARTE_2]),
        "cis_global": resumir(cis, fuera),
        "prerrequisitos_parte_1": resumen_prerrequisitos(pre),
        "global_operativo": resumir(filas, fuera),
        "etiquetas": {
            "parte_1": "Cumplimiento Parte 1 (CIS)", "parte_2": "Cumplimiento Parte 2 (CIS)",
            "cis_global": "Cumplimiento CIS Global (Parte 1 + Parte 2, por filas del proyecto)",
            "prerrequisitos_parte_1": "Prerrequisitos Parte 1 (requisito interno, no CIS)",
            "global_operativo": "Cumplimiento Global Operativo (CIS + prerrequisitos internos; NO es el porcentaje CIS)",
        },
    }


_PEOR = ("NO CUMPLE", "CUMPLE PARCIALMENTE", "ERROR DE AUDITORÍA", "REQUIERE VALIDACIÓN MANUAL",
         "REQUIERE EVIDENCIA ORGANIZACIONAL", "CUMPLE", "NO APLICA")


def controles_cis_unicos(resultados_cis: Iterable[Dict]) -> Dict:
    """Indicador 'Controles CIS únicos': cada (host, benchmark, control CIS) cuenta una vez aunque lo exijan
    filas de la Parte 1 y de la Parte 2 (p. ej. 7.1.11). Si las filas difieren, prevalece el resultado más
    desfavorable. Denominador distinto del de 'Resultados por filas del proyecto': no se mezclan."""
    import collections
    grupos: Dict = collections.defaultdict(list)
    en_conflicto = set()
    for r in resultados_cis:
        r = normalizar_fila(r)
        if r.get("conflicto_baseline"):
            for c in (r.get("controles_cis") or [r.get("control_cis")]):
                if c:
                    en_conflicto.add((r["host"], r["benchmark"], c))
            continue
        pares = ([(c, v["resultado"]) for c, v in r["resultados_por_control_cis"].items()]
                 if r.get("resultados_por_control_cis") else [(r.get("control_cis"), r["resultado_cis"])])
        for c, est in pares:
            if c:
                grupos[(r["host"], r["benchmark"], c)].append((r["parte_proyecto"], est))
    filas, compartidos = [], 0
    for (h, b, c), v in sorted(grupos.items(), key=lambda kv: (kv[0][0], kv[0][1], clave_control(kv[0][2]))):
        ests = [e for _, e in v]
        peor = min(ests, key=lambda e: _PEOR.index(e) if e in _PEOR else len(_PEOR))
        partes_ = sorted({p for p, _ in v})
        compartidos += len(partes_) > 1
        filas.append({"host": h, "benchmark": b, "control_cis": c, "resultado": peor, "partes": partes_})
    cnt = collections.Counter(f["resultado"] for f in filas)
    ev = cnt["CUMPLE"] + cnt["NO CUMPLE"] + cnt["CUMPLE PARCIALMENTE"]
    en_alcance = set(grupos) | en_conflicto
    # total = únicos evaluables (sin conflicto de baseline); en_alcance los incluye. Un control en conflicto que también
    # exija una fila sin conflicto cuenta como evaluable.
    return {"total": len(filas), "unicos_en_alcance": len(en_alcance), "unicos_evaluables": len(filas),
            "conflictos_baseline": len(en_alcance) - len(filas),
            "compartidos_p1_p2": compartidos, "conteo": dict(cnt), "evaluables": ev,
            "porcentaje_cumplimiento": round(100.0 * cnt["CUMPLE"] / ev, 1) if ev else None,
            "cobertura_tecnica": round(100.0 * ev / len(filas), 1) if filas else None,
            "formula": "Controles CIS únicos por host: cumplimiento = CUMPLE / (CUMPLE + NO CUMPLE + PARCIAL); "
                       "cobertura = evaluables / controles únicos. Denominador propio (no se mezcla con filas del proyecto).",
            "detalle": filas}


def evidence_id(benchmark: str, comando_id: str, host: Optional[str] = None) -> str:
    """Identificador estable de una evidencia técnica (un comando por host/benchmark).

    Una misma evidencia puede alimentar filas de la Parte 1 y de la Parte 2: cada fila conserva su
    propio parte_proyecto/id_matriz y referencia el evidence_id compartido (nunca se fusionan filas)."""
    base = f"{host or '*'}|{benchmark}|{comando_id}"
    return "EV-" + hashlib.sha256(base.encode()).hexdigest()[:12]


def procedencias(filas: Iterable[Dict], ev_id: str) -> List[Dict]:
    """Filas (de cualquier parte) que usan un evidence_id; para trazabilidad de evidencia compartida."""
    return [{"id_matriz": f.get("id_matriz"), "parte_proyecto": normalizar_fila(f)["parte_proyecto"],
             "control_excel": f.get("control_excel")}
            for f in filas if ev_id in (f.get("evidence_ids") or [])]
