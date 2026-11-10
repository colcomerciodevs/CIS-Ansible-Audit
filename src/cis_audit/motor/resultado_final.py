"""Capa de resultado final: excepciones y exclusiones de Infraestructura aplicadas DESPUÉS de la evaluación CIS técnica.

La evaluación técnica (resultado_cis = resultado_cis_tecnico) no se modifica ni se oculta. Esta capa añade:

    resultado_final            resultado tras aplicar exclusiones/excepciones (el que computa en el % de cumplimiento)
    tipo_cumplimiento          CUMPLIMIENTO_CIS | EXCLUSION_INFRAESTRUCTURA | INSTRUMENTACION_AUDITORIA |
                               CONTROL_EXCLUIDO_PROCESO_INDEPENDIENTE | CONTROL_EXCLUIDO_INFRAESTRUCTURA |
                               INCUMPLIMIENTO_CIS | CUMPLIMIENTO_PARCIAL_CIS |
                               NO_APLICA | SIN_CONCLUSION | CONFLICTO_BASELINE | PRERREQUISITO_INTERNO
    evidencia_motivo           evidencia actual / motivo del resultado final
    exclusion_infraestructura  definición de la exclusión/excepción aplicable (texto de la fuente)
    detalle_exclusion          qué cubrió exactamente (componentes y elementos) y con qué fuente
    hallazgos_cubiertos        hallazgos CIS cubiertos por una exclusión o por la instrumentación de auditoría
    hallazgos_no_cubiertos     hallazgos CIS que ninguna exclusión cubre
    validacion_exclusion       validación EFECTIVA de la exclusión definida contra la configuración real del servidor:
                               APLICADA | NO APLICADA | NO VERIFICADA (motor/validacion_exclusiones.py)
    tratamiento                clasificación operativa de la remediación (p. ej. REQUIERE REMEDIACIÓN MANUAL); no cambia
                               el resultado técnico ni el final

Reglas:
- Una exclusión cubre SOLO lo que define su fuente: P1 -> columna "Exclusiones de Infraestructura" del Excel P1
  (fuente canónica; cis_audit.excepciones_p1), por FILA P1; P2 -> exclusivamente la columna
  EXCLUSIONES (Infraestructura Linux) de la línea base Parte 2, y solo las reglas que definen un valor alternativo
  para un parámetro que exige el CIS (SUSTITUTIVAS), cuando la propia exclusión se verificó CUMPLE, o las que autorizan
  elementos concretos (POR_ELEMENTO_P2: archivos de log por patrón y condición verificada archivo por archivo).
- Un hallazgo sin identificación por elemento no puede cubrirse por una excepción de usuarios (fail-closed).
- Si queda cualquier hallazgo no cubierto: resultado_final = NO CUMPLE.
- Las excepciones P1 no se aplican a filas P2 y viceversa.
- srvinnops: su NOPASSWD en los sudoers exactos del recolector es INSTRUMENTACION_AUDITORIA (D-3B-01). Desde el
  2026-10-27 Infraestructura además lo declara administrador exceptuado de 5.2.4 (config/exclusiones_infraestructura.yml).
- CONTROL EXCLUIDO: controles de parchado (proceso independiente). OMITIDO: filas P1 con exclusión CONTROL_NO_APLICADO
  (rds y tipc, regla desactivada en el hardening). Ambos visibles y trazables, fuera del porcentaje final; nunca cuentan
  como CUMPLE. El resultado CIS técnico se conserva tal cual.
- Estados finales operativos (ajuste 2026-10-27), siempre con el resultado técnico NO CUMPLE conservado y dentro del
  denominador final como no cumplidos: REQUIERE REMEDIACIÓN MANUAL (Ensure no unconfined services exist) y REQUIERE
  AJUSTE MANUAL (7.2.9: archivos ocultos cuyo único hallazgo es un modo más permisivo que el exigido).
"""

import re
from typing import Dict, List, Optional, Tuple

from .base import (CUMPLE, ERROR, ERROR_R, FALLA, MANUAL_R, NO_APLICA, NO_CUMPLE, OK, ORG_R, PARCIAL,
                   Componente, agregar)

