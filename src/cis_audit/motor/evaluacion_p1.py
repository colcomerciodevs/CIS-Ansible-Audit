"""Evaluación de la Parte 1 sobre la misma recolección del host que la Parte 2.

Unidad de resultado: fila de la línea base Parte 1 × host (`id_resultado` con prefijo `P1|`).
- La hoja única (SLES15-ORA8-9) aplica a ORACLE8, ORACLE9, SLES15 y también a RHEL 8/9 (decisión D-P1-01,
  confirmada 2026-10-23). Un benchmark fuera de OFICIALES deja sus filas como REQUIERE CONFIRMACIÓN (lista aparte).
- Una fila puede corresponder a varios controles CIS (MAPEADO_DIVIDIDO): se evalúan todos; la fila agrega
  sus componentes y además conserva el resultado de cada control CIS (para el indicador de controles únicos).
- Resultado CIS: solo el requisito del Benchmark. El valor del Excel se informa aparte como
  valor_linea_base_interna / resultado_linea_base_interna (nunca suaviza el CIS).
- Prerrequisitos PRE-*: requisito interno (config/prerrequisitos_parte1.yml), evaluado con la evidencia. Bloque propio.
"""

import copy
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import base, partes
from .base import CUMPLE, ERROR_R, NO_CUMPLE, PARCIAL, Ctx, Evaluacion, agregar
from .controles import adaptacion_para, control_para_titulo

OFICIALES = ("ORACLE8", "RHEL8", "ORACLE9", "RHEL9", "SLES15")


# --------------------------------------------------------------- línea base interna (separada del CIS)

def _lbi_login(clave: str, ok, desc: str):
    def f(ctx: Ctx) -> Tuple[str, str]:
        from .controles_p1 import _prob, login_defs
        rel = ("/etc/default/useradd",) if clave == "INACTIVE" else ("/etc/login.defs", "/usr/etc/login.defs")
        if _prob(ctx, "p1.login_defs", rel):
            return "no legible", "NO EVALUADO (evidencia no disponible)"
        v = login_defs(ctx).get(clave)
        val = v[0] if v else ("-1" if clave == "INACTIVE" else None)
        if val is None:
            return "no definido", NO_CUMPLE
        if not ok(val):
            return f"{clave}={val}", NO_CUMPLE
        return f"{clave}={val}", "REQUIERE_EXTENSION_RECOLECTOR (valor por cuenta en /etc/shadow)"
    f.desc = desc
    return f


def _lbi_remember(ctx: Ctx) -> Tuple[str, str]:
    from .controles_p1 import _arg_pam, _conf_sec, _num, _prob
    if _prob(ctx, "p1.pam"):
        return "no legible", "NO EVALUADO (evidencia no disponible)"
    vals = [v for _, k, v in _conf_sec(ctx, "pwhistory") if k == "remember"] + [v for _, v in _arg_pam(ctx, "pam_pwhistory", "remember")]
    if not vals:
        return "no definido", NO_CUMPLE
    return "remember=" + ",".join(vals), CUMPLE if all((_num(v) or 0) >= 12 for v in vals) else NO_CUMPLE


def _lbi_sha512(ctx: Ctx) -> Tuple[str, str]:
    from .controles_p1 import _prob, login_defs
    if _prob(ctx, "p1.login_defs", ("/etc/login.defs", "/usr/etc/login.defs")):
        return "no legible", "NO EVALUADO (evidencia no disponible)"
    v = login_defs(ctx).get("ENCRYPT_METHOD")
    return (f"ENCRYPT_METHOD={v[0]}" if v else "no definido"), CUMPLE if v and v[0].upper() == "SHA512" else NO_CUMPLE


_num = lambda v: int(v) if re.fullmatch(r"-?\d+", str(v).strip()) else None  # noqa: E731

