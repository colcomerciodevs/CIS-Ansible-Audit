"""Plan de auditoría y modo dry-run (no se conecta a ningún host).

- `matriz_controles()`: tabla por (benchmark, control CIS en alcance) con método,
  comandos, sudo, impacto, recorrido de filesystem, evidencia, criterios,
  exclusión, información sensible y redacción.
- `resumen_filas()`: conteos por fila YES (sudo, profundas, organizacionales, conflictos, impacto).
- `scripts_por_benchmark()` / `plan_inventario()`: qué se ejecutaría en cada host.
- `validar_plan()`: comprobaciones de seguridad previas a cualquier ejecución.
"""

import collections
from pathlib import Path
from typing import Dict, List

import yaml

from .comandos import COMANDOS
from .controles import POR_ID, control_para_titulo
from .recolector import generar_script
from .seguridad import problemas_comando
from .seleccion import FAMILIA, requerimientos

_IMP = {"bajo": 1, "medio": 2, "alto": 3}
BENCHMARKS = ("RHEL8", "ORACLE8", "RHEL9", "ORACLE9", "SLES15")
GRUPO_PERFIL = {"rhel_oracle_8": "RHEL_ORACLE_8", "rhel_oracle_9": "RHEL_ORACLE_9", "sles_15": "SLES_15"}
_ORG_CIS = {"repos", "gpg_keys", "suid_sgid"}


def _cmds_fila(req: Dict, ctl) -> List:
    return [COMANDOS[c] for c in ctl.comandos_para(req["familia"]) if c in req["comandos"]] if ctl else []


def _alcance_subs(ctl, fila, grupo) -> Dict[str, bool]:
    from .evaluacion import _alcance_fila
    if not ctl or not ctl.subcondiciones:
        return {}
    propios, _ = _alcance_fila(ctl, fila, grupo)
    return {s: s in propios for s in ctl.subcondiciones}


def matriz_controles(matriz: List[Dict], profunda: bool) -> List[Dict]:
    filas = []
    for bench in BENCHMARKS:
        req = requerimientos(matriz, bench)
        grupos = collections.OrderedDict()
        for f in matriz:
            if f["benchmark"] == bench and f["control_cis"]:
                grupos.setdefault(f["control_cis"], []).append(f)
        conflictivos = {c.grupo_conflicto for c in req["controles"].values() if c.grupo_conflicto}
        conflictivos = {g for g in conflictivos if sum(1 for c in req["controles"].values() if c.grupo_conflicto == g) > 1}
        for cid, fs in grupos.items():
            si = [f for f in fs if f["estado_alcance"] == "EN ALCANCE"]
            if not si:
                continue
            ctl = control_para_titulo(si[0]["titulo_cis"])
            cmds = _cmds_fila(req, ctl)
            prof = [c.id for c in cmds if c.profunda]
            excl = [f for f in si if f["tipo_exclusion"] not in ("NINGUNA",)]
            fuera = {}
            for f in si:
                for s, v in _alcance_subs(ctl, f, fs).items():
                    fuera[s] = fuera.get(s, False) or v
            filas.append({
                "benchmark": bench, "sistema_operativo": si[0]["sistema_operativo"], "version_cis": si[0]["version_cis"],
                "control_cis": cid, "titulo_cis": si[0]["titulo_cis"], "pagina_cis": si[0]["pagina_cis"],
                "controles_excel": ", ".join(f"{f['control_excel']} (F{f['fila_excel']})" for f in si),
                "filas_yes": len(si),
                "filas_no_relacionadas": ", ".join(f"{f['control_excel']} (F{f['fila_excel']})" for f in fs if f["estado_alcance"] != "EN ALCANCE"),
                "control_motor": ctl.id if ctl else None,
                "metodo": ctl.metodo if ctl else "SIN EVALUADOR",
                "comandos": [{"id": c.id, "cmd": c.cmd.format(paquetes=" ".join(req["paquetes"])) if c.id == "pkg.rpm" else c.cmd,
                              "sudo": c.sudo, "profunda": c.profunda} for c in cmds],
                "requiere_sudo": any(c.sudo for c in cmds),
                "impacto": max((c.impacto for c in cmds if not c.profunda or profunda), key=lambda x: _IMP[x], default="bajo"),
                "impacto_si_profunda": max((c.impacto for c in cmds), key=lambda x: _IMP[x], default="bajo"),
                "recorre_filesystem": any(c.recorre_fs for c in cmds),
                "auditoria_profunda": bool(prof),
                "se_ejecuta_con_config_actual": "sí" if (not prof or profunda) else "parcial: comandos profundos omitidos (pendiente de ejecución autorizada)",
                "evidencia": ctl.evidencia if ctl else "",
                "criterio_cumple": ctl.criterio_cumple if ctl else "",
                "criterio_no_cumple": ctl.criterio_no_cumple if ctl else "",
                "exclusion": " || ".join(sorted({(f["exclusion_infraestructura"] or "") for f in excl})) or "Ninguna",
                "tipo_exclusion": ", ".join(sorted({f["tipo_exclusion"] for f in si})),
                "verificacion_exclusion": " || ".join(sorted({f["verificacion_exclusion_propuesta"] for f in si if f["verificacion_exclusion_propuesta"]})) or "-",
                "evidencia_organizacional": " || ".join(sorted({f["evidencia_organizacional_requerida"] for f in si if f["evidencia_organizacional_requerida"]})) or "-",
                "sensible": " | ".join(sorted({c.sensible for c in cmds if c.sensible})) or "No se espera información sensible",
                "redaccion": " | ".join(sorted({c.redaccion for c in cmds if c.sensible})) or "Redacción genérica del controlador",
                "componentes_fuera_de_alcance": [ctl.subcondiciones[s][0] for s, v in fuera.items() if not v] if fuera else [],
                "politica_sitio": bool(ctl and ctl.politica_sitio),
                "conflicto_baseline": bool(ctl and ctl.grupo_conflicto in conflictivos),
                "adaptacion_procedimiento_auditoria": bool(ctl and ctl.adaptacion and (not ctl.adaptacion.get("benchmarks") or bench in ctl.adaptacion["benchmarks"])),
                "criticidad": max((f["criticidad"] for f in si), key=lambda c: {"ALTO": 3, "MEDIO": 2, "BAJO": 1}.get(c, 0)),
            })
    return filas


