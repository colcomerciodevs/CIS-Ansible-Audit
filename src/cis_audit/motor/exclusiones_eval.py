"""Evaluación de las exclusiones de Infraestructura (independiente del Resultado CIS).

Cada regla de clasificación de la matriz (`exclusiones.REGLAS`) tiene aquí su
prueba técnica o su tratamiento organizacional. La exclusión nunca modifica el
requisito ni el resultado CIS (D-04, D-06).

Estados: CUMPLE, NO CUMPLE, PARCIAL, NINGUNA, NO APLICA,
REQUIERE EVIDENCIA ORGANIZACIONAL, NO VERIFICABLE TÉCNICAMENTE.
"""

from typing import Dict, List, Optional

from .base import Ctx
from .controles import _auditd_conf, _destinos_remotos, _gdm_instalado, _journald, homes, modo_ok

CUMPLE, NO_CUMPLE, PARCIAL = "CUMPLE", "NO CUMPLE", "PARCIAL"
NINGUNA, NO_APLICA = "NINGUNA", "NO APLICA"
ORG = "REQUIERE EVIDENCIA ORGANIZACIONAL"
NO_VERIF = "NO VERIFICABLE TÉCNICAMENTE"
ERROR = "ERROR DE AUDITORÍA"


def _r(estado, esperado="", encontrado="", evidencia=None, que_falta=None):
    return {"estado": estado, "esperado": esperado, "encontrado": encontrado,
            "evidencia": evidencia or [], "que_falta": que_falta}


INTERPRETACION_15GB = "15 GB = 15.000.000.000 bytes (interpretación inicial documentada; 15 GiB serían 16.106.127.360 bytes)"


def _particion(ctx: Ctx, p: Dict) -> Dict:
    minimo = int(ctx.config.get("particion_audit_min_bytes", 15 * 10 ** 9))
    prob = ctx.problema("sys.var_log_audit")
    l = ctx.lineas("sys.var_log_audit")
    if prob or not l:
        return _r(ERROR, "/var/log/audit montaje propio >= 15 GB", prob or "[NO_RECOLECTADO] sin dato", ["sys.var_log_audit"])
    target, source, fstype, size = (l[0].split() + ["", "", "", "0"])[:4]
    bytes_ = int(size) if size.isdigit() else 0
    datos = {"punto_montaje": target, "dispositivo": source, "filesystem": fstype, "tamano_bytes": bytes_,
             "tamano_gib": round(bytes_ / 2 ** 30, 2), "umbral_bytes": minimo, "interpretacion": INTERPRETACION_15GB}
    propia = target == "/var/log/audit"
    tam_ok = bytes_ >= minimo
    prob_conf = ctx.problema("auditd.conf")
    rot = None if prob_conf else (_auditd_conf(ctx).get("max_log_file_action") or "").lower()
    # El tamaño mínimo es una recomendación de Infraestructura (ajuste 2026-10-27): por debajo de 15 GB es una
    # ADVERTENCIA y no impide que la exclusión quede aplicada si la partición propia y la rotación lo están.
    partes = [propia] + ([] if rot is None else [rot == "rotate"])
    enc = (f"punto de montaje que contiene /var/log/audit: {target}; dispositivo {source}; filesystem {fstype}; "
           f"tamaño {bytes_} bytes ({datos['tamano_gib']} GiB); max_log_file_action="
           + ("no verificable sin privilegios [PERMISOS_INSUFICIENTES]" if rot is None else (rot or "no definido")))
    falta = []
    if not propia:
        falta.append(f"/var/log/audit no es una partición independiente (está en {target})")
    advertencias = [] if tam_ok else [f"ADVERTENCIA: tamaño {bytes_} bytes ({datos['tamano_gib']} GiB) inferior al mínimo "
                                       f"recomendado de {minimo} bytes (15 GB)"]
    if rot is None:
        falta.append("rotación de logs no verificable: auditd.conf no legible sin sudo -n")
    elif rot != "rotate":
        falta.append("rotación de logs (max_log_file_action=rotate) no configurada")
    # Las tres condiciones son obligatorias e independientes: cualquier incumplimiento demostrado es
    # NO CUMPLE (no PARCIAL). Una condición no verificable se documenta aparte como no evaluada.
    condiciones = {"particion_independiente": "CUMPLE" if propia else "NO CUMPLE",
                   "tamano_minimo": "CUMPLE" if tam_ok else "ADVERTENCIA",
                   "rotacion_configurada": "NO EVALUADA (PERMISOS_INSUFICIENTES)" if rot is None else ("CUMPLE" if rot == "rotate" else "NO CUMPLE")}
    datos["condiciones"] = condiciones
    if not all(partes):
        est = NO_CUMPLE
    elif rot is None:
        est = ERROR  # lo verificable cumple, pero una condición obligatoria no pudo evaluarse
    else:
        est = CUMPLE
    r = _r(est, f"/var/log/audit montaje propio y rotación configurada; tamaño recomendado >= {minimo} bytes ({INTERPRETACION_15GB})",
           enc, ["sys.var_log_audit", "auditd.conf"], "; ".join(falta) or None)
    r["datos"], r["advertencias"] = datos, advertencias
    return r