LINEA_BASE_INTERNA = {
    "5.4.3": ("remember >= 12", _lbi_remember),
    "5.4.4": ("SHA-512 (login.defs)", _lbi_sha512),
    "5.5.1": ("PASS_MAX_DAYS <= 42", _lbi_login("PASS_MAX_DAYS", lambda v: 0 < (_num(v) or 0) <= 42, "<= 42")),
    "5.5.2": ("PASS_MIN_DAYS >= 3", _lbi_login("PASS_MIN_DAYS", lambda v: (_num(v) or 0) >= 3, ">= 3")),
    "5.5.3": ("PASS_WARN_AGE >= 5", _lbi_login("PASS_WARN_AGE", lambda v: (_num(v) or 0) >= 5, ">= 5")),
    "5.5.4": ("INACTIVE <= 30", _lbi_login("INACTIVE", lambda v: 0 <= (_num(v) if _num(v) is not None else -1) <= 30, "<= 30")),
}


# --------------------------------------------------------------- evaluación

def _ev_control(ctl, ctx: Ctx, cache: Dict) -> Evaluacion:
    if ctl.id not in cache:
        try:
            cache[ctl.id] = ctl.evaluar(ctx)
        except Exception as e:  # salida no interpretable: error de auditoría, nunca incumplimiento
            err = Evaluacion()
            err.add("parser", "Interpretación de la evidencia", base.ERROR, "", f"[SALIDA_NO_INTERPRETABLE] {type(e).__name__}: {e}")
            cache[ctl.id] = err
    return copy.deepcopy(cache[ctl.id])


def _fila_fuera(host: str, perfil: str, f: Dict) -> Dict:
    return {"host": host, **{k: f[k] for k in ("parte_proyecto", "tipo_control", "fuente_linea_base") if f.get(k)},
            "id_matriz": f["id_matriz"], "fila_excel": f["fila_excel"], "control_excel": f["control_excel"],
            "titulo_excel": f["titulo_excel"], "control_cis": ", ".join(f.get("controles_cis") or []) or None,
            "titulo_cis": " | ".join(f.get("titulos_cis") or []) or None, "resultado_cis": f["estado_alcance"],
            "benchmark": f["benchmark"], "perfil": perfil, "criticidad": f.get("riesgo_normalizado") or "NO DEFINIDA",
            "exclusion_infraestructura": f.get("exclusion_infraestructura"), "estado_mapeo": f["estado_mapeo"],
            "motivo_fuera": "; ".join(f.get("motivos_alcance") or []) or f.get("nota_mapeo")}


def _exclusion_p1(f: Dict) -> Dict:
    t = f.get("tipo_exclusion") or "NINGUNA"
    if t == "NINGUNA":
        est = "NINGUNA"
    elif t == "EXCEPCION_INFRAESTRUCTURA":
        est = "DEFINIDA"  # la capa de resultado final determina si cubre exactamente los hallazgos
    elif t in ("MIXTA", "ORGANIZACIONAL"):
        est = "REQUIERE EVIDENCIA ORGANIZACIONAL"
    else:
        est = "NO VERIFICABLE TÉCNICAMENTE"
    return {"texto": f.get("exclusion_infraestructura"), "tipo": t, "estado": est,
            "esperado": f.get("verificacion_exclusion_propuesta") or "", "encontrado": "",
            "que_falta": f.get("evidencia_organizacional_requerida"), "evidencia": []}


_PRIORIDAD_UNIVERSO = {"INCUMPLE": 0, "CUMPLE": 1, "FUERA_POBLACION": 2}


def _unir_universos(a, b):
    """Fila P1 con varios controles CIS (p. ej. 5.4.2.7 y 5.4.2.8): un usuario está exceptuado si en ALGUNO de los controles
    conserva la configuración que el CIS no permite; los detalles de ambos se conservan."""
    usuarios = {}
    for u in sorted(set(a["usuarios"]) | set(b["usuarios"])):
        xs = [x["usuarios"][u] for x in (a, b) if u in x["usuarios"]]
        est = min((x["estado"] for x in xs), key=lambda e: _PRIORIDAD_UNIVERSO.get(e, 3))
        usuarios[u] = {"estado": est, "detalle": " | ".join(x["detalle"] for x in xs if x.get("detalle"))}
    ex = None if a.get("existentes") is None or b.get("existentes") is None else sorted(set(a["existentes"]) | set(b["existentes"]))
    return {"descripcion": f"{a['descripcion']} + {b['descripcion']}", "fuente": f"{a['fuente']} + {b['fuente']}",
            "excluido_si": f"{a['excluido_si']} o {b['excluido_si']}", "usuarios": usuarios, "existentes": ex,
            "limitaciones": list(a.get("limitaciones") or []) + list(b.get("limitaciones") or [])}


