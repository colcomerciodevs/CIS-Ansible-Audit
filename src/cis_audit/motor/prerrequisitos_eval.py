"""Prerrequisitos internos de la Parte 1 (PRE-1..PRE-5): requisito interno, fuera de los indicadores CIS.

Fuente del criterio: config/prerrequisitos_parte1.yml (fuente activa única). Se valida exactamente el criterio definido,
con la evidencia de la ejecución. Fail-closed: sin evidencia, ERROR DE AUDITORÍA; si la evidencia procede de un recolector
que no obtiene el dato (PRE-3: priv.authorized_keys, desde 1.5.0), REQUIERE VALIDACIÓN MANUAL. Nunca CUMPLE sin evidencia
de la ejecución.
"""

from typing import Dict, List, Optional, Tuple

CUMPLE, NO_CUMPLE, NO_APLICA, ERROR = "CUMPLE", "NO CUMPLE", "NO APLICA", "ERROR DE AUDITORÍA"
MANUAL = "REQUIERE VALIDACIÓN MANUAL"


# ------------------------------------------------------------------ evaluación con la evidencia actual

def _lineas(regs: Dict, cid: str) -> Optional[List[str]]:
    r = regs.get(cid)
    if r is None or r.omitido or r.rc not in (0,):
        return None
    return [l for l in (r.stdout or "").splitlines() if l.strip() and not l.startswith("#")]


def _passwd(regs) -> Optional[set]:
    l = _lineas(regs, "usr.passwd")
    return None if l is None else {x.split(":", 1)[0] for x in l if ":" in x}


_AK = "priv.authorized_keys"


def _seguro(campos: List[str], usuario: str) -> Optional[str]:
    """Problema de modo/propietario ('%#a', '%U') de un archivo o directorio de llaves, o None (criterio de StrictModes)."""
    if len(campos) < 2:
        return "metadatos no disponibles"
    try:
        modo = int(campos[0], 8)
    except ValueError:
        return f"metadatos no interpretables ({'|'.join(campos)[:80]})"
    if campos[1] not in (usuario, "root"):
        return f"propietario {campos[1]} (se exige {usuario} o root)"
    if modo & 0o022:
        return f"modo {campos[0]} escribible por grupo/otros"
    return None