def _valores_auditd(ctx: Ctx, esperado: Dict[str, str]) -> Dict:
    prob = ctx.problema("auditd.conf")
    if prob:
        return _r(ERROR, str(esperado), prob, ["auditd.conf"])
    conf = _auditd_conf(ctx)
    malos = {k: conf.get(k.lower()) for k, v in esperado.items() if (conf.get(k.lower()) or "").lower() != v.lower()}
    n_ok = len(esperado) - len(malos)
    est = CUMPLE if not malos else (PARCIAL if n_ok else NO_CUMPLE)
    return _r(est, ", ".join(f"{k}={v}" for k, v in esperado.items()),
              ", ".join(f"{k}={conf.get(k.lower())}" for k in esperado), ["auditd.conf"],
              "; ".join(f"{k} debe ser {esperado[k]} (encontrado {v})" for k, v in malos.items()) or None)


def _journald_vals(ctx: Ctx, esperado: Dict[str, str]) -> Dict:
    prob = ctx.problema("journald.conf")
    if prob:
        return _r(ERROR, str(esperado), prob, ["journald.conf"])
    eff = _journald(ctx)
    malos = {k: (eff.get(k) or (None,))[0] for k, v in esperado.items()
             if not eff.get(k) or eff[k][0].lower() != v.lower()}
    est = CUMPLE if not malos else (PARCIAL if len(malos) < len(esperado) else NO_CUMPLE)
    return _r(est, ", ".join(f"{k}={v}" for k, v in esperado.items()),
              ", ".join(f"{k}={(eff.get(k) or (None,))[0]}" for k in esperado), ["journald.conf"],
              "; ".join(f"{k} debe ser {esperado[k]} (encontrado {v})" for k, v in malos.items()) or None)


def _usuarios_login(ctx: Ctx) -> Dict:
    prob = ctx.problema("usr.homes")
    if prob:
        return _r(ERROR, "homes de usuarios bash/sh", prob, ["usr.homes"])
    infra = homes(ctx, "infra")
    cis = {f[0] for f in homes(ctx, "cis")}
    malos = []
    for u, h, sh, own, modo in [(f + [""] * 5)[:5] for f in infra]:
        if own == "NO_EXISTE":
            malos.append(f"{u}: home {h} no existe")
        else:
            if own != u:
                malos.append(f"{u}: propietario {own}")
            if modo and not modo_ok(modo, 0o027):
                malos.append(f"{u}: modo {modo}")
    dif = sorted(cis - {f[0] for f in infra})
    enc = f"{len(infra)} usuarios con /bin/bash o /bin/sh evaluados"
    if dif:
        enc += f"; usuarios interactivos según CIS no incluidos por la definición de Infraestructura: {dif}"
    return _r(NO_CUMPLE if malos else CUMPLE, "home existente, propio y <= 0750 para usuarios con /bin/bash o /bin/sh",
              enc, ["usr.homes", "sys.shells"], "; ".join(malos) or None)


def logs_autorizado(def_: Dict, archivo: str, modo: str, propietario: str) -> Optional[str]:
    """None si el archivo está dentro de la exclusión ARCHIVOS_LOG_AUTORIZADOS y cumple su CONDICION; si no, el motivo."""
    import re
    if not any(re.match(p, archivo) for p in def_["patrones"]):
        return "no coincide con los archivos de la exclusión"
    if propietario != def_["propietario"]:
        return f"propietario {propietario} (la exclusión exige {def_['propietario']})"
    try:
        m = int(modo, 8)
    except ValueError:
        return f"modo no interpretable ({modo})"
    if m & ~def_["modo_max"] & 0o7777:
        return f"modo {modo} más permisivo que {def_['modo_max_texto']}"
    return None