CONTROL_EXCLUIDO = "CONTROL EXCLUIDO"
OMITIDO = "OMITIDO"  # solo la exclusión P1 CONTROL_NO_APLICADO (rds, tipc)
TRATAMIENTO_MANUAL = "REQUIERE REMEDIACIÓN MANUAL"
AJUSTE_MANUAL = "REQUIERE AJUSTE MANUAL"
# Estados finales que quedan FUERA del denominador del cumplimiento final (nunca cuentan como CUMPLE).
FUERA_DENOMINADOR_FINAL = (CONTROL_EXCLUIDO, OMITIDO)
# Estados finales operativos de incumplimiento: dentro del denominador, nunca CUMPLE.
MANUALES_FINAL = (TRATAMIENTO_MANUAL, AJUSTE_MANUAL)
# Clasificación operativa de tratamiento por título CIS (decisión 2026-10-26): {título: motivo}.
TRATAMIENTO_POR_TITULO = {
    "Ensure no unconfined services exist": (
        "Los procesos bajo unconfined_service_t (p. ej. agentes de seguridad como Trend Micro) requieren análisis especializado "
        "y, en su caso, desarrollo o ajuste de políticas SELinux. No debe corregirse de forma automática sin validar el impacto "
        "operativo: la remediación requiere evaluación técnica y coordinación con el proveedor responsable. Esta clasificación no "
        "significa que el control cumpla."),
}
# Advertencia de dependencia de Infraestructura por título CIS (decisión 2026-11-10): {título: (tratamiento, motivo)}. Solo
# acompaña al incumplimiento: el resultado técnico y el final se conservan (NO CUMPLE sigue en el denominador).
PENDIENTE_INFRA = "PENDIENTE INFRAESTRUCTURA"
ADVERTENCIA_POR_TITULO = {
    "Ensure rsyslog is configured to send logs to a remote log host": (
        "Pendiente el despliegue del servidor RSYSLOG central por parte de Infraestructura. Mientras no exista el servidor de "
        "destino no es posible configurar el envío remoto de logs; una vez desplegado, se debe configurar y auditar nuevamente. "
        "Esta advertencia no significa que el control cumpla."),
}
TITULO_PARCHADO = "Ensure updates, patches, and additional security software are installed"
MOTIVO_PARCHADO = ("Control gestionado mediante proceso independiente de parchado de sistemas operativos. "
                   "Se excluye de la evaluación de hardening.")
MOTIVO_PARCHADO_REPOS = "Control administrado mediante proceso formal independiente de parchado de sistemas operativos."
# CONTROL EXCLUIDO por proceso independiente: {título CIS: (motivo, fuente de la decisión)}. El resultado CIS técnico
# se conserva tal cual (p. ej. NO CUMPLE o REQUIERE EVIDENCIA ORGANIZACIONAL); solo el resultado final es CONTROL EXCLUIDO.
PROCESO_PARCHADO = {
    TITULO_PARCHADO: (MOTIVO_PARCHADO, "Decisión de Infraestructura: parchado gestionado por proceso independiente"),
    "Ensure package manager repositories are configured": (
        MOTIVO_PARCHADO_REPOS, "Decisión de Infraestructura: repositorios administrados por el proceso formal de parchado de S.O."),
    "Ensure GPG keys are configured": (
        MOTIVO_PARCHADO_REPOS, "Decisión de Infraestructura: llaves GPG administradas por el proceso formal de parchado de S.O."),
}

# P2: reglas de exclusión que sustituyen el valor exigido por el CIS para componentes concretos del evaluador.
# Solo estas pueden convertir una FALLA técnica en cumplimiento por exclusión; el resto son condiciones adicionales
# de Infraestructura (partición, rotación recomendada, SIEM, AIDE, bóveda…) o notas, y nunca cubren hallazgos CIS.
SUSTITUTIVAS = {
    "AUDITD_ROTATE": ("max_log_file_action",),                          # CIS: keep_logs; línea base: rotate
    "AUDITD_DISK_ACTION_SYSLOG": ("disk_full_action", "disk_error_action"),  # CIS: halt|single; línea base: syslog
}
# P2: reglas que cubren ELEMENTO a ELEMENTO (archivo identificado + condición verificada), nunca un componente completo.
POR_ELEMENTO_P2 = ("LOGS_ARCHIVOS_AUTORIZADOS",)

_TIPO_POR_ESTADO = {CUMPLE: "CUMPLIMIENTO_CIS", NO_CUMPLE: "INCUMPLIMIENTO_CIS", PARCIAL: "CUMPLIMIENTO_PARCIAL_CIS",
                    NO_APLICA: "NO_APLICA", ERROR_R: "SIN_CONCLUSION", MANUAL_R: "SIN_CONCLUSION", ORG_R: "SIN_CONCLUSION"}


