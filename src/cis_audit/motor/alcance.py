"""Alcance previsto de una ejecución por benchmark: PARTE 1, PARTE 2 y GLOBAL.

Cálculo ESTRUCTURAL y local (matrices de alcance + registro de evaluadores + selección de comandos): no se conecta
a ningún host, no ejecuta comandos ni evalúa evidencia. Lo usan `precheck` (bloque de conteos) y `plan`.
Las cifras coinciden con lo que `ejecutar` recolecta y evalúa: los mismos `requerimientos(matriz, bench, matriz_p1)`.
"""

from typing import Dict, List, Optional

from .comandos import COMANDOS
from .controles import control_para_titulo
from .seleccion import FAMILIA, OFICIALES_P1, requerimientos

_ORG_CIS = {"repos", "gpg_keys", "suid_sgid"}   # mismo criterio que plan.resumen_filas


def _priv_ids(familia: str) -> List[str]:
    from .privilegiado import IDS_PRIVILEGIADOS
    excl = ("grub.grubby",) if familia == "sles" else ("grub.cfg_linux", "apparmor.status")
    return [c for c in IDS_PRIVILEGIADOS if c not in excl]


def alcance_benchmark(matriz2: List[Dict], matriz_p1: Optional[Dict], bench: str, profunda: bool = False) -> Dict:
    from .controles_p1 import MANUAL_P1
    from .privilegiado import IDS_NUEVOS_1_2_0, IDS_PRIVILEGIADOS, VERSION
    familia = FAMILIA[bench]
    req2 = requerimientos(matriz2, bench)
    req12 = requerimientos(matriz2, bench, matriz_p1)
    priv_ids = _priv_ids(familia)

    # ------------------------------------------------------------------ PARTE 2
    f2 = [f for f in matriz2 if f["benchmark"] == bench]
    yes = req2["filas_si"]
    conf_grupos: Dict[str, int] = {}
    for c in req2["controles"].values():
        if c.grupo_conflicto:
            conf_grupos[c.grupo_conflicto] = conf_grupos.get(c.grupo_conflicto, 0) + 1

    def cmds2(f):
        ctl = control_para_titulo(f["titulo_cis"]) if f["titulo_cis"] else None
        return ctl, [COMANDOS[c] for c in (ctl.comandos_para(familia) if ctl else []) if c in req2["comandos"]]

    p2 = {"filas": len(f2), "yes": len(yes), "no": len(f2) - len(yes), "evaluadas": 0, "filesystem_profundo": 0,
          "manuales_politica_sitio": 0, "conflictos_baseline": 0, "cis_evidencia_organizacional": 0,
          "exclusiones_evidencia_organizacional": 0, "usan_evidencia_privilegiada": 0, "usan_grupos_nuevos_1_2_0": 0}
    det2 = {"conflictos": [], "fs_profundo": [], "manuales": [], "organizacional": [], "privilegiadas": []}
    ev2, ids2, conf_ids = set(), set(), set()
    for f in yes:
        ctl, cmds = cmds2(f)
        etiqueta = f"F{f['fila_excel']} {f['control_cis']}"
        if ctl:
            ev2.add(ctl.id)
        if f["control_cis"]:
            ids2.add(f["control_cis"])
        prof = any(c.profunda for c in cmds) and not profunda
        p2["filesystem_profundo"] += prof
        p2["evaluadas"] += not prof
        if prof:
            det2["fs_profundo"].append(etiqueta)
        if ctl and ctl.politica_sitio:
            p2["manuales_politica_sitio"] += 1
            det2["manuales"].append(etiqueta)
        if ctl and ctl.grupo_conflicto and conf_grupos.get(ctl.grupo_conflicto, 0) > 1:
            p2["conflictos_baseline"] += 1
            det2["conflictos"].append(f"{etiqueta} {f['titulo_cis']}")
            conf_ids.add(f["control_cis"])
        if ctl and ctl.id in _ORG_CIS:
            p2["cis_evidencia_organizacional"] += 1
            det2["organizacional"].append(etiqueta)
        if f.get("tipo_exclusion") in ("ORGANIZACIONAL", "MIXTA"):
            p2["exclusiones_evidencia_organizacional"] += 1
        if any(c.sudo for c in cmds):
            p2["usan_evidencia_privilegiada"] += 1
            det2["privilegiadas"].append(etiqueta)
        p2["usan_grupos_nuevos_1_2_0"] += any(c.id in IDS_NUEVOS_1_2_0 for c in cmds)
    p2["evaluadores"] = len(ev2)

    # ------------------------------------------------------------------ PARTE 1
    filas1 = [f for f in (matriz_p1 or {}).get("matriz", []) if f["benchmark"] == bench]
    pre = [p for p in (matriz_p1 or {}).get("prerrequisitos", []) if p["benchmark"] == bench]
    en = [f for f in filas1 if f["estado_alcance"] == "EN ALCANCE"]
    p1 = {"filas": len(filas1), "en_alcance": len(en), "prerrequisitos": len(pre), "manuales": 0, "evidencia_organizacional": 0,
          "filesystem_profundo": 0, "sin_evaluador": 0, "usan_evidencia_privilegiada": 0, "usan_grupos_nuevos_1_2_0": 0,
          "fuera_de_alcance": {}}
    det1 = {"manuales": [], "organizacional": [], "fs_profundo": [], "privilegiadas": [], "sin_evaluador": []}
    for f in filas1:
        if f["estado_alcance"] != "EN ALCANCE":
            p1["fuera_de_alcance"][f["estado_alcance"]] = p1["fuera_de_alcance"].get(f["estado_alcance"], 0) + 1
    ev1, ids1 = set(), set()
    for f in en:
        ctls = [control_para_titulo(t) for t in f["titulos_cis"]]
        etiqueta = f"F{f['fila_excel']} {f['control_cis']}"
        ids1.update(f["controles_cis"])
        ev1.update(c.id for c in ctls if c)
        if any(c is None for c in ctls):
            p1["sin_evaluador"] += 1
            det1["sin_evaluador"].append(etiqueta)
        if f.get("manual_cis") or any(c and c.id in MANUAL_P1 for c in ctls):
            p1["manuales"] += 1
            det1["manuales"].append(etiqueta)
        if f.get("evidencia_organizacional_requerida"):
            p1["evidencia_organizacional"] += 1
            det1["organizacional"].append(etiqueta)
        if f.get("requiere_filesystem_profundo") and not profunda:
            p1["filesystem_profundo"] += 1
            det1["fs_profundo"].append(etiqueta)
        cm = {x for c in ctls if c for x in c.comandos}
        if cm & set(IDS_PRIVILEGIADOS):
            p1["usan_evidencia_privilegiada"] += 1
            det1["privilegiadas"].append(etiqueta)
        p1["usan_grupos_nuevos_1_2_0"] += bool(cm & set(IDS_NUEVOS_1_2_0))
    p1["automaticos"] = p1["en_alcance"] - p1["manuales"] - p1["filesystem_profundo"]
    p1["evaluadores"] = len(ev1)
    p1["fuera_de_alcance_total"] = sum(p1["fuera_de_alcance"].values())

    # ------------------------------------------------------------------ comandos
    cmd2 = list(req2["comandos"])
    cmd12 = list(req12["comandos"])
    comandos = {"recolector_sin_sudo_total": len(cmd12), "parte_2": len(cmd2), "adicionales_parte_1": len(set(cmd12) - set(cmd2)),
                "omitidos_fs_profundo": [c for c in cmd12 if COMANDOS[c].profunda and not profunda],
                "paquetes_total": len(req12["paquetes"]), "paquetes_parte_2": len(req2["paquetes"]),
                "paquetes_adicionales_parte_1": len(set(req12["paquetes"]) - set(req2["paquetes"])),
                "recolector_privilegiado": {"version": VERSION, "bloques": len(priv_ids), "ids": priv_ids,
                                            "grupos_nuevos_1_2_0": [c for c in priv_ids if c in IDS_NUEVOS_1_2_0]}}
    comandos["lista"] = {"parte_2": cmd2, "adicionales_parte_1": [c for c in cmd12 if c not in cmd2]}

    # ------------------------------------------------------------------ GLOBAL
    unicos = ids1 | ids2
    evaluables = unicos - (conf_ids - ids1)   # un id en conflicto que también exija una fila P1 sin conflicto sigue siendo evaluable
    cobertura_p1 = (p1["automaticos"] / p1["en_alcance"]) if p1["en_alcance"] else None
    no_ev2 = p2["filesystem_profundo"] + p2["conflictos_baseline"] + p2["cis_evidencia_organizacional"] + p2["manuales_politica_sitio"]
    cobertura_p2 = ((p2["yes"] - no_ev2) / p2["yes"]) if p2["yes"] else None
    cis_total = p1["en_alcance"] + p2["yes"]
    glob = {"filas_cis_en_alcance": cis_total, "prerrequisitos": len(pre),
            "fuera_de_alcance": p1["fuera_de_alcance_total"] + p2["no"],
            "filas_reporte": cis_total + len(pre) + p1["fuera_de_alcance_total"] + p2["no"],
            "controles_cis_unicos_en_alcance": len(unicos), "controles_cis_unicos_evaluables": len(evaluables),
            "conflictos_baseline_controles": len(unicos) - len(evaluables),
            "solapamientos_p1_p2": sorted(ids1 & ids2),
            "cobertura_prevista_estructural": {
                "parte_1": round(100 * cobertura_p1, 1) if cobertura_p1 is not None else None,
                "parte_2": round(100 * cobertura_p2, 1) if cobertura_p2 is not None else None,
                "global": round(100 * (p1["automaticos"] + p2["yes"] - no_ev2) / cis_total, 1) if cis_total else None,
                "criterio": ("Máximo estructural: filas con evaluador automático / filas CIS en alcance. Resta manuales por diseño, "
                             "filesystem profundo, conflictos de baseline y evidencia organizacional de CIS; no anticipa resultados "
                             "MANUAL/ERROR que dependan de la evidencia real del host.")}}
    return {"benchmark": bench, "familia": familia, "parte_1": p1, "parte_2": p2, "global": glob, "comandos": comandos,
            "detalle": {"parte_1": det1, "parte_2": det2}, "parte_1_oficial": bench in OFICIALES_P1}