def _logs_autorizados(ctx: Ctx, texto_excl) -> Dict:
    from ..exclusiones import archivos_log_autorizados
    d = archivos_log_autorizados(texto_excl)
    esperado = "Archivos de la exclusión con su CONDICION (propietario y modo máximo)"
    if d is None:
        return _r(NO_VERIF, esperado, "Exclusión sin ARCHIVOS/CONDICION interpretables", que_falta="Definir ARCHIVOS y CONDICION con la sintaxis documentada")
    prob = ctx.problema("logs.permisos")
    if prob:
        return _r(ERROR, esperado, prob, ["logs.permisos"])
    import re
    vistos, malos = [], []
    for l in ctx.lineas("logs.permisos"):
        p = l.split("^")
        if len(p) != 6 or not any(re.match(x, p[0]) for x in d["patrones"]):
            continue
        mot = logs_autorizado(d, p[0], p[1], p[2])
        (malos if mot else vistos).append(f"{p[0]} ({p[1]} {p[2]}:{p[4]})" + (f": {mot}" if mot else ""))
    esperado = (f"{', '.join(d['texto_patrones'])}: propietario {d['propietario']}, modo {d['modo_max_texto']} o más restrictivo")
    if malos:
        return _r(NO_CUMPLE, esperado, "; ".join(malos + vistos), ["logs.permisos"], "Archivos de la exclusión que no cumplen su condición: " + "; ".join(malos))
    return _r(CUMPLE, esperado, "; ".join(vistos) or "ningún archivo de la exclusión presente (la exclusión no cubre nada)", ["logs.permisos"])


