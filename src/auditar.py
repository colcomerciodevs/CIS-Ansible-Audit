#!/usr/bin/env python3
"""CIS Linux Audit — CLI de auditoría CIS de hardening (solo lectura).

Subcomandos:
  preparar                         Genera data/ desde las fuentes canónicas (Excel P1/P2 y PDF CIS). Sin conexión.
  plan                             Dry-run: genera los scripts recolectores y muestra qué se ejecutaría. Sin conexión.
  precheck --limit <hosts>         Precheck read-only (conectividad, SO, recolectores, sudoers).
  ejecutar --limit <hosts>         Auditoría: precheck + gate + recolección + evaluación + reportes.
  reportes --run-id <RUN_ID>       Regenera results/<RUN_ID>/reports/ desde results/<RUN_ID>/resultados.json.
  reevaluar --run-id <RUN_ID>      Evalúa de nuevo la evidencia almacenada en una ejecución NUEVA (sin conexión).
  informe-proveedor [--run-id ...] Informe consolidado de remediación (HTML + PDF) de varios servidores, para el proveedor.
  generar-recolector-privilegiado  Genera el recolector principal, su SHA-256 y su sudoers (no instala nada).
  generar-recolector-fs            Genera el recolector de filesystem profundo, su SHA-256 y su sudoers (no instala nada).
  matriz-controles                 Matriz de controles (método, comandos, sudo, impacto) en build/.
  demo                             Pipeline completo con evidencia SINTÉTICA, sin conexión (verificación de la instalación).
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402

from cis_audit.motor import plan as plan_mod  # noqa: E402
from cis_audit.motor import partes  # noqa: E402
from cis_audit.motor.evaluacion import evaluar_ejecucion  # noqa: E402
from cis_audit.motor.redaccion import redactar  # noqa: E402
from cis_audit.reportes import excel as rep_xlsx  # noqa: E402
from cis_audit.reportes import html as rep_html  # noqa: E402
from cis_audit.reportes import proveedor as rep_proveedor  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
MATRIZ = RAIZ / "data" / "alcance" / "matriz_alcance.json"
MATRIZ_P1 = RAIZ / "data" / "alcance" / "matriz_alcance_parte1.json"
EXCEPCIONES_P1 = RAIZ / "data" / "exclusiones" / "excepciones_p1.json"
EXCLUSIONES_P2 = RAIZ / "data" / "exclusiones" / "exclusiones_p2.json"
RESULTS = RAIZ / "results"
VERSION_PROYECTO = (RAIZ / "VERSION").read_text(encoding="utf-8").strip() if (RAIZ / "VERSION").exists() else "?"


def _config():
    return yaml.safe_load((RAIZ / "config" / "audit.yml").read_text(encoding="utf-8"))


def _listas():
    p = RAIZ / "config" / "listas_aprobadas.yml"
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _run_id_valido(r: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", r or ""):
        raise SystemExit("run-id inválido (solo letras, números, '_', '.', '-')")
    return r


def _nuevo_run_id(hosts: List[str], sufijo: str = "") -> str:
    """Nombre productivo de ejecución: <AAAAMMDDTHHMMSS>_<HOST>[-<HOST>…][_<SUFIJO>]."""
    base = f"{dt.datetime.now().strftime('%Y%m%dT%H%M%S')}_{'-'.join(hosts)}"[:110]
    return _run_id_valido(base + (f"_{sufijo}" if sufijo else ""))


def _fuentes() -> Dict[str, Path]:
    pp = _config()["partes_proyecto"]
    return {"P1": RAIZ / pp["PARTE_1"]["linea_base"], "P2": RAIZ / pp["PARTE_2"]["linea_base"]}


def verificar_preparacion() -> List[str]:
    """Los datos generados (data/) deben corresponder exactamente a las fuentes canónicas actuales (SHA-256 del Excel).
    Devuelve la lista de problemas (vacía = coherente)."""
    p = []
    f = _fuentes()
    for nombre, ruta in f.items():
        if not ruta.exists():
            p.append(f"falta la línea base {nombre}: {ruta.relative_to(RAIZ)}")
    if p:
        return p
    sha1, sha2 = _sha(f["P1"]), _sha(f["P2"])

    def leer(ruta: Path):
        if not ruta.exists():
            p.append(f"falta {ruta.relative_to(RAIZ)}")
            return None
        return json.loads(ruta.read_text(encoding="utf-8"))
    m2, m1, e1, e2 = leer(MATRIZ), leer(MATRIZ_P1), leer(EXCEPCIONES_P1), leer(EXCLUSIONES_P2)
    if m2 and (m2.get("fuentes") or {}).get("excel", {}).get("sha256") != sha2:
        p.append("data/alcance/matriz_alcance.json no corresponde al Excel P2 actual")
    if m1 and (m1.get("fuentes") or {}).get("parte_1", {}).get("sha256") != sha1:
        p.append("data/alcance/matriz_alcance_parte1.json no corresponde al Excel P1 actual")
    if e1 and (e1.get("fuente") or {}).get("sha256") != sha1:
        p.append("data/exclusiones/excepciones_p1.json no corresponde al Excel P1 actual")
    if e2 and (e2.get("fuente") or {}).get("sha256") != sha2:
        p.append("data/exclusiones/exclusiones_p2.json no corresponde al Excel P2 actual")
    for k, b in (_config().get("banners_aprobados") or {}).items():
        if not (RAIZ / b["archivo"]).exists():
            p.append(f"falta el banner aprobado {b['archivo']} ({k})")
    for bench in ("ORACLE8", "ORACLE9", "RHEL8", "RHEL9", "SLES15"):
        if not (RAIZ / "data" / "cis_catalog" / f"{bench}.json").exists():
            p.append(f"falta el catálogo CIS data/cis_catalog/{bench}.json")
    return p


def _exigir_preparacion() -> None:
    prob = verificar_preparacion()
    if prob:
        print("ABORTADO: los datos de data/ no corresponden a las fuentes canónicas:\n  - " + "\n  - ".join(prob)
              + "\nEjecute: python3 src/auditar.py preparar")
        raise SystemExit(6)


# =========================================================================================== preparar

def cmd_preparar(a) -> int:
    """Genera data/ desde las fuentes canónicas. No se conecta a ningún host y no modifica las fuentes."""
    from cis_audit import excepciones_p1 as ep1
    from cis_audit import linea_base_p2, parte1
    from cis_audit.motor import resultado_final as rf
    print("== 1/3 Catálogo CIS (PDF oficiales) y matriz de alcance de la Parte 2 ==")
    linea_base_p2.main(["--reconstruir-catalogo"] if a.reconstruir_catalogo else [])
    print("== 2/3 Matriz de alcance de la Parte 1 ==")
    parte1.main([])
    print("== 3/3 Exclusiones de Infraestructura (P1 y P2) ==")
    f = _fuentes()
    mp1 = json.loads(MATRIZ_P1.read_text(encoding="utf-8"))
    normalizadas = ep1.extraer(f["P1"], mp1)
    ahora = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    EXCEPCIONES_P1.parent.mkdir(parents=True, exist_ok=True)
    EXCEPCIONES_P1.write_text(json.dumps(
        {"generado": ahora, "fuente": {"archivo": str(f["P1"].relative_to(RAIZ)), "sha256": _sha(f["P1"]), "columna": ep1.COLUMNA,
                                       "origen": ep1.ORIGEN},
         "exclusiones": normalizadas}, ensure_ascii=False, indent=1), encoding="utf-8")
    m2 = json.loads(MATRIZ.read_text(encoding="utf-8"))["matriz"]
    p2 = rf.extraer_exclusiones_p2(f["P2"], m2)
    EXCLUSIONES_P2.write_text(json.dumps(
        {"generado": ahora, "fuente": {"archivo": str(f["P2"].relative_to(RAIZ)), "sha256": _sha(f["P2"]),
                                       "columna": rf.ENCABEZADO_EXCLUSION_P2},
         "sustitutivas": {k: list(v) for k, v in rf.SUSTITUTIVAS.items()}, "exclusiones": p2}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"Exclusiones P1 (columna '{ep1.COLUMNA}', origen {ep1.ORIGEN}): {len(normalizadas)}")
    for e in normalizadas:
        print(f"  fila {e['source_row']} {e['control_excel']} -> CIS {', '.join(e['control_cis'])}: "
              + (f"{e['tipo']} {', '.join(e['elementos_excluidos'])}" if e["elementos_excluidos"] else f"{e['tipo']} VALOR {e['valor']}"))
    print(f"Exclusiones P2 (columna '{rf.ENCABEZADO_EXCLUSION_P2}'): {len(p2)} filas con texto")
    prob = verificar_preparacion()
    print("Verificación de coherencia de data/: " + ("OK" if not prob else "FALLA: " + "; ".join(prob)))
    return 0 if not prob else 3


# =========================================================================================== plan

def _benchmark_de_host(h: str, perfil: str) -> Tuple[List[str], Optional[str]]:
    """Benchmarks posibles del host por su grupo de inventario y, si existe, el detectado en su último precheck."""
    cand = {"RHEL_ORACLE_8": ["RHEL8", "ORACLE8"], "RHEL_ORACLE_9": ["RHEL9", "ORACLE9"], "SLES_15": ["SLES15"]}.get(perfil, [])
    for f in sorted(RESULTS.glob("*_PRECHECK/precheck_resumen.json"), reverse=True):
        r = (json.loads(f.read_text(encoding="utf-8")) or {}).get(h) or {}
        if r.get("benchmark_seleccionado") in cand:
            return cand, f"{r['benchmark_seleccionado']}|{f.parent.name}"
    return cand, None


def cmd_plan(a) -> int:
    """DRY-RUN estrictamente local: no se conecta a ningún host ni ejecuta controles."""
    from cis_audit.motor import alcance, operacion
    _exigir_preparacion()
    cfg = _config()
    ej = cfg["ejecucion"]
    profunda = bool(ej["auditoria_filesystem_profunda"])
    inv = RAIZ / (a.inventario or ej["inventario"])
    matriz = json.loads(MATRIZ.read_text(encoding="utf-8"))["matriz"]
    matriz_p1 = json.loads(MATRIZ_P1.read_text(encoding="utf-8"))
    hosts = plan_mod.leer_inventario(inv) if inv.exists() else []
    seleccion = None
    if getattr(a, "limit", None):
        try:
            inv_h = operacion.leer_inventario(inv)
            seleccion = operacion.validar_limit(a.limit, inv_h, max(len(inv_h), 1))
        except operacion.ErrorOperacion as e:
            print(str(e)); return 2
        hosts = [h for h in hosts if h["host"] in seleccion]
    scripts = plan_mod.scripts_por_benchmark(matriz, profunda, ej["timeout_comando_seg"], ej["timeout_filesystem_seg"],
                                             RAIZ / "build" / "scripts", _sha(MATRIZ), matriz_p1)
    validaciones = plan_mod.validar_plan(scripts, cfg, RAIZ, matriz, matriz_p1=matriz_p1)
    por_host = []
    for h in hosts:
        cand, previo = _benchmark_de_host(h["host"], h["perfil_esperado"])
        benchs = [previo.split("|")[0]] if previo else cand
        por_host.append({**h, "benchmarks_candidatos": cand, "benchmark_precheck": previo,
                         "alcance": {b: alcance.alcance_benchmark(matriz, matriz_p1, b, profunda) for b in benchs}})
    benchs_ver = sorted({b for h in por_host for b in h["alcance"]}) if seleccion else list(scripts)
    alc = {b: alcance.alcance_benchmark(matriz, matriz_p1, b, profunda) for b in benchs_ver}
    salida = RAIZ / "build" / "plan_hosts.json"
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(json.dumps({"generado": dt.datetime.now().isoformat(timespec="seconds"), "profunda": profunda, "limit": seleccion,
                                  "scripts": scripts, "alcance": alc, "validaciones": validaciones,
                                  "hosts": por_host}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"CIS Linux Audit {VERSION_PROYECTO} — DRY-RUN: no se ha contactado ningún host ni ejecutado ningún control.")
    print(f"ejecucion.habilitada = {ej.get('habilitada')} | recolector principal requerido = "
          f"{(cfg.get('recolector_privilegiado') or {}).get('version')}")
    cfs = cfg.get("filesystem_profundo") or {}
    print(f"filesystem profundo: solo con --filesystem-profundo explícito (recolector FS {cfs.get('version')} {cfs.get('ruta')})")
    print(f"Inventario: {inv.relative_to(RAIZ) if inv.exists() else str(inv) + ' (no existe)'}" + (f" | --limit {', '.join(seleccion)}" if seleccion else ""))
    for h in por_host:
        print(f"\n- host {h['host']} (grupo {h['grupo']}, perfil {h['perfil_esperado']}): benchmarks posibles {h['benchmarks_candidatos']}"
              + (f"; detectado en el último precheck: {h['benchmark_precheck'].replace('|', ' (') + ')'}" if h["benchmark_precheck"]
                 else "; el benchmark definitivo se decide en el host (/etc/os-release)"))
    for b, x in alc.items():
        print(f"\n== {b} — alcance de la ejecución (Parte 1 + Parte 2) ==")
        for linea in alcance.lineas_resumen(x):
            print("  " + linea)
        print(f"  EVALUADORES  P1={x['parte_1']['evaluadores']} | P2={x['parte_2']['evaluadores']} | sin evaluador P1={x['parte_1']['sin_evaluador']}")
        print(f"  SCRIPT       {Path(scripts[b]['ruta']).relative_to(RAIZ)} ({len(scripts[b]['comandos'])} comandos)")
        if a.detalle:
            c = x["comandos"]
            print(f"  Comandos P2 ({len(c['lista']['parte_2'])}): {', '.join(c['lista']['parte_2'])}")
            print(f"  Comandos adicionales P1 ({len(c['lista']['adicionales_parte_1'])}): {', '.join(c['lista']['adicionales_parte_1'])}")
            print(f"  Omitidos (filesystem profundo): {', '.join(c['omitidos_fs_profundo']) or 'ninguno'}")
            rp = c["recolector_privilegiado"]
            print(f"  Evidencia privilegiada (recolector {rp['version']}, {rp['bloques']} grupos): {', '.join(rp['ids'])}")
            det = x["detalle"]
            for parte, et in (("parte_1", "P1"), ("parte_2", "P2")):
                for k, titulo in (("privilegiadas", "usan evidencia privilegiada"), ("manuales", "manuales"),
                                  ("fs_profundo", "filesystem profundo"), ("conflictos", "conflictos baseline"),
                                  ("organizacional", "evidencia organizacional"), ("sin_evaluador", "sin evaluador")):
                    if k in det[parte]:
                        print(f"  {et} {titulo} ({len(det[parte][k])}): {'; '.join(det[parte][k]) or '—'}")
    print("\n== Validaciones previas a la ejecución ==")
    for v in validaciones:
        print(f"  [{'OK' if v['ok'] else 'FALLA'}] {v['verificacion']} — {v['detalle']}")
    print("\nPara auditar: python3 src/auditar.py ejecutar --limit <host>[,<host>]")
    print(f"Plan guardado en {salida.relative_to(RAIZ)}")
    return 0 if all(v["ok"] for v in validaciones) else 3


def cmd_matriz(a) -> int:
    cfg = _config()
    matriz = json.loads(MATRIZ.read_text(encoding="utf-8"))["matriz"]
    profunda = bool(cfg["ejecucion"]["auditoria_filesystem_profunda"])
    filas = plan_mod.matriz_controles(matriz, profunda)
    d = RAIZ / "build"
    d.mkdir(parents=True, exist_ok=True)
    (d / "matriz_controles.json").write_text(json.dumps(filas, ensure_ascii=False, indent=1), encoding="utf-8")
    cols = ["benchmark", "sistema_operativo", "control_cis", "titulo_cis", "pagina_cis", "controles_excel", "filas_yes", "criticidad", "metodo", "comandos",
            "requiere_sudo", "impacto", "recorre_filesystem", "auditoria_profunda", "se_ejecuta_con_config_actual", "evidencia",
            "criterio_cumple", "criterio_no_cumple", "exclusion", "tipo_exclusion", "verificacion_exclusion", "evidencia_organizacional",
            "sensible", "redaccion", "componentes_fuera_de_alcance", "politica_sitio", "conflicto_baseline", "adaptacion_procedimiento_auditoria",
            "impacto_si_profunda", "filas_no_relacionadas"]

    def plano(k, v):
        if k == "comandos":
            return "\n".join(f"[{c['id']}]{' (sudo -n)' if c['sudo'] else ''}{' (PROFUNDA)' if c['profunda'] else ''} {c['cmd']}" for c in v)
        if isinstance(v, bool):
            return "sí" if v else "no"
        if isinstance(v, dict):
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, list):
            return ", ".join(map(str, v))
        return v
    with open(d / "matriz_controles.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for r in filas:
            w.writerow([plano(k, r.get(k)) for k in cols])
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook(); ws = wb.active; ws.title = "Matriz_Controles"
    ws.append(cols)
    for c in ws[1]:
        c.fill = PatternFill("solid", fgColor="1F3864"); c.font = Font(bold=True, color="FFFFFF")
    for r in filas:
        ws.append([plano(k, r.get(k)) for k in cols])
    for i, k in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = 60 if k in ("comandos", "criterio_cumple", "criterio_no_cumple", "exclusion", "metodo") else 18
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
    ws2 = wb.create_sheet("Comandos")
    cmd_cols = ["id", "descripcion", "familias", "sudo", "impacto", "recorre_fs", "profunda", "sensible", "redaccion", "cmd"]
    ws2.append(cmd_cols)
    for r in plan_mod.comandos_tabla():
        ws2.append([("sí" if r[k] else "no") if isinstance(r[k], bool) else r[k] for k in cmd_cols])
    ws2.freeze_panes = "A2"; ws2.auto_filter.ref = ws2.dimensions
    wb.save(d / "matriz_controles.xlsx")
    print(f"{len(filas)} filas (benchmark × control CIS en alcance) -> build/matriz_controles.{{json,csv,xlsx}}")
    return 0


# =========================================================================================== ejecución remota

def _version_ansible() -> str:
    try:
        return subprocess.run(["ansible", "--version"], capture_output=True, text=True, timeout=30).stdout.splitlines()[0]
    except Exception as e:  # noqa: BLE001
        return f"no disponible ({e})"


def _preparar_ejecucion(a, sufijo: str = ""):
    """Validaciones comunes de precheck/ejecutar. Devuelve (cfg, inv, hosts, seleccion, run_id, dir_run) o termina."""
    from cis_audit.motor import operacion
    _exigir_preparacion()
    cfg = _config()
    ej = cfg["ejecucion"]
    inv = Path(a.inventory).resolve() if a.inventory else RAIZ / ej["inventario"]
    try:
        hosts = operacion.leer_inventario(inv)
        sel = operacion.validar_limit(a.limit, hosts, int(ej.get("max_hosts_por_ejecucion", 3)))
    except operacion.ErrorOperacion as e:
        print(str(e)); raise SystemExit(2)
    if not ej.get("habilitada"):
        print("ABORTADO: ejecucion.habilitada=false en config/audit.yml (requiere autorización expresa)."); raise SystemExit(2)
    if ej.get("auditoria_filesystem_profunda"):
        print("ABORTADO: ejecucion.auditoria_filesystem_profunda debe ser false; el filesystem profundo se solicita solo con "
              "--filesystem-profundo (recolector FS)."); raise SystemExit(2)
    problemas = operacion.validar_hosts(sel, hosts, RAIZ)
    if problemas:
        print("ABORTADO: inventario incompleto o inseguro:\n  - " + "\n  - ".join(problemas)); raise SystemExit(2)
    run_id = _run_id_valido(a.run_id) if a.run_id else _nuevo_run_id(sel, sufijo)
    d = RESULTS / run_id
    if d.exists():
        print(f"ABORTADO: {d.relative_to(RAIZ)} ya existe; no se sobrescriben ejecuciones anteriores."); raise SystemExit(2)
    for sub in ("evidence", "logs", "reports"):
        (d / sub).mkdir(parents=True)
    return cfg, inv, hosts, sel, run_id, d


def _log(d: Path, msg: str):
    linea = f"{dt.datetime.now().astimezone().isoformat(timespec='seconds')} {msg}"
    print(msg)
    (d / "logs").mkdir(parents=True, exist_ok=True)
    with open(d / "logs" / "ejecucion.log", "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def _ansible(d: Path, playbook: str, inv: Path, limit: List[str], extra: Dict, log: str) -> int:
    env = dict(os.environ, ANSIBLE_CONFIG=str(RAIZ / "ansible.cfg"), ANSIBLE_LOG_PATH=str(d / "logs" / log))
    c = ["ansible-playbook", "-i", str(inv), str(RAIZ / "playbooks" / playbook), "--limit", ",".join(limit)]
    for k, v in extra.items():
        c += ["-e", f"{k}={v}"]
    _log(d, "Ejecutando: " + " ".join(shlex.quote(x) for x in c))
    return subprocess.call(c, cwd=str(RAIZ), env=env)


def _precheck(cfg, inv, hosts, sel, d, fs: bool = False) -> Dict[str, Dict]:
    from cis_audit.motor import alcance, operacion
    t0 = dt.datetime.now()
    priv = cfg.get("recolector_privilegiado") or {}
    cfs = cfg.get("filesystem_profundo") or {}
    extra = {"cis_destino": str(d / "evidence"), "cis_priv_ruta": priv.get("ruta") or ""}
    if fs:  # solo con --filesystem-profundo explícito (metadatos de montajes; no recorre el filesystem)
        extra["cis_fs_ruta"] = cfs.get("ruta") or ""
    rc = _ansible(d, "precheck.yml", inv, sel, extra, "ansible_precheck.log")
    res = {}
    matriz = json.loads(MATRIZ.read_text(encoding="utf-8"))["matriz"]
    matriz_p1 = json.loads(MATRIZ_P1.read_text(encoding="utf-8"))
    for h in sel:
        f = d / "evidence" / h / "precheck" / "precheck_ansible.json"
        datos = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"host": h, "inalcanzable": True, "mensaje": f"sin respuesta de Ansible (rc={rc})"}
        r = operacion.interpretar_precheck(datos, hosts[h]["grupo"], cfg["os_profiles"], priv.get("sha256"),
                                           cfs.get("sha256") if fs else None)
        # Trazabilidad: el alias de inventario no tiene por qué coincidir con el hostname del SO.
        r["alias_inventario"] = h
        r["ip_inventario"] = (hosts[h].get("vars") or {}).get("ansible_host")
        if r.get("benchmark_seleccionado"):
            r["conteos"] = operacion.conteos_host(matriz, r["benchmark_seleccionado"], False,
                                                  r.get("sudo_no_interactivo", False)
                                                  or (r.get("recolector_privilegiado") or {}).get("estado") == "CONFIABLE")
            r["alcance_p1p2"] = alcance.alcance_benchmark(matriz, matriz_p1, r["benchmark_seleccionado"])
        (d / "evidence" / h / "precheck").mkdir(parents=True, exist_ok=True)
        (d / "evidence" / h / "precheck" / "precheck.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        res[h] = r
    _log(d, f"Precheck terminado en {(dt.datetime.now() - t0).total_seconds():.1f} s (ansible rc={rc})")
    for h, r in res.items():
        _log(d, f"\n[{h}] alias_inventario={h} hostname_so={r.get('hostname')} ip={r.get('ip_inventario')} "
                f"SSH={'OK' if r['conectividad_ssh'] else 'FALLIDO'} SO={r.get('os_nombre')} "
                f"kernel={r.get('kernel')} arq={r.get('arquitectura')} usuario={r.get('usuario_conectado')} "
                f"sudo -n general={'sí' if r.get('sudo_no_interactivo') else 'no'}"
                f"{'' if r.get('sudo_no_interactivo') else ' (esperado: srvinnops no tiene sudo general; el recolector se valida aparte)'}")
        rp = r.get("recolector_privilegiado") or {}
        if rp:
            _log(d, f"   recolector principal: {rp.get('estado')} — {rp.get('detalle')}")
        if fs:
            rf = r.get("recolector_fs") or {}
            _log(d, f"   recolector FS (filesystem profundo): {rf.get('estado') or 'SIN_RESULTADO'} — {rf.get('detalle') or 'sin detalle'}")
            imp = r.get("impacto_fs") or {}
            if imp:
                _log(d, "   impacto previsto (informativo, sin recorrido): filesystem | montaje | inodos | tamaño KB | "
                        "7.1.11 | 7.1.12 | 7.1.13 | 6.3.3.6")
                for m in imp["montajes"]:
                    _log(d, "     " + " | ".join(str(x) for x in (m["filesystem"], m["montaje"], m["inodos_usados"], m["tamano_kb"]))
                         + " | " + " | ".join("sí" if m[k] else "no" for k in ("7.1.11", "7.1.12", "7.1.13", "6.3.3.6")))
                _log(d, f"   inodos en montajes elegibles: {imp['inodos_elegibles']}")
                if imp["remotos_o_fuse_elegibles"]:
                    _log(d, "   AVISO: montajes remotos/FUSE elegibles según el Audit CIS literal (se recorrerán con los límites "
                            f"y timeouts definidos): {', '.join(imp['remotos_o_fuse_elegibles'])}")
        _log(d, f"   grupo inventario={r['grupo_inventario']} -> perfil esperado {r['perfil_esperado_inventario']}; "
                f"perfil detectado={r.get('perfil_detectado')}; benchmark seleccionado={r.get('benchmark_seleccionado')}")
        if r.get("alcance_p1p2"):
            for linea in alcance.lineas_resumen(r["alcance_p1p2"]):
                _log(d, "   " + linea)
        _log(d, "   -> conectividad y SO/perfil OK (la decisión final la toma el gate de ejecución)" if r["continuar"]
             else f"   -> DETENIDO: {r.get('motivo_detencion')}")
    return res


_IDENTIDAD_RECOLECTOR = ("version", "sha256", "ruta", "sudoers_sha256_archivo", "sudoers_sha256_semantico")


def _config_de_ejecucion(meta: Dict) -> Tuple[Dict, Dict]:
    """Configuración para reevaluar: la vigente, con la identidad de los recolectores y de la instrumentación sudoers con la que
    se RECOLECTÓ la ejecución original (metadata.json). Si la ejecución no registró los SHA del sudoers, se derivan del digest
    del recolector usado y de la plantilla (pv.sudoers / fsm.sudoers_fs): si el archivo instalado no es exactamente ese, la
    regla de auditoría no se reconoce como instrumentación (fail-closed). Devuelve (config, origen por recolector)."""
    import copy
    from cis_audit.motor import filesystem as fsm
    from cis_audit.motor import privilegiado as pv
    cfg = copy.deepcopy(_config())
    origen = {}
    for clave_meta, clave_cfg, plantilla in (("recolector_principal", "recolector_privilegiado", pv.sudoers),
                                             ("recolector_fs", "filesystem_profundo", fsm.sudoers_fs)):
        m = meta.get(clave_meta) or {}
        c = cfg.setdefault(clave_cfg, {})
        if not m.get("sha256") or m.get("sha256") == c.get("sha256"):
            origen[clave_cfg] = "configuración vigente (mismo recolector)"
            continue
        if m.get("sudoers_sha256_archivo"):
            c.update({k: m.get(k) for k in _IDENTIDAD_RECOLECTOR if m.get(k)})
            origen[clave_cfg] = f"metadata.json de la ejecución original (recolector {m.get('version')})"
            continue
        sud = plantilla(m["sha256"])
        c.update({k: m.get(k) for k in ("version", "sha256", "ruta") if m.get(k)},
                 sudoers_sha256_archivo=pv.sha_sudoers_archivo(sud), sudoers_sha256_semantico=pv.sha_instrumentacion(sud))
        origen[clave_cfg] = (f"derivada del recolector {m.get('version')} de la ejecución original (sudoers generado por la plantilla "
                             "para su digest)")
    return cfg, origen


def _metadata(d: Path, cfg, inv: Optional[Path], sel, tipo, extra=None):
    f = _fuentes()
    m = {"run_id": d.name, "tipo": tipo, "version_proyecto": VERSION_PROYECTO,
         "inicio": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
         "operador": os.environ.get("USER"), "controlador": os.uname().nodename, "hosts_limit": sel,
         "inventario": {"ruta": str(inv), "sha256": _sha(inv)} if inv and inv.exists() else None,
         "fuentes": {"excel_p1_sha256": _sha(f["P1"]), "excel_p2_sha256": _sha(f["P2"]),
                     "matriz_p2_sha256": _sha(MATRIZ), "matriz_p1_sha256": _sha(MATRIZ_P1),
                     "excepciones_p1_sha256": _sha(EXCEPCIONES_P1), "exclusiones_p2_sha256": _sha(EXCLUSIONES_P2)},
         "recolector_principal": {k: (cfg.get("recolector_privilegiado") or {}).get(k) for k in _IDENTIDAD_RECOLECTOR},
         "recolector_fs": {k: (cfg.get("filesystem_profundo") or {}).get(k) for k in _IDENTIDAD_RECOLECTOR},
         "config_ejecucion": cfg["ejecucion"], "config_evaluacion": cfg.get("evaluacion"),
         "python": sys.version.split()[0], "ansible": _version_ansible(), **(extra or {})}
    (d / "metadata.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    return m


def cmd_precheck(a) -> int:
    cfg, inv, hosts, sel, run_id, d = _preparar_ejecucion(a, "PRECHECK")
    _metadata(d, cfg, inv, sel, "precheck")
    _log(d, f"PRECHECK read-only — run_id {run_id} — hosts: {sel}")
    fs = getattr(a, "filesystem_profundo", False) is True
    res = _precheck(cfg, inv, hosts, sel, d, fs)
    gate = _registrar_gate(d, res, cfg, fs)
    (d / "precheck_resumen.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    _log(d, f"\nResultado en {d.relative_to(RAIZ)}/precheck_resumen.json")
    return 0 if all(g["permitido"] for g in gate.values()) else 4


def _registrar_gate(d: Path, pre: Dict[str, Dict], cfg, fs: bool = False) -> Dict[str, Dict]:
    """Gate obligatorio del recolector principal (operacion.evaluar_gate) y, con fs, del recolector de filesystem profundo."""
    from cis_audit.motor import operacion
    gate = operacion.evaluar_gate(pre, cfg, fs)
    (d / "gate_recolectores.json").write_text(json.dumps(gate, ensure_ascii=False, indent=1), encoding="utf-8")
    for h, g in gate.items():
        _log(d, f"[{h}] gate de ejecución: {'PERMITIDO' if g['permitido'] else 'BLOQUEADO'} — {g['motivo']}")
    return gate


def nombre_reporte(datos: Dict) -> str:
    """Nombre base de los reportes con el servidor (o servidores) auditado(s): cis_hardening_audit_<HOST>[-<HOST>...]."""
    hosts = [re.sub(r"[^A-Za-z0-9._-]", "_", str(h.get("host") or "")) for h in datos.get("hosts") or []]
    hosts = [h for h in hosts if h]
    return "cis_hardening_audit" + ("_" + "-".join(hosts) if hosts else "")


def _generar_reportes(datos: Dict, d: Path, demo: bool = False) -> Path:
    """Entregables de una ejecución: reports/cis_hardening_audit_<HOST>.{html,xlsx} y reports/resultados_reporte.json."""
    rep = d / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    for viejo in list(rep.glob("cis_hardening_audit*.html")) + list(rep.glob("cis_hardening_audit*.xlsx")):
        viejo.unlink()  # regeneración: no dejar reportes con el nombre anterior
    base = rep / nombre_reporte(datos)
    # Orden numérico jerárquico de los controles (4.2.9 < 4.2.10) en todos los entregables, también en resultados_reporte.json.
    if "resultados" in datos:
        datos = {**datos, "resultados": sorted(datos["resultados"], key=partes.orden_fila),
                 "fuera_de_alcance": sorted(datos.get("fuera_de_alcance") or [], key=partes.orden_fila)}
    rep_html.generar(datos, base.with_name(base.name + ".html"), demo=demo)
    rep_xlsx.generar(datos, base.with_name(base.name + ".xlsx"), demo=demo)
    (rep / "resultados_reporte.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return base


def cmd_ejecutar(a) -> int:
    from cis_audit.motor import operacion
    t0 = dt.datetime.now()
    cfg, inv, hosts, sel, run_id, d = _preparar_ejecucion(a)
    ej = cfg["ejecucion"]
    matriz = json.loads(MATRIZ.read_text(encoding="utf-8"))["matriz"]
    matriz_p1 = json.loads(MATRIZ_P1.read_text(encoding="utf-8"))
    scripts = plan_mod.scripts_por_benchmark(matriz, False, ej["timeout_comando_seg"], ej["timeout_filesystem_seg"],
                                             d / "scripts", _sha(MATRIZ), matriz_p1)
    validaciones = plan_mod.validar_plan(scripts, cfg, RAIZ, matriz, para_ejecucion=True, matriz_p1=matriz_p1)
    meta = _metadata(d, cfg, inv, sel, "auditoria", {"scripts": {b: {"sha256": _sha(Path(s["ruta"])), "comandos": len(s["comandos"])}
                                                                   for b, s in scripts.items()}, "validaciones": validaciones})
    fs = getattr(a, "filesystem_profundo", False) is True
    meta["filesystem_profundo"] = fs
    _log(d, f"AUDITORÍA CIS {VERSION_PROYECTO} — run_id {run_id} — hosts: {sel} — filesystem profundo (cis-audit-collect-fs): "
            f"{'SOLICITADO EXPLÍCITAMENTE (--filesystem-profundo)' if fs else 'no'}")
    if not all(v["ok"] for v in validaciones):
        _log(d, "ABORTADO: validaciones previas fallidas: " + str([v for v in validaciones if not v["ok"]])); return 3
    pre = _precheck(cfg, inv, hosts, sel, d, fs)
    gate = _registrar_gate(d, pre, cfg, fs)
    if not all(g["permitido"] for g in gate.values()):
        # Gate obligatorio: sin recolector CONFIABLE no se ejecuta ningún control CIS (ni auditoría parcial).
        _log(d, "\n" + operacion.MENSAJE_GATE.format(hosts=",".join(h for h, g in gate.items() if not g["permitido"])))
        meta.update({"fin": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "abortado_por_gate": True,
                     "gate_recolectores": gate, "hosts_auditados": [], "hosts_detenidos": list(sel)})
        (d / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return 5
    seguir = [h for h, r in pre.items() if r["continuar"]]
    for h, r in pre.items():
        if not r["continuar"]:
            (d / "evidence" / h / "raw").mkdir(parents=True, exist_ok=True)
            (d / "evidence" / h / "raw" / "estado.json").write_text(json.dumps({
                "host": h, "inalcanzable": not r["conectividad_ssh"], "mensaje": r.get("motivo_detencion"),
                "benchmark_detectado": r.get("benchmark_seleccionado") or "NO_SOPORTADO", "detenido_en_precheck": True},
                ensure_ascii=False, indent=1), encoding="utf-8")
    rcs = {}
    for h in seguir:  # un host por invocación: permite elegir la variante sin sudo según su precheck
        variante = "" if pre[h].get("sudo_no_interactivo") else "_sinsudo"
        rp = pre[h].get("recolector_privilegiado") or {}
        if variante and rp.get("estado") == "CONFIABLE":
            _log(d, f"[{h}] sin sudo general: el recolector normal no invoca sudo; los controles privilegiados los "
                    "cubre el recolector principal CONFIABLE")
        elif variante:
            _log(d, f"[{h}] sin sudo -n: se usa la variante del recolector que no invoca sudo (controles privilegiados -> "
                    "ERROR DE AUDITORÍA / PERMISOS_INSUFICIENTES; no se generan intentos fallidos de sudo en el host)")
        extra = {"cis_run_id": run_id, "cis_destino": str(d / "evidence"), "cis_scripts_dir": str(d / "scripts"),
                 "cis_serial": ej["serial"], "cis_variante": variante, "cis_privilegiado": "0"}
        if rp.get("estado") == "CONFIABLE":
            priv = cfg.get("recolector_privilegiado") or {}
            extra.update({"cis_privilegiado": "1", "cis_priv_ruta": priv["ruta"], "cis_priv_sha": priv["sha256"]})
            _log(d, f"[{h}] recolector principal CONFIABLE: se ejecutará 'sudo -n {priv['ruta']}' (sin argumentos)")
        if fs and (pre[h].get("recolector_fs") or {}).get("estado") == "CONFIABLE":
            cfs = cfg["filesystem_profundo"]
            extra.update({"cis_fs_profundo": "1", "cis_fs_ruta": cfs["ruta"], "cis_fs_sha": cfs["sha256"]})
            _log(d, f"[{h}] recolector FS CONFIABLE: se ejecutará 'sudo -n {cfs['ruta']}' (sin argumentos, filesystem profundo)")
        elif rp.get("estado") == "NO_CONFIABLE":
            _log(d, f"[{h}] recolector principal NO CONFIABLE: NO se ejecuta ({rp.get('detalle')}); controles privilegiados -> "
                    "ERROR DE AUDITORÍA / RECOLECTOR_PRIVILEGIADO_NO_CONFIABLE")
        rcs[h] = _ansible(d, "recolectar.yml", inv, [h], extra, f"ansible_recoleccion_{h}.log")
        _log(d, f"[{h}] recolección terminada (ansible rc={rcs[h]})")
    if not seguir:
        _log(d, "Ningún host superó el precheck: no se ejecutó la recolección.")
    datos = evaluar_ejecucion(d / "evidence", MATRIZ, cfg, _listas(), d / "resultados.json", MATRIZ_P1)
    base = _generar_reportes(datos, d)
    fin = dt.datetime.now()
    resumen = _resumen_ejecucion(d, datos, pre, (fin - t0).total_seconds())
    meta.update({"fin": fin.astimezone().isoformat(timespec="seconds"), "duracion_seg": round((fin - t0).total_seconds(), 1),
                 "ansible_rc_recoleccion": rcs or None, "hosts_auditados": seguir, "hosts_detenidos": [h for h in sel if h not in seguir]})
    (d / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    _log(d, "\n" + resumen)
    _log(d, f"\nArtefactos: {d.relative_to(RAIZ)}/ (metadata.json, resultados.json, resumen_ejecucion.json, evidence/, logs/, "
            f"reports/{base.name}.html, reports/{base.name}.xlsx, reports/resultados_reporte.json)")
    return 0


# =========================================================================================== recolectores

def _visudo(ruta: Path) -> None:
    if shutil.which("visudo"):
        r = subprocess.run(["visudo", "-cf", str(ruta)], capture_output=True, text=True)
        print(f"visudo -cf (validación local de sintaxis, no instala): rc={r.returncode} {(r.stdout + r.stderr).strip()}")


def cmd_generar_privilegiado(a) -> int:
    """Genera los entregables del recolector principal para Infraestructura. NO instala nada."""
    from cis_audit.motor import privilegiado as pv
    texto = pv.generar(_config()["ejecucion"]["timeout_comando_seg"])
    prob = pv.problemas_script(texto)
    if prob:
        print("ABORTADO: el script generado no supera las verificaciones:\n - " + "\n - ".join(prob)); return 3
    d = RAIZ / "collectors" / f"principal-{pv.VERSION}"
    d.mkdir(parents=True, exist_ok=True)
    digest = pv.sha256(texto)
    (d / "cis-audit-collect").write_text(texto, encoding="utf-8")
    (d / "cis-audit-collect.sha256").write_text(f"{digest}  cis-audit-collect\n", encoding="utf-8")
    sud = pv.sudoers(digest)
    (d / "cis_audit.sudoers").write_text(sud, encoding="utf-8")
    fisico, instr = pv.sha_sudoers_archivo(sud), pv.sha_instrumentacion(sud)
    (d / "cis_audit.sudoers.sha256").write_text(f"{fisico}  cis_audit.sudoers\n", encoding="utf-8")
    (d / "cis_audit.sudoers.semantico.sha256").write_text(
        f"{instr}  contenido efectivo normalizado de cis_audit.sudoers (comprobación adicional; NO es la identidad)\n", encoding="utf-8")
    assert hashlib.sha256((d / "cis_audit.sudoers").read_bytes()).hexdigest() == fisico
    _visudo(d / "cis_audit.sudoers")
    print(f"Recolector principal {pv.VERSION} — SHA-256 {digest}")
    print(f"SHA-256 sudoers archivo (bytes exactos, identidad de la instrumentación): {fisico}")
    print(f"SHA-256 sudoers semántico (comprobación adicional): {instr}")
    print(f"Entregables en {d.relative_to(RAIZ)}/")
    rp = _config().get("recolector_privilegiado") or {}
    if (rp.get("version"), rp.get("sha256"), rp.get("sudoers_sha256_archivo"), rp.get("sudoers_sha256_semantico")) != (pv.VERSION, digest, fisico, instr):
        print("AVISO: config/audit.yml -> recolector_privilegiado no coincide con lo generado "
              f"(version={pv.VERSION}, sha256={digest}, sudoers_sha256_archivo={fisico}, sudoers_sha256_semantico={instr}).")
        return 4
    return 0


def cmd_generar_fs(a) -> int:
    """Genera los entregables del recolector de filesystem profundo. NO instala nada."""
    from cis_audit.motor import filesystem as fsm
    from cis_audit.motor import privilegiado as pv
    texto = fsm.generar_fs(_config()["ejecucion"]["timeout_filesystem_seg"])
    prob = fsm.problemas_script_fs(texto)
    if prob:
        print("ABORTADO: el script FS generado no supera las verificaciones:\n - " + "\n - ".join(prob)); return 3
    d = RAIZ / "collectors" / f"fs-{fsm.VERSION_FS}"
    d.mkdir(parents=True, exist_ok=True)
    digest = fsm.sha256(texto)
    (d / "cis-audit-collect-fs").write_text(texto, encoding="utf-8")
    (d / "cis-audit-collect-fs.sha256").write_text(f"{digest}  cis-audit-collect-fs\n", encoding="utf-8")
    sud = fsm.sudoers_fs(digest)
    (d / "cis_audit_fs.sudoers").write_text(sud, encoding="utf-8")
    fisico, instr = pv.sha_sudoers_archivo(sud), pv.sha_instrumentacion(sud)
    (d / "cis_audit_fs.sudoers.sha256").write_text(f"{fisico}  cis_audit_fs.sudoers\n", encoding="utf-8")
    (d / "cis_audit_fs.sudoers.semantico.sha256").write_text(
        f"{instr}  contenido efectivo normalizado de cis_audit_fs.sudoers (comprobación adicional; NO es la identidad)\n", encoding="utf-8")
    assert hashlib.sha256((d / "cis_audit_fs.sudoers").read_bytes()).hexdigest() == fisico
    _visudo(d / "cis_audit_fs.sudoers")
    print(f"Recolector FS {fsm.VERSION_FS} — SHA-256 {digest}")
    print(f"SHA-256 sudoers FS archivo (identidad de /etc/sudoers.d/cis_audit_fs): {fisico}")
    print(f"SHA-256 sudoers FS semántico (comprobación adicional): {instr}")
    print(f"Entregables en {d.relative_to(RAIZ)}/ (TEMPORALES: instalar solo en la ventana autorizada y retirar después)")
    c = _config().get("filesystem_profundo") or {}
    if (c.get("version"), c.get("sha256"), c.get("sudoers_sha256_archivo"), c.get("sudoers_sha256_semantico")) != (fsm.VERSION_FS, digest, fisico, instr):
        print(f"AVISO: config/audit.yml -> filesystem_profundo debe tener version={fsm.VERSION_FS}, sha256={digest}, "
              f"sudoers_sha256_archivo={fisico}, sudoers_sha256_semantico={instr}.")
        return 4
    return 0


# =========================================================================================== reportes / reevaluación / demo

def cmd_reportes(a) -> int:
    run_id = _run_id_valido(a.run_id)
    d = RESULTS / run_id
    if not (d / "resultados.json").exists():
        print(f"No existe {d.relative_to(RAIZ)}/resultados.json"); return 2
    datos = json.loads((d / "resultados.json").read_text(encoding="utf-8"))
    meta = json.loads((d / "metadata.json").read_text(encoding="utf-8")) if (d / "metadata.json").exists() else {}
    base = _generar_reportes(datos, d, demo=meta.get("tipo") == "demo")
    print(f"-> {d.relative_to(RAIZ)}/reports/{base.name}.html, .xlsx y resultados_reporte.json")
    return 0


def _ultimas_ejecuciones() -> List[Path]:
    """Última ejecución con resultados.json de cada servidor (sin demos), por orden de RUN_ID (fecha)."""
    ultimas: Dict[str, Path] = {}
    for d in sorted(p for p in RESULTS.glob("*/resultados.json")):
        meta = json.loads((d.parent / "metadata.json").read_text(encoding="utf-8")) if (d.parent / "metadata.json").exists() else {}
        if meta.get("tipo") == "demo":
            continue
        datos = json.loads(d.read_text(encoding="utf-8"))
        for h in datos.get("hosts") or []:
            ultimas[h["host"]] = d.parent
    return sorted(set(ultimas.values()))


def cmd_informe_proveedor(a) -> int:
    """Informe consolidado de remediación para el proveedor: results/informe_proveedor/<fecha>/ (.html y .pdf)."""
    dirs = [RESULTS / _run_id_valido(r) for r in a.run_id] if a.run_id else _ultimas_ejecuciones()
    faltan = [d for d in dirs if not (d / "resultados.json").exists()]
    if faltan or not dirs:
        print("Sin resultados.json en: " + (", ".join(str(d.relative_to(RAIZ)) for d in faltan) or "results/")); return 2
    datos = [json.loads((d / "resultados.json").read_text(encoding="utf-8")) for d in dirs]
    destino = RESULTS / "informe_proveedor" / dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    archivos = rep_proveedor.generar(datos, destino / "informe_remediacion_cis.html", rep_proveedor.cargar_catalogos(RAIZ / "data" / "cis_catalog"),
                                     RAIZ / "input" / "baseline" / "files")
    print("Ejecuciones: " + ", ".join(d.name for d in dirs))
    for f in archivos:
        print(f"-> {f.relative_to(RAIZ)}")
    if len(archivos) == 1:
        print("   (PDF no generado: WeasyPrint no está disponible en esta estación)")
    return 0


def cmd_reevaluar(a) -> int:
    """Evalúa de nuevo, sin conexión, la evidencia almacenada de una ejecución. El resultado es una ejecución NUEVA
    (<fecha>_<host>_REEVALUACION) con una copia de la evidencia; la ejecución original no se modifica."""
    _exigir_preparacion()
    origen = RESULTS / _run_id_valido(a.run_id)
    if not (origen / "evidence").is_dir():
        print(f"No existe {origen.relative_to(RAIZ)}/evidence"); return 2
    meta_o = json.loads((origen / "metadata.json").read_text(encoding="utf-8")) if (origen / "metadata.json").exists() else {}
    hosts = meta_o.get("hosts_limit") or sorted(p.name for p in (origen / "evidence").iterdir() if p.is_dir())
    d = RESULTS / _nuevo_run_id(hosts, "REEVALUACION")
    d.mkdir(parents=True)
    shutil.copytree(origen / "evidence", d / "evidence")
    cfg, identidad = _config_de_ejecucion(meta_o)
    datos = evaluar_ejecucion(d / "evidence", MATRIZ, cfg, _listas(), d / "resultados.json", MATRIZ_P1)
    datos["reevaluacion_de"] = origen.name
    (d / "resultados.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    _generar_reportes(datos, d)
    _metadata(d, cfg, None, hosts, "reevaluacion", {"reevaluacion_de": origen.name, "motivo": a.motivo,
                                                    "identidad_recolectores": identidad})
    pre = {h: json.loads((d / "evidence" / h / "precheck" / "precheck.json").read_text(encoding="utf-8"))
           for h in hosts if (d / "evidence" / h / "precheck" / "precheck.json").exists()}
    _log(d, f"Reevaluación offline (sin conexión) de {origen.name} — motivo: {a.motivo}")
    _log(d, _resumen_ejecucion(d, datos, pre, 0.0))
    _log(d, f"Artefactos: {d.relative_to(RAIZ)}/")
    return 0


def cmd_demo(a) -> int:
    """Pipeline completo con evidencia SINTÉTICA (sin conexión): verifica que la instalación genera una ejecución
    completa (evidence/, resultados.json, reports/). Los resultados NO describen ningún servidor real."""
    from cis_audit.motor import privilegiado as pv
    from cis_audit.motor.simulacion import recolector_sintetico
    from cis_audit.motor.simulacion_p1 import privilegiado_sintetico
    _exigir_preparacion()
    d = RESULTS / _nuevo_run_id(["DEMO-SINTETICO"])
    ev = d / "evidence"
    matriz = json.loads(MATRIZ.read_text(encoding="utf-8"))["matriz"]
    matriz_p1 = json.loads(MATRIZ_P1.read_text(encoding="utf-8"))
    for host, bench in (("demo-ol9-01", "ORACLE9"), ("demo-sles15-01", "SLES15")):
        p = ev / host / "raw"
        p.mkdir(parents=True, exist_ok=True)
        (p / "recolector.out").write_text(redactar(recolector_sintetico(bench, matriz, matriz_p1)), encoding="utf-8")
        (p / "privilegiado.out").write_text(redactar(privilegiado_sintetico(bench, pv.sudoers(pv.sha256(pv.generar())))),
                                            encoding="utf-8")
        (ev / host / "precheck").mkdir(parents=True, exist_ok=True)
        (ev / host / "precheck" / "precheck.json").write_text(json.dumps(
            {"sintetico": True, "recolector_privilegiado": {"estado": "CONFIABLE", "version": pv.VERSION, "sintetico": True}},
            ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "logs").mkdir()
    cfg = _config()
    datos = evaluar_ejecucion(ev, MATRIZ, cfg, _listas(), d / "resultados.json", MATRIZ_P1)
    _generar_reportes(datos, d, demo=True)
    _metadata(d, cfg, None, ["demo-ol9-01", "demo-sles15-01"], "demo", {"sintetico": True})
    g = datos["resumen"]["global"]
    print(f"DEMO (evidencia SINTÉTICA, sin conexión): {len(datos['resultados'])} resultados. {g['indicadores']}.")
    print(f"-> {d.relative_to(RAIZ)}/ (metadata.json, resultados.json, evidence/, reports/)")
    return 0


def _resumen_ejecucion(d: Path, datos: Dict, pre: Dict, segundos: float) -> str:
    from cis_audit.motor import operacion
    from cis_audit.motor.recolector import parsear
    salida, L = {}, [f"Resumen de ejecución {d.name}", f"Tiempo total: {segundos:.1f} s"]
    fin = (datos["resumen"].get("final") or {}).get("global") or {}
    for h in datos["hosts"]:
        host = h["host"]
        rs = [r for r in datos["resultados"] if r["host"] == host]
        g = datos["resumen"]["por_host"].get(host, {"conteo": {}})
        c = g.get("conteo", {})
        raw = d / "evidence" / host / "raw" / "recolector.out"
        rec = parsear(raw.read_text(encoding="utf-8")) if raw.exists() else None
        ejecutados = [x for x in (rec.registros.values() if rec else []) if not x.omitido]
        info = {
            "host": host, "sistema_operativo": h["sistema"].get("nombre_completo") or pre.get(host, {}).get("os_nombre"),
            "benchmark": h.get("benchmark"), "filas_yes": len(rs),
            "comandos_ejecutados": len(ejecutados),
            "comandos_omitidos": sum(1 for x in (rec.registros.values() if rec else []) if x.omitido),
            "CUMPLE": c.get("CUMPLE", 0), "NO_CUMPLE": c.get("NO CUMPLE", 0), "CUMPLE_PARCIALMENTE": c.get("CUMPLE PARCIALMENTE", 0),
            "NO_APLICA": c.get("NO APLICA", 0), "REQUIERE_VALIDACION_MANUAL": c.get("REQUIERE VALIDACIÓN MANUAL", 0),
            "REQUIERE_EVIDENCIA_ORGANIZACIONAL": c.get("REQUIERE EVIDENCIA ORGANIZACIONAL", 0),
            "ERROR_DE_AUDITORIA": c.get("ERROR DE AUDITORÍA", 0),
            "errores_por_falta_de_privilegios": g.get("no_evaluados_por_falta_de_privilegios", 0),
            "pendientes_filesystem_profundo": g.get("pendientes_auditoria_profunda", 0),
            "cumplimiento_cis_tecnico": g.get("porcentaje_cumplimiento"), "cobertura_tecnica_cis": g.get("cobertura_tecnica"),
            "comandos_resultado_inesperado": operacion.comandos_inesperados(d / "evidence" / host),
            "errores_host": h["errores"],
        }
        salida[host] = info
        L.append(f"[{host}] " + " | ".join(f"{k}={v}" for k, v in info.items()
                                           if k not in ("host", "comandos_resultado_inesperado", "errores_host")))
        if info["errores_host"]:
            L.append(f"[{host}] errores: {'; '.join(info['errores_host'])}")
    salida["_global_final"] = {"cumplimiento_final_linea_base": fin.get("cumplimiento_final_linea_base"),
                               "cobertura_validacion": fin.get("cobertura_validacion")}
    L.append(f"Cumplimiento final de línea base: {fin.get('cumplimiento_final_linea_base')} % · "
             f"Cobertura de validación: {fin.get('cobertura_validacion')} %")
    (d / "resumen_ejecucion.json").write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    return "\n".join(L)


# =========================================================================================== CLI

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"CIS Linux Audit {VERSION_PROYECTO}")
    s = p.add_subparsers(dest="cmd", required=True)
    x = s.add_parser("preparar", help="Generar data/ desde los Excel P1/P2 y los PDF CIS (sin conexión)")
    x.add_argument("--reconstruir-catalogo", action="store_true", help="Volver a extraer los PDF CIS aunque su SHA no haya cambiado")
    x.set_defaults(f=cmd_preparar)
    x = s.add_parser("plan", aliases=["dry-run"], help="Mostrar qué se ejecutaría (no se conecta)")
    x.add_argument("--inventario"); x.add_argument("--detalle", action="store_true", help="Listar comandos por categoría")
    x.add_argument("--limit", help="Hosts explícitos del inventario (separados por coma); sin conexión")
    x.set_defaults(f=cmd_plan)
    for nombre, func, ayuda in (("precheck", cmd_precheck, "Precheck read-only (requiere --limit)"),
                                ("ejecutar", cmd_ejecutar, "Auditoría: precheck + recolección + evaluación + reportes (requiere --limit)")):
        x = s.add_parser(nombre, help=ayuda)
        x.add_argument("--inventory", help="Inventario (por defecto config ejecucion.inventario)")
        x.add_argument("--limit", help="Hosts explícitos del inventario, separados por coma (OBLIGATORIO)")
        x.add_argument("--run-id", help="Nombre de la ejecución (por defecto <AAAAMMDDTHHMMSS>_<HOST>)")
        x.add_argument("--filesystem-profundo", action="store_true",
                       help="Verificar/ejecutar el recolector de filesystem profundo cis-audit-collect-fs (explícito; nunca implícito)")
        x.set_defaults(f=func)
    x = s.add_parser("reportes", help="Regenerar results/<RUN_ID>/reports/ desde resultados.json")
    x.add_argument("--run-id", required=True); x.set_defaults(f=cmd_reportes)
    x = s.add_parser("reevaluar", help="Evaluar de nuevo la evidencia almacenada en una ejecución nueva (sin conexión)")
    x.add_argument("--run-id", required=True); x.add_argument("--motivo", required=True); x.set_defaults(f=cmd_reevaluar)
    x = s.add_parser("informe-proveedor", help="Informe consolidado de remediación (HTML + PDF) de varios servidores, para el proveedor")
    x.add_argument("--run-id", action="append", help="Ejecución a incluir (repetible; por defecto la última de cada servidor)")
    x.set_defaults(f=cmd_informe_proveedor)
    x = s.add_parser("generar-recolector-privilegiado", help="Generar script/SHA-256/sudoers del recolector principal (no instala)")
    x.set_defaults(f=cmd_generar_privilegiado)
    x = s.add_parser("generar-recolector-fs", help="Generar script/SHA-256/sudoers del recolector de filesystem profundo (no instala)")
    x.set_defaults(f=cmd_generar_fs)
    x = s.add_parser("matriz-controles", help="Matriz de controles (método, comandos, sudo, impacto) en build/")
    x.set_defaults(f=cmd_matriz)
    x = s.add_parser("demo", help="Pipeline completo con evidencia SINTÉTICA, sin conexión")
    x.set_defaults(f=cmd_demo)
    a = p.parse_args(argv)
    return a.f(a)


if __name__ == "__main__":
    raise SystemExit(main())