def _cis_de(comp: Dict, r: Dict) -> Optional[str]:
    """Control CIS al que pertenece un componente (filas P1 con varios controles prefijan el id: '<cis>:<id>')."""
    ids = r.get("controles_cis") or ([r["control_cis"]] if r.get("control_cis") else [])
    if len(ids) > 1 and ":" in comp["id"]:
        return comp["id"].split(":", 1)[0]
    return ids[0] if ids else None


def _id_local(comp: Dict, r: Dict) -> str:
    ids = r.get("controles_cis") or []
    return comp["id"].split(":", 1)[1] if len(ids) > 1 and ":" in comp["id"] else comp["id"]


def _instrumentaciones(r: Dict) -> Dict[str, str]:
    """{archivo sudoers: descripción} de la instrumentación EXACTA (D-3B-01) presente en la fila."""
    ins = r.get("instrumentacion_auditoria") or (r.get("mecanismo") or {}).get("instrumentacion_auditoria") or {}
    res = {}
    if ins.get("instrumentacion_auditoria_detectada") and ins.get("archivo"):
        res[ins["archivo"]] = "recolector privilegiado (cis-audit-collect)"
    fs = ins.get("instrumentacion_fs") or {}
    if fs.get("instrumentacion_auditoria_detectada") and fs.get("archivo"):
        res[fs["archivo"]] = "recolector de filesystem profundo (cis-audit-collect-fs)"
    return res


def _texto_elem(e: Dict) -> str:
    if e.get("modo") is not None:          # archivo (p. ej. 6.2.4.1): ruta, modo y propietario:grupo
        t = f"{e['archivo']} ({e['modo']} {e.get('propietario')}:{e.get('grupo')})"
        return t + (f" — {e['motivo_no_cubierto']}" if e.get("motivo_no_cubierto") else "")
    u = e.get("usuario")
    loc = f"{e['archivo']}:{e['linea']}" if e.get("archivo") else None
    val = e.get("valor")
    base_ = f"{u}={val}" if val is not None else (u or "")
    return f"{base_} ({loc})" if loc else base_


def _cobertura(r: Dict, excepciones_p1: Dict, excepciones_p2: Optional[Dict] = None) -> Tuple[List[Dict], List[str], Dict]:
    """(componentes recalculados, hallazgos no cubiertos, detalle de lo cubierto)."""
    p1 = r.get("parte_proyecto") == "PARTE_1"
    instr = _instrumentaciones(r)
    excl = r.get("exclusion") or {}
    cubiertos_p2 = set()
    reglas_aplicadas = []
    if not p1:
        detalle = excl.get("detalle") or []
        for i, regla in enumerate(excl.get("reglas") or []):
            parcial = detalle[i] if i < len(detalle) else None
            if regla in SUSTITUTIVAS and parcial and parcial.get("estado") == CUMPLE:
                cubiertos_p2.update(SUSTITUTIVAS[regla])
                reglas_aplicadas.append({"regla": regla, "componentes": list(SUSTITUTIVAS[regla]),
                                         "esperado_exclusion": parcial.get("esperado"), "encontrado": parcial.get("encontrado")})
    # P2 ARCHIVOS_LOG_AUTORIZADOS: cubre archivo por archivo (patrón + condición verificada en la evidencia del host).
    logs_def = None
    if not p1 and "LOGS_ARCHIVOS_AUTORIZADOS" in (excl.get("reglas") or []):
        from ..exclusiones import archivos_log_autorizados
        logs_def = archivos_log_autorizados(excl.get("texto"))
    comps, no_cub = [], []
    det = {"elementos_por_excepcion": [], "elementos_por_instrumentacion": [], "componentes_por_exclusion": [],
           "elementos_por_exclusion_p2": [], "reglas_p2_aplicadas": reglas_aplicadas, "excepciones_p1": []}
    for c in r.get("componentes") or []:
        c2 = dict(c)
        comps.append(c2)
        if c["estado"] != FALLA:
            continue
        cis = _cis_de(c, r)
        local = _id_local(c, r)
        if not p1 and local in cubiertos_p2:
            c2["estado"] = OK
            det["componentes_por_exclusion"].append(f"{c['id']}: {c.get('encontrado')}")
            continue
        exc = (excepciones_p1.get(r.get("control_excel")) if p1 else
               (excepciones_p2 or {}).get(r.get("control_cis")) if r.get("exclusion_p2_usuarios") else None)
        if exc and exc.get("tipo") != "USUARIOS":
            exc = None  # CONTROL_NO_APLICADO no cubre hallazgos: la fila completa es CONTROL EXCLUIDO (ver aplicar)
        elems = c.get("elementos") or []
        if not elems:
            no_cub.append(f"[{cis}] {c['descripcion']}: {c.get('encontrado')}" if cis else f"{c['descripcion']}: {c.get('encontrado')}")
            continue
        quedan = []
        for e in elems:
            if logs_def and e.get("modo") is not None:
                from .exclusiones_eval import logs_autorizado
                mot = logs_autorizado(logs_def, e["archivo"], e["modo"], e.get("propietario") or "")
                if mot is None:
                    det["elementos_por_exclusion_p2"].append({**e, "regla": "LOGS_ARCHIVOS_AUTORIZADOS", "origen": "LINEA_BASE_P2"})
                else:
                    quedan.append({**e, "motivo_no_cubierto": mot})
                continue
            if e.get("archivo") in instr and e.get("usuario"):
                det["elementos_por_instrumentacion"].append({**e, "tipo": "INSTRUMENTACION_AUDITORIA", "instrumentacion": instr[e["archivo"]]})
            elif exc and e.get("usuario") in exc["usuarios"]:
                det["elementos_por_excepcion"].append({**e, "control_cis": cis, "origen": exc.get("origen")})
            else:
                quedan.append(e)
        if exc and r["control_excel"] not in det["excepciones_p1"]:
            det["excepciones_p1"].append(r["control_excel"])
        if quedan:
            no_cub += [f"[{cis}] {c['descripcion']}: {_texto_elem(e)}" for e in quedan]
        else:
            c2["estado"] = OK
    return comps, no_cub, det


