"""Análisis ESTÁTICO del PATH de root (CIS 5.4.2.5, decisión R6 del usuario, 2026-10-08).

El procedimiento Audit del CIS obtiene el PATH con `sudo -Hiu root env`, lo que ejecuta el perfil
de login de root. En este proyecto eso está prohibido: los archivos de inicio contienen código shell
y ejecutarlos podría tener efectos secundarios. Se analizan solo las líneas recolectadas por
`usr.root_path` (PATH y estructura), sin ejecutar ni hacer `source` de nada.

Regla de decisión (conservadora):
- El PATH final solo se considera determinado si TODAS las modificaciones de PATH que alcanzan la
  sesión de login son asignaciones estáticas, de nivel superior, en archivos cargados de forma
  incondicional, y la primera asignación aplicada es absoluta (no depende del PATH heredado).
- Cualquier condicional, función (p. ej. pathmunge), bucle, sustitución de comandos, variable
  distinta de PATH/HOME, inclusión dinámica o condicional de archivos que afectan a PATH, o
  estructura no interpretable -> NO determinable -> REQUIERE VALIDACIÓN MANUAL, indicando
  archivo, línea y construcción.
"""

import re
from typing import Dict, List, Optional, Tuple

SHELLS_SOPORTADAS = ("/bin/bash", "/usr/bin/bash", "/bin/sh", "/usr/bin/sh")

_RE_MOD = re.compile(r"(?:^|[\s;&|(])(?:export\s+|readonly\s+|(?:declare|typeset)\s+(?:-\w+\s+)*|local\s+)?PATH(\+?)=")
_RE_PATHMUNGE_LLAMADA = re.compile(r"(?:^|[\s;&|(])pathmunge\s+(?!\(\))\S")
_RE_DEF_FUNCION = re.compile(r"^\s*(?:function\s+)?[A-Za-z_][\w-]*\s*\(\)\s*\{?|^\s*function\s+[A-Za-z_][\w-]*")
_RE_ABRE = re.compile(r"(?:^|[\s;&|(])(if|case|for|while|until|select)(?=\s|$)")
_RE_CIERRA = re.compile(r"(?:^|[\s;&|])(fi|esac|done)(?=\s|;|$|\))")
_RE_INCLUYE = re.compile(r"(?:^|[\s;&|])(?:\.|source)\s+(\S+)")
_RE_VAR = re.compile(r"\$\{?(\w+)")


def parsear(stdout: str) -> Dict:
    d = {"root_home": None, "root_shell": None, "archivos": {}, "lineas": {}, "directorios": {}}
    for l in (stdout or "").splitlines():
        if l.startswith("S|root|"):
            p = l.split("|")
            d["root_home"], d["root_shell"] = (p[2] if len(p) > 2 else None), (p[3] if len(p) > 3 else None)
        elif l.startswith("F|"):
            p = l.split("|")
            d["archivos"][p[1]] = None if p[2:3] == ["NO_EXISTE"] else {"owner": p[2], "group": p[3], "mode": p[4]} if len(p) >= 5 else {}
        elif l.startswith("L|"):
            _, f, resto = l.split("|", 2)
            n, _, txt = resto.partition(":")
            if n.isdigit():
                d["lineas"].setdefault(f, []).append((int(n), txt))
        elif l.startswith("D|"):
            p = l.split("|")
            d["directorios"][p[1]] = None if p[2:3] == ["NO_EXISTE"] else {"mode": p[2], "owner": p[3], "tipo": "|".join(p[4:])}
    for f in d["lineas"]:
        d["lineas"][f].sort()
    return d


def _limpiar(txt: str) -> str:
    """Quita contenido entre comillas simples y expansiones ${...} para contar estructura."""
    t = re.sub(r"'[^']*'", "''", txt)
    t = re.sub(r"\$\{[^}]*\}", "$V", t)
    return t


