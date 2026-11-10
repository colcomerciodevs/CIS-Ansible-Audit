"""Mapeo Control Excel -> Control CIS del Benchmark suministrado.

Flujo por (fila Excel, benchmark):
1. Mapeo automático a partir de `NUMERO DE CIS_CONTROL`, validando que el id
   exista en el Benchmark y que el título corresponda (no solo el número).
2. Interpretación de `NOVEDADES AJUSTE NUMERACION CIS` (retirado / absorbido).
3. Aplicación de revisiones del auditor (`config/revisiones_mapeo.yml`), que
   deben referenciar un id existente en el catálogo; nunca se inventan controles.
"""

import difflib
import re
from typing import Dict, List, Optional, Tuple

from .normalizacion import (
    compactar,
    es_placeholder,
    normalizar_id_control,
    normalizar_titulo,
)

UMBRAL_TITULO = 0.90

ESTADOS_MAPEO = {
    "MAPEADO": "El id existe en el Benchmark suministrado y el título corresponde.",
    "MAPEADO_TITULO_ACTUALIZADO": "El id existe; el Benchmark cambió la redacción del título y el requisito corresponde (revisado).",
    "MAPEADO_CONSOLIDADO": "El control Excel quedó absorbido dentro de un control CIS más amplio.",
    "ID_EXCEL_INCORRECTO": "El id indicado en el Excel no existe o corresponde a otro control; se identificó el control correcto en el Benchmark suministrado.",
    "RETIRADO": "El control no existe en el Benchmark suministrado (verificado por búsqueda en el catálogo).",
    "NO_ENCONTRADO": "No se encontró correspondencia en el Benchmark suministrado.",
    "PENDIENTE_REVISION": "Mapeo ambiguo o contradictorio; requiere decisión antes de auditar.",
}

_RE_IDS_TEXTO = re.compile(r"(?<![v\d.])(\d+\.\d+(?:\.\d+)*)(?![\d])")