def lineas_resumen(a: Dict) -> List[str]:
    """Texto compacto P1 / P2 / GLOBAL (precheck y plan)."""
    p1, p2, g, c = a["parte_1"], a["parte_2"], a["global"], a["comandos"]
    fa1 = ", ".join(f"{k.lower()}={v}" for k, v in p1["fuera_de_alcance"].items()) or "0"
    cob = g["cobertura_prevista_estructural"]
    return [
        f"PARTE 1  CIS en alcance={p1['en_alcance']} | automáticos={p1['automaticos']} | manuales={p1['manuales']} | "
        f"evidencia organizacional={p1['evidencia_organizacional']} | FS profundo={p1['filesystem_profundo']} | "
        f"fuera de alcance={p1['fuera_de_alcance_total']} ({fa1}) | usan recolector privilegiado={p1['usan_evidencia_privilegiada']} "
        f"| prerrequisitos={p1['prerrequisitos']} (separados)",
        f"PARTE 2  YES={p2['yes']} | NO={p2['no']} | evaluadas={p2['evaluadas']} | manuales (política de sitio)={p2['manuales_politica_sitio']} | "
        f"conflictos baseline={p2['conflictos_baseline']} | FS profundo={p2['filesystem_profundo']} | "
        f"evidencia organizacional: CIS={p2['cis_evidencia_organizacional']}, exclusiones={p2['exclusiones_evidencia_organizacional']} | "
        f"usan recolector privilegiado={p2['usan_evidencia_privilegiada']}",
        f"GLOBAL   filas CIS en alcance={g['filas_cis_en_alcance']} ({p1['en_alcance']} P1 + {p2['yes']} P2) | prerrequisitos={g['prerrequisitos']} | "
        f"fuera de alcance={g['fuera_de_alcance']} | filas en el reporte={g['filas_reporte']} | "
        f"controles CIS únicos en alcance={g['controles_cis_unicos_en_alcance']}, evaluables={g['controles_cis_unicos_evaluables']}, "
        f"conflictos baseline={g['conflictos_baseline_controles']} | solapamientos={', '.join(g['solapamientos_p1_p2']) or 'ninguno'} | "
        f"cobertura prevista (estructural máx.): P1 {cob['parte_1']} %, P2 {cob['parte_2']} %, global {cob['global']} %",
        f"COMANDOS recolector sin sudo={c['recolector_sin_sudo_total']} ({c['parte_2']} P2 + {c['adicionales_parte_1']} P1) | "
        f"paquetes={c['paquetes_total']} | recolector privilegiado {c['recolector_privilegiado']['version']}: "
        f"{c['recolector_privilegiado']['bloques']} bloques | omitidos FS profundo={len(c['omitidos_fs_profundo'])}",
    ]
