"""Evaluadores de la Parte 1: READ-ONLY, sin ampliar privilegios.

Se registran en el mismo catálogo de `controles.py` (vinculación por título oficial del CIS). Criterios
tomados de la sección Audit de los Benchmark suministrados; las diferencias v2 (OL/RHEL 9) / v4
(OL/RHEL 8) / SLES 15 se distinguen explícitamente.

Reglas comunes:
- El Resultado CIS usa solo el requisito del Benchmark (nunca el valor de la línea base interna).
- Evidencia ilegible o ausente -> ERROR DE AUDITORÍA (nunca NO CUMPLE).
- Dato privilegiado ausente: con el recolector 1.1.0 instalado -> ERROR / REQUIERE_EXTENSION_RECOLECTOR; sin
  recolector 1.2.0 instalado/ejecutado -> ERROR / PERMISOS_INSUFICIENTES (requiere 1.2.0). Nunca NO CUMPLE.
- Configuración efectiva cuando el CIS la exige (sshd -T); si no puede obtenerse sin privilegios no se
  sustituye por una lectura simple de archivos (sin falsos CUMPLE).
"""

import difflib
import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .base import ERROR, FALLA, INFO, MANUAL, NA, OK, ORG, Ctx, Evaluacion
from .base import motivo_de as base_motivo
from .comandos import RHEL, SLES
from .controles import Control, paquetes, unidades

EXT = "[REQUIERE_EXTENSION_RECOLECTOR]"


def _v8(ctx: Ctx) -> bool:
    return ctx.benchmark in ("RHEL8", "ORACLE8")


def no_legibles(ctx: Ctx, cid: str) -> List[str]:
    r = ctx.reg(cid)
    return [l.split("|", 1)[1] for l in (r.stdout.splitlines() if r else []) if l.startswith("#NO_LEGIBLE|")]


def _prob(ctx: Ctx, cid: str, relevantes: Optional[Tuple[str, ...]] = None) -> Optional[str]:
    """ctx.problema; una lectura incompleta sin privilegios de un dato que el recolector 1.1.0 no entrega
    se informa como REQUIERE_EXTENSION_RECOLECTOR (no como permisos genéricos).

    relevantes: prefijos de ruta que afectan al control; un archivo ilegible ajeno al control no lo bloquea."""
    from .privilegiado import IDS_PRIVILEGIADOS
    p = ctx.problema(cid)
    if p and relevantes is not None and p.startswith("[PERMISOS_INSUFICIENTES]"):
        nl = no_legibles(ctx, cid)
        r = ctx.reg(cid)
        if nl and "Permission denied" not in ((r.stderr if r else "") or ""):
            afect = [x for x in nl if x.startswith(relevantes)]
            if not afect:
                return None
            p = f"[PERMISOS_INSUFICIENTES] {cid}: lectura incompleta sin privilegios ({', '.join(afect)})"
    if p and p.startswith("[PERMISOS_INSUFICIENTES]") and cid not in IDS_PRIVILEGIADOS:
        return EXT + p[len("[PERMISOS_INSUFICIENTES]"):] + " (dato no incluido en el recolector privilegiado)"
    if p and p.startswith("[PERMISOS_INSUFICIENTES]"):
        p = _sin_privilegiado(ctx, cid, p)
    return p


def version_privilegiado(ctx: Ctx) -> Optional[str]:
    """Versión del recolector privilegiado cuya salida se usó (cabecera de raw/privilegiado.out), o None."""
    return (ctx.config or {}).get("recolector_privilegiado_version")


def _version_insuficiente(ctx: Ctx, cid: str) -> Optional[str]:
    """Versión instalada (cabecera del recolector privilegiado) si es ANTERIOR a la que introdujo el grupo `cid`."""
    from .privilegiado import VERSION_MINIMA, version_tupla
    inst = version_privilegiado(ctx)
    minima = VERSION_MINIMA.get(cid)
    if inst and minima and version_tupla(inst) < version_tupla(minima):
        return inst
    return None


def _sin_privilegiado(ctx: Ctx, cid: str, p: str) -> str:
    """Dato de un grupo reutilizado (p1.*) sin registro privilegiado: EXT si el recolector instalado es anterior a la
    versión que lo incluye; si no, permisos (requiere esa versión)."""
    from .privilegiado import IDS_NUEVOS_1_2_0, VERSION_MINIMA
    if cid not in IDS_NUEVOS_1_2_0:
        return p
    inst = _version_insuficiente(ctx, cid)
    if inst:
        return EXT + p[len("[PERMISOS_INSUFICIENTES]"):] + f" (dato no incluido en el recolector privilegiado {inst} instalado; requiere {VERSION_MINIMA[cid]})"
    return p + f" (requiere el recolector privilegiado {VERSION_MINIMA[cid]})"


def _priv(ctx: Ctx, cid: str) -> Optional[str]:
    """Problema de un comando exclusivo del recolector privilegiado (priv.*), o None si es evidencia válida.

    Sin registro privilegiado: EXT si el recolector instalado es anterior a la versión mínima del grupo (p. ej. 1.2.0
    instalada y grupo de 1.3.0); PERMISOS_INSUFICIENTES si no hay recolector. Nunca NO CUMPLE."""
    from .privilegiado import VERSION, VERSION_MINIMA
    r = ctx.reg(cid)
    cfg = ctx.config or {}
    minima = VERSION_MINIMA.get(cid, VERSION)
    if r is None or not r.sudo:
        if cfg.get("recoleccion_privilegiada_fallida"):
            return f"[RECOLECCION_PRIVILEGIADA_FALLIDA] {cid}: {cfg['recoleccion_privilegiada_fallida']}"
        if cfg.get("recolector_privilegiado_no_confiable"):
            return f"[RECOLECTOR_PRIVILEGIADO_NO_CONFIABLE] {cid}: no ejecutado con privilegios; {cfg['recolector_privilegiado_no_confiable']}"
        inst = _version_insuficiente(ctx, cid)
        if inst:
            return f"{EXT} {cid}: dato no incluido en el recolector privilegiado {inst} instalado (requiere {minima})"
        return (f"[PERMISOS_INSUFICIENTES] {cid}: requiere el recolector privilegiado {minima} "
                f"(no instalado, no confiable o no ejecutado en este host)")
    return ctx.problema(cid)


def ilegibles_solo(ctx: Ctx, cid: str, prefijos: Tuple[str, ...]) -> Optional[List[str]]:
    """Si el ÚNICO problema de `cid` es la lectura incompleta sin privilegios (#NO_LEGIBLE) y todos los archivos no
    legibles están bajo `prefijos`, devuelve esa lista (el evaluador decide qué componentes dependen de ellos).
    En cualquier otro caso (comando no ejecutado, rc inesperado, 'Permission denied' en stderr, archivos ilegibles fuera
    de `prefijos`...) devuelve None y el control sigue el camino fail-safe de siempre. Los archivos no legibles siguen
    registrados en la evidencia cruda; solo se acota su impacto a los controles que los necesitan."""
    p = ctx.problema(cid)
    r = ctx.reg(cid)
    nl = no_legibles(ctx, cid)
    if (p and p.startswith("[PERMISOS_INSUFICIENTES]") and nl and r is not None and "Permission denied" not in (r.stderr or "")
            and all(x.startswith(prefijos) for x in nl)):
        return nl
    return None


def obs_ilegibles(ctx: Ctx, ev: Evaluacion, cid: str) -> None:
    """Trazabilidad: deja constancia de los archivos no legibles del comando aunque no sean evidencia de este control."""
    nl = no_legibles(ctx, cid)
    if nl and not any(c.estado == ERROR for c in ev.componentes):
        ev.observaciones.append(f"Archivos no legibles sin privilegios en {cid} (registrados en la evidencia cruda; no son evidencia "
                                f"necesaria para este control según el Audit CIS): {', '.join(nl)}")


def con_traza(cid: str):
    """Decorador: añade la observación de trazabilidad de archivos no legibles (obs_ilegibles) al resultado."""
    def deco(f):
        def _ev(ctx: Ctx) -> Evaluacion:
            ev = f(ctx)
            obs_ilegibles(ctx, ev, cid)
            return ev
        _ev.__name__ = getattr(f, "__name__", "_ev")
        return _ev
    return deco


