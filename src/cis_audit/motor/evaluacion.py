"""Evaluación de la evidencia almacenada y generación del JSON normalizado.

No se conecta a ningún host: lee `evidence/<run_id>/<host>/raw/recolector.out`
(ya redactado), las matrices de alcance (data/alcance/) y el catálogo CIS extraído.

Unidad de resultado: **fila del Excel** (decisión 1).
- Fila YES: se audita. Fila NO: FUERA DE ALCANCE (no se evalúa, no afecta el porcentaje).
- Si un control CIS tiene partes asociadas a otras filas (o a filas NO / inexistentes), cada
  fila evalúa solo su parte; las demás quedan como componentes FUERA y se explican en una
  observación. Nunca se usa CUMPLE PARCIALMENTE por partes ajenas a la fila (decisiones 2-4).
- D-01: requisitos YES incompatibles -> conflicto_baseline=true, REQUIERE VALIDACIÓN MANUAL,
  se conserva el resultado técnico observado y no computa en el porcentaje.
- D-04/D-06: Resultado CIS y Resultado Exclusión siempre separados.
"""

import copy
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import base
from .partes import evidence_id
from .base import (CUMPLE, ERROR_R, FALLA, FUERA, FUERA_R, MANUAL_R, NO_CUMPLE, ORG_R, PARCIAL, Ctx, Evaluacion,
                   agregar, motivo_de)
from .controles import CONTROLES, Control, adaptacion_para, control_para_titulo, mecanismo_logging, politica_logging
from .exclusiones_eval import evaluar_exclusion
from .resultado_final import aplicar as aplicar_resultado_final
from .porcentaje import resumir, resumir_exclusiones, resumir_final, resumir_por
from .recolector import Recoleccion, parsear

ESQUEMA = "cis-linux-audit/resultados/2.0"
_ORDEN_CRIT = {"ALTO": 3, "MEDIO": 2, "BAJO": 1}
RAIZ = Path(__file__).resolve().parents[3]