def _estructura(lineas: List[Tuple[int, str]]):
    """Profundidad de bloque antes de cada línea. Devuelve ({n: profundidad}, fiable, motivo)."""
    prof, res = 0, {}
    for n, txt in lineas:
        res[n] = prof
        t = _limpiar(txt)
        abre = len(_RE_ABRE.findall(t)) + t.count("{")
        cierra = len(_RE_CIERRA.findall(t)) + t.count("}")
        prof += abre - cierra
        if prof < 0:
            return res, False, f"línea {n}: cierre de bloque sin apertura (estructura no interpretable)"
    if prof != 0:
        return res, False, "bloques sin cerrar al final del archivo (estructura no interpretable)"
    return res, True, ""


def _expandir_ruta(t: str, home: str) -> str:
    t = t.strip("\"'")
    for pre in ("${HOME}", "$HOME", "~"):
        if t.startswith(pre):
            return home + t[len(pre):]
    return t


def _valor(txt: str, m: re.Match) -> str:
    resto = txt[m.end():]
    out, q = [], None
    for ch in resto:
        if q:
            if ch == q:
                q = None
            else:
                out.append(ch)
        elif ch in "\"'":
            q = ch
        elif ch in " \t;&|":
            break
        else:
            out.append(ch)
    return "".join(out)