def evaluar_host_p1(host: str, dir_host: Path, rec, sistema: Dict, perfil: Optional[str], bench: Optional[str],
                    matriz_p1: Dict, cfg_ev: Dict, listas: Dict, errores: List[str]) -> Dict:
    """{resultados, fuera_de_alcance, no_confirmados} de la Parte 1 para un host."""
    res = {"resultados": [], "fuera_de_alcance": [], "no_confirmados": []}
    if not bench:
        return res
    filas = [f for f in matriz_p1["matriz"] if f["benchmark"] == bench]
    pre = [p for p in matriz_p1.get("prerrequisitos", []) if p["benchmark"] == bench]
    if bench not in OFICIALES:
        res["no_confirmados"] = [{"host": host, "id_matriz": f["id_matriz"], "control_excel": f["control_excel"],
                                  "benchmark": bench, "motivo": "; ".join(f.get("motivos_alcance") or [])} for f in filas + pre]
        return res
    familia = rec.familia or ("sles" if bench == "SLES15" else "rhel")
    cache: Dict[str, Evaluacion] = {}
    for f in filas:
        if f["estado_alcance"] != "EN ALCANCE":
            res["fuera_de_alcance"].append(_fila_fuera(host, perfil, f))
            continue
        ctx = Ctx(rec.registros, bench, familia, cfg_ev, f, listas, rec.profunda)
        comps, obs, por_cis, ids_motor, adapts, mec = [], [], {}, [], [], {}
        for cid_cis, titulo in zip(f["controles_cis"], f["titulos_cis"]):
            ctl = control_para_titulo(titulo)
            if not rec.registros:
                ev = Evaluacion()
                ev.add("host", "Recolección del host", base.ERROR, "", "[HOST_SIN_EVIDENCIA] " + "; ".join(errores))
            elif ctl is None:
                ev = Evaluacion()
                ev.add("evaluador", "Evaluador", base.MANUAL, "", "[POLITICA_O_REVISION] sin evaluador definido para este título CIS")
            else:
                ev = _ev_control(ctl, ctx, cache)
                ids_motor.append(ctl.id)
                a = adaptacion_para(ctl, bench)
                if a:
                    adapts.append({"control": cid_cis, **a})
            for c in ev.componentes:
                if len(f["controles_cis"]) > 1:
                    c.id = f"{cid_cis}:{c.id}"
                    c.descripcion = f"[{cid_cis}] {c.descripcion}"
            nuevo = dict(ev.mecanismo or {})
            if "universo_exclusion" in nuevo and "universo_exclusion" in mec:
                nuevo["universo_exclusion"] = _unir_universos(mec["universo_exclusion"], nuevo["universo_exclusion"])
            mec.update(nuevo)
            est_c, _ = agregar(ev.componentes, ctl.permite_parcial if ctl else False)
            por_cis[cid_cis] = {"titulo": titulo, "resultado": est_c}
            comps += ev.componentes
            obs += [f"[{cid_cis}] {o}" if len(f["controles_cis"]) > 1 else o for o in ev.observaciones]
        estado, tipo_parcial = agregar(comps, len(f["controles_cis"]) > 1)
        from .evaluacion import _motivo, _persistir_evidencia, _textos
        esperado, encontrado, hallazgo, que_falta = _textos(comps, estado)
        lbi_val = lbi_res = None
        if f.get("criterio_excel"):
            if f["control_excel"] in LINEA_BASE_INTERNA and rec.registros:
                desc, fn = LINEA_BASE_INTERNA[f["control_excel"]]
                try:
                    enc_lbi, lbi_res = fn(ctx)
                except Exception as e:  # noqa: BLE001
                    enc_lbi, lbi_res = f"{type(e).__name__}", "NO EVALUADO (evidencia no interpretable)"
                lbi_val = {"criterio": f["criterio_excel"], "evaluado_como": desc, "encontrado": enc_lbi}
            else:
                lbi_val = {"criterio": f["criterio_excel"], "evaluado_como": "mismo criterio que el CIS", "encontrado": None}
                lbi_res = "IGUAL AL CRITERIO CIS"
        clave = f"P1_{bench}_F{f['fila_excel']:03d}"
        d = dir_host / "controls" / clave
        ids_cis = sorted({e for c in comps for e in c.evidencia})
        _persistir_evidencia(d, rec, ids_cis, "cis")
        excl = _exclusion_p1(f)
        r = {
            "id_resultado": f"P1|{host}|{bench}|F{f['fila_excel']:03d}",
            "parte_proyecto": partes.PARTE_1, "tipo_control": partes.CIS, "fuente_linea_base": f["fuente_linea_base"],
            "host": host, "hostname": sistema.get("hostname"), "perfil": perfil, "benchmark": bench,
            "sistema_operativo": sistema.get("distribucion"), "version": sistema.get("version"),
            "id_matriz": f["id_matriz"], "hoja_excel": f["hoja_excel"], "fila_excel": f["fila_excel"], "seccion_excel": f["seccion_excel"],
            "control_excel": f["control_excel"], "titulo_excel": f["titulo_excel"], "propuesta_proveedor": f.get("playbook") or "—",
            "criticidad": f.get("riesgo_normalizado") or "NO DEFINIDA", "criticidad_heredada": False,
            "nivel_riesgo_prod": f.get("nivel_riesgo_prod"),
            "control_cis": ", ".join(f["controles_cis"]), "controles_cis": f["controles_cis"], "titulo_cis": " | ".join(f["titulos_cis"]),
            "version_cis": f["version_cis"], "fuente_cis": f["fuente_cis"], "pagina_cis": ", ".join(str(p) for p in f["paginas_cis"]),
            "referencia_cis": f"{f['fuente_cis']} {f['version_cis']}, {', '.join(f['controles_cis'])}",
            "estado_mapeo": f["estado_mapeo"], "control_id_motor": ", ".join(ids_motor) or None,
            "resultados_por_control_cis": por_cis, "filas_relacionadas": [],
            "resultado_cis": estado, "tipo_parcial": tipo_parcial, "motivo_resultado": _motivo(estado, comps),
            "resultado_tecnico_observado": estado,
            "valor_esperado_cis": esperado, "encontrado": encontrado, "hallazgo": hallazgo, "que_falta": que_falta,
            "valor_linea_base_interna": lbi_val, "resultado_linea_base_interna": lbi_res,
            "politica_sitio": False, "componentes": [vars(c) for c in comps], "componentes_fuera_de_alcance": [],
            "requiere_sudo": f.get("requiere_sudo"), "requiere_filesystem_profundo": f.get("requiere_filesystem_profundo"),
            "requiere_extension_recolector": any(base.motivo_de(c.encontrado) == "REQUIERE_EXTENSION_RECOLECTOR" for c in comps),
            "evidence_ids": [partes.evidence_id(bench, i, host) for i in ids_cis],
            "fecha_evidencia": next((rec.registros[i].fecha for i in ids_cis if i in rec.registros), None),
            "evidencia": [{"comando_id": i, "comando": rec.registros[i].comando if i in rec.registros else None,
                           "fecha": rec.registros[i].fecha if i in rec.registros else None,
                           "rc": rec.registros[i].rc if i in rec.registros else None,
                           "sudo": rec.registros[i].sudo if i in rec.registros else None,
                           "omitido": rec.registros[i].omitido if i in rec.registros else "no recolectado",
                           "stdout": (rec.registros[i].stdout or "")[:2000] if i in rec.registros else None,
                           "stderr": (rec.registros[i].stderr or "")[:500] if i in rec.registros else None} for i in ids_cis],
            "ruta_evidencia": str(d), "exclusion": excl, "resultado_exclusion_infraestructura": excl["estado"],
            "conflicto_baseline": False, "conflicto_detalle": None,
            "computa_porcentaje": estado in (CUMPLE, NO_CUMPLE, PARCIAL),
            "adaptacion_procedimiento_auditoria": bool(adapts), "adaptacion": adapts[0] if adapts else None,
            # Columna "Observacion" del Excel P1: observación, nunca exclusión (concepto separado).
            "observacion": f.get("observacion"),
            "observaciones": ([f.get("nota_mapeo")] if f.get("nota_mapeo") else []) + obs,
            "mecanismo": mec or None,
            "fuente_politica": (mec or {}).get("fuente_politica"),
            # D-3B-01: instrumentación temporal de auditoría (nunca una excepción CIS permanente).
            "instrumentacion_auditoria": mec.get("instrumentacion_auditoria"),
            # D-3B-02: origen del timestamp_timeout evaluado.
            "origen_timestamp_timeout": mec.get("origen_timestamp_timeout"),
            "valor_timestamp_timeout": mec.get("valor_timestamp_timeout"),
        }
        from .resultado_final import aplicar as aplicar_resultado_final
        aplicar_resultado_final(r, cfg_ev.get("_excepciones_p1"))  # capa posterior a la evaluación técnica
        d.mkdir(parents=True, exist_ok=True)
        (d / "result.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        res["resultados"].append(r)
    from .prerrequisitos_eval import evaluar as evaluar_pre
    from .prerrequisitos_eval import fuente_evidencia as fuente_pre
    criterios = cfg_ev.get("_prerrequisitos") or {}
    for p in pre:
        cid = p["control_excel"]
        h = criterios.get(cid)
        if not rec.registros:
            est, mot, ev_ids = (ERROR_R, "[HOST_SIN_EVIDENCIA] " + "; ".join(errores), [])
        elif h is None:
            est, mot, ev_ids = (ERROR_R, f"Sin criterio definido para {cid} en config/prerrequisitos_parte1.yml.", [])
        else:
            est, mot, ev_ids = evaluar_pre(cid, h.get("criterio"), rec.registros)
        r = {
            "id_resultado": f"P1|{host}|{bench}|{cid}",
            "parte_proyecto": partes.PARTE_1, "tipo_control": partes.PRERREQUISITO, "fuente_requisito": p["fuente_requisito"],
            "origen_requisito": p["origen_requisito"],
            "host": host, "hostname": sistema.get("hostname"), "perfil": perfil, "benchmark": bench,
            "sistema_operativo": sistema.get("distribucion"), "version": sistema.get("version"),
            "id_matriz": p["id_matriz"], "hoja_excel": None, "fila_excel": cid, "seccion_excel": p["seccion_excel"],
            "control_excel": cid, "titulo_excel": (h or {}).get("descripcion") or p["titulo_excel"], "propuesta_proveedor": "—",
            "criticidad": "NO DEFINIDA", "control_cis": None, "titulo_cis": f"{cid} — PRERREQUISITO INTERNO (no es un control CIS)",
            "referencia_cis": "No es un control CIS (requisito interno)", "estado_mapeo": None,
            "resultado_cis": est, "tipo_parcial": None, "motivo_resultado": None if est in ("CUMPLE", "NO APLICA") else est,
            "valor_esperado_cis": None, "encontrado": mot, "hallazgo": mot if est == "NO CUMPLE" else None,
            "que_falta": None if est in ("CUMPLE", "NO APLICA") else mot, "evidencia_motivo": mot,
            "fuente_evidencia": fuente_pre(cid, (h or {}).get("criterio")), "criterio": (h or {}).get("criterio"),
            "requiere_excepcion_seguridad": False, "requiere_sudo": p.get("requiere_sudo"),
            "componentes": [], "evidence_ids": [partes.evidence_id(bench, i, host) for i in ev_ids],
            "evidencia": [{"comando_id": i, "comando": rec.registros[i].comando if i in rec.registros else None,
                           "fecha": rec.registros[i].fecha if i in rec.registros else None,
                           "rc": rec.registros[i].rc if i in rec.registros else None,
                           "sudo": rec.registros[i].sudo if i in rec.registros else None,
                           "omitido": rec.registros[i].omitido if i in rec.registros else "no recolectado"} for i in ev_ids],
            "exclusion": {"texto": None, "tipo": "NINGUNA", "estado": "NINGUNA"},
            "resultado_exclusion_infraestructura": "NINGUNA", "conflicto_baseline": False, "computa_porcentaje": False,
            "observaciones": ["Prerrequisito interno: no forma parte de los indicadores CIS ni del cumplimiento final de línea base "
                              "(bloque propio). Criterio: config/prerrequisitos_parte1.yml."] + ([h["nota"]] if (h or {}).get("nota") else []),
        }
        from .resultado_final import aplicar as aplicar_resultado_final
        aplicar_resultado_final(r)
        res["resultados"].append(r)
    return res