_MOTIVO_LEGIBLE = {
    "EVIDENCIA_ORGANIZACIONAL": "Requiere evidencia organizacional",
    "POLITICA_O_REVISION": "Requiere una política del sitio que no está definida",
    "REQUIERE_EXTENSION_RECOLECTOR": "La evidencia necesaria no la obtiene el recolector actual",
    "ANALISIS_ESTATICO_NO_CONCLUYENTE": "No determinable de forma segura sin ejecutar el perfil de login",
    "PENDIENTE_AUDITORIA_PROFUNDA": "Requiere el recorrido de filesystem profundo",
    "PERMISOS_INSUFICIENTES": "La cuenta de auditoría no tuvo privilegios para leer la evidencia",
    "CONFLICTO_BASELINE": "La línea base exige requisitos incompatibles entre sí",
    "EVIDENCIA_HOST_INSUFICIENTE": "Política aprobada disponible, pero la evidencia del host no permite la comparación exacta",
}


def _limpio(t: Optional[str]) -> str:
    """Quita los códigos internos entre corchetes para la vista del reporte (el JSON los conserva en motivo_resultado)."""
    return re.sub(r"\s*\[[A-Z_]{4,}\]\s*", " ", t or "").strip()


def aplicar(r: Dict, excepciones_p1: Optional[Dict] = None, excepciones_p2: Optional[Dict] = None) -> Dict:
    """Añade los campos de la capa final a una fila de resultado (in situ) y la devuelve.

    observacion, evidencia_motivo y exclusion_infraestructura son conceptos distintos: ninguno puebla al otro.
    Después del resultado final se añaden la validación efectiva de la exclusión y la clasificación de tratamiento."""
    from .validacion_exclusiones import validar
    _aplicar(r, excepciones_p1, excepciones_p2)
    r["validacion_exclusion"] = validar(r)
    tec = r.get("resultado_cis_tecnico")
    r["tratamiento"], r["tratamiento_motivo"] = None, None
    if r.get("tipo_control", "CIS") != "CIS" or r.get("resultado_final") not in (NO_CUMPLE, PARCIAL):
        return r
    trat = TRATAMIENTO_POR_TITULO.get(r.get("titulo_cis") or "")
    if trat and tec in (NO_CUMPLE, PARCIAL):
        _final_manual(r, TRATAMIENTO_MANUAL, "REQUIERE_REMEDIACION_MANUAL", trat,
                      f"REQUIERE REMEDIACIÓN MANUAL: {trat} Hallazgo técnico CIS ({tec}): {_limpio(r.get('hallazgo') or r.get('que_falta'))}")
        return r
    adv = ADVERTENCIA_POR_TITULO.get(r.get("titulo_cis") or "")
    if adv:
        r["tratamiento"], r["tratamiento_motivo"] = PENDIENTE_INFRA, adv
        return r
    ajustes = _ajustes_dotfiles(r)
    if ajustes:
        _final_manual(r, AJUSTE_MANUAL, "REQUIERE_AJUSTE_MANUAL",
                      "Archivos ocultos con permisos más amplios que los exigidos por el CIS: requieren validación con el responsable "
                      "de cada usuario y, de corresponder, el ajuste manual del modo. La auditoría no modifica permisos.",
                      " ".join(ajustes))
    return r