def _llaves(crit: Dict, us: List[str], regs: Dict) -> Tuple[str, str, List[str]]:
    """PRE-3: cada cuenta tiene la llave esperada (por fingerprint SHA256) en un archivo AuthorizedKeysFile efectivo, con
    archivo y directorio no escribibles por terceros. Solo fingerprints: el recolector nunca entrega la llave."""
    from .comandos_priv import USUARIOS_LLAVES
    fp = crit.get("fingerprints") or {}
    r = regs.get(_AK)
    if r is None or r.omitido or not r.sudo:
        return (MANUAL, "Dato no disponible en esta ejecución: las llaves autorizadas las entrega el recolector privilegiado 1.5.0 "
                        "(priv.authorized_keys). Validar manualmente, por fingerprint, las llaves esperadas: "
                        + "; ".join(f"{u}: {fp.get(u, 'fingerprint no definido')}" for u in us) + ".", [_AK])
    l = [x for x in (r.stdout or "").splitlines() if x.strip()]
    if r.rc != 0 or "AK_FIN" not in l:
        return ERROR, f"Salida de {_AK} incompleta o con error (rc={r.rc}).", [_AK]
    cfg = next((x for x in l if x.startswith("AKCFG|")), "")
    if not cfg.startswith("AKCFG|archivos="):
        return ERROR, f"sshd -T no disponible en el host ({cfg or 'sin AKCFG'}): AuthorizedKeysFile efectivo no determinable.", [_AK]
    c = dict(x.split("=", 1) for x in cfg.split("|")[1:] if "=" in x)
    ev = [f"AuthorizedKeysFile efectivo (sshd -T): {c.get('archivos') or 'no definido'}; StrictModes: {c.get('strictmodes')}"
          + ("; AuthorizedKeysCommand configurado" if c.get("comando") == "1" else "")]
    estados = []
    for u in us:
        esperado = fp.get(u)
        if not esperado:
            estados.append(ERROR); ev.append(f"{u}: fingerprint esperado no definido en config/prerrequisitos_parte1.yml"); continue
        if u not in USUARIOS_LLAVES:
            estados.append(MANUAL); ev.append(f"{u}: cuenta no incluida en el recolector (USUARIOS_LLAVES): validar manualmente"); continue
        cu = [x.split("|") for x in l if x.startswith(f"AKU|{u}|")]
        if not cu:
            estados.append(ERROR); ev.append(f"{u}: sin registro de la cuenta en {_AK}"); continue
        if cu[0][2] == "NO_EXISTE":
            estados.append(NO_CUMPLE); ev.append(f"{u}: la cuenta no existe"); continue
        archivos = {x.split("|")[2]: x.split("|")[3:] for x in l if x.startswith(f"AKA|{u}|")}
        llaves = [x.split("|") for x in l if x.startswith(f"AKK|{u}|")]
        errores = [x.split("|")[2] for x in l if x.startswith(f"AKE|{u}|")]
        llaves = [k[:5] + [k[5].strip("()")] for k in llaves if len(k) >= 6]
        presentes = [k for k in llaves if k[4] == esperado]
        if presentes:
            f = presentes[0][2]
            m = archivos.get(f) or []
            meta = m[1:5] if len(m) >= 5 else []           # tipo, modo, propietario, grupo
            dirm = []
            for x in m:
                if x.startswith("dir="):
                    dirm = [x[4:]] + m[m.index(x) + 1:m.index(x) + 3]
            prob = None
            if not meta or meta[0] != "regular_file":
                prob = f"{f} no es un archivo regular ({meta[0] if meta else 'sin metadatos'})"
            else:
                pa, pd = _seguro(meta[1:3], u), _seguro(dirm[:2], u)
                prob = f"{f}: {pa}" if pa else (f"directorio de {f}: {pd}" if pd else None)
            if prob:
                estados.append(NO_CUMPLE)
                ev.append(f"{u}: llave esperada {esperado} presente ({presentes[0][5]}, {presentes[0][3]} bits) en {f}, pero {prob} "
                          "(sshd con StrictModes rechaza la llave; cualquier otro usuario podría modificarla)")
            else:
                estados.append(CUMPLE)
                ev.append(f"{u}: llave esperada {esperado} presente ({presentes[0][5]}, {presentes[0][3]} bits) en {f} "
                          f"(modo {meta[1]}, {meta[2]}:{meta[3]}; directorio {dirm[0] if dirm else 'n/d'})")
            continue
        encontrados = [f"{k[4]} ({k[5]})" for k in llaves]
        exist = [f for f, m in archivos.items() if m and m[0] != "NO_EXISTE"]
        detalle = (f"archivos: {', '.join(exist) if exist else 'ninguno existe (' + ', '.join(archivos) + ')'}; "
                   f"llaves encontradas: {', '.join(encontrados) if encontrados else 'ninguna'}")
        if errores:
            estados.append(ERROR); ev.append(f"{u}: ssh-keygen no pudo leer {', '.join(errores)}; {detalle}")
        elif c.get("comando") == "1":
            estados.append(MANUAL); ev.append(f"{u}: llave esperada {esperado} no está en los archivos; AuthorizedKeysCommand podría "
                                              f"entregarla (validar manualmente); {detalle}")
        else:
            estados.append(NO_CUMPLE); ev.append(f"{u}: llave esperada {esperado} NO configurada; {detalle}")
    est = next((e for e in (ERROR, MANUAL, NO_CUMPLE) if e in estados), CUMPLE)
    return est, "\n".join(ev), [_AK]


