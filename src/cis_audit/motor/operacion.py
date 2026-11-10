"""Operación de auditorías remotas: validaciones de ejecución, precheck, gate de confianza y resumen.

Reglas de ejecución:
- `--limit` obligatorio, solo con nombres de host del inventario (sin grupos, `all` ni comodines)
  y como máximo `ejecucion.max_hosts_por_ejecucion` hosts.
- Inventario sin marcadores pendientes, con `ansible_user` y llave SSH local fuera del proyecto.
- Precheck read-only antes de auditar: si el host no corresponde al sistema esperado o no tiene
  benchmark soportado, no se audita (nunca se usa un benchmark incorrecto).
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .seleccion import requerimientos

MARCADOR = "COMPLETAR"
_PATRON = re.compile(r"[*?\[\]!&:~@,]|^all$|^ungrouped$")
_ESPERADO = {"rhel_oracle_8": "RHEL_ORACLE_8", "rhel_oracle_9": "RHEL_ORACLE_9", "sles_15": "SLES_15"}


class ErrorOperacion(Exception):
    pass


def leer_inventario(ruta: Path) -> Dict[str, Dict]:
    """{host: {grupo, vars}} combinando all.vars, vars de grupo y de host."""
    if not ruta.exists():
        raise ErrorOperacion(f"No existe el inventario {ruta}")
    inv = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    todos = inv.get("all") or {}
    base = dict(todos.get("vars") or {})
    hosts = {}
    for grupo, datos in (todos.get("children") or {}).items():
        datos = datos or {}
        gvars = {**base, **(datos.get("vars") or {})}
        for h, hv in (datos.get("hosts") or {}).items():
            if h in hosts:
                raise ErrorOperacion(f"El host {h} figura en más de un grupo")
            hosts[h] = {"grupo": grupo, "vars": {**gvars, **(hv or {})}}
    return hosts


def validar_limit(limit: Optional[str], hosts: Dict[str, Dict], maximo: int) -> List[str]:
    if not limit or not limit.strip():
        raise ErrorOperacion("ABORTADO: --limit es obligatorio (nombres de host explícitos, separados por coma).")
    sel = [x.strip() for x in limit.split(",") if x.strip()]
    for h in sel:
        if _PATRON.search(h):
            raise ErrorOperacion(f"ABORTADO: '{h}' no es un nombre de host explícito (no se admiten grupos, 'all' ni patrones).")
        if h not in hosts:
            grupos = {v["grupo"] for v in hosts.values()}
            raise ErrorOperacion(f"ABORTADO: '{h}' no es un host del inventario" + (" (es un grupo)" if h in grupos else "") + ".")
    if len(set(sel)) != len(sel):
        raise ErrorOperacion("ABORTADO: host repetido en --limit.")
    if len(sel) > maximo:
        raise ErrorOperacion(f"ABORTADO: se admiten como máximo {maximo} hosts por ejecución ({len(sel)} indicados).")
    return sel


def validar_hosts(sel: List[str], hosts: Dict[str, Dict], raiz: Path) -> List[str]:
    """Problemas de configuración de los hosts seleccionados (vacío = OK). No modifica nada."""
    problemas = []
    for h in sel:
        v = hosts[h]["vars"]
        for k in ("ansible_host", "ansible_user", "ansible_ssh_private_key_file"):
            if not v.get(k) or MARCADOR in str(v.get(k)):
                problemas.append(f"{h}: falta completar {k}")
        if any(k in v for k in ("ansible_password", "ansible_ssh_pass", "ansible_become_pass", "ansible_become_password")):
            problemas.append(f"{h}: el inventario no debe contener contraseñas")
        if hosts[h]["grupo"] not in _ESPERADO:
            problemas.append(f"{h}: grupo '{hosts[h]['grupo']}' no reconocido (rhel_oracle_8, rhel_oracle_9, sles_15)")
        llave = v.get("ansible_ssh_private_key_file")
        if llave and MARCADOR not in str(llave):
            p = Path(str(llave)).expanduser()
            if not p.is_file():
                problemas.append(f"{h}: la llave {p} no existe en el controlador")
            else:
                try:
                    p.resolve().relative_to(raiz.resolve())
                    problemas.append(f"{h}: la llave privada está dentro del proyecto ({p}); debe estar fuera del repositorio")
                except ValueError:
                    pass
                if p.stat().st_mode & 0o077:
                    problemas.append(f"{h}: la llave {p} tiene permisos {oct(p.stat().st_mode & 0o777)}; SSH exige 0600 o más restrictivo")
    return problemas


def interpretar_privilegiado(lineas: List[str], sha_esperado: Optional[str], prefijo: str = "priv_", ruta: Optional[str] = None) -> Dict:
    """Estado del recolector privilegiado según el precheck (solo lectura).

    NO_INSTALADO  -> no existe la ruta: los controles privilegiados quedan en PERMISOS_INSUFICIENTES.
    NO_CONFIABLE  -> no es archivo regular, es enlace, owner/grupo/modo, ACL, enlaces de directorios,
                     directorios padre o SHA-256 no coinciden: NO se ejecuta
                     (ERROR DE AUDITORÍA, RECOLECTOR_PRIVILEGIADO_NO_CONFIABLE).
    NO_AUTORIZADO -> archivo confiable pero "sudo -n -l <ruta>" no lo autoriza: PERMISOS_INSUFICIENTES.
    CONFIABLE     -> se ejecuta "sudo -n <ruta>" (sin argumentos).
    """
    from .privilegiado import RUTA, verificar_instalacion
    ruta = ruta or RUTA
    P = prefijo
    campos = [l.split("|", 2)[1:] for l in lineas if l.startswith(f"@@PRE|{P}")]
    kv = {k: v for k, v in ((c[0], c[1] if len(c) > 1 else "") for c in campos)}
    if kv.get(f"{P}existe") != "si":
        return {"estado": "NO_INSTALADO", "problemas": [], "detalle": f"no existe {ruta}"}
    objetos, acls = {}, {}
    for l in lineas:
        p = l.split("|")
        if l.startswith(f"@@PRE|{P}obj|") and len(p) == 12:
            objetos[p[2]] = {"uid_l": p[3], "gid_l": p[4], "tipo_l": p[5], "destino": p[6],
                             "uid": p[7], "gid": p[8], "modo": p[9], "tipo": p[10], "acl": p[11].strip()}
        elif l.startswith(f"@@PRE|{P}acl|") and len(p) >= 5:
            acls[p[2]] = {"rc": p[3], "entradas": "|".join(p[4:]).strip()}
    sha = kv.get(f"{P}sha", "").strip()
    problemas = verificar_instalacion(objetos, acls, sha, sha_esperado or "", ruta)
    sudo_l = next((l.split("|", 3)[2:] for l in lineas if l.startswith(f"@@PRE|{P}sudo_l|")), ["", ""])
    res = {"sha256_encontrado": sha, "sha256_esperado": sha_esperado, "metadatos": objetos, "acl": acls,
           "selinux": kv.get(f"{P}selinux", "").strip()[:300], "problemas": problemas,
           "sudo_l_rc": sudo_l[0], "sudo_l_salida": (sudo_l[1] if len(sudo_l) > 1 else "").strip()[:300]}
    if problemas:
        return {**res, "estado": "NO_CONFIABLE", "detalle": "; ".join(problemas)}
    if sudo_l[0] != "0":
        return {**res, "estado": "NO_AUTORIZADO", "detalle": "sudo -n -l no autoriza el recolector para la cuenta de auditoría"}
    return {**res, "estado": "CONFIABLE",
            "detalle": "archivo regular, owner/grupo/modo, ACL, enlaces, directorios padre y SHA-256 verificados; autorizado en sudoers"}


def impacto_fs(lineas: List[str]) -> Dict:
    """Precheck de impacto de filesystem profundo (informativo; NO recorre nada): montajes de findmnt, tamaño (df -PTk)
    e inodos usados (df -Pi), con la elegibilidad por control según los filtros literales del Audit CIS."""
    from .filesystem import RE_DESTINO_7111, RE_DESTINO_RUN_USER, RE_TIPOS_EXCLUIDOS
    tx = re.compile(RE_TIPOS_EXCLUIDOS.replace("[[:space:]]", r"\s"))
    nodev = set(next((l.split("|", 2)[2] for l in lineas if l.startswith("@@PRE|fs_nodev|")), "").split(","))
    df = {p[2]: {"fstype": p[3], "kb": p[4]} for p in (l.split("|") for l in lineas if l.startswith("@@PRE|fs_df|")) if len(p) >= 5}
    di = {p[2]: p[3] for p in (l.split("|") for l in lineas if l.startswith("@@PRE|fs_dfi|")) if len(p) >= 4}
    filas = []
    for l in lineas:
        if not l.startswith("@@PRE|fs_mnt|"):
            continue
        c = l.split("|", 2)[2].split()
        if len(c) < 3:
            continue
        ft, t, op = c[0], c[1], c[2]
        ex = bool(tx.match(ft))
        filas.append({"filesystem": ft, "montaje": t, "inodos_usados": di.get(t), "tamano_kb": (df.get(t) or {}).get("kb"),
                      "7.1.11": not ex and not re.match(RE_DESTINO_7111, t), "7.1.12": not ex and not re.match(RE_DESTINO_RUN_USER, t),
                      "7.1.13": not ex and not re.match(RE_DESTINO_RUN_USER, t) and "noexec" not in op and "nosuid" not in op,
                      "6.3.3.6": ft not in nodev and "noexec" not in op and "nosuid" not in op})
    for f in filas:
        f["elegible"] = any(f[k] for k in ("7.1.11", "7.1.12", "7.1.13", "6.3.3.6"))
    total = sum(int(f["inodos_usados"]) for f in filas if f["elegible"] and str(f["inodos_usados"] or "").isdigit())
    remotos = [f["montaje"] for f in filas if f["elegible"] and re.match(r"(cifs|smb3|fuse|nfs|9p|ceph|glusterfs)", f["filesystem"])]
    return {"montajes": filas, "inodos_elegibles": total, "remotos_o_fuse_elegibles": remotos}


def interpretar_precheck(datos: Dict, grupo: str, perfiles: Dict, sha_privilegiado: Optional[str] = None,
                         sha_fs: Optional[str] = None) -> Dict:
    """Resultado del precheck de un host a partir de precheck_ansible.json."""
    res = {"host": datos.get("host"), "grupo_inventario": grupo, "perfil_esperado_inventario": _ESPERADO.get(grupo),
           "conectividad_ssh": not datos.get("inalcanzable") and datos.get("rc") == 0, "continuar": False}
    if datos.get("inalcanzable") or datos.get("rc") not in (0,):
        res["motivo_detencion"] = "SSH_FALLIDO: " + (datos.get("mensaje") or datos.get("stderr") or "sin respuesta").strip()[:300]
        return res
    campos, osr, en_osr = {}, {}, False
    for l in (datos.get("stdout") or "").replace("\r", "").splitlines():
        if l == "@@PRE|os_release_inicio":
            en_osr = True; continue
        if l == "@@PRE|os_release_fin":
            en_osr = False; continue
        if en_osr and "=" in l:
            k, v = l.split("=", 1)
            osr[k.strip()] = v.strip().strip('"')
        elif l.startswith("@@PRE|"):
            _, k, v = (l.split("|", 2) + [""])[:3]
            campos[k] = v
    version = osr.get("VERSION_ID", "")
    major = version.split(".")[0] if version else ""
    p = (perfiles.get((osr.get("ID") or "").lower()) or {}).get(major)
    res.update({
        "hostname": campos.get("hostname"), "kernel": campos.get("kernel"), "arquitectura": campos.get("arch"),
        "usuario_conectado": campos.get("user"), "sudo_no_interactivo": campos.get("sudo_n") == "si",
        "os_id": osr.get("ID"), "os_nombre": osr.get("PRETTY_NAME") or osr.get("NAME"), "version": version,
        "perfil_detectado": p["perfil"] if p else None, "benchmark_seleccionado": p["benchmark"] if p else None,
        "recolector_privilegiado": interpretar_privilegiado((datos.get("stdout") or "").replace("\r", "").splitlines(), sha_privilegiado),
    })
    lineas = (datos.get("stdout") or "").replace("\r", "").splitlines()
    if any(l.startswith("@@PRE|fs_existe|") for l in lineas):
        from .filesystem import RUTA_FS
        res["recolector_fs"] = interpretar_privilegiado(lineas, sha_fs, "fs_", RUTA_FS)
        res["impacto_fs"] = impacto_fs(lineas)
    if not p:
        res["motivo_detencion"] = f"SO_NO_SOPORTADO: ID={osr.get('ID')} VERSION_ID={version}"
    elif res["perfil_esperado_inventario"] and p["perfil"] != res["perfil_esperado_inventario"]:
        res["motivo_detencion"] = (f"SO_NO_CORRESPONDE: el inventario espera {res['perfil_esperado_inventario']} "
                                   f"pero /etc/os-release indica {p['perfil']} ({osr.get('PRETTY_NAME')})")
    else:
        res["continuar"] = True
    return res


MENSAJE_GATE = ("EJECUCIÓN ABORTADA: el recolector privilegiado no superó el precheck de confianza. "
                "Ejecute primero 'python3 src/auditar.py precheck --limit {hosts}' y corrija la condición detectada.")


def recolector_esperado(cfg: Dict) -> bool:
    """El proyecto espera el recolector privilegiado si config/audit.yml define su ruta."""
    return bool((cfg.get("recolector_privilegiado") or {}).get("ruta"))


def evaluar_gate(pre: Dict[str, Dict], cfg: Dict, fs: bool = False) -> Dict[str, Dict]:
    """Gate obligatorio previo a la recolección: {host: {permitido, motivo}}.

    Si se espera el recolector privilegiado, solo se permite continuar con estado CONFIABLE (archivo regular,
    owner/grupo/modo, ACL, enlaces, directorios padre, SHA-256 y "sudo -n -l <ruta>" específico). Cualquier
    otro estado (NO_CONFIABLE, NO_AUTORIZADO, NO_INSTALADO, ausente) bloquea. "sudo -n true" (sudo general)
    NO interviene: srvinnops no tiene sudo general por diseño. No existe override."""
    import re
    priv = cfg.get("recolector_privilegiado") or {}
    res = {}
    for h, r in pre.items():
        rp = r.get("recolector_privilegiado") or {}
        if not r.get("continuar"):
            res[h] = {"permitido": False, "motivo": f"precheck detenido: {r.get('motivo_detencion') or 'sin detalle'}"}
        elif not recolector_esperado(cfg):
            res[h] = {"permitido": True, "motivo": "recolector privilegiado no configurado en config/audit.yml (no se espera)"}
        elif not re.fullmatch(r"[0-9a-f]{64}", str(priv.get("sha256") or "")):
            res[h] = {"permitido": False, "motivo": "recolector_privilegiado.sha256 ausente o inválido en config/audit.yml"}
        elif priv.get("version") and priv.get("version") != "1.1.0" and not re.fullmatch(r"[0-9a-f]{64}", str(priv.get("sudoers_sha256_archivo") or "")):
            # D-3B-01: sin el SHA-256 físico aprobado del sudoers no puede identificarse la instrumentación.
            res[h] = {"permitido": False, "motivo": "recolector_privilegiado.sudoers_sha256_archivo ausente o inválido en config/audit.yml"}
        elif rp.get("estado") != "CONFIABLE":
            res[h] = {"permitido": False,
                      "motivo": f"recolector privilegiado: {rp.get('estado') or 'SIN_RESULTADO'} — {rp.get('detalle') or 'sin detalle'}"}
        elif rp.get("sha256_encontrado") != priv["sha256"] or str(rp.get("sudo_l_rc")) != "0":
            res[h] = {"permitido": False, "motivo": "recolector privilegiado: resultado CONFIABLE incoherente (digest o sudo -n -l)"}
        else:
            res[h] = {"permitido": True, "motivo": f"recolector privilegiado: CONFIABLE — {rp.get('detalle')}"}
        if fs and res[h]["permitido"]:
            # Filesystem profundo solicitado explícitamente -> el recolector FS debe ser CONFIABLE (sin degradar).
            cfs, rfs = cfg.get("filesystem_profundo") or {}, r.get("recolector_fs") or {}
            if not re.fullmatch(r"[0-9a-f]{64}", str(cfs.get("sha256") or "")) or \
                    not re.fullmatch(r"[0-9a-f]{64}", str(cfs.get("sudoers_sha256_archivo") or "")):
                res[h] = {"permitido": False, "motivo": "filesystem_profundo.sha256/sudoers_sha256_archivo ausentes o inválidos en config/audit.yml"}
            elif rfs.get("estado") != "CONFIABLE":
                res[h] = {"permitido": False, "motivo": f"recolector FS: {rfs.get('estado') or 'SIN_RESULTADO'} — {rfs.get('detalle') or 'sin detalle'}"}
            elif rfs.get("sha256_encontrado") != cfs["sha256"] or str(rfs.get("sudo_l_rc")) != "0":
                res[h] = {"permitido": False, "motivo": "recolector FS: resultado CONFIABLE incoherente (digest o sudo -n -l)"}
            else:
                res[h]["motivo"] += f" | recolector FS: CONFIABLE — {rfs.get('detalle')}"
    return res


def conteos_host(matriz: List[Dict], benchmark: str, profunda: bool, sudo: bool) -> Dict:
    from .plan import resumen_filas
    r = resumen_filas(matriz, profunda)[benchmark]
    req = requerimientos(matriz, benchmark)
    return {
        "filas_yes": r["filas_yes"],
        "filas_evaluadas_en_esta_ejecucion": r["filas_yes"] - r["pendientes_fs_profundo"],
        "requieren_sudo": r["requieren_sudo"],
        "sudo_disponible": sudo,
        "previstas_error_por_falta_de_sudo": 0 if sudo else r["requieren_sudo"],
        "omitidas_fs_profundo": r["pendientes_fs_profundo"],
        "cis_evidencia_organizacional": r["cis_evidencia_organizacional"],
        "exclusiones_evidencia_organizacional": r["exclusiones_evidencia_organizacional"],
        "conflictos_baseline": r["conflictos_baseline"],
        "comandos_en_script": len(req["comandos"]),
    }


def comandos_inesperados(dir_host: Path) -> List[Dict]:
    """Comandos con rc fuera de lo esperado, timeout, sudo denegado o stderr no vacío (ya redactado)."""
    from .base import Ctx
    from .comandos import COMANDOS
    from .recolector import parsear
    raw = dir_host / "raw" / "recolector.out"
    if not raw.exists():
        return []
    rec = parsear(raw.read_text(encoding="utf-8", errors="replace"))
    ctx = Ctx(rec.registros, "", rec.familia or "", {})
    out = []
    for cid, r in rec.registros.items():
        if r.omitido:
            continue
        prob = ctx.problema(cid)
        ruido = (r.stderr or "").strip()
        if prob or (ruido and COMANDOS.get(cid) and COMANDOS[cid].rc_validos is not None):
            out.append({"comando_id": cid, "rc": r.rc, "problema": prob, "stderr": ruido[:300], "duracion_ms": r.ms})
    return out