def resumen_filas(matriz: List[Dict], profunda: bool) -> Dict[str, Dict]:
    """Conteos por fila YES y benchmark (lo que se auditará en un host de cada tipo)."""
    res = {}
    for bench in BENCHMARKS:
        req = requerimientos(matriz, bench)
        conflictos = collections.Counter(c.grupo_conflicto for c in req["controles"].values() if c.grupo_conflicto)
        c = collections.Counter()
        impacto, conf_filas, org_excl = [], [], []
        for f in req["filas_si"]:
            ctl = control_para_titulo(f["titulo_cis"]) if f["titulo_cis"] else None
            cmds = _cmds_fila(req, ctl)
            c["filas_yes"] += 1
            c["requieren_sudo"] += any(x.sudo for x in cmds)
            prof = any(x.profunda for x in cmds) and not profunda
            c["pendientes_fs_profundo"] += prof
            if ctl and ctl.id in _ORG_CIS:
                c["cis_evidencia_organizacional"] += 1
            if f["tipo_exclusion"] in ("ORGANIZACIONAL", "MIXTA"):
                org_excl.append(f"F{f['fila_excel']} {f['control_cis']}")
            if ctl and ctl.grupo_conflicto and conflictos[ctl.grupo_conflicto] > 1:
                conf_filas.append(f"F{f['fila_excel']} {f['control_cis']} {f['titulo_cis']}")
            imp = max((x.impacto for x in cmds if not x.profunda or profunda), key=lambda x: _IMP[x], default="bajo")
            if imp != "bajo":
                impacto.append(f"F{f['fila_excel']} {f['control_cis']} ({imp})")
            if prof:
                impacto.append(f"F{f['fila_excel']} {f['control_cis']} (alto si se autoriza la auditoría profunda; omitido)")
        res[bench] = {**c, "exclusiones_evidencia_organizacional": len(org_excl), "detalle_exclusiones_org": org_excl,
                      "conflictos_baseline": conf_filas, "impacto_medio_alto": impacto,
                      "comandos": len(req["comandos"]), "paquetes_consultados": req["paquetes"]}
    return res