def _final_manual(r: Dict, estado: str, tipo: str, motivo: str, evidencia: str) -> None:
    """Estado final operativo (REQUIERE REMEDIACIÓN/AJUSTE MANUAL): el resultado técnico se conserva; sigue en el
    denominador final como no cumplido."""
    r["resultado_final"], r["tipo_cumplimiento"] = estado, tipo
    r["evidencia_motivo"] = evidencia.strip()[:1500]
    r["tratamiento"], r["tratamiento_motivo"] = estado, motivo
    r["computa_porcentaje_final"] = not r.get("conflicto_baseline")


def _ajustes_dotfiles(r: Dict) -> List[str]:
    """CIS 7.2.9: acción por archivo si el ÚNICO hallazgo son archivos ocultos con modo más permisivo (propietario y grupo
    correctos). .netrc, .forward, .rhost(s), propietario o grupo incorrectos siguen siendo NO CUMPLE."""
    if (r.get("control_id_motor") or "") != "dotfiles" or r.get("resultado_cis_tecnico") != NO_CUMPLE:
        return []
    fallas = [c for c in r.get("componentes") or [] if c.get("estado") == FALLA]
    elems = (r.get("mecanismo") or {}).get("dotfiles_permisos") or []
    if not elems or [c.get("subcondicion") or c.get("id") for c in fallas] != ["permisos"] or not all(e.get("solo_modo") for e in elems):
        return []
    return [f"REQUIERE AJUSTE MANUAL: el archivo {e['archivo']} (usuario {e['usuario']}, propietario {e['propietario']}) tiene "
            f"permisos {e['modo']} y el control CIS exige {e['modo_esperado']} o más restrictivos. Validar con el responsable del usuario "
            f"si se requieren permisos de ejecución y, de no ser necesarios, ajustarlos a {e['modo_esperado']}." for e in elems]