def _num(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _modo(v: str) -> Optional[int]:
    try:
        return int(v, 8)
    except (TypeError, ValueError):
        return None


def _lineas_tag(ctx: Ctx, cid: str, tag: str) -> List[Tuple[str, int, str]]:
    """Líneas 'TAG|archivo|n:texto' -> [(archivo, n, texto)]."""
    res = []
    for l in ctx.lineas(cid):
        if l.startswith(tag + "|"):
            p = l.split("|", 2)
            if len(p) == 3:
                n, _, t = p[2].partition(":")
                res.append((p[1], _num(n) or 0, t))
    return res


def _stats(ctx: Ctx, cid: str) -> Dict[str, Optional[Tuple[int, str, str, str]]]:
    """'S|ruta|modo|usuario|grupo|tipo' o 'S|ruta|NO_EXISTE' -> {ruta: (modo, u, g, tipo) | None}."""
    res = {}
    for l in ctx.lineas(cid):
        if not l.startswith("S|"):
            continue
        p = l.split("|")
        if len(p) >= 3 and p[2] == "NO_EXISTE":
            res[p[1]] = None
        elif len(p) >= 6:
            res[p[1]] = (_modo(p[2]), p[3], p[4], p[5])
    return res


def _acceso(ev: Evaluacion, nombre: str, st, max_modo: int, grupos=("root",), cid="", opcional=True):
    if st is None:
        ev.add(nombre, f"{nombre}: permisos", NA if opcional else FALLA, f"<= {max_modo:04o} root:{'/'.join(grupos)}",
               "no existe", [cid]); return
    modo, u, g, _ = st
    malos = []
    if modo is None or modo & ~max_modo & 0o7777:
        malos.append(f"modo {modo:04o}" if modo is not None else "modo no interpretable")
    if u != "root":
        malos.append(f"propietario {u}")
    if g not in grupos:
        malos.append(f"grupo {g}")
    ev.add(nombre, f"{nombre}: modo <= {max_modo:04o}, root:{'/'.join(grupos)}", FALLA if malos else OK,
           f"<= {max_modo:04o} root {'/'.join(grupos)}", f"{modo:04o} {u} {g}" if modo is not None else f"? {u} {g}", [cid])


# ===================================================================== módulos de kernel

_ORDEN_MODPROBE = ("/etc/modprobe.d/", "/run/modprobe.d/", "/usr/local/lib/modprobe.d/", "/usr/lib/modprobe.d/", "/lib/modprobe.d/")


def configuracion_modprobe(ctx: Ctx, ilegibles: Tuple[str, ...] = ()) -> Tuple[Dict[str, List[str]], List[str]]:
    """Directivas install/blacklist efectivas (mismo archivo en /etc oculta al de /usr/lib) -> {modulo: [directivas]}.

    ilegibles: archivos de modprobe.d no legibles. Participan en la precedencia por nombre: si uno oculta a un archivo
    legible de menor prioridad con el mismo nombre, las directivas de este último NO se usan como evidencia."""
    por_nombre: Dict[str, Tuple[int, str]] = {}
    lineas = _lineas_tag(ctx, "p1.kmod", "C")
    for f in list({f for f, _, _ in lineas}) + list(ilegibles):
        pri = next((i for i, d in enumerate(_ORDEN_MODPROBE) if f.startswith(d)), 99)
        base = f.rsplit("/", 1)[-1]
        if base not in por_nombre or pri < por_nombre[base][0]:
            por_nombre[base] = (pri, f)
    efectivos = {f for _, f in por_nombre.values()} - set(ilegibles)
    res: Dict[str, List[str]] = {}
    for f, n, t in lineas:
        if f not in efectivos:
            continue
        m = re.match(r"^\s*(install|blacklist)\s+(\S+)(.*)$", t)
        if m:
            res.setdefault(m.group(2).replace("-", "_"), []).append(f"{m.group(1)} {m.group(2)}{m.group(3)} ({f}:{n})")
    cmdline = []
    for l in ctx.lineas("p1.kmod"):
        if l.startswith("B|modprobe.blacklist="):
            cmdline += [x.replace("-", "_") for x in l.split("=", 1)[1].split(",") if x]
    return res, cmdline


def kmod_config_priv(ctx: Ctx) -> Tuple[Optional[Dict[str, List[Tuple[str, str]]]], Optional[str], List[str]]:
    """priv.kmod_config (1.3.0) -> ({modulo: [(directiva, destino|'')...]}, problema, archivos inciertos).

    Precedencia de kmod: para cada nombre de archivo gana el directorio de mayor prioridad (DIRS_MODPROBE); un enlace
    a /dev/null enmascara. Si el archivo ganador no pudo procesarse (KE), ese nombre queda INCIERTO: una directiva
    ausente no puede descartarse (-> ERROR, nunca CUMPLE)."""
    from .comandos_priv import DIRS_MODPROBE
    cid = "priv.kmod_config"
    prob = _priv(ctx, cid)
    if prob:
        return None, prob, []
    lin = ctx.lineas(cid)
    if not lin or lin[-1] != "K_FIN" or lin.count("K_FIN") != 1:
        return None, f"[SALIDA_NO_INTERPRETABLE] {cid}: salida incompleta o truncada (sin K_FIN)", []
    entradas: Dict[str, Tuple[int, str, str]] = {}      # basename -> (prioridad, ruta, estado)
    directivas = []
    for l in lin:
        p = l.split("|")
        if p[0] in ("KF", "KE") and len(p) >= 3:
            ruta = p[1]
            d, base = ruta.rsplit("/", 1)
            if d not in DIRS_MODPROBE:
                return None, f"[SALIDA_NO_INTERPRETABLE] {cid}: directorio inesperado {d}", []
            estado = p[2] if p[0] == "KF" else "incierto:" + p[2]
            pri = DIRS_MODPROBE.index(d)
            if base not in entradas or pri < entradas[base][0]:
                entradas[base] = (pri, ruta, estado)
        elif p[0] == "KC" and len(p) >= 6:
            if p[3] not in ("install", "blacklist") or (p[3] == "install" and p[5] not in ("true", "false", "OTRO")):
                return None, f"[SALIDA_NO_INTERPRETABLE] {cid}: línea KC no válida", []
            directivas.append((p[1], p[2], p[3], p[4], p[5]))
        elif p[0] != "K_FIN":
            return None, f"[SALIDA_NO_INTERPRETABLE] {cid}: salida inesperada", []
    efectivos = {r for _, r, e in entradas.values() if e == "regular"}
    inciertos = sorted(r for _, r, e in entradas.values() if e.startswith("incierto"))
    conf: Dict[str, List[Tuple[str, str]]] = {}
    for ruta, n, tipo, mod, dest in directivas:
        if ruta in efectivos:
            conf.setdefault(mod, []).append((f"{tipo} {mod}" + (f" -> {dest}" if dest else "") + f" ({ruta}:{n})", dest if tipo == "install" else "blacklist"))
    return conf, None, inciertos


def _modulo_cargado(ctx: Ctx, mod: str) -> bool:
    return any(l == "L|" + mod.replace("-", "_") for l in ctx.lineas("p1.kmod"))


def _eval_modulo(ctx: Ctx, ev: Evaluacion, mod: str, disponible: Optional[bool] = None, ilegibles: Tuple[str, ...] = (),
                 priv: Optional[Tuple[Dict, List[str]]] = None) -> None:
    """Audit CIS: módulo no disponible -> sin más configuración; si está disponible: no cargado (lsmod) + 'install <m>
    /bin/true|false' + 'blacklist <m>' en `modprobe --showconfig` (presencia).

    Con archivos de modprobe.d no legibles (ilegibles): disponibilidad y carga se evalúan igual (no dependen de
    modprobe.d); una directiva encontrada en un archivo legible efectivo es evidencia válida (un archivo ilegible no
    puede retirarla de --showconfig); una directiva AUSENTE es indeterminable -> ERROR (nunca FALLA ni CUMPLE).
    Evidencia: disponibilidad por kernel, carga, resolución deducida de modprobe.d (el proyecto no invoca
    modprobe) y motivo explícito."""
    norm = mod.replace("-", "_")
    rutas = [l[2:] for l in ctx.lineas("p1.kmod") if l.startswith("K|")
             and (re.search(r"/" + re.escape(mod) + r"\.ko", l) or re.search(r"/" + re.escape(norm) + r"\.ko", l))]
    if disponible is None:
        disponible = bool(rutas)
    if not disponible:
        ev.add(f"{mod}_disponible", f"Módulo {mod} disponible en algún kernel instalado", OK,
               "no disponible (o compilado en el kernel): sin configuración adicional", "no hay archivo de módulo en /lib/modules", ["p1.kmod"])
        return
    conf, cmdline = configuracion_modprobe(ctx, tuple(ilegibles))
    if priv is not None:
        _eval_modulo_priv(ctx, ev, mod, norm, priv[0], priv[1], cmdline)
    else:
        d = conf.get(norm, [])
        inst = [x for x in d if re.match(r"install \S+\s+(/usr)?/bin/(true|false)\b", x)]
        bl = [x for x in d if x.startswith("blacklist")] + (["modprobe.blacklist (cmdline)"] if norm in cmdline else [])
        cargado = _modulo_cargado(ctx, mod)
        ev.add(f"{mod}_no_cargado", f"{mod} no cargado en el kernel en ejecución", FALLA if cargado else OK, "no cargado",
               "cargado" if cargado else "no cargado", ["p1.kmod"])
        falta = (f"{_priv(ctx, 'priv.kmod_config') or _prob(ctx, 'p1.kmod')}; la directiva no está en los archivos legibles y podría "
                 f"estar en: {', '.join(ilegibles)}" if ilegibles else None)
        ev.add(f"{mod}_install", f"{mod}: 'install {mod} /bin/false|/bin/true' en modprobe.d", OK if inst else (ERROR if falta else FALLA),
               f"install {mod} /bin/false", "; ".join(inst) or falta or "no configurado", ["p1.kmod"])
        ev.add(f"{mod}_blacklist", f"{mod}: deny list (blacklist) en modprobe.d", OK if bl else (ERROR if falta else FALLA), f"blacklist {mod}",
               "; ".join(bl) or falta or "no configurado", ["p1.kmod"])
    comp = {c.id: c for c in ev.componentes}
    inst_c, bl_c, carg_c = comp.get(f"{mod}_install"), comp.get(f"{mod}_blacklist"), comp.get(f"{mod}_no_cargado")
    bloqueado = bool(inst_c and inst_c.estado == OK and bl_c and bl_c.estado == OK)
    demostrado = any(c and c.estado == FALLA for c in (inst_c, bl_c))   # fail-closed: una directiva incierta (ERROR) no decide
    kernels = sorted({re.sub(r"^/lib/modules/([^/]+)/.*$", r"\1", r) for r in rutas})
    ev.add(f"{mod}_disponible", f"Módulo {mod} presente en el kernel (disponible para cargarse)",
           OK if bloqueado else (FALLA if demostrado else INFO),
           "no disponible, o disponible con la carga bloqueada (install + blacklist)",
           f"SÍ en {', '.join(kernels) or 'algún kernel instalado'}" + (" — carga bloqueada" if bloqueado else " — carga NO bloqueada"),
           ["p1.kmod"])
    ev.add(f"{mod}_resolucion", f"Resolución de modprobe para {mod} (deducida de modprobe.d; el proyecto no invoca modprobe)", INFO,
           "install /bin/false|true", (inst_c.encontrado if inst_c and inst_c.estado == OK else "sin directiva install: modprobe cargaría el módulo"),
           ["p1.kmod"] + (["priv.kmod_config"] if priv is not None else []))
    if not bloqueado and not any(c.estado == ERROR for c in (inst_c, bl_c) if c):
        no_cargado = bool(carg_c and carg_c.estado == OK)
        estado = lambda c: "OK" if c and c.estado == OK else "FALLA"   # noqa: E731
        ev.mecanismo["motivo_visible"] = (
            ("Módulo no cargado actualmente, pero continúa disponible y su carga no está bloqueada." if no_cargado else
             "Módulo cargado actualmente; continúa disponible y su carga no está bloqueada.")
            + f" {mod}: Módulo presente: SÍ ({', '.join(kernels) or 'kernel instalado'}) · Módulo cargado: {'NO' if no_cargado else 'SÍ'}"
            f" · Install rule: {estado(inst_c)} · Blacklist: {estado(bl_c)}.")


def _eval_modulo_priv(ctx: Ctx, ev: Evaluacion, mod: str, norm: str, conf: Dict, inciertos: List[str], cmdline: List[str]) -> None:
    """Configuración completa leída como root (priv.kmod_config). Presencia según el Audit CIS; conflictos e incertidumbre
    de precedencia -> ERROR (fail-safe)."""
    ev_ids = ["p1.kmod", "priv.kmod_config"]
    cargado = _modulo_cargado(ctx, mod)
    ev.add(f"{mod}_no_cargado", f"{mod} no cargado en el kernel en ejecución", FALLA if cargado else OK, "no cargado",
           "cargado" if cargado else "no cargado", ["p1.kmod"])
    d = conf.get(norm, [])
    inst = [t for t, dest in d if dest in ("true", "false")]
    otro = [t for t, dest in d if dest == "OTRO"]
    bl = [t for t, dest in d if dest == "blacklist"] + (["modprobe.blacklist (cmdline)"] if norm in cmdline else [])
    incierto = (f"[ANALISIS_ESTATICO_NO_CONCLUYENTE] precedencia no determinable: archivo(s) de modprobe.d de mayor prioridad no "
                f"procesables {', '.join(inciertos)}") if inciertos else None
    if inst and incierto:
        # Un archivo efectivo no procesable podría contener otro 'install' del mismo módulo: precedencia no segura.
        ev.add(f"{mod}_install", f"{mod}: 'install {mod} /bin/false|/bin/true' en modprobe.d", ERROR, f"install {mod} /bin/false",
               f"{'; '.join(inst)} | {incierto}", ev_ids)
    elif inst and otro:
        ev.add(f"{mod}_install", f"{mod}: 'install {mod} /bin/false|/bin/true' en modprobe.d", ERROR, f"install {mod} /bin/false",
               "[ANALISIS_ESTATICO_NO_CONCLUYENTE] directivas install en conflicto (true/false y otro comando): " + "; ".join(inst + otro), ev_ids)
    elif inst:
        ev.add(f"{mod}_install", f"{mod}: 'install {mod} /bin/false|/bin/true' en modprobe.d", OK, f"install {mod} /bin/false", "; ".join(inst), ev_ids)
    elif otro:
        # Con una fuente no procesable, otro 'install' (p. ej. /bin/false) podría prevalecer: no es concluyente.
        ev.add(f"{mod}_install", f"{mod}: 'install {mod} /bin/false|/bin/true' en modprobe.d", ERROR if incierto else FALLA,
               f"install {mod} /bin/false", "install con otro comando (el módulo sigue siendo cargable): " + "; ".join(otro)
               + (f" | {incierto}" if incierto else ""), ev_ids)
    else:
        ev.add(f"{mod}_install", f"{mod}: 'install {mod} /bin/false|/bin/true' en modprobe.d", ERROR if incierto else FALLA,
               f"install {mod} /bin/false", incierto or "no configurado", ev_ids)
    ev.add(f"{mod}_blacklist", f"{mod}: deny list (blacklist) en modprobe.d", OK if bl else (ERROR if incierto else FALLA), f"blacklist {mod}",
           "; ".join(bl) or incierto or "no configurado", ev_ids)


def ev_modulo(mod: str):
    def _ev(ctx: Ctx) -> Evaluacion:
        from .comandos_priv import MODULOS_KMOD_1_3
        ev = Evaluacion()
        ileg = ilegibles_solo(ctx, "p1.kmod", _ORDEN_MODPROBE)
        prob = None if ileg else _prob(ctx, "p1.kmod")
        if prob:
            ev.add("kmod", f"Configuración del módulo {mod}", ERROR, "", prob, ["p1.kmod"]); return ev
        priv = None
        if mod.replace("-", "_") in MODULOS_KMOD_1_3 and (ctx.reg("priv.kmod_config") is not None and ctx.reg("priv.kmod_config").sudo):
            conf, pprob, inciertos = kmod_config_priv(ctx)
            if pprob:
                ev.add("kmod", f"Configuración del módulo {mod} (recolector privilegiado)", ERROR, "", pprob, ["priv.kmod_config"]); return ev
            priv = (conf, inciertos)
        _eval_modulo(ctx, ev, mod, ilegibles=() if priv else tuple(ileg or ()), priv=priv)
        obs_ilegibles(ctx, ev, "p1.kmod")
        return ev
    return _ev


def sysctl_efectivo(ctx: Ctx, param: str) -> Tuple[Optional[Tuple[str, str]], List[Tuple[str, str]], List[str]]:
    """Valor PERSISTENTE EFECTIVO de un parámetro según la precedencia de systemd-sysctl (archivos en el orden de
    `--cat-config`; dentro de un archivo, la última línea gana) y el criterio del script CIS v2 (el archivo efectivo es
    el último que define el nombre exacto; se validan los valores de ese archivo). Devuelve (efectivo (archivo, valor) o
    None, todas las ocurrencias, avisos). Una asignación con comodín POSTERIOR al archivo efectivo podría prevalecer en
    el arranque: se informa como aviso (y el componente queda no concluyente)."""
    ocurr, extra = sysctl_persistente(ctx, param)
    ocurr = [(a, v) for a, v in ocurr]
    if not ocurr:
        return None, ocurr, extra
    efectivo = ocurr[-1]
    avisos = []
    hoja = param.rsplit(".", 1)[1]
    pos, comodines, archivo = {}, [], None
    for l in ctx.lineas("p1.sysctl_archivos"):
        m = re.match(r"^\s*#\s*(/\S+)", l)
        if m:
            archivo = m.group(1); pos.setdefault(archivo, len(pos) + 1)
            continue
        if "*" in l and re.search(r"[./]" + re.escape(hoja) + r"\s*=", l) and archivo:
            comodines.append((archivo, l.strip()))
    for archivo, l in comodines:   # posiciones calculadas ANTES de comparar (orden de carga completo)
        if pos.get(archivo, 0) > pos.get(efectivo[0], 0):
            avisos.append(f"{l} en {archivo}: asignación con comodín posterior al archivo efectivo")
    return efectivo, ocurr, avisos


_RE_UMASK_OK = re.compile(r"^\s*umask\s+(0?[0-7][2-7]7|u(=[rwx]{0,3}),g=([rx]{0,2}),o=)(\s*#.*)?$", re.I)


_RE_UMASK_MAL = re.compile(r"^\s*umask\s+(([0-7][0-7][01][0-7]\b|[0-7][0-7][0-7][0-6]\b)|([0-7][01][0-7]\b|[0-7][0-7][0-6]\b)|"
                           r"(u=[rwx]{1,3},)?(((g=[rx]?[rx]?w[rx]?[rx]?\b)(,o=[rwx]{1,3})?)|((g=[wrx]{1,3},)?o=[wrx]{1,3}\b)))", re.I)


_RE_PAMUMASK_OK = re.compile(r"^\s*session\s+[^#\n\r]+\s+pam_umask\.so\s+([^#\n\r]+\s+)?umask=(0?[0-7][2-7]7)\b", re.I)


_RE_PAMUMASK_MAL = re.compile(r"^\s*session\s+[^#\n\r]+\s+pam_umask\.so\s+([^#\n\r]+\s+)?umask=(([0-7][0-7][01][0-7]\b|[0-7][0-7][0-7][0-6]\b)|([0-7][01][0-7]\b))", re.I)


# ===================================================================== particiones y opciones de montaje

def montajes(ctx: Ctx) -> Dict[str, Optional[Dict]]:
    res = {}
    for l in ctx.lineas("p1.montajes"):
        if not l.startswith("MNT|"):
            continue
        cuerpo = l[4:]
        if cuerpo.endswith("|NO_MONTADO"):
            res[cuerpo.split("|")[0]] = None
            continue
        p = cuerpo.split(" ")
        if len(p) >= 4:
            res[p[0]] = {"origen": p[1], "fstype": p[2], "opciones": p[3].split(",")}
    return res


def _fstab(ctx: Ctx) -> Dict[str, List[str]]:
    res = {}
    for l in ctx.lineas("p1.montajes"):
        if l.startswith("FSTAB|"):
            p = l.split("|")
            if len(p) >= 4:
                res[p[1]] = p[3].split(",")
    return res


def ev_particion(destino: str, tmpfs_requerido: bool = False, tmp_mount: bool = False):
    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        prob = _prob(ctx, "p1.montajes")
        mnt = montajes(ctx)
        if prob or destino not in mnt:
            ev.add("montaje", f"{destino} montado", ERROR, "", prob or "[SALIDA_NO_INTERPRETABLE] sin dato de findmnt", ["p1.montajes"])
            return ev
        m = mnt[destino]
        if tmpfs_requerido:
            ok = bool(m) and m["fstype"] == "tmpfs"
            ev.add("tmpfs", f"{destino} montado como tmpfs", OK if ok else FALLA, "tmpfs",
                   f"{m['fstype']} ({m['origen']})" if m else "no montado", ["p1.montajes"])
        else:
            ev.add("montaje", f"{destino} es un punto de montaje propio", OK if m else FALLA, f"{destino} montado (findmnt -kn {destino})",
                   f"{m['origen']} {m['fstype']}" if m else "no es un montaje independiente", ["p1.montajes"])
        if tmp_mount and m:
            u = unidades(ctx, "p1.unidades").get("tmp.mount")
            if u is None and _prob(ctx, "p1.unidades"):
                ev.add("tmp_mount", "systemd monta /tmp al arranque (tmp.mount no masked/disabled)", ERROR, "no masked ni disabled",
                       _prob(ctx, "p1.unidades"), ["p1.unidades"])
            elif u and u[0] in ("masked", "disabled"):
                ev.add("tmp_mount", "systemd monta /tmp al arranque (tmp.mount no masked/disabled)", FALLA, "no masked ni disabled", u[0], ["p1.unidades"])
            elif u:
                ev.add("tmp_mount", "systemd monta /tmp al arranque (tmp.mount no masked/disabled)", OK, "no masked ni disabled", u[0], ["p1.unidades"])
        if m and destino not in _fstab(ctx) and destino not in ("/tmp", "/dev/shm"):
            ev.observaciones.append(f"{destino} está montado pero no figura en /etc/fstab: verificar que el montaje persista tras reinicio.")
        return ev
    return _ev


def ev_opcion(destino: str, opcion: str):
    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        prob = _prob(ctx, "p1.montajes")
        mnt = montajes(ctx)
        if prob or destino not in mnt:
            ev.add("opcion", f"{opcion} en {destino}", ERROR, "", prob or "[SALIDA_NO_INTERPRETABLE] sin dato de findmnt", ["p1.montajes"])
            return ev
        m = mnt[destino]
        if not m:
            ev.add("opcion", f"{opcion} en {destino}", NA, f"-IF- {destino} es partición propia", f"{destino} no es un montaje independiente",
                   ["p1.montajes"])
            return ev
        ok = opcion in m["opciones"]
        ev.add("opcion", f"{opcion} en {destino} (en ejecución)", OK if ok else FALLA, opcion, ",".join(m["opciones"]), ["p1.montajes"])
        fs = _fstab(ctx).get(destino)
        if fs is not None and opcion not in fs:
            ev.observaciones.append(f"La entrada de /etc/fstab de {destino} no incluye {opcion} ({','.join(fs)}): la opción no persistiría tras reinicio.")
        return ev
    return _ev


# ===================================================================== servicios y paquetes

def ev_servicio(nombre: str, pkgs: Tuple[str, ...], units: Tuple[str, ...], solo_paquete: bool = False):
    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        prob = _prob(ctx, "pkg.rpm")
        pk = paquetes(ctx)
        falt = [p for p in pkgs if p not in pk]
        if prob or falt:
            ev.add("paquete", f"Paquetes de {nombre}", ERROR, "", prob or f"[SALIDA_NO_INTERPRETABLE] rpm -q sin dato de {falt}", ["pkg.rpm"])
            return ev
        inst = [pk[p] for p in pkgs if pk[p]]
        if not inst:
            ev.add("paquete", f"{nombre}: paquetes no instalados", OK, f"{', '.join(pkgs)} no instalados", "no instalados", ["pkg.rpm"])
            return ev
        if solo_paquete:
            ev.add("paquete", f"{nombre}: paquetes no instalados", FALLA, f"{', '.join(pkgs)} no instalados", "; ".join(inst), ["pkg.rpm"])
            return ev
        prob = _prob(ctx, "p1.unidades")
        u = unidades(ctx, "p1.unidades")
        if prob:
            ev.add("servicio", f"{nombre}: estado de servicios", ERROR, "", prob, ["p1.unidades"]); return ev
        presentes = [x for x in units if x in u and u[x][0] != "no-existe"]
        ev.observaciones.append(f"{nombre}: paquete(s) instalado(s) ({'; '.join(inst)}); el CIS lo admite solo si los servicios no están habilitados ni activos.")
        if not presentes:
            ev.add("servicio", f"{nombre}: servicios no habilitados ni activos", OK, "no enabled / no active", "unidades no presentes", ["pkg.rpm", "p1.unidades"])
        for x in presentes:
            en, ac = u[x]
            ev.add(f"{x}_enabled", f"{x} no habilitado", FALLA if en == "enabled" else OK, "no enabled", en, ["p1.unidades"])
            ev.add(f"{x}_active", f"{x} no activo", FALLA if ac == "active" else OK, "no active", ac, ["p1.unidades"])
        return ev
    return _ev


def ev_paquete_ausente(nombre: str, pkgs: Tuple[str, ...]):
    return ev_servicio(nombre, pkgs, (), solo_paquete=True)


def ev_paquete_presente(nombre: str, pkg: str):
    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        prob = _prob(ctx, "pkg.rpm")
        pk = paquetes(ctx)
        if prob or pkg not in pk:
            ev.add("paquete", f"{pkg} instalado", ERROR, "", prob or "[SALIDA_NO_INTERPRETABLE] sin dato de rpm -q", ["pkg.rpm"]); return ev
        ev.add("paquete", f"{pkg} instalado", OK if pk[pkg] else FALLA, f"{pkg} instalado", pk[pkg] or "no instalado", ["pkg.rpm"])
        return ev
    return _ev


def ev_mta(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = _prob(ctx, "p1.escucha")
    if prob:
        ev.add("mta", "MTA solo en loopback", ERROR, "", prob, ["p1.escucha"]); return ev
    pc = next((l.split("|", 1)[1] for l in ctx.lineas("p1.escucha") if l.startswith("POSTCONF|")), None)
    if pc is None:
        ev.add("mta", "MTA solo en loopback", ERROR, "", "[SALIDA_NO_INTERPRETABLE] sin dato de postconf", ["p1.escucha"]); return ev
    if pc != "NO_INSTALADO":
        ev.add("postfix", "postfix inet_interfaces distinto de all", FALLA if pc.strip() == "all" else OK, "loopback-only / localhost",
               pc, ["p1.escucha"])
    malos = []
    for l in ctx.lineas("p1.escucha"):
        p = l.split()
        if len(p) >= 5 and p[0] in ("tcp", "udp") and re.search(r":(25|465|587)$", p[4]):
            if not re.match(r"^(127\.0\.0\.1|\[?::1\]?|\[::ffff:127\.0\.0\.1\]):", p[4]):
                malos.append(p[4])
    ev.add("puertos", "Puertos 25/465/587 solo en loopback", FALLA if malos else OK, "127.0.0.1 / ::1",
           ", ".join(malos) or "sin escucha no-loopback", ["p1.escucha"])
    return ev


# ===================================================================== banners

def _os_id(ctx: Ctx) -> str:
    for l in ctx.lineas("sys.os_release"):
        if l.startswith("ID="):
            return l.split("=", 1)[1].strip().strip('"')
    return ""


_MO_CAMPOS = ("tipo", "legible", "bytes", "esc_v", "esc_r", "esc_m", "esc_s", "id_so")


def motd_priv(ctx: Ctx) -> Tuple[Optional[List[Dict[str, str]]], Optional[str]]:
    """priv.motd (1.3.0) -> ([{ruta, tipo, legible, bytes, esc_*, id_so}], problema). Formato cerrado; cualquier otra
    línea, campo o valor no 0/1 invalida la evidencia (fail-safe)."""
    cid = "priv.motd"
    prob = _priv(ctx, cid)
    if prob:
        return None, prob
    lin = ctx.lineas(cid)
    if not lin or lin[-1] != "MO_FIN" or lin.count("MO_FIN") != 1:
        return None, f"[SALIDA_NO_INTERPRETABLE] {cid}: salida incompleta o truncada (sin MO_FIN)"
    res = []
    for l in lin[:-1]:
        p = l.split("|")
        kv = dict(x.split("=", 1) for x in p[2:] if "=" in x)
        if p[0] != "MO" or len(p) != 10 or not (p[1] == "/etc/motd" or p[1].startswith("/etc/motd.d/")) or tuple(kv) != _MO_CAMPOS \
                or any(kv[k] not in ("0", "1") for k in _MO_CAMPOS if k.startswith(("esc_", "id_so", "legible"))) or not kv["bytes"].isdigit():
            return None, f"[SALIDA_NO_INTERPRETABLE] {cid}: línea no válida"
        res.append({"ruta": p[1], **kv})
    return res, None


_CLAVE_BANNER = {"/etc/motd": "motd", "/etc/issue": "issue", "/etc/issue.net": "issue_net"}


_LIMITE_LINEAS_BANNER = 60   # p1.banner captura 'head -n 60' línea a línea ('while IFS= read -r': conserva espacios y orden)
_RAIZ_PROYECTO = Path(__file__).resolve().parents[3]


_SIMILITUD_MINIMA_BANNER = 0.95


def _texto_banner(lineas: List[str]) -> str:
    """Texto del banner sin el marco decorativo: se quitan los '*' y se colapsan los espacios (no se tocan palabras,
    acentos, mayúsculas ni signos)."""
    return " ".join(w for l in lineas for w in l.replace("*", " ").split())


def comparar_banner(politica: bytes, lineas_host: List[str], bytes_host: Optional[int],
                    umbral: float = _SIMILITUD_MINIMA_BANNER) -> Dict:
    """Comparación SIMILITUD_TEXTUAL (decisión aprobada): se compara el texto del mensaje, no el formato.
    Se ignoran el marco de '*', los espacios de relleno, las líneas vacías y CRLF/LF. Similitud = difflib ratio sobre el
    texto resultante; CUMPLE si >= umbral (config banners_comparacion.similitud_minima, por defecto 0,95).

    Fidelidad de la evidencia (captura 'head -n 60' + 'while IFS= read -r'): con >= 60 líneas capturadas puede faltar
    contenido; si el texto es similar en lo capturado, el resultado queda en MANUAL. Una última línea sin salto final no se
    captura: se informa (completitud por tamaño verificada solo si root informó el tamaño, priv.motd).
    Devuelve {estado: OK|FALLA|MANUAL, mensaje, detalle}."""
    p = politica.decode("utf-8", errors="strict").replace("\r\n", "\n").split("\n")
    if p and p[-1] == "":
        p = p[:-1]
    h = list(lineas_host)
    recon = sum(len(x.encode("utf-8")) + 1 for x in h)
    truncada = len(h) >= _LIMITE_LINEAS_BANNER
    completa = bytes_host is not None and bytes_host == recon and not truncada
    det = {"comparacion": "SIMILITUD_TEXTUAL", "normalizacion": "sin marco '*'; espacios colapsados; CRLF->LF; líneas vacías",
           "umbral_similitud": umbral, "lineas_politica": len(p), "lineas_host_capturadas": len(h), "bytes_host": bytes_host,
           "bytes_reconstruidos": recon,
           "evidencia_completa": "VERIFICADA (tamaño del host)" if completa else
           ("POSIBLEMENTE_TRUNCADA (>= 60 líneas)" if truncada else "NO_VERIFICABLE_POR_TAMAÑO (sin tamaño del host)")}
    if any("<REDACTADO>" in x for x in h):
        return {"estado": MANUAL, "mensaje": "La evidencia almacenada contiene texto redactado: no permite reconstruir el mensaje.", "detalle": det}
    tp, th = _texto_banner(p), _texto_banner(h)
    # Captura truncada: se compara lo capturado contra el tramo equivalente de la política.
    ref = tp[:len(th)] if truncada and len(tp) > len(th) else tp
    sim = difflib.SequenceMatcher(None, ref, th, autojunk=False).ratio() if (ref or th) else 1.0
    det["similitud"] = round(sim, 4)
    det["texto_identico"] = tp == th
    det["lineas_formato_distintas"] = [i + 1 for i in range(min(len(p), len(h))) if p[i] != h[i]]
    pct = f"{sim * 100:.1f} %".replace(".", ",")
    if sim < umbral:
        return {"estado": FALLA, "detalle": det,
                "mensaje": f"El texto del host no corresponde a la política: similitud {pct} (mínimo {umbral * 100:.0f} %)."}
    if truncada:
        return {"estado": MANUAL, "detalle": det,
                "mensaje": f"Texto similar en lo capturado ({pct}), pero se alcanzó el límite de 60 líneas de la captura."}
    nota = "" if completa else " Completitud por tamaño no verificable (sin tamaño del host)."
    return {"estado": OK, "detalle": det,
            "mensaje": ("texto idéntico a la política (sin considerar el marco ni los espacios de relleno)" if tp == th
                        else f"texto similar a la política: similitud {pct} (mínimo {umbral * 100:.0f} %)") + "." + nota}


def _politica_banner(ctx: Ctx, ev: Evaluacion, archivo: str) -> None:
    """Política del banner: banners_aprobados (config/audit.yml) -> archivo de política (input/baseline/files/p1/);
    el texto se lee del propio archivo y su SHA-256 se calcula aquí (nunca se copia a YAML ni a código)."""
    aprobado = (ctx.listas or {}).get("banner_texto_aprobado")
    pol = ((ctx.config or {}).get("banners_aprobados") or {}).get(_CLAVE_BANNER.get(archivo, ""))
    if aprobado:
        texto = " ".join(l.split("|", 2)[2] for l in ctx.lineas("p1.banner") if l.startswith(f"C|{archivo}|"))
        ev.add("politica", f"{archivo} conforme a la política del sitio", OK if aprobado.lower() in texto.lower() else FALLA,
               f"contiene: {aprobado}", texto[:200] or "sin contenido legible", ["p1.banner"])
        return
    if not pol:
        ev.add("politica", f"{archivo} conforme a la política del sitio", ORG, "texto aprobado (config/listas_aprobadas.yml: banner_texto_aprobado)",
               "[EVIDENCIA_ORGANIZACIONAL] sin texto de banner aprobado", ["p1.banner"])
        return
    ruta = _RAIZ_PROYECTO / pol["archivo"]
    if not ruta.is_file() or ruta.stat().st_size == 0:
        ev.add("politica", f"{archivo} conforme al banner aprobado", ERROR, pol["archivo"],
               f"[SALIDA_NO_INTERPRETABLE] archivo de política no disponible o vacío: {pol['archivo']}", ["p1.banner"])
        return
    datos = ruta.read_bytes()
    sha = hashlib.sha256(datos).hexdigest()
    lineas = [l.split("|", 2)[2] for l in ctx.lineas("p1.banner") if l.startswith(f"C|{archivo}|")]
    tam = None
    if archivo == "/etc/motd" and ctx.reg("priv.motd") is not None and ctx.reg("priv.motd").sudo:
        arch, prob = motd_priv(ctx)
        tam = next((_num(a.get("bytes")) for a in (arch or []) if a.get("ruta") == archivo and a.get("tipo") == "regular_file"), None)
    c = comparar_banner(datos, lineas, tam, float(((ctx.config or {}).get("banners_comparacion") or {}).get("similitud_minima", _SIMILITUD_MINIMA_BANNER)))
    esperado = f"contenido de {pol['archivo']} (SHA-256 {sha[:16]}…) · comparación SIMILITUD_TEXTUAL"
    if c["estado"] == OK:
        enc = f"{archivo} coincide con la política aprobada {pol['archivo']}: {c['mensaje']}"
    elif c["estado"] == FALLA:
        enc = f"{archivo} no coincide con la política aprobada {pol['archivo']}: {c['mensaje']}"
    else:
        enc = f"[EVIDENCIA_HOST_INSUFICIENTE] {c['mensaje']}"
    ev.add("politica", f"{archivo} conforme al banner aprobado", c["estado"], esperado, enc, ["p1.banner"] + (["priv.motd"] if tam else []))
    ev.mecanismo = {**(ev.mecanismo or {}), "comparacion_banner": {"archivo_host": archivo, "fuente_politica": pol["archivo"],
                                                                   "sha256_politica": sha, **c["detalle"], "mensaje": enc}}


def ev_banner(archivo: str, es_motd: bool = False):
    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        if es_motd and ctx.reg("priv.motd") is not None and ctx.reg("priv.motd").sudo:
            return _ev_motd_priv(ctx, archivo)
        # Evidencia obligatoria (Audit CIS): el propio archivo; 1.7.1 recorre además /etc/motd.d/*.
        relevantes = (archivo,) + (("/etc/motd.d/",) if es_motd else ())
        ileg = ilegibles_solo(ctx, "p1.banner", relevantes) if es_motd else None
        prob = None if ileg else _prob(ctx, "p1.banner", relevantes)
        if prob:
            ev.add("banner", f"Contenido de {archivo}", ERROR, "", prob, ["p1.banner"]); return ev
        st = _stats(ctx, "p1.banner")
        archivos = [archivo] + ([f for f in {l.split('|')[1] for l in ctx.lineas('p1.banner') if l.startswith('C|/etc/motd.d/')}] if es_motd else [])
        contenido = {f: [l.split("|", 2)[2] for l in ctx.lineas("p1.banner") if l.startswith(f"C|{f}|")] for f in archivos}
        if not es_motd and st.get(archivo) is None:
            ev.add("existe", f"{archivo} existe", FALLA, "banner definido", "no existe", ["p1.banner"]); return ev
        osid = _os_id(ctx)
        pat = r"(\\[vrms])" + (r"|\b" + re.escape(osid) + r"\b" if osid else "")
        malos = [f"{f}: {t.strip()[:60]}" for f, ls in contenido.items() for t in ls if re.search(pat, t, re.I)]
        if ileg:
            # MOTD con archivos de /etc/motd.d no legibles: solo una falla demostrada en lo legible es concluyente.
            if malos:
                ev.add("info_sistema", f"{archivo} sin información del sistema", FALLA, r"sin \m \r \s \v ni ID del SO", "; ".join(malos),
                       ["p1.banner", "sys.os_release"])
            ev.add("motd_d", "Archivos de /etc/motd.d", ERROR, r"sin \m \r \s \v ni ID del SO",
                   f"{_priv(ctx, 'priv.motd')}; {_prob(ctx, 'p1.banner', relevantes)}", ["p1.banner"])
            return ev
        if es_motd and not any(contenido.values()):
            ev.add("info_sistema", "MOTD sin información del sistema", OK, r"sin \m \r \s \v ni ID del SO", "sin archivos MOTD con contenido", ["p1.banner"])
            return ev
        ev.add("info_sistema", f"{archivo} sin información del sistema", FALLA if malos else OK, r"sin \m \r \s \v ni ID del SO ('" + osid + "')",
               "; ".join(malos) or "sin información del sistema", ["p1.banner", "sys.os_release"])
        _politica_banner(ctx, ev, archivo)
        return ev
    return con_traza("p1.banner")(_ev)


def _ev_motd_priv(ctx: Ctx, archivo: str) -> Evaluacion:
    """1.7.1 con priv.motd: indicadores de TODOS los archivos de /etc/motd{,.d/*} leídos como root (Audit CIS).
    Información del sistema -> NO CUMPLE; archivo no evaluable -> ERROR; limpio sin texto aprobado -> ORG."""
    ev = Evaluacion()
    archivos, prob = motd_priv(ctx)
    if prob:
        ev.add("info_sistema", "MOTD sin información del sistema", ERROR, r"sin \m \r \s \v ni ID del SO", prob, ["priv.motd"]); return ev
    if not archivos:
        ev.add("info_sistema", "MOTD sin información del sistema", OK, r"sin \m \r \s \v ni ID del SO", "no existe ningún archivo MOTD", ["priv.motd"])
        return ev
    nombres = {"esc_v": "\\v", "esc_r": "\\r", "esc_m": "\\m", "esc_s": "\\s", "id_so": f"ID del SO ({_os_id(ctx) or '?'})"}
    malos = [f"{a['ruta']}: {', '.join(n for k, n in nombres.items() if a[k] == '1')}" for a in archivos
             if a["legible"] == "1" and any(a[k] == "1" for k in nombres)]
    no_ev = [f"{a['ruta']} ({a['tipo']}, {a['bytes']} bytes)" for a in archivos if a["legible"] != "1"]
    if malos:
        ev.add("info_sistema", "MOTD sin información del sistema", FALLA, r"sin \m \r \s \v ni ID del SO", "; ".join(malos), ["priv.motd"])
    if no_ev:
        from .comandos_priv import MOTD_MAX_BYTES
        ev.add("no_evaluables", "Archivos MOTD evaluables", ERROR, f"archivo regular <= {MOTD_MAX_BYTES} bytes",
               "[SALIDA_NO_INTERPRETABLE] no evaluables (destino no regular, enlace roto, tamaño excesivo o lectura fallida): " + "; ".join(no_ev),
               ["priv.motd"])
    if not malos and not no_ev:
        ev.add("info_sistema", "MOTD sin información del sistema", OK, r"sin \m \r \s \v ni ID del SO",
               f"{len(archivos)} archivo(s) sin información del sistema: " + ", ".join(f"{a['ruta']} ({a['tipo']})" for a in archivos), ["priv.motd"])
    # Política del sitio: separada de la evidencia técnica; el texto de /etc/motd.d no se exporta.
    _politica_banner(ctx, ev, archivo)
    return ev


def ev_acceso_banner(archivo: str):
    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        # Audit CIS: solo stat del archivo (no requiere leer su contenido): ningún #NO_LEGIBLE es bloqueante.
        prob = _prob(ctx, "p1.banner", ())
        st = _stats(ctx, "p1.banner")
        if prob or archivo not in st:
            ev.add("acceso", f"{archivo}: permisos", ERROR, "", prob or "[SALIDA_NO_INTERPRETABLE] sin stat", ["p1.banner"]); return ev
        _acceso(ev, archivo, st[archivo], 0o644, cid="p1.banner")
        return ev
    return con_traza("p1.banner")(_ev)


# ===================================================================== tiempo

def ev_chrony_en_uso(ctx: Ctx) -> Evaluacion:
    return ev_paquete_presente("chrony", "chrony")(ctx)


def ev_un_solo_demonio(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = _prob(ctx, "p1.unidades")
    if prob:
        ev.add("tiempo", "Un único demonio de sincronización", ERROR, "", prob, ["p1.unidades"]); return ev
    u = unidades(ctx, "p1.unidades")
    # chrony.service es un alias de chronyd.service en algunas distribuciones: ambos son el mismo demonio.
    nombres = ("chronyd.service", "chrony.service", "systemd-timesyncd.service")
    if not any(x in u for x in nombres):
        ev.add("tiempo", "Un único demonio de sincronización", ERROR, "",
               "[SALIDA_NO_INTERPRETABLE] p1.unidades sin estado de chronyd ni systemd-timesyncd", ["p1.unidades"]); return ev
    presentes = {x: u[x] for x in nombres if x in u and u[x][0] != "no-existe"}
    demonio = lambda x: "chrony" if x.startswith("chrony") else "systemd-timesyncd"  # noqa: E731
    en_uso = sorted({demonio(x) for x, (e, a) in presentes.items() if e == "enabled" or a == "active"})
    completos = sorted({demonio(x) for x, (e, a) in presentes.items() if e == "enabled" and a == "active"})
    estado = "; ".join(f"{x}={e}/{a}" for x, (e, a) in presentes.items()) or "ninguna unidad de sincronización presente"
    ok = len(en_uso) == 1 and completos == en_uso
    falta = ("" if ok else "ningún demonio habilitado y activo" if not en_uso else
             f"más de un demonio en uso ({', '.join(en_uso)})" if len(en_uso) > 1 else f"{en_uso[0]} no está habilitado y activo a la vez")
    ev.add("tiempo", "Exactamente un demonio (chronyd o systemd-timesyncd) habilitado y activo", OK if ok else FALLA,
           "uno habilitado y activo; el otro no habilitado ni activo", estado + (f" — {falta}" if falta else ""), ["p1.unidades"])
    return ev


def ev_chrony_configurado(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    if ctx.familia == SLES:
        # Sin estado consultable (systemd no consultable) no se asume que chrony no esté en uso: se valida la configuración.
        u = unidades(ctx, "p1.unidades").get("chronyd.service")
        if u and u[0] != "no-existe" and not (u[0] == "enabled" or u[1] == "active"):
            ev.add("chrony", "chrony configurado", NA, "-IF- chrony en uso", "chronyd no habilitado ni activo", ["p1.unidades"]); return ev
    pk = paquetes(ctx)
    if not ctx.problema("pkg.rpm") and pk.get("chrony", "x") is None:
        ev.add("chrony", "chrony configurado", NA, "-IF- chrony instalado", "chrony no instalado (ver control de sincronización en uso)", ["pkg.rpm"])
        return ev
    prob = _prob(ctx, "p1.chrony")
    if prob:
        ev.add("chrony", "Servidores de tiempo en chrony", ERROR, "", prob, ["p1.chrony"]); return ev
    t = _lineas_tag(ctx, "p1.chrony", "T")
    srv = [f"{x[2].strip()} ({x[0]})" for x in t if re.match(r"\s*(server|pool)\s+\S", x[2])]
    otros = [f"{x[2].strip()} ({x[0]})" for x in t if not re.match(r"\s*(server|pool)\s", x[2])]
    if srv:
        ev.add("chrony", "chrony con server/pool remoto", OK, "server|pool <remoto>", "; ".join(srv), ["p1.chrony"])
    elif otros:
        ev.add("chrony", "chrony con server/pool remoto", MANUAL, "server|pool <remoto>",
               "[POLITICA_O_REVISION] fuentes definidas por sourcedir/include: " + "; ".join(otros), ["p1.chrony"])
    else:
        ev.add("chrony", "chrony con server/pool remoto", FALLA, "server|pool <remoto>", "sin server/pool", ["p1.chrony"])
    return ev


# ===================================================================== red

def ipv6_habilitado(ctx: Ctx) -> Optional[bool]:
    l = ctx.lineas("p1.ipv6")
    if not l:
        return None
    if "IPV6_KERNEL|no" in l or any(x.startswith("CMDLINE|ipv6.disable=1") for x in l):
        return False
    rt = _sysctl_runtime(ctx)
    if rt.get("net.ipv6.conf.all.disable_ipv6") == "1" and rt.get("net.ipv6.conf.default.disable_ipv6") == "1":
        return False
    return True


def ev_ipv6(ctx: Ctx) -> Evaluacion:
    """CIS 3.1.1 (Manual): identificar el estado de IPv6 con el script del Audit (IPv6 NO habilitado si
    /sys/module/ipv6/parameters/disable != 0 o net.ipv6.conf.{all,default}.disable_ipv6 = 1) y contrastarlo con la
    política del sitio ("Enable or disable IPv6 in accordance with system requirements and local site policy").
    Con ipv6_politica (config/listas_aprobadas.yml): estado identificado = estado aprobado -> CUMPLE; distinto -> NO CUMPLE.
    Sin política: REQUIERE VALIDACIÓN MANUAL. Se muestran además la persistencia efectiva y las direcciones IPv6."""
    ev = Evaluacion()
    prob = _prob(ctx, "p1.ipv6")
    if prob:
        ev.add("ipv6", "Estado de IPv6 identificado", ERROR, "", prob, ["p1.ipv6"]); return ev
    hab = ipv6_habilitado(ctx)
    rt = _sysctl_runtime(ctx)
    dirs = [l.split("|", 2)[2] for l in ctx.lineas("p1.ipv6") if l.startswith("ADDR|")]
    estado = "habilitado" if hab else "deshabilitado"
    for k in ("net.ipv6.conf.all.disable_ipv6", "net.ipv6.conf.default.disable_ipv6"):
        ev.add(f"{k}_runtime", f"{k} en ejecución", INFO, "1 = IPv6 deshabilitado", rt.get(k, "sin dato"), ["p1.sysctl"])
        ef, _, _ = sysctl_efectivo(ctx, k)
        ev.add(f"{k}_persistente", f"{k} persistente (valor efectivo por precedencia de sysctl.d)", INFO, "1",
               f"{ef[1]} ({ef[0]})" if ef else "no definido en ningún archivo", ["p1.sysctl_archivos"])
    ev.add("direcciones", "Direcciones IPv6 configuradas (ip -6 addr)", INFO, "ninguna si IPv6 está deshabilitado",
           ", ".join(dirs) or "ninguna", ["p1.ipv6"])
    ev.add("estado", "Estado de IPv6 (script del Audit CIS)", INFO, "identificado", f"IPv6 {estado}", ["p1.ipv6", "p1.sysctl"])
    pol = (ctx.listas or {}).get("ipv6_politica") or {}
    aprobado = str(pol.get("estado_aprobado") or "").strip().lower()
    if aprobado not in ("habilitado", "deshabilitado"):
        ev.add("ipv6", "Estado de IPv6 identificado y conforme a la política del sitio", MANUAL, "según política del sitio",
               f"[POLITICA_O_REVISION] IPv6 {estado}; direcciones: {', '.join(dirs) or 'ninguna'}", ["p1.ipv6", "p1.sysctl"])
        return ev
    ok = aprobado == estado
    ev.add("politica", "Estado de IPv6 conforme a la política del sitio (ipv6_politica)", OK if ok else FALLA,
           f"IPv6 {aprobado} (requerido: {'sí' if pol.get('requerido') else 'no'})", f"IPv6 {estado}", ["p1.ipv6", "p1.sysctl"])
    fuente = "config/listas_aprobadas.yml · ipv6_politica" + (f" · {ctx.listas.get('decisiones_origen')}" if ctx.listas.get("decisiones_origen") else "")
    ev.mecanismo = {"fuente_politica": fuente,
                    "motivo_visible": (f"IPv6 {estado} (net.ipv6.conf.all/default.disable_ipv6 = "
                                       f"{rt.get('net.ipv6.conf.all.disable_ipv6', '?')}/{rt.get('net.ipv6.conf.default.disable_ipv6', '?')}; "
                                       f"direcciones IPv6: {', '.join(dirs) or 'ninguna'}), " + ("conforme a" if ok else "distinto de")
                                       + f" la política del sitio (IPv6 {aprobado}).")}
    return ev


def ev_wireless(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = _prob(ctx, "p1.wireless")
    if prob:
        ev.add("wireless", "Interfaces inalámbricas", ERROR, "", prob, ["p1.wireless"]); return ev
    if "W_FIN" not in ctx.lineas("p1.wireless"):
        ev.add("wireless", "Interfaces inalámbricas", ERROR, "", "[SALIDA_NO_INTERPRETABLE] salida incompleta", ["p1.wireless"]); return ev
    w = [l.split("|") for l in ctx.lineas("p1.wireless") if l.startswith("W|")]
    if not w:
        ev.add("wireless", "Sin interfaces inalámbricas", OK, "sin NIC inalámbrica o módulos no cargables", "sin interfaces inalámbricas", ["p1.wireless"])
        obs_ilegibles(ctx, ev, "p1.kmod")
        return ev
    # p1.kmod solo es evidencia necesaria si existen interfaces inalámbricas (sus módulos).
    ileg = ilegibles_solo(ctx, "p1.kmod", _ORDEN_MODPROBE)
    prob = None if ileg else _prob(ctx, "p1.kmod")
    if prob:
        ev.add("wireless", "Módulos de las interfaces inalámbricas", ERROR, "", prob, ["p1.kmod"]); return ev
    for _, iface, mod in w:
        _eval_modulo(ctx, ev, mod, disponible=True, ilegibles=tuple(ileg or ()))
        ev.observaciones.append(f"Interfaz inalámbrica {iface} (módulo {mod}).")
    return ev


def _sysctl_runtime(ctx: Ctx) -> Dict[str, str]:
    res = {}
    for l in ctx.lineas("p1.sysctl"):
        if " = " in l:
            k, v = l.split(" = ", 1)
            res[k.strip()] = v.strip()
    return res


def sysctl_persistente(ctx: Ctx, param: str) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Ocurrencias (archivo, valor) en orden de carga de systemd-sysctl y líneas con comodín/exclusión relacionadas."""
    archivo, ocurr, extra = None, [], []
    pat = re.escape(param).replace("\\.", "[./]")
    for l in ctx.lineas("p1.sysctl_archivos"):
        m = re.match(r"^\s*#\s*(/\S+)", l)
        if m:
            archivo = m.group(1); continue
        m = re.match(r"^\s*-?" + pat + r"\s*=\s*(\S+)", l)
        if m:
            ocurr.append((archivo, m.group(1))); continue
        if re.match(r"^\s*-" + pat + r"\s*$", l):
            extra.append(f"{l.strip()} en {archivo} (excluye {param} de una asignación con comodín)")
        elif "*" in l and re.search(r"[./]" + re.escape(param.rsplit(".", 1)[1]) + r"\s*=", l):
            extra.append(f"{l.strip()} en {archivo} (asignación con comodín: el Audit CIS solo evalúa el nombre exacto)")
    return ocurr, extra


def _sysctl_param(ctx: Ctx, ev: Evaluacion, param: str, esperado: str) -> None:
    if param.startswith("net.ipv6.") and ipv6_habilitado(ctx) is False:
        ev.add(param, f"{param}", NA, f"{param} = {esperado}", "IPv6 deshabilitado: el parámetro no aplica", ["p1.ipv6"]); return
    prob = _prob(ctx, "p1.sysctl")
    actual = _sysctl_runtime(ctx).get(param)
    ev.add(f"{param}_runtime", f"{param} en ejecución (RUNTIME EFECTIVO)", ERROR if prob or actual is None else (OK if actual == esperado else FALLA),
           esperado, prob or (actual if actual is not None else "[SALIDA_NO_INTERPRETABLE] sin dato"), ["p1.sysctl"])
    prob = _prob(ctx, "p1.sysctl_archivos")
    if prob:
        ev.add(f"{param}_persistente", f"{param} persistente", ERROR, esperado, prob, ["p1.sysctl_archivos"]); return
    efectivo, ocurr, avisos = sysctl_efectivo(ctx, param)
    _, extra = sysctl_persistente(ctx, param)
    ev.observaciones += extra
    if not efectivo:
        ev.add(f"{param}_persistente", f"{param} persistente", FALLA, f"{param} = {esperado} en un archivo cargado por systemd-sysctl",
               "no definido en ningún archivo", ["p1.sysctl_archivos"]); return
    if _v8(ctx):
        valores, base_txt = [efectivo[1]], "última ocurrencia en orden de carga"
    else:
        # Script CIS v2: el archivo efectivo es el último que define el nombre exacto; se validan sus valores.
        valores, base_txt = [v for a, v in ocurr if a == efectivo[0]], "archivo efectivo (último que define el parámetro)"
    ok = all(v == esperado for v in valores)
    superados = [f"{v} ({a})" for a, v in ocurr if a != efectivo[0]]
    enc = (f"PERSISTENTE EFECTIVO {efectivo[1]} en {efectivo[0]} ({base_txt})"
           + (f"; valores anteriores superados por precedencia: {', '.join(superados)}" if superados else ""))
    if avisos:
        ev.add(f"{param}_persistente", f"{param} persistente", MANUAL, f"{param} = {esperado}",
               "[ANALISIS_ESTATICO_NO_CONCLUYENTE] " + enc + " | " + "; ".join(avisos), ["p1.sysctl_archivos"])
        return
    ev.add(f"{param}_persistente", f"{param} persistente (PERSISTENTE EFECTIVO)", OK if ok else FALLA, f"{param} = {esperado}", enc,
           ["p1.sysctl_archivos"])
    if len(ocurr) > 1:
        ev.observaciones.append(f"{param} definido {len(ocurr)} veces; gana {efectivo[0]} (precedencia de systemd-sysctl): {ocurr}")


def ev_sysctl(*pares: Tuple[str, str]):
    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        if any(p.startswith("net.ipv6.") for p, _ in pares) and (_prob(ctx, "p1.ipv6") or ipv6_habilitado(ctx) is None):
            ev.add("ipv6", "Estado IPv6", ERROR, "", "[SALIDA_NO_INTERPRETABLE] sin dato de IPv6", ["p1.ipv6"]); return ev
        for p, v in pares:
            _sysctl_param(ctx, ev, p, v)
        comp = {c.id: c for c in ev.componentes}
        if ev.componentes and all(c.estado == OK for c in ev.componentes):
            partes = []
            for p, v in pares:
                rt, pe = comp.get(f"{p}_runtime"), comp.get(f"{p}_persistente")
                if rt and pe:
                    m = re.search(r"PERSISTENTE EFECTIVO (\S+) en (\S+)", pe.encontrado)
                    partes.append(f"{p}: runtime {rt.encontrado}, persistente efectivo {m.group(1)} ({m.group(2)})" if m else f"{p}: {v}")
            if partes:
                ev.mecanismo["motivo_visible"] = "; ".join(partes) + "."
        return ev
    return _ev


# ===================================================================== cron / at

def ev_cron_daemon(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    pk = paquetes(ctx)
    prob = _prob(ctx, "pkg.rpm")
    if prob or "cronie" not in pk:
        ev.add("cron", "cron instalado", ERROR, "", prob or "[SALIDA_NO_INTERPRETABLE] sin dato de rpm -q", ["pkg.rpm"]); return ev
    if not pk.get("cronie") and not pk.get("cron"):
        ev.add("cron", "cron habilitado y activo", NA, "-IF- cron instalado", "cron no instalado", ["pkg.rpm"]); return ev
    prob = _prob(ctx, "p1.unidades")
    u = unidades(ctx, "p1.unidades")
    # SLES usa cron.service; RHEL/Oracle Linux crond.service. Se toma la unidad que existe en el host (la propia de la
    # familia primero); si ninguna existe se informa la de la familia.
    cands = ("cron.service", "crond.service") if ctx.familia == SLES else ("crond.service", "cron.service")
    unidad = next((x for x in cands if x in u and u[x][0] != "no-existe"), cands[0])
    if prob or unidad not in u:
        ev.add("cron", f"{unidad} habilitado y activo", ERROR, "", prob or "[SALIDA_NO_INTERPRETABLE] sin estado", ["p1.unidades"]); return ev
    en, ac = u[unidad]
    ev.add("habilitado", f"{unidad} habilitado", OK if en == "enabled" else FALLA, "enabled", en, ["p1.unidades"])
    ev.add("activo", f"{unidad} activo", OK if ac == "active" else FALLA, "active", ac, ["p1.unidades"])
    return ev


def _con_paquete(ctx: Ctx, ev: Evaluacion, nombres: Tuple[str, ...], desc: str) -> bool:
    """True si al menos uno está instalado; si no, agrega NA. Sin dato -> ERROR (y False)."""
    pk = paquetes(ctx)
    prob = _prob(ctx, "pkg.rpm")
    if prob or not any(n in pk for n in nombres):
        ev.add("paquete", desc, ERROR, "", prob or "[SALIDA_NO_INTERPRETABLE] sin dato de rpm -q", ["pkg.rpm"]); return False
    if not any(pk.get(n) for n in nombres):
        ev.add("paquete", desc, NA, f"-IF- {'/'.join(nombres)} instalado", "no instalado", ["pkg.rpm"]); return False
    return True


def ev_cron_archivo(ruta: str, max_modo: int):
    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        if not _con_paquete(ctx, ev, ("cronie", "cron"), f"{ruta}: cron instalado"):
            return ev
        prob = _prob(ctx, "p1.cron")
        st = _stats(ctx, "p1.cron")
        if prob or ruta not in st:
            ev.add("acceso", f"{ruta}: permisos", ERROR, "", prob or "[SALIDA_NO_INTERPRETABLE] sin stat", ["p1.cron"]); return ev
        _acceso(ev, ruta, st[ruta], max_modo, cid="p1.cron")
        return ev
    return _ev


def ev_allow(servicio: str, paquetes_: Tuple[str, ...], grupos: Tuple[str, ...]):
    allow, deny = f"/etc/{servicio}.allow", f"/etc/{servicio}.deny"

    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        if not _con_paquete(ctx, ev, paquetes_, f"{servicio} instalado"):
            return ev
        prob = _prob(ctx, "p1.cron")
        st = _stats(ctx, "p1.cron")
        if prob or allow not in st or deny not in st:
            ev.add("acceso", f"{allow}/{deny}", ERROR, "", prob or "[SALIDA_NO_INTERPRETABLE] sin stat", ["p1.cron"]); return ev
        _acceso(ev, allow, st[allow], 0o640, grupos, "p1.cron", opcional=False)
        if st[deny] is not None:
            _acceso(ev, deny, st[deny], 0o640, grupos, "p1.cron")
        return ev
    return _ev


# ===================================================================== sudo / su

OBS_INSTRUMENTACION = ("Existe una regla NOPASSWD temporal creada exclusivamente para la ejecución controlada del recolector CIS. "
                       "Esta instrumentación no formaba parte del hardening evaluado y no se utiliza para determinar el resultado "
                       "del proveedor.")
_SUDO = "priv.sudo_politica"


def sudo_politica(ctx: Ctx) -> Tuple[Optional[Dict], Optional[str]]:
    """Salida normalizada de priv.sudo_politica -> dict, o (None, problema). Nunca contiene líneas de sudoers."""
    prob = _priv(ctx, _SUDO)
    if prob:
        return None, prob
    d = {"archivos": {}, "timeout": [], "nopasswd": [], "noauth": [], "use_pty": [], "instr": None, "default": None, "visudo": None}
    for l in ctx.lineas(_SUDO):
        p = l.split("|")
        kv = {x.split("=", 1)[0]: x.split("=", 1)[1] for x in p[1:] if "=" in x}
        car = kv.get("cargado") == "1"
        if p[0] == "ARCHIVO" and len(p) >= 3:
            d["archivos"][p[1]] = car
        elif p[0] == "TIMEOUT" and len(p) >= 6:
            d["timeout"].append({"valor": p[1], "alcance": p[2], "archivo": p[3], "linea": p[4], "cargado": car})
        elif p[0] in ("NOPASSWD", "NOAUTH") and len(p) >= 5:
            d["nopasswd" if p[0] == "NOPASSWD" else "noauth"].append({"archivo": p[1], "linea": p[2], "titular": p[3], "cargado": car})
        elif p[0] == "USE_PTY" and len(p) >= 5:
            d["use_pty"].append({"archivo": p[1], "presente": _num(kv.get("presente")) or 0, "negado": _num(kv.get("negado")) or 0,
                                 "cargado": car})
        elif p[0] == "INSTR":
            d["instr"] = ({"existe": True, "modo": p[2], "uid": p[3], "gid": p[4], "sha": kv.get("sha", ""),
                           "sha_archivo": kv.get("sha_archivo", ""), "tipo": kv.get("tipo", "").replace("_", " ")}
                          if kv.get("existe") == "1" and len(p) >= 6 else {"existe": False})
        elif p[0] == "TIMEOUT_DEFAULT" and len(p) >= 2:
            d["default"] = p[1]
        elif p[0] == "VISUDO" and len(p) >= 2:
            d["visudo"] = p[1]
    if d["visudo"] is None or d["instr"] is None or "/etc/sudoers" not in d["archivos"]:
        return None, f"[SALIDA_NO_INTERPRETABLE] {_SUDO}: salida incompleta"
    if d["visudo"] != "0":
        return None, f"[RC_INESPERADO] {_SUDO}: 'visudo -c' rc={d['visudo']} (sudoers con errores de sintaxis; política efectiva no determinable)"
    return d, None


def _ubic(x: Dict) -> str:
    return f"{x['archivo']}:{x['linea']} ({x['titular']})"


INSTRUMENTACION_FS = "/etc/sudoers.d/cis_audit_fs"


def instrumentacion_fs(ctx: Ctx, d: Dict) -> Dict:
    """D-3B-01 aplicada a la instrumentación TEMPORAL de filesystem profundo (/etc/sudoers.d/cis_audit_fs). Identidad
    aportada por el recolector FS (INSTR_FS: bytes exactos, tipo, modo y propietario; nunca el contenido). Exacta solo si
    coinciden ruta, archivo regular, 0440 root:root, cargado por sudo y el SHA-256 aprobado. Sin evidencia FS no es exacta."""
    esp = ((ctx.config or {}).get("instrumentacion_fs_sudoers_sha256_archivo") or "").lower()
    lin = [l for l in ctx.lineas("fsd.instrumentacion") if l.startswith("INSTR_FS|")]
    kv = dict(x.split("=", 1) for x in lin[0].split("|")[1:] if "=" in x) if lin else {}
    presente = kv.get("existe") == "True" or bool(d["archivos"].get(INSTRUMENTACION_FS) is not None and INSTRUMENTACION_FS in d["archivos"])
    motivos = []
    if not lin:
        motivos.append("sin evidencia del recolector FS (identidad no verificable)")
    elif kv.get("existe") != "True":
        motivos.append("no existe")
    else:
        if kv.get("tipo") != "regular file":
            motivos.append(f"no es un archivo regular ({kv.get('tipo')})")
        if (kv.get("modo"), kv.get("uid"), kv.get("gid")) != ("440", "0", "0"):
            motivos.append(f"modo/propietario {kv.get('modo')} {kv.get('uid')}:{kv.get('gid')} (esperado 440 0:0)")
        if not d["archivos"].get(INSTRUMENTACION_FS):
            motivos.append("no cargado por sudo")
        if not esp:
            motivos.append("SHA-256 aprobado del sudoers FS no configurado")
        elif (kv.get("sha_archivo") or "").lower() != esp:
            motivos.append("SHA-256 del archivo instalado distinto del aprobado")
    return {"archivo": INSTRUMENTACION_FS, "temporal": True, "archivo_presente": presente,
            "instrumentacion_auditoria_detectada": not motivos, "motivo_no_exacta": "; ".join(motivos) or None,
            "sudoers_sha256_archivo_aprobado": esp or None, "sudoers_sha256_archivo_encontrado": kv.get("sha_archivo") or None}


def instrumentacion(ctx: Ctx, d: Dict) -> Dict:
    """D-3B-01: identificación EXACTA y fail-closed de la instrumentación temporal de auditoría (/etc/sudoers.d/cis_audit).

    Identidad principal: SHA-256 de los BYTES EXACTOS del archivo instalado == sudoers_sha256_archivo aprobado (el archivo
    aprobado fija usuario srvinnops, digest del recolector, comando exacto y "" sin argumentos; cualquier cambio, aunque
    sea un comentario o un espacio, deja de coincidir). Además: ruta exacta, archivo regular (no symlink), cargado por
    sudo, 0440 root:root y, como comprobación adicional, el SHA semántico (contenido efectivo normalizado). Solo se
    excluye del resultado del proveedor si no existe ninguna otra regla NOPASSWD cargada."""
    from .comandos_priv import INSTRUMENTACION
    cfg = ctx.config or {}
    esp_arch = (cfg.get("instrumentacion_sudoers_sha256_archivo") or "").lower()
    esp_sem = (cfg.get("instrumentacion_sudoers_sha256_semantico") or "").lower()
    ins = d["instr"] or {"existe": False}
    motivos = []
    if not ins.get("existe"):
        motivos.append("no existe")
    else:
        if ins.get("tipo") != "regular file":
            motivos.append(f"no es un archivo regular ({ins.get('tipo') or 'tipo no informado'})")
        if not d["archivos"].get(INSTRUMENTACION):
            motivos.append("no cargado por sudo")
        if ins.get("modo") != "440" or ins.get("uid") != "0" or ins.get("gid") != "0":
            motivos.append(f"modo/propietario {ins.get('modo')} {ins.get('uid')}:{ins.get('gid')} (esperado 440 0:0)")
        if not esp_arch:
            motivos.append("SHA-256 aprobado del archivo sudoers no configurado")
        elif (ins.get("sha_archivo") or "").lower() != esp_arch:
            motivos.append("SHA-256 del archivo instalado distinto del aprobado (contenido modificado, incluidos comentarios o espacios)")
        if esp_sem and (ins.get("sha") or "").lower() != esp_sem:
            motivos.append("SHA-256 semántico distinto del aprobado")
    exacta = not motivos
    propias = [x for x in d["nopasswd"] if x["cargado"] and x["archivo"] == INSTRUMENTACION]
    fs = instrumentacion_fs(ctx, d)
    propias_fs = [x for x in d["nopasswd"] if x["cargado"] and x["archivo"] == INSTRUMENTACION_FS]
    otras = [x for x in d["nopasswd"] if x["cargado"] and x["archivo"] not in (INSTRUMENTACION, INSTRUMENTACION_FS)]
    # La regla FS (temporal, ventana de filesystem profundo) solo se acepta si su archivo es EXACTO y contiene una regla.
    fs_ok = not propias_fs or (fs["instrumentacion_auditoria_detectada"] and len(propias_fs) == 1)
    excluida = exacta and len(propias) == 1 and not otras and fs_ok
    return {"instrumentacion_auditoria_detectada": exacta, "instrumentacion_excluida_del_resultado_proveedor": excluida,
            "instrumentacion_fs": fs if (propias_fs or fs["archivo_presente"]) else None,
            "archivo": INSTRUMENTACION, "temporal": True, "archivo_presente": bool(ins.get("existe")),
            "motivo_no_exacta": "; ".join(motivos) or None,
            "motivo_no_excluida": (None if excluida else "; ".join(motivos) if not exacta else
                                   "existen otras reglas NOPASSWD cargadas" if otras else
                                   f"regla NOPASSWD en {INSTRUMENTACION_FS} no reconocida como instrumentación exacta "
                                   f"({fs['motivo_no_exacta'] or 'más de una regla'})" if not fs_ok else
                                   "la instrumentación no contiene exactamente una regla NOPASSWD"),
            "sudoers_sha256_archivo_aprobado": esp_arch or None, "sudoers_sha256_archivo_encontrado": ins.get("sha_archivo") or None,
            "sudoers_sha256_semantico_aprobado": esp_sem or None, "sudoers_sha256_semantico_encontrado": ins.get("sha") or None,
            "observacion": OBS_INSTRUMENTACION if exacta else None}


def ev_sudo_pty(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    d, prob = sudo_politica(ctx)
    if prob:
        ev.add("use_pty", "Defaults use_pty", ERROR, "Defaults use_pty", prob, [_SUDO]); return ev
    car = [x for x in d["use_pty"] if x["cargado"]]
    pres = [x["archivo"] for x in car if x["presente"]]
    neg = [x["archivo"] for x in car if x["negado"]]
    ev.add("use_pty", "Defaults use_pty en sudoers cargados", OK if pres else FALLA, "Defaults use_pty",
           f"presente en {', '.join(pres)}" if pres else "no configurado", [_SUDO])
    ev.add("no_use_pty", "Sin Defaults !use_pty", FALLA if neg else OK, "ningún !use_pty",
           f"!use_pty en {', '.join(neg)}" if neg else "ninguno", [_SUDO])
    return ev


def usuarios_sistema(ctx: Ctx) -> Optional[List[str]]:
    """Cuentas de /etc/passwd (usr.passwd), o None si la evidencia no está disponible."""
    if ctx.problema("usr.passwd") or not ctx.lineas("usr.passwd"):
        return None
    return sorted({l.split(":", 1)[0] for l in ctx.lineas("usr.passwd") if ":" in l})


def universo(descripcion: str, fuente: str, excluido_si: str, usuarios: Dict[str, Dict], existentes: Optional[List[str]],
             limitaciones: Optional[List[str]] = None) -> Dict:
    """Configuración real por usuario para validar exclusiones de usuarios (resultado_final / validacion_exclusiones).

    usuarios: {usuario: {"estado": "INCUMPLE" | "CUMPLE" | "FUERA_POBLACION", "detalle": texto}}. Un usuario excluido está
    EFECTIVAMENTE exceptuado si su configuración real es la que la política CIS no permite (INCUMPLE)."""
    return {"descripcion": descripcion, "fuente": fuente, "excluido_si": excluido_si, "usuarios": usuarios,
            "existentes": existentes, "limitaciones": limitaciones or []}


def ev_sudo_nopasswd(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    d, prob = sudo_politica(ctx)
    if prob:
        ev.add("nopasswd", "Reglas NOPASSWD en sudoers", ERROR, "ninguna regla NOPASSWD", prob, [_SUDO]); return ev
    ins = instrumentacion(ctx, d)
    car = [x for x in d["nopasswd"] if x["cargado"]]
    # Excluida (D-3B-01) solo si TODO es exacto; en ese caso la regla FS temporal, si existe, también es exacta.
    propias = (ins["archivo"], INSTRUMENTACION_FS) if ins["instrumentacion_excluida_del_resultado_proveedor"] else ()
    evaluadas = [x for x in car if x["archivo"] not in propias]
    c = ev.add("nopasswd", "Reglas NOPASSWD en sudoers cargados", FALLA if evaluadas else OK, "ninguna regla NOPASSWD",
               "; ".join(_ubic(x) for x in evaluadas) or
               ("ninguna (salvo la instrumentación temporal de auditoría, excluida)" if ins["instrumentacion_excluida_del_resultado_proveedor"]
                else "ninguna"), [_SUDO])
    c.elementos = [{"usuario": x["titular"], "archivo": x["archivo"], "linea": x["linea"]} for x in evaluadas]
    no_car = [x for x in d["nopasswd"] if not x["cargado"]]
    if no_car:
        ev.observaciones.append("NOPASSWD en archivos no cargados por sudo (no efectivos): " + "; ".join(_ubic(x) for x in no_car))
    exist = usuarios_sistema(ctx)
    uni: Dict[str, Dict] = {u: {"estado": "CUMPLE", "detalle": "sin regla NOPASSWD en los sudoers cargados"} for u in exist or []}
    for x in no_car:
        if x["titular"] in uni:
            uni[x["titular"]]["detalle"] = f"regla NOPASSWD solo en un archivo NO cargado por sudo ({_ubic(x)}): no es efectiva"
    for x in car:
        prev = uni.get(x["titular"], {}).get("estado") == "INCUMPLE"
        uni[x["titular"]] = {"estado": "INCUMPLE", "detalle": ((uni[x["titular"]]["detalle"] + "; ") if prev else "")
                             + f"regla NOPASSWD efectiva en {_ubic(x)}"}
    ev.mecanismo["universo_exclusion"] = universo(
        "Reglas NOPASSWD de sudo por titular", "priv.sudo_politica (sudoers cargados por sudo) + /etc/passwd",
        "el titular conserva una regla NOPASSWD efectiva (sudo sin contraseña)", uni, exist,
        ["sudo aplica la última regla que coincide: el recolector informa las reglas NOPASSWD, no el orden completo de todas "
         "las reglas; una regla posterior sin NOPASSWD para el mismo titular no se detecta."])
    if ins["instrumentacion_auditoria_detectada"]:
        ev.observaciones.append(OBS_INSTRUMENTACION)
        if not ins["instrumentacion_excluida_del_resultado_proveedor"]:
            ev.observaciones.append(f"Exclusión de la instrumentación NO aplicada: {ins['motivo_no_excluida']}.")
    elif ins["archivo_presente"]:
        ev.observaciones.append(f"{ins['archivo']} existe pero no coincide con la instrumentación aprobada ({ins['motivo_no_exacta']}); "
                                "se evalúa como regla normal.")
    fs = ins.get("instrumentacion_fs")
    if fs and fs["instrumentacion_auditoria_detectada"]:
        ev.observaciones.append(f"Instrumentación temporal de filesystem profundo ({INSTRUMENTACION_FS}) idéntica a la aprobada"
                                + ("; excluida del resultado del proveedor." if propias else "; exclusión NO aplicada."))
    elif fs:
        ev.observaciones.append(f"{INSTRUMENTACION_FS} presente pero no reconocida como instrumentación exacta "
                                f"({fs['motivo_no_exacta']}); se evalúa como regla normal.")
    ev.mecanismo["instrumentacion_auditoria"] = ins
    return ev


def ev_sudo_noauth(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    d, prob = sudo_politica(ctx)
    if prob:
        ev.add("noauth", "Reglas !authenticate en sudoers", ERROR, "ninguna regla !authenticate", prob, [_SUDO]); return ev
    car = [x for x in d["noauth"] if x["cargado"]]
    ev.add("noauth", "Reglas !authenticate en sudoers cargados", FALLA if car else OK, "ninguna regla !authenticate",
           "; ".join(_ubic(x) for x in car) or "ninguna", [_SUDO])
    return ev


def _float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def timestamp_timeout(d: Dict) -> Tuple[str, Optional[float], List[Dict]]:
    """D-3B-02 -> (origen EXPLICITO|DEFAULT_SUDO|NO_DETERMINADO, valor global efectivo en minutos, valores explícitos cargados).

    Precedencia: el último Defaults global en orden de carga (/etc/sudoers y luego /etc/sudoers.d en orden léxico)."""
    car = [x for x in d["timeout"] if x["cargado"]]
    orden = lambda x: (x["archivo"] != "/etc/sudoers", x["archivo"], _num(x["linea"]) or 0)
    glob = sorted([x for x in car if x["alcance"] == "global"], key=orden)
    if glob:
        return "EXPLICITO", _float(glob[-1]["valor"]), car
    m = re.fullmatch(r"(-?[0-9]+(?:\.[0-9]+)?) minutes?", (d.get("default") or "").strip())
    if m:
        return "DEFAULT_SUDO", float(m.group(1)), car
    return "NO_DETERMINADO", None, car


def ev_sudo_timeout(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    d, prob = sudo_politica(ctx)
    if prob:
        ev.add("timeout", "timestamp_timeout", ERROR, "<= 15 y != -1", prob, [_SUDO]); return ev
    origen, valor, car = timestamp_timeout(d)
    ok = lambda v: v is not None and v != -1 and 0 <= v <= 15
    ev.mecanismo = {"origen_timestamp_timeout": origen, "valor_timestamp_timeout": valor}
    ev.observaciones.append(f"origen_timestamp_timeout={origen}; valor_timestamp_timeout={valor if valor is not None else 'NO_DETERMINADO'}")
    if origen == "NO_DETERMINADO":
        ev.add("timeout", "timestamp_timeout (sin valor explícito)", MANUAL, "<= 15 y != -1",
               "[ANALISIS_ESTATICO_NO_CONCLUYENTE] sin timestamp_timeout explícito y valor por defecto de 'sudo -V' no determinable "
               f"({d.get('default') or 'sin salida'})", [_SUDO]); return ev
    desc = "timestamp_timeout efectivo (explícito)" if origen == "EXPLICITO" else "timestamp_timeout efectivo (valor por defecto de sudo -V)"
    ev.add("timeout", desc, OK if ok(valor) else FALLA, "<= 15 y != -1", f"{valor:g} minutos ({origen})" if valor is not None else
           "no interpretable", [_SUDO])
    malos = [x for x in car if not ok(_float(x["valor"]))]
    if car:
        ev.add("timeout_todos", "Todos los timestamp_timeout configurados (global y con alcance)", FALLA if malos else OK, "<= 15 y != -1",
               "; ".join(f"{x['valor']} en {x['archivo']}:{x['linea']} ({x['alcance']})" for x in (malos or car)), [_SUDO])
    return ev


def ev_sudo_logfile(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = ctx.problema("auditd.sudo_logfile")
    if prob:
        ev.add("logfile", "Defaults logfile en sudoers", ERROR, "", prob, ["auditd.sudo_logfile"]); return ev
    l = [x for x in ctx.lineas("auditd.sudo_logfile") if "logfile" in x]
    ev.add("logfile", "Defaults logfile en sudoers", OK if l else FALLA, 'Defaults logfile="<ruta>"', "; ".join(l) or "no configurado",
           ["auditd.sudo_logfile"])
    return ev


def _grupos(ctx: Ctx) -> Dict[str, List[str]]:
    res = {}
    for l in ctx.lineas("usr.group"):
        p = l.split(":")
        if len(p) >= 3:
            res[p[0]] = [x for x in p[2].split(",") if x]
    return res


def ev_su(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    for cid in ("p1.pam_su", "usr.group"):
        prob = _prob(ctx, cid)
        if prob:
            ev.add("su", "pam_wheel en /etc/pam.d/su", ERROR, "", prob, [cid]); return ev
    w = [t for _, _, t in _lineas_tag(ctx, "p1.pam_su", "W")
         if re.match(r"\s*auth\s+(required|requisite)\s+pam_wheel\.so\b", t) and re.search(r"\buse_uid\b", t) and re.search(r"\bgroup=\S+", t)]
    if not w:
        ev.add("pam_wheel", "auth required pam_wheel.so use_uid group=<grupo>", FALLA, "línea presente", "no configurado", ["p1.pam_su"])
        return ev
    g = re.search(r"\bgroup=(\S+)", w[0]).group(1)
    ev.add("pam_wheel", "auth required pam_wheel.so use_uid group=<grupo>", OK, "línea presente", w[0].strip(), ["p1.pam_su"])
    gr = _grupos(ctx)
    if g not in gr:
        ev.add("grupo", f"Grupo {g} existe y está vacío", FALLA, f"{g} existe sin miembros", "el grupo no existe", ["usr.group"])
    else:
        c = ev.add("grupo", f"Grupo {g} sin miembros", FALLA if gr[g] else OK, "sin miembros", ", ".join(gr[g]) or "sin miembros", ["usr.group"])
        c.elementos = [{"usuario": u} for u in gr[g]]
        exist = usuarios_sistema(ctx)
        uni = {u: {"estado": "CUMPLE", "detalle": f"no es miembro del grupo {g}: su le está restringido"} for u in exist or []}
        uni.update({u: {"estado": "INCUMPLE", "detalle": f"miembro del grupo {g} (pam_wheel use_uid group={g}): puede usar su"}
                    for u in gr[g]})
        ev.mecanismo["universo_exclusion"] = universo(
            f"Miembros del grupo {g} autorizado por pam_wheel para su", "/etc/pam.d/su + /etc/group + /etc/passwd",
            f"el usuario es miembro del grupo {g} (puede usar su)", uni, exist,
            ["Se evalúan los miembros secundarios declarados en /etc/group; un usuario cuyo grupo primario es el grupo de su "
             "no figura en esa lista."])
    return ev


# ===================================================================== PAM

def _pam_archivos(ctx: Ctx) -> Tuple[str, ...]:
    return (("/etc/pam.d/common-auth", "/etc/pam.d/common-account", "/etc/pam.d/common-password")
            if ctx.familia == SLES else ("/etc/pam.d/system-auth", "/etc/pam.d/password-auth"))


def _pam_lineas(ctx: Ctx) -> List[Tuple[str, int, str]]:
    return _lineas_tag(ctx, "p1.pam", "P")


def _pam_presentes(ctx: Ctx) -> List[str]:
    return [l[2:] for l in ctx.lineas("p1.pam") if l.startswith("F|")]


# Evidencia de la pila PAM (siempre necesaria para los controles PAM); los .conf de /etc/security solo para su control.
_PAM_PILA = ("/etc/pam.d/", "/usr/lib/pam.d/", "/usr/etc/pam.d/", "/etc/authselect/")


def _pam_base(ctx: Ctx, ev: Evaluacion, desc: str, conf: Tuple[str, ...] = ()) -> bool:
    """conf: archivos de /etc/security que el control necesita (p. ej. ('/etc/security/faillock.conf',)); un archivo
    no legible fuera de la pila PAM y de `conf` no bloquea el control."""
    prob = _prob(ctx, "p1.pam", _PAM_PILA + conf)
    if prob:
        ev.add("pam", desc, ERROR, "", prob, ["p1.pam"]); return False
    faltan = [f for f in _pam_archivos(ctx) if f not in _pam_presentes(ctx)]
    if faltan:
        ev.add("pam", desc, ERROR, "", f"[SALIDA_NO_INTERPRETABLE] no se encontraron {faltan}", ["p1.pam"]); return False
    return True


def _modulo_pam(ctx: Ctx, archivo: str, tipo: str, modulo: str) -> List[str]:
    return [t for f, _, t in _pam_lineas(ctx) if f == archivo and re.match(r"\s*-?" + tipo + r"\s", t) and re.search(r"\b" + re.escape(modulo) + r"\b", t)]


def ev_pam_modulo(modulo: str, tipos: Tuple[str, ...]):
    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        if not _pam_base(ctx, ev, f"{modulo} habilitado"):
            return ev
        for f in _pam_archivos(ctx):
            for t in tipos:
                l = _modulo_pam(ctx, f, t, modulo)
                ev.add(f"{f}_{t}", f"{modulo} en {f} ({t})", OK if l else FALLA, f"{t} ... {modulo}", "; ".join(x.strip() for x in l) or "ausente", ["p1.pam"])
        return ev
    return con_traza("p1.pam")(_ev)


@con_traza("p1.pam")
def ev_authselect_modulos(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = _prob(ctx, "p1.pam", _PAM_PILA)
    if prob:
        ev.add("authselect", "Perfil authselect", ERROR, "", prob, ["p1.pam"]); return ev
    conf = next((l.split("|", 1)[1] for l in ctx.lineas("p1.pam") if l.startswith("AUTHSELECT_CONF|")), None)
    if not conf:
        ev.add("authselect", "Perfil authselect personalizado activo", FALLA, "custom/<perfil>", "authselect no configurado", ["p1.pam"]); return ev
    if not conf.startswith("custom/"):
        ev.add("authselect", "Perfil authselect personalizado activo", FALLA, "custom/<perfil>", conf, ["p1.pam"]); return ev
    ev.add("authselect", "Perfil authselect personalizado activo", OK, "custom/<perfil>", conf, ["p1.pam"])
    t = _lineas_tag(ctx, "p1.pam", "T")
    for a in ("system-auth", "password-auth"):
        for m in ("pam_pwquality.so", "pam_pwhistory.so", "pam_faillock.so", "pam_unix.so"):
            ok = any(f.endswith("/" + a) and m in x for f, _, x in t)
            ev.add(f"{a}_{m}", f"{m} en la plantilla {a} del perfil", OK if ok else FALLA, m, "presente" if ok else "ausente", ["p1.pam"])
    return ev


def _orden_lectura(base: str, entradas: List[Tuple[str, int, str, str]]) -> List[Tuple[str, str, str]]:
    """Orden de PRECEDENCIA (el último gana) según el Audit CIS OL9 v2.0.0 (5.3.3.2.x, Notes): los .conf de
    /etc/security/<base>.conf.d se leen en orden canónico (gana el último leído) y /etc/security/<base>.conf PREVALECE
    sobre ellos; los argumentos del módulo PAM prevalecen sobre ambos (se evalúan aparte, _arg_pam).
    Corrige el defecto latente previo, que dejaba ganar a conf.d sobre el archivo principal."""
    principal = f"/etc/security/{base}.conf"
    orden = sorted(entradas, key=lambda x: (x[0] == principal, x[0], x[1]))
    return [(f, k, v) for f, _, k, v in orden]


def _conf_sec(ctx: Ctx, base: str) -> List[Tuple[str, str, str]]:
    """[(archivo, clave, valor)] de /etc/security/<base>.conf y <base>.conf.d en orden de precedencia (el último gana).
    Para pwquality usa priv.pwquality (1.3.0) si existe como evidencia privilegiada."""
    if base == "pwquality" and _pwq_privilegiado(ctx):
        ent, _ = pwquality_priv(ctx)
        return _orden_lectura(base, ent or [])
    res = []
    for f, n, t in _lineas_tag(ctx, "p1.pam", "S"):
        if f == f"/etc/security/{base}.conf" or f.startswith(f"/etc/security/{base}.conf.d/"):
            m = re.match(r"\s*([a-z_]+)\s*(?:=\s*(\S+))?", t)
            if m:
                res.append((f, _num(n) or 0, m.group(1), m.group(2) if m.group(2) is not None else ""))
    return _orden_lectura(base, res)


def _pwq_privilegiado(ctx: Ctx) -> bool:
    r = ctx.reg("priv.pwquality")
    return r is not None and r.sudo


def pwquality_priv(ctx: Ctx) -> Tuple[Optional[List[Tuple[str, int, str, str]]], Optional[str]]:
    """priv.pwquality (1.3.0) -> ([(archivo, línea, clave, valor)], problema). Formato cerrado: solo las 6 claves CIS;
    un archivo no procesable (PE), una línea inesperada o la salida truncada invalidan la evidencia."""
    from .comandos_priv import CLAVES_PWQUALITY_1_3
    cid = "priv.pwquality"
    prob = _priv(ctx, cid)
    if prob:
        return None, prob
    lin = ctx.lineas(cid)
    if not lin or lin[-1] != "PQ_FIN" or lin.count("PQ_FIN") != 1:
        return None, f"[SALIDA_NO_INTERPRETABLE] {cid}: salida incompleta o truncada (sin PQ_FIN)"
    ok_ruta = lambda f: f == "/etc/security/pwquality.conf" or (f.startswith("/etc/security/pwquality.conf.d/") and f.endswith(".conf"))
    res, no_proc = [], []
    for l in lin[:-1]:
        p = l.split("|")
        if p[0] == "PF" and len(p) == 2 and ok_ruta(p[1]):
            continue
        if p[0] == "PE" and len(p) == 3 and ok_ruta(p[1]):
            no_proc.append(f"{p[1]} ({p[2]})"); continue
        if p[0] == "PQ" and len(p) == 5 and ok_ruta(p[1]) and p[2].isdigit() and p[3] in CLAVES_PWQUALITY_1_3 \
                and (re.fullmatch(r"-?[0-9]+", p[4]) or p[4] == "NO_NUMERICO"):
            res.append((p[1], int(p[2]), p[3], p[4])); continue
        return None, f"[SALIDA_NO_INTERPRETABLE] {cid}: línea no válida"
    if no_proc:
        return None, f"[SALIDA_NO_INTERPRETABLE] {cid}: archivos no procesables: {', '.join(no_proc)}"
    return res, None


def _pwq_base(ctx: Ctx, ev: Evaluacion, desc: str) -> bool:
    """Base de los controles pwquality: pila PAM + (priv.pwquality si existe; si no, la lectura sin privilegios)."""
    if _pwq_privilegiado(ctx):
        if not _pam_base(ctx, ev, desc):            # la pila PAM sigue siendo obligatoria
            return False
        _, prob = pwquality_priv(ctx)
        if prob:
            ev.add("pwquality", desc, ERROR, "", prob, ["priv.pwquality"]); return False
        return True
    if not _pam_base(ctx, ev, desc, ("/etc/security/pwquality.conf",)):
        if ilegibles_solo(ctx, "p1.pam", _PAM_PILA + ("/etc/security/pwquality.conf",)):
            ev.componentes[-1].encontrado += f" | {_priv(ctx, 'priv.pwquality')}"
        return False
    return True


def _arg_pam(ctx: Ctx, modulo: str, arg: str) -> List[Tuple[str, str]]:
    res = []
    for f, _, t in _pam_lineas(ctx):
        if f in _pam_archivos(ctx) and modulo in t:
            m = re.search(r"\b" + arg + r"\s*=\s*(\S+)", t)
            if m:
                res.append((f, m.group(1)))
    return res


def ev_pam_valor(base: str, modulo: str, clave: str, ok, desc_esperado: str, solo_args_sles: bool = False):
    """Valor en /etc/security/<base>.conf(.d) y argumentos del módulo PAM (que tienen precedencia).

    El Audit exige el valor en el .conf y que ningún argumento PAM lo contradiga. solo_args_sles: en SLES el
    Audit (pam_pwhistory) verifica el argumento del módulo en common-password."""
    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        base_ok = (_pwq_base(ctx, ev, f"{clave} ({base})") if base == "pwquality"
                   else _pam_base(ctx, ev, f"{clave} ({base})", (f"/etc/security/{base}.conf",)))
        if not base_ok:
            # Sin el .conf: solo un argumento PAM que contradiga el requisito es concluyente (falla demostrada).
            if ilegibles_solo(ctx, "p1.pam", _PAM_PILA + (f"/etc/security/{base}.conf",)) and not any(
                    x.startswith(_PAM_PILA) for x in no_legibles(ctx, "p1.pam")):
                for f, v in _arg_pam(ctx, modulo, clave):
                    if not ok(v):
                        ev.add(f"arg_{f}", f"{clave} como argumento de {modulo} en {f} (precedencia sobre el .conf)", FALLA,
                               desc_esperado, v, ["p1.pam"])
            return ev
        conf = [x for x in _conf_sec(ctx, base) if x[1] == clave]
        args = _arg_pam(ctx, modulo, clave)
        if solo_args_sles and ctx.familia == SLES:
            if not args and not conf:
                ev.add("arg", f"{clave} en {modulo}", FALLA, desc_esperado, "no definido", ["p1.pam"])
        else:
            val = conf[-1][2] if conf else None
            fuente = ["priv.pwquality"] if base == "pwquality" and _pwq_privilegiado(ctx) else ["p1.pam"]
            if val == "NO_NUMERICO":
                ev.add("conf", f"{clave} en /etc/security/{base}.conf(.d)", ERROR, desc_esperado,
                       f"[SALIDA_NO_INTERPRETABLE] valor no numérico ({conf[-1][0]})", fuente)
            else:
                desc = (f"{clave} en /etc/security/{base}.conf(.d) (valor efectivo)" if base == "pwquality"
                        else f"{clave} en /etc/security/{base}.conf")     # texto sin cambios para faillock/pwhistory
                ev.add("conf", desc, OK if conf and ok(val) else FALLA, desc_esperado,
                       f"{val} ({conf[-1][0]})" if conf else "no definido", fuente)
            if len(conf) > 1:
                ev.observaciones.append(f"{clave} definido en {len(conf)} ubicaciones (el CIS recomienda una sola); precedencia CIS "
                                        f"(pwquality.conf > conf.d canónico, último gana): " + "; ".join(f"{v} ({f})" for f, _, v in conf)
                                        + f" -> efectivo {val} ({conf[-1][0]})")
        for f, v in args:
            ev.add(f"arg_{f}", f"{clave} como argumento de {modulo} en {f} (precedencia sobre el .conf)", OK if ok(v) else FALLA,
                   desc_esperado, v, ["p1.pam"])
        return ev
    return con_traza("p1.pam")(_ev)


_CLAVES_COMPLEJIDAD = ("minclass", "dcredit", "ucredit", "lcredit", "ocredit")


@con_traza("p1.pam")
def ev_complejidad(ctx: Ctx) -> Evaluacion:
    """5.3.3.2.3 (Manual en el CIS). Criterio técnico del Audit: minclass no < 4 y [dulo]credit no >= 0 (valor efectivo
    del .conf y argumentos PAM, que prevalecen). El CIS acepta minclass -- AND/OR -- créditos: con créditos negativos no
    se exige minclass. Una violación técnica es NO CUMPLE. Si técnicamente cumple, la conformidad con la política del
    sitio exige `pwquality_complejidad_aprobada` (config/listas_aprobadas.yml): sin ella, REQUIERE VALIDACIÓN MANUAL.
    Cada clave se muestra como componente (OK/FALLA)."""
    ev = Evaluacion()
    if not _pwq_base(ctx, ev, "complejidad de contraseña"):
        return ev
    fuente = ["priv.pwquality" if _pwq_privilegiado(ctx) else "p1.pam", "p1.pam"]
    conf = _conf_sec(ctx, "pwquality")
    efectivo = {}
    for k in _CLAVES_COMPLEJIDAD:
        c = [x for x in conf if x[1] == k]
        if c:
            efectivo[k] = (c[-1][2], c[-1][0])
        for f, v in _arg_pam(ctx, "pam_pwquality", k):     # los argumentos del módulo prevalecen
            efectivo[k] = (v, f"argumento pam_pwquality en {f}")
    if any(v == "NO_NUMERICO" or _num(v) is None for v, _ in efectivo.values()):
        ev.add("complejidad", "Complejidad de contraseña (valores efectivos)", ERROR, "valores enteros",
               "[SALIDA_NO_INTERPRETABLE] " + "; ".join(f"{k}={v} ({o})" for k, (v, o) in efectivo.items()), fuente); return ev
    ent = {k: int(v) for k, (v, _) in efectivo.items()}
    desc = "; ".join(f"{k}={v} ({o})" for k, (v, o) in efectivo.items()) or "sin minclass ni credits configurados"
    pol = (ctx.listas or {}).get("pwquality_complejidad_aprobada") or {}
    hay_creditos = any(k in ent for k in _CLAVES_COMPLEJIDAD[1:])
    for k in _CLAVES_COMPLEJIDAD:
        if k == "minclass":
            if k in ent:
                ev.add("tecnico_minclass", "minclass no menor que 4 (Audit CIS)", FALLA if ent[k] < 4 else OK, ">= 4",
                       f"{ent[k]} ({efectivo[k][1]})", fuente)
            continue
        if k in ent:
            ok = ent[k] < 0 and (k not in pol or ent[k] <= int(pol[k]))
            ev.add(f"tecnico_{k}", f"{k} negativo (Audit CIS: no 0 o mayor)" + (f"; política {pol[k]}" if k in pol else ""),
                   OK if ok else FALLA, f"< 0" + (f" y <= {pol[k]}" if k in pol else ""), f"{ent[k]} ({efectivo[k][1]})", fuente)
        elif k in pol:
            ev.add(f"tecnico_{k}", f"{k} definido según la política ({pol[k]})", FALLA, f"{k} = {pol[k]}", "no definido", fuente)
    if not ent:
        # Sin claves: ningún valor contradice el Audit (minclass no < 4 / credits no >= 0 solo se evalúan si existen); la
        # conformidad con la política decide (sin política: MANUAL; con política: las claves exigidas faltan -> FALLA).
        ev.add("tecnico", "Complejidad: minclass no < 4 y [dulo]credit no >= 0 (Audit CIS)", OK, "minclass >= 4; credits < 0",
               "sin minclass ni credits configurados", fuente)
    if any(c.estado == FALLA for c in ev.componentes):
        return ev
    if pol:
        ev.add("politica", "Complejidad conforme a la política aprobada (pwquality_complejidad_aprobada)", OK,
               ", ".join(f"{k}={v}" for k, v in pol.items()), desc + (" (minclass no exigido: créditos negativos)" if hay_creditos and "minclass" not in ent else ""),
               fuente)
        ev.mecanismo["fuente_politica"] = "config/listas_aprobadas.yml · pwquality_complejidad_aprobada" + (
            f" · {ctx.listas.get('decisiones_origen')}" if ctx.listas.get("decisiones_origen") else "")
    else:
        estado = MANUAL if ctx.familia == RHEL else ORG
        ev.add("politica", "Complejidad conforme a la política del sitio", estado, "pwquality_complejidad_aprobada (config/listas_aprobadas.yml)",
               ("[POLITICA_O_REVISION] " if estado == MANUAL else "[EVIDENCIA_ORGANIZACIONAL] ") + f"política no definida; efectivos: {desc}",
               fuente)
    return ev


@con_traza("p1.pam")
def ev_pam_unix_hash(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    if not _pam_base(ctx, ev, "pam_unix hash"):
        return ev
    for f in _pam_archivos(ctx):
        if ctx.familia == SLES and not f.endswith("password"):
            continue
        l = _modulo_pam(ctx, f, "password", "pam_unix.so")
        ok = bool(l) and all(re.search(r"\b(sha512|yescrypt)\b", x) for x in l)
        ev.add(f, f"pam_unix (password) en {f} con sha512 o yescrypt", OK if ok else FALLA, "sha512|yescrypt",
               "; ".join(x.strip() for x in l) or "pam_unix ausente", ["p1.pam"])
    return ev


# ===================================================================== login.defs / envejecimiento

def login_defs(ctx: Ctx) -> Dict[str, Tuple[str, str]]:
    """{CLAVE: (valor, archivo)}; /etc/login.defs prevalece sobre /usr/etc/login.defs (SLES)."""
    res = {}
    for f, _, t in sorted(_lineas_tag(ctx, "p1.login_defs", "D"), key=lambda x: x[0] == "/etc/login.defs"):
        m = re.match(r"\s*([A-Z_]+)\s*[= ]\s*(\S+)", t)
        if m:
            res[m.group(1)] = (m.group(2), f)
    return res


_CTAS = "priv.cuentas"


def cuentas_shadow(ctx: Ctx) -> Tuple[Optional[Dict], Optional[str]]:
    """priv.cuentas -> ({usuario: campos}, hoy, inactive_default) sin hash; o (None, problema)."""
    prob = _priv(ctx, _CTAS)
    if prob:
        return None, prob
    u, hoy, ina = {}, None, None
    for l in ctx.lineas(_CTAS):
        p = l.split("|")
        if p[0] == "U" and len(p) >= 11:
            kv = {x.split("=", 1)[0]: x.split("=", 1)[1] for x in p[2:5] if "=" in x}
            u[p[1]] = {"hash": kv.get("hash") == "1", "bloqueada": kv.get("bloqueada") == "1", "vacia": kv.get("vacia") == "1",
                       "last": p[5], "min": p[6], "max": p[7], "warn": p[8], "inactive": p[9], "expire": p[10]}
        elif p[0] == "HOY" and len(p) >= 2:
            hoy = _num(p[1])
        elif p[0] == "INACTIVE_DEFAULT" and len(p) >= 3:
            ina = (p[1], p[2])
    if not u or hoy is None or ina is None:
        return None, f"[SALIDA_NO_INTERPRETABLE] {_CTAS}: salida incompleta"
    return {"usuarios": u, "hoy": hoy, "inactive_default": ina}, None


def _ld_comp(ctx: Ctx, ev: Evaluacion, clave: str, ok, esperado: str) -> None:
    rel = ("/etc/default/useradd",) if clave == "INACTIVE" else ("/etc/login.defs", "/usr/etc/login.defs")
    if clave == "INACTIVE":
        c, prob_c = cuentas_shadow(ctx)
        if c:
            val, origen = c["inactive_default"]
            val = "-1" if val == "NO_DEFINIDO" else val
            ev.add(clave, "INACTIVE por defecto (/etc/default/useradd)", OK if ok(val) else FALLA, esperado,
                   f"{val} ({origen})" if origen != "NO_EXISTE" else "-1 (no existe /etc/default/useradd: valor por defecto)", [_CTAS])
            return
    prob = _prob(ctx, "p1.login_defs", rel)
    if prob:
        if clave == "INACTIVE" and base_motivo(prob) == "REQUIERE_EXTENSION_RECOLECTOR":
            prob = prob_c
        ev.add(clave, f"{clave}", ERROR, esperado, prob, ["p1.login_defs"]); return
    v = login_defs(ctx).get(clave)
    if clave == "INACTIVE":
        val = v[0] if v else "-1"
        ev.add(clave, "INACTIVE por defecto (/etc/default/useradd)", OK if ok(val) else FALLA, esperado,
               f"{val} ({v[1]})" if v else "-1 (no definido: valor por defecto)", ["p1.login_defs"])
        return
    ev.add(clave, f"{clave} en login.defs", OK if v and ok(v[0]) else FALLA, esperado, f"{v[0]} ({v[1]})" if v else "no definido",
           ["p1.login_defs"])


def ev_envejecimiento(clave: str, ok, esperado: str, campo_shadow: str, campo: str, ok_cuenta):
    """login.defs/useradd + campo por cuenta con contraseña (hash), según el Audit CIS (campo vacío = no cumple)."""
    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        _ld_comp(ctx, ev, clave, ok, esperado)
        c, prob = cuentas_shadow(ctx)
        if prob:
            ev.add("cuentas", f"{campo_shadow} de cada cuenta con contraseña", ERROR, esperado, prob, [_CTAS]); return ev
        c_ = c
        con = {n: x for n, x in c["usuarios"].items() if x["hash"]}
        malas = [(n, x[campo] or "vacío") for n, x in sorted(con.items()) if not ok_cuenta(_num(x[campo]))]
        c = ev.add("cuentas", f"{campo_shadow} de cada cuenta con contraseña ({len(con)})", FALLA if malas else OK, esperado,
                   ", ".join(f"{n}={v}" for n, v in malas) or (f"todas conformes ({len(con)})" if con else "ninguna cuenta con contraseña"),
                   [_CTAS])
        c.elementos = [{"usuario": n, "valor": v} for n, v in malas]
        mal = dict(malas)
        uni = {n: ({"estado": "INCUMPLE", "detalle": f"{campo_shadow} = {mal[n]} en /etc/shadow (fuera de {esperado})"} if n in mal else
                   {"estado": "CUMPLE", "detalle": f"{campo_shadow} = {x[campo]} en /etc/shadow (dentro de {esperado})"}) if x["hash"] else
               {"estado": "FUERA_POBLACION", "detalle": "cuenta sin contraseña (sin hash): fuera de la población del control CIS"}
               for n, x in sorted(c_["usuarios"].items())}
        ev.mecanismo["universo_exclusion"] = universo(
            f"{campo_shadow} por cuenta", "/etc/shadow minimizado (priv.cuentas, sin hash)",
            f"la cuenta conserva un {campo_shadow} fuera de la política CIS ({esperado})", uni, sorted(c_["usuarios"]))
        if campo == "inactive":
            ev.add("poblacion", "Población CIS: cuentas con contraseña ($2 ~ /^\\$.+\\$/)", INFO, "todas las cuentas con hash",
                   ", ".join(f"{n}={x[campo] or 'vacío'}" for n, x in sorted(con.items())) or "ninguna", [_CTAS])
            if malas:
                ev.mecanismo["motivo_visible"] = (
                    "INACTIVE por defecto conforme, pero hay cuentas con contraseña cuyo campo de inactividad no está entre 0 y 45: "
                    + ", ".join(f"{n}={v}" for n, v in malas) + ". El CIS exige el valor por defecto Y el de cada cuenta con contraseña.")
                ev.observaciones.append(
                    "Audit CIS 5.4.1.5: 'Verify all users with a password have Password inactive no more than 45 days'. Un INACTIVE "
                    "por defecto conforme no basta: el criterio incluye toda cuenta con contraseña. Una excepción por usuario solo puede "
                    "provenir de la columna 'Exclusiones de Infraestructura' del Excel P1 y solo afecta el resultado final.")
        return ev
    return _ev


def ev_hash_login_defs(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    _ld_comp(ctx, ev, "ENCRYPT_METHOD", lambda v: v.upper() in ("SHA512", "YESCRYPT"), "SHA512 o YESCRYPT")
    return ev


def ev_ultimo_cambio(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    c, prob = cuentas_shadow(ctx)
    if prob:
        ev.add("ultimo_cambio", "Fecha de último cambio de contraseña en el pasado", ERROR, "<= hoy", prob, [_CTAS]); return ev
    # Audit CIS: cuentas cuyo campo de contraseña no empieza por ! ni * (y no vacío) y fecha de cambio > hoy.
    act = {n: x for n, x in c["usuarios"].items() if not x["bloqueada"] and not x["vacia"]}
    fut = [f"{n} (día {x['last']} > {c['hoy']})" for n, x in sorted(act.items()) if (_num(x["last"]) or 0) > c["hoy"]]
    ev.add("ultimo_cambio", f"Último cambio de contraseña no posterior a hoy ({len(act)} cuentas)", FALLA if fut else OK, "<= hoy",
           ", ".join(fut) or "ninguna fecha en el futuro", [_CTAS])
    return ev


# ===================================================================== cuentas

def _passwd(ctx: Ctx) -> List[List[str]]:
    return [l.split(":") for l in ctx.lineas("usr.passwd") if l.count(":") >= 5]


def ev_gid0(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = _prob(ctx, "usr.passwd")
    p = _passwd(ctx)
    if prob or not p:
        ev.add("gid0", "GID 0", ERROR, "", prob or "[SALIDA_NO_INTERPRETABLE] sin /etc/passwd", ["usr.passwd"]); return ev
    root = next((x for x in p if x[0] == "root"), None)
    otros = [x[0] for x in p if x[3] == "0" and x[0] not in ("root", "sync", "shutdown", "halt", "operator")]
    ev.add("root_gid", "GID primario de root = 0", OK if root and root[3] == "0" else FALLA, "root:0", f"root:{root[3]}" if root else "sin root", ["usr.passwd"])
    ev.add("otros_gid0", "Ninguna otra cuenta con GID primario 0", FALLA if otros else OK, "solo root", ", ".join(otros) or "ninguna", ["usr.passwd"])
    return ev


def _shells_validas(ctx: Ctx) -> List[str]:
    return [l.strip() for l in ctx.lineas("sys.shells") if l.strip().startswith("/") and not l.strip().endswith("nologin")]


def ev_cuentas_sistema_shell(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    for cid in ("usr.passwd", "sys.shells", "sys.uid_min"):
        prob = _prob(ctx, cid)
        if prob:
            ev.add("shell", "Cuentas de sistema sin shell válida", ERROR, "", prob, [cid]); return ev
    validas, umin = _shells_validas(ctx), ctx.uid_min
    pw = _passwd(ctx)
    poblacion = [x for x in pw if x[0] not in ("root", "halt", "sync", "shutdown", "nfsnobody")
                 and _num(x[2]) is not None and (_num(x[2]) < umin or _num(x[2]) == 65534)]
    malas = [x for x in poblacion if x[5] in validas]
    c = ev.add("shell", f"Cuentas de sistema (UID < {umin}) sin shell de login válida", FALLA if malas else OK, "ninguna con shell válida",
               ", ".join(f"{x[0]} (UID {x[2]}, {x[5]})" for x in malas) or "ninguna", ["usr.passwd", "sys.shells", "sys.uid_min"])
    c.elementos = [{"usuario": x[0], "valor": x[5]} for x in malas]
    interactivas = [f"{x[0]} ({x[5]})" for x in malas if x[5].rsplit("/", 1)[-1] not in ("false", "true")]
    if interactivas:
        ev.observaciones.append("Cuentas de sistema con una shell interactiva: " + ", ".join(interactivas) + ". Una excepción de "
                                "Infraestructura solo cambia el resultado final; no implica que una cuenta de sistema con shell "
                                "interactiva sea segura (el hallazgo técnico CIS se conserva).")
    en_pob = {x[0] for x in poblacion}
    uni = {x[0]: ({"estado": "INCUMPLE", "detalle": f"UID {x[2]}, shell {x[5]} (shell de login válida en /etc/shells)"} if x in malas else
                  {"estado": "CUMPLE", "detalle": f"UID {x[2]}, shell {x[5]} (sin shell de login válida)"}) if x[0] in en_pob else
           {"estado": "FUERA_POBLACION", "detalle": f"UID {x[2]} >= UID_MIN {umin} o cuenta excluida por el CIS"} for x in pw}
    ev.mecanismo["universo_exclusion"] = universo(
        "Shell de las cuentas de sistema", "/etc/passwd + /etc/shells + UID_MIN",
        "la cuenta de sistema conserva una shell de login válida", uni, sorted(x[0] for x in pw))
    return ev


def ev_cuentas_bloqueadas(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    for cid in ("usr.passwd", "sys.shells"):
        prob = _prob(ctx, cid)
        if prob:
            ev.add("bloqueadas", "Cuentas sin shell válida bloqueadas", ERROR, "", prob, [cid]); return ev
    c, prob = cuentas_shadow(ctx)
    if prob:
        ev.add("bloqueadas", "Cuentas sin shell válida bloqueadas", ERROR, "", prob, [_CTAS]); return ev
    validas = _shells_validas(ctx)
    pw = _passwd(ctx)
    sin_shell = [x[0] for x in pw if x[0] != "root" and x[-1] not in validas]
    malas = [n for n in sin_shell if n in c["usuarios"] and not c["usuarios"][n]["bloqueada"]]
    sin_sh = [n for n in sin_shell if n not in c["usuarios"]]
    comp = ev.add("bloqueadas", f"Cuentas sin shell válida (excepto root) bloqueadas ({len(sin_shell)})", FALLA if malas else OK,
                  "contraseña bloqueada (! o *)", ", ".join(malas) or "todas bloqueadas", ["usr.passwd", "sys.shells", _CTAS])
    comp.elementos = [{"usuario": n, "valor": "contraseña no bloqueada"} for n in malas]
    uni = {x[0]: ({"estado": "INCUMPLE" if x[0] in malas else "CUMPLE",
                   "detalle": f"shell {x[-1]}; contraseña {'no bloqueada' if x[0] in malas else 'bloqueada'}"}
                  if x[0] in sin_shell and x[0] in c["usuarios"] else
                  {"estado": "FUERA_POBLACION", "detalle": f"shell {x[-1]} válida en /etc/shells (o root): fuera de la población de 5.4.2.8"})
           for x in pw}
    ev.mecanismo["universo_exclusion"] = universo(
        "Bloqueo de cuentas sin shell válida", "/etc/passwd + /etc/shells + /etc/shadow minimizado",
        "la cuenta sin shell válida conserva la contraseña sin bloquear", uni, sorted(x[0] for x in pw))
    if sin_sh:
        ev.add("sin_shadow", "Cuentas sin entrada en /etc/shadow", MANUAL, "entrada bloqueada en /etc/shadow",
               "[POLITICA_O_REVISION] " + ", ".join(sin_sh), ["usr.passwd", _CTAS])
    return ev


# ===================================================================== entorno de shell (lectura estática)

_ARCH_TMOUT = ("/etc/profile", "/etc/bashrc", "/etc/bash.bashrc", "/etc/profile.local", "/etc/bash.bashrc.local")


def _sin_comentario(t: str) -> str:
    """Comentario shell: '#' al inicio o tras espacio (no el de ${var#patrón})."""
    return re.sub(r"(^|\s)#.*$", r"\1", t)


def _perfiles(ctx: Ctx) -> List[Tuple[str, int, str, int]]:
    """[(archivo, línea, texto, profundidad)]; profundidad > 0 = dentro de if/case/bucle/función (condicional)."""
    res, prof = [], {}
    for f, n, t in _lineas_tag(ctx, "p1.perfiles", "L"):
        d = prof.get(f, 0)
        s = _sin_comentario(t)
        abre = len(re.findall(r"(^|[\s;&|(])(if|case|for|while|until)(\s|$)", s)) + s.count("{")
        cierra = len(re.findall(r"(^|[\s;&|])(fi|esac|done)(\s|;|$)", s)) + s.count("}")
        res.append((f, n, t, d))
        prof[f] = max(0, d + abre - cierra)
    return res


def ev_tmout(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = _prob(ctx, "p1.perfiles")
    if prob:
        ev.add("tmout", "TMOUT", ERROR, "", prob, ["p1.perfiles"]); return ev
    lineas = [x for x in _perfiles(ctx) if x[0] in _ARCH_TMOUT or x[0].startswith("/etc/profile.d/")]
    vals = [(f, n, int(m.group(1)), d) for f, n, t, d in lineas for m in [re.search(r"(?:^|[\s;])(?:typeset\s+-xr\s+)?TMOUT=(\d+)\b", t)] if m]
    if not vals:
        ev.add("tmout", "TMOUT <= 900, readonly y exportado", FALLA, "TMOUT=1..900; readonly; export", "TMOUT no configurado", ["p1.perfiles"]); return ev
    malos = [f"{v} ({f}:{n})" for f, n, v, _ in vals if not 0 < v <= 900]
    ev.add("valor", "TMOUT entre 1 y 900 en todas las asignaciones", FALLA if malos else OK, "1..900",
           ", ".join(malos) or ", ".join(f"{v} ({f}:{n})" for f, n, v, _ in vals), ["p1.perfiles"])
    archivos = {f for f, _, v, _ in vals if 0 < v <= 900}
    for f in sorted(archivos):
        txt = [t for g, _, t, _ in lineas if g == f]
        ro = any(re.search(r"\breadonly\b[^#]*\bTMOUT\b|typeset\s+-xr\s+TMOUT", t) for t in txt)
        ex = any(re.search(r"\bexport\b[^#]*\bTMOUT\b|typeset\s+-xr\s+TMOUT", t) for t in txt)
        ev.add(f"readonly_{f}", f"readonly TMOUT en {f}", OK if ro else FALLA, "readonly TMOUT", "presente" if ro else "ausente", ["p1.perfiles"])
        ev.add(f"export_{f}", f"export TMOUT en {f}", OK if ex else FALLA, "export TMOUT", "presente" if ex else "ausente", ["p1.perfiles"])
    if all(d > 0 for f, _, v, d in vals if 0 < v <= 900):
        ev.add("estatico", "TMOUT definido incondicionalmente", MANUAL, "asignación incondicional",
               "[ANALISIS_ESTATICO_NO_CONCLUYENTE] las asignaciones válidas están dentro de construcciones condicionales", ["p1.perfiles"])
    return ev


def ev_umask(ctx: Ctx) -> Evaluacion:
    """CIS 5.4.3.3: réplica del script del Audit, con su ORDEN DE PRECEDENCIA. Se evalúan todos los /etc/profile.d/*.sh;
    después, solo mientras ningún archivo anterior tenga un umask correcto: /etc/profile, /etc/bashrc, /etc/bash.bashrc,
    pam_umask en /etc/pam.d/postlogin, /etc/login.defs y /etc/default/login. Una línea 'umask' menos restrictiva que 027
    en un archivo evaluado es FAIL; sin ninguna configuración, 'umask is not set' (FAIL). Patrones exactos del Audit
    (grep -Pi sobre el inicio de línea: 'UMASK 022' de login.defs coincide). Los umask de los archivos de usuario (p. ej.
    /root/.bash_profile) no forman parte de este control (5.4.2.6 trata el umask de root)."""
    ev = Evaluacion()
    prob = _prob(ctx, "p1.perfiles")
    if prob:
        ev.add("umask", "umask por defecto", ERROR, "", prob, ["p1.perfiles"]); return ev
    lineas: Dict[str, List[Tuple[int, str]]] = {}
    for f, n, t in _lineas_tag(ctx, "p1.perfiles", "L"):
        lineas.setdefault(f, []).append((n, t))
    archivos_p = {l.split("|", 1)[1] for l in ctx.lineas("p1.perfiles") if l.startswith("F|")}
    correcto, incorrecto, traza = [], [], []

    def chk(f, ok_re=_RE_UMASK_OK, mal_re=_RE_UMASK_MAL):
        if f not in archivos_p and f not in lineas:
            traza.append((f, "no existe o no evaluado", "—")); return
        oks = [(n, t.strip()) for n, t in lineas.get(f, []) if ok_re.search(t)]
        mals = [(n, t.strip()) for n, t in lineas.get(f, []) if not ok_re.search(t) and mal_re.search(t)]
        for n, t in oks:
            correcto.append(f"{f}:{n} {t}")
        for n, t in mals:
            incorrecto.append(f"{f}:{n} {t}")
        traza.append((f, "correcto" if oks else ("incorrecto" if mals else "sin umask"),
                      "; ".join(f"línea {n}: {t}" for n, t in oks + mals) or "—"))

    for f in sorted(x for x in archivos_p | set(lineas) if x.startswith("/etc/profile.d/") and x.endswith(".sh")):
        chk(f)
    for f in ("/etc/profile", "/etc/bashrc", "/etc/bash.bashrc"):
        if not correcto:
            chk(f)
    if not correcto:   # el script CIS registra el pam_umask correcto en l_output1 (sin efecto en la precedencia): se replica
        chk("/etc/pam.d/postlogin", _RE_PAMUMASK_OK, _RE_PAMUMASK_MAL)
        correcto[:] = [c for c in correcto if not c.startswith("/etc/pam.d/postlogin")]
    for f in ("/etc/login.defs", "/etc/default/login"):
        if not correcto:
            chk(f)
    for i, (f, est, det) in enumerate(traza, 1):
        ev.add(f"precedencia_{i}", f"Precedencia {i}: {f}", INFO, "umask 027 o más restrictivo", f"{est} — {det}", ["p1.perfiles"])
    if incorrecto:
        ev.add("resultado_audit", "Ningún umask menos restrictivo que 027 en los archivos evaluados (script CIS)", FALLA, "ninguno < 027",
               "; ".join(incorrecto), ["p1.perfiles"])
    elif not correcto:
        ev.add("resultado_audit", "umask por defecto configurado (script CIS)", FALLA, "umask 027 o más restrictivo", "umask is not set", ["p1.perfiles"])
    else:
        ev.add("resultado_audit", "umask por defecto 027 o más restrictivo (script CIS)", OK, "umask 027 o más restrictivo", "; ".join(correcto),
               ["p1.perfiles"])
    if incorrecto:
        ev.mecanismo["motivo_visible"] = ("umask por defecto menos restrictivo que 027: " + "; ".join(incorrecto)
                                          + ". Ningún archivo de mayor precedencia (/etc/profile.d/*.sh, /etc/profile, /etc/bashrc) define umask 027.")
    elif not correcto:
        ev.mecanismo["motivo_visible"] = "umask por defecto no configurado en ningún archivo del orden de precedencia del CIS."
    return ev


# ===================================================================== SSH

def _ssh_grupo_llaves(ctx: Ctx) -> Optional[str]:
    return next((g for g in _grupos(ctx) if re.fullmatch(r"ssh_keys|_?ssh", g)), None)


def _obs_acl(ctx: Ctx, ev: Evaluacion, patron: str = r"/sshd_config(\.d(/.*\.conf)?)?$") -> None:
    """1.2.0 (como root) añade acl_escritura=n: entradas ACL nominales con escritura. Se informa como observación."""
    acl = [(l.split("|")[1], l.rsplit("acl_escritura=", 1)[1]) for l in ctx.lineas("p1.ssh_permisos")
           if l.startswith("S|") and "|acl_escritura=" in l and re.search(patron, l.split("|")[1])]
    con = [f"{r} ({n})" for r, n in acl if n not in ("0", "NO_DISPONIBLE")]
    if con:
        ev.observaciones.append("ACL con permiso de escritura para usuarios/grupos nominales: " + ", ".join(con))
    elif any(n == "NO_DISPONIBLE" for _, n in acl):
        ev.observaciones.append("getfacl no disponible: ACL no verificadas")


def ev_ssh_config_permisos(ctx: Ctx) -> Evaluacion:
    ev = Evaluacion()
    prob = _prob(ctx, "p1.ssh_permisos")
    if prob:
        ev.add("sshd_config", "Permisos de sshd_config y sshd_config.d", ERROR, "", prob, ["p1.ssh_permisos"]); return ev
    st = _stats(ctx, "p1.ssh_permisos")
    if "/etc/ssh/sshd_config" not in st:
        ev.add("sshd_config", "sshd_config", ERROR, "", "[SALIDA_NO_INTERPRETABLE] sin stat", ["p1.ssh_permisos"]); return ev
    for r in [r for r in st if r == "/etc/ssh/sshd_config" or (r.startswith("/etc/ssh/sshd_config.d/") and r.endswith(".conf"))]:
        _acceso(ev, r, st[r], 0o600, cid="p1.ssh_permisos", opcional=False)
    _obs_acl(ctx, ev)
    return ev


def ev_ssh_llaves(privadas: bool):
    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        prob = _prob(ctx, "p1.ssh_permisos", ("/etc/ssh/ssh_host_",))
        if prob or "S_FIN" not in ctx.lineas("p1.ssh_permisos"):
            ev.add("llaves", "Llaves de host", ERROR, "", prob or "[SALIDA_NO_INTERPRETABLE] salida incompleta", ["p1.ssh_permisos"]); return ev
        st = _stats(ctx, "p1.ssh_permisos")
        llaves = {r: v for r, v in st.items() if re.search(r"/ssh_host_[^/]*_key$" if privadas else r"/ssh_host_[^/]*_key\.pub$", r)}
        if not llaves:
            ev.add("llaves", "Llaves de host", NA, "", "no hay llaves de host", ["p1.ssh_permisos"]); return ev
        grupo = _ssh_grupo_llaves(ctx)
        for r, v in sorted(llaves.items()):
            if privadas and v and v[2] == grupo:
                _acceso(ev, r, v, 0o640, (grupo,), "p1.ssh_permisos")
            else:
                _acceso(ev, r, v, 0o600 if privadas else 0o644, cid="p1.ssh_permisos")
        _obs_acl(ctx, ev, r"/ssh_host_[^/]*_key$" if privadas else r"/ssh_host_[^/]*_key\.pub$")
        return ev
    return _ev


_SSH = "priv.ssh_efectiva"


def _sshd_privilegiado(ctx: Ctx):
    """priv.ssh_efectiva (1.2.0) -> (config, problema, {clave: [bloques Match que la redefinen]}, evidencia, estado_problema)."""
    prob = _priv(ctx, _SSH)
    if prob:
        return None, prob, {}, [_SSH], ERROR
    lin = ctx.lineas(_SSH)
    if "SSHD|NO_INSTALADO" in lin:
        return None, "sshd no instalado (binario sshd ausente)", {}, [_SSH], NA
    tag = lambda t: [l.split("|")[1:] for l in lin if l.startswith(t + "|")]
    sint, rc = (tag("SSHD_SINTAXIS") or [[None]])[0][0], (tag("SSHD_T_RC") or [[None]])[0][0]
    ol8 = (tag("OPCIONES_OL8") or [[None]])[0]
    if ol8[0] == "bloqueado":
        return None, ("[ANALISIS_ESTATICO_NO_CONCLUYENTE] OPTIONS/CRYPTO_POLICY de sshd contienen construcciones no literales; "
                      "'sshd -T $OPTIONS $CRYPTO_POLICY' no puede reproducirse sin ejecutar código del host"), {}, [_SSH], MANUAL
    if sint != "0":
        return None, f"[RC_INESPERADO] {_SSH}: 'sshd -t' rc={sint} (configuración de sshd inválida)", {}, [_SSH], ERROR
    if rc != "0":
        return None, f"[RC_INESPERADO] {_SSH}: 'sshd -T' rc={rc}", {}, [_SSH], ERROR
    if not tag("MATCH") or len(tag("ACCESO")) != 4:
        return None, f"[SALIDA_NO_INTERPRETABLE] {_SSH}: salida incompleta", {}, [_SSH], ERROR
    conf = {k[0].lower(): k[1] for k in tag("K") if len(k) >= 2}
    for a in tag("ACCESO"):
        n = _num(a[1]) or 0
        conf[a[0]] = f"definido ({n} entradas)" if n else None
    match: Dict[str, List[str]] = {}
    for b in tag("MATCH_BLOQUE"):
        if len(b) < 4 or not b[3]:
            continue
        for kv in b[3].split(";"):
            k, _, v = kv.partition("=")
            if k.startswith(("allow", "deny")) or (conf.get(k) or "").lower() != v.lower():
                match.setdefault(k, []).append(f"bloque Match {b[0]} ({b[1]}; criterio {b[2]}): {k}={v}")
    return conf, None, match, [_SSH], None


def sshd_efectivo(ctx: Ctx) -> Tuple[Optional[Dict[str, str]], Optional[str], Dict[str, List[str]], List[str], Optional[str]]:
    """(config, problema, {clave|'*': bloques Match}, evidencia, estado si hay problema).

    Prefiere priv.ssh_efectiva (1.2.0). Sin ella, p1.sshd (1.1.0): sin `sshd -T` válido no hay configuración efectiva,
    y cualquier Match se trata como no evaluable ('*')."""
    r = ctx.reg(_SSH)
    if r is not None and r.sudo:
        return _sshd_privilegiado(ctx)
    r = ctx.reg("p1.sshd")
    if r is None:
        return None, "[NO_RECOLECTADO] p1.sshd", {}, ["p1.sshd"], ERROR
    lin = r.stdout.splitlines()
    rc = next((l.split("|", 1)[1] for l in lin if l.startswith("SSHD_T_RC|")), None)
    no_leg = [l.split("|", 1)[1] for l in lin if l.startswith("#NO_LEGIBLE|")]
    if rc != "0" or no_leg:
        motivo = (f"'sshd -T' no puede ejecutarse sin privilegios (rc={rc}; "
                  f"{(r.stderr or '').strip()[:120] or 'llaves de host/sshd_config no legibles'})" if rc != "0" else
                  f"no legibles: {', '.join(no_leg)} (no puede descartarse la existencia de bloques Match)")
        p = _priv(ctx, _SSH)
        return None, f"{p}; sin privilegios: {motivo}", {}, ["p1.sshd"], ERROR
    conf = {}
    for l in lin:
        p = l.split(None, 1)
        if len(p) == 2 and not l.startswith(("SSHD_T_RC|", "M|", "F|", "#")):
            k = p[0].lower()
            conf[k] = (conf[k] + " " + p[1]) if k in conf and k.startswith(("allow", "deny")) else p[1]
    match = [f"{f}:{n} {t.strip()}" for f, n, t in _lineas_tag(ctx, "p1.sshd", "M") if t.strip().lower().startswith("match")]
    return conf, None, ({"*": match} if match else {}), ["p1.sshd"], None


def ev_sshd(clave: str, ok, esperado: str, varias: Tuple[str, ...] = ()):
    claves = varias or (clave,)

    def _ev(ctx: Ctx) -> Evaluacion:
        ev = Evaluacion()
        conf, prob, match, evid, est = sshd_efectivo(ctx)
        if prob:
            ev.add(clave, f"sshd {' '.join(claves)} (configuración efectiva)", est, esperado, prob, evid); return ev
        vals = {k: conf.get(k.lower()) for k in claves}
        if None in vals.values() and not varias:
            ev.add(clave, f"sshd {clave}", FALLA, esperado, "no definido", evid)
        else:
            ev.add(clave, f"sshd {' '.join(claves)}", OK if ok(vals) else FALLA, esperado,
                   "; ".join(f"{k} {v}" for k, v in vals.items() if v is not None) or "no definido", evid)
        rel = match.get("*", []) + [m for k in claves for m in match.get(k.lower(), [])]
        if rel:
            ev.add("match", "Bloques Match que redefinen el requisito", MANUAL,
                   "evaluar cada Match con 'sshd -T -C' en un contexto aprobado",
                   "[POLITICA_O_REVISION] " + "; ".join(rel) + " (no existe contexto de conexión aprobado; no se asume)", evid)
        return ev
    return _ev


def _maxstartups(v: Dict[str, str]) -> bool:
    p = [_num(x) for x in (v.get("maxstartups") or "").split(":")]
    return len(p) == 3 and None not in p and p[0] <= 10 and p[1] <= 30 and p[2] <= 60


# ===================================================================== registro

_T = lambda *t: tuple(t)  # noqa: E731
_C = lambda i, t, cmds, ev, met, crit, **k: Control(i, t, cmds, met, k.pop("evid", "Ver comandos"), crit,  # noqa: E731
                                                     k.pop("nocumple", "No se cumple el criterio CIS"), ev, **k)

_SRV = [  # (id, títulos, nombre, paquetes, unidades)
    ("p1_autofs", ("Ensure autofs services are not in use",), "autofs", ("autofs",), ("autofs.service",)),
    ("p1_avahi", ("Ensure avahi daemon services are not in use",), "avahi", ("avahi",), ("avahi-daemon.socket", "avahi-daemon.service")),
    ("p1_dhcp", ("Ensure dhcp server services are not in use",), "servidor DHCP", ("dhcp-server",), ("dhcpd.service", "dhcpd6.service")),
    ("p1_dns", ("Ensure dns server services are not in use",), "servidor DNS", ("bind",), ("named.service",)),
    ("p1_ftp", ("Ensure ftp server services are not in use",), "servidor FTP", ("vsftpd",), ("vsftpd.service",)),
    ("p1_imap", ("Ensure message access server services are not in use",), "IMAP/POP3", ("dovecot", "cyrus-imapd"),
     ("dovecot.socket", "dovecot.service", "cyrus-imapd.service")),
    ("p1_nfs", ("Ensure network file system services are not in use",), "NFS", ("nfs-utils",), ("nfs-server.service",)),
    ("p1_nis_srv", ("Ensure nis server services are not in use",), "servidor NIS", ("ypserv",), ("ypserv.service",)),
    ("p1_cups", ("Ensure print server services are not in use",), "CUPS", ("cups",), ("cups.socket", "cups.service")),
    ("p1_rpcbind", ("Ensure rpcbind services are not in use",), "rpcbind", ("rpcbind",), ("rpcbind.socket", "rpcbind.service")),
    ("p1_rsync", ("Ensure rsync services are not in use",), "rsync", ("rsync-daemon",), ("rsyncd.socket", "rsyncd.service")),
    ("p1_samba", ("Ensure samba file server services are not in use",), "Samba", ("samba",), ("smb.service",)),
    ("p1_snmp", ("Ensure snmp services are not in use",), "SNMP", ("net-snmp",), ("snmpd.service",)),
    ("p1_telnet_srv", ("Ensure telnet server services are not in use",), "servidor telnet", ("telnet-server",), ("telnet.socket",)),
    ("p1_tftp_srv", ("Ensure tftp server services are not in use",), "servidor TFTP", ("tftp-server",), ("tftp.socket", "tftp.service")),
    ("p1_proxy", ("Ensure web proxy server services are not in use",), "proxy web", ("squid",), ("squid.service",)),
    ("p1_web", ("Ensure web server services are not in use",), "servidor web", ("httpd", "nginx"),
     ("httpd.socket", "httpd.service", "nginx.service", "apache2.service")),
    ("p1_xinetd", ("Ensure xinetd services are not in use",), "xinetd", ("xinetd",), ("xinetd.service",)),
    ("p1_ldap_srv", ("Ensure ldap server services are not in use",), "servidor LDAP", ("openldap2", "openldap2_5"), ("slapd.service",)),
]
# Nombres de paquete que difieren en SLES / RHEL 8 (rpm -q de un nombre ajeno a la plataforma devuelve 'not installed').
# Nombres según el Audit de cada Benchmark (rpm -q de un nombre ajeno a la plataforma devuelve 'not installed').
_PKG_SLES = {"p1_rsync": ("rsync",), "p1_nfs": ("nfs-kernel-server",), "p1_web": ("apache2", "nginx"),
             "p1_ldap_srv": ("openldap2", "openldap2_5"), "p1_xorg": ("xorg-x11-server", "xorg-x11-server-extra", "xorg-x11-server-wayland"),
             "p1_ldap_cli": ("openldap2-client", "openldap2_5")}
_PKG_RHEL = {"p1_xorg": ("xorg-x11-server-common",), "p1_ldap_cli": ("openldap-clients",)}


def _pkgs_para(cid: str, base: Tuple[str, ...]):
    def sel(ctx: Ctx) -> Tuple[str, ...]:
        return (_PKG_SLES if ctx.familia == SLES else _PKG_RHEL).get(cid, base)
    return sel


def _ev_srv(cid, nombre, pkgs, units):
    sel = _pkgs_para(cid, pkgs)
    return lambda ctx: ev_servicio(nombre, sel(ctx), units)(ctx)


_MOD = [("cramfs",), ("squashfs",), ("udf",), ("usb-storage",), ("dccp",), ("sctp",), ("rds",), ("tipc",)]

_SYSCTL = {
    # v2 / SLES (agrupados)
    "Ensure ip forwarding is disabled": (("net.ipv4.ip_forward", "0"), ("net.ipv6.conf.all.forwarding", "0")),
    "Ensure packet redirect sending is disabled": (("net.ipv4.conf.all.send_redirects", "0"), ("net.ipv4.conf.default.send_redirects", "0")),
    "Ensure bogus icmp responses are ignored": (("net.ipv4.icmp_ignore_bogus_error_responses", "1"),),
    "Ensure broadcast icmp requests are ignored": (("net.ipv4.icmp_echo_ignore_broadcasts", "1"),),
    "Ensure icmp redirects are not accepted": (("net.ipv4.conf.all.accept_redirects", "0"), ("net.ipv4.conf.default.accept_redirects", "0"),
                                               ("net.ipv6.conf.all.accept_redirects", "0"), ("net.ipv6.conf.default.accept_redirects", "0")),
    "Ensure secure icmp redirects are not accepted": (("net.ipv4.conf.all.secure_redirects", "0"), ("net.ipv4.conf.default.secure_redirects", "0")),
    "Ensure reverse path filtering is enabled": (("net.ipv4.conf.all.rp_filter", "1"), ("net.ipv4.conf.default.rp_filter", "1")),
    "Ensure source routed packets are not accepted": (("net.ipv4.conf.all.accept_source_route", "0"), ("net.ipv4.conf.default.accept_source_route", "0"),
                                                      ("net.ipv6.conf.all.accept_source_route", "0"), ("net.ipv6.conf.default.accept_source_route", "0")),
    "Ensure suspicious packets are logged": (("net.ipv4.conf.all.log_martians", "1"), ("net.ipv4.conf.default.log_martians", "1")),
    "Ensure tcp syn cookies is enabled": (("net.ipv4.tcp_syncookies", "1"),),
    "Ensure ipv6 router advertisements are not accepted": (("net.ipv6.conf.all.accept_ra", "0"), ("net.ipv6.conf.default.accept_ra", "0")),
}
# v4 (un control por parámetro): "Ensure <param> is configured"
_SYSCTL_V4 = {
    "net.ipv4.ip_forward": "0", "net.ipv4.conf.all.forwarding": "0", "net.ipv4.conf.default.forwarding": "0",
    "net.ipv6.conf.all.forwarding": "0", "net.ipv6.conf.default.forwarding": "0",
    "net.ipv4.conf.all.send_redirects": "0", "net.ipv4.conf.default.send_redirects": "0",
    "net.ipv4.icmp_ignore_bogus_error_responses": "1", "net.ipv4.icmp_echo_ignore_broadcasts": "1",
    "net.ipv4.conf.all.accept_redirects": "0", "net.ipv4.conf.default.accept_redirects": "0",
    "net.ipv6.conf.all.accept_redirects": "0", "net.ipv6.conf.default.accept_redirects": "0",
    "net.ipv4.conf.all.secure_redirects": "0", "net.ipv4.conf.default.secure_redirects": "0",
    "net.ipv4.conf.all.rp_filter": "1", "net.ipv4.conf.default.rp_filter": "1",
    "net.ipv4.conf.all.accept_source_route": "0", "net.ipv4.conf.default.accept_source_route": "0",
    "net.ipv6.conf.all.accept_source_route": "0", "net.ipv6.conf.default.accept_source_route": "0",
    "net.ipv4.conf.all.log_martians": "1", "net.ipv4.conf.default.log_martians": "1",
    "net.ipv4.tcp_syncookies": "1", "net.ipv6.conf.all.accept_ra": "0", "net.ipv6.conf.default.accept_ra": "0",
}

_SYS = ("p1.sysctl", "p1.sysctl_archivos", "p1.ipv6")
_PAM = ("p1.pam",)

CONTROLES_P1: List[Control] = []
for (_m,) in _MOD:
    CONTROLES_P1.append(_C(f"p1_mod_{_m.replace('-', '_')}", _T(f"Ensure {_m} kernel module is not available"),
                           ("p1.kmod", "priv.kmod_config") if _m.replace("-", "_") in ("cramfs", "squashfs", "udf", "usb_storage", "tipc", "rds")
                           else ("p1.kmod",), ev_modulo(_m),
                           "Archivos de módulo en /lib/modules (todos los kernels), /proc/modules y modprobe.d efectivo",
                           f"{_m} no disponible, o no cargado + install /bin/false + blacklist", permite_parcial=False))
for _d, _t1, _t2 in (("/tmp", "Ensure /tmp is a separate partition", "Ensure /tmp is tmpfs or a separate partition"),
                     ("/var", "Ensure separate partition exists for /var", None), ("/var/tmp", "Ensure separate partition exists for /var/tmp", None),
                     ("/var/log", "Ensure separate partition exists for /var/log", None),
                     ("/var/log/audit", "Ensure separate partition exists for /var/log/audit", None),
                     ("/home", "Ensure separate partition exists for /home", None),
                     ("/dev/shm", "Ensure /dev/shm is a separate partition", None)):
    CONTROLES_P1.append(_C("p1_part_" + _d.strip("/").replace("/", "_"), _T(*[x for x in (_t1, _t2) if x]), ("p1.montajes", "p1.unidades"),
                           ev_particion(_d, tmp_mount=_d == "/tmp"), "findmnt -kn " + _d + " (+ tmp.mount)", f"{_d} es un montaje propio"))
CONTROLES_P1.append(_C("p1_part_dev_shm_tmpfs", _T("Ensure /dev/shm is tmpfs"), ("p1.montajes",), ev_particion("/dev/shm", tmpfs_requerido=True),
                       "findmnt -kn /dev/shm", "/dev/shm montado como tmpfs"))
for _d, _ops in (("/tmp", ("nodev", "nosuid", "noexec")), ("/dev/shm", ("nodev", "nosuid", "noexec")), ("/home", ("nodev", "nosuid")),
                 ("/var", ("nodev", "nosuid")), ("/var/tmp", ("nodev", "nosuid", "noexec")), ("/var/log", ("nodev", "nosuid", "noexec")),
                 ("/var/log/audit", ("nodev", "nosuid", "noexec"))):
    for _o in _ops:
        CONTROLES_P1.append(_C(f"p1_opt_{_o}_" + _d.strip("/").replace("/", "_"), _T(f"Ensure {_o} option set on {_d} partition"), ("p1.montajes",),
                               ev_opcion(_d, _o), f"findmnt -kn {_d}", f"-IF- {_d} es partición propia: {_o} en las opciones"))
for _cid, _tit, _nom, _pk, _un in _SRV:
    CONTROLES_P1.append(_C(_cid, _tit, ("pkg.rpm", "p1.unidades"), _ev_srv(_cid, _nom, _pk, _un), "rpm -q + systemctl is-enabled/is-active",
                           f"{_nom}: paquete no instalado, o servicios no habilitados ni activos"))
for _cid, _tit, _nom, _pk in (("p1_xorg", "Ensure X window server services are not in use", "X Window server", ("xorg-x11-server-common",)),
                              ("p1_nis_cli", "Ensure nis client is not installed", "cliente NIS", ("ypbind",)),
                              ("p1_telnet_cli", "Ensure telnet client is not installed", "cliente telnet", ("telnet",)),
                              ("p1_ldap_cli", "Ensure ldap client is not installed", "cliente LDAP", ("openldap-clients",)),
                              ("p1_tftp_cli", "Ensure tftp client is not installed", "cliente TFTP", ("tftp",))):
    CONTROLES_P1.append(_C(_cid, _T(_tit), ("pkg.rpm",), (lambda c, n, p: lambda ctx: ev_paquete_ausente(n, _pkgs_para(c, p)(ctx))(ctx))(_cid, _nom, _pk),
                           "rpm -q", f"{_nom} no instalado"))
CONTROLES_P1 += [
    _C("p1_mta", _T("Ensure mail transfer agents are configured for local-only mode"), ("p1.escucha",), ev_mta,
       "ss -lntu + postconf inet_interfaces", "25/465/587 solo en loopback; inet_interfaces != all"),
    _C("p1_motd", _T("Ensure message of the day is configured properly", "Ensure /etc/motd is configured"), ("p1.banner", "sys.os_release", "priv.motd"),
       ev_banner("/etc/motd", es_motd=True), "Contenido de /etc/motd y /etc/motd.d", "sin \\m \\r \\s \\v ni ID del SO; texto según política"),
    _C("p1_issue", _T("Ensure local login warning banner is configured properly", "Ensure /etc/issue is configured"), ("p1.banner", "sys.os_release"),
       ev_banner("/etc/issue"), "Contenido de /etc/issue", "sin \\m \\r \\s \\v ni ID del SO; texto según política"),
    _C("p1_issue_net", _T("Ensure remote login warning banner is configured properly", "Ensure /etc/issue.net is configured"),
       ("p1.banner", "sys.os_release"), ev_banner("/etc/issue.net"), "Contenido de /etc/issue.net", "sin \\m \\r \\s \\v ni ID del SO; texto según política"),
    _C("p1_acc_motd", _T("Ensure access to /etc/motd is configured"), ("p1.banner",), ev_acceso_banner("/etc/motd"), "stat", "<= 0644 root:root",
       permite_parcial=False),
    _C("p1_acc_issue", _T("Ensure access to /etc/issue is configured"), ("p1.banner",), ev_acceso_banner("/etc/issue"), "stat", "<= 0644 root:root",
       permite_parcial=False),
    _C("p1_acc_issue_net", _T("Ensure access to /etc/issue.net is configured"), ("p1.banner",), ev_acceso_banner("/etc/issue.net"), "stat",
       "<= 0644 root:root", permite_parcial=False),
    _C("p1_chrony_uso", _T("Ensure time synchronization is in use"), ("pkg.rpm",), ev_chrony_en_uso, "rpm -q chrony", "chrony instalado"),
    _C("p1_timesync_unico", _T("Ensure a single time synchronization daemon is in use"), ("p1.unidades",), ev_un_solo_demonio,
       "systemctl chronyd / systemd-timesyncd", "exactamente uno habilitado y activo"),
    _C("p1_chrony_conf", _T("Ensure chrony is configured"), ("pkg.rpm", "p1.unidades", "p1.chrony"), ev_chrony_configurado,
       "chrony.conf / chrony.d", "server|pool remoto configurado"),
    _C("p1_ipv6", _T("Ensure IPv6 status is identified"), ("p1.ipv6", "p1.sysctl"), ev_ipv6, "Estado de IPv6", "según política del sitio (Manual)"),
    _C("p1_wireless", _T("Ensure wireless interfaces are disabled", "Ensure wireless interfaces are not available"), ("p1.wireless", "p1.kmod"),
       ev_wireless, "/sys/class/net/*/wireless + modprobe.d", "sin NIC inalámbrica o módulo no cargable"),
]
for _tit, _pares in _SYSCTL.items():
    CONTROLES_P1.append(_C("p1_sysctl_" + re.sub(r"\W+", "_", _tit[7:].lower()).strip("_"), _T(_tit), _SYS, ev_sysctl(*_pares),
                           "sysctl en ejecución + systemd-sysctl --cat-config", "; ".join(f"{p} = {v}" for p, v in _pares), permite_parcial=False))
for _p, _v in _SYSCTL_V4.items():
    CONTROLES_P1.append(_C("p1_sysctl_" + _p.replace(".", "_"), _T(f"Ensure {_p} is configured"), _SYS, ev_sysctl((_p, _v)),
                           "sysctl en ejecución + systemd-sysctl --cat-config", f"{_p} = {_v}", permite_parcial=False))
CONTROLES_P1 += [
    _C("p1_cron_daemon", _T("Ensure cron daemon is enabled and active"), ("pkg.rpm", "p1.unidades"), ev_cron_daemon, "systemctl crond/cron",
       "-IF- cron instalado: enabled y active"),
]
for _r, _m, _t1, _t2 in (("/etc/crontab", 0o600, "Ensure permissions on /etc/crontab are configured", "Ensure access to /etc/crontab is configured"),
                         ("/etc/cron.hourly", 0o700, "Ensure permissions on /etc/cron.hourly are configured", "Ensure access to /etc/cron.hourly is configured"),
                         ("/etc/cron.daily", 0o700, "Ensure permissions on /etc/cron.daily are configured", "Ensure access to /etc/cron.daily is configured"),
                         ("/etc/cron.weekly", 0o700, "Ensure permissions on /etc/cron.weekly are configured", "Ensure access to /etc/cron.weekly is configured"),
                         ("/etc/cron.monthly", 0o700, "Ensure permissions on /etc/cron.monthly are configured", "Ensure access to /etc/cron.monthly is configured"),
                         ("/etc/cron.d", 0o700, "Ensure permissions on /etc/cron.d are configured", "Ensure access to /etc/cron.d is configured")):
    CONTROLES_P1.append(_C("p1_cron_" + _r.rsplit("/", 1)[1].replace(".", "_"), _T(_t1, _t2), ("pkg.rpm", "p1.cron"), ev_cron_archivo(_r, _m),
                           "stat", f"<= {_m:04o} root:root", permite_parcial=False))
CONTROLES_P1 += [
    _C("p1_crontab_v2", _T("Ensure crontab is restricted to authorized users"), ("pkg.rpm", "p1.cron"), ev_allow("cron", ("cronie", "cron"), ("root",)),
       "stat cron.allow/cron.deny", "cron.allow existe <=0640 root:root; cron.deny ausente o <=0640 root:root", permite_parcial=False),
    _C("p1_crontab_v4", _T("Ensure access to crontab is configured"), ("pkg.rpm", "p1.cron"), ev_allow("cron", ("cronie", "cron"), ("root", "crontab")),
       "stat cron.allow/cron.deny", "cron.allow existe <=0640 root:root|crontab; cron.deny ausente o igual", permite_parcial=False),
    _C("p1_at", _T("Ensure at is restricted to authorized users", "Ensure access to at is configured"), ("pkg.rpm", "p1.cron"),
       ev_allow("at", ("at",), ("root", "daemon")), "stat at.allow/at.deny", "at.allow existe <=0640 root:daemon|root; at.deny ausente o igual",
       permite_parcial=False),
    _C("p1_sudo", _T("Ensure sudo is installed"), ("pkg.rpm",), ev_paquete_presente("sudo", "sudo"), "rpm -q sudo", "sudo instalado"),
    _C("p1_sudo_pty", _T("Ensure sudo commands use pty"), ("priv.sudo_politica",), ev_sudo_pty, "sudoers normalizado (recolector 1.2.0)",
       "Defaults use_pty y ningún !use_pty"),
    _C("p1_sudo_log", _T("Ensure sudo log file exists"), ("auditd.sudo_logfile",), ev_sudo_logfile, "Defaults logfile (recolector privilegiado 1.1.0)",
       'Defaults logfile="<ruta>"'),
    _C("p1_sudo_pass", _T("Ensure users must provide password for escalation"), ("priv.sudo_politica",), ev_sudo_nopasswd,
       "sudoers normalizado (recolector 1.2.0)", "ninguna regla NOPASSWD"),
    _C("p1_sudo_reauth", _T("Ensure re-authentication for privilege escalation is not disabled globally"), ("priv.sudo_politica",),
       ev_sudo_noauth, "sudoers normalizado (recolector 1.2.0)", "ninguna regla !authenticate"),
    _C("p1_sudo_timeout", _T("Ensure sudo authentication timeout is configured correctly", "Ensure sudo timestamp_timeout is configured"),
       ("priv.sudo_politica",), ev_sudo_timeout, "sudoers normalizado + 'sudo -V' (recolector 1.2.0)", "timestamp_timeout <= 15 y != -1"),
    _C("p1_su", _T("Ensure access to the su command is restricted"), ("p1.pam_su", "usr.group"), ev_su, "/etc/pam.d/su + /etc/group",
       "pam_wheel use_uid group=<g> y <g> sin miembros"),
    _C("p1_authselect", _T("Ensure active authselect profile includes pam modules"), _PAM, ev_authselect_modulos, "authselect.conf + plantillas del perfil",
       "perfil custom con pwquality/pwhistory/faillock/unix", familias=(RHEL,)),
    _C("p1_pam_faillock", _T("Ensure pam_faillock module is enabled"), _PAM, ev_pam_modulo("pam_faillock.so", ("auth", "account")),
       "system-auth/password-auth", "pam_faillock en auth y account", familias=(RHEL,)),
    _C("p1_pam_pwquality", _T("Ensure pam_pwquality module is enabled"), _PAM, ev_pam_modulo("pam_pwquality.so", ("password",)),
       "system-auth/password-auth", "pam_pwquality en password", familias=(RHEL,)),
    _C("p1_pw_len", _T("Ensure password length is configured"), _PAM + ("priv.pwquality",),
       ev_pam_valor("pwquality", "pam_pwquality", "minlen", lambda v: (_num(v) or 0) >= 14, ">= 14"), "pwquality.conf(.d) + argumentos pam_pwquality",
       "minlen >= 14"),
    _C("p1_pw_complex", _T("Ensure password complexity is configured"), _PAM + ("priv.pwquality",), ev_complejidad, "pwquality.conf(.d) + argumentos pam_pwquality",
       "minclass >= 4 y [dulo]credit < 0 (técnico) + política del sitio"),
    _C("p1_pw_deny", _T("Ensure password failed attempts lockout is configured"), _PAM,
       ev_pam_valor("faillock", "pam_faillock", "deny", lambda v: 1 <= (_num(v) or 0) <= 5, "1..5"), "faillock.conf + argumentos pam_faillock",
       "deny entre 1 y 5"),
    _C("p1_pw_unlock", _T("Ensure password unlock time is configured"), _PAM,
       ev_pam_valor("faillock", "pam_faillock", "unlock_time", lambda v: _num(v) == 0 or (_num(v) or 0) >= 900, "0 o >= 900"),
       "faillock.conf + argumentos pam_faillock", "unlock_time 0 o >= 900"),
    _C("p1_pw_remember", _T("Ensure password history remember is configured"), _PAM,
       ev_pam_valor("pwhistory", "pam_pwhistory", "remember", lambda v: (_num(v) or 0) >= 24, ">= 24", solo_args_sles=True), "pwhistory.conf + argumentos pam_pwhistory",
       "remember >= 24"),
    _C("p1_pam_unix_hash", _T("Ensure pam_unix includes a strong password hashing algorithm"), _PAM, ev_pam_unix_hash, "pam_unix (password)",
       "sha512 o yescrypt"),
    _C("p1_hash_logindefs", _T("Ensure strong password hashing algorithm is configured"), ("p1.login_defs",), ev_hash_login_defs, "login.defs",
       "ENCRYPT_METHOD SHA512|YESCRYPT"),
    _C("p1_pass_max", _T("Ensure password expiration is configured"), ("p1.login_defs", "priv.cuentas"),
       ev_envejecimiento("PASS_MAX_DAYS", lambda v: 0 < (_num(v) or 0) <= 365, "1..365", "Campo 5 (máximo)", "max",
                         lambda n: n is not None and 1 <= n <= 365), "login.defs + /etc/shadow",
       "PASS_MAX_DAYS <= 365 y por cuenta"),
    _C("p1_pass_min", _T("Ensure minimum password days is configured"), ("p1.login_defs", "priv.cuentas"),
       ev_envejecimiento("PASS_MIN_DAYS", lambda v: (_num(v) or 0) >= 1, ">= 1", "Campo 4 (mínimo)", "min",
                         lambda n: n is not None and n >= 1), "login.defs + /etc/shadow",
       "PASS_MIN_DAYS >= 1 y por cuenta"),
    _C("p1_pass_warn", _T("Ensure password expiration warning days is configured"), ("p1.login_defs", "priv.cuentas"),
       ev_envejecimiento("PASS_WARN_AGE", lambda v: (_num(v) or 0) >= 7, ">= 7", "Campo 6 (aviso)", "warn",
                         lambda n: n is not None and n >= 7), "login.defs + /etc/shadow",
       "PASS_WARN_AGE >= 7 y por cuenta"),
    _C("p1_inactive", _T("Ensure inactive password lock is configured"), ("p1.login_defs", "priv.cuentas"),
       ev_envejecimiento("INACTIVE", lambda v: 0 <= (_num(v) if _num(v) is not None else -1) <= 45, "0..45 (no -1)", "Campo 7 (inactividad)",
                         "inactive", lambda n: n is not None and 0 <= n <= 45),
       "/etc/default/useradd + /etc/shadow", "INACTIVE <= 45 y por cuenta"),
    _C("p1_ultimo_cambio", _T("Ensure all users last password change date is in the past"), ("priv.cuentas",), ev_ultimo_cambio,
       "/etc/shadow minimizado (recolector 1.2.0)",
       "fecha de cambio <= hoy"),
    _C("p1_gid0", _T("Ensure root is the only GID 0 account"), ("usr.passwd",), ev_gid0, "/etc/passwd", "root:0 y ningún otro GID 0"),
    _C("p1_sys_shell", _T("Ensure system accounts do not have a valid login shell"), ("usr.passwd", "sys.shells", "sys.uid_min"),
       ev_cuentas_sistema_shell, "/etc/passwd + /etc/shells + UID_MIN", "ninguna cuenta de sistema con shell válida"),
    _C("p1_bloqueadas", _T("Ensure accounts without a valid login shell are locked"), ("usr.passwd", "sys.shells", "priv.cuentas"),
       ev_cuentas_bloqueadas, "/etc/passwd + /etc/shells + /etc/shadow minimizado (recolector 1.2.0)",
       "cuentas sin shell válida bloqueadas"),
    _C("p1_tmout", _T("Ensure default user shell timeout is configured"), ("p1.perfiles",), ev_tmout, "Lectura estática de perfiles",
       "TMOUT 1..900, readonly y export"),
    _C("p1_umask", _T("Ensure default user umask is configured"), ("p1.perfiles",), ev_umask, "Lectura estática de perfiles, login.defs y pam_umask",
       "umask 027 o más restrictivo y ninguno menos restrictivo"),
    _C("p1_sshd_config", _T("Ensure permissions on /etc/ssh/sshd_config are configured", "Ensure access to /etc/ssh/sshd_config is configured"),
       ("p1.ssh_permisos",), ev_ssh_config_permisos, "stat", "<= 0600 root:root (incluye sshd_config.d)", permite_parcial=False),
    _C("p1_ssh_priv", _T("Ensure permissions on SSH private host key files are configured", "Ensure access to SSH private host key files is configured"),
       ("p1.ssh_permisos", "usr.group"), ev_ssh_llaves(True), "stat", "root:root <= 0600 o root:ssh_keys <= 0640", permite_parcial=False),
    _C("p1_ssh_pub", _T("Ensure permissions on SSH public host key files are configured", "Ensure access to SSH public host key files is configured"),
       ("p1.ssh_permisos",), ev_ssh_llaves(False), "stat", "<= 0644 root:root", permite_parcial=False),
]
_SSHD = [
    ("access", "Ensure sshd access is configured", lambda v: any(v.values()), "AllowUsers/AllowGroups/DenyUsers/DenyGroups definidos",
     ("allowusers", "allowgroups", "denyusers", "denygroups")),
    ("loglevel", "Ensure sshd LogLevel is configured", lambda v: (v["loglevel"] or "").upper() in ("INFO", "VERBOSE"), "INFO o VERBOSE", ()),
    ("disableforwarding", "Ensure sshd DisableForwarding is enabled", lambda v: v["disableforwarding"] == "yes", "yes", ()),
    ("maxauthtries", "Ensure sshd MaxAuthTries is configured", lambda v: 0 < (_num(v["maxauthtries"]) or 99) <= 4, "<= 4", ()),
    ("ignorerhosts", "Ensure sshd IgnoreRhosts is enabled", lambda v: v["ignorerhosts"] == "yes", "yes", ()),
    ("hostbasedauthentication", "Ensure sshd HostbasedAuthentication is disabled", lambda v: v["hostbasedauthentication"] == "no", "no", ()),
    ("permitrootlogin", "Ensure sshd PermitRootLogin is disabled", lambda v: v["permitrootlogin"] == "no", "no", ()),
    ("permitemptypasswords", "Ensure sshd PermitEmptyPasswords is disabled", lambda v: v["permitemptypasswords"] == "no", "no", ()),
    ("permituserenvironment", "Ensure sshd PermitUserEnvironment is disabled", lambda v: v["permituserenvironment"] == "no", "no", ()),
    ("clientalive", "Ensure sshd ClientAliveInterval and ClientAliveCountMax are configured",
     lambda v: (_num(v["clientaliveinterval"]) or 0) > 0 and (_num(v["clientalivecountmax"]) or 0) > 0, "ambos > 0",
     ("clientaliveinterval", "clientalivecountmax")),
    ("logingracetime", "Ensure sshd LoginGraceTime is configured", lambda v: 0 < (_num(v["logingracetime"]) or 0) <= 60, "1..60", ()),
    ("banner", "Ensure sshd Banner is configured", lambda v: (v["banner"] or "none") != "none", "ruta de banner (no none)", ()),
    ("usepam", "Ensure sshd UsePAM is enabled", lambda v: v["usepam"] == "yes", "yes", ()),
    ("maxstartups", "Ensure sshd MaxStartups is configured", _maxstartups, "10:30:60 o más restrictivo", ()),
    ("maxsessions", "Ensure sshd MaxSessions is configured", lambda v: 0 < (_num(v["maxsessions"]) or 99) <= 10, "<= 10", ()),
]
for _k, _tit, _ok, _esp, _varias in _SSHD:
    CONTROLES_P1.append(_C(f"p1_sshd_{_k}", _T(_tit), ("p1.sshd", "priv.ssh_efectiva"), ev_sshd(_k, _ok, _esp, _varias),
                           "sshd -T (configuración efectiva; recolector 1.2.0)", _esp))

# Clasificación de privilegios:
# - EXTENSION_P1: el dato exige el recolector privilegiado (priv.*); sin él -> PERMISOS_INSUFICIENTES (ERROR DE AUDITORÍA).
# - EXTENSION_CONDICIONAL_P1: se evalúa sin sudo; solo si el host tiene archivos relevantes no legibles
#   (endurecimiento de permisos) requiere la evidencia privilegiada (p1.ssh_permisos/p1.sysctl_archivos).
# - CUBIERTO_1_1_0: evidencia privilegiada incluida en el primer conjunto de grupos del recolector.
EXTENSION_P1 = {"p1_sudo_pty", "p1_sudo_pass", "p1_sudo_reauth", "p1_sudo_timeout", "p1_pass_max", "p1_pass_min",
                "p1_pass_warn", "p1_inactive", "p1_ultimo_cambio", "p1_bloqueadas"} | {f"p1_sshd_{k}" for k, *_ in _SSHD}
EXTENSION_CONDICIONAL_P1 = {c.id for c in CONTROLES_P1 if c.id.startswith("p1_sysctl_")} | {"p1_sshd_config"}
CUBIERTO_1_1_0 = {"p1_sudo_log"}
MANUAL_P1 = {"p1_ipv6", "p1_pw_complex"}

# Cada control CIS de la Parte 1 es un requisito único: una falla demostrada es NO CUMPLE (no PARCIAL).
# El parcial solo se usa al agregar varios controles CIS de una misma fila (MAPEADO_DIVIDIDO).
for _c in CONTROLES_P1:
    _c.permite_parcial = False

PAQUETES_P1 = {
    **{c: tuple(dict.fromkeys(p + _PKG_SLES.get(c, ()) + _PKG_RHEL.get(c, ()))) for c, _, _, p, _ in _SRV},
    "p1_xorg": ("xorg-x11-server-common",) + _PKG_SLES["p1_xorg"], "p1_nis_cli": ("ypbind",), "p1_telnet_cli": ("telnet",),
    "p1_ldap_cli": ("openldap-clients",) + _PKG_SLES["p1_ldap_cli"], "p1_tftp_cli": ("tftp",), "p1_sudo": ("sudo",),
    "p1_chrony_uso": ("chrony",), "p1_chrony_conf": ("chrony",), "p1_cron_daemon": ("cronie", "cron"),
    "p1_crontab_v2": ("cronie", "cron"), "p1_crontab_v4": ("cronie", "cron"), "p1_at": ("at",),
    **{f"p1_cron_{x}": ("cronie", "cron") for x in ("crontab", "cron_hourly", "cron_daily", "cron_weekly", "cron_monthly", "cron_d")},
}

# Adaptaciones documentadas del procedimiento Audit (nunca del requisito).
_ADAPT_MOD = dict(
    procedimiento_original="modprobe --showconfig | grep install/blacklist; lsmod; readlink -f /lib/modules/**/kernel/<tipo>",
    comportamiento_esperado="Módulo no disponible, o no cargado + 'install <m> /bin/false' + 'blacklist <m>'",
    adaptacion="Se leen las fuentes de --showconfig (modprobe.d en /etc, /run, /usr/local/lib, /usr/lib y /lib con su precedencia por nombre de "
               "archivo, y modprobe.blacklist de /proc/cmdline), /proc/modules y los archivos .ko de /lib/modules",
    justificacion="La regla de seguridad del proyecto prohíbe invocar modprobe; las fuentes leídas son las mismas que usa --showconfig.")
ADAPTACIONES_P1 = {
    **{f"p1_mod_{m.replace('-', '_')}": _ADAPT_MOD for (m,) in _MOD},
    "p1_wireless": _ADAPT_MOD,
    "p1_inactive": dict(procedimiento_original="useradd -D | grep INACTIVE", comportamiento_esperado="INACTIVE <= 45 (y por cuenta)",
                        adaptacion="Lectura de /etc/default/useradd (fuente de 'useradd -D'); ausente = -1",
                        justificacion="La regla de seguridad prohíbe invocar useradd."),
    "p1_mta": dict(procedimiento_original="ss -plntu", comportamiento_esperado="25/465/587 solo en loopback",
                   adaptacion="ss -lntu sin -p", justificacion="El nombre del proceso (que exige root) no interviene en el criterio."),
}