def similitud(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def ids_en_texto(valor) -> List[str]:
    """Ids de control citados en NOVEDADES (excluye versiones tipo v4.0.0)."""
    vistos = []
    for m in _RE_IDS_TEXTO.finditer(compactar(valor)):
        i = m.group(1).rstrip(".")
        if i not in vistos:
            vistos.append(i)
    return vistos


def mejores_por_titulo(catalogo_idx: Dict[str, Dict], titulo: str, n: int = 3) -> List[Tuple[float, str, str]]:
    t = normalizar_titulo(titulo)
    res = [(similitud(t, r["titulo_normalizado"]), r["id"], r["titulo"]) for r in catalogo_idx.values()]
    return sorted(res, reverse=True)[:n]


def _titulo_coincide(titulo_excel: str, rec: Dict) -> Tuple[bool, float, bool]:
    t = normalizar_titulo(titulo_excel)
    s = similitud(t, rec["titulo_normalizado"])
    truncado = len(t) >= 20 and rec["titulo_normalizado"].startswith(t) and s < 1.0
    return (s >= UMBRAL_TITULO or truncado), s, truncado


def mapear_automatico(fila: Dict, catalogo_idx: Dict[str, Dict]) -> Dict:
    nov = compactar(fila.get("novedades_numeracion"))
    nov_l = nov.lower()
    retirado_excel = "removido" in nov_l
    absorbido_excel = "haria parte de" in nov_l or "hace parte de" in nov_l
    cid = normalizar_id_control(fila.get("cis_id_excel"))
    titulo_excel = fila.get("cis_titulo_excel") or ""
    obs: List[str] = []
    notas: List[str] = []
    res = {
        "control_cis": None,
        "estado_mapeo": None,
        "similitud_titulo": None,
        "candidatos_por_titulo": [],
        "ids_citados_en_novedades": ids_en_texto(nov),
        "indicacion_excel": (
            "RETIRADO" if retirado_excel else "ABSORBIDO" if absorbido_excel else None
        ),
        "observaciones_mapeo": obs,
        "notas_mapeo": notas,
    }

    if cid is None or es_placeholder(fila.get("cis_id_excel")):
        if retirado_excel:
            res["estado_mapeo"] = "RETIRADO"
            obs.append("El Excel indica que el control fue removido del Benchmark; sin id CIS.")
        else:
            res["estado_mapeo"] = "NO_ENCONTRADO"
            obs.append("El Excel no indica un id CIS interpretable.")
        return res

    rec = catalogo_idx.get(cid)
    if rec is None:
        res["estado_mapeo"] = "PENDIENTE_REVISION"
        res["candidatos_por_titulo"] = mejores_por_titulo(catalogo_idx, titulo_excel)
        obs.append(f"El id {cid} indicado en el Excel no existe en el Benchmark suministrado.")
        return res

    ok, s, truncado = _titulo_coincide(titulo_excel, rec)
    res["similitud_titulo"] = round(s, 3)
    if truncado:
        notas.append("Título CIS truncado en el Excel; coincide como prefijo del título oficial.")

    if absorbido_excel:
        # El título del Excel puede ser el heredado; se valida con el texto de NOVEDADES.
        if ok or rec["titulo_normalizado"] in normalizar_titulo(nov):
            res.update(control_cis=cid, estado_mapeo="MAPEADO_CONSOLIDADO")
        else:
            res["estado_mapeo"] = "PENDIENTE_REVISION"
            res["candidatos_por_titulo"] = mejores_por_titulo(catalogo_idx, titulo_excel)
            obs.append(
                f"El Excel indica absorción en {cid}, pero en el Benchmark suministrado {cid} es "
                f"'{rec['titulo']}'."
            )
        return res

    if ok:
        res.update(control_cis=cid, estado_mapeo="MAPEADO")
        if "no esta presente" in nov_l and not nov_l.startswith("si esta presente"):
            obs.append(
                f"NOVEDADES afirma que el control no está presente en el CIS, pero {cid} "
                f"'{rec['titulo']}' sí existe en el Benchmark suministrado."
            )
        # Consistencia: ids citados en NOVEDADES distintos al id mapeado
        otros = [i for i in res["ids_citados_en_novedades"] if i != cid]
        if otros:
            obs.append(f"NOVEDADES cita {', '.join(otros)}, distinto del id mapeado {cid}.")
        return res

    res["estado_mapeo"] = "PENDIENTE_REVISION"
    res["candidatos_por_titulo"] = mejores_por_titulo(catalogo_idx, titulo_excel)
    obs.append(
        f"El título del Excel no coincide con el título de {cid} en el Benchmark suministrado "
        f"('{rec['titulo']}', similitud {s:.2f})."
    )
    return res


def aplicar_revision(res: Dict, revision: Optional[Dict], catalogo_idx: Dict[str, Dict], clave_bench: str,
                     version_bench: Optional[str] = None) -> Dict:
    """Aplica la decisión documentada del auditor, validándola contra el catálogo."""
    if not revision:
        res["origen_mapeo"] = "AUTOMATICO"
        return res
    estado = revision["estado_mapeo"]
    if estado not in ESTADOS_MAPEO:
        raise ValueError(f"Estado de mapeo no válido en revisión: {estado}")
    cid = revision.get("control_cis")
    if cid is not None:
        cid = str(cid)
        if cid not in catalogo_idx:
            raise ValueError(
                f"Revisión {revision.get('hoja')} fila {revision.get('fila')}: el control {cid} "
                f"no existe en {clave_bench}"
            )
    versiones = revision.get("version_cis") or {}
    if clave_bench in versiones and version_bench and str(versiones[clave_bench]) != version_bench:
        raise ValueError(f"Revisión fila {revision.get('fila')}: version_cis {versiones[clave_bench]} no coincide con {clave_bench} {version_bench}")
    if revision.get("control_cis_corregido") and str(revision["control_cis_corregido"]) != str(cid):
        raise ValueError(f"Revisión fila {revision.get('fila')}: control_cis_corregido distinto de control_cis")
    res["aprobacion_mapeo"] = revision.get("aprobacion")
    res["evidencia_mapeo"] = compactar(revision.get("evidencia_mapeo")) or None
    res["estado_mapeo_automatico"] = res.get("estado_mapeo")
    # Los mensajes del mapeo automático quedan como antecedente; la decisión vigente
    # es la revisión documentada.
    res["observaciones_mapeo_automatico"] = res["observaciones_mapeo"]
    res["observaciones_mapeo"] = []
    res["control_cis"] = cid
    res["estado_mapeo"] = estado
    res["origen_mapeo"] = "REVISION_AUDITOR"
    res["justificacion_revision"] = compactar(revision.get("justificacion"))
    # Verificación de retiro: ningún título vigente debe contener todas las palabras
    # de alguno de los grupos indicados.
    grupos = revision.get("verificar_ausencia") or []
    if grupos:
        hallados = [
            f"{r['id']} {r['titulo']}"
            for r in catalogo_idx.values()
            if any(all(k.lower() in r["titulo"].lower() for k in g) for g in grupos)
        ]
        res["verificacion_ausencia"] = {"grupos_palabras_clave": grupos, "coincidencias": hallados}
        if hallados and estado == "RETIRADO":
            res["estado_mapeo"] = "PENDIENTE_REVISION"
            res["observaciones_mapeo"].append(
                f"Se esperaba ausencia, pero el catálogo contiene: {'; '.join(hallados)}"
            )
    return res