def analizar(stdout: str) -> Dict:
    d = parsear(stdout)
    home, shell = d["root_home"] or "/root", d["root_shell"] or ""
    bloqueos: List[Dict] = []
    info = {"root_home": home, "root_shell": shell, "archivos_analizados": sorted(f for f, v in d["archivos"].items() if v is not None)}

    def bloqueo(archivo, n, construccion, texto=""):
        bloqueos.append({"archivo": archivo, "linea": n, "construccion": construccion, "texto": texto.strip()[:160]})

    if shell not in SHELLS_SOPORTADAS:
        bloqueo("/etc/passwd", None, f"shell de root '{shell}' no soportada por el análisis estático (solo bash/sh)")
        return {**info, "determinable": False, "path": None, "bloqueos": bloqueos, "directorios": d["directorios"]}

    estr, mods, incl, cortes = {}, {}, {}, {}
    for f, ls in d["lineas"].items():
        prof, fiable, motivo = _estructura(ls)
        estr[f] = (prof, fiable, motivo)
        for n, txt in ls:
            if _RE_CORTE.search(_limpiar(txt)):
                cortes.setdefault(f, []).append((n, txt))
            m = _RE_MOD.search(txt)
            if m:
                mods.setdefault(f, []).append((n, txt, m))
            if _RE_PATHMUNGE_LLAMADA.search(txt) and not _RE_DEF_FUNCION.search(txt):
                mods.setdefault(f, []).append((n, txt, None))
            for mi in _RE_INCLUYE.finditer(txt):
                incl.setdefault(f, []).append((n, txt, mi.group(1)))

    def modifica(f, vistos=()) -> bool:
        if f in vistos:
            return True  # inclusión recursiva: conservador
        if f in mods:
            return True
        for _, _, objetivo in incl.get(f, []):
            t = _expandir_ruta(objetivo, home)
            if "$" in t or "*" in t or t not in d["archivos"]:
                return True
            if d["archivos"].get(t) is not None and modifica(t, vistos + (f,)):
                return True
        return False

    secuencia: List[Tuple[str, int, str, re.Match]] = []
    visitados: List[str] = []

    def recorrer(f: str, pila=()):
        if f in pila:
            bloqueo(f, None, "inclusión recursiva de archivos"); return
        visitados.append(f)
        for n, txt in cortes.get(f, []):
            bloqueo(f, n, "construcción que puede terminar la carga del archivo o la sesión (return/exit/exec/logout)", txt)
        prof, fiable, motivo = estr.get(f, ({}, True, ""))
        if not fiable and (f in mods or f in incl):
            bloqueo(f, None, motivo)
        eventos = sorted([(n, "mod", txt, m) for n, txt, m in mods.get(f, [])] +
                         [(n, "inc", txt, obj) for n, txt, obj in incl.get(f, [])], key=lambda e: e[0])
        for n, tipo, txt, x in eventos:
            anidado = prof.get(n, 0) > 0 or bool(_RE_ABRE.search(_limpiar(txt))) or "&&" in txt or "||" in txt
            if tipo == "mod":
                if x is None:
                    bloqueo(f, n, "llamada a función que modifica PATH (pathmunge)", txt); continue
                if anidado:
                    bloqueo(f, n, "asignación de PATH condicional o dentro de bloque/función/bucle", txt); continue
                val = _valor(txt, x)
                if "$(" in txt or "`" in txt:
                    bloqueo(f, n, "sustitución de comandos en la asignación de PATH", txt); continue
                otras = {v for v in _RE_VAR.findall(val) if v not in ("PATH", "HOME")}
                if otras:
                    bloqueo(f, n, f"variable(s) no determinables en PATH: {', '.join(sorted(otras))}", txt); continue
                secuencia.append((f, n, txt, x))
            else:
                t = _expandir_ruta(x, home)
                if "$" in t or "*" in t or "?" in t:
                    bloqueo(f, n, "inclusión dinámica de archivos (destino no determinable estáticamente)", txt)
                    continue
                if t not in d["archivos"]:
                    bloqueo(f, n, f"inclusión de un archivo no analizado ({t})", txt); continue
                if d["archivos"][t] is None:
                    continue  # el destino no existe: la inclusión no tiene efecto
                if anidado:
                    if modifica(t):
                        bloqueo(f, n, f"inclusión condicional de {t}, que afecta a PATH", txt)
                    continue
                recorrer(t, pila + (f,))

    recorrer("/etc/profile")
    login = next((f for f in (f"{home}/.bash_profile", f"{home}/.bash_login", f"{home}/.profile")
                  if d["archivos"].get(f) is not None), None) if shell.endswith("bash") else \
        (f"{home}/.profile" if d["archivos"].get(f"{home}/.profile") is not None else None)
    if login:
        recorrer(login)
    info["archivos_cargados"] = visitados
    for f in mods:
        if f not in visitados and f not in ("/etc/login.defs", "/usr/etc/login.defs", "/etc/environment"):
            info.setdefault("archivos_no_cargados_con_path", []).append(f)

    if bloqueos:
        return {**info, "determinable": False, "path": None, "bloqueos": bloqueos, "directorios": d["directorios"]}

    path: Optional[str] = None
    for f, n, txt, m in secuencia:
        val = _valor(txt, m)
        if path is None and (m.group(1) == "+" or re.search(r"\$\{?PATH\b", val)):
            bloqueo(f, n, "la primera asignación depende del PATH heredado del proceso de sesión (sshd/login/su)", txt)
            break
        val = re.sub(r"\$\{?PATH\}?", path or "", val)
        val = re.sub(r"\$\{HOME\}|\$HOME", home, val)
        val = ":".join(home + p[1:] if p.startswith("~") else p for p in val.split(":"))
        path = (path or "") + val if m.group(1) == "+" else val
    if path is None and not bloqueos:
        bloqueo("-", None, "ningún archivo de inicio asigna PATH: el valor proviene del proceso de sesión (sshd/login/su) y no es determinable estáticamente")
    if bloqueos:
        return {**info, "determinable": False, "path": None, "bloqueos": bloqueos, "directorios": d["directorios"]}
    return {**info, "determinable": True, "path": path, "bloqueos": [], "directorios": d["directorios"],
            "origen": [f"{f}:{n}" for f, n, _, _ in secuencia]}


def observaciones_directorios(directorios: Dict) -> List[str]:
    """Defectos observados en directorios candidatos (no concluyentes si el PATH no es determinable)."""
    out = []
    for p, v in sorted(directorios.items()):
        if v is None:
            out.append(f"{p}: no existe")
            continue
        if "directory" not in v["tipo"]:
            out.append(f"{p}: no es un directorio ({v['tipo']})")
        if v["owner"] != "root":
            out.append(f"{p}: propietario {v['owner']}")
        try:
            if int(v["mode"], 8) & 0o022:
                out.append(f"{p}: modo {v['mode']} (escribible por grupo/otros)")
        except ValueError:
            out.append(f"{p}: modo no interpretable ({v['mode']})")
    return out