def _aplicar(r: Dict, excepciones_p1: Optional[Dict] = None, excepciones_p2: Optional[Dict] = None) -> Dict:
    excepciones_p1, excepciones_p2 = excepciones_p1 or {}, excepciones_p2 or {}
    from .fuentes import de_fila
    r["donde_se_busco"] = [{"tipo": t, "ubicacion": u} for t, u in de_fila(r)]
    tec = r["resultado_cis"]
    p1 = r.get("parte_proyecto") == "PARTE_1"
    r["resultado_cis_tecnico"] = tec
    r.update({"exclusion_infraestructura": None, "exclusion_definida": False, "exclusion_aplicada": False,
              "detalle_exclusion": None, "detalle_exclusion_texto": None, "instrumentacion_texto": None,
              "hallazgos_cubiertos": [], "hallazgos_no_cubiertos": []})
    r.setdefault("observacion", None)
    excl = r.get("exclusion") or {}
    # P2: solo la columna EXCLUSIONES (Infraestructura Linux) con contenido real define una exclusión.
    # P1: solo la columna "Exclusiones de Infraestructura" del Excel P1 (su columna Observacion no es una exclusión).
    if p1:
        d = excepciones_p1.get(r.get("control_excel"))
        if d:
            r["exclusion_definida"] = True
            r["exclusion_p1"] = {k: (sorted(v) if isinstance(v, set) else v) for k, v in d.items()}
            r["exclusion_infraestructura"] = (", ".join(sorted(d["usuarios"])) if d["tipo"] == "USUARIOS"
                                              else f"Control excluido: regla desactivada en el hardening (VALOR {d.get('valor')})")
            r["exclusion_fuente"], r["exclusion_origen"] = d.get("fuente"), d.get("origen")
    elif clasificar_texto_p2(excl.get("texto")) == "CONTENIDO":
        r["exclusion_definida"] = True
        r["exclusion_infraestructura"] = excl.get("texto")
    elif excepciones_p2.get(r.get("control_cis")):
        # P2 sin exclusión en el Excel: decisión de Infraestructura posterior (config/exclusiones_infraestructura.yml,
        # exclusiones_p2), solo usuarios y por control CIS. Se valida y se cubre usuario por usuario como en la Parte 1.
        d = excepciones_p2[r["control_cis"]]
        r["exclusion_definida"] = True
        r["exclusion_p2_usuarios"] = {"tipo": "USUARIOS", "usuarios": sorted(d["usuarios"]), "motivo": d.get("motivo"),
                                      "origen": d.get("origen"), "fuente": d.get("fuente")}
        r["exclusion_infraestructura"] = ", ".join(sorted(d["usuarios"]))

    def fin(final, tipo, motivo):
        r["resultado_final"], r["tipo_cumplimiento"], r["evidencia_motivo"] = final, tipo, (motivo or "").strip()[:1500]
        # computa_porcentaje (CIS técnico) no se toca; el resultado final tiene su propio indicador.
        r["computa_porcentaje_final"] = final in (CUMPLE, NO_CUMPLE, PARCIAL) + MANUALES_FINAL and not r.get("conflicto_baseline") and \
            r.get("tipo_control", "CIS") == "CIS"
        return r

    if r.get("tipo_control") == "PRERREQUISITO":
        return fin(tec, "PRERREQUISITO_INTERNO", r.get("evidencia_motivo") or r.get("que_falta") or "")
    if r.get("conflicto_baseline"):
        return fin(tec, "CONFLICTO_BASELINE", "La línea base marca YES en requisitos incompatibles (journald ForwardToSyslog=no frente "
                                               "a envío a rsyslog). Pendiente de que el responsable de la línea base defina el método oficial.")
    if r.get("titulo_cis") in PROCESO_PARCHADO and r.get("tipo_control", "CIS") == "CIS":
        motivo, fuente = PROCESO_PARCHADO[r["titulo_cis"]]
        r["detalle_exclusion"] = {"fuente": fuente, "resultado_cis_tecnico": tec,
                                  "hallazgo_tecnico_informativo": r.get("hallazgo") or r.get("que_falta")}
        r["fuente_politica"] = r.get("fuente_politica") or fuente
        return fin(CONTROL_EXCLUIDO, "CONTROL_EXCLUIDO_PROCESO_INDEPENDIENTE", motivo)
    if p1 and (r.get("exclusion_p1") or {}).get("tipo") == "CONTROL_NO_APLICADO":
        d = r["exclusion_p1"]
        r["detalle_exclusion"] = {"fuente": d.get("fuente"), "resultado_cis_tecnico": tec,
                                  "control_no_aplicado": {"valor": d.get("valor"), "motivo": d.get("motivo")},
                                  "hallazgo_tecnico_informativo": r.get("hallazgo") or r.get("que_falta")}
        r["detalle_exclusion_texto"] = f"Control omitido por Infraestructura (VALOR {d.get('valor')})"
        return fin(OMITIDO, "CONTROL_OMITIDO_INFRAESTRUCTURA",
                   f"Control omitido por decisión de Infraestructura (regla del hardening desactivada): "
                   f"{d.get('motivo')} Resultado CIS técnico conservado: {tec}.")
    if tec not in (NO_CUMPLE, PARCIAL):
        _condiciones_adicionales(r)
        tipo = (r.get("mecanismo") or {}).get("tipo_cumplimiento") if tec == NO_APLICA else None
        return fin(tec, tipo or _TIPO_POR_ESTADO.get(tec, "SIN_CONCLUSION"), _motivo_sin_cobertura(r, tec))
    comps, no_cub, det = _cobertura(r, excepciones_p1, excepciones_p2)
    cubrio = (det["elementos_por_excepcion"] or det["elementos_por_instrumentacion"] or det["componentes_por_exclusion"]
              or det["elementos_por_exclusion_p2"])
    r["hallazgos_no_cubiertos"] = no_cub
    r["hallazgos_cubiertos"] = ([_texto_elem(e) + f" ({e.get('control_cis')})" * bool(e.get("control_cis"))
                                 for e in det["elementos_por_excepcion"]]
                                + [f"{_texto_elem(e)} (instrumentación de auditoría)" for e in det["elementos_por_instrumentacion"]]
                                + list(det["componentes_por_exclusion"])
                                + [f"{_texto_elem(e)} (exclusión de Infraestructura P2)" for e in det["elementos_por_exclusion_p2"]])
    if cubrio:
        r["detalle_exclusion"] = {**det, "estado_evaluacion_exclusion_p2": excl.get("estado") if not p1 else None}
        r["exclusion_aplicada"] = bool(det["elementos_por_excepcion"] or det["componentes_por_exclusion"] or det["elementos_por_exclusion_p2"])
        r["detalle_exclusion_texto"] = _detalle_texto(det) or None
        r["instrumentacion_texto"] = ", ".join(sorted({e["usuario"] for e in det["elementos_por_instrumentacion"]})) or None
    _condiciones_adicionales(r)
    if no_cub and not cubrio and not r["exclusion_definida"]:
        # Control sin exclusión: el motivo es el propio hallazgo CIS (no hay nada que cubrir).
        return fin(tec, _TIPO_POR_ESTADO.get(tec, "SIN_CONCLUSION"), _motivo_sin_cobertura(r, tec))
    if no_cub:
        mot = "Hallazgos no cubiertos por ninguna exclusión: " + "; ".join(_limpio(x) for x in no_cub) + "."
        if cubrio:
            mot += f" Elementos autorizados (no son hallazgo): {_detalle_texto(det, True)}."
        return fin(NO_CUMPLE if cubrio else tec, "INCUMPLIMIENTO_CIS" if (cubrio or tec == NO_CUMPLE) else "CUMPLIMIENTO_PARCIAL_CIS", mot)
    # Todos los hallazgos cubiertos: el resultado final depende del resto de componentes (ERROR/MANUAL/ORG siguen).
    objs = [Componente(**{k: v for k, v in c.items() if k in Componente.__dataclass_fields__}) for c in comps]
    est, _ = agregar(objs, True)
    solo_instr = not det["elementos_por_excepcion"] and not det["componentes_por_exclusion"] and not det["elementos_por_exclusion_p2"]
    tipo = ("INSTRUMENTACION_AUDITORIA" if solo_instr else "EXCLUSION_INFRAESTRUCTURA") if est == CUMPLE else _TIPO_POR_ESTADO.get(est, "SIN_CONCLUSION")
    return fin(est, tipo, _motivo_cubierto(r, det) if est == CUMPLE else _motivo_sin_cobertura(r, est))