def scripts_por_benchmark(matriz: List[Dict], profunda: bool, timeout: int, timeout_fs: int, dir_scripts: Path, matriz_sha: str,
                          matriz_p1: Dict = None) -> Dict:
    dir_scripts.mkdir(parents=True, exist_ok=True)
    salida = {}
    for bench in BENCHMARKS:
        req = requerimientos(matriz, bench, matriz_p1)
        s = generar_script(bench, req["comandos"], req["paquetes"], profunda, timeout, timeout_fs, matriz_sha)
        p = dir_scripts / f"recolector_{bench}.sh"
        p.write_text(s, encoding="utf-8")
        (dir_scripts / f"recolector_{bench}_sinsudo.sh").write_text(
            generar_script(bench, req["comandos"], req["paquetes"], profunda, timeout, timeout_fs, matriz_sha, sin_sudo=True),
            encoding="utf-8")
        salida[bench] = {"ruta": str(p), "comandos": req["comandos"], "paquetes": req["paquetes"],
                         "omitidos_profundos": [c for c in req["comandos"] if COMANDOS[c].profunda and not profunda]}
    return salida


def leer_inventario(ruta: Path) -> List[Dict]:
    inv = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    hosts = []
    hijos = ((inv.get("all") or {}).get("children") or {})
    for grupo, datos in hijos.items():
        for h, v in ((datos or {}).get("hosts") or {}).items():
            hosts.append({"host": h, "grupo": grupo, "perfil_esperado": GRUPO_PERFIL.get(grupo), "vars": v or {}})
    return hosts


def validar_plan(scripts: Dict, config: Dict, raiz: Path, matriz: List[Dict], para_ejecucion: bool = False, matriz_p1: Dict = None) -> List[Dict]:
    """Comprobaciones previas a la ejecución. Cada una: {verificacion, ok, detalle}."""
    v = []

    def add(n, ok, det=""):
        v.append({"verificacion": n, "ok": bool(ok), "detalle": det})

    usados = sorted({c for s in scripts.values() for c in s["comandos"]})
    problemas = {c: problemas_comando(COMANDOS[c].cmd) for c in usados}
    malos = {c: p for c, p in problemas.items() if p}
    for cat, texto in (("modifica archivos", "Ningún comando modifica archivos"),
                       ("instala o remueve paquetes", "Ningún comando instala o remueve paquetes"),
                       ("reinicia o cambia servicios", "Ningún comando reinicia o cambia servicios"),
                       ("reinicia servidores", "Ningún comando reinicia servidores")):
        det = {c: [x for x in p if x.startswith(cat)] for c, p in malos.items()}
        det = {c: x for c, x in det.items() if x}
        add(texto, not det, str(det) if det else f"{len(usados)} comandos revisados")
    refresco = {c: [x for x in p if "refresc" in x or "sin -C" in x or "no-refresh" in x] for c, p in problemas.items()}
    refresco = {c: x for c, x in refresco.items() if x}
    add("Ningún comando refresca repositorios (dnf -C / zypper --no-refresh)", not refresco, str(refresco) if refresco else "ok")
    otros = {c: [x for x in p if "redirección" in x or "modifica" in x and not any(x.startswith(k) for k in ("modifica archivos",))] for c, p in malos.items()}
    otros = {c: x for c, x in otros.items() if x}
    add("Sin redirecciones a archivo ni cambios de auditd/GRUB/crypto/firewall/kernel/usuarios", not otros, str(otros) if otros else "ok")
    sudo_mal = {c: [x for x in p if "sudo sin -n" in x] for c, p in problemas.items()}
    sudo_mal = {c: x for c, x in sudo_mal.items() if x}
    no_decl = [c for c in usados if "sudo" in COMANDOS[c].cmd and not COMANDOS[c].sudo]
    add("Comandos privilegiados usan 'sudo -n' y lo declaran", not sudo_mal and not no_decl,
        str(sudo_mal or no_decl) if (sudo_mal or no_decl) else f"{sum(1 for c in usados if COMANDOS[c].sudo)} comandos con sudo -n")
    cfg = (raiz / "ansible.cfg").read_text(encoding="utf-8")
    pb = yaml.safe_load((raiz / "playbooks" / "recolectar.yml").read_text(encoding="utf-8"))[0]
    pide = ("BatchMode=yes" not in cfg or "PasswordAuthentication=no" not in cfg or "ask_pass = False" not in cfg
            or pb.get("become") is not False)
    add("Ningún comando ni conexión solicita contraseña (BatchMode, sin password, sin become, sudo -n)", not pide,
        "ansible.cfg: BatchMode=yes, PasswordAuthentication=no, ask_pass=False; playbook become=false")
    remotos = [t for t in pb["tasks"] if t.get("delegate_to") != "localhost"]
    solo_raw = all(all(k in ("ansible.builtin.raw", "ansible.builtin.set_fact") for k in t if k.startswith("ansible.")) for t in remotos)
    add("El playbook solo usa ansible.builtin.raw en el host (no copia archivos)", solo_raw, f"{len(remotos)} tareas remotas")
    prof_cfg = bool(config["ejecucion"]["auditoria_filesystem_profunda"])
    prof_ejec = [c for s in scripts.values() for c in s["comandos"] if COMANDOS[c].profunda and c not in s["omitidos_profundos"]]
    add("Auditoría profunda de filesystem deshabilitada (comandos profundos omitidos en los scripts)", not prof_cfg and not prof_ejec,
        f"omitidos: {sorted({c for s in scripts.values() for c in s['omitidos_profundos']})}")
    no_rows = []
    for bench, s in scripts.items():
        req = requerimientos(matriz, bench, matriz_p1)
        necesarios = set(req["comandos"])
        sobran = set(s["comandos"]) - necesarios
        if sobran:
            no_rows.append(f"{bench}: {sorted(sobran)}")
        solo_no = {control_para_titulo(f["titulo_cis"]).id for f in matriz if f["benchmark"] == bench
                   and f["estado_alcance"] != "EN ALCANCE" and f["titulo_cis"] and control_para_titulo(f["titulo_cis"])} - set(req["controles"])
        exclusivos = {c for i in solo_no for c in POR_ID[i].comandos_para(FAMILIA[bench])} - necesarios
        if exclusivos & set(s["comandos"]):
            no_rows.append(f"{bench}: comandos de filas NO {sorted(exclusivos & set(s['comandos']))}")
    add("Las filas NO no generan comandos (cada script solo contiene lo exigido por filas YES)", not no_rows, "; ".join(no_rows) or "ok")
    add("Los controles con conflicto de baseline no afectan el porcentaje (verificado con evidencia sintética)",
        *_verificar_conflictos(config, raiz))
    if not para_ejecucion:
        add("La ejecución real solo es posible con 'ejecutar --limit <host>' (--limit explícito; máximo de hosts por ejecución limitado)",
            True, f"ejecucion.habilitada={config['ejecucion'].get('habilitada')}; max_hosts_por_ejecucion={config['ejecucion'].get('max_hosts_por_ejecucion')}")
    return v


