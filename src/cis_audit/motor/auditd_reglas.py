"""Interpretación y comparación de reglas de auditd.

Se normalizan tanto las reglas en disco (formato de rules.d) como las cargadas
(`auditctl -l`), y se comparan contra las reglas esperadas del CIS por
componentes (tipo, arquitectura, syscalls, filtros, ruta, permisos, key), no
por coincidencia literal de texto. Un conjunto de reglas puede repartir las
syscalls esperadas en varias líneas (el CIS lo admite explícitamente).
"""

import shlex
from dataclasses import dataclass
from typing import FrozenSet, List, Optional, Set, Tuple

_AUID_UNSET = {"auid!=unset", "auid!=-1", "auid!=4294967295"}


@dataclass(frozen=True)
class Regla:
    tipo: str                    # "watch" | "syscall" | "control"
    texto: str
    ruta: Optional[str] = None
    permisos: FrozenSet[str] = frozenset()
    arch: Optional[str] = None
    syscalls: FrozenSet[str] = frozenset()
    filtros: FrozenSet[str] = frozenset()
    key: Optional[str] = None


def _norm_ruta(p: str) -> str:
    p = p.rstrip("/") or "/"
    return "/run" + p[len("/var/run"):] if p.startswith("/var/run") else p


def _norm_filtro(f: str) -> str:
    f = f.replace(" ", "")
    if f in _AUID_UNSET:
        return "auid!=unset"
    if f in ("euid!=uid", "uid!=euid"):
        return "euid!=uid"
    return f


def parsear(linea: str) -> Optional[Regla]:
    s = linea.strip()
    if ":" in s.split(" ")[0] and s.split(":", 1)[0].endswith(".rules"):
        s = s.split(":", 1)[1].strip()  # prefijo de archivo de grep -H
    if not s or s.startswith("#"):
        return None
    try:
        t = shlex.split(s)
    except ValueError:
        t = s.split()
    if not t:
        return None
    if t[0] in ("-D", "-b", "-f", "-e", "-r", "--backlog_wait_time", "--loginuid-immutable", "-i", "-c"):
        return Regla("control", s)
    ruta, perms, arch, key = None, frozenset(), None, None
    sysc: Set[str] = set()
    filtros: Set[str] = set()
    es_watch = False
    i = 0
    while i < len(t):
        tok = t[i]
        val = t[i + 1] if i + 1 < len(t) else ""
        if tok == "-w":
            es_watch, ruta = True, _norm_ruta(val); i += 2; continue
        if tok == "-p":
            perms = frozenset(val); i += 2; continue
        if tok == "-k":
            key = val; i += 2; continue
        if tok == "-a":
            i += 2; continue
        if tok == "-S":
            if val != "all":
                sysc.update(x for x in val.split(",") if x)
            i += 2; continue
        if tok in ("-F", "-C"):
            f = _norm_filtro(val)
            if f.startswith("arch="):
                arch = f.split("=", 1)[1]
            elif f.startswith("key="):
                key = f.split("=", 1)[1]
            elif f.startswith("dir=") or f.startswith("path="):
                ruta = _norm_ruta(f.split("=", 1)[1])
                if f.startswith("path="):
                    filtros.add("path=" + ruta)
            elif f.startswith("perm="):
                perms = frozenset(f.split("=", 1)[1])
            else:
                filtros.add(f)
            i += 2; continue
        i += 1
    # Watch expresado como regla always,exit con dir=/path= y perm sin syscalls ni otros filtros
    if not es_watch and ruta and perms and not sysc and not arch and not (filtros - {"path=" + ruta}):
        es_watch = True
    if es_watch:
        return Regla("watch", s, ruta=ruta, permisos=perms, key=key)
    return Regla("syscall", s, ruta=ruta if any(f.startswith("path=") for f in filtros) else None,
                 permisos=perms, arch=arch, syscalls=frozenset(sysc), filtros=frozenset(filtros), key=key)


def parsear_todas(lineas: List[str]) -> List[Regla]:
    return [r for r in (parsear(l) for l in lineas) if r is not None]


def cumple(esperada: Regla, reglas: List[Regla]) -> Tuple[bool, str]:
    """¿El conjunto `reglas` satisface la regla esperada? Devuelve (ok, detalle)."""
    if esperada.tipo == "watch":
        for r in reglas:
            if r.tipo == "watch" and r.ruta == esperada.ruta and esperada.permisos <= r.permisos and r.key:
                return True, r.texto
        return False, f"falta watch {esperada.ruta} -p {''.join(sorted(esperada.permisos))} con key"
    candidatas = [
        r for r in reglas
        if r.tipo == "syscall" and r.key and r.arch == esperada.arch
        and esperada.filtros <= r.filtros and esperada.permisos <= r.permisos
        and (esperada.ruta is None or r.ruta == esperada.ruta)
    ]
    if esperada.syscalls:
        cubiertas = set()
        for r in candidatas:
            cubiertas |= r.syscalls & esperada.syscalls
        faltan = esperada.syscalls - cubiertas
        if not faltan:
            return True, "; ".join(r.texto for r in candidatas if r.syscalls & esperada.syscalls)
        return False, f"syscalls sin regla equivalente ({esperada.arch}, filtros {sorted(esperada.filtros)}): {sorted(faltan)}"
    if candidatas:
        return True, candidatas[0].texto
    return False, f"falta regla equivalente a: {esperada.texto}"


def esperadas(textos: List[str], uid_min: int) -> List[Regla]:
    return [parsear(t.replace("{UID_MIN}", str(uid_min))) for t in textos]
