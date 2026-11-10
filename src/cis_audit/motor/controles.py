"""Catálogo de controles evaluables: método, evidencia, criterio exacto y evaluador.

Un `Control` se vincula a las recomendaciones del CIS por su título oficial (los
títulos son estables entre Benchmark; la numeración no). Los criterios se
tomaron de la sección *Audit* de los Benchmark suministrados; cuando un
Benchmark difiere (RHEL/OL 8 v4.0.0 vs RHEL/OL 9 v2.0.0 vs SLES 15 v2.0.1) el
evaluador lo distingue explícitamente.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Dict, List, Optional, Tuple

from . import auditd_reglas as ar
from .base import (RE_SYSTEMD_SIN_ACCESO, ERROR, FALLA, INFO, MANUAL, NA, OK, ORG, Ctx, Evaluacion, archivos_catconfig, config_efectiva, lista_archivos, max_modo,
                   modo_ok)
from .comandos import COMANDOS, RHEL, SLES


@dataclass
class Control:
    id: str
    titulos: Tuple[str, ...]
    comandos: Tuple[str, ...]
    metodo: str
    evidencia: str
    criterio_cumple: str
    criterio_no_cumple: str
    evaluar: Callable[[Ctx], Evaluacion]
    familias: Tuple[str, ...] = (RHEL, SLES)
    subcondiciones: Dict[str, Tuple[str, Tuple[str, ...]]] = field(default_factory=dict)
    grupo_conflicto: Optional[str] = None
    politica_sitio: bool = False
    permite_parcial: bool = True  # False: los componentes son atributos de un único requisito
    paquetes: Tuple[str, ...] = ()  # paquetes que este control consulta con rpm -q
    # Adaptación documentada del procedimiento Audit del CIS (nunca del requisito):
    # {"benchmarks": (...)|None, "procedimiento_original", "comportamiento_esperado", "adaptacion", "justificacion"}
    adaptacion: Optional[Dict] = None
    notas: str = ""

    def comandos_para(self, familia: str) -> List[str]:
        return [c for c in self.comandos if c in COMANDOS and familia in COMANDOS[c].familias]


def _v8(ctx: Ctx) -> bool:
    return ctx.benchmark in ("RHEL8", "ORACLE8")


# ===================================================================== utilidades

def paquetes(ctx: Ctx) -> Dict[str, Optional[str]]:
    """{nombre: versión|None} a partir de `rpm -q` (None = no instalado)."""
    res = {}
    for l in ctx.lineas("pkg.rpm"):
        m = re.match(r"package (\S+) is not installed", l)
        if m:
            res[m.group(1)] = None
        else:
            res.setdefault(_nombre_rpm(l), l.strip())
    return res


def _nombre_rpm(nvr: str) -> str:
    partes = nvr.strip().rsplit("-", 2)
    return partes[0] if len(partes) == 3 else nvr.strip()


def _pkg_comp(ev: Evaluacion, ctx: Ctx, nombre: str, debe_estar: bool, cid: str = None):
    prob = ctx.problema("pkg.rpm")
    pk = paquetes(ctx)
    cid = cid or f"paquete_{nombre}"
    esperado = f"{nombre} {'instalado' if debe_estar else 'no instalado'}"
    if prob or nombre not in pk:
        return ev.add(cid, esperado, ERROR, esperado, prob or "sin dato de rpm -q", ["pkg.rpm"])
    inst = pk[nombre] is not None
    return ev.add(cid, esperado, OK if inst == debe_estar else FALLA, esperado,
                  pk[nombre] or f"{nombre} no instalado", ["pkg.rpm"])


def _gdm_instalado(ctx: Ctx) -> Optional[bool]:
    if ctx.problema("pkg.rpm"):
        return None
    pk = paquetes(ctx)
    return any(pk.get(p) for p in ("gdm", "gdm3"))


def unidades(ctx: Ctx, cid: str) -> Dict[str, Tuple[str, str]]:
    """{unidad: (is-enabled, is-active)} desde la salida etiquetada 'unidad|enabled|active'.

    Mecanismo único de consulta de servicios: una respuesta sin acceso a systemd ('Failed to connect to bus: Operation not
    permitted', permisos, D-Bus) NO es un estado y la unidad se omite del resultado; ctx.problema(cid) la informa como
    [SYSTEMD_NO_CONSULTABLE]. 'not-found'/'No such file' = la unidad no existe."""
    res = {}
    for l in ctx.lineas(cid):
        p = l.split("|")
        if len(p) == 3:
            en, ac = p[1].strip(), p[2].strip()
            if RE_SYSTEMD_SIN_ACCESO.search(en) or RE_SYSTEMD_SIN_ACCESO.search(ac):
                continue
            res[p[0].strip()] = ("no-existe" if "No such file" in en or "not-found" in en else en, ac)
    return res


def _unidades(ctx: Ctx) -> Dict[str, Tuple[str, str]]:
    return {k: (v[0] if v[0] != "no-existe" else "not-found", v[1]) for k, v in unidades(ctx, "journald.remoto").items()}


def _kv(lineas: List[str], sep="=") -> Dict[str, str]:
    d = {}
    for l in lineas:
        if sep in l:
            k, v = l.split(sep, 1)
            d[k.strip().lower()] = v.strip()
    return d


def _auditd_conf(ctx: Ctx) -> Dict[str, str]:
    return _kv(ctx.lineas("auditd.conf"))


def _estado_svc(ev, ctx, cid, unidad, quiere_enabled=("enabled",), quiere_active=("active",), comp=""):
    prob = ctx.problema(cid)
    u = unidades(ctx, cid)
    nombre = next((k for k in u if k in (unidad, unidad + ".service")), None)
    if prob or not nombre:
        ev.add(comp + "estado", f"{unidad} habilitado y activo", ERROR, "", prob or "[SALIDA_NO_INTERPRETABLE] salida incompleta", [cid])
        return
    en, ac = u[nombre]
    if quiere_enabled:
        ev.add(comp + "habilitado", f"{unidad} is-enabled", OK if en in quiere_enabled else FALLA, " o ".join(quiere_enabled), en, [cid])
    ev.add(comp + "activo", f"{unidad} is-active", OK if ac in quiere_active else FALLA, " o ".join(quiere_active), ac, [cid])


def _evid(ctx: Ctx, cid: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """(líneas, None) de un grupo de evidencia, o (None, problema). Para los grupos del recolector privilegiado el problema
    distingue recolector no confiable, fallido, no ejecutado o de una versión anterior (REQUIERE_EXTENSION_RECOLECTOR).
    Sin evidencia nunca se concluye CUMPLE ni NO CUMPLE."""
    from .controles_p1 import _priv
    prob = _priv(ctx, cid) if cid.startswith("priv.") else ctx.problema(cid)
    return (None, prob) if prob else (ctx.lineas(cid), None)


def _sin_evidencia(prob: str) -> str:
    """Estado de un componente decisorio sin evidencia: versión anterior del recolector -> MANUAL; resto -> ERROR."""
    return MANUAL if (prob or "").startswith("[REQUIERE_EXTENSION_RECOLECTOR]") else ERROR


def _grubby_entradas(ctx: Ctx) -> List[Tuple[str, str]]:
    ents, k = [], None
    for l in ctx.lineas("grub.grubby"):
        if l.startswith("kernel="):
            k = l.split("=", 1)[1].strip().strip('"')
        elif l.startswith("args="):
            ents.append((k or "?", l.split("=", 1)[1].strip().strip('"')))
            k = None
    return ents


def _etiqueta_regla(texto: str) -> str:
    m = re.search(r"-w\s+(\S+)", texto)
    if m:
        return m.group(1)
    s = re.search(r"-S\s+(\S+)", texto)
    a = re.search(r"arch=(b\d\d)", texto)
    p = re.search(r"-F\s+path=(\S+)", texto)
    return (p.group(1) if p else (s.group(1) if s else texto[:60])) + (f" ({a.group(1)})" if a else "")


_RE_TMPF_CIS = re.compile(r"^\s*[a-z]+\s+[^\s]+\s+0*([6-7][4-7][1-7]|7[0-7][0-7])\s+", re.I)


def _modo_tmpfiles(modo: str, es_dir: bool) -> Optional[int]:
    """Modo resultante de una entrada tmpfiles.d para un archivo/directorio. '~' (tmpfiles.d(5)): en archivos sin bits
    de ejecución se eliminan los de ejecución; SUID/SGID/sticky solo se conservan en directorios."""
    m = modo.lstrip("~:")
    if not re.fullmatch(r"[0-7]{3,4}", m):
        return None
    v = int(m, 8)
    if not es_dir:
        if modo.startswith("~"):
            v &= ~0o111
        v &= 0o777
    return v


def _sin_coment_rs(t: str) -> str:
    """Quita un comentario '#' fuera de comillas."""
    q = None
    for i, ch in enumerate(t):
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == "#":
            return t[:i]
    return t


def _balance(t: str) -> int:
    t = re.sub(r'"[^"]*"|\'[^\']*\'', "", t)
    return t.count("(") - t.count(")")


def _rsyslog_sentencias(ctx: Ctx) -> List[Tuple[str, str]]:
    """[(archivo, sentencia)] en orden de lectura: une las sentencias RainerScript multilínea y quita comentarios."""
    res, buf, org = [], "", None
    for l in ctx.lineas("rsyslog.conf"):
        f, sep, t = l.partition(":")
        if not sep or not f.startswith("/"):
            f, t = "", l
        t = _sin_coment_rs(t).strip()
        if not t and not buf:
            continue
        buf, org = (buf + " " + t, org) if buf else (t, f)
        if _balance(buf) <= 0:
            res.append((org, buf.strip())); buf = ""
    if buf:
        res.append((org, buf.strip()))
    return res


def _rs_params(t: str) -> Dict[str, str]:
    return {k.lower(): v for k, v in re.findall(r'([A-Za-z][\w.]*)\s*=\s*"([^"]*)"', t)}


def _rs_accion(t: str) -> Optional[Dict]:
    """Acción de una sentencia rsyslog: {'tipo': archivo|reenvio|usuarios|descarte|otra, 'destino', 'modo'} o None."""
    tl = t.lower()
    if tl.startswith(("$", "module(", "input(", "global(", "include(", "template(", "ruleset(", "main_queue(", "}")):
        return None
    if "action(" in tl:
        prm = _rs_params(t[tl.index("action("):])
        ty = prm.get("type", "").lower()
        if ty == "omfile":
            return {"tipo": "archivo", "destino": prm.get("file") or prm.get("dynafile") or "?", "modo": prm.get("filecreatemode")}
        if ty == "omfwd":
            return {"tipo": "reenvio", "destino": f"{prm.get('protocol', 'udp').lower()} {prm.get('target', '?')}", "modo": None}
        return {"tipo": "otra", "destino": ty, "modo": None}
    m = re.search(r"\bthen\s+(\S+)\s*$", t) or re.match(r'^:\S+,\s*!?\S+,\s*"[^"]*"\s+(\S+)$', t) or re.match(r"^[^\s(]+\s+(\S+)$", t)
    if not m:
        return None
    d = m.group(1).split(";", 1)[0]
    if d.startswith("-/") or d.startswith("/"):
        return {"tipo": "archivo", "destino": d.lstrip("-"), "modo": None}
    if d.startswith("@"):
        return {"tipo": "reenvio", "destino": ("tcp " if d.startswith("@@") else "udp ") + d.lstrip("@"), "modo": None}
    if d.startswith(":omusrmsg:"):
        return {"tipo": "usuarios", "destino": d, "modo": None}
    if d in ("~", "stop"):
        return {"tipo": "descarte", "destino": d, "modo": None}
    return {"tipo": "otra", "destino": d, "modo": None}


def _rs_reglas_legacy(ctx: Ctx) -> List[Tuple[str, str, str]]:
    """[(selector, destino, archivo)] de las reglas selector/acción (formato basic/legacy)."""
    res = []
    for org, t in _rsyslog_sentencias(ctx):
        m = re.match(r"^([^\s$:(][^\s]*)\s+(\S+)$", t)
        if not m or t.lower().startswith(("if ", "module(", "input(", "global(", "include(", "template(", "ruleset(", "action(")):
            continue
        if not re.search(r"[*.]", m.group(1)):
            continue
        res.append((m.group(1), m.group(2).split(";", 1)[0].lstrip("-") if m.group(2).startswith(("-", "/")) else m.group(2), org))
    return res


_RE_MODO_RSYSLOG_CIS = re.compile(r"^0[0246][024]0$")


def _selector_norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def _rs_archivos(ctx: Ctx) -> List[str]:
    vistos = []
    for _, t in _rsyslog_sentencias(ctx):
        a = _rs_accion(t)
        if a and a["tipo"] == "archivo" and a["destino"].startswith("/") and not a["destino"].startswith("/dev/") and a["destino"] not in vistos:
            vistos.append(a["destino"])
    return vistos


def _logrotate_parse(lineas: List[str]):
    """LC|archivo|n:texto -> (globales, bloques[{patrones, directivas, postrotate, archivo}]) en orden de lectura."""
    import shlex as _sh
    glob_, bloques, pend, actual, en_script = {}, [], [], None, None
    for l in lineas:
        if not l.startswith("LC|"):
            continue
        _, f, resto = l.split("|", 2)
        t = resto.split(":", 1)[1].strip() if ":" in resto else resto.strip()
        if en_script is not None:
            if t == "endscript":
                actual["scripts"][en_script] = actual["scripts"].get(en_script, "") ; en_script = None
            else:
                actual["scripts"][en_script] = (actual["scripts"].get(en_script, "") + " " + t).strip()
            continue
        if actual is None:
            if "{" in t:
                antes = t.split("{", 1)[0]
                pend += [x for x in _sh.split(antes)] if antes.strip() else []
                actual = {"patrones": pend, "directivas": {}, "scripts": {}, "archivo": f}
                pend = []
                continue
            if t.startswith("/") or t.startswith('"/'):
                pend += _sh.split(t); continue
            k, _, v = t.partition(" ")
            glob_[k] = v.strip()
            continue
        if t.startswith("}"):
            bloques.append(actual); actual = None; continue
        if t in ("postrotate", "prerotate", "firstaction", "lastaction", "preremove"):
            en_script = t; continue
        k, _, v = t.partition(" ")
        actual["directivas"][k] = v.strip()
    return glob_, bloques


def _politica_logrotate(efectivas: Dict[str, str], scripts: Dict[str, str], pol: Dict) -> List[str]:
    falta = []
    for d in pol.get("directivas") or []:
        k, _, v = str(d).partition(" ")
        if k in ("weekly", "daily", "monthly", "yearly"):
            orden = {"daily": 1, "weekly": 2, "monthly": 3, "yearly": 4}
            frec = next((x for x in ("daily", "weekly", "monthly", "yearly") if x in efectivas), None)
            if not frec or orden[frec] > orden[k]:
                falta.append(f"{k} (efectivo: {frec or 'sin frecuencia'})")
        elif k == "rotate":
            try:
                ok = int(efectivas.get("rotate", "0")) >= int(v)
            except ValueError:
                ok = False
            if not ok:
                falta.append(f"rotate {v} (efectivo: {efectivas.get('rotate', 'no definido')})")
        elif k == "compress":
            if "compress" not in efectivas or "nocompress" in efectivas:
                falta.append("compress")
        elif k == "notifempty":
            if "notifempty" not in efectivas or "ifempty" in efectivas:
                falta.append("notifempty")
        elif k not in efectivas:
            falta.append(k)
    if pol.get("postrotate_recarga_rsyslog") and not re.search(r"rsyslog", scripts.get("postrotate", "")):
        falta.append("postrotate que recargue rsyslog")
    return falta


_RE_LOG_ESPECIAL = re.compile(r"^(lastlog(\..*)?|wtmp([.\-].*)?|btmp([.\-].*)?|README)$")


_ESPERADO_DIR_PATH = "existe y es un directorio; propietario root; sin escritura para grupo ni otros (0755 o más restrictivo)"


def _dir_path_defecto(d: str, v: Optional[Dict]) -> Tuple[bool, str]:
    """(conforme, texto) de una entrada del PATH de root según CIS 5.4.2.5, con stat -L del DESTINO (nunca el modo del enlace)."""
    if v is None:
        return False, "no existe en el servidor — el script de auditoría del CIS lo reporta como \"is not a directory\""
    enlace = f" (enlace -> {v['destino']})" if v.get("enlace") else ""
    mal = []
    if "directory" not in v["tipo"]:
        mal.append(f"no es un directorio ({v['tipo']})")
    if v["owner"] != "root":
        mal.append(f"propietario {v['owner']} (se requiere root)")
    try:
        if int(v["mode"], 8) & 0o022:
            mal.append(f"modo {v['mode']} menos restrictivo que 0755")
    except ValueError:
        mal.append(f"modo no interpretable {v['mode']}")
    return (not mal), (f"directorio{enlace} {v['mode']} {v['owner']}" + (" — " + "; ".join(mal) if mal else " — conforme"))


def ev_un_solo_logging(ctx: Ctx) -> Evaluacion:
    """CIS 6.2.1.4 (script oficial): rsyslog activo -> rsyslog en uso ("follow ... Configure rsyslog subsection only");
    si no, systemd-journald activo -> journald; ninguno -> FAIL. Con metodo_logging definido se verifica además que el
    sistema en uso sea el método corporativo (consistencia; una diferencia se muestra como falla)."""
    ev = Evaluacion()
    rs = unidades(ctx, "rsyslog.servicio").get("rsyslog")
    jd = unidades(ctx, "journald.servicio").get("systemd-journald.service")
    if ctx.problema("rsyslog.servicio") or ctx.problema("journald.servicio") or rs is None or jd is None:
        ev.add("script_cis", "Sistema de logging en uso", ERROR, "", ctx.problema("rsyslog.servicio") or ctx.problema("journald.servicio")
               or "[SALIDA_NO_INTERPRETABLE] sin estado de rsyslog/journald", ["rsyslog.servicio", "journald.servicio"]); return ev
    detectado = "rsyslog" if rs[1] == "active" else ("journald" if jd[1] == "active" else None)
    ev.add("script_cis", "Sistema de logging en uso (script del Audit CIS)", OK if detectado else FALLA, "rsyslog o journald activo",
           f"{detectado} en uso (rsyslog {rs[1]}, systemd-journald {jd[1]})" if detectado else "unable to determine system logging",
           ["rsyslog.servicio", "journald.servicio"])
    pol = politica_logging(ctx)
    if pol and detectado:
        ev.add("politica", "Sistema en uso = método corporativo de logging", OK if detectado == pol["metodo"] else FALLA, pol["metodo"],
               detectado, ["rsyslog.servicio", "journald.servicio"])
        ev.mecanismo["fuente_politica"] = pol["fuente"]
    return ev


# ===================================================================== evaluadores

def ev_repos(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    cid = "repo.yum_repos" if ctx.familia == RHEL else "repo.zypp_repos"
    prob = ctx.problema(cid)
    ids = sorted({re.sub(r".*\[(.+)\].*", r"\1", l) for l in ctx.lineas(cid) if re.search(r":\s*\[.+\]", l)})
    if prob:
        ev.add("repos", "Repositorios configurados", ERROR, "", prob, [cid]); return ev
    aprobados = ctx.listas.get("repositorios") or []
    if aprobados:
        no_aut = [i for i in ids if i not in aprobados]
        ev.add("repos", "Repositorios definidos están en la lista aprobada", FALLA if no_aut else OK,
               f"subconjunto de {aprobados}", f"no aprobados: {no_aut}" if no_aut else ", ".join(ids), [cid])
    else:
        ev.add("repos", "Repositorios conforme a política del sitio", ORG,
               "Lista oficial de repositorios autorizados (no suministrada)", "[EVIDENCIA_ORGANIZACIONAL] existentes: " + (", ".join(ids) or "(ninguno)"),
               [cid, "repo.dnf_repolist" if ctx.familia == RHEL else "repo.zypper_lr"])
    return ev


def _repos_secciones(lineas: List[str]) -> Dict[str, Dict[str, str]]:
    repos, actual = {}, None
    for l in lineas:
        archivo, _, resto = l.partition(":")
        resto = resto.strip()
        m = re.match(r"^\[(.+)\]$", resto)
        if m:
            actual = f"{archivo}:{m.group(1)}"
            repos[actual] = {}
        elif actual and "=" in resto:
            k, v = resto.split("=", 1)
            repos[actual][k.strip().lower()] = v.strip()
    return repos


def ev_gpg_keys(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    claves = [l.split("|")[0] for l in ctx.lineas("repo.gpg_keys") if l.startswith("gpg-pubkey")]
    if ctx.problema("repo.gpg_keys"):
        ev.add("claves", "Claves GPG instaladas", ERROR, "", ctx.problema("repo.gpg_keys"), ["repo.gpg_keys"])
        return ev
    if ctx.familia == RHEL:
        prob = ctx.problema("repo.yum_repos")
        repos = _repos_secciones(ctx.lineas("repo.yum_repos"))
        sin = [r for r, kv in repos.items() if kv.get("enabled", "1") in ("1", "true", "yes") and not kv.get("gpgkey")]
        ev.add("gpgkey_por_repo", "Cada repositorio habilitado declara gpgkey", ERROR if prob else (FALLA if sin else OK),
               "gpgkey=<URL> en cada repositorio habilitado", prob or (f"sin gpgkey: {sin}" if sin else f"{len(repos)} repositorios con gpgkey"),
               ["repo.yum_repos"])
    aprob = ctx.listas.get("claves_gpg") or []
    if aprob:
        no = [c for c in claves if not any(a in c for a in aprob)]
        ev.add("claves", "Claves GPG instaladas autorizadas", FALLA if no else OK, f"en {aprob}",
               f"no autorizadas: {no}" if no else ", ".join(claves), ["repo.gpg_keys"])
    else:
        ev.add("claves", "Claves GPG instaladas corresponden al proveedor", ORG,
               "Lista de claves GPG autorizadas (no suministrada)", "[EVIDENCIA_ORGANIZACIONAL] existentes: " + (", ".join(claves) or "(ninguna)"),
               ["repo.gpg_keys", "repo.pki_rpm_gpg"])
    return ev


_RE_GPG_MAL = re.compile(r"^\s*gpgcheck\s*=\s*(0|[2-9]|[1-9][0-9]+|false|no|off)\b", re.I)


def ev_gpgcheck(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    if ctx.familia == RHEL:
        prob = ctx.problema("repo.dnf_conf")
        glob = [l for l in ctx.lineas("repo.dnf_conf") if re.search(r":\s*gpgcheck\s*=\s*(1|true|yes)\b", l, re.I)]
        ev.add("global", "gpgcheck global en /etc/dnf/dnf.conf", ERROR if prob else (OK if glob else FALLA),
               "gpgcheck=1|true|yes", prob or ("; ".join(ctx.lineas("repo.dnf_conf")) or "no definido"), ["repo.dnf_conf"])
        cid = "repo.yum_repos"
    else:
        prob = ctx.problema("repo.zypp_conf")
        mal = [l for l in ctx.lineas("repo.zypp_conf") if _RE_GPG_MAL.search(l.split(":", 1)[-1])]
        ev.add("global", "gpgcheck no deshabilitado en /etc/zypp/zypp.conf", ERROR if prob else (FALLA if mal else OK),
               "sin gpgcheck=0|false|no|off", prob or ("; ".join(mal) or "sin desactivación"), ["repo.zypp_conf"])
        cid = "repo.zypp_repos"
    prob = ctx.problema(cid)
    mal = [l for l in ctx.lineas(cid) if _RE_GPG_MAL.search(l.split(":", 1)[-1])]
    ev.add("repos", "Ningún repositorio deshabilita gpgcheck", ERROR if prob else (FALLA if mal else OK),
           "ninguna línea gpgcheck=0/false/no/inválida", prob or ("; ".join(mal) or "ninguna"), [cid])
    return ev


def _edad_cache(ctx: Ctx, cid: str) -> Optional[int]:
    fechas = []
    for l in ctx.lineas(cid):
        try:
            fechas.append(datetime.strptime(l.split()[0], "%Y-%m-%d").date())
        except (ValueError, IndexError):
            pass
    if not fechas:
        return None
    ref = ctx.config.get("fecha_referencia") or date.today()
    return (ref - max(fechas)).days


def _sin_pendientes(ev: Evaluacion, ctx: Ctx, comp: str, desc: str, evidencia: List[str], cache_cid: str):
    """0 pendientes según caché local: solo es CUMPLE si Infraestructura definió una antigüedad máxima aceptable."""
    edad = _edad_cache(ctx, cache_cid)
    max_dias = ctx.config.get("actualizaciones_cache_max_dias")
    txt = f"0 pendientes según la caché local (antigüedad: {edad if edad is not None else 'desconocida'} días)"
    ev.mecanismo[f"antiguedad_cache_dias_{comp}"] = edad
    if max_dias is not None and edad is not None and edad <= int(max_dias):
        ev.add(comp, desc, OK, f"0 pendientes con caché <= {max_dias} días (umbral definido por Infraestructura)", txt, evidencia)
    else:
        ev.add(comp, desc, MANUAL, "0 pendientes confirmados con metadatos actualizados",
               "[POLITICA_O_REVISION] " + txt + "; sin refrescar repositorios no puede confirmarse que no existan actualizaciones",
               evidencia + [cache_cid])


def ev_actualizaciones(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    if ctx.familia == RHEL:
        prob = ctx.problema("upd.dnf_check")
        r = ctx.reg("upd.dnf_check")
        if prob:
            ev.add("pendientes", "Sin actualizaciones pendientes (dnf check-update)", ERROR, "rc=0", prob, ["upd.dnf_check"])
        elif r.rc == 1:
            ev.add("pendientes", "Sin actualizaciones pendientes (dnf check-update)", MANUAL, "rc=0",
                   f"[POLITICA_O_REVISION] dnf -C no pudo usar la caché local (no se refrescan repositorios): {(r.stderr or '')[:200]}",
                   ["upd.dnf_check"])
        elif r.rc == 100:
            pend = [l for l in ctx.lineas("upd.dnf_check") if not l.startswith(("Obsoleting", "Security"))]
            ev.add("pendientes", "Sin actualizaciones pendientes", FALLA, "0 paquetes por actualizar",
                   f"{len(pend)} paquetes con actualización disponible según la caché local "
                   f"(antigüedad {_edad_cache(ctx, 'upd.dnf_cache_edad')} días)", ["upd.dnf_check", "upd.dnf_cache_edad"])
        else:
            _sin_pendientes(ev, ctx, "pendientes", "Sin actualizaciones pendientes", ["upd.dnf_check"], "upd.dnf_cache_edad")
        prob = ctx.problema("upd.needs_restarting")
        r = ctx.reg("upd.needs_restarting")
        ev.add("reinicio", "No se requiere reinicio (dnf needs-restarting -r)",
               ERROR if prob else (OK if r.rc == 0 else FALLA), "rc=0",
               prob or ("; ".join(ctx.lineas("upd.needs_restarting"))[:300] or f"rc={r.rc}"), ["upd.needs_restarting"])
    else:
        for cid, desc in (("upd.zypper_updates", "actualizaciones"), ("upd.zypper_patches", "parches")):
            prob = ctx.problema(cid)
            r = ctx.reg(cid)
            filas = [l for l in ctx.lineas(cid) if l.count("|") >= 3 and not re.match(r"^\s*(S|Repository)\s*\|", l)
                     and not set(l.strip()) <= set("-+|")]
            if prob or (r and r.rc not in (0, 100, 101, 102, 103)):
                ev.add(desc, f"Sin {desc} pendientes", ERROR, "lista vacía", prob or f"[RC_INESPERADO] rc={r.rc} {(r.stderr or '')[:200]}", [cid])
            elif filas:
                ev.add(desc, f"Sin {desc} pendientes", FALLA, "lista vacía",
                       f"{len(filas)} {desc} pendientes según la caché local (antigüedad {_edad_cache(ctx, 'upd.zypp_cache_edad')} días)",
                       [cid, "upd.zypp_cache_edad"])
            else:
                _sin_pendientes(ev, ctx, desc, f"Sin {desc} pendientes", [cid], "upd.zypp_cache_edad")
    ev.observaciones.append("Consulta sin red (solo caché local; no se refrescan repositorios). Las pendientes encontradas son concluyentes; "
                            "la ausencia de pendientes no lo es mientras Infraestructura no defina una antigüedad de caché aceptable.")
    return ev


def ev_aide_inst(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    _pkg_comp(ev, ctx, "aide", True)
    return ev


def ev_aide_prog(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    cron = [l for l in ctx.lineas("aide.cron") if "aide" in l]
    u = unidades(ctx, "aide.timer")
    srv, tim = u.get("aidecheck.service", ("", "")), u.get("aidecheck.timer", ("", ""))
    timer_ok = srv[0] in ("enabled", "static", "indirect") and tim[0] == "enabled" and tim[1] == "active"
    t = [f"aidecheck.service={srv[0] or 'n/d'}", f"aidecheck.timer={tim[0] or 'n/d'}/{tim[1] or 'n/d'}"]
    if cron or timer_ok:
        ev.add("programacion", "Ejecución periódica de aide --check", OK, "cron con aide --check o aidecheck.timer habilitado y activo",
               "; ".join(cron) if cron else "aidecheck.service/timer habilitados, timer activo", ["aide.cron", "aide.timer"])
    elif ctx.problema("aide.timer"):
        ev.add("programacion", "Ejecución periódica de aide --check", ERROR, "cron o timer",
               ctx.problema("aide.timer") + " Sin cron de aide visible; el estado de aidecheck.timer no se pudo determinar.",
               ["aide.cron", "aide.timer"])
    elif ctx.sin_sudo("aide.cron") or ctx.problema("aide.cron"):
        ev.add("programacion", "Ejecución periódica de aide --check", ERROR, "cron o timer",
               "[PERMISOS_INSUFICIENTES] No se encontró timer y no se pudo leer /var/spool/cron sin sudo", ["aide.cron", "aide.timer"])
    else:
        ev.add("programacion", "Ejecución periódica de aide --check", FALLA, "cron o timer",
               f"sin cron de aide; timer: {' / '.join(t[:3]) or 'sin dato'}", ["aide.cron", "aide.timer"])
    ev.mecanismo["programacion_aide"] = cron or t[:3]
    return ev


def ev_grub_pass(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("grub.password")
    l = ctx.lineas("grub.password")
    if prob:
        ev.add("password", "Contraseña de GRUB definida", ERROR, "", prob, ["grub.password"]); return ev
    # Formato normalizado (recolector >= 1.1.0): "GRUB|archivo|clave=true|clave=false"; el hash nunca se emite.
    ind = {}
    for x in l:
        if x.startswith("GRUB|"):
            partes = x.split("|")
            for kv in partes[2:]:
                k, _, v = kv.partition("=")
                if v == "true":
                    ind.setdefault(k, []).append(f"{partes[1]}: {k}={v}")
    if any(x.startswith("GRUB|") for x in l):
        user_cfg = [e for e in ind.get("password_grub_configurada", [])
                    if any(e.split(":")[0] == f.split(":")[0] for f in ind.get("formato_pbkdf2_sha512", []))]
        superu = ind.get("superusuario_grub_configurado", [])
        pbkdf2 = ind.get("password_pbkdf2_configurada", [])
        sin_formato = [e for e in ind.get("password_grub_configurada", []) if e not in user_cfg]
        if sin_formato:
            ev.observaciones.append("GRUB2_PASSWORD definida sin formato grub.pbkdf2.sha512 en: " + "; ".join(sin_formato))
    else:  # evidencia de ejecuciones anteriores (prefijo del hash ya redactado en el host)
        user_cfg = [x for x in l if "GRUB2_PASSWORD=grub.pbkdf2" in x]
        superu = [x for x in l if re.search(r"set\s+superusers", x)]
        pbkdf2 = [x for x in l if "password_pbkdf2" in x]
        sin_formato = []
    if ctx.familia == RHEL:
        # El Audit del Benchmark (RHEL/OL 8 y 9) solo verifica GRUB2_PASSWORD en user.cfg. grub.cfg trae por defecto el bloque
        # de /etc/grub.d/01_users (set superusers="root" / password_pbkdf2 root ${GRUB2_PASSWORD}), que solo pide contraseña
        # si existe user.cfg: sus indicadores se informan aparte y nunca se presentan como contraseña encontrada.
        ok = bool(user_cfg)
        encontrado = ("; ".join(user_cfg) if ok else "GRUB2_PASSWORD sin formato grub.pbkdf2.sha512: " + "; ".join(sin_formato)
                      if sin_formato else "no existe ningún user.cfg con GRUB2_PASSWORD en /boot")
        ev.add("password", "Contraseña de GRUB definida (user.cfg)", OK if ok else FALLA, "GRUB2_PASSWORD=grub.pbkdf2.sha512 en user.cfg",
               encontrado, ["grub.password"])
        if superu or pbkdf2:
            ev.add("grub_cfg", "Indicadores en grub.cfg (informativo: no decide el resultado)", INFO,
                   "bloque estándar de /etc/grub.d/01_users", "; ".join(superu + pbkdf2) + ". Por defecto grub.cfg incluye "
                   "'set superusers' y 'password_pbkdf2 root ${GRUB2_PASSWORD}', que solo activan la contraseña si existe user.cfg: "
                   "no prueban que haya contraseña definida.", ["grub.password"])
        return ev
    ok = bool(superu) and bool(pbkdf2)
    ev.add("password", "Contraseña de GRUB definida", OK if ok else FALLA, "set superusers y password_pbkdf2 en /boot/grub2/grub.cfg",
           "; ".join(superu + pbkdf2) or "no encontrada", ["grub.password"])
    return ev


def ev_grub_acceso(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("grub.archivos")
    l = ctx.lineas("grub.archivos")
    if prob or not l:
        ev.add("archivos", "Permisos de archivos GRUB", ERROR, "", prob or "no se listaron archivos en /boot", ["grub.archivos"])
        return ev
    for linea in l:
        n, modo, u, g = linea.split("|")
        mascara = 0o077 if n.startswith("/boot/efi/EFI") else 0o177
        malos = []
        if not modo_ok(modo, mascara):
            malos.append(f"modo {modo} (máx {max_modo(mascara)})")
        if u != "root":
            malos.append(f"propietario {u}")
        if g != "root":
            malos.append(f"grupo {g}")
        ev.add(n, f"{n}: modo <= {max_modo(mascara)}, root:root", FALLA if malos else OK,
               f"{max_modo(mascara)} root root", f"{modo} {u} {g}", ["grub.archivos"])
    return ev


def _sysctl_eval(ctx: Ctx, ev: Evaluacion, param: str, valor: str):
    prob = ctx.problema("sysctl.runtime")
    rt = _kv([l.replace(" = ", "=") for l in ctx.lineas("sysctl.runtime")])
    actual = rt.get(param)
    ev.add("runtime", f"{param} en ejecución", ERROR if prob else (OK if actual == valor else FALLA),
           valor, prob or str(actual), ["sysctl.runtime"])
    prob = ctx.problema("sysctl.archivos")
    archivo, ocurr = None, []
    for l in ctx.lineas("sysctl.archivos"):
        m = re.match(r"^\s*#\s*(/\S+)", l)
        if m:
            archivo = m.group(1); continue
        m = re.match(r"^\s*-?" + re.escape(param).replace("\\.", "[./]") + r"\s*=\s*(\S+)", l)
        if m:
            ocurr.append((archivo, m.group(1)))
    if prob:
        ev.add("persistente", f"{param} persistente", ERROR, valor, prob, ["sysctl.archivos"]); return
    if not ocurr:
        ev.add("persistente", f"{param} persistente", FALLA, f"{param}={valor} en un archivo cargado por systemd-sysctl",
               "no definido en ningún archivo", ["sysctl.archivos"]); return
    if _v8(ctx):
        ok = ocurr[-1][1] == valor
        enc = f"valor efectivo {ocurr[-1][1]} en {ocurr[-1][0]}"
    else:
        ok = all(v == valor for _, v in ocurr)
        enc = "; ".join(f"{v} en {a}" for a, v in ocurr)
    ev.add("persistente", f"{param} persistente", OK if ok else FALLA, f"{param}={valor}", enc, ["sysctl.archivos"])
    if len(ocurr) > 1:
        ev.observaciones.append(f"{param} definido en {len(ocurr)} archivos: {ocurr}")


def ev_aslr(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    _sysctl_eval(ctx, ev, "kernel.randomize_va_space", "2")
    return ev


def _coredump(ctx: Ctx, opcion: str, valor: str) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("coredump.conf")
    eff = config_efectiva(ctx.lineas("coredump.conf"), "Coredump", aceptar_comentado=_v8(ctx))
    v = eff.get(opcion)
    if prob:
        ev.add(opcion, f"{opcion} en coredump.conf", ERROR, valor, prob, ["coredump.conf"]); return ev
    ok = v is not None and v[0].lower() == valor.lower()
    enc = "no definido" if v is None else f"{v[0]} en {v[1]}{' (valor por defecto comentado)' if v[2] else ''}"
    ev.add(opcion, f"{opcion} efectivo en systemd-coredump", OK if ok else FALLA, f"{opcion}={valor}", enc, ["coredump.conf"])
    return ev


def ev_core_backtraces(ctx):
    return _coredump(ctx, "ProcessSizeMax", "0")


def ev_core_storage(ctx):
    return _coredump(ctx, "Storage", "none")


def ev_core_restringido(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("coredump.limits")
    lim = [l for l in ctx.lineas("coredump.limits") if re.search(r"\*\s+hard\s+core\s+0\b", l)]
    ev.add("limits", "* hard core 0 en limits.conf/limits.d", ERROR if prob else (OK if lim else FALLA),
           "* hard core 0", prob or ("; ".join(ctx.lineas("coredump.limits")) or "no definido"), ["coredump.limits"])
    _sysctl_eval(ctx, ev, "fs.suid_dumpable", "0")
    svc = unidades(ctx, "coredump.servicio")
    if any(v[0] in ("enabled", "disabled", "static", "indirect") for v in svc.values()):
        ev.observaciones.append("systemd-coredump está instalado: el CIS indica configurar además Storage=none y ProcessSizeMax=0.")
    return ev


def ev_unconfined(ctx: Ctx) -> Evaluacion:
    """CIS 1.3.1.6 (Manual): `ps -eZ | grep unconfined_service_t` no debe devolver salida. Se identifica cada proceso
    (PID, comm, contexto), su ejecutable y su paquete RPM (priv.unconfined_detalle)."""
    ev = Evaluacion()
    se = (ctx.lineas("sys.selinux") or ["?"])[0].strip()
    prob = ctx.problema("selinux.unconfined")
    l = ctx.lineas("selinux.unconfined")
    if se.lower() == "disabled":
        ev.add("selinux", "Sin servicios unconfined_service_t", MANUAL, "SELinux habilitado y sin procesos unconfined_service_t",
               "SELinux Disabled: el criterio no es evaluable (ningún proceso tiene contexto)", ["sys.selinux", "selinux.unconfined"])
        return ev
    procs = []
    for x in l:
        p = x.split()
        if len(p) >= 5 and "unconfined_service_t" in p[0]:
            procs.append({"contexto": p[0], "pid": p[1], "comm": " ".join(p[4:])})
    detalle = {}
    dl, dprob = _evid(ctx, "priv.unconfined_detalle")
    for x in dl or []:
        q = x.split("|")
        if q[0] == "UP" and len(q) >= 6:
            detalle[q[1]] = {"exe": q[4], "rpm": q[5]}
    grupos: Dict[str, List[Dict]] = {}
    for pr in procs:
        grupos.setdefault(pr["comm"], []).append(pr)
    resumen = ", ".join(f"{c} ({len(v)})" for c, v in grupos.items())
    ev.add("procesos", "Sin procesos unconfined_service_t", ERROR if prob else (FALLA if procs else OK),
           "ninguna salida de ps -eZ | grep unconfined_service_t",
           prob or (f"{len(procs)} procesos ejecutándose bajo unconfined_service_t: {resumen}" if procs else "ninguno"),
           ["selinux.unconfined"])
    for comm, v in grupos.items():
        exes = sorted({detalle[p["pid"]]["exe"] for p in v if p["pid"] in detalle})
        rpms = sorted({detalle[p["pid"]]["rpm"] for p in v if p["pid"] in detalle})
        ev.add(f"proceso_{comm}", f"Proceso unconfined: {comm}", INFO, "dominio SELinux confinado",
               f"PID {', '.join(p['pid'] for p in v)} · contexto {', '.join(sorted({p['contexto'] for p in v}))} · "
               f"ejecutable {', '.join(exes) or 'no disponible'} · paquete RPM {', '.join(rpms) or 'no disponible'}",
               ["selinux.unconfined"] + (["priv.unconfined_detalle"] if detalle else []))
    if procs and not prob:
        ev.mecanismo["motivo_visible"] = f"{len(procs)} procesos ejecutándose bajo unconfined_service_t: {resumen}."
        if dl is None:
            ev.observaciones.append(f"Ejecutable y paquete RPM de cada proceso no disponibles: {dprob}")
    return ev


def ev_setroubleshoot(ctx):
    ev = Evaluacion(); _pkg_comp(ev, ctx, "setroubleshoot", False); return ev


def ev_aa_inst(ctx):
    ev = Evaluacion()
    for p in ("apparmor-parser", "apparmor-profiles", "apparmor-utils", "libapparmor1"):
        _pkg_comp(ev, ctx, p, True)
    return ev


def ev_aa_boot(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("grub.cfg_linux")
    l = ctx.lineas("grub.cfg_linux")
    if prob or not l:
        ev.add("grub", "apparmor=1 y security=apparmor en todas las líneas linux", ERROR, "", prob or "sin líneas linux", ["grub.cfg_linux"])
        return ev
    for param in ("apparmor=1", "security=apparmor"):
        faltan = [x for x in l if param not in x.split()]
        ev.add(param, f"{param} en todas las líneas linux de grub.cfg", FALLA if faltan else OK, param,
               f"{len(faltan)} de {len(l)} líneas sin {param}" if faltan else f"presente en {len(l)} líneas", ["grub.cfg_linux"])
    return ev


def _aa_status(ctx: Ctx) -> Dict[str, int]:
    res = {}
    for l in ctx.lineas("apparmor.status"):
        m = re.match(r"^\s*(\d+)\s+(.*)$", l.strip())
        if m:
            res[re.sub(r"\s+", " ", m.group(2)).rstrip(".")] = int(m.group(1))
    return res


def ev_aa_no_deshab(ctx: Ctx, enforce=False) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("apparmor.status")
    if prob:
        ev.add("estado", "Estado de AppArmor", ERROR, "", prob, ["apparmor.status"]); return ev
    s = _aa_status(ctx)
    cargados = s.get("profiles are loaded", 0)
    enf, comp = s.get("profiles are in enforce mode", 0), s.get("profiles are in complain mode", 0)
    unconf = s.get("processes are unconfined but have a profile defined", 0)
    ev.add("cargados", "Perfiles cargados", OK if cargados > 0 else FALLA, "> 0", str(cargados), ["apparmor.status"])
    if enforce:
        ev.add("complain", "Ningún perfil en modo complain", OK if comp == 0 and enf == cargados else FALLA,
               "complain = 0 y enforce = cargados", f"enforce={enf} complain={comp} cargados={cargados}", ["apparmor.status"])
    else:
        ev.add("modos", "Perfiles en enforce o complain", OK if enf + comp >= cargados and cargados > 0 else FALLA,
               "enforce + complain = cargados", f"enforce={enf} complain={comp} cargados={cargados}", ["apparmor.status"])
    ev.add("unconfined", "Procesos sin confinar con perfil definido", OK if unconf == 0 else FALLA, "0", str(unconf), ["apparmor.status"])
    return ev


def ev_gdm_removido(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    _pkg_comp(ev, ctx, "gdm", False)
    if _v8(ctx):
        r = ctx.reg("gdm.grupos_dnf")
        prob = ctx.problema("gdm.grupos_dnf")
        txt = r.stdout if r else ""
        if prob or not txt.strip() or (r and r.rc != 0):
            ev.add("grupo_entorno", "Grupo de entorno 'Server with GUI' no instalado", MANUAL, "no instalado",
                   "No se pudo consultar dnf grouplist con caché local; se usa la presencia de gdm como evidencia principal",
                   ["gdm.grupos_dnf"])
        else:
            gui = "Server with GUI" in txt
            ev.add("grupo_entorno", "Grupo de entorno 'Server with GUI' no instalado", FALLA if gui else OK,
                   "no instalado", "instalado" if gui else "no instalado", ["gdm.grupos_dnf"])
    return ev


def _gdm_na(ev: Evaluacion, ctx: Ctx) -> bool:
    inst = _gdm_instalado(ctx)
    if inst is None:
        ev.add("gdm", "Presencia de GDM", ERROR, "", ctx.problema("pkg.rpm") or "sin dato", ["pkg.rpm"]); return True
    if not inst:
        ev.add("gdm", "GDM instalado (condición de aplicabilidad)", NA, "Si GDM no está instalado el control no aplica",
               "gdm/gdm3 no instalados", ["pkg.rpm"])
        return True
    return False


def ev_gdm_userlist(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    if _gdm_na(ev, ctx):
        return ev
    if ctx.problema("gdm.dconf"):
        ev.add("dconf", "Configuración dconf de GDM", ERROR, "", ctx.problema("gdm.dconf"), ["gdm.dconf"]); return ev
    l = ctx.lineas("gdm.dconf")
    act = [x for x in l if re.search(r"disable-user-list\s*=\s*true", x, re.I)]
    ev.add("disable_user_list", "disable-user-list=true en la base dconf de GDM", OK if act else FALLA, "true",
           "; ".join(act) or "no definido", ["gdm.dconf"])
    perfiles = [x for x in l if "system-db:" in x]
    ev.add("perfil", "Perfil dconf con system-db", OK if perfiles else FALLA, "system-db:<perfil>", "; ".join(perfiles) or "ninguno", ["gdm.dconf"])
    if _v8(ctx):
        ev.observaciones.append("RHEL/OL 8 v4.0.0 audita con gsettings (sesión gráfica); se evalúa la configuración dconf del sistema equivalente.")
    return ev


def ev_gdm_banner(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    if _gdm_na(ev, ctx):
        return ev
    if ctx.problema("gdm.dconf"):
        ev.add("dconf", "Configuración dconf de GDM", ERROR, "", ctx.problema("gdm.dconf"), ["gdm.dconf"]); return ev
    l = ctx.lineas("gdm.dconf")
    en = [x for x in l if re.search(r"banner-message-enable\s*=\s*true", x, re.I)]
    tx = [x for x in l if re.search(r"banner-message-text\s*=", x, re.I)]
    ev.add("enable", "banner-message-enable=true", OK if en else FALLA, "true", "; ".join(en) or "no", ["gdm.dconf"])
    ev.add("texto", "banner-message-text definido", OK if tx else FALLA, "texto definido", "; ".join(tx)[:200] or "no", ["gdm.dconf"])
    return ev


def ev_crypto(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("crypto.politica")
    l = ctx.lineas("crypto.politica")
    legacy = [x for x in l if re.match(r"^\s*LEGACY\b", x, re.I)]
    ev.add("politica", "Política criptográfica distinta de LEGACY", FALLA if legacy else (ERROR if prob else OK),
           "sin línea LEGACY en /etc/crypto-policies/config", " / ".join(l) or prob or "sin dato", ["crypto.politica"])
    return ev


def ev_auditd_pkgs(ctx):
    ev = Evaluacion()
    _pkg_comp(ev, ctx, "audit", True)
    _pkg_comp(ev, ctx, "audit-libs", True)
    return ev


def ev_auditd_svc(ctx):
    ev = Evaluacion(); _estado_svc(ev, ctx, "auditd.servicio", "auditd"); return ev


def _cmdline_param(ctx: Ctx, ev: Evaluacion, regex: str, desc: str, validar=None):
    """Parámetro de arranque en entradas de GRUB (grubby, RHEL) y en /etc/default/grub."""
    if ctx.familia == RHEL:
        prob = ctx.problema("grub.grubby")
        args = [l for l in ctx.lineas("grub.grubby") if l.startswith("args=")]
        if prob or not args:
            ev.add("entradas", f"{desc} en todas las entradas de arranque", ERROR, desc, prob or "sin entradas", ["grub.grubby"])
        else:
            vals = [re.search(regex, a) for a in args]
            ok = all(v and (validar is None or validar(v.group(1) if v.groups() else v.group(0))) for v in vals)
            ev.add("entradas", f"{desc} en todas las entradas de arranque (grubby)", OK if ok else FALLA, desc,
                   f"{sum(1 for v in vals if v)} de {len(args)} entradas: " + ", ".join(sorted({v.group(0) for v in vals if v})),
                   ["grub.grubby"])
    prob = ctx.problema("grub.default")
    l = [x for x in ctx.lineas("grub.default") if x.strip().startswith("GRUB_CMDLINE_LINUX=")]
    v = re.search(regex, " ".join(l)) if l else None
    ok = bool(v) and (validar is None or validar(v.group(1) if v.groups() else v.group(0)))
    ev.add("default_grub", f"{desc} en GRUB_CMDLINE_LINUX (/etc/default/grub)", ERROR if prob else (OK if ok else FALLA), desc,
           prob or ("; ".join(l) or "GRUB_CMDLINE_LINUX no definido"), ["grub.default"])


def ev_audit_boot(ctx: Ctx) -> Evaluacion:
    """CIS 6.3.1.2: audit=1 en todas las entradas de arranque (grubby --info=ALL) y en GRUB_CMDLINE_LINUX de
    /etc/default/grub. El valor en ejecución (/proc/cmdline) no forma parte del Audit: se informa (INFO).
    audit_backlog_limit es un requisito separado (6.3.1.3) y no interviene aquí."""
    ev = Evaluacion()
    _cmdline_param(ctx, ev, r"\baudit=1\b", "audit=1")
    ents = _grubby_entradas(ctx)
    for c in ev.componentes:
        if c.id == "entradas" and ents and c.estado in (OK, FALLA):
            sin = [k for k, a in ents if not re.search(r"\baudit=1\b", a)]
            c.encontrado = f"{len(ents) - len(sin)} de {len(ents)} entradas conformes" + (f"; sin audit=1: {', '.join(sin)}" if sin else "")
    cmd, cprob = _evid(ctx, "sys.cmdline")
    if cmd is None:
        ev.add("runtime", "audit=1 en el kernel en ejecución (/proc/cmdline; informativo, fuera del Audit CIS)", INFO, "audit=1",
               f"no disponible: {cprob}", [])
    else:
        toks = [x.strip() for x in cmd if not x.startswith("#")]
        ok = "audit=1" in toks
        ev.add("runtime", "audit=1 en el kernel en ejecución (/proc/cmdline; informativo, fuera del Audit CIS)", INFO, "audit=1",
               ("OK: audit=1" if ok else "FALLA: audit=1 ausente") + f" (tokens: {', '.join(toks) or 'ninguno'})", ["sys.cmdline"])
    if any(c.estado == FALLA for c in ev.componentes):
        partes = []
        for c in ev.componentes:
            if c.id == "default_grub":
                partes.append(f"persistente en /etc/default/grub: {'OK' if c.estado == OK else 'FALLA'}")
            elif c.id == "entradas":
                partes.append(f"entradas de arranque (grubby): {c.encontrado}")
        ev.mecanismo["motivo_visible"] = ("audit=1 no está configurado: " + "; ".join(partes)
                                          + ". audit_backlog_limit se evalúa por separado (6.3.1.3).")
    return ev


def ev_audit_backlog(ctx: Ctx) -> Evaluacion:
    """Valor esperado tomado del Audit del Benchmark suministrado (ctx.config['backlog_cis'])."""
    ev = Evaluacion()
    ref = ctx.config.get("backlog_cis") or {}
    minimo = ref.get("minimo")
    if minimo:
        desc = f"audit_backlog_limit>={minimo}"
        _cmdline_param(ctx, ev, r"\baudit_backlog_limit=(\d+)\b", desc, validar=lambda x: int(x) >= minimo)
    else:
        _cmdline_param(ctx, ev, r"\baudit_backlog_limit=(\d+)\b", "audit_backlog_limit=<valor> definido")
        for c in ev.componentes:
            m = re.search(r"audit_backlog_limit=(\d+)", c.encontrado)
            if c.estado == OK and m and int(m.group(1)) <= 64:
                c.estado = MANUAL
                c.encontrado = "[POLITICA_O_REVISION] " + c.encontrado + " (no supera el valor por defecto 64 que el Benchmark considera posiblemente insuficiente)"
    ev.mecanismo["valor_esperado_cis"] = ref.get("texto")
    ev.observaciones.append(f"Valor esperado según el Benchmark: {ref.get('texto', 'no determinado')}")
    return ev


def _auditd_param(ctx: Ctx, ev: Evaluacion, clave: str, validos: Tuple[str, ...], desc: str):
    prob = ctx.problema("auditd.conf")
    v = _auditd_conf(ctx).get(clave)
    ok = v is not None and v.lower() in validos
    ev.add(clave, desc, ERROR if prob else (OK if ok else FALLA), f"{clave} = {'|'.join(validos)}",
           prob or (f"{clave} = {v}" if v is not None else "no definido"), ["auditd.conf"])


def ev_audit_max_log(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    politica = (ctx.exclusion.get("parametros_exclusion") or {}).get("max_log_file")
    prob = ctx.problema("auditd.conf")
    v = _auditd_conf(ctx).get("max_log_file")
    if prob:
        ev.add("max_log_file", "max_log_file", ERROR, "", prob, ["auditd.conf"]); return ev
    if politica:
        ev.add("max_log_file", "max_log_file conforme a la política del sitio", OK if v == politica else FALLA,
               f"max_log_file = {politica} (política del sitio definida por Infraestructura)", f"max_log_file = {v}", ["auditd.conf"])
    else:
        ev.add("max_log_file", "max_log_file conforme a la política del sitio", ORG if v else FALLA,
               "valor de la política del sitio", f"max_log_file = {v}" if v else "no definido", ["auditd.conf"])
    return ev


def ev_audit_keep_logs(ctx):
    ev = Evaluacion(); _auditd_param(ctx, ev, "max_log_file_action", ("keep_logs",), "max_log_file_action"); return ev


def ev_audit_disk_full(ctx):
    ev = Evaluacion()
    _auditd_param(ctx, ev, "disk_full_action", ("halt", "single"), "disk_full_action")
    _auditd_param(ctx, ev, "disk_error_action", ("syslog", "single", "halt"), "disk_error_action")
    return ev


# --------------------------------------------------------------- reglas auditd

REGLAS = {
    "scope": ["-w /etc/sudoers -p wa -k scope", "-w /etc/sudoers.d -p wa -k scope"],
    "user_emulation": [f"-a always,exit -F arch={a} -C euid!=uid -F auid!=unset -S execve -k user_emulation" for a in ("b64", "b32")],
    "time": [f"-a always,exit -F arch={a} -S adjtimex,settimeofday -k time-change" for a in ("b64", "b32")]
            + [f"-a always,exit -F arch={a} -S clock_settime -F a0=0x0 -k time-change" for a in ("b64", "b32")]
            + ["-w /etc/localtime -p wa -k time-change"],
    "network": [f"-a always,exit -F arch={a} -S sethostname,setdomainname -k system-locale" for a in ("b64", "b32")]
               + [f"-w {p} -p wa -k system-locale" for p in ("/etc/issue", "/etc/issue.net", "/etc/hosts", "/etc/hostname",
                                                             "/etc/sysconfig/network", "/etc/sysconfig/network-scripts", "/etc/NetworkManager")],
    "access": [f"-a always,exit -F arch={a} -S creat,open,openat,truncate,ftruncate -F exit={e} -F auid>={{UID_MIN}} -F auid!=unset -k access"
               for a in ("b64", "b32") for e in ("-EACCES", "-EPERM")],
    "identity": [f"-w {p} -p wa -k identity" for p in ("/etc/group", "/etc/passwd", "/etc/gshadow", "/etc/shadow",
                                                        "/etc/security/opasswd", "/etc/nsswitch.conf", "/etc/pam.conf", "/etc/pam.d")],
    "perm_mod": [f"-a always,exit -F arch={a} -S {s} -F auid>={{UID_MIN}} -F auid!=unset -k perm_mod"
                 for a in ("b64", "b32") for s in ("chmod,fchmod,fchmodat", "chown,fchown,lchown,fchownat",
                                                   "setxattr,lsetxattr,fsetxattr,removexattr,lremovexattr,fremovexattr")],
    "mounts": [f"-a always,exit -F arch={a} -S mount -F auid>={{UID_MIN}} -F auid!=unset -k mounts" for a in ("b64", "b32")],
    "session": [f"-w {p} -p wa -k session" for p in ("/var/run/utmp", "/var/log/wtmp", "/var/log/btmp")],
    "logins": ["-w /var/log/lastlog -p wa -k logins", "-w /var/run/faillock -p wa -k logins"],
    "delete": [f"-a always,exit -F arch={a} -S unlink,unlinkat,rename,renameat -F auid>={{UID_MIN}} -F auid!=unset -k delete" for a in ("b64", "b32")],
    "mac_rhel": ["-w /etc/selinux -p wa -k MAC-policy", "-w /usr/share/selinux -p wa -k MAC-policy"],
    "mac_sles": ["-w /etc/apparmor -p wa -k MAC-policy", "-w /usr/share/apparmor -p wa -k MAC-policy"],
    "chcon": ["-a always,exit -F path=/usr/bin/chcon -F perm=x -F auid>={UID_MIN} -F auid!=unset -k perm_chng"],
    "setfacl": ["-a always,exit -F path=/usr/bin/setfacl -F perm=x -F auid>={UID_MIN} -F auid!=unset -k perm_chng"],
    "chacl": ["-a always,exit -F path=/usr/bin/chacl -F perm=x -F auid>={UID_MIN} -F auid!=unset -k perm_chng"],
    "usermod": ["-a always,exit -F path=/usr/sbin/usermod -F perm=x -F auid>={UID_MIN} -F auid!=unset -k usermod"],
    "kernel_modules": ["-a always,exit -F arch=b64 -S init_module,finit_module,delete_module,create_module,query_module -F auid>={UID_MIN} -F auid!=unset -k kernel_modules",
                       "-a always,exit -F path=/usr/bin/kmod -F perm=x -F auid>={UID_MIN} -F auid!=unset -k kernel_modules"],
}


def _reglas_host(ctx: Ctx):
    disco = ar.parsear_todas(ctx.lineas("auditd.reglas_disco"))
    cargadas = ar.parsear_todas(ctx.lineas("auditd.reglas_cargadas"))
    return disco, cargadas


def _eval_reglas(ctx: Ctx, ev: Evaluacion, textos: List[str]):
    """Reglas esperadas en /etc/audit/rules.d (persistentes) Y cargadas (auditctl -l). Los dos componentes agregados
    deciden el resultado (falta solo en uno: CUMPLE PARCIALMENTE); cada regla se muestra además como INFO con su
    estado PERSISTENTE / CARGADA."""
    disco, cargadas = _reglas_host(ctx)
    esperadas = ar.esperadas(textos, ctx.uid_min)
    estado_por_regla = {i: {} for i in range(len(esperadas))}
    for comp, cid, reglas in (("en_disco", "auditd.reglas_disco", disco), ("cargadas", "auditd.reglas_cargadas", cargadas)):
        prob = ctx.problema(cid)
        if prob:
            ev.add(comp, f"Reglas {comp.replace('_', ' ')}", ERROR, "", prob, [cid]); continue
        faltan = []
        for i, e in enumerate(esperadas):
            ok, det = ar.cumple(e, reglas)
            estado_por_regla[i][comp] = ok
            if not ok:
                faltan.append(det)
        ev.add(comp, f"Reglas {'en /etc/audit/rules.d' if comp == 'en_disco' else 'cargadas (auditctl -l)'}",
               FALLA if faltan else OK, " | ".join(t.replace("{UID_MIN}", str(ctx.uid_min)) for t in textos),
               ("faltan: " + " | ".join(faltan)) if faltan else f"{len(esperadas)} reglas esperadas presentes", [cid])
    faltantes = []
    for i, t in enumerate(textos):
        st = estado_por_regla.get(i, {})
        if not st:
            continue
        txt = lambda k: "sin dato" if k not in st else ("OK" if st[k] else "FALLA")  # noqa: E731
        etq = _etiqueta_regla(t)
        ev.add(f"regla_{i + 1}", f"Regla: {etq}", INFO, t.replace("{UID_MIN}", str(ctx.uid_min)),
               f"PERSISTENTE: {txt('en_disco')} · CARGADA: {txt('cargadas')}", ["auditd.reglas_disco", "auditd.reglas_cargadas"])
        if st.get("en_disco") is False or st.get("cargadas") is False:
            faltantes.append(f"{etq} (persistente: {txt('en_disco')}, cargada: {txt('cargadas')})")
    if faltantes:
        ev.mecanismo["motivo_visible"] = "Faltan reglas de auditoría: " + "; ".join(faltantes) + "."


def _ev_reglas(clave):
    def f(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        k = clave
        if clave == "mac":
            k = "mac_sles" if ctx.familia == SLES else "mac_rhel"
        _eval_reglas(ctx, ev, REGLAS[k])
        if clave == "kernel_modules":
            _ev_kmod(ctx, ev)
        return ev
    return f


def _num_uid(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


HERRAMIENTAS_KMOD = ("lsmod", "rmmod", "insmod", "modinfo", "modprobe", "depmod")


def kmod_herramientas(lineas: List[str]):
    """({herramienta: [(ruta, destino)]}, {binario kmod: destino}) desde auditd.kmod_symlinks.

    Formato vigente: 'T|herramienta|ruta|destino' y 'K|ruta_kmod|destino'. Formato anterior (evidencia previa al
    2026-10-27): 'ruta|destino' por herramienta (solo /usr/sbin) y 'kmod|destino de /bin/kmod'."""
    tools, kmods = {}, {}
    for l in lineas:
        p = l.split("|")
        if p[0] == "T" and len(p) == 4:
            tools.setdefault(p[1], []).append((p[2], p[3]))
        elif p[0] == "K" and len(p) == 3:
            kmods[p[1]] = p[2]
        elif len(p) == 2 and p[0] != "kmod":
            tools.setdefault(p[0].rsplit("/", 1)[-1], []).append((p[0], p[1]))
    return tools, kmods


def _ev_kmod(ctx: Ctx, ev: Evaluacion) -> None:
    """CIS 6.3.3.19: cada herramienta de módulos debe ser un enlace a kmod, y el kmod efectivo debe ser el binario que vigila
    la regla '-F path=<kmod> -F perm=x'. Se resuelve toda la cadena de enlaces (readlink -f) de cada ruta real encontrada."""
    cid = "auditd.kmod_symlinks"
    prob = ctx.problema(cid)
    if prob:
        ev.add("symlinks", "Herramientas de módulos enlazan a kmod", ERROR, "", prob, [cid]); return
    vigilado = next((m.group(1) for r in REGLAS["kernel_modules"] for m in [re.search(r"-F path=(\S+)", r)] if m), "/usr/bin/kmod")
    tools, kmods = kmod_herramientas(ctx.lineas(cid))
    if not tools:
        ev.add("symlinks", "Herramientas de módulos enlazan a kmod", ERROR, "", "[SALIDA_NO_INTERPRETABLE] sin rutas de herramientas", [cid])
        return
    # Destino real del binario vigilado (si el recolector lo reporta); las herramientas deben resolver a ese mismo destino.
    real = kmods.get(vigilado, vigilado)
    destinos_ok = {vigilado, real}
    filas, malos = [], []
    for t in HERRAMIENTAS_KMOD:
        rutas = tools.get(t) or []
        if not rutas:
            malos.append(f"{t}: no encontrada"); filas.append(f"{t}: no encontrada en /sbin, /usr/sbin, /bin ni /usr/bin")
            continue
        mal = [f"{r} -> {d}" for r, d in rutas if d not in destinos_ok]
        filas.append(f"{t}: " + ", ".join(f"{r} -> {d}" for r, d in rutas) + (" ✔" if not mal else " ✘"))
        ev.add(f"kmod_{t}", f"{t} -> kmod", INFO, f"-> {real}", ", ".join(f"{r} -> {d}" for r, d in rutas), [cid])
        if mal:
            malos.append(f"{t}: {', '.join(mal)}")
    est = FALLA if malos else OK
    ev.add("symlinks", "Herramientas de módulos enlazan al kmod vigilado por auditd", est,
           f"lsmod/rmmod/insmod/modinfo/modprobe/depmod -> {real} (ruta de la regla: {vigilado})",
           ("no enlazan al kmod vigilado: " + "; ".join(malos) + " | ") * bool(malos) + "; ".join(filas), [cid])
    if kmods and vigilado not in kmods:
        ev.observaciones.append(f"El binario vigilado por la regla ({vigilado}) no figura entre los kmod existentes "
                                f"({', '.join(f'{k} -> {v}' for k, v in kmods.items())}).")


def ev_sudo_log(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("auditd.sudo_logfile")
    l = ctx.lineas("auditd.sudo_logfile")
    m = re.search(r'logfile\s*=\s*"?([^",\s]+)', " ".join(l))
    if prob:
        ev.add("logfile", "Archivo de log de sudo definido", ERROR, "", prob, ["auditd.sudo_logfile"]); return ev
    if not m:
        ev.add("logfile", "Archivo de log de sudo definido (requisito previo)", FALLA, "Defaults logfile=<ruta>",
               "sudo no tiene logfile definido: la regla de auditoría no puede existir", ["auditd.sudo_logfile"])
        return ev
    _eval_reglas(ctx, ev, [f"-w {m.group(1)} -p wa -k sudo_log_file"])
    return ev


def ev_privilegiados(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    if ctx.omitido("fs.suid_sgid"):
        ev.add("binarios", "Regla de auditoría por cada binario SUID/SGID", MANUAL,
               "cada ejecutable SUID/SGID con regla en disco y cargada",
               "[PENDIENTE_AUDITORIA_PROFUNDA] requiere recorrer el filesystem (auditoria_filesystem_profunda=false)", ["fs.suid_sgid"])
        return ev
    prob = ctx.problema("fs.suid_sgid")
    if prob:
        ev.add("binarios", "Listado SUID/SGID", ERROR, "", prob, ["fs.suid_sgid"]); return ev
    nodev = set(ctx.lineas("sys.fs_nodev"))
    archivos, fs_actual = [], None
    fuente = "fsd.privilegiados_6336" if ctx.reg("fsd.privilegiados_6336") is not None else "fs.suid_sgid"
    for l in ctx.lineas(fuente):
        if l.startswith("#MOUNT|"):
            fs_actual = l.split("|")[1]; continue
        if fs_actual not in nodev:
            archivos.append(l.split("|", 3)[3])
    disco = "\n".join(ctx.lineas("auditd.reglas_disco"))
    carg = "\n".join(ctx.lineas("auditd.reglas_cargadas"))
    for comp, cid, txt in (("en_disco", "auditd.reglas_disco", disco), ("cargadas", "auditd.reglas_cargadas", carg)):
        p2 = ctx.problema(cid)
        faltan = [a for a in archivos if a not in txt]
        ev.add(comp, f"Binarios privilegiados auditados ({comp})", ERROR if p2 else (FALLA if faltan else OK),
               "todos los SUID/SGID presentes en las reglas", p2 or (f"{len(faltan)} de {len(archivos)} sin regla: {faltan[:20]}" if faltan else f"{len(archivos)} cubiertos"),
               [fuente, cid])
    aplicar_completitud(ev, fs_completitud(ctx, "sg6"), fuente, "Inventario SUID/SGID (6.3.3.6)")
    if ctx.sin_sudo("fs.suid_sgid"):
        ev.add("cobertura", "Recorrido con privilegios", ERROR, "sudo -n", "[PERMISOS_INSUFICIENTES] recorrido sin sudo: listado posiblemente incompleto", ["fs.suid_sgid"])
    return ev


def ev_audit_inmutable(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("auditd.reglas_disco")
    e = [ar.parsear(l).texto for l in ctx.lineas("auditd.reglas_disco") if ar.parsear(l) and ar.parsear(l).texto.startswith("-e")]
    dos = [x for x in e if re.match(r"^-e\s+2\b", x)]
    ev.add("e2", "-e 2 en /etc/audit/rules.d", ERROR if prob else (OK if dos else FALLA), "-e 2",
           prob or ("; ".join(e) or "sin directiva -e"), ["auditd.reglas_disco"])
    if e and not re.match(r"^-e\s+2\b", e[-1]):
        ev.observaciones.append(f"La última directiva -e es '{e[-1]}', posterior a '-e 2'.")
    st = " ".join(ctx.lineas("auditd.estado"))
    m = re.search(r"enabled\s+(\d)", st)
    if m:
        ev.observaciones.append(f"auditctl -s: enabled {m.group(1)} ({'inmutable en ejecución' if m.group(1) == '2' else 'no inmutable en ejecución'}).")
    return ev


def ev_audit_consistente(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    p1, p2 = ctx.problema("auditd.reglas_disco"), ctx.problema("auditd.audit_rules")
    if p1 or p2:
        ev.add("fusion", "rules.d fusionado en audit.rules", ERROR, "", p1 or p2, ["auditd.reglas_disco", "auditd.audit_rules"]); return ev
    a = sorted(r.texto for r in ar.parsear_todas(ctx.lineas("auditd.reglas_disco")))
    b = sorted(r.texto for r in ar.parsear_todas(ctx.lineas("auditd.audit_rules")))
    falta = sorted(set(a) - set(b)); sobra = sorted(set(b) - set(a))
    ev.add("fusion", "Todas las reglas de rules.d están fusionadas en audit.rules (equivalente a 'augenrules --check: No change')",
           OK if not falta and not sobra else FALLA, "sin diferencias",
           "sin diferencias" if not falta and not sobra else f"solo en rules.d: {falta[:10]}; solo en audit.rules: {sobra[:10]}",
           ["auditd.reglas_disco", "auditd.audit_rules"])
    ev.observaciones.append("No se ejecuta 'augenrules --check' (crea archivos temporales); se compara el contenido normalizado.")
    return ev


# --------------------------------------------------------------- journald / rsyslog

def _journald(ctx: Ctx):
    return config_efectiva(ctx.lineas("journald.conf"), "Journal", aceptar_comentado=_v8(ctx))


def _journald_param(ctx: Ctx, opcion: str, valor: str) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("journald.conf")
    v = _journald(ctx).get(opcion)
    if prob:
        ev.add(opcion, opcion, ERROR, "", prob, ["journald.conf"]); return ev
    ok = v is not None and v[0].lower() == valor
    leidos = archivos_catconfig(ctx.lineas("journald.conf"))
    ev.add(opcion, f"{opcion} efectivo en journald", OK if ok else FALLA, f"{opcion}={valor}",
           (f"no definido en ninguno de los {len(leidos)} archivos leídos" if v is None else
            f"{opcion}={v[0]} en {v[1]}{' (por defecto, comentado)' if v[2] else ''}"), ["journald.conf"])
    ev.add("archivos_journald", "Archivos de configuración de journald leídos (orden de precedencia; el último gana)", INFO,
           "configuración efectiva (systemd-analyze cat-config systemd/journald.conf)", lista_archivos(leidos), ["journald.conf"])
    ev.mecanismo = mecanismo_logging(ctx)
    return ev


def mecanismo_logging(ctx: Ctx) -> Dict:
    rs_u = unidades(ctx, "rsyslog.servicio").get("rsyslog")
    ju = _unidades(ctx).get("systemd-journal-upload.service")
    fwd = _journald(ctx).get("ForwardToSyslog")
    remoto_rs = [l for l in ctx.lineas("rsyslog.conf") if re.search(r"(@@?[\w.\-\[\]:]+)|action\(.*target=", l)]
    return {  # None = estado no determinable (systemd no consultable): nunca se asume inactivo
        "rsyslog_activo": None if rs_u is None else rs_u[1] == "active",
        "journal_upload_activo": None if ju is None else ju[1] == "active",
        "journald_ForwardToSyslog": fwd[0] if fwd else None,
        "rsyslog_destinos_remotos": len(remoto_rs),
    }


# Método corporativo de logging (config/listas_aprobadas.yml -> metodo_logging). Aplicabilidad tomada de las notas de cada
# Benchmark suministrado ("Note: This recommendation only applies if journald|rsyslog is the chosen method for client side
# logging. Do not apply this recommendation if rsyslog|journald is used."; la sección "Configure rsyslog" lleva además la
# nota de sección "This section only applies if rsyslog is the chosen method for client side logging"). Verificado por
# recomendación en los PDF: OL9/RHEL9 v2.0.0 y SLES 15 v2.0.1 -> 7 recomendaciones del camino journald; OL8/RHEL8 v4.0.0
# -> solo "ForwardToSyslog is disabled" y "systemd-journal-remote service is not in use" llevan la nota (el resto aplica).
_JOURNALD_V2 = frozenset({"journald_no_reenvia", "journald_compress", "journald_storage", "journal_remote_instalado",
                          "journal_upload_auth", "journal_upload_activo", "journal_remote_no_usado"})
_JOURNALD_V4 = frozenset({"journald_no_reenvia", "journal_remote_no_usado"})
# Oracle Linux 8 v4.0.0 (ajuste 2026-10-27): 6.2.1.2.1-6.2.1.2.3 (systemd-journal-remote/upload) pertenecen a "6.2.1 Configure
# journald"; la sección 6.2 establece que rsyslog y journald "are written such that each is wholly independent of each other and
# only one is implemented" y el Audit de 6.2.1.2.1 empieza por "- IF - journald will be used for logging on the system". Con
# rsyslog como método corporativo no aplican, igual que sus equivalentes con nota en OL9 (6.2.2.1.1-6.2.2.1.3). Solo ORACLE8:
# RHEL8 v4.0.0 tiene el mismo texto pero no se extiende sin decisión expresa.
_JOURNALD_OL8 = _JOURNALD_V4 | {"journal_remote_instalado", "journal_upload_auth", "journal_upload_activo"}
NOTA_OL8_JOURNAL_REMOTE = ("Sección 6.2 System Logging: 'The recommendations are written such that each is wholly independent of "
                           "each other and only one is implemented'; 6.2.1.2 pertenece a 6.2.1 Configure journald; Audit de "
                           "6.2.1.2.1: '- IF - journald will be used for logging on the system'.")
_RSYSLOG = frozenset({"journald_a_rsyslog", "rsyslog_instalado", "rsyslog_servicio", "rsyslog_filecreatemode", "rsyslog_logging",
                      "rsyslog_remoto", "rsyslog_no_recibe", "logrotate"})
NOTA_METODO = {"journald": {"ORACLE9": _JOURNALD_V2, "RHEL9": _JOURNALD_V2, "SLES15": _JOURNALD_V2,
                            "ORACLE8": _JOURNALD_OL8, "RHEL8": _JOURNALD_V4},
               "rsyslog": {b: _RSYSLOG for b in ("ORACLE9", "RHEL9", "SLES15", "ORACLE8", "RHEL8")}}
VERSION_BENCH = {"ORACLE9": "v2.0.0", "RHEL9": "v2.0.0", "SLES15": "v2.0.1", "ORACLE8": "v4.0.0", "RHEL8": "v4.0.0"}
NOTA_CAMINO = {
    "journald": "Note: This recommendation only applies if journald is the chosen method for client side logging. "
                "Do not apply this recommendation if rsyslog is used.",
    "rsyslog": "Note: This recommendation only applies if rsyslog is the chosen method for client side logging. "
               "Do not apply this recommendation if journald (systemd-journald) is used.",
}
MOTIVO_NA_LOGGING = {
    "journald_no_reenvia": "Rsyslog fue definido como el método corporativo principal de logging. Este control corresponde al "
                           "escenario en el que journald es el método seleccionado, por lo tanto no aplica.",
    "journal_upload_auth": "Rsyslog es el método corporativo de logging. La arquitectura no utiliza systemd-journal-upload para el "
                           "envío remoto de logs; este control pertenece al camino journald y no aplica al método seleccionado.",
    "journal_upload_activo": "systemd-journal-upload no requerido porque rsyslog es el método corporativo seleccionado. Este control "
                             "pertenece al camino journald y no aplica al método seleccionado (no se habilita el servicio).",
    "journal_remote_instalado": "Rsyslog es el método corporativo de logging. systemd-journal-remote solo se requiere si journald es "
                                "el método elegido para el envío remoto; este control pertenece al camino journald y no aplica.",
}


def politica_logging(ctx: Ctx) -> Optional[Dict]:
    """{'metodo', 'fuente'} si la organización definió el método de logging; None si no hay decisión."""
    pol = (ctx.listas or {}).get("metodo_logging") or {}
    m = str(pol.get("metodo") or "").strip().lower()
    if m not in ("rsyslog", "journald"):
        return None
    return {"metodo": m, "fuente": "config/listas_aprobadas.yml · metodo_logging=" + m
            + (f" · {ctx.listas.get('decisiones_origen')}" if ctx.listas.get("decisiones_origen") else "")}


def _na_por_metodo(ctx: Ctx, camino: str, cid: str, evaluador) -> Optional[Evaluacion]:
    """NO APLICA cuando el método corporativo elegido es el OTRO camino y el Benchmark tiene la nota de aplicabilidad.
    La evaluación técnica del otro camino se conserva como evidencia INFO (no decide)."""
    pol = politica_logging(ctx)
    if not pol or pol["metodo"] == camino or cid not in NOTA_METODO[camino].get(ctx.benchmark, ()):
        return None
    ev = Evaluacion()
    motivo = MOTIVO_NA_LOGGING.get(cid) if camino == "journald" else None
    motivo = motivo or (f"{pol['metodo'].capitalize()} es el método corporativo principal de logging. El CIS indica que esta "
                        f"recomendación solo aplica si {camino} es el método elegido; por lo tanto no aplica.")
    ev.add("aplicabilidad", f"Aplicabilidad: recomendación del camino {camino}", NA,
           f"{camino} como método de logging elegido", motivo, [])
    try:
        tecnico = evaluador(ctx)
        for c in tecnico.componentes:
            c.estado, c.descripcion = INFO, f"Informativo (no decide): {c.descripcion} — estado técnico {c.estado}"
            ev.componentes.append(c)
    except Exception:  # la evidencia informativa nunca puede cambiar el NO APLICA
        pass
    ev.mecanismo = {"motivo_visible": motivo, "tipo_cumplimiento": "NO_APLICA_METODO_LOGGING",
                    "fuente_politica": pol["fuente"],
                    "fuente_aplicabilidad": f"CIS {ctx.benchmark} {VERSION_BENCH.get(ctx.benchmark, '')}: "
                                            + (NOTA_OL8_JOURNAL_REMOTE if ctx.benchmark == "ORACLE8" and cid in _JOURNALD_OL8 - _JOURNALD_V4
                                               else NOTA_CAMINO[camino]),
                    **mecanismo_logging(ctx)}
    return ev


def _camino(camino: str, cid: str):
    def deco(f):
        def g(ctx: Ctx) -> Evaluacion:
            return _na_por_metodo(ctx, camino, cid, f) or f(ctx)
        g.__name__, g.__doc__ = f.__name__, f.__doc__
        return g
    return deco


def ev_journald_svc(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    if _v8(ctx):
        _estado_svc(ev, ctx, "journald.servicio", "systemd-journald", quiere_enabled=())
    else:
        _estado_svc(ev, ctx, "journald.servicio", "systemd-journald", quiere_enabled=("static",))
    return ev


def ev_journald_acceso(ctx: Ctx) -> Evaluacion:
    """CIS 6.2.1.2 (Manual). Audit: si existe /etc/tmpfiles.d/systemd.conf (override) se inspecciona ese; si no,
    /usr/lib/tmpfiles.d/systemd.conf; los archivos de log de journald deben crearse con 0640 o más restrictivo (o la
    política del sitio si es menos restrictiva). Decide: las entradas que crean/ajustan ARCHIVOS de journal (rutas con
    '.journal' y las recursivas Z sobre directorios de journal, con el modo efectivo para archivos). El resultado del
    script oficial (que marca REVIEW cualquier línea con modo > 0640, incluidas las de directorios y de /run/utmp) se
    muestra como INFO. Los permisos reales de los journals se validan con priv.journal_archivos."""
    ev = Evaluacion()
    prob = ctx.problema("journald.tmpfiles")
    l = ctx.lineas("journald.tmpfiles")
    if prob or not l:
        ev.add("tmpfiles", "Permisos en tmpfiles systemd.conf", ERROR, "", prob or "sin archivo systemd.conf de tmpfiles", ["journald.tmpfiles"]); return ev
    override = any(x.startswith("/etc/tmpfiles.d/systemd.conf:") for x in l)
    archivo = "/etc/tmpfiles.d/systemd.conf" if override else "/usr/lib/tmpfiles.d/systemd.conf"
    ev.add("override", "Override /etc/tmpfiles.d/systemd.conf", INFO, "si existe, reemplaza a /usr/lib/tmpfiles.d/systemd.conf",
           ("existe: se evalúa el override" if override else "no existe: se evalúa /usr/lib/tmpfiles.d/systemd.conf"), ["journald.tmpfiles"])
    lineas = [x.split(":", 1)[1] for x in l if x.startswith(archivo + ":")]
    revisar = [x.strip() for x in lineas if _RE_TMPF_CIS.search(x)]
    ev.add("script_cis", "Resultado del script del Audit CIS (patrón de modo > 0640 en cualquier línea)", INFO, "PASS o REVIEW",
           ("REVIEW por " + "; ".join(revisar) + " — no son archivos de journal (directorios o /run/utmp)") if revisar else "PASS",
           ["journald.tmpfiles"])
    reglas, malos = [], []
    for x in lineas:
        p = x.split()
        if len(p) < 3 or "journal" not in p[1]:
            continue
        tipo, ruta, modo = p[0], p[1], p[2]
        if tipo.lower().startswith("a"):
            reglas.append(f"{tipo} {ruta}: ACL (concede lectura a grupos; no cambia el modo)"); continue
        es_archivo = ".journal" in ruta
        if not es_archivo and not tipo.startswith("Z"):
            reglas.append(f"{tipo} {ruta} {modo} (directorio)"); continue
        v = _modo_tmpfiles(modo, False)
        ok = v is not None and (v & 0o137) == 0
        objetivo = ruta if es_archivo else ruta + "/* (recursivo)"
        reglas.append(f"{tipo} {objetivo} {modo} -> archivos {('%04o' % v) if v is not None else 'no interpretable'} ({'OK' if ok else 'FALLA'})")
        if not ok:
            malos.append(f"{tipo} {objetivo} {modo}")
    ev.add("archivos_journal", f"Archivos de journal con 0640 o más restrictivo ({archivo})", FALLA if malos else OK, "<= 0640",
           ("más permisivos: " + "; ".join(malos) + " | ") * bool(malos) + ("; ".join(reglas) or "sin entradas de journal (journald crea 0640)"),
           ["journald.tmpfiles"])
    reales, jprob = _evid(ctx, "priv.journal_archivos")
    if reales is None:
        ev.add("permisos_reales", "Permisos reales de los archivos de journal (informativo sin evidencia)", INFO, "<= 0640",
               f"no disponible: {jprob}", [])
    else:
        arch = [x.split("|") for x in reales if x.startswith("J|f|")]
        jr = [a for a in arch if len(a) >= 6 and ".journal" in a[5]]
        mal = [f"{a[5]} {a[2]}" for a in jr if (int(a[2], 8) & 0o137)]
        ausentes = [x.split("|")[1] for x in reales if x.startswith("JD|")]
        ev.add("permisos_reales", "Permisos reales de los archivos de journal", FALLA if mal else OK, "<= 0640",
               (f"{len(mal)} más permisivos: {mal[:20]}" if mal else f"{len(jr)} archivos de journal con 0640 o más restrictivo")
               + (f"; no existen: {', '.join(ausentes)}" if ausentes else ""), ["priv.journal_archivos"])
    if not malos and not any(c.estado == FALLA for c in ev.componentes):
        ev.mecanismo["motivo_visible"] = (f"{'Existe' if override else 'Sin'} override; {archivo} define los archivos de journal con "
                                          "0640 o más restrictivo (" + "; ".join(r for r in reglas if "->" in r) + ").")
    return ev


def ev_journald_rotacion(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("journald.conf")
    eff = _journald(ctx)
    claves = ("SystemMaxUse", "SystemKeepFree", "RuntimeMaxUse", "RuntimeKeepFree", "MaxFileSec")
    politica = {k: v for k, v in (ctx.exclusion.get("parametros_exclusion") or {}).items() if k in claves}
    if prob:
        ev.add("rotacion", "Rotación journald", ERROR, "", prob, ["journald.conf"]); return ev
    if not politica:
        enc = {k: eff.get(k, (None,))[0] for k in claves}
        ev.add("rotacion", "Rotación conforme a política del sitio", ORG, "política del sitio", str(enc), ["journald.conf"])
        return ev
    leidos = archivos_catconfig(ctx.lineas("journald.conf"))
    for k, esperado in politica.items():
        v = eff.get(k)
        ev.add(k, f"{k} (política del sitio definida por Infraestructura)",
               OK if v and v[0].lower() == esperado.lower() else FALLA, f"{k}={esperado}",
               f"{k}={v[0]} en {v[1]}" if v else f"no definido en ninguno de los {len(leidos)} archivos leídos", ["journald.conf"])
    ev.add("archivos_journald", "Archivos de configuración de journald leídos (orden de precedencia; el último gana)", INFO,
           "configuración efectiva (systemd-analyze cat-config systemd/journald.conf)", lista_archivos(leidos), ["journald.conf"])
    faltan = [k for k in politica if not eff.get(k)]
    if faltan:
        ev.mecanismo["motivo_visible"] = (f"{', '.join(faltan)} no están definidos en ninguno de los {len(leidos)} archivos de journald "
                                          f"leídos ({', '.join(leidos) or 'ninguno'}); journald usa sus valores por defecto y no la "
                                          "política definida por Infraestructura.")
    return ev


@_camino("journald", "journald_no_reenvia")
def ev_journald_fwd_no(ctx):
    return _journald_param(ctx, "ForwardToSyslog", "no")


@_camino("rsyslog", "journald_a_rsyslog")
def ev_journald_a_rsyslog(ctx):
    """CIS 6.2.3.3: - IF - rsyslog es el método preferido, ForwardToSyslog=yes efectivo (systemd-analyze cat-config).
    Se valida además rsyslog instalado, habilitado y activo (6.2.3.1/6.2.3.2): sin rsyslog en marcha el reenvío no
    tiene destino."""
    ev = _journald_param(ctx, "ForwardToSyslog", "yes")
    _pkg_comp(ev, ctx, "rsyslog", True, "rsyslog_instalado")
    _estado_svc(ev, ctx, "rsyslog.servicio", "rsyslog", comp="rsyslog_")
    pol = politica_logging(ctx)
    if pol:
        ev.mecanismo["fuente_politica"] = pol["fuente"]
    fw = next((c for c in ev.componentes if c.id == "ForwardToSyslog"), None)
    if fw and fw.estado == FALLA:
        ev.mecanismo["motivo_visible"] = (("Rsyslog es el método corporativo de logging, pero " if pol and pol["metodo"] == "rsyslog" else "")
                                          + f"journald no reenvía los eventos a rsyslog: {fw.encontrado} "
                                            "(se requiere ForwardToSyslog=yes).")
    return ev


@_camino("journald", "journald_compress")
def ev_journald_compress(ctx):
    return _journald_param(ctx, "Compress", "yes")


@_camino("journald", "journald_storage")
def ev_journald_storage(ctx):
    return _journald_param(ctx, "Storage", "persistent")


@_camino("journald", "journal_remote_instalado")
def ev_jremote_inst(ctx):
    ev = Evaluacion(); _pkg_comp(ev, ctx, "systemd-journal-remote", True); return ev


@_camino("journald", "journal_upload_auth")
def ev_jupload_auth(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("journald.upload_conf")
    kv = {}
    for l in ctx.lineas("journald.upload_conf"):
        _, _, resto = l.partition(":")
        if "=" in resto:
            k, v = resto.split("=", 1)
            kv[k.strip()] = v.strip()
    if prob:
        ev.add("lectura", "Lectura de journal-upload.conf", ERROR, "", prob, ["journald.upload_conf"]); return ev
    for k in ("URL", "ServerKeyFile", "ServerCertificateFile", "TrustedCertificateFile"):
        ev.add(k, f"{k} definido en journal-upload.conf", OK if kv.get(k) else FALLA,
               f"{k}=<valor del entorno>", kv.get(k, "no definido"), ["journald.upload_conf"])
    if kv:
        ev.add("validez", "URL y certificados corresponden al servidor de logs autorizado", ORG,
               "confirmación del servidor/PKI autorizados", kv.get("URL", ""), ["journald.upload_conf"])
    return ev


@_camino("journald", "journal_upload_activo")
def ev_jupload_activo(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    u = _unidades(ctx).get("systemd-journal-upload.service")
    prob = ctx.problema("journald.remoto")
    if prob or not u:
        ev.add("estado", "systemd-journal-upload habilitado y activo", ERROR, "", prob or "sin dato", ["journald.remoto"]); return ev
    ev.add("habilitado", "is-enabled", OK if u[0] == "enabled" else FALLA, "enabled", u[0], ["journald.remoto"])
    ev.add("activo", "is-active", OK if u[1] == "active" else FALLA, "active", u[1], ["journald.remoto"])
    return ev


@_camino("journald", "journal_remote_no_usado")
def ev_jremote_no(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    un = _unidades(ctx)
    prob = ctx.problema("journald.remoto")
    for u in ("systemd-journal-remote.socket", "systemd-journal-remote.service"):
        e = un.get(u)
        if prob or not e:
            ev.add(u, u, ERROR, "", prob or "sin dato", ["journald.remoto"]); continue
        ev.add(u, f"{u} no habilitado ni activo", FALLA if e[0] == "enabled" or e[1] == "active" else OK,
               "no enabled / no active", f"{e[0]} / {e[1]}", ["journald.remoto"])
    return ev


@_camino("rsyslog", "rsyslog_instalado")
def ev_rsyslog_inst(ctx):
    ev = Evaluacion(); _pkg_comp(ev, ctx, "rsyslog", True); return ev


@_camino("rsyslog", "rsyslog_servicio")
def ev_rsyslog_svc(ctx):
    ev = Evaluacion(); _estado_svc(ev, ctx, "rsyslog.servicio", "rsyslog"); return ev


def _rsyslog_lineas(ctx: Ctx) -> List[str]:
    return [l.split(":", 1)[1] if ":" in l else l for l in ctx.lineas("rsyslog.conf")]


@_camino("rsyslog", "rsyslog_filecreatemode")
def ev_rsyslog_filemode(ctx: Ctx) -> Evaluacion:
    """CIS 6.2.3.4: modo con el que rsyslog CREA LOS ARCHIVOS DE LOG: 0640 o más restrictivo. El Audit CIS busca
    `$FileCreateMode 0[0246][024]0`; se acepta además su equivalente RainerScript del módulo omfile (module(load=
    "builtin:omfile" fileCreateMode=...) y action(type="omfile" fileCreateMode=...)). Modo efectivo de cada acción de
    archivo: parámetro de la acción > $FileCreateMode anterior > parámetro del módulo omfile > 0644 (valor por defecto).
    El FileCreateMode del módulo imjournal controla su archivo de ESTADO: se muestra (INFO) y no decide."""
    ev = Evaluacion()
    prob = ctx.problema("rsyslog.conf")
    if prob:
        ev.add("filecreatemode", "Modo de creación de archivos de log", ERROR, "", prob, ["rsyslog.conf"]); return ev
    legacy, modulo, imj, acciones, dispositivos = None, None, [], [], []
    for org, t in _rsyslog_sentencias(ctx):
        m = re.match(r"^\$FileCreateMode\s+(\S+)", t, re.I)
        if m:
            legacy = (m.group(1), org); continue
        if t.lower().startswith("module("):
            prm = _rs_params(t)
            carga = prm.get("load", "").lower()
            if "imjournal" in carga and "filecreatemode" in prm:
                imj.append(f'FileCreateMode="{prm["filecreatemode"]}" ({org}, module imjournal)')
            elif carga in ("builtin:omfile", "omfile") and "filecreatemode" in prm:
                modulo = (prm["filecreatemode"], org)
            continue
        a = _rs_accion(t)
        if not a or a["tipo"] != "archivo":
            continue
        if a["destino"].startswith("/dev/"):
            dispositivos.append(a["destino"]); continue
        if a["modo"]:
            modo, origen = a["modo"], f"fileCreateMode de la acción ({org})"
        elif legacy:
            modo, origen = legacy[0], f"$FileCreateMode ({legacy[1]})"
        elif modulo:
            modo, origen = modulo[0], f"fileCreateMode del módulo omfile ({modulo[1]})"
        else:
            modo, origen = "0644", "valor por defecto de rsyslog (no configurado)"
        acciones.append((a["destino"], modo, origen, org))
    leidos = list(dict.fromkeys(o for o, _ in _rsyslog_sentencias(ctx) if o))
    ev.add("archivos_rsyslog", "Archivos de configuración de rsyslog leídos", INFO, "/etc/rsyslog.conf y /etc/rsyslog.d/*.conf",
           lista_archivos(leidos), ["rsyslog.conf"])
    ev.add("imjournal", "FileCreateMode del módulo imjournal (archivo de estado; informativo, NO decide 6.2.3.4)", INFO,
           "no aplica a los archivos de log", "; ".join(imj) or "no definido", ["rsyslog.conf"])
    if acciones:
        malos = [f"{d} {m} ({o})" for d, m, o, _ in acciones if not _RE_MODO_RSYSLOG_CIS.match(m)]
        ev.add("filecreatemode", "Modo de creación de los archivos de log (omfile / $FileCreateMode) 0640 o más restrictivo",
               FALLA if malos else OK, "$FileCreateMode 0640 (o fileCreateMode=\"0640\" de omfile) o más restrictivo",
               f"{len(acciones)} acciones de archivo: " + "; ".join(f"{d} → modo {m} [{o}; acción declarada en {f or 'n/d'}]"
                                                                  for d, m, o, f in acciones), ["rsyslog.conf"])
        if malos:
            sin_conf = not legacy and not modulo and not any(o.startswith("fileCreateMode de la acción") for _, _, o, _ in acciones)
            ev.mecanismo["motivo_visible"] = (
                (f"Se buscó $FileCreateMode, module(load=\"builtin:omfile\" fileCreateMode=...) y action(type=\"omfile\" "
                 f"fileCreateMode=...) en {len(leidos)} archivos ({', '.join(leidos) or 'ninguno'}) y no existe ninguno: "
                 if sin_conf else "Modo de creación más permisivo que 0640: ")
                + f"{len(malos)} de {len(acciones)} acciones de archivo usan " + ("0644 por defecto" if sin_conf else "; ".join(malos))
                + " (se requiere 0640 o más restrictivo)."
                + (" El FileCreateMode=\"0644\" encontrado pertenece a imjournal (archivo de estado) y no se usa para decidir." if imj else ""))
    else:
        conf = legacy or modulo
        ok = bool(conf) and bool(_RE_MODO_RSYSLOG_CIS.match(conf[0]))
        ev.add("filecreatemode", "$FileCreateMode / fileCreateMode de omfile 0640 o más restrictivo", OK if ok else FALLA,
               "$FileCreateMode 0640 (o más restrictivo)", f"{conf[0]} ({conf[1]})" if conf else "no definido (por defecto 0644)", ["rsyslog.conf"])
    if dispositivos:
        ev.observaciones.append("Acciones hacia dispositivos (no crean archivos de log; no evaluadas): " + ", ".join(sorted(set(dispositivos))))
    return ev


@_camino("rsyslog", "rsyslog_logging")
def ev_rsyslog_logging(ctx: Ctx) -> Evaluacion:
    """CIS 6.2.3.5 (Manual: logging "appropriate for your environment"). Con rsyslog_reglas_aprobadas
    (config/listas_aprobadas.yml): cada regla aprobada (selector + destino, comparación semántica: espacios y '-' de sin
    sync no cuentan) debe existir; reglas adicionales no fallan salvo descarte (~/stop) previo; la configuración debe ser
    sintácticamente válida (rsyslogd -N1, grupo priv.rsyslog_validacion). Sin política: MANUAL. Sin evidencia de -N1: no hay
    CUMPLE (ERROR DE AUDITORÍA)."""
    ev = Evaluacion()
    prob = ctx.problema("rsyslog.conf")
    if prob:
        ev.add("reglas", "Reglas de logging", ERROR, "", prob, ["rsyslog.conf"]); return ev
    reglas = _rs_reglas_legacy(ctx)
    pol = (ctx.listas or {}).get("rsyslog_reglas_aprobadas") or []
    if not pol:
        txt = [f"{s} {d}" for s, d, _ in reglas]
        ev.add("reglas", "Reglas de logging apropiadas según política del sitio", MANUAL if txt else FALLA,
               "reglas que envían cada facilidad a su archivo",
               ("[POLITICA_O_REVISION] " + f"{len(txt)} reglas: " + "; ".join(txt)[:500]) if txt else "sin reglas de logging",
               ["rsyslog.conf", "rsyslog.var_log"])
        return ev
    vistas = [(_selector_norm(s), d.lstrip("-"), o, i) for i, (s, d, o) in enumerate(reglas)]
    descartes = [i for _, d, _, i in vistas if d in ("~", "stop")]
    usadas = set()
    for k, r in enumerate(pol, 1):
        sel, dest = _selector_norm(str(r["selector"])), str(r["destino"]).lstrip("-")
        hit = next(((o, i) for s, d, o, i in vistas if s == sel and d == dest), None)
        contradicha = hit is not None and any(x < hit[1] for x in descartes)
        if hit:
            usadas.add(hit[1])
        ev.add(f"regla_{k}", f"Regla aprobada: {r['selector']} -> {r['destino']}", FALLA if (not hit or contradicha) else OK,
               f"{r['selector']}  {r['destino']}",
               ("presente" + (f" ({hit[0]})" if hit[0] else "") + (" pero precedida por un descarte (~/stop)" if contradicha else "")) if hit else "ausente",
               ["rsyslog.conf"])
    extra = [f"{reglas[i][0]} {reglas[i][1]}" for _, _, _, i in vistas if i not in usadas]
    otras = [f"{t}" for _, t in _rsyslog_sentencias(ctx) if t.lower().startswith("if ") and (_rs_accion(t) or {}).get("tipo")]
    ev.add("reglas_adicionales", "Reglas adicionales (no contradicen las aprobadas)", INFO, "permitidas",
           "; ".join(extra + otras) or "ninguna", ["rsyslog.conf"])
    val, vprob = _evid(ctx, "priv.rsyslog_validacion")
    if val is None:
        ev.add("sintaxis", "Configuración de rsyslog sintácticamente válida (rsyslogd -N1)", _sin_evidencia(vprob), "rc=0 sin errores",
               vprob, ["priv.rsyslog_validacion"])
    else:
        rc = next((x.split("|", 1)[1] for x in val if x.startswith("RN_RC|")), None)
        err = [x.split("|", 1)[1] for x in val if x.startswith("RN_OUT|") and re.search(r"\berror\b", x, re.I)]
        if "RN|NO_INSTALADO" in val:
            ev.add("sintaxis", "Configuración de rsyslog sintácticamente válida (rsyslogd -N1)", FALLA, "rc=0", "rsyslogd no instalado", ["priv.rsyslog_validacion"])
        else:
            ev.add("sintaxis", "Configuración de rsyslog sintácticamente válida (rsyslogd -N1)", OK if rc == "0" and not err else FALLA,
                   "rc=0 sin errores", f"rc={rc}" + (f"; errores: {err[:5]}" if err else "; End of config validation run"), ["priv.rsyslog_validacion"])
    archivos = [x for x in ctx.lineas("rsyslog.var_log") if re.search(r"\b(messages|secure|maillog|cron|spooler|boot\.log)$", x)]
    ev.add("archivos_log", "Archivos de log escritos (Audit CIS: ls -l /var/log/maillog)", INFO, "los archivos existen y reciben logs",
           "; ".join(" ".join(x.split()[-5:]) for x in archivos) or "sin listado", ["rsyslog.var_log"])
    ev.mecanismo["fuente_politica"] = "config/listas_aprobadas.yml · rsyslog_reglas_aprobadas" + (
        f" · {ctx.listas.get('decisiones_origen')}" if ctx.listas.get("decisiones_origen") else "")
    falt = [c.descripcion.split(": ", 1)[1] for c in ev.componentes if c.id.startswith("regla_") and c.estado == FALLA]
    if falt:
        ev.mecanismo["motivo_visible"] = "Faltan reglas aprobadas de rsyslog: " + "; ".join(falt) + "."
    return ev


def _destinos_remotos(ctx: Ctx) -> List[str]:
    return [a["destino"] for _, t in _rsyslog_sentencias(ctx) for a in [_rs_accion(t)] if a and a["tipo"] == "reenvio"]


@_camino("rsyslog", "rsyslog_remoto")
def ev_rsyslog_remoto(ctx: Ctx) -> Evaluacion:
    """CIS 6.2.3.6 (Manual): rsyslog envía los logs a un host central (basic @/@@ o advanced action(type="omfwd"
    target=...)). ForwardToSyslog (journald -> rsyslog local) NO equivale a envío remoto."""
    ev = Evaluacion()
    prob = ctx.problema("rsyslog.conf")
    d = _destinos_remotos(ctx)
    if prob:
        ev.add("destino", "Destino remoto", ERROR, "", prob, ["rsyslog.conf"]); return ev
    if _v8(ctx):
        validos = [x for x in d if not x.startswith("udp ")]
        ev.add("destino", "Envío a host remoto por TCP (@@ o action target)", OK if validos else FALLA,
               "*.* @@<loghost> o action(type=\"omfwd\" target=...)", "; ".join(d) or "sin destino remoto", ["rsyslog.conf"])
        if d and not validos:
            ev.observaciones.append("RHEL/OL 8 v4.0.0: un único @ (UDP) se considera estado de falla.")
    else:
        ev.add("destino", "Envío a host remoto (@/@@ o action target)", OK if d else FALLA,
               "*.* @@<loghost> o action(type=\"omfwd\" target=...)", "; ".join(d) or "sin destino remoto", ["rsyslog.conf"])
    rs = unidades(ctx, "rsyslog.servicio").get("rsyslog")
    locales = [a["destino"] for _, t in _rsyslog_sentencias(ctx) for a in [_rs_accion(t)] if a and a["tipo"] == "archivo"]
    ev.add("rsyslog_activo", "rsyslog en ejecución", INFO, "active", f"{rs[0]} / {rs[1]}" if rs else "sin dato", ["rsyslog.servicio"])
    ev.add("omfwd_activo", "Acciones de reenvío activas (omfwd / @ / @@)", INFO, ">= 1", str(len(d)), ["rsyslog.conf"])
    ev.add("logs_locales", "Logs locales configurados", INFO, "informativo", f"{len(locales)} acciones de archivo: {', '.join(locales)}", ["rsyslog.conf"])
    ev.add("destino_activo", "Destino remoto activo", INFO, "host central de logs", ", ".join(d) or "NO", ["rsyslog.conf"])
    ev.mecanismo["rsyslog_destinos"] = d
    if not d:
        ev.mecanismo["motivo_visible"] = ("Rsyslog no tiene una acción activa de reenvío hacia un host remoto (sin @/@@ ni "
                                          "action(type=\"omfwd\") activos; las líneas comentadas no cuentan). "
                                          "ForwardToSyslog=yes no equivale a envío remoto.")
    return ev


@_camino("rsyslog", "rsyslog_no_recibe")
def ev_rsyslog_no_recibe(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("rsyslog.conf")
    pats = [r'^\s*module\(load="?imtcp"?\)', r'^\s*input\(type="?imtcp"?\b', r"^\s*\$ModLoad\s+imtcp\b", r"^\s*\$InputTCPServerRun\b"]
    hall = [x.strip() for x in _rsyslog_lineas(ctx) for p in pats if re.search(p, x, re.I)]
    ev.add("imtcp", "rsyslog no acepta logs remotos (imtcp)", FALLA if hall else (ERROR if prob else OK),
           "sin module/input imtcp ni $ModLoad/$InputTCPServerRun", "; ".join(hall) or prob or "ninguna directiva de recepción", ["rsyslog.conf"])
    return ev


@_camino("rsyslog", "logrotate")
def ev_logrotate(ctx: Ctx) -> Evaluacion:
    """CIS 6.2.3.8 (Manual). Script del Audit: si existe /etc/logrotate.conf -> REVIEW (rotación según política del sitio);
    sin logrotate configurado -> FAIL. Con logrotate_rsyslog_politica: cada archivo que rsyslog escribe (reglas activas;
    no se asume /var/log/rsyslog/*.log) debe estar cubierto por un bloque de logrotate con las directivas de la política
    (globales + bloque); la configuración debe pasar `logrotate -d`. Requiere priv.logrotate; sin esa evidencia no hay CUMPLE."""
    ev = Evaluacion()
    if ctx.problema("logrotate.conf"):
        ev.add("logrotate", "Configuración de logrotate", ERROR, "", ctx.problema("logrotate.conf"), ["logrotate.conf"]); return ev
    l = ctx.lineas("logrotate.conf")
    conf = [x.split(":", 1)[1].strip() for x in l if x.startswith("/etc/logrotate.conf:")]
    if not conf and not l:
        ev.add("logrotate", "logrotate configurado", FALLA, "/etc/logrotate.conf presente", "rsyslog is in use and logrotate is not configured",
               ["logrotate.conf"]); return ev
    dest = _rs_archivos(ctx)
    ev.add("destinos", "Archivos que rsyslog escribe (reglas activas)", INFO, "cada uno cubierto por logrotate",
           ", ".join(dest) or "ninguno", ["rsyslog.conf"])
    ev.add("script_cis", "Resultado del script del Audit CIS", INFO, "REVIEW (según política del sitio)",
           "REVIEW: /etc/logrotate.conf existe (" + "; ".join(conf)[:300] + ")", ["logrotate.conf"])
    pol = (ctx.listas or {}).get("logrotate_rsyslog_politica")
    if not pol:
        ev.add("logrotate", "Rotación conforme a política del sitio", ORG, "logrotate_rsyslog_politica (config/listas_aprobadas.yml)",
               "[EVIDENCIA_ORGANIZACIONAL] política de rotación no definida", ["logrotate.conf"])
        return ev
    ev.mecanismo["fuente_politica"] = "config/listas_aprobadas.yml · logrotate_rsyslog_politica" + (
        f" · {ctx.listas.get('decisiones_origen')}" if ctx.listas.get("decisiones_origen") else "")
    lr, lprob = _evid(ctx, "priv.logrotate")
    if lr is None:
        listado = [x for x in l if not x.startswith("/etc/logrotate.conf:")]
        ev.add("cobertura", "Cada archivo de rsyslog cubierto por logrotate con las directivas de la política", _sin_evidencia(lprob),
               ", ".join(str(x) for x in pol.get("directivas") or []) + (", postrotate que recarga rsyslog" if pol.get("postrotate_recarga_rsyslog") else ""),
               f"{lprob}. Archivos en /etc/logrotate.d: {', '.join(listado)}", ["logrotate.conf", "priv.logrotate"])
        return ev
    import fnmatch
    glob_, bloques = _logrotate_parse(lr)
    for d in dest:
        b = [x for x in bloques if any(fnmatch.fnmatch(d, p) for p in x["patrones"])]
        if not b:
            ev.add(f"archivo_{d}", f"logrotate cubre {d}", FALLA, "bloque de logrotate que lo incluya", "no cubierto por ningún bloque",
                   ["priv.logrotate", "rsyslog.conf"]); continue
        bl = b[-1]
        efectivas = {**glob_, **bl["directivas"]}
        falta = _politica_logrotate(efectivas, bl["scripts"], pol)
        ev.add(f"archivo_{d}", f"logrotate cubre {d} con la política", FALLA if falta else OK,
               ", ".join(str(x) for x in pol.get("directivas") or []), (f"faltan: {', '.join(falta)}" if falta else "conforme")
               + f" (bloque de {bl['archivo']}: {' '.join(bl['patrones'])})", ["priv.logrotate", "rsyslog.conf"])
    rc = next((x.split("|", 1)[1] for x in lr if x.startswith("LD_RC|")), None)
    errs = [x.split("|", 1)[1] for x in lr if x.startswith("LD|error")]
    ev.add("sintaxis", "logrotate -d sin errores", OK if rc == "0" and not errs else FALLA, "rc=0 sin 'error'",
           f"rc={rc}" + (f"; {errs[:5]}" if errs else ""), ["priv.logrotate"])
    falt = [c.descripcion.replace("logrotate cubre ", "") + f": {c.encontrado}" for c in ev.componentes if c.id.startswith("archivo_") and c.estado == FALLA]
    if falt:
        ev.mecanismo["motivo_visible"] = "Rotación de rsyslog no conforme con la política: " + "; ".join(falt) + "."
    return ev


def ev_logs_acceso(ctx: Ctx) -> Evaluacion:
    """CIS 6.2.4.1: script oficial sobre los archivos de /var/log preseleccionados por
    `find -L /var/log -type f \\( -perm /0137 -o ! -user root -o ! -group root \\)`, con el criterio de cada tipo:
    lastlog/wtmp/btmp/README <= 0664 root:(root|utmp); secure/auth.log/syslog/messages <= 0640 (root|syslog):(root|adm);
    SSSD/sssd <= 0660; gdm/gdm3 <= 0660; *.journal(~) <= 0640 root:(root|systemd-journal); resto <= 0640 (root|syslog):
    (root|adm), aceptando el propietario/grupo de sistema del archivo si el UID es < UID_MIN y el grupo no tiene miembros
    ni usuarios interactivos como grupo primario. Diferencia documentada: las comparaciones de propietario/grupo son
    exactas (el script usa =~ sin anclar)."""
    ev = Evaluacion()
    prob = ctx.problema("logs.permisos")
    if prob:
        ev.add("logs", "Permisos de /var/log", ERROR, "", prob, ["logs.permisos"]); return ev
    grupos = {}
    for l in ctx.lineas("usr.group"):
        p = l.split(":")
        if len(p) >= 3:
            grupos[p[0]] = p[2]
    uid_min = ctx.uid_min
    usuarios_gid: Dict[str, List[int]] = {}
    for l in ctx.lineas("usr.passwd"):
        p = l.split(":")
        if len(p) >= 4 and p[2].isdigit():
            usuarios_gid.setdefault(p[3], []).append(int(p[2]))
    malos, especiales, revisados, elementos = [], [], 0, []
    for l in ctx.lineas("logs.permisos"):
        p = l.split("^")
        if len(p) != 6:
            continue
        revisados += 1
        n, modo, u, uid, g, gid = p
        b = n.rsplit("/", 1)[-1]
        if _RE_LOG_ESPECIAL.match(b):
            m, au, ag, tipo = 0o113, r"root", r"(root|utmp)", "lastlog/wtmp/btmp/README (<= 0664, root:root|utmp)"
        elif b in ("secure", "auth.log", "syslog", "messages"):
            m, au, ag, tipo = 0o137, r"(root|syslog)", r"(root|adm)", "secure/auth.log/syslog/messages (<= 0640)"
        elif b in ("SSSD", "sssd"):
            m, au, ag, tipo = 0o117, r"(root|SSSD)", r"(root|SSSD)", "SSSD (<= 0660)"
        elif b in ("gdm", "gdm3"):
            m, au, ag, tipo = 0o117, r"root", r"(root|gdm|gdm3)", "gdm (<= 0660)"
        elif b.endswith((".journal", ".journal~")):
            m, au, ag, tipo = 0o137, r"root", r"(root|systemd-journal)", "journal (<= 0640, root:root|systemd-journal)"
        else:
            m, au, ag, tipo = 0o137, r"(root|syslog)", r"(root|adm)", "resto (<= 0640, root|syslog : root|adm)"
            if uid.isdigit() and int(uid) < uid_min and not grupos.get(g):
                if not re.fullmatch(au, u):
                    au = f"(root|syslog|{re.escape(u)})"
                if not re.fullmatch(ag, g) and not any(x >= uid_min for x in usuarios_gid.get(gid, [])):
                    ag, tipo = f"(root|adm|{re.escape(g)})", tipo + f"; grupo de sistema {g} aceptado"
        mot = []
        if not modo_ok(modo, m):
            mot.append(f"modo {modo} > {max_modo(m)}")
        if not re.fullmatch(au, u):
            mot.append(f"propietario {u}")
        if not re.fullmatch(ag, g):
            mot.append(f"grupo {g}" + (" (grupo primario de un usuario interactivo)" if any(x >= uid_min for x in usuarios_gid.get(gid, [])) else ""))
        if mot:
            malos.append(f"{n} ({modo} {u}:{g}): {', '.join(mot)}")
            elementos.append({"archivo": n, "modo": modo, "propietario": u, "grupo": g, "motivo": ", ".join(mot)})
        elif _RE_LOG_ESPECIAL.match(b) or "aceptado" in tipo:
            especiales.append(f"{n} {modo} {u}:{g} — {tipo}")
    ev.add("logs", "Archivos de /var/log con permisos y propietarios CIS", FALLA if malos else OK, "según tipo de archivo (script CIS)",
           f"{revisados} archivos revisados (preseleccionados por el find del CIS), {revisados - len(malos)} conformes y "
           f"{len(malos)} no conformes" + (": " + "; ".join(malos) if malos else ""), ["logs.permisos"], elementos=elementos)
    if especiales:
        ev.add("excepciones_cis", "Archivos conformes por su tratamiento específico del CIS", INFO, "criterio específico del script",
               "; ".join(especiales), ["logs.permisos"])
    if malos:
        ev.mecanismo["motivo_visible"] = f"{len(malos)} de {revisados} archivos revisados no cumplen: " + "; ".join(malos) + "."
    return ev


# --------------------------------------------------------------- permisos de cuentas

def _perm_archivo(ruta: str):
    sombra = "shadow" in ruta

    def f(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        prob = ctx.problema("perm.cuentas")
        fila = next((l for l in ctx.lineas("perm.cuentas") if l.split("|")[0] == ruta), None)
        if fila is None:
            reg = ctx.reg("perm.cuentas")
            err = reg.stderr if reg is not None else ""
            inexistente = re.search(rf"cannot stat(x)? '{re.escape(ruta)}': No such file or directory", err or "")
            if "gshadow" in ruta and reg is not None and reg.rc in (0, 1) and inexistente:
                # Decisión del usuario (2026-10-16): un gshadow inexistente (ENOENT, no un error de permisos) no tiene
                # permisos que asegurar. El Audit CIS no contempla el caso: NO APLICA con causa explícita.
                ev.add("stat", ruta, NA, "archivo existente con permisos CIS",
                       f"{ruta} no existe en el servidor (stat: No such file or directory); no hay permisos que evaluar",
                       ["perm.cuentas"])
                ev.mecanismo["motivo_visible"] = (f"NO APLICA: {ruta} no existe en el servidor (stat devolvió «No such file or "
                                                  "directory», no un error de permisos), por lo que no hay permisos que asegurar.")
                return ev
            ev.add("stat", ruta, ERROR, "", prob or f"{ruta} no existe o no se pudo leer", ["perm.cuentas"]); return ev
        _, modo, u, uid, g, gid = fila.split("|")
        if not sombra:
            mascara, grupos, txt = 0o133, ("root",), "0644 o más restrictivo, root:root"
        elif ctx.familia == SLES:
            mascara, grupos, txt = 0o137, ("root", "shadow"), "0640 o más restrictivo, root:root o root:shadow"
        else:
            mascara, grupos, txt = 0o777, ("root",), "0000, root:root"
        ev.add("modo", f"Modo de {ruta}", OK if modo_ok(modo, mascara) else FALLA, max_modo(mascara), modo, ["perm.cuentas"])
        ev.add("propietario", f"Propietario de {ruta}", OK if u == "root" and uid == "0" else FALLA, "0/root", f"{uid}/{u}", ["perm.cuentas"])
        ev.add("grupo", f"Grupo de {ruta}", OK if g in grupos else FALLA, "/".join(grupos), f"{gid}/{g}", ["perm.cuentas"])
        return ev
    return f


# --------------------------------------------------------------- filesystem profundo

def fs_completitud(ctx: Ctx, cat: str) -> Optional[Dict]:
    """Completitud de la evidencia del recolector de filesystem profundo (fsd.completitud) para una categoría
    (ww, nu, sg13, sg6, dot), o None si la ejecución no incluyó filesystem profundo."""
    from .filesystem import completitud
    if ctx.reg("fsd.completitud") is None:
        return None
    return completitud(ctx.lineas("fsd.completitud"), cat) or {"completo": False, "truncado": False, "total": 0,
                                                                  "motivo": "sin datos de completitud"}


def aplicar_completitud(ev: Evaluacion, comp: Optional[Dict], cid: str, desc: str) -> None:
    """Filesystem profundo: un recorrido TRUNCADO o INCOMPLETO nunca produce CUMPLE. Con un hallazgo concluyente se mantiene la
    FALLA (NO CUMPLE) y se informa el recuento total; sin hallazgos -> ERROR DE AUDITORÍA."""
    if comp and comp.get("ajenos"):
        ev.add("montajes_ajenos", "Puntos de montaje de otro sistema de archivos no consultables (no forman parte del recorrido -xdev)",
               INFO, "sin efecto en la evidencia: el recorrido no entra en otros sistemas de archivos",
               "; ".join(comp["ajenos"]), [cid, "fsd.completitud"])
    if not comp or (comp["completo"] and not comp["truncado"]):
        return
    motivo = ("[SALIDA_TRUNCADA] " if comp["truncado"] else "[RECORRIDO_INCOMPLETO] ") + \
             f"total {comp['total']} entradas; {comp['motivo'] or 'recorrido no completado'}"
    if any(c.estado == FALLA for c in ev.componentes):
        ev.observaciones.append(f"{desc}: evidencia {'truncada' if comp['truncado'] else 'incompleta'} ({motivo}); la falla es concluyente.")
        return
    ev.componentes = [c for c in ev.componentes if c.estado not in (OK,)]
    ev.add("completitud", desc, ERROR, "recorrido completo y no truncado", motivo, [cid, "fsd.completitud"])


def _profundo(ctx: Ctx, ev: Evaluacion, cid: str, desc: str) -> bool:
    if ctx.omitido(cid):
        ev.add("recorrido", desc, MANUAL, "recorrido completo de filesystems locales",
               "[PENDIENTE_AUDITORIA_PROFUNDA] recorrido intensivo no autorizado en esta ejecución (auditoria_filesystem_profunda=false)", [cid])
        return True
    prob = ctx.problema(cid)
    if prob:
        ev.add("recorrido", desc, ERROR, "", prob, [cid]); return True
    return False


def ev_world_writable(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    if _profundo(ctx, ev, "fs.world_writable", "Archivos world-writable"):
        return ev
    arch, dirs = [], []
    for l in ctx.lineas("fs.world_writable"):
        t, m, p = l.split("|", 2)
        if t == "f":
            arch.append(p)
        elif t == "d" and not (int(m, 8) & 0o1000):
            dirs.append(p)
    ev.add("archivos", "Sin archivos world-writable", FALLA if arch else OK, "0", f"{len(arch)}: {arch[:20]}", ["fs.world_writable"])
    ev.add("directorios", "Directorios world-writable con sticky bit", FALLA if dirs else OK, "0 sin sticky", f"{len(dirs)}: {dirs[:20]}", ["fs.world_writable"])
    if ctx.sin_sudo("fs.world_writable") and not (arch or dirs):
        ev.add("cobertura", "Recorrido con privilegios", ERROR, "sudo -n", "[PERMISOS_INSUFICIENTES] recorrido sin sudo: resultado no concluyente", ["fs.world_writable"])
    aplicar_completitud(ev, fs_completitud(ctx, "ww"), "fs.world_writable", "Recorrido world-writable")
    return ev


def ev_sin_dueno(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    if _profundo(ctx, ev, "fs.sin_dueno", "Archivos sin dueño/grupo"):
        return ev
    nu = [l.split("|", 1)[1] for l in ctx.lineas("fs.sin_dueno") if l.startswith("NOUSER|")]
    ng = [l.split("|", 1)[1] for l in ctx.lineas("fs.sin_dueno") if l.startswith("NOGROUP|")]
    ev.add("sin_usuario", "Sin archivos/directorios sin usuario propietario", FALLA if nu else OK, "0", f"{len(nu)}: {nu[:20]}",
           ["fs.sin_dueno"], subcondicion="sin_usuario")
    ev.add("sin_grupo", "Sin archivos/directorios sin grupo", FALLA if ng else OK, "0", f"{len(ng)}: {ng[:20]}",
           ["fs.sin_dueno"], subcondicion="sin_grupo")
    aplicar_completitud(ev, fs_completitud(ctx, "nu"), "fs.sin_dueno", "Recorrido sin dueño/grupo")
    if ctx.sin_sudo("fs.sin_dueno") and not (nu or ng):
        ev.add("cobertura", "Recorrido con privilegios", ERROR, "sudo -n", "[PERMISOS_INSUFICIENTES] recorrido sin sudo: resultado no concluyente", ["fs.sin_dueno"])
    return ev


def ev_suid_sgid(ctx: Ctx) -> Evaluacion:
    """CIS 7.1.13 (Manual): inventario de SUID/SGID para revisión. Sin lista aprobada (suid_sgid en
    config/listas_aprobadas.yml): REQUIERE EVIDENCIA ORGANIZACIONAL (pertenecer a un paquete no equivale a estar
    autorizado). NO CUMPLE solo ante un hallazgo objetivo: binario fuera de la lista aprobada o integridad RPM alterada
    (rpm -V: tamaño, digest, modo, usuario, grupo o enlace) según priv.suid_rpm."""
    ev = Evaluacion()
    if _profundo(ctx, ev, "fs.suid_sgid", "Listado de SUID/SGID"):
        return ev
    suid, sgid, meta = [], [], {}
    for l in ctx.lineas("fs.suid_sgid"):
        if l.startswith("#MOUNT|"):
            continue
        m, u, g, p = l.split("|", 3)
        meta[p] = f"{m} {u}:{g}"
        if int(m, 8) & 0o4000:
            suid.append(p)
        if int(m, 8) & 0o2000:
            sgid.append(p)
    aprob = set(ctx.listas.get("suid_sgid") or [])
    for sub, lista in (("suid", suid), ("sgid", sgid)):
        if aprob:
            no = [x for x in lista if x not in aprob]
            ev.add(sub, f"Binarios {sub.upper()} revisados contra lista aprobada", FALLA if no else OK, "en lista aprobada",
                   f"no aprobados: {no}" if no else f"{len(lista)} aprobados", ["fs.suid_sgid"], subcondicion=sub)
        else:
            ev.add(sub, f"Binarios {sub.upper()} revisados", ORG, "lista aprobada de binarios (no suministrada)",
                   f"[EVIDENCIA_ORGANIZACIONAL] {len(lista)} binarios {sub.upper()} encontrados (modo propietario:grupo), pendientes de "
                   "revisión contra una lista aprobada por la organización: " + "; ".join(f"{x} ({meta.get(x)})" for x in lista),
                   ["fs.suid_sgid"], subcondicion=sub)
    rpm, rprob = _evid(ctx, "priv.suid_rpm")
    if rpm is None:
        ev.add("rpm", "Paquete RPM propietario e integridad (rpm -V)", INFO, "cada binario pertenece a un paquete y no está alterado",
               f"no disponible: {rprob}", [])
    else:
        decl = {}
        for x in rpm:
            q = x.split("|")
            if q[0] == "RS" and len(q) >= 4:
                decl[q[2]] = q[3]
        alter = {}
        for x in rpm:
            q = x.split("|", 2)
            if q[0] == "RV" and len(q) == 3:
                m = re.match(r"^(\S+)\s+(?:[cdglr]\s+)?(/\S+)", q[2])
                if m and m.group(2) in meta and (m.group(1) == "missing" or re.search(r"[SM5UGL]", m.group(1))):
                    alter[m.group(2)] = f"{m.group(1)} ({q[1]})"
        for sub, lista in (("suid", suid), ("sgid", sgid)):
            sin = [x for x in lista if x not in decl]
            mal = [f"{x}: {alter[x]}" for x in lista if x in alter]
            ev.add(f"integridad_{sub}", f"Integridad RPM de los binarios {sub.upper()} (rpm -V)", FALLA if mal else OK,
                   "sin cambios de tamaño, digest, modo, usuario, grupo o enlace", "; ".join(mal) or f"{len(lista) - len(sin)} verificados sin cambios",
                   ["priv.suid_rpm", "fs.suid_sgid"], subcondicion=sub)
            if sin:
                ev.add(f"sin_paquete_{sub}", f"Binarios {sub.upper()} sin paquete RPM (requieren revisión)", INFO, "pertenecer a un paquete",
                       ", ".join(sin), ["priv.suid_rpm", "fs.suid_sgid"], subcondicion=sub)
    comp = fs_completitud(ctx, "sg13")
    if comp and not (comp["completo"] and not comp["truncado"]) and not any(c.estado == FALLA for c in ev.componentes):
        ev.componentes = []
    aplicar_completitud(ev, comp, "fs.suid_sgid", "Inventario SUID/SGID")
    return ev


# --------------------------------------------------------------- cuentas

def _passwd(ctx: Ctx) -> List[List[str]]:
    return [l.split(":") for l in ctx.lineas("usr.passwd") if l.count(":") >= 5]


def ev_shadowed(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    no = [p[0] for p in _passwd(ctx) if p[1] != "x"]
    ev.add("shadow", "Todas las cuentas usan shadow (campo 2 = x)", ERROR if ctx.problema("usr.passwd") else (FALLA if no else OK),
           "x", f"cuentas sin shadow: {no}" if no else "todas", ["usr.passwd"])
    return ev


def ev_shadow_vacio(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("usr.shadow_vacios")
    l = ctx.lineas("usr.shadow_vacios")
    ev.add("vacios", "Sin contraseñas vacías en /etc/shadow", ERROR if prob else (FALLA if l else OK), "ninguna",
           prob or (f"cuentas con contraseña vacía: {l}" if l else "ninguna"), ["usr.shadow_vacios"])
    return ev


def ev_grupos_existen(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    gids = {l.split(":")[1] for l in ctx.lineas("usr.group") if l.count(":") >= 2}
    no = [f"{p[0]} (GID {p[3]})" for p in _passwd(ctx) if p[3] not in gids]
    prob = ctx.problema("usr.passwd") or ctx.problema("usr.group")
    ev.add("gids", "GID primario de cada usuario existe en /etc/group", ERROR if prob else (FALLA if no else OK), "todos",
           f"inexistentes: {no}" if no else "todos existen", ["usr.passwd", "usr.group"])
    return ev


def _dup(ctx: Ctx, cid: str, idx: int, nombre: str, desc: str) -> Evaluacion:
    ev = Evaluacion()
    vals = [l.split(":") for l in ctx.lineas(cid) if ":" in l]
    cnt = {}
    for v in vals:
        cnt.setdefault(v[idx], []).append(v[0])
    d = {k: v for k, v in cnt.items() if len(v) > 1}
    ev.add(nombre, desc, ERROR if ctx.problema(cid) else (FALLA if d else OK), "sin duplicados", str(d) if d else "ninguno", [cid])
    return ev


def ev_dup_uid(ctx):
    return _dup(ctx, "usr.passwd", 2, "uid", "UIDs únicos en /etc/passwd")


def ev_dup_gid(ctx):
    return _dup(ctx, "usr.group", 1, "gid", "GIDs únicos en /etc/group")


def ev_dup_usuario(ctx):
    ev = _dup(ctx, "usr.passwd", 0, "usuario", "Nombres de usuario únicos en /etc/passwd")
    ev.observaciones.append("Adaptación del procedimiento Audit: el script del Benchmark lee /etc/group; se evalúa /etc/passwd (ver campo 'adaptacion').")
    return ev


def ev_dup_grupo(ctx):
    return _dup(ctx, "usr.group", 0, "grupo", "Nombres de grupo únicos en /etc/group")


def ev_uid0(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    u = [p[0] for p in _passwd(ctx) if p[2] == "0"]
    ev.add("uid0", "Solo root tiene UID 0", ERROR if ctx.problema("usr.passwd") else (OK if u == ["root"] else FALLA), "root", ", ".join(u), ["usr.passwd"])
    return ev


def ev_root_path(ctx: Ctx) -> Evaluacion:
    """CIS 5.4.2.5 por análisis ESTÁTICO (decisión R6): nunca se ejecuta ni se hace source del perfil de root (ni sudo -iu,
    ni su -). Cada entrada del PATH: existe y es directorio (stat -L: se evalúa el DESTINO de los enlaces), propietario root,
    modo 0755 o más restrictivo; sin '::', ':' final ni '.'. Las entradas inexistentes fallan ('is not a directory').

    Ajuste 2026-10-26: se distinguen
      - HALLAZGOS CONFIRMADOS: defectos en entradas cuya incorporación al PATH efectivo está DEMOSTRADA estáticamente
        (deciden NO CUMPLE);
      - REFERENCIAS POTENCIALES: directorios declarados en los archivos de inicio cuya incorporación no puede demostrarse
        (informativas: nunca deciden NO CUMPLE);
      - LIMITACIONES del análisis.
    NO CUMPLE solo con evidencia concluyente; CUMPLE solo si todas las condiciones están verificadas; en otro caso
    REQUIERE VALIDACIÓN MANUAL."""
    from . import root_path as rp
    ev = Evaluacion()
    prob = ctx.problema("usr.root_path")
    if prob:
        ev.add("path", "PATH de root (análisis estático de archivos de inicio)", ERROR, "", prob, ["usr.root_path"]); return ev
    stdout = ctx.reg("usr.root_path").stdout
    a = rp.analizar(stdout)
    inc = rp.incorporaciones(stdout)
    flujo = inc["flujo_recolectado"]
    dirs = {k: (None if v is None else {**v, "owner": v["owner"], "enlace": False}) for k, v in a["directorios"].items()}
    pd, _ = _evid(ctx, "priv.root_path_dirs")
    for x in pd or []:
        q = x.split("|")
        if q[0] == "PD" and len(q) >= 3:
            if q[2] == "NO_EXISTE":
                dirs[q[1]] = None
            elif len(q) >= 8:
                dirs[q[1]] = {"mode": q[4], "owner": q[5], "tipo": q[7], "enlace": q[2] == "enlace=1", "destino": q[3].split("=", 1)[1]}
    ids_ev = ["usr.root_path"] + (["priv.root_path_dirs"] if pd is not None else [])
    ev.mecanismo = {"analisis_estatico_root_path": {k: a.get(k) for k in ("root_shell", "root_home", "archivos_analizados",
                                                                          "archivos_cargados", "bloqueos", "origen", "path")}}
    ev.mecanismo["analisis_estatico_root_path"]["incorporaciones"] = inc
    limitaciones = [] if flujo else [rp.LIMITACION_SIN_FLUJO]

    if a["determinable"] and flujo:
        path = a["path"]
        prob_estr = []
        if "::" in path:
            prob_estr.append("directorio vacío (::)")
        if path.rstrip().endswith(":"):
            prob_estr.append("':' final")
        if re.search(r"(^|:)\.(:|$)", path):
            prob_estr.append("directorio actual (.)")
        ev.add("estructura", "PATH sin '::', ':' final ni '.'", FALLA if prob_estr else OK, "sin entradas vacías ni '.'",
               (", ".join(prob_estr) + f" — PATH={path}") if prob_estr else f"PATH={path} (origen: {', '.join(a['origen'])})", ["usr.root_path"])
        for d in [x for x in path.split(":") if x and x != "."]:
            if d not in dirs:
                ev.add(f"entrada_{d}", f"PATH: {d}", ERROR, "directorio root <= 0755", f"[SALIDA_NO_INTERPRETABLE] sin metadatos de {d}", ids_ev); continue
            ok, txt = _dir_path_defecto(d, dirs[d])
            ev.add(f"entrada_{d}", f"Directorio {d} del PATH de root", OK if ok else FALLA, _ESPERADO_DIR_PATH, txt, ids_ev)
        return ev

    det = "; ".join(f"{b['archivo']}" + (f" línea {b['linea']}" if b["linea"] else "") + f" ({b['construccion']})"
                    + (f" «{b['texto']}»" if b["texto"] else "") for b in a["bloqueos"][:12])
    if len(a["bloqueos"]) > 12:
        det += f" (+{len(a['bloqueos']) - 12} más)"
    if a["determinable"]:
        limitaciones.append(f"PATH calculado estáticamente (no demostrado): {a['path']}")
    elif det:
        limitaciones.append("el PATH completo no se puede calcular sin ejecutar los archivos de inicio (la auditoría nunca los "
                            f"ejecuta): {det}")
    confirmados, potenciales = [], []
    for e in inc["entradas"]:
        d = e["directorio"]
        arch, _, lin = e["origen"].rpartition(":")
        donde_txt = f"{arch}, línea {lin}" if lin.isdigit() else e["origen"]
        if d not in dirs and pd is None:
            ev.add(f"entrada_{d}", f"Directorio {d} del PATH de root ({donde_txt})", ERROR, "directorio root <= 0755",
                   f"[SALIDA_NO_INTERPRETABLE] sin metadatos de {d}", ids_ev); continue
        ok, txt = _dir_path_defecto(d, dirs.get(d))
        if e["demostrada"]:
            ev.add(f"entrada_{d}", f"Directorio {d} del PATH de root (incorporación demostrada: {donde_txt})", OK if ok else FALLA,
                   _ESPERADO_DIR_PATH, f"{txt} · cadena de carga: {e['cadena']}", ids_ev)
            if not ok:
                confirmados.append(f"{d} ({txt.split(' — ')[0]}; {donde_txt})")
        else:
            ev.add(f"potencial_{d}", f"Referencia potencial: {d} (declarado en {donde_txt}; incorporación al PATH efectivo no demostrada)",
                   INFO, _ESPERADO_DIR_PATH,
                   f"{txt} · declarado con «{e['asignacion']}» · cadena de carga: {e['cadena']} · no concluyente: {e['motivo']}", ids_ev)
            potenciales.append(f"{d} ({txt.split(' — ')[0]}; {donde_txt})")
    otros = [o for o in rp.observaciones_directorios(a["directorios"]) if not any(o.startswith(x["directorio"] + ":") for x in inc["entradas"])]
    if confirmados:
        ev.add("path", "Resto del PATH de root (informativo: no decide el resultado)", INFO,
               "PATH completo calculable leyendo los archivos de inicio", "; ".join(limitaciones) or "—", ["usr.root_path"])
        ev.mecanismo["motivo_visible"] = (
            "El CIS 5.4.2.5 exige que cada directorio del PATH de root exista, sea propiedad de root y no tenga escritura "
            "para grupo ni otros. Hallazgos confirmados (incorporación al PATH demostrada por análisis estático): "
            + "; ".join(confirmados) + "." + (f" Referencias potenciales (no deciden): {'; '.join(potenciales)}." if potenciales else ""))
        return ev
    resumen = ("Hallazgos confirmados: ninguno. "
               + (f"Referencias potenciales (declaradas en los archivos de inicio, no demostradas en el PATH efectivo): {'; '.join(potenciales)}. "
                  if potenciales else "")
               + "Limitaciones del análisis: " + "; ".join(limitaciones) + ".")
    ev.add("path", "PATH efectivo de root no demostrable por análisis estático", MANUAL,
           "PATH efectivo determinable y cada entrada verificada", f"[ANALISIS_ESTATICO_NO_CONCLUYENTE] {resumen}", ["usr.root_path"])
    ev.observaciones.append("Validación manual: ejecutar como root, de forma interactiva y con autorización, "
                            "'sudo -Hiu root env | grep ^PATH' y verificar las 6 condiciones del CIS 5.4.2.5. La auditoría no lo "
                            "hace porque ejecutaría los archivos de inicio de root (solo lectura, decisión R6).")
    if otros:
        ev.observaciones.append("Rutas mencionadas en líneas de los archivos de inicio que contienen PATH, con defectos (no son "
                                "necesariamente entradas del PATH; no concluyente): " + "; ".join(otros))
    return ev


# Shells que terminan de inmediato y no pueden abrir una sesión interactiva aunque figuren en /etc/shells (SLES 15 incluye
# /bin/false y /bin/true). El procedimiento literal del CIS 7.2.8/7.2.9 solo descarta "nologin"; este criterio técnico
# (ajuste 2026-10-27) evita tratar cuentas de servicio como rtkit (home /proc) como usuarios interactivos. No depende del
# UID: una cuenta de UID bajo con /bin/bash sigue siendo interactiva.
SHELLS_NO_INTERACTIVAS = ("nologin", "false", "true")


def shell_interactiva(shell: str, validas) -> bool:
    return shell in validas and shell.rsplit("/", 1)[-1] not in SHELLS_NO_INTERACTIVAS


def homes(ctx: Ctx, conjunto: str = "cis") -> List[List[str]]:
    """Filas usuario|home|shell|propietario|modo[|grupo] de usuarios interactivos. conjunto: 'cis' (shells válidas de
    /etc/shells que permiten sesión interactiva) o 'infra' (/bin/bash, /bin/sh)."""
    validas = {l.strip() for l in ctx.lineas("sys.shells") if l.strip().startswith("/") and not l.strip().endswith("nologin")}
    filas = [l.split("|") for l in ctx.lineas("usr.homes") if l.count("|") >= 3]
    if conjunto == "infra":
        return [f for f in filas if f[2] in ("/bin/bash", "/bin/sh", "/usr/bin/bash", "/usr/bin/sh")]
    if conjunto == "todas":
        return filas
    return [f for f in filas if shell_interactiva(f[2], validas)]


def no_interactivas_en_shells(ctx: Ctx) -> List[List[str]]:
    """Cuentas cuya shell figura en /etc/shells pero no permite sesión interactiva (excluidas de 7.2.8/7.2.9)."""
    validas = {l.strip() for l in ctx.lineas("sys.shells") if l.strip().startswith("/") and not l.strip().endswith("nologin")}
    return [f for f in homes(ctx, "todas") if f[2] in validas and not shell_interactiva(f[2], validas)]


def universo_exclusion(descripcion: str, fuente: str, excluido_si: str, usuarios: Dict[str, Dict], existentes) -> Dict:
    """Mismo formato que controles_p1.universo (validación de exclusiones por usuario)."""
    return {"descripcion": descripcion, "fuente": fuente, "excluido_si": excluido_si, "usuarios": usuarios,
            "existentes": existentes, "limitaciones": []}


def _uids(ctx: Ctx) -> Dict[str, str]:
    return {x[0]: x[2] for x in (l.split(":") for l in ctx.lineas("usr.passwd")) if len(x) >= 6}


def ev_homes(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("usr.homes")
    if prob:
        ev.add("homes", "Directorios home", ERROR, "", prob, ["usr.homes"]); return ev
    f = homes(ctx)
    sin_perm = [x[0] for x in f if x[3] == "SIN_PERMISO"]
    if sin_perm:
        ev.add("permisos", "Acceso a los home", ERROR, "", f"[PERMISOS_INSUFICIENTES] sin permiso para verificar: {sin_perm}", ["usr.homes"])
    f = [x for x in f if x[3] != "SIN_PERMISO"]
    no_ex = [x[0] for x in f if x[3] == "NO_EXISTE"]
    prop = [f"{x[0]} ({x[3]})" for x in f if x[3] != "NO_EXISTE" and x[3] != x[0]]
    modo = [f"{x[0]} ({x[4]})" for x in f if x[3] != "NO_EXISTE" and x[4] and not modo_ok(x[4], 0o027)]
    c_ex = ev.add("existe", "El home de cada usuario interactivo existe", FALLA if no_ex else OK, "existe", f"no existen: {no_ex}" if no_ex else f"{len(f)} usuarios", ["usr.homes"], subcondicion="existe")
    c_pr = ev.add("propietario", "Cada usuario es propietario de su home", FALLA if prop else OK, "propietario = usuario", f"{prop}" if prop else "correcto", ["usr.homes"], subcondicion="propietario")
    c_mo = ev.add("modo", "Home con modo 0750 o más restrictivo", FALLA if modo else OK, "<= 0750", f"{modo}" if modo else "correcto", ["usr.homes"], subcondicion="modo")
    # Elementos por usuario: permiten aplicar una excepción de Infraestructura usuario por usuario (capa de resultado final).
    c_ex.elementos = [{"usuario": x[0], "valor": f"home {x[1]} no existe"} for x in f if x[3] == "NO_EXISTE"]
    c_pr.elementos = [{"usuario": x[0], "valor": f"home {x[1]} de {x[3]}"} for x in f if x[3] != "NO_EXISTE" and x[3] != x[0]]
    c_mo.elementos = [{"usuario": x[0], "valor": f"home {x[1]} modo {x[4]}"} for x in f
                      if x[3] != "NO_EXISTE" and x[4] and not modo_ok(x[4], 0o027)]
    fallos = {}
    for c in (c_ex, c_pr, c_mo):
        for e in c.elementos:
            fallos.setdefault(e["usuario"], []).append(e["valor"])
    validas = {l.strip() for l in ctx.lineas("sys.shells") if l.strip().startswith("/") and not l.strip().endswith("nologin")}
    uni = {}
    for x in homes(ctx, "todas"):
        if not shell_interactiva(x[2], validas):
            uni[x[0]] = {"estado": "FUERA_POBLACION", "detalle": f"shell {x[2]}: no es un usuario interactivo"}
        elif x[0] in fallos:
            uni[x[0]] = {"estado": "INCUMPLE", "detalle": "; ".join(fallos[x[0]]) + " (fuera de lo exigido por el CIS)"}
        else:
            uni[x[0]] = {"estado": "CUMPLE", "detalle": f"home {x[1]} existe, propietario {x[3]}, modo {x[4]}"}
    pw = [l.split(":", 1)[0] for l in ctx.lineas("usr.passwd") if ":" in l]
    ev.mecanismo["universo_exclusion"] = universo_exclusion(
        "Directorio home por usuario interactivo", "usr.homes (stat del home) + /etc/shells",
        "su home conserva una configuración fuera del CIS (no existe, otro propietario o modo > 0750)", uni, sorted(pw) or None)
    uid, umin = _uids(ctx), ctx.uid_min
    excl = no_interactivas_en_shells(ctx)

    def _fila(x, interactiva):
        g = x[5] if len(x) > 5 and x[5] else "no recolectado"
        u = _num_uid(uid.get(x[0]))
        tipo = "interactivo" if interactiva else "cuenta de servicio (no interactiva)"
        cond = f"{tipo}, UID {u} {'<' if u < umin else '>='} UID_MIN {umin}" if u is not None else tipo
        if not interactiva:
            return (f"{x[0]} (UID {uid.get(x[0], '?')}, shell {x[2]}, home {x[1]}, propietario {x[3]}, grupo {g}, modo {x[4] or '—'}; {cond}; "
                    f"excluida: la shell {x[2]} figura en /etc/shells pero no permite sesión interactiva)")
        existe = x[3] != "NO_EXISTE"
        checks = [("existe", existe), ("propietario", existe and x[3] == x[0]),
                  ("modo <= 0750", existe and bool(x[4]) and modo_ok(x[4], 0o027))]
        return (f"{x[0]} (UID {uid.get(x[0], '?')}, shell {x[2]}, home {x[1]}, propietario {x[3]}, grupo {g}, modo {x[4] or '—'}; {cond}; "
                "incluido: shell interactiva; " + ", ".join(f"{k} {'✔' if v else '✘'}" for k, v in checks) + ")")
    ev.add("usuarios", "Usuarios considerados (criterio de usuario interactivo)", INFO, "usuarios con shell interactiva de /etc/shells",
           "; ".join([_fila(x, True) for x in f] + [_fila(x, False) for x in excl]) or "ninguno", ["usr.homes", "sys.shells", "usr.passwd"])
    if excl:
        ev.observaciones.append(
            "Criterio de usuario interactivo: el procedimiento literal del CIS toma toda shell de /etc/shells salvo 'nologin'. Las "
            "shells /bin/false y /bin/true terminan de inmediato y no abren sesión interactiva, por lo que estas cuentas de servicio "
            "no se evalúan como usuarios interactivos: " + ", ".join(f"{x[0]} ({x[2]}, home {x[1]})" for x in excl)
            + ". El criterio no depende del UID: una cuenta de UID bajo con shell interactiva sí se evalúa.")
    if ctx.sin_sudo("usr.homes"):
        ev.observaciones.append("Consulta sin sudo -n: test -d/stat solo requieren permiso de búsqueda en el directorio padre; "
                                "los homes no accesibles se informan como SIN_PERMISO (ERROR DE AUDITORÍA).")
    return ev


def ev_dotfiles(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    completo = not ctx.omitido("usr.dotfiles_completo") and not ctx.problema("usr.dotfiles_completo")
    cid = "usr.dotfiles_completo" if completo else "usr.dotfiles_raiz"
    prob = ctx.problema(cid)
    if prob:
        ev.add("dotfiles", "Archivos ocultos", ERROR, "", prob, [cid]); return ev
    validos = {x[0] for x in homes(ctx)}
    gen, netrc, fwd, rh = [], [], [], []
    elems_gen = []  # archivo por archivo (usuario, modo actual y esperado) para el tratamiento REQUIERE AJUSTE MANUAL
    for l in ctx.lineas(cid):
        p = l.split("|", 5)
        if len(p) != 6 or p[0] not in validos:
            continue
        u, g, modo, ow, gw, ruta = p
        b = ruta.rsplit("/", 1)[-1]
        if b in (".forward",):
            fwd.append(ruta); continue
        if b in (".rhost", ".rhosts"):
            rh.append(ruta); continue
        mascara = 0o177 if b in (".netrc", ".bash_history") else 0o133
        mot = []
        if not modo_ok(modo, mascara):
            mot.append(f"modo {modo}>{max_modo(mascara)}")
        if ow != u:
            mot.append(f"propietario {ow}")
        if gw != g:
            mot.append(f"grupo {gw}")
        if mot:
            (netrc if b == ".netrc" else gen).append(f"{ruta}: {', '.join(mot)}")
            if b != ".netrc":
                elems_gen.append({"usuario": u, "archivo": ruta, "modo": modo, "modo_esperado": max_modo(mascara),
                                  "propietario": ow, "grupo": gw, "solo_modo": ow == u and gw == g})
    alcance = "árbol completo del home" if completo else "solo la raíz del home; recorrido completo pendiente de ejecución autorizada"
    for sub, lst, desc, esperado in (
            ("permisos", gen, "Archivos ocultos <= 0644 (.bash_history <= 0600), propietario y grupo primario del usuario", "conformes"),
            ("netrc", netrc, ".netrc con modo 0600 o más restrictivo", "<= 0600"),
            ("forward", fwd, "No existen archivos .forward", "no existen"),
            ("rhosts", rh, "No existen archivos .rhost/.rhosts", "no existen")):
        if lst:
            est = FALLA
        elif completo:
            est = OK
        else:
            est = MANUAL
        pref = "[PENDIENTE_AUDITORIA_PROFUNDA] " if est == MANUAL else ""
        c = ev.add(sub, desc, est, esperado, pref + (f"{len(lst)}: {lst[:20]}" if lst else "ninguno") + f" [{alcance}]", [cid], subcondicion=sub)
        if sub == "permisos" and lst:
            ev.mecanismo["dotfiles_permisos"] = elems_gen
    if ctx.sin_sudo(cid):
        ev.observaciones.append("Recorrido sin sudo: homes no legibles para el usuario de auditoría no fueron inspeccionados.")
    if completo:
        aplicar_completitud(ev, fs_completitud(ctx, "dot"), cid, "Recorrido de dotfiles")
    return ev


# ===================================================================== registro

_T = lambda *t: tuple(t)  # noqa: E731
_RV = "Lectura de reglas en /etc/audit/rules.d (sudo -n grep) y cargadas (sudo -n auditctl -l); comparación por componentes"
_RE = "Reglas en disco y cargadas (texto completo, sin secretos)"


def _ctl_reglas(i, titulo, clave, desc_regla):
    return Control(
        i, _T(titulo), ("sys.uid_min", "auditd.reglas_disco", "auditd.reglas_cargadas") + (("auditd.kmod_symlinks",) if clave == "kernel_modules" else ()),
        _RV, _RE,
        f"Todas las reglas esperadas ({desc_regla}) existen en rules.d Y en las reglas cargadas, con key; auid>=UID_MIN del host.",
        "Falta al menos una regla esperada en disco o en ejecución (si falta solo en uno de los dos: CUMPLE PARCIALMENTE).",
        _ev_reglas(clave))


_SIN_PARCIAL = {"actualizaciones", "unconfined", "apparmor_no_deshabilitado", "apparmor_enforce", "auditd_servicio",
                "journald_servicio", "journal_upload_auth", "journal_upload_activo", "journal_remote_no_usado",
                "rsyslog_servicio", "world_writable", "root_path", "gpg_keys",
                # requisitos únicos (componentes de apoyo no convierten una falla en desviación parcial)
                "journald_a_rsyslog", "journald_acceso", "rsyslog_filecreatemode", "rsyslog_logging", "rsyslog_remoto",
                "logrotate", "logs_acceso", "un_solo_logging"}

CONTROLES: List[Control] = [
    Control("repos", _T("Ensure package manager repositories are configured"),
            ("repo.yum_repos", "repo.dnf_repolist", "repo.zypp_repos", "repo.zypper_lr"),
            "Listado de repositorios definidos (grep de claves de interés) y habilitados (dnf -C repolist / zypper --no-refresh lr)",
            "Ids, nombre, enabled, gpgcheck, gpgkey y URL (redactada) por repositorio",
            "Todos los repositorios definidos están en la lista aprobada (config/listas_aprobadas.yml).",
            "Existe un repositorio no aprobado. Sin lista aprobada: REQUIERE EVIDENCIA ORGANIZACIONAL.",
            ev_repos),
    Control("gpg_keys", _T("Ensure GPG keys are configured"), ("repo.gpg_keys", "repo.pki_rpm_gpg", "repo.yum_repos"),
            "rpm -q gpg-pubkey (id, fecha, emisor); en RHEL, gpgkey por repositorio",
            "Claves gpg-pubkey instaladas y gpgkey de cada repo",
            "(RHEL/OL) cada repo habilitado declara gpgkey Y todas las claves están en la lista aprobada.",
            "Repo habilitado sin gpgkey, o clave no aprobada. Sin lista aprobada: REQUIERE EVIDENCIA ORGANIZACIONAL.",
            ev_gpg_keys),
    Control("gpgcheck", _T("Ensure gpgcheck is configured", "Ensure gpgcheck is globally activated"),
            ("repo.dnf_conf", "repo.yum_repos", "repo.zypp_conf", "repo.zypp_repos"),
            "grep de gpgcheck en dnf.conf/zypp.conf y en cada archivo de repositorio",
            "Líneas gpgcheck globales y por repositorio",
            "RHEL/OL: gpgcheck=1|true|yes en /etc/dnf/dnf.conf y ningún repo con gpgcheck=0|false|no|valor inválido. SLES: ninguna desactivación en zypp.conf ni en repos.d.",
            "gpgcheck global ausente/deshabilitado (RHEL/OL) o algún repo/zypp.conf lo deshabilita.",
            ev_gpgcheck),
    Control("actualizaciones", _T("Ensure updates, patches, and additional security software are installed"),
            ("upd.dnf_check", "upd.dnf_cache_edad", "upd.needs_restarting", "upd.zypper_updates", "upd.zypper_patches", "upd.zypp_cache_edad"),
            "dnf -C check-update + dnf -C needs-restarting -r (RHEL/OL); zypper --no-refresh list-updates/list-patches (SLES). Sin red.",
            "Paquetes/parches pendientes, código de retorno, antigüedad de la caché",
            "0 actualizaciones/parches pendientes con caché <= 7 días Y (RHEL/OL) needs-restarting -r rc=0.",
            "Hay actualizaciones o parches pendientes, o se requiere reinicio. Caché > 7 días: REQUIERE VALIDACIÓN MANUAL.",
            ev_actualizaciones),
    Control("aide_instalado", _T("Ensure AIDE is installed"), ("pkg.rpm",), "rpm -q aide", "Versión instalada",
            "aide instalado.", "aide no instalado.", ev_aide_inst),
    Control("aide_programado", _T("Ensure filesystem integrity is regularly checked"), ("aide.cron", "aide.timer"),
            "grep de aide --check en cron del sistema y spool (sudo -n) y estado de aidecheck.service/timer. Nunca ejecuta AIDE.",
            "Líneas cron con aide y estado del timer",
            "Existe tarea cron con 'aide --check|--update|$AIDEARGS' O aidecheck.service y aidecheck.timer habilitados y timer activo.",
            "Ni cron ni timer de AIDE.", ev_aide_prog),
    Control("grub_password", _T("Ensure bootloader password is set"), ("grub.password",),
            "sudo -n find/awk en /boot: GRUB2_PASSWORD en user.cfg (RHEL/OL) o 'set superusers' + password_pbkdf2 en grub.cfg (SLES)",
            "Prefijo del hash (algoritmo) y superusuario; el hash se imprime como <REDACTADO>",
            "RHEL/OL: GRUB2_PASSWORD=grub.pbkdf2.sha512 en user.cfg. SLES: set superusers y password_pbkdf2 en grub.cfg.",
            "No se encuentra contraseña de GRUB.", ev_grub_pass),
    Control("grub_acceso", _T("Ensure access to bootloader config is configured"), ("grub.archivos",),
            "sudo -n find /boot -name 'grub*' -o -name user.cfg | stat", "Ruta, modo, propietario y grupo de cada archivo",
            "Cada archivo: /boot/efi/EFI/* modo <= 0700; resto <= 0600; propietario root; grupo root.",
            "Algún archivo con modo más permisivo, propietario o grupo distinto de root.", ev_grub_acceso),
    Control("aslr", _T("Ensure address space layout randomization is enabled", "Ensure kernel.randomize_va_space is configured"),
            ("sysctl.runtime", "sysctl.archivos"), "sysctl en ejecución + systemd-sysctl --cat-config (orden de precedencia)",
            "Valor runtime y valores por archivo",
            "kernel.randomize_va_space = 2 en ejecución Y en archivos cargados por systemd-sysctl (RHEL/OL 8: valor efectivo; RHEL/OL 9 y SLES: todas las ocurrencias).",
            "Valor runtime distinto de 2, no persistido, o alguna ocurrencia/valor efectivo distinto de 2.", ev_aslr),
    Control("core_backtraces", _T("Ensure core dump backtraces are disabled", "Ensure systemd-coredump ProcessSizeMax is configured"),
            ("coredump.conf",), "systemd-analyze cat-config systemd/coredump.conf", "Valor efectivo y archivo de origen",
            "ProcessSizeMax=0 efectivo en [Coredump] (RHEL/OL 8 acepta el valor por defecto si fuese 0).",
            "ProcessSizeMax distinto de 0 o no definido.", ev_core_backtraces, familias=(RHEL,)),
    Control("core_storage", _T("Ensure core dump storage is disabled", "Ensure systemd-coredump Storage is configured"),
            ("coredump.conf",), "systemd-analyze cat-config systemd/coredump.conf", "Valor efectivo y archivo de origen",
            "Storage=none efectivo en [Coredump].", "Storage distinto de none o no definido.", ev_core_storage, familias=(RHEL,)),
    Control("core_restringido", _T("Ensure core dumps are restricted"),
            ("coredump.limits", "sysctl.runtime", "sysctl.archivos", "coredump.servicio"),
            "grep '* hard core' en limits; sysctl fs.suid_dumpable runtime y persistente", "Líneas de limits y valores sysctl",
            "'* hard core 0' Y fs.suid_dumpable=0 en ejecución y en archivos sysctl.",
            "Falta el límite de core o fs.suid_dumpable distinto de 0.", ev_core_restringido, familias=(SLES,)),
    Control("unconfined", _T("Ensure no unconfined services exist"), ("sys.selinux", "selinux.unconfined", "priv.unconfined_detalle"),
            "ps -eZ | grep unconfined_service_t", "Procesos con contexto unconfined_service_t",
            "Sin procesos unconfined_service_t (con SELinux habilitado).",
            "Existe al menos un proceso unconfined_service_t. SELinux Disabled: REQUIERE VALIDACIÓN MANUAL.",
            ev_unconfined, familias=(RHEL,)),
    Control("setroubleshoot", _T("Ensure SETroubleshoot is not installed"), ("pkg.rpm",), "rpm -q setroubleshoot", "Estado del paquete",
            "setroubleshoot no instalado.", "setroubleshoot instalado.", ev_setroubleshoot, familias=(RHEL,)),
    Control("apparmor_instalado", _T("Ensure AppArmor is installed"), ("pkg.rpm",),
            "rpm -q apparmor-parser apparmor-profiles apparmor-utils libapparmor1", "Estado de paquetes",
            "Los cuatro paquetes instalados.", "Falta alguno.", ev_aa_inst, familias=(SLES,)),
    Control("apparmor_boot", _T("Ensure AppArmor is enabled in the bootloader configuration"), ("grub.cfg_linux",),
            "sudo -n grep '^\\s*linux' /boot/grub2/grub.cfg", "Líneas linux",
            "Todas las líneas linux contienen apparmor=1 y security=apparmor.", "Alguna línea sin esos parámetros.",
            ev_aa_boot, familias=(SLES,)),
    Control("apparmor_no_deshabilitado", _T("Ensure all AppArmor Profiles are not disabled"), ("apparmor.status",),
            "sudo -n apparmor_status", "Conteos de perfiles y procesos",
            "Perfiles cargados > 0, todos en enforce o complain, 0 procesos sin confinar con perfil.",
            "Sin perfiles, perfiles fuera de enforce/complain o procesos sin confinar.",
            lambda c: ev_aa_no_deshab(c, False), familias=(SLES,)),
    Control("apparmor_enforce", _T("Ensure all AppArmor Profiles are enforcing"), ("apparmor.status",),
            "sudo -n apparmor_status", "Conteos de perfiles y procesos",
            "Perfiles cargados > 0, 0 en complain (enforce = cargados), 0 procesos sin confinar con perfil.",
            "Algún perfil en complain o procesos sin confinar.", lambda c: ev_aa_no_deshab(c, True), familias=(SLES,)),
    Control("gdm_removido", _T("Ensure GNOME Display Manager is removed"), ("pkg.rpm", "gdm.grupos_dnf"),
            "rpm -q gdm; RHEL/OL 8: dnf -C group list --installed (Server with GUI)", "Estado del paquete y grupos de entorno",
            "gdm no instalado (RHEL/OL 8: además, grupo 'Server with GUI' no instalado).",
            "gdm instalado o grupo 'Server with GUI' instalado.", ev_gdm_removido, familias=(RHEL,)),
    Control("gdm_disable_user_list", _T("Ensure GDM disable-user-list is configured", "Ensure GDM disable-user-list option is enabled"),
            ("pkg.rpm", "gdm.dconf"), "rpm -q gdm; grep en /etc/dconf/db y perfiles dconf",
            "Claves dconf y perfil", "GDM no instalado: NO APLICA. Instalado: disable-user-list=true en la base dconf y perfil con system-db.",
            "GDM instalado sin disable-user-list=true o sin perfil system-db.", ev_gdm_userlist, familias=(RHEL,)),
    Control("gdm_banner", _T("Ensure GDM login banner is configured"), ("pkg.rpm", "gdm.dconf"),
            "rpm -q gdm; grep banner-message-* en /etc/dconf/db", "Claves dconf",
            "GDM no instalado: NO APLICA. Instalado: banner-message-enable=true y banner-message-text definido.",
            "GDM instalado sin banner habilitado o sin texto.", ev_gdm_banner),
    Control("crypto_no_legacy", _T("Ensure system wide crypto policy is not set to legacy"), ("crypto.politica",),
            "cat /etc/crypto-policies/config; update-crypto-policies --show", "Política configurada y efectiva",
            "Ninguna línea LEGACY en /etc/crypto-policies/config.", "La política es LEGACY.", ev_crypto),
    Control("auditd_paquetes", _T("Ensure auditd packages are installed"), ("pkg.rpm",), "rpm -q audit audit-libs",
            "Versiones", "audit y audit-libs instalados.", "Falta alguno.", ev_auditd_pkgs),
    Control("auditd_servicio", _T("Ensure auditd service is enabled and active"), ("auditd.servicio",),
            "systemctl is-enabled/is-active auditd", "Estados", "enabled y active.", "No enabled o no active.", ev_auditd_svc),
    Control("audit_arranque", _T("Ensure auditing for processes that start prior to auditd is enabled"),
            ("grub.grubby", "grub.default"), "sudo -n grubby --info=ALL (RHEL/OL) + GRUB_CMDLINE_LINUX en /etc/default/grub",
            "Argumentos de cada entrada y línea de /etc/default/grub",
            "audit=1 en todas las entradas de arranque (RHEL/OL) Y en GRUB_CMDLINE_LINUX (todas las familias).",
            "Falta audit=1 en alguna entrada o en /etc/default/grub.", ev_audit_boot),
    Control("audit_backlog", _T("Ensure audit_backlog_limit is configured", "Ensure audit_backlog_limit is sufficient"),
            ("grub.grubby", "grub.default"), "Igual que audit=1 para audit_backlog_limit", "Valor por entrada",
            "audit_backlog_limit >= 8192 en todas las entradas (RHEL/OL) y en GRUB_CMDLINE_LINUX.",
            "Ausente o menor que 8192.", ev_audit_backlog),
    Control("audit_max_log_file", _T("Ensure audit log storage size is configured"), ("auditd.conf",),
            "sudo -n grep en /etc/audit/auditd.conf", "max_log_file",
            "max_log_file = valor de la política del sitio. El Benchmark remite a la política del sitio; se usa el valor de Infraestructura (200).",
            "max_log_file ausente o distinto del valor de la política del sitio.", ev_audit_max_log, politica_sitio=True),
    Control("audit_keep_logs", _T("Ensure audit logs are not automatically deleted"), ("auditd.conf",),
            "sudo -n grep en /etc/audit/auditd.conf", "max_log_file_action",
            "max_log_file_action = keep_logs (requisito CIS; la exclusión 'rotate' no lo modifica).",
            "Cualquier otro valor (incluido rotate).", ev_audit_keep_logs),
    Control("audit_disco_lleno", _T("Ensure system is disabled when audit logs are full"), ("auditd.conf",),
            "sudo -n grep en /etc/audit/auditd.conf", "disk_full_action y disk_error_action",
            "disk_full_action = halt|single Y disk_error_action = syslog|single|halt.",
            "Ambos fuera de rango: NO CUMPLE; solo uno: CUMPLE PARCIALMENTE.", ev_audit_disk_full),
    _ctl_reglas("audit_sudoers", "Ensure changes to system administration scope (sudoers) is collected", "scope", "-w /etc/sudoers y /etc/sudoers.d -p wa"),
    _ctl_reglas("audit_user_emulation", "Ensure actions as another user are always logged", "user_emulation", "execve b64/b32 con euid!=uid y auid!=unset"),
    Control("audit_sudo_log", _T("Ensure events that modify the sudo log file are collected"),
            ("auditd.sudo_logfile", "auditd.reglas_disco", "auditd.reglas_cargadas"), _RV + "; logfile de sudoers", _RE,
            "sudo tiene 'Defaults logfile=<ruta>' y existe '-w <ruta> -p wa' con key en disco y cargada.",
            "Sin logfile de sudo o sin regla para él.", ev_sudo_log),
    _ctl_reglas("audit_fecha_hora", "Ensure events that modify date and time information are collected", "time", "adjtimex/settimeofday, clock_settime a0=0x0, -w /etc/localtime"),
    _ctl_reglas("audit_red", "Ensure events that modify the system's network environment are collected", "network", "sethostname/setdomainname y watches de red"),
    Control("audit_privilegiados", _T("Ensure use of privileged commands are collected"),
            ("sys.fs_nodev", "fs.suid_sgid", "auditd.reglas_disco", "auditd.reglas_cargadas"),
            "Recorrido SUID/SGID (profundo) y búsqueda de cada ruta en reglas en disco y cargadas",
            "Lista de binarios y presencia en reglas",
            "Cada binario SUID/SGID de filesystems locales sin noexec/nosuid aparece en las reglas en disco y cargadas.",
            "Algún binario sin regla. Recorrido no autorizado: REQUIERE VALIDACIÓN MANUAL.", ev_privilegiados),
    _ctl_reglas("audit_acceso_fallido", "Ensure unsuccessful file access attempts are collected", "access", "creat/open/openat/truncate/ftruncate con EACCES/EPERM"),
    _ctl_reglas("audit_identidad", "Ensure events that modify user/group information are collected", "identity", "watches de passwd/group/shadow/gshadow/opasswd/nsswitch/pam"),
    _ctl_reglas("audit_perm_mod", "Ensure discretionary access control permission modification events are collected", "perm_mod", "chmod/chown/xattr b64/b32"),
    _ctl_reglas("audit_montajes", "Ensure successful file system mounts are collected", "mounts", "mount b64/b32"),
    _ctl_reglas("audit_sesion", "Ensure session initiation information is collected", "session", "utmp/wtmp/btmp"),
    _ctl_reglas("audit_logins", "Ensure login and logout events are collected", "logins", "lastlog/faillock"),
    _ctl_reglas("audit_borrado", "Ensure file deletion events by users are collected", "delete", "unlink/unlinkat/rename/renameat"),
    _ctl_reglas("audit_mac", "Ensure events that modify the system's Mandatory Access Controls are collected", "mac", "SELinux (RHEL/OL) o AppArmor (SLES)"),
    _ctl_reglas("audit_chcon", "Ensure successful and unsuccessful attempts to use the chcon command are collected", "chcon", "path=/usr/bin/chcon perm=x"),
    _ctl_reglas("audit_setfacl", "Ensure successful and unsuccessful attempts to use the setfacl command are collected", "setfacl", "path=/usr/bin/setfacl perm=x"),
    _ctl_reglas("audit_chacl", "Ensure successful and unsuccessful attempts to use the chacl command are collected", "chacl", "path=/usr/bin/chacl perm=x"),
    _ctl_reglas("audit_usermod", "Ensure successful and unsuccessful attempts to use the usermod command are collected", "usermod", "path=/usr/sbin/usermod perm=x"),
    _ctl_reglas("audit_modulos", "Ensure kernel module loading unloading and modification is collected", "kernel_modules", "init/finit/delete/create/query_module y kmod; enlaces a kmod"),
    Control("audit_inmutable", _T("Ensure the audit configuration is immutable"), ("auditd.reglas_disco", "auditd.estado"),
            "grep -e en rules.d; auditctl -s (informativo)", "Directivas -e",
            "Existe '-e 2' en /etc/audit/rules.d/*.rules.", "No existe '-e 2'.", ev_audit_inmutable),
    Control("audit_disco_vs_ejecucion", _T("Ensure the running and on disk configuration is the same"),
            ("auditd.reglas_disco", "auditd.audit_rules"), "Comparación normalizada rules.d vs /etc/audit/audit.rules (sin augenrules)",
            "Diferencias entre ambos conjuntos", "Sin diferencias (equivalente a 'augenrules --check: No change').",
            "Reglas presentes solo en uno de los dos.", ev_audit_consistente),
    Control("journald_servicio", _T("Ensure journald service is active", "Ensure journald service is enabled and active"),
            ("journald.servicio",), "systemctl is-enabled/is-active systemd-journald", "Estados",
            "active (RHEL/OL 9 y SLES además is-enabled = static).", "No active (o is-enabled distinto de static en 9/SLES).", ev_journald_svc),
    Control("un_solo_logging", _T("Ensure only one logging system is in use"), ("rsyslog.servicio", "journald.servicio"),
            "systemctl is-active rsyslog / systemd-journald (script del Audit CIS)", "Sistema de logging en uso",
            "rsyslog activo (o, si no, journald activo) y coincide con el método corporativo (metodo_logging).",
            "Ningún sistema de logging determinable, o el sistema en uso difiere del método corporativo.", ev_un_solo_logging),
    Control("journald_acceso", _T("Ensure journald log file access is configured"), ("journald.tmpfiles", "priv.journal_archivos"),
            "Lectura de /etc/tmpfiles.d/systemd.conf o /usr/lib/tmpfiles.d/systemd.conf", "Entradas con permisos",
            "Override o archivo por defecto: los archivos de journal quedan con 0640 o más restrictivo (modo efectivo de cada regla).",
            "Alguna regla (o un journal real) más permisiva que 0640.", ev_journald_acceso),
    Control("journald_rotacion", _T("Ensure journald log file rotation is configured"), ("journald.conf",),
            "systemd-analyze cat-config systemd/journald.conf", "SystemMaxUse, SystemKeepFree, RuntimeMaxUse, RuntimeKeepFree, MaxFileSec efectivos",
            "Valores efectivos = política del sitio (definida por Infraestructura: 4G/1G/200M/50M/1month).",
            "Algún valor ausente o distinto.", ev_journald_rotacion, politica_sitio=True),
    Control("journald_no_reenvia", _T("Ensure journald ForwardToSyslog is disabled"), ("journald.conf", "rsyslog.servicio", "journald.remoto", "rsyslog.conf"),
            "systemd-analyze cat-config systemd/journald.conf", "ForwardToSyslog efectivo y mecanismo de logging en uso",
            "ForwardToSyslog=no efectivo (aplica '- IF - journald es el método').", "ForwardToSyslog distinto de no.",
            ev_journald_fwd_no, grupo_conflicto="journald_vs_rsyslog_ForwardToSyslog"),
    Control("journald_compress", _T("Ensure journald Compress is configured"), ("journald.conf",), "cat-config journald", "Compress efectivo",
            "Compress=yes efectivo.", "Distinto de yes o no definido (9/SLES exigen valor explícito).", ev_journald_compress),
    Control("journald_storage", _T("Ensure journald Storage is configured"), ("journald.conf",), "cat-config journald", "Storage efectivo",
            "Storage=persistent efectivo.", "Distinto de persistent o no definido.", ev_journald_storage),
    Control("journal_remote_instalado", _T("Ensure systemd-journal-remote is installed"), ("pkg.rpm",), "rpm -q systemd-journal-remote",
            "Estado del paquete", "Instalado.", "No instalado.", ev_jremote_inst),
    Control("journal_upload_auth", _T("Ensure systemd-journal-remote authentication is configured", "Ensure systemd-journal-upload authentication is configured"),
            ("journald.upload_conf",), "grep URL/ServerKeyFile/ServerCertificateFile/TrustedCertificateFile en journal-upload.conf",
            "Valores (rutas, URL redactada)", "Los 4 parámetros definidos y confirmados como los del entorno (confirmación externa).",
            "Falta algún parámetro.", ev_jupload_auth),
    Control("journal_upload_activo", _T("Ensure systemd-journal-upload is enabled and active"), ("journald.remoto",),
            "systemctl is-enabled/is-active systemd-journal-upload", "Estados", "enabled y active.", "No enabled o no active.", ev_jupload_activo),
    Control("journal_remote_no_usado", _T("Ensure systemd-journal-remote service is not in use"), ("journald.remoto",),
            "systemctl is-enabled/is-active de systemd-journal-remote.socket/.service", "Estados",
            "Ninguna de las dos unidades enabled ni active.", "Alguna enabled o active.", ev_jremote_no),
    Control("rsyslog_instalado", _T("Ensure rsyslog is installed"), ("pkg.rpm",), "rpm -q rsyslog", "Versión", "Instalado.", "No instalado.", ev_rsyslog_inst),
    Control("rsyslog_servicio", _T("Ensure rsyslog service is enabled and active"), ("rsyslog.servicio",), "systemctl is-enabled/is-active rsyslog",
            "Estados", "enabled y active.", "No enabled o no active.", ev_rsyslog_svc),
    Control("journald_a_rsyslog", _T("Ensure journald is configured to send logs to rsyslog"), ("journald.conf", "rsyslog.servicio", "journald.remoto", "rsyslog.conf", "pkg.rpm"),
            "systemd-analyze cat-config systemd/journald.conf", "ForwardToSyslog efectivo y mecanismo de logging en uso",
            "ForwardToSyslog=yes efectivo (aplica '- IF - rsyslog es el método').", "ForwardToSyslog distinto de yes.",
            ev_journald_a_rsyslog, grupo_conflicto="journald_vs_rsyslog_ForwardToSyslog"),
    Control("rsyslog_filecreatemode", _T("Ensure rsyslog log file creation mode is configured"), ("rsyslog.conf",),
            "Sentencias de rsyslog (legacy y RainerScript, multilínea)", "Modo efectivo de cada acción de archivo",
            "Todas las acciones de archivo con $FileCreateMode / fileCreateMode de omfile 0640 o más restrictivo (imjournal no cuenta).",
            "Alguna acción con el valor por defecto 0644 o más permisivo.", ev_rsyslog_filemode),
    Control("rsyslog_logging", _T("Ensure rsyslog logging is configured"), ("rsyslog.conf", "rsyslog.var_log", "priv.rsyslog_validacion"),
            "Lectura de reglas de rsyslog y listado de /var/log", "Reglas de selección/acción y archivos de log",
            "Reglas aprobadas (rsyslog_reglas_aprobadas) presentes y configuración válida (rsyslogd -N1).",
            "Falta una regla aprobada o hay errores de sintaxis. Sin política: REQUIERE VALIDACIÓN MANUAL.", ev_rsyslog_logging),
    Control("rsyslog_remoto", _T("Ensure rsyslog is configured to send logs to a remote log host"), ("rsyslog.conf", "rsyslog.servicio"),
            "Búsqueda de @/@@ y action(target=) en rsyslog", "Destinos remotos (protocolo, host)",
            "RHEL/OL 8: destino @@ (TCP) o action target. RHEL/OL 9 y SLES: destino @, @@ o action target.",
            "Sin destino remoto (RHEL/OL 8: solo @ UDP también falla).", ev_rsyslog_remoto),
    Control("rsyslog_no_recibe", _T("Ensure rsyslog is not configured to receive logs from a remote client"), ("rsyslog.conf",),
            "Búsqueda de imtcp (module/input/$ModLoad/$InputTCPServerRun)", "Directivas encontradas",
            "Ninguna directiva de recepción TCP.", "Existe alguna.", ev_rsyslog_no_recibe),
    Control("logrotate", _T("Ensure logrotate is configured", "Ensure rsyslog logrotate is configured"), ("logrotate.conf", "rsyslog.conf", "priv.logrotate"),
            "logrotate.conf + logrotate.d y destinos de rsyslog", "Cobertura y directivas por archivo; logrotate -d",
            "Cada archivo de rsyslog cubierto con las directivas de logrotate_rsyslog_politica y logrotate -d sin errores.",
            "Archivo no cubierto, directiva ausente o error. Sin logrotate: FAIL. Sin política: REQUIERE EVIDENCIA ORGANIZACIONAL.", ev_logrotate),
    Control("logs_acceso", _T("Ensure access to all logfiles has been configured"), ("logs.permisos", "usr.group", "usr.passwd", "sys.uid_min"),
            "sudo -n find -L /var/log (criterio de preselección del CIS) + stat", "Archivos fuera de criterio con modo/propietario/grupo",
            "Cada archivo cumple el modo/propietario/grupo de su tipo según el script del CIS.", "Algún archivo fuera de criterio.",
            ev_logs_acceso),
]

for _ruta, _t1, _t2 in (
        ("/etc/passwd", "Ensure access to /etc/passwd is configured", "Ensure permissions on /etc/passwd are configured"),
        ("/etc/passwd-", "Ensure access to /etc/passwd- is configured", "Ensure permissions on /etc/passwd- are configured"),
        ("/etc/group", "Ensure access to /etc/group is configured", "Ensure permissions on /etc/group are configured"),
        ("/etc/group-", "Ensure access to /etc/group- is configured", "Ensure permissions on /etc/group- are configured"),
        ("/etc/shadow", "Ensure access to /etc/shadow is configured", "Ensure permissions on /etc/shadow are configured"),
        ("/etc/shadow-", "Ensure access to /etc/shadow- is configured", "Ensure permissions on /etc/shadow- are configured"),
        ("/etc/gshadow", "Ensure access to /etc/gshadow is configured", "Ensure permissions on /etc/gshadow are configured"),
        ("/etc/gshadow-", "Ensure access to /etc/gshadow- is configured", "Ensure permissions on /etc/gshadow- are configured")):
    _som = "shadow" in _ruta
    CONTROLES.append(Control(
        "permisos_" + _ruta.strip("/").replace("/", "_").replace("-", "_backup"), _T(_t1, _t2), ("perm.cuentas",),
        f"stat -Lc de {_ruta}", "Modo, uid/usuario, gid/grupo",
        ("RHEL/OL: modo 0000, root:root. SLES: <= 0640, propietario root, grupo root o shadow." if _som
         else "Modo <= 0644, propietario 0/root, grupo 0/root."),
        "Modo más permisivo o propietario/grupo distinto.", _perm_archivo(_ruta), permite_parcial=False))

CONTROLES += [
    Control("world_writable", _T("Ensure world writable files and directories are secured"), ("fs.world_writable",),
            "Recorrido de filesystems locales (find -xdev -perm -0002), con nice/ionice y timeout", "Rutas encontradas",
            "0 archivos world-writable y 0 directorios world-writable sin sticky bit.",
            "Alguno encontrado. Recorrido no autorizado: REQUIERE VALIDACIÓN MANUAL.", ev_world_writable),
    Control("sin_dueno", _T("Ensure no files or directories without an owner and a group exist"), ("fs.sin_dueno",),
            "Recorrido de filesystems locales (find -nouser / -nogroup)", "Rutas encontradas",
            "0 archivos/directorios sin usuario Y 0 sin grupo (sub-requisitos según alcance).",
            "Alguno encontrado. Recorrido no autorizado: REQUIERE VALIDACIÓN MANUAL.", ev_sin_dueno,
            subcondiciones={"sin_usuario": ("Sin archivos sin usuario propietario", ("6.1.11",)),
                            "sin_grupo": ("Sin archivos sin grupo", ("6.1.12",))}),
    Control("suid_sgid", _T("Ensure SUID and SGID files are reviewed"), ("fs.suid_sgid", "priv.suid_rpm"),
            "Recorrido de filesystems sin noexec/nosuid (find -perm -4000/-2000)", "Lista de binarios SUID/SGID",
            "Todos los binarios SUID/SGID están en la lista aprobada (config/listas_aprobadas.yml).",
            "Binario no aprobado. Sin lista: REQUIERE EVIDENCIA ORGANIZACIONAL. Sin recorrido: REQUIERE VALIDACIÓN MANUAL.",
            ev_suid_sgid,
            subcondiciones={"suid": ("Revisión de ejecutables SUID", ("6.1.13",)),
                            "sgid": ("Revisión de ejecutables SGID", ("6.1.14",))}),
    Control("cuentas_shadow", _T("Ensure accounts in /etc/passwd use shadowed passwords"), ("usr.passwd",),
            "awk sobre /etc/passwd (campo 2 clasificado en el host)", "Cuentas sin 'x'", "Todas las cuentas con 'x'.", "Alguna distinta.",
            ev_shadowed, familias=(SLES,)),
    Control("shadow_vacio", _T("Ensure /etc/shadow password fields are not empty"), ("usr.shadow_vacios",),
            "sudo -n awk sobre /etc/shadow imprimiendo solo usuarios con campo vacío", "Nombres de usuario (nunca hashes)",
            "Ninguna cuenta con contraseña vacía.", "Alguna cuenta con campo vacío.", ev_shadow_vacio),
    Control("grupos_existen", _T("Ensure all groups in /etc/passwd exist in /etc/group"), ("usr.passwd", "usr.group"),
            "Comparación de GIDs", "GIDs inexistentes", "Todos los GID primarios existen.", "Alguno no existe.", ev_grupos_existen),
    Control("uid_unicos", _T("Ensure no duplicate UIDs exist"), ("usr.passwd",), "Conteo de UIDs", "Duplicados", "Sin duplicados.", "Hay duplicados.", ev_dup_uid),
    Control("gid_unicos", _T("Ensure no duplicate GIDs exist"), ("usr.group",), "Conteo de GIDs", "Duplicados", "Sin duplicados.", "Hay duplicados.", ev_dup_gid),
    Control("usuarios_unicos", _T("Ensure no duplicate user names exist"), ("usr.passwd",), "Conteo de nombres", "Duplicados", "Sin duplicados.", "Hay duplicados.", ev_dup_usuario),
    Control("grupos_unicos", _T("Ensure no duplicate group names exist"), ("usr.group",), "Conteo de nombres", "Duplicados", "Sin duplicados.", "Hay duplicados.", ev_dup_grupo),
    Control("uid0_root", _T("Ensure root is the only UID 0 account"), ("usr.passwd",), "awk UID 0", "Cuentas UID 0", "Solo root.", "Otra cuenta con UID 0.", ev_uid0),
    Control("root_path", _T("Ensure root path integrity"), ("usr.root_path", "priv.root_path_dirs"),
            "sudo -n -Hiu root env (PATH) + sudo -n stat de cada directorio", "PATH y modo/propietario de cada entrada",
            "Sin '::', ':' final ni '.'; cada entrada es directorio, propietario root, modo <= 0755.", "Alguna condición incumplida.", ev_root_path),
    Control("homes", _T("Ensure local interactive user home directories are configured"), ("sys.shells", "usr.homes"),
            "Usuarios con shell válida (/etc/shells sin nologin); sudo -n test/stat de cada home", "Existencia, propietario y modo de cada home",
            "Cada home existe, es propiedad del usuario y tiene modo <= 0750 (sub-requisitos según alcance).", "Alguna condición incumplida.",
            ev_homes,
            subcondiciones={"existe": ("El home existe", ("6.2.4",)),
                            "propietario": ("El usuario es propietario de su home", ("6.2.5",)),
                            "modo": ("Modo 0750 o más restrictivo", ("6.2.6",))}),
    Control("dotfiles", _T("Ensure local interactive user dot files access is configured"), ("usr.dotfiles_raiz", "usr.dotfiles_completo", "usr.homes", "sys.shells"),
            "Listado de archivos ocultos (nombre, modo, propietario) en los homes; recorrido completo solo si está autorizado",
            "Nombre, modo, propietario y grupo; nunca el contenido",
            "Archivos ocultos <= 0644 (.bash_history <= 0600), del usuario y su grupo primario; .netrc <= 0600; sin .forward ni .rhost(s) (sub-requisitos según alcance).",
            "Alguna condición incumplida (una falla en la raíz del home es concluyente). Sin fallas y sin recorrido completo: REQUIERE VALIDACIÓN MANUAL.",
            ev_dotfiles,
            subcondiciones={"permisos": ("Permisos/propiedad de archivos ocultos", ("6.2.7",)),
                            "netrc": (".netrc", ("6.2.8",)),
                            "forward": (".forward", ("6.2.9",)),
                            "rhosts": (".rhosts", ("6.2.10",))}),
]

_A9 = ("RHEL9", "ORACLE9", "SLES15")
_ADAPTACIONES = {
    "root_path": dict(
        procedimiento_original="l_root_path=\"$(sudo -Hiu root env | grep '^PATH' | cut -d= -f2)\" y verificación de cada entrada",
        comportamiento_esperado="PATH de root sin '::', ':' final ni '.'; cada entrada es un directorio de root con modo 0755 o más restrictivo",
        adaptacion="Análisis estático de los archivos de inicio de root (/etc/profile, profile.d, bashrc, ~/.bash_profile, ~/.bashrc, ...): "
                   "solo líneas con PATH y estructura; nunca se ejecutan ni se hace source. Si el PATH final no es determinable "
                   "(condicionales, funciones, inclusiones dinámicas, sustituciones) -> REQUIERE VALIDACIÓN MANUAL con el detalle",
        justificacion="'sudo -Hiu root' ejecuta como root el código de los archivos de inicio, con posibles efectos secundarios; "
                      "contradice la naturaleza READ-ONLY de la auditoría (decisión R6 del usuario, 2026-10-08)."),
    "aide_programado": dict(
        procedimiento_original="grep -Ers '<regex aide>' /etc/cron.* /etc/crontab /var/spool/cron/",
        comportamiento_esperado="Existe una tarea programada que ejecuta aide --check/--update",
        adaptacion="Misma expresión con grep -Er (sin -s) sobre los directorios cron explícitos",
        justificacion="La opción -s suprime los errores de lectura: un spool no legible se confundiría con 'no existe cron'."),
    "audit_disco_vs_ejecucion": dict(
        procedimiento_original="augenrules --check (salida esperada: '/usr/sbin/augenrules: No change')",
        comportamiento_esperado="Todas las reglas de /etc/audit/rules.d están fusionadas en /etc/audit/audit.rules",
        adaptacion="Lectura con sudo -n grep de rules.d y de audit.rules y comparación de los conjuntos de reglas normalizadas",
        justificacion="augenrules crea un archivo temporal en el host (mktemp); la regla read-only lo impide. Se obtiene la misma evidencia por lectura; el requisito no cambia."),
    "gdm_removido": dict(benchmarks=("RHEL8", "ORACLE8"),
        procedimiento_original="dnf grouplist | sed -n '/Installed Environment Groups:/,/Installed Groups:/p' (no debe figurar 'Server with GUI')",
        comportamiento_esperado="El grupo de entorno 'Server with GUI' no está instalado",
        adaptacion="dnf -C -q group list --installed (solo caché local) y rpm -q gdm",
        justificacion="dnf sin -C puede refrescar metadatos de repositorios (prohibido). Si la caché no está disponible el componente queda en revisión manual."),
    "actualizaciones": dict(
        procedimiento_original="dnf check-update y dnf needs-restarting -r (SLES: zypper list-updates y zypper list-patches)",
        comportamiento_esperado="No hay actualizaciones ni parches pendientes y no se requiere reinicio",
        adaptacion="dnf -C / zypper --no-refresh (sin red) registrando la antigüedad de la caché; sin pendientes en caché no validada -> REQUIERE VALIDACIÓN MANUAL",
        justificacion="Decisión 13: no refrescar repositorios. Las pendientes halladas son concluyentes; su ausencia no lo es con caché antigua."),
    "gdm_disable_user_list": dict(benchmarks=("RHEL8", "ORACLE8"),
        procedimiento_original="gsettings writable/get org.gnome.login-screen disable-user-list",
        comportamiento_esperado="disable-user-list = true y bloqueado a nivel de sistema",
        adaptacion="Lectura de la base dconf del sistema (/etc/dconf/db, perfiles con system-db)",
        justificacion="gsettings requiere una sesión D-Bus/gráfica del usuario; solo aplica si GDM está instalado."),
    "usuarios_unicos": dict(
        procedimiento_original="cut -f1 -d':' /etc/group | sort -n | uniq -c (script del Benchmark)",
        comportamiento_esperado="No existen nombres de usuario duplicados (título del control)",
        adaptacion="Conteo de nombres en /etc/passwd",
        justificacion="El script lee /etc/group, idéntico al de nombres de grupo duplicados (errata aparente). Se corrige la fuente de evidencia, no el requisito."),
    "dotfiles": dict(
        procedimiento_original="find \"$home\" -xdev -type f -name '.*' sobre el árbol completo; case '.forward | .rhost'",
        comportamiento_esperado="Archivos ocultos con permisos/propiedad correctos; sin .forward ni .rhosts; .netrc 0600",
        adaptacion="Se consideran '.rhost' y '.rhosts'. Mientras la auditoría profunda no esté autorizada solo se inspecciona la raíz de cada home: una falla allí es concluyente; su ausencia queda pendiente",
        justificacion="'.rhost' parece errata de '.rhosts'. El recorrido completo de homes es intensivo (decisión 12)."),
    "journald_compress": dict(benchmarks=_A9,
        procedimiento_original="systemd-analyze cat-config ... | grep -E '^Compress=yes'",
        comportamiento_esperado="Compress=yes es el valor efectivo de journald",
        adaptacion="Se toma el valor efectivo (última asignación en el orden de cat-config)",
        justificacion="El grep acepta cualquier línea aunque otra posterior la anule; se evalúa lo que journald realmente aplica."),
    "journald_storage": dict(benchmarks=_A9,
        procedimiento_original="systemd-analyze cat-config ... | grep -E '^Storage=persistent'",
        comportamiento_esperado="Storage=persistent es el valor efectivo",
        adaptacion="Valor efectivo según el orden de cat-config", justificacion="Igual que Compress."),
    "journald_no_reenvia": dict(benchmarks=_A9,
        procedimiento_original="systemd-analyze cat-config ... | grep -E '^ForwardToSyslog=no'",
        comportamiento_esperado="ForwardToSyslog=no es el valor efectivo",
        adaptacion="Valor efectivo según el orden de cat-config", justificacion="Igual que Compress."),
    "journald_a_rsyslog": dict(benchmarks=_A9,
        procedimiento_original="systemd-analyze cat-config ... | grep -E '^ForwardToSyslog=yes'",
        comportamiento_esperado="ForwardToSyslog=yes es el valor efectivo",
        adaptacion="Valor efectivo según el orden de cat-config", justificacion="Igual que Compress."),
    "world_writable": dict(
        procedimiento_original="findmnt -Dkerno fstype,target excluyendo nfs/proc/smb/vfat/iso9660/efivarfs/selinuxfs; find -xdev -perm -0002",
        comportamiento_esperado="Sin archivos world-writable ni directorios world-writable sin sticky bit en filesystems locales",
        adaptacion="Se excluyen además nfs4, cifs, fuse y sshfs (también remotos) y se ejecuta con nice/ionice/timeout",
        justificacion="Evitar recorrer almacenamiento remoto y limitar el impacto; el conjunto de filesystems locales evaluados es el mismo."),
    "sin_dueno": dict(
        procedimiento_original="findmnt -Dkerno ...; find -xdev -nouser -o -nogroup",
        comportamiento_esperado="Sin archivos/directorios sin usuario o sin grupo en filesystems locales",
        adaptacion="Exclusión adicional de filesystems remotos (nfs4, cifs, fuse, sshfs); nice/ionice/timeout",
        justificacion="Igual que 7.1.11."),
    "suid_sgid": dict(
        procedimiento_original="findmnt ... sin noexec/nosuid; find -xdev -type f -perm -2000 -o -perm -4000",
        comportamiento_esperado="Listado de SUID/SGID para revisión contra lo aprobado",
        adaptacion="Exclusión adicional de filesystems remotos; nice/ionice/timeout; un único recorrido compartido con 6.3.3.6",
        justificacion="Limitar impacto y no recorrer dos veces."),
    "audit_privilegiados": dict(
        procedimiento_original="findmnt -it <fs nodev excluidos> sin noexec/nosuid; find -perm /6000; grep de cada ruta en rules.d y auditctl -l",
        comportamiento_esperado="Cada binario privilegiado tiene regla en disco y cargada",
        adaptacion="Se reutiliza el recorrido de 7.1.13 filtrando los filesystems nodev de /proc/filesystems; la búsqueda de rutas se hace en el controlador",
        justificacion="Un solo recorrido intensivo; misma población de binarios."),
}

_PAQUETES = {
    "aide_instalado": ("aide",), "setroubleshoot": ("setroubleshoot",),
    "apparmor_instalado": ("apparmor-parser", "apparmor-profiles", "apparmor-utils", "libapparmor1"),
    "gdm_removido": ("gdm",), "gdm_disable_user_list": ("gdm", "gdm3"), "gdm_banner": ("gdm", "gdm3"),
    "auditd_paquetes": ("audit", "audit-libs"), "journal_remote_instalado": ("systemd-journal-remote",),
    "rsyslog_instalado": ("rsyslog",), "journald_a_rsyslog": ("rsyslog",),
}

# Parte 1: mismos tipos y mismo registro por título oficial.
from .controles_p1 import ADAPTACIONES_P1, CONTROLES_P1, PAQUETES_P1  # noqa: E402

CONTROLES += CONTROLES_P1
_PAQUETES.update(PAQUETES_P1)
_ADAPTACIONES.update(ADAPTACIONES_P1)

for _c in CONTROLES:
    if _c.id in _SIN_PARCIAL:
        _c.permite_parcial = False
    _c.paquetes = _PAQUETES.get(_c.id, ())
    _c.adaptacion = _ADAPTACIONES.get(_c.id)


def adaptacion_para(ctl: "Control", benchmark: str) -> Optional[Dict]:
    a = ctl.adaptacion if ctl else None
    if not a or (a.get("benchmarks") and benchmark not in a["benchmarks"]):
        return None
    return {k: v for k, v in a.items() if k != "benchmarks"}

POR_TITULO: Dict[str, Control] = {}
for _c in CONTROLES:
    for _t in _c.titulos:
        if _t in POR_TITULO:
            raise RuntimeError(f"Título duplicado en catálogo de controles: {_t}")
        POR_TITULO[_t] = _c
POR_ID: Dict[str, Control] = {c.id: c for c in CONTROLES}
if len(POR_ID) != len(CONTROLES):
    raise RuntimeError("Ids de control duplicados")


def control_para_titulo(titulo: str) -> Optional[Control]:
    return POR_TITULO.get(titulo)