def _motivo_sin_cobertura(r: Dict, est: str) -> str:
    cb = (r.get("mecanismo") or {}).get("comparacion_banner")
    if cb:
        return _limpio(cb.get("mensaje"))
    mv = (r.get("mecanismo") or {}).get("motivo_visible")
    if mv and est in (CUMPLE, NO_CUMPLE, PARCIAL, NO_APLICA):
        return _limpio(mv)
    if est in (CUMPLE, NO_APLICA):
        return _limpio(r.get("encontrado")) or "Requisito verificado en la evidencia."
    if est in (NO_CUMPLE, PARCIAL):
        return _limpio(r.get("hallazgo")) or _limpio(r.get("que_falta"))
    base_ = _MOTIVO_LEGIBLE.get(r.get("motivo_resultado") or "", "Sin conclusión automática")
    return f"{base_}: {_limpio(r.get('que_falta') or r.get('hallazgo'))}".rstrip(": ")


def _motivo_cubierto(r: Dict, det: Dict) -> str:
    p = []
    if det["elementos_por_excepcion"] or det["elementos_por_instrumentacion"]:
        p.append("Los hallazgos CIS encontrados corresponden únicamente a elementos autorizados: " + _detalle_texto(det, True) + ".")
    if det["elementos_por_exclusion_p2"]:
        p.append("Los archivos que incumplen el script CIS son únicamente los autorizados por la exclusión de Infraestructura "
                 "(línea base Parte 2) y cumplen su condición: " + "; ".join(_texto_elem(e) for e in det["elementos_por_exclusion_p2"]) + ".")
    if det["componentes_por_exclusion"]:
        p.append("El valor encontrado es el definido por Infraestructura en la línea base ("
                 + "; ".join(str(x.get("esperado_exclusion")) for x in det["reglas_p2_aplicadas"]) + ").")
    return "No quedan hallazgos sin cubrir. " + " ".join(p)


def _detalle_texto(det: Dict, con_instr: bool = False) -> str:
    p = []
    if det["elementos_por_excepcion"]:
        p.append("excepciones de Infraestructura: " + ", ".join(dict.fromkeys(e["usuario"] for e in det["elementos_por_excepcion"])))
    if det["componentes_por_exclusion"]:
        p.append("; ".join(f"{x.get('esperado_exclusion')} ({x['regla']})" for x in det["reglas_p2_aplicadas"]))
    if det["elementos_por_exclusion_p2"]:
        p.append("archivos autorizados (ARCHIVOS_LOG_AUTORIZADOS): " + ", ".join(e["archivo"] for e in det["elementos_por_exclusion_p2"]))
    if con_instr and det["elementos_por_instrumentacion"]:
        p.append("instrumentación controlada de auditoría: " + ", ".join(
            f"{e['usuario']} ({e['archivo']})" for e in det["elementos_por_instrumentacion"]))
    return "; ".join(p)