def evaluar_exclusion(fila: Dict, ctx: Ctx, evaluacion_cis=None) -> Dict:
    """`fila`: fila de la matriz (tipo_exclusion, reglas_exclusion, parametros_exclusion)."""
    tipo = fila.get("tipo_exclusion")
    reglas: List[str] = fila.get("reglas_exclusion") or []
    params = fila.get("parametros_exclusion") or {}
    base = {"texto": fila.get("exclusion_infraestructura"), "tipo": tipo, "reglas": reglas,
            "evidencia_organizacional_requerida": fila.get("evidencia_organizacional_requerida")}
    if tipo in ("NINGUNA", "NINGUNA_CON_NOTA_INFORMATIVA"):
        return {**base, **_r(NINGUNA, "Sin exclusión", "No aplica evaluación de exclusión")}
    if tipo == "PENDIENTE_REVISION" or not reglas:
        return {**base, **_r(NO_VERIF, "", "Texto de exclusión no clasificado", que_falta="Clasificar la exclusión")}

    parciales = []
    for regla in reglas:
        if regla == "AUDITD_PARTICION_15GB":
            parciales.append(_particion(ctx, params))
        elif regla == "AUDITD_MAX_LOG_FILE":
            parciales.append(_valores_auditd(ctx, {k: v for k, v in params.items() if k in ("max_log_file", "num_logs")}
                                             or {"max_log_file": "200", "num_logs": "50"}))
        elif regla == "AUDITD_ROTATE":
            parciales.append(_valores_auditd(ctx, {"max_log_file_action": params.get("max_log_file_action", "rotate")}))
        elif regla == "AUDITD_DISK_ACTION_SYSLOG":
            parciales.append(_valores_auditd(ctx, {"disk_full_action": params.get("disk_full_action", "syslog"),
                                                   "disk_error_action": params.get("disk_error_action", "syslog")}))
        elif regla == "JOURNALD_ROTACION":
            parciales.append(_journald_vals(ctx, {k: v for k, v in params.items()
                                                  if k in ("SystemMaxUse", "SystemKeepFree", "RuntimeMaxUse", "RuntimeKeepFree", "MaxFileSec")}))
        elif regla == "GDM_NO_APLICA_SI_REMOVIDO":
            inst = _gdm_instalado(ctx)
            if inst is None:
                parciales.append(_r(ERROR, "GDM no instalado", ctx.problema("pkg.rpm") or "sin dato", ["pkg.rpm"]))
            else:
                parciales.append(_r(NO_CUMPLE if inst else CUMPLE, "GDM removido (condición de la exclusión)",
                                    "gdm instalado" if inst else "gdm/gdm3 no instalados", ["pkg.rpm"],
                                    "GDM está instalado: la condición 'N/A por remoción de GDM' no se cumple" if inst else None))
        elif regla == "USUARIOS_CON_LOGIN":
            parciales.append(_usuarios_login(ctx))
        elif regla == "BOOTLOADER_PASSWORD_BOVEDA":
            parciales.append(_r(ORG, "Contraseña de GRUB custodiada en bóveda", "No verificable desde el host",
                                que_falta="Registro de custodia en la bóveda corporativa"))
        elif regla == "PARCHADO_PROCESO":
            parciales.append(_r(ORG, "Proceso formal de parchado", "No verificable desde el host",
                                que_falta="Procedimiento, calendario y último ciclo aplicado al host"))
        elif regla == "SIEM_INTEGRACION":
            d = _destinos_remotos(ctx)
            aprob = ctx.listas.get("destinos_log") or []
            prob = ctx.problema("rsyslog.conf")
            if prob and not d:
                parciales.append(_r(ERROR, "Envío de logs a SIEM/Syslog central", prob, ["rsyslog.conf"]))
            elif not d:
                parciales.append(_r(NO_CUMPLE, "Envío de logs a SIEM/Syslog central", "sin destino remoto configurado en rsyslog",
                                    ["rsyslog.conf"], "Configurar el envío al SIEM/Syslog central"))
            elif aprob:
                ok = any(a in x for x in d for a in aprob)
                parciales.append(_r(CUMPLE if ok else NO_CUMPLE, f"destino en {aprob}", "; ".join(d), ["rsyslog.conf"],
                                    None if ok else "El destino configurado no es el SIEM autorizado"))
            else:
                parciales.append(_r(ORG, "Destino = SIEM/Syslog central autorizado", "; ".join(d), ["rsyslog.conf"],
                                    "Confirmación del Área de Seguridad del destino autorizado y de la recepción de eventos"))
        elif regla == "LOGS_ARCHIVOS_AUTORIZADOS":
            parciales.append(_logs_autorizados(ctx, fila.get("exclusion_infraestructura")))
        elif regla == "AIDE_HORARIO_CONTROLADO":
            cron = [l for l in ctx.lineas("aide.cron") if "aide" in l]
            from .controles import unidades
            u = unidades(ctx, "aide.timer").get("aidecheck.timer", ("", ""))
            t = [f"aidecheck.timer={u[0]}/{u[1]}"]
            timer = u[0] == "enabled" and u[1] == "active"
            incompleto = ctx.sin_sudo("aide.cron") or ctx.problema("aide.cron") or ctx.problema("aide.timer")
            if not cron and not timer and incompleto:
                parciales.append(_r(ERROR, "Ejecución programada en horario controlado",
                                    "[PERMISOS_INSUFICIENTES] sin cron/timer visible; /var/spool/cron no legible sin sudo -n",
                                    ["aide.cron", "aide.timer"], "Verificar la programación de AIDE con privilegios"))
            elif not cron and not timer:
                parciales.append(_r(NO_CUMPLE, "Ejecución programada en horario controlado", "sin programación de AIDE",
                                    ["aide.cron", "aide.timer"], "No existe programación de AIDE"))
            else:
                parciales.append(_r(ORG, "Ejecución en ventana horaria aprobada", "; ".join(cron) or "; ".join(t)[:300],
                                    ["aide.cron", "aide.timer"], "Definición formal de la ventana horaria aprobada"))
        else:
            parciales.append(_r(NO_VERIF, "", f"Regla {regla} sin prueba definida"))

    estados = [p["estado"] for p in parciales]
    if ERROR in estados:
        est = ERROR
    elif all(e == CUMPLE for e in estados):
        est = CUMPLE
    elif all(e == NO_CUMPLE for e in estados):
        est = NO_CUMPLE
    elif NO_CUMPLE in estados or PARCIAL in estados:
        est = PARCIAL if CUMPLE in estados or PARCIAL in estados else NO_CUMPLE
    elif ORG in estados:
        est = ORG
    else:
        est = NO_VERIF
    return {
        **base,
        "estado": est,
        "esperado": " | ".join(p["esperado"] for p in parciales if p["esperado"]),
        "encontrado": " | ".join(p["encontrado"] for p in parciales if p["encontrado"]),
        "evidencia": sorted({e for p in parciales for e in p["evidencia"]}),
        "que_falta": " | ".join(p["que_falta"] for p in parciales if p["que_falta"]) or None,
        "advertencias": [a for p in parciales for a in p.get("advertencias") or []],
        "detalle": parciales,
    }


# Comandos y paquetes que necesita cada regla de exclusión (para construir el recolector).
COMANDOS_EXCLUSION = {
    "AUDITD_PARTICION_15GB": ("sys.var_log_audit", "auditd.conf"),
    "AUDITD_MAX_LOG_FILE": ("auditd.conf",),
    "AUDITD_ROTATE": ("auditd.conf",),
    "AUDITD_DISK_ACTION_SYSLOG": ("auditd.conf",),
    "JOURNALD_ROTACION": ("journald.conf",),
    "GDM_NO_APLICA_SI_REMOVIDO": ("pkg.rpm",),
    "USUARIOS_CON_LOGIN": ("usr.homes", "sys.shells"),
    "SIEM_INTEGRACION": ("rsyslog.conf",),
    "AIDE_HORARIO_CONTROLADO": ("aide.cron", "aide.timer"),
    "LOGS_ARCHIVOS_AUTORIZADOS": ("logs.permisos",),
}
PAQUETES_EXCLUSION = {"GDM_NO_APLICA_SI_REMOVIDO": ("gdm", "gdm3")}