def sha256_archivo(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def info_sistema(rec: Recoleccion) -> Dict:
    r = rec.registros
    osr = {}
    if "sys.os_release" in r:
        for l in r["sys.os_release"].stdout.splitlines():
            if "=" in l:
                k, v = l.split("=", 1)
                osr[k] = v.strip().strip('"')
    kern = dict(l.split("=", 1) for l in (r["sys.kernel"].stdout.splitlines() if "sys.kernel" in r else []) if "=" in l)
    ver = osr.get("VERSION_ID", "")
    return {
        "hostname": (r["sys.hostname"].stdout.strip() if "sys.hostname" in r else None),
        "id": osr.get("ID"),
        "distribucion": osr.get("NAME"),
        "nombre_completo": osr.get("PRETTY_NAME"),
        "version": ver,
        "major": ver.split(".")[0] if ver else None,
        "kernel": kern.get("kernel"),
        "arquitectura": kern.get("arch"),
        "fecha_host": (r["sys.fecha"].stdout.strip() if "sys.fecha" in r else None),
        "usuario_auditoria": (r["sys.usuario"].stdout.strip() if "sys.usuario" in r else None),
        "sudo_no_interactivo": bool("sys.sudo" in r and not r["sys.sudo"].omitido and "SUDO_OK" in r["sys.sudo"].stdout),
        "recolector_sin_sudo": rec.sin_sudo,
        "familia_recolector": rec.familia,
        "benchmark_recolector": rec.benchmark,
        "recoleccion_completa": rec.completo,
        "auditoria_profunda": rec.profunda,
    }


def seleccionar_perfil(sistema: Dict, perfiles: Dict) -> Tuple[Optional[str], Optional[str], str]:
    fam, major = (sistema.get("id") or "").lower(), sistema.get("major")
    p = (perfiles.get(fam) or {}).get(str(major))
    if not p:
        return None, None, f"Sistema no soportado por el alcance: ID={fam} VERSION_ID={sistema.get('version')}"
    return p["perfil"], p["benchmark"], ""


def backlog_cis(benchmark: str) -> Dict:
    """Valor esperado de audit_backlog_limit tomado del texto Audit del Benchmark suministrado."""
    cat = RAIZ / "data" / "cis_catalog" / f"{benchmark}.json"
    if not cat.exists():
        return {"minimo": None, "texto": f"catálogo {cat.name} no disponible: regenerar con: python3 src/auditar.py preparar"}
    c = json.loads(cat.read_text(encoding="utf-8"))
    rec = next((r for r in c["recomendaciones"] if "audit_backlog_limit" in r["titulo"]), None)
    if rec is None:
        return {"minimo": None, "texto": "recomendación no encontrada en el Benchmark"}
    ref = f"{c['archivo']} {c['version']}, {rec['id']}, p.{rec['pagina']}"
    m = re.search(r"Recommended that this value be (\d+) or larger", rec["audit"])
    if m:
        return {"minimo": int(m.group(1)), "referencia": ref,
                "texto": f"audit_backlog_limit >= {m.group(1)} (Audit: 'Recommended that this value be {m.group(1)} or larger'; {ref})"}
    return {"minimo": None, "referencia": ref,
            "texto": f"audit_backlog_limit definido; el Audit no fija un mínimo y la descripción indica que el valor por defecto "
                     f"(64) puede ser insuficiente ({ref})"}


def _criticidad(fila: Dict, grupo: List[Dict]) -> Tuple[str, bool]:
    if fila["criticidad"] in _ORDEN_CRIT:
        return fila["criticidad"], False
    defs = [f["criticidad"] for f in grupo if f["criticidad"] in _ORDEN_CRIT and f["estado_alcance"] == "EN ALCANCE"]
    if defs:
        return max(defs, key=lambda c: _ORDEN_CRIT[c]), True
    return "NO DEFINIDA", True


def _alcance_fila(ctl: Optional[Control], fila: Dict, grupo: List[Dict]) -> Tuple[set, List[str]]:
    """Sub-requisitos que evalúa esta fila y observaciones sobre los que quedan fuera."""
    if not ctl or not ctl.subcondiciones:
        return set(), []
    subs = ctl.subcondiciones
    propios = {s for s, (_, ex) in subs.items() if fila["control_excel"] in ex}
    reclamados = {s for s, (_, ex) in subs.items() if any(f["control_excel"] in ex for f in grupo)}
    if not propios:
        propios = (set(subs) - reclamados) or set(subs)
    obs = []
    filas_no = [f for f in grupo if f["estado_alcance"] != "EN ALCANCE"]
    validados = ", ".join(subs[s][0] for s in subs if s in propios)
    for s, (desc, ex) in subs.items():
        if s in propios:
            continue
        si = [f for f in grupo if f["estado_alcance"] == "EN ALCANCE" and f["control_excel"] in ex and f is not fila]
        no = [f for f in grupo if f["estado_alcance"] != "EN ALCANCE" and f["control_excel"] in ex]
        if si:
            obs.append(f"El componente '{desc}' se evalúa en el control Excel {si[0]['control_excel']}.")
        elif no:
            obs.append(f"Se validó únicamente el componente '{validados}' definido como YES en la línea base. El componente '{desc}' "
                       f"está fuera de alcance debido a que está marcado como NO (control Excel {no[0]['control_excel']}).")
        else:
            ctx_no = (f" Controles Excel marcados NO relacionados con este control: {', '.join(f['control_excel'] for f in filas_no)}."
                      if filas_no else "")
            obs.append(f"Se validó únicamente el componente '{validados}' definido como YES en la línea base. El componente '{desc}' "
                       f"está fuera de alcance: no existe una fila YES correspondiente (control Excel {', '.join(ex)}).{ctx_no}")
    return propios, obs


def _textos(comps: List[base.Componente], estado: str):
    activos = [c for c in comps if c.estado != FUERA]
    esperado = " | ".join(f"{c.descripcion}: {c.esperado}" for c in activos if c.esperado)
    encontrado = " | ".join(f"{c.descripcion}: {c.encontrado}" for c in activos if c.encontrado)
    fallas = [c for c in activos if c.estado == FALLA]
    hallazgo = que_falta = None
    if fallas:
        hallazgo = " | ".join(f"Esperado: {c.esperado}. Encontrado: {c.encontrado}." for c in fallas)
        que_falta = " | ".join(f"{c.descripcion} ({c.esperado})" for c in fallas)
    elif estado in (MANUAL_R, ORG_R, ERROR_R):
        pend = [c for c in activos if c.estado in (base.MANUAL, base.ORG, base.ERROR)]
        que_falta = " | ".join(f"{c.descripcion}: {c.encontrado}" for c in pend) or None
    return esperado, encontrado, hallazgo, que_falta


def _motivo(estado: str, comps: List[base.Componente]) -> Optional[str]:
    mapa = {ERROR_R: base.ERROR, MANUAL_R: base.MANUAL, ORG_R: base.ORG}
    if estado not in mapa:
        return None
    for c in comps:
        if c.estado == mapa[estado]:
            m = motivo_de(c.encontrado)
            if m:
                return m
    return {ERROR_R: "SALIDA_NO_INTERPRETABLE", MANUAL_R: "POLITICA_O_REVISION", ORG_R: "EVIDENCIA_ORGANIZACIONAL"}[estado]


def _persistir_evidencia(d: Path, rec: Recoleccion, ids: List[str], sub: str):
    if not ids:
        return
    p = d / sub
    p.mkdir(parents=True, exist_ok=True)
    cmds, out, err, meta = [], [], [], []
    for i in ids:
        r = rec.registros.get(i)
        if r is None:
            meta.append({"comando_id": i, "estado": "no recolectado"}); continue
        cmds.append(f"# [{i}] sudo={'sí' if r.sudo else 'no'} rc={r.rc} ms={r.ms}\n{r.comando or '(omitido: ' + str(r.omitido) + ')'}\n")
        out.append(f"===== [{i}] rc={r.rc}\n{r.stdout}\n")
        err.append(f"===== [{i}]\n{r.stderr}\n")
        meta.append({"comando_id": i, "rc": r.rc, "sudo": r.sudo, "fecha": r.fecha, "duracion_ms": r.ms, "omitido": r.omitido})
    (p / "commands.txt").write_text("\n".join(cmds), encoding="utf-8")
    (p / "stdout.txt").write_text("\n".join(out), encoding="utf-8")
    (p / "stderr.txt").write_text("\n".join(err), encoding="utf-8")
    (p / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def _grupo_conflicto_activo(ctl: Optional[Control], ids_en_alcance: set) -> List[str]:
    if not ctl or not ctl.grupo_conflicto:
        return []
    otros = [c.id for c in CONTROLES if c.grupo_conflicto == ctl.grupo_conflicto and c.id != ctl.id and c.id in ids_en_alcance]
    return [ctl.id] + otros if otros else []


def _parte(f: Dict) -> Dict:
    """Identificación Parte 1/2. Matrices sin el campo = Parte 2 (sin reescribir archivos)."""
    from .partes import normalizar_fila
    n = normalizar_fila(f)
    return {k: n[k] for k in ("parte_proyecto", "tipo_control", "fuente_linea_base", "fuente_requisito") if n.get(k)}


def _fila_fuera(host, bench, perfil, f) -> Dict:
    return {"host": host, **_parte(f), "id_matriz": f["id_matriz"], "fila_excel": f["fila_excel"], "control_excel": f["control_excel"],
            "titulo_excel": f["titulo_excel"], "control_cis": f["control_cis"], "titulo_cis": f["titulo_cis"],
            "propuesta_proveedor": f["propuesta_proveedor"], "resultado_cis": FUERA_R, "benchmark": bench, "perfil": perfil,
            "criticidad": f["criticidad"], "exclusion_infraestructura": f["exclusion_infraestructura"], "estado_mapeo": f["estado_mapeo"]}


def _fusionar_privilegiado(dir_host: Path, rec: Recoleccion, bench: Optional[str], errores: List[str]) -> Dict:
    """Incorpora la salida del recolector privilegiado (raw/privilegiado.out) a la recolección normal.

    Solo se toman los comandos de IDS_PRIVILEGIADOS y solo si el precheck lo declaró CONFIABLE. Sus
    registros sustituyen a los de la ejecución sin privilegios (omitidos o con lectura incompleta).
    La evidencia cruda de ambos recolectores se conserva por separado y sin modificar."""
    from .privilegiado import IDS_PRIVILEGIADOS
    pre_f = dir_host / "precheck" / "precheck.json"
    pre = json.loads(pre_f.read_text(encoding="utf-8")) if pre_f.exists() else {}
    info = dict(pre.get("recolector_privilegiado") or {"estado": "SIN_PRECHECK"})
    info.pop("metadatos", None)
    info.pop("acl", None)
    raw = dir_host / "raw" / "privilegiado.out"
    if not raw.exists():
        info["usado"] = False
        return info
    info["evidencia_cruda"] = str(raw)
    info["evidencia_sha256"] = sha256_archivo(raw)
    if info.get("estado") != "CONFIABLE":
        errores.append(f"Existe salida privilegiada pero el precheck no declaró el recolector CONFIABLE ({info.get('estado')}); se ignora")
        info["usado"] = False
        return info
    priv = parsear(raw.read_text(encoding="utf-8", errors="replace"))
    if priv.fatal:
        # Fail-closed: cualquier @@CIS_FATAL (p. ej. redaccion_fallida) descarta toda la salida privilegiada.
        errores.append(f"Recolector privilegiado abortado: {priv.fatal}")
        info.update({"usado": False, "fallo": priv.fatal})
        return info
    if bench and priv.benchmark != bench:
        errores.append(f"El recolector privilegiado detectó {priv.benchmark}, distinto del sistema ({bench}); se ignora")
        info["usado"] = False
        return info
    if not priv.completo:
        errores.append("Salida del recolector privilegiado incompleta (sin marca de fin); se usan solo los bloques completos")
    usados = []
    for cid, r in priv.registros.items():
        if cid in IDS_PRIVILEGIADOS:
            rec.registros[cid] = r
            usados.append(cid)
    info.update({"usado": True, "comandos": usados, "completo": priv.completo, "version": priv.version})
    return info


def _fusionar_fs(dir_host: Path, rec: Recoleccion, bench: Optional[str], errores: List[str]) -> Dict:
    """Filesystem profundo: incorpora raw/filesystem.out (cis-audit-collect-fs) SOLO si el precheck declaró el recolector FS
    CONFIABLE. La salida fs1 se valida (formato cerrado) y se traduce a los registros que ya consumen los evaluadores.
    Salida inválida, truncada en su marca de fin o con @@CIS_FATAL -> evidencia FS marcada como inválida (ERROR en
    los 5 controles; nunca CUMPLE). Sin el archivo: si el FS se solicitó (precheck CONFIABLE) -> inválida; si no, los
    controles siguen en PENDIENTE_AUDITORIA_PROFUNDA."""
    from .filesystem import materializar_fs, parsear_fs
    raw = dir_host / "raw" / "filesystem.out"
    pre_f = dir_host / "precheck" / "precheck.json"
    pre = json.loads(pre_f.read_text(encoding="utf-8")) if pre_f.exists() else {}
    if not raw.exists():
        if (pre.get("recolector_fs") or {}).get("estado") == "CONFIABLE":
            # Solicitado (--filesystem-profundo) y autorizado por el gate, pero sin salida: nunca CUMPLE.
            errores.append("Recolector FS CONFIABLE en el precheck pero sin raw/filesystem.out")
            rec.registros.update(materializar_fs({"problema": "sin salida del recolector FS"}))
            return {"usado": False, "problema": "sin salida del recolector FS"}
        return {"usado": False}
    info = {k: v for k, v in (pre.get("recolector_fs") or {"estado": "SIN_PRECHECK"}).items() if k not in ("metadatos", "acl")}
    info.update(evidencia_cruda=str(raw), evidencia_sha256=sha256_archivo(raw))
    if info.get("estado") != "CONFIABLE":
        errores.append(f"Existe salida del recolector FS pero el precheck no lo declaró CONFIABLE ({info.get('estado')}); se ignora")
        info["usado"] = False
        return info
    par = parsear_fs(raw.read_text(encoding="utf-8", errors="replace"))
    if not par.get("problema") and bench and par.get("benchmark") != bench:
        par["problema"] = f"el recolector FS detectó {par.get('benchmark')}, distinto del sistema ({bench})"
    if par.get("problema"):
        errores.append(f"Recolector FS: evidencia descartada ({par['problema']})")
    rec.registros.update(materializar_fs(par))
    info.update(usado=not par.get("problema"), version=par.get("version"), problema=par.get("problema"),
                montajes=sorted(par.get("montajes", {})), usuarios=len(par.get("usuarios", {})))
    return info


def _ip_precheck(dir_host: Path) -> Optional[str]:
    """IP con la que se auditó el host (ansible_host del inventario, registrada en su precheck)."""
    f = dir_host / "precheck" / "precheck.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("ip_inventario") or None
    except (OSError, ValueError):
        return None


def evaluar_host(host: str, dir_host: Path, matriz: List[Dict], config: Dict, listas: Dict, matriz_p1: Optional[Dict] = None) -> Dict:
    raw = dir_host / "raw" / "recolector.out"
    errores: List[str] = []
    rec = parsear(raw.read_text(encoding="utf-8", errors="replace")) if raw.exists() else Recoleccion()
    if not raw.exists():
        errores.append("No existe evidencia cruda (recolección no realizada o fallida)")
        estado = dir_host / "raw" / "estado.json"
        if estado.exists():
            e = json.loads(estado.read_text(encoding="utf-8"))
            if e.get("detenido_en_precheck"):
                errores.append(f"Detenido en precheck (no se auditó): {(e.get('mensaje') or '').strip()}")
            elif e.get("inalcanzable"):
                errores.append(f"Host inalcanzable por SSH: {(e.get('mensaje') or '').strip()}")
            elif e.get("benchmark_detectado") in (None, "", "NO_SOPORTADO"):
                errores.append("Sistema operativo no soportado por el alcance (según /etc/os-release)")
            elif (e.get("mensaje") or "").strip():
                errores.append(f"Ansible: {e['mensaje'].strip()}")
    sistema = info_sistema(rec)
    sistema["ip"] = _ip_precheck(dir_host)
    perfil, bench, motivo = seleccionar_perfil(sistema, config.get("os_profiles", {}))
    from .. import excepciones_p1 as _ep1
    exc_p2 = _ep1.p2_para_perfil(config.get("_excepciones_p2_complementarias") or [], perfil, bench)
    priv_info = _fusionar_privilegiado(dir_host, rec, bench, errores)
    sistema["recolector_privilegiado"] = priv_info
    sistema["recolector_fs"] = _fusionar_fs(dir_host, rec, bench, errores)
    if rec.fatal:
        errores.append(f"Recolector abortado: {rec.fatal}")
    if raw.exists() and not rec.completo:
        errores.append("Salida del recolector incompleta (sin marca de fin)")
    if not perfil and raw.exists():
        errores.append(motivo)
    if bench and rec.benchmark and rec.benchmark != bench:
        errores.append(f"El recolector ejecutado ({rec.benchmark}) no corresponde al sistema detectado ({bench})")
    (dir_host / "system_info").mkdir(parents=True, exist_ok=True)
    (dir_host / "system_info" / "system.json").write_text(json.dumps(sistema, ensure_ascii=False, indent=1), encoding="utf-8")
    # Trazabilidad: archivos que la cuenta de auditoría no pudo leer (por comando), aunque no bloqueen ningún control.
    ileg = {cid: [l.split("|", 1)[1] for l in (r.stdout or "").splitlines() if l.startswith("#NO_LEGIBLE|")] for cid, r in rec.registros.items()}
    host_info = {"archivos_no_legibles": {k: v for k, v in ileg.items() if v},
                 "host": host, "sistema": sistema, "perfil": perfil, "benchmark": bench, "errores": errores,
                 "evidencia_cruda": str(raw), "evidencia_sha256": sha256_archivo(raw) if raw.exists() else None}
    if not bench:
        return {"host": host_info, "resultados": [], "fuera_de_alcance": [], "parte1_no_confirmada": []}

    filas = [f for f in matriz if f["benchmark"] == bench]
    fuera = [_fila_fuera(host, bench, perfil, f) for f in filas if f["estado_alcance"] != "EN ALCANCE"]
    grupos: Dict[str, List[Dict]] = {}
    for f in filas:
        if f["control_cis"]:
            grupos.setdefault(f["control_cis"], []).append(f)
    ids_en_alcance = {control_para_titulo(f["titulo_cis"]).id for f in filas
                      if f["estado_alcance"] == "EN ALCANCE" and f["titulo_cis"] and control_para_titulo(f["titulo_cis"])}
    familia = rec.familia or ("sles" if bench == "SLES15" else "rhel")
    cfg_ev = dict(config.get("evaluacion", {}), backlog_cis=backlog_cis(bench))
    if config.get("banners_aprobados"):
        cfg_ev["banners_aprobados"] = config["banners_aprobados"]
    if config.get("banners_comparacion"):
        cfg_ev["banners_comparacion"] = config["banners_comparacion"]
    if priv_info.get("estado") == "NO_CONFIABLE":
        cfg_ev["recolector_privilegiado_no_confiable"] = priv_info.get("detalle") or "verificación de integridad fallida"
    elif priv_info.get("fallo"):
        cfg_ev["recoleccion_privilegiada_fallida"] = f"el recolector privilegiado terminó con @@CIS_FATAL|{priv_info['fallo']}; evidencia descartada"
    rp = config.get("recolector_privilegiado") or {}
    # D-3B-01: identidad = SHA-256 físico del sudoers aprobado; el semántico es solo comprobación adicional.
    for k in ("sudoers_sha256_archivo", "sudoers_sha256_semantico"):
        if rp.get(k):
            cfg_ev[f"instrumentacion_{k}"] = rp[k]
    rfs = config.get("filesystem_profundo") or {}
    if rfs.get("sudoers_sha256_archivo"):
        cfg_ev["instrumentacion_fs_sudoers_sha256_archivo"] = rfs["sudoers_sha256_archivo"]
    if priv_info.get("usado"):
        cfg_ev["recolector_privilegiado_version"] = priv_info.get("version") or "1.1.0"
    cache: Dict[Tuple[str, int], Evaluacion] = {}
    resultados = []
    for fila in [f for f in filas if f["estado_alcance"] == "EN ALCANCE"]:
        grupo = grupos.get(fila["control_cis"], [fila])
        ctl = control_para_titulo(fila["titulo_cis"]) if fila["titulo_cis"] else None
        ctx = Ctx(rec.registros, bench, familia, cfg_ev, fila, listas, rec.profunda)
        if not rec.registros:
            ev = Evaluacion()
            ev.add("host", "Recolección del host", base.ERROR, "", "[HOST_SIN_EVIDENCIA] " + "; ".join(errores))
        elif ctl is None:
            ev = Evaluacion()
            ev.add("evaluador", "Evaluador", base.MANUAL, "", "[POLITICA_O_REVISION] sin evaluador definido para este título CIS")
        else:
            clave_cache = (ctl.id, fila["fila_excel"] if ctl.politica_sitio else 0)
            if clave_cache not in cache:
                try:
                    cache[clave_cache] = ctl.evaluar(ctx)
                except Exception as e:  # salida no interpretable: error de auditoría, nunca incumplimiento
                    err = Evaluacion()
                    err.add("parser", "Interpretación de la evidencia", base.ERROR, "", f"[SALIDA_NO_INTERPRETABLE] {type(e).__name__}: {e}")
                    cache[clave_cache] = err
            ev = copy.deepcopy(cache[clave_cache])
        propios, obs = _alcance_fila(ctl, fila, grupo)
        for c in ev.componentes:
            if c.subcondicion and propios and c.subcondicion not in propios:
                # La comprobación decide en otra fila (o está marcada NO): se muestra su resultado real como informativo.
                legible = {base.OK: "cumple", base.FALLA: "no cumple", base.ERROR: "no determinable"}.get(c.estado, c.estado)
                c.encontrado = f"Resultado informativo (no decide en esta fila): {legible} — {c.encontrado}"
                c.estado = FUERA
        fuera_comp = [d for sub, (d, _) in (ctl.subcondiciones.items() if ctl and propios else []) if sub not in propios]
        estado, tipo_parcial = agregar(ev.componentes, ctl.permite_parcial if ctl else True)
        esperado, encontrado, hallazgo, que_falta = _textos(ev.componentes, estado)
        # D-01: el conflicto journald/rsyslog solo existe mientras la organización no haya elegido el método de logging
        # (config/listas_aprobadas.yml -> metodo_logging, decisión de Infraestructura 2026-10-14).
        conflicto_ids = [] if politica_logging(ctx) else _grupo_conflicto_activo(ctl, ids_en_alcance)
        conflicto = None
        resultado_tecnico = estado
        if conflicto_ids:
            conflicto = {
                "grupo": ctl.grupo_conflicto, "controles": conflicto_ids,
                "detalle": "La línea base marca YES en requisitos técnicamente incompatibles: 'ForwardToSyslog=no' (journald como método) "
                           "y 'ForwardToSyslog=yes' (rsyslog como método). Solo uno puede estar vigente. Pendiente de resolución por el "
                           "responsable de la línea base; no se imputa al proveedor.",
                "mecanismo_detectado": mecanismo_logging(ctx),
            }
            estado, tipo_parcial = MANUAL_R, None
            que_falta = "Resolver el conflicto de la línea base (definir journald o rsyslog como método oficial)."
        crit, heredada = _criticidad(fila, grupo)
        if heredada:
            obs.append(f"Criticidad no definida en la fila; se toma {crit} de las filas YES del mismo control CIS."
                       if crit != "NO DEFINIDA" else "Criticidad no definida en el Excel.")
        dup = [f["fila_excel"] for f in grupo if f is not fila and f["control_excel"] == fila["control_excel"]
               and f["estado_alcance"] == "EN ALCANCE"]
        if dup:
            obs.append(f"Fila duplicada en el Excel: el control {fila['control_excel']} también figura con YES en la(s) fila(s) {dup}.")
        excl = evaluar_exclusion(fila, ctx, ev)
        adapt = adaptacion_para(ctl, bench)
        if adapt:
            adapt = {"control": fila["control_cis"], "pagina": fila["pagina_cis"], **adapt}
        clave = f"{bench}_{fila['control_cis']}"
        d = dir_host / "controls" / clave
        ids_cis = sorted({e for c in ev.componentes if c.estado != FUERA for e in c.evidencia})
        _persistir_evidencia(d, rec, ids_cis, "cis")
        _persistir_evidencia(d, rec, excl.get("evidencia") or [], f"exclusion_F{fila['fila_excel']:03d}")
        motivo_r = "CONFLICTO_BASELINE" if conflicto else _motivo(estado, ev.componentes)
        resultado = {
            # La Parte 2 conserva su id histórico; la Parte 1 se prefija para no colisionar (misma fila Excel).
            "id_resultado": ("P1|" if _parte(fila)["parte_proyecto"] == "PARTE_1" else "") + f"{host}|{bench}|F{fila['fila_excel']:03d}",
            **_parte(fila),
            "host": host, "hostname": sistema.get("hostname"), "perfil": perfil, "benchmark": bench,
            "sistema_operativo": sistema.get("distribucion"), "version": sistema.get("version"),
            "id_matriz": fila["id_matriz"], "hoja_excel": fila["hoja_excel"], "fila_excel": fila["fila_excel"],
            "control_excel": fila["control_excel"], "titulo_excel": fila["titulo_excel"],
            "propuesta_proveedor": fila["propuesta_proveedor"], "criticidad": crit, "criticidad_heredada": heredada,
            "control_cis": fila["control_cis"], "titulo_cis": fila["titulo_cis"], "version_cis": fila["version_cis"],
            "fuente_cis": fila["fuente_cis"], "pagina_cis": fila["pagina_cis"],
            "referencia_cis": f"{fila['fuente_cis']} {fila['version_cis']}, {fila['control_cis']}, p.{fila['pagina_cis']}",
            "estado_mapeo": fila["estado_mapeo"], "control_id_motor": ctl.id if ctl else None,
            "filas_relacionadas": [{"fila_excel": f["fila_excel"], "control_excel": f["control_excel"],
                                    "propuesta_proveedor": f["propuesta_proveedor"]} for f in grupo if f is not fila],
            "resultado_cis": estado, "tipo_parcial": tipo_parcial, "motivo_resultado": motivo_r,
            "resultado_tecnico_observado": resultado_tecnico,
            "valor_esperado_cis": esperado, "encontrado": encontrado, "hallazgo": hallazgo, "que_falta": que_falta,
            "politica_sitio": bool(ctl and ctl.politica_sitio),
            "componentes": [vars(c) for c in ev.componentes],
            "componentes_fuera_de_alcance": fuera_comp,
            # Metadata: mismo esquema que la Parte 1; apunta a la misma evidencia ('evidencia'/'ruta_evidencia').
            "evidence_ids": [evidence_id(bench, i, host) for i in ids_cis],
            "fecha_evidencia": next((rec.registros[i].fecha for i in ids_cis if i in rec.registros), None),
            "evidencia": [{"comando_id": i, "comando": rec.registros[i].comando if i in rec.registros else None,
                           "fecha": rec.registros[i].fecha if i in rec.registros else None,
                           "rc": rec.registros[i].rc if i in rec.registros else None,
                           "sudo": rec.registros[i].sudo if i in rec.registros else None,
                           "omitido": rec.registros[i].omitido if i in rec.registros else "no recolectado",
                           "stdout": (rec.registros[i].stdout or "")[:2000] if i in rec.registros else None,
                           "stderr": (rec.registros[i].stderr or "")[:500] if i in rec.registros else None} for i in ids_cis],
            "ruta_evidencia": str(d),
            "exclusion": excl, "resultado_exclusion_infraestructura": excl["estado"],
            "exclusion_parametros": fila.get("parametros_exclusion") or None,
            "conflicto_baseline": bool(conflicto), "conflicto_detalle": conflicto,
            "computa_porcentaje": estado in (CUMPLE, NO_CUMPLE, PARCIAL) and not conflicto,
            "adaptacion_procedimiento_auditoria": bool(adapt), "adaptacion": adapt,
            "observaciones": obs + ev.observaciones,
            "mecanismo": ev.mecanismo or None,
            "fuente_politica": (ev.mecanismo or {}).get("fuente_politica"),
        }
        # Capa de exclusiones posterior a la evaluación técnica (P2: sin excepciones P1; usuarios P2 de decisiones posteriores).
        aplicar_resultado_final(resultado, None, exc_p2)
        (d / f"result_F{fila['fila_excel']:03d}.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
        resultados.append(resultado)
    no_conf = []
    if matriz_p1:
        from .evaluacion_p1 import evaluar_host_p1
        from .. import excepciones_p1 as ep1
        cfg_ev["_excepciones_p1"] = ep1.para_perfil(config.get("_excepciones_p1") or {},
                                                    config.get("_excepciones_p1_complementarias") or [], perfil, bench)
        cfg_ev["_prerrequisitos"] = config.get("_prerrequisitos") or {}
        p1 = evaluar_host_p1(host, dir_host, rec, sistema, perfil, bench, matriz_p1, cfg_ev, listas, errores)
        resultados += p1["resultados"]
        fuera += p1["fuera_de_alcance"]
        no_conf = p1["no_confirmados"]
        ins = next((r["instrumentacion_auditoria"] for r in p1["resultados"] if r.get("instrumentacion_auditoria")), None)
        if ins:
            host_info["instrumentacion_auditoria"] = ins
    return {"host": host_info, "resultados": resultados, "fuera_de_alcance": fuera, "parte1_no_confirmada": no_conf}


def evaluar_ejecucion(dir_run: Path, matriz_path: Path, config: Dict, listas: Dict, salida: Path,
                      matriz_p1_path: Optional[Path] = None, excepciones_path: Optional[Path] = None) -> Dict:
    """matriz_p1_path: si se indica, se evalúa también la Parte 1 (siempre en 'ejecutar', 'reevaluar' y 'demo')."""
    from .partes import controles_cis_unicos, indicadores
    matriz = json.loads(matriz_path.read_text(encoding="utf-8"))
    matriz_p1 = json.loads(matriz_p1_path.read_text(encoding="utf-8")) if matriz_p1_path else None
    # Excepciones P1 (capa de resultado final): generadas por 'auditar.py preparar' desde la columna "Exclusiones de
    # Infraestructura" del Excel P1 (única fuente). Sin el archivo no se aplica ninguna (fail-closed: final = técnico).
    raiz = matriz_path.resolve().parents[2]
    exc_path = excepciones_path or raiz / "data" / "exclusiones" / "excepciones_p1.json"
    exc = json.loads(exc_path.read_text(encoding="utf-8")) if matriz_p1 and exc_path.exists() else None
    from .. import excepciones_p1 as ep1
    pre_cfg = raiz / "config" / "prerrequisitos_parte1.yml"
    import yaml
    prerrequisitos = (yaml.safe_load(pre_cfg.read_text(encoding="utf-8")) or {}).get("prerrequisitos", {}) \
        if matriz_p1 and pre_cfg.exists() else {}
    # Decisiones de Infraestructura posteriores a la línea base (unión por control y perfil; nunca reemplazan la celda).
    complementarias = ep1.cargar_complementarias(raiz / "config" / "exclusiones_infraestructura.yml") if matriz_p1 else []
    config = {**config, "_prerrequisitos": prerrequisitos, "_excepciones_p1": ep1.para_motor(exc["exclusiones"]) if exc else {},
              "_excepciones_p1_complementarias": complementarias,
              "_excepciones_p2_complementarias": ep1.cargar_complementarias_p2(raiz / "config" / "exclusiones_infraestructura.yml")
              if matriz_p1 else []}
    hosts, resultados, fuera, no_conf = [], [], [], []
    for dir_host in sorted(p for p in dir_run.iterdir() if p.is_dir()):
        r = evaluar_host(dir_host.name, dir_host, matriz["matriz"], config, listas, matriz_p1)
        hosts.append(r["host"]); resultados += r["resultados"]; fuera += r["fuera_de_alcance"]; no_conf += r["parte1_no_confirmada"]
    from .partes import orden_fila
    resultados.sort(key=orden_fila)   # orden numérico jerárquico de los controles también en resultados.json
    fuera.sort(key=orden_fila)
    cis = [r for r in resultados if r.get("tipo_control", "CIS") == "CIS"]
    fuera_cis = [f for f in fuera if f.get("tipo_control", "CIS") == "CIS"]
    datos = {
        "esquema": ESQUEMA,
        "unidad": "fila del Excel con propuesta YES, por host",
        "run_id": dir_run.name,
        "generado": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "matriz": {"ruta": str(matriz_path), "sha256": sha256_archivo(matriz_path), "generada": matriz.get("generado")},
        "fuentes_cis": matriz["fuentes"]["cis"],
        "fuentes_exclusiones": {"excepciones_p1": {"ruta": str(exc_path.relative_to(raiz)) if exc_path.is_relative_to(raiz) else str(exc_path),
                                                   "sha256": sha256_archivo(exc_path), "fuente": (exc or {}).get("fuente")} if exc else None,
                                "excepciones_complementarias": {"ruta": "config/exclusiones_infraestructura.yml",
                                                                 "sha256": sha256_archivo(raiz / "config" / "exclusiones_infraestructura.yml"),
                                                                 "entradas": len(complementarias)} if complementarias else None,
                                "regla": "Capa de resultado final aplicada después de la evaluación CIS técnica (resultado_cis_tecnico "
                                         "no se modifica). P1: excepciones por usuario; P2: solo la columna EXCLUSIONES (Infraestructura "
                                         "Linux) y solo reglas sustitutivas verificadas CUMPLE; parchado y P1 CONTROL_NO_APLICADO: CONTROL EXCLUIDO."},
        "config": {"auditoria_filesystem_profunda": any(h["sistema"].get("auditoria_profunda") for h in hosts),
                   "evaluacion": config.get("evaluacion", {})},
        "hosts": hosts,
        "resultados": resultados,
        "fuera_de_alcance": fuera,
        "parte1_no_confirmada": no_conf,
        "resumen": {
            # "global" = CIS (Parte 1 + Parte 2) por filas del proyecto; los prerrequisitos van aparte.
            # Indicadores CIS técnicos (resultado_cis): global, por host/benchmark/criticidad/parte y controles únicos.
            "global": resumir(cis, fuera_cis),
            # Indicadores de resultado final (resultado_final): cumplimiento final de línea base y cobertura de validación.
            # Bloque propio: nunca se mezcla con los indicadores CIS técnicos.
            "final": {"global": resumir_final(cis),
                      "parte_1": resumir_final([r for r in cis if r.get("parte_proyecto") == "PARTE_1"]),
                      "parte_2": resumir_final([r for r in cis if r.get("parte_proyecto", "PARTE_2") == "PARTE_2"])},
            "por_host": resumir_por(cis, fuera_cis, lambda r: r["host"]),
            "por_benchmark": resumir_por(cis, fuera_cis, lambda r: r["benchmark"]),
            "por_criticidad": resumir_por(cis, fuera_cis, lambda r: r["criticidad"]),
            "por_parte": indicadores(resultados, resumir, fuera),
            "controles_cis_unicos": controles_cis_unicos(cis),
            "parte1_evaluada": bool(matriz_p1),
            "exclusiones": resumir_exclusiones(cis),
            "errores_hosts": {h["host"]: h["errores"] for h in hosts if h["errores"]},
        },
    }
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps(datos, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return datos