def _condiciones_adicionales(r: Dict) -> None:
    """Exclusiones P2 no sustitutivas: se informan (estado de la exclusión) sin alterar el resultado final."""
    excl = r.get("exclusion") or {}
    if r.get("parte_proyecto") == "PARTE_1" or clasificar_texto_p2(excl.get("texto")) != "CONTENIDO":
        return
    no_sust = [g for g in (excl.get("reglas") or []) if g not in SUSTITUTIVAS and g not in POR_ELEMENTO_P2]
    if not no_sust:
        return
    d = r.get("detalle_exclusion") or {}
    d["condiciones_adicionales_infraestructura"] = {
        "reglas": no_sust, "estado": excl.get("estado"), "que_falta": excl.get("que_falta"),
        "nota": "Condición adicional o nota de Infraestructura: se informa por separado y no modifica el resultado final."}
    r["detalle_exclusion"] = d


# ------------------------------------------------------------------ extracción de fuentes

ENCABEZADO_EXCLUSION_P2 = "EXCLUSIONES (Infraestructura Linux)"


def clasificar_texto_p2(txt: Optional[str]) -> str:
    """Contenido de la celda 'EXCLUSIONES (Infraestructura Linux)': VACIA | NINGUNA ('Ninguna', con o sin nota) | CONTENIDO.
    Solo una fila con CONTENIDO define una exclusión; y solo cambia el resultado si cubre exactamente el hallazgo."""
    t = (txt or "").strip()
    if not t:
        return "VACIA"
    if re.match(r"(?i)^ningun[ao]\b", t):  # 'Ninguna' / 'Ninguno', con o sin nota (mismo criterio que exclusiones._es_ninguna)
        return "NINGUNA"
    return "CONTENIDO"


def extraer_exclusiones_p2(excel_p2, matriz: List[Dict]) -> List[Dict]:
    """Exclusiones P2 leídas EXCLUSIVAMENTE de la columna 'EXCLUSIONES (Infraestructura Linux)' del Excel Parte 2 y
    contrastadas con la matriz (que las clasificó al prepararla). Una diferencia de texto se informa (no se corrige)."""
    from ..excel_baseline import leer_linea_base
    from ..normalizacion import compactar
    lb = leer_linea_base(excel_p2)
    for h in lb["hojas"]:
        # Encabezado EXACTO (normalizando espacios): la única columna del Excel P2 que puede originar una exclusión.
        enc = compactar(h["columnas"]["exclusion_infraestructura"]["encabezado_original"])
        if enc != ENCABEZADO_EXCLUSION_P2:
            raise ValueError(f"{h['hoja']}: columna de exclusión '{enc}' distinta de '{ENCABEZADO_EXCLUSION_P2}'")
    idx = {(f["hoja_excel"], f["fila_excel"]): f for f in matriz}
    res = []
    for h in lb["hojas"]:
        for f in h["filas"]:
            txt = (f.get("exclusion_infraestructura") or "").strip()
            m = idx.get((h["hoja"], f["fila_excel"]))
            if not txt or not m:
                continue
            res.append({"hoja_excel": h["hoja"], "fila_excel": f["fila_excel"], "benchmarks": [x["benchmark"] for x in matriz
                        if x["hoja_excel"] == h["hoja"] and x["fila_excel"] == f["fila_excel"]],
                        "control_excel": m["control_excel"], "control_cis": m["control_cis"], "estado_alcance": m["estado_alcance"],
                        "texto_excel": txt, "coincide_con_matriz": compactar(txt) == compactar(m.get("exclusion_infraestructura") or ""),
                        "tipo_exclusion": m["tipo_exclusion"], "reglas": m["reglas_exclusion"], "parametros": m["parametros_exclusion"],
                        "contenido": clasificar_texto_p2(txt),
                        "sustitutiva": [g for g in m["reglas_exclusion"] if g in SUSTITUTIVAS],
                        "declara_cis_parcial": bool(re.search(r"cumplimiento del control cis\W*parcial", txt, re.I))})
    return res