# ------------------------------------------------------------------ entradas GARANTIZADAS (2026-10-14)
# Cuando el PATH completo no es determinable, ciertas entradas sí lo son: las que un archivo de inicio cargado con
# seguridad añade siempre con el idioma idempotente de RHEL/OL
#     if ! [[ "$PATH" =~ "<A>:<B>:" ]]
#     then
#         PATH="<A>:<B>:$PATH"
#     fi
# (si la condición es falsa, "<A>:<B>:" ya está en PATH: en ambos casos A y B forman parte del PATH final), siempre que
# ninguna asignación posterior reemplace el PATH sin conservar $PATH. Una entrada garantizada con defecto CIS es una
# falla concluyente aunque el resto del PATH no sea determinable. Nunca se ejecuta ni se hace source de nada (R6).
_RE_IDIOMA = re.compile(r'^\s*if\s+!\s+\[\[\s+"\$PATH"\s+=~\s+"([^"]+)"\s+\]\]\s*(;\s*then)?\s*$')
_RE_GUARDA_F = re.compile(r'^\s*if\s+\[\s+-[fr]\s+("?)([^"\s\]]+)\1\s+\]\s*;\s*then\s*$')


# Construcciones que terminan la carga de un archivo de inicio (o la sesión) antes de llegar a una línea posterior.
_RE_CORTE = re.compile(r"(?:^|[\s;&|(])(return|exit|exec|logout)(?=\s|;|$|\))")
# Marca que el recolector emite cuando sus líneas L| incluyen también return/exit/exec/logout (flujo completo).
MARCA_FLUJO = "FLUJO|1"
LIMITACION_SIN_FLUJO = ("la evidencia recolectada solo contiene las líneas de PATH y de estructura (if/fi, inclusiones) de los "
                        "archivos de inicio; no incluye las líneas return/exit/exec que podrían terminar la carga de un archivo "
                        "antes de esa línea, por lo que no se puede demostrar que la línea se ejecute")