def evaluar(control: str, crit: Dict, regs: Dict) -> Tuple[str, str, List[str]]:
    """(resultado, evidencia/motivo legible, evidencia usada) para el criterio de config/prerrequisitos_parte1.yml."""
    crit = crit or {}
    t, us = crit.get("tipo"), crit.get("usuarios") or []
    if t == "sin_requisito":
        return NO_APLICA, "Sin requisito técnico definido para este prerrequisito (config/prerrequisitos_parte1.yml).", []
    if t == "cuentas_cambio_forzado":
        pw = _passwd(regs)
        from .controles_p1 import _CTAS
        sh = {}
        for l in _lineas(regs, _CTAS) or []:
            p = l.split("|")
            if p[0] == "U" and len(p) >= 6:
                sh[p[1]] = p[5]
        if pw is None or not sh:
            return ERROR, "Sin evidencia de /etc/passwd o /etc/shadow (priv.cuentas) en esta ejecución.", ["usr.passwd", _CTAS]
        ev = [f"{u}: " + ("no existe" if u not in pw else "existe; cambio de contraseña forzado (password must be changed)" if sh.get(u) == "0"
                          else f"existe; cambio de contraseña NO forzado (último cambio: día {sh.get(u) or 'no disponible'})") for u in us]
        ok = all(u in pw and sh.get(u) == "0" for u in us)
        return CUMPLE if ok else NO_CUMPLE, "\n".join(ev), ["usr.passwd", _CTAS]
    if t == "cuentas_nopasswd_all":
        from .controles_p1 import _SUDO
        l = _lineas(regs, _SUDO)
        if l is None:
            return ERROR, "Sin evidencia de la política sudo (priv.sudo_politica) en esta ejecución.", [_SUDO]
        reglas = {}
        for x in l:
            p = x.split("|")
            if p[0] == "NOPASSWD" and len(p) >= 5 and "cargado=1" in p[4:]:
                reglas.setdefault(p[3], []).append(f"{p[1]}:{p[2]}")
        # El recolector registra la regla NOPASSWD cargada (archivo:línea); nunca transcribe la lista de comandos.
        ev = [f"{u}: NOPASSWD ALL configurado (regla NOPASSWD cargada en {', '.join(reglas[u])})" if u in reglas
              else f"{u}: sin regla NOPASSWD cargada" for u in us]
        ok = all(u in reglas for u in us)
        return (CUMPLE if ok else NO_CUMPLE, "\n".join(ev) + "\nRequisito interno PRE-2: independiente del resultado técnico CIS 5.2.4.",
                [_SUDO])
    if t == "llaves_authorized_keys":
        return _llaves(crit, us, regs)
    if t == "grupo_miembros":
        l = _lineas(regs, "usr.group")
        if l is None:
            return ERROR, "Sin evidencia de /etc/group (usr.group) en esta ejecución.", ["usr.group"]
        g = next((x for x in l if x.split(":", 1)[0] == crit.get("grupo")), None)
        if g is None:
            return NO_CUMPLE, f"Grupo {crit.get('grupo')}: no existe", ["usr.group"]
        miembros = (g.split(":") + ["", "", ""])[2].split(",")
        ev = [f"Grupo {crit.get('grupo')}: existe"] + [f"{u}: {'miembro' if u in miembros else 'NO es miembro'}" for u in us]
        return CUMPLE if all(u in miembros for u in us) else NO_CUMPLE, "\n".join(ev), ["usr.group"]
    return ERROR, "Criterio no interpretable en config/prerrequisitos_parte1.yml.", []


def fuente_evidencia(control: str, crit: Dict) -> str:
    t = (crit or {}).get("tipo")
    return {"cuentas_cambio_forzado": "Evidencia de la ejecución: /etc/passwd (usr.passwd) y /etc/shadow (priv.cuentas, sin hashes)",
            "cuentas_nopasswd_all": "Evidencia de la ejecución: política sudo efectiva (priv.sudo_politica)",
            "llaves_authorized_keys": "Evidencia de la ejecución: llaves autorizadas por fingerprint (priv.authorized_keys, sin el texto de las llaves)",
            "grupo_miembros": "Evidencia de la ejecución: /etc/group (usr.group)",
            "sin_requisito": "Definición del prerrequisito (config/prerrequisitos_parte1.yml)"}.get(t, "—")