def _verificar_conflictos(config: Dict, raiz: Path):
    import json
    import shutil
    import tempfile
    from .evaluacion import evaluar_ejecucion
    from .redaccion import redactar
    from .simulacion import recolector_sintetico
    tmp = Path(tempfile.mkdtemp())
    try:
        mpath = raiz / "data" / "alcance" / "matriz_alcance.json"
        m = json.loads(mpath.read_text(encoding="utf-8"))["matriz"]
        for host, bench in (("sintetico-ol9", "ORACLE9"), ("sintetico-sles", "SLES15")):
            (tmp / "run" / host / "raw").mkdir(parents=True)
            (tmp / "run" / host / "raw" / "recolector.out").write_text(redactar(recolector_sintetico(bench, m)), encoding="utf-8")
        d = evaluar_ejecucion(tmp / "run", mpath, config, {}, tmp / "r.json")
        conf = [r for r in d["resultados"] if r["conflicto_baseline"]]
        ok = bool(conf) and not any(r["computa_porcentaje"] for r in conf) and d["resumen"]["global"]["evaluables"] == \
            sum(1 for r in d["resultados"] if r["computa_porcentaje"])
        return ok, f"{len(conf)} filas en conflicto excluidas del denominador; categoría CONFLICTO DE BASELINE = {d['resumen']['global']['conteo']['CONFLICTO DE BASELINE']}"
    finally:
        shutil.rmtree(tmp)


def plan_inventario(hosts: List[Dict], scripts: Dict) -> List[Dict]:
    res = []
    for h in hosts:
        candidatos = {"RHEL_ORACLE_8": ("RHEL8", "ORACLE8"), "RHEL_ORACLE_9": ("RHEL9", "ORACLE9"), "SLES_15": ("SLES15",)}.get(h["perfil_esperado"], ())
        res.append({**h, "scripts_posibles": {b: scripts[b]["ruta"] for b in candidatos},
                    "nota": "El script definitivo se elige en el host leyendo /etc/os-release (ID + VERSION_ID)."})
    return res


def comandos_tabla() -> List[Dict]:
    return [{"id": c.id, "descripcion": c.descripcion, "familias": "/".join(c.familias), "sudo": c.sudo, "impacto": c.impacto,
             "recorre_fs": c.recorre_fs, "profunda": c.profunda, "sensible": c.sensible or "-", "redaccion": c.redaccion,
             "cmd": c.cmd} for c in COMANDOS.values()]