def incorporaciones(stdout: str) -> Dict:
    """Entradas que los archivos de inicio de root añaden al PATH con el idioma idempotente de RHEL/OL.

    {"flujo_recolectado": bool, "entradas": [{directorio, origen ('archivo:línea'), cadena, demostrada, motivo}]}.
    Una entrada solo está DEMOSTRADA si la evidencia incluye el flujo completo (MARCA_FLUJO) y ninguna construcción
    return/exit/exec/logout puede cortar la carga antes de la línea. Sin flujo completo es una REFERENCIA POTENCIAL
    (declarada en un archivo que se carga, pero no demostrada en el PATH efectivo). Nunca se ejecuta nada (R6)."""
    d = parsear(stdout)
    flujo = any(l.strip() == MARCA_FLUJO for l in (stdout or "").splitlines())
    res = {"flujo_recolectado": flujo, "entradas": []}
    home, shell = d["root_home"] or "/root", d["root_shell"] or ""
    if not shell.endswith("bash"):
        return res
    login = next((f for f in (f"{home}/.bash_profile", f"{home}/.bash_login", f"{home}/.profile")
                  if d["archivos"].get(f) is not None), None)
    if not login:
        return res
    eventos: List[Tuple[str, int, str]] = []   # (archivo, línea, texto) en orden de ejecución, siguiendo inclusiones seguras
    cadena_de: Dict[str, List[str]] = {login: [login]}
    ancestros: Dict[str, List[str]] = {login: [login]}   # archivos cuya carga está en curso cuando se ejecuta cada archivo

    def recorrer(f: str, pila=()):
        if f in pila:
            return
        ls = d["lineas"].get(f, [])
        prof, fiable, _ = _estructura(ls)
        if not fiable:
            eventos.append((f, 0, "<estructura no interpretable>")); return
        for k, (n, txt) in enumerate(ls):
            eventos.append((f, n, txt))
            for mi in _RE_INCLUYE.finditer(txt):
                t = _expandir_ruta(mi.group(1), home)
                if "$" in t or "*" in t or d["archivos"].get(t) is None:
                    continue
                ok = False
                if prof.get(n, 0) == 0 and not _RE_ABRE.search(_limpiar(txt)) and "&&" not in txt and "||" not in txt:
                    ok = True
                elif prof.get(n, 0) == 1 and k > 0:
                    g = _RE_GUARDA_F.match(ls[k - 1][1])
                    # 'if [ -f X ]; then . X; fi' con X existente: la inclusión ocurre siempre.
                    ok = bool(g and _expandir_ruta(g.group(2), home) == t and k + 1 < len(ls) and ls[k + 1][1].strip() == "fi")
                if ok:
                    cadena_de.setdefault(t, cadena_de.get(f, [f]) + [f"{t} (incluido en {f}:{n})"])
                    ancestros.setdefault(t, ancestros.get(f, [f]) + [t])
                    recorrer(t, pila + (f,))

    recorrer(login)
    vistos: List[str] = []
    for i, (f, n, txt) in enumerate(eventos):
        m = _RE_IDIOMA.match(txt)
        if not m:
            continue
        patron = m.group(1)
        sig = [e for e in eventos[i + 1:i + 4] if e[0] == f]
        asign = next((e for e in sig if _RE_MOD.search(e[2])), None)
        if not asign or not any(e[2].strip() == "fi" for e in sig):
            continue
        val = _valor(asign[2], _RE_MOD.search(asign[2]))
        if val.replace("${PATH}", "$PATH") != patron + "$PATH":
            continue
        posteriores = [e for e in eventos[eventos.index(asign) + 1:] if _RE_MOD.search(e[2]) or "unset PATH" in e[2]]
        if any("unset PATH" in e[2] or not re.search(r"\$\{?PATH\b", _valor(e[2], _RE_MOD.search(e[2])) if _RE_MOD.search(e[2]) else "")
               for e in posteriores):
            continue
        # exit/exec/logout terminan la sesión en cualquier archivo; return solo termina el archivo en el que está, así que
        # corta la línea solo si está en el mismo archivo o en uno de los que lo incluyen (no en un archivo ya terminado).
        en_curso = set(ancestros.get(f, [f]))
        cortes = [f"{e[0]}:{e[1]} «{e[2].strip()[:80]}»" for e in eventos[:i]
                  if (m_ := _RE_CORTE.search(_limpiar(e[2]))) and (m_.group(1) != "return" or e[0] in en_curso)]
        # /etc/profile se ejecuta antes que el perfil de root: un exit/exec/logout allí también corta la sesión.
        cortes += [f"/etc/profile:{n} «{x.strip()[:80]}»" for n, x in d["lineas"].get("/etc/profile", [])
                   if (m_ := _RE_CORTE.search(_limpiar(x))) and m_.group(1) != "return"]
        if not flujo:
            demostrada, motivo = False, LIMITACION_SIN_FLUJO
        elif cortes:
            demostrada, motivo = False, "antes de esa línea hay construcciones que pueden terminar la carga: " + "; ".join(cortes[:5])
        else:
            demostrada, motivo = True, ""
        for x in [_expandir_ruta(x, home) for x in patron.split(":") if x]:
            if x in vistos:
                continue
            vistos.append(x)
            res["entradas"].append({"directorio": x, "origen": f"{asign[0]}:{asign[1]}", "condicion": f"{f}:{n}",
                                    "asignacion": asign[2].strip(), "cadena": " → ".join(cadena_de.get(f, [f])),
                                    "demostrada": demostrada, "motivo": motivo})
    return res


def entradas_garantizadas(stdout: str) -> List[Tuple[str, str]]:
    """[(directorio, 'archivo:línea')] de entradas añadidas con el idioma idempotente (demostradas o potenciales)."""
    return [(e["directorio"], e["condicion"]) for e in incorporaciones(stdout)["entradas"]]
