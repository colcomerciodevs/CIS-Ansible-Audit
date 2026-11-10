"""Validación EFECTIVA de las exclusiones de Infraestructura contra la configuración real del servidor (2026-10-26).

Una exclusión DEFINIDA (columna "Exclusiones de Infraestructura" del Excel P1 o "EXCLUSIONES (Infraestructura Linux)" del
Excel P2) solo declara una intención. Esta capa comprueba, con la evidencia recolectada en el servidor, si la exclusión está
realmente implementada:

    APLICADA        definida y confirmada técnicamente (la configuración real es la que la exclusión declara)
    NO APLICADA     definida, pero la configuración real no corresponde a lo que la exclusión declara
    NO VERIFICADA   no hay evidencia suficiente para confirmarlo (error de recolección, condición organizacional...)

Nunca se marca APLICADA por el solo hecho de estar declarada en el Excel, en variables o en parámetros de Ansible.

- P1 USUARIOS: cada usuario se valida por separado contra la configuración real publicada por el evaluador del control
  (`mecanismo.universo_exclusion`: /etc/shadow, sudoers cargados, grupo de su...). Un usuario está exceptuado si su
  configuración real es la que la política CIS no permite (p. ej. conserva NOPASSWD, PASS_MAX_DAYS 99999); si la política
  CIS sí se le aplica, la exclusión no está implementada para ese usuario.
- P1 CONTROL_NO_APLICADO (rds, tipc): estado OMITIDO (solicitud 2026-10-27); no se usan APLICADA/NO APLICADA. La
  configuración real (resultado técnico) se informa como dato.
- P2: resultado de la prueba técnica de cada regla de la exclusión (motor/exclusiones_eval.py). Una ADVERTENCIA (p. ej. la
  partición /var/log/audit por debajo de los 15 GB recomendados) se informa en naranja y no impide que la exclusión quede APLICADA.
Esta capa no modifica el resultado CIS técnico ni el resultado final.
"""

from typing import Dict, List, Optional

APLICADA, NO_APLICADA, NO_VERIFICADA, OMITIDO = "APLICADA", "NO APLICADA", "NO VERIFICADA", "OMITIDO"
ESTADOS = (APLICADA, NO_APLICADA, NO_VERIFICADA, OMITIDO)

# Estado por elemento (usuario / regla)
EXCEPTUADO = "EXCEPTUADO EN LA CONFIGURACIÓN REAL"
POLITICA_APLICADA = "POLÍTICA CIS APLICADA (NO EXCEPTUADO)"
NO_EXISTE = "NO EXISTE EN EL SERVIDOR"
FUERA_POBLACION = "FUERA DE LA POBLACIÓN DEL CONTROL"
SIN_EVIDENCIA = "SIN EVIDENCIA"

_P2 = {"CUMPLE": APLICADA, "NO CUMPLE": NO_APLICADA, "PARCIAL": NO_APLICADA}


def _res(estado: str, esperado: str, encontrado: str, elementos: List[Dict], evidencia: List[str], observacion: str = "",
         tipo: str = "", advertencias: Optional[List[str]] = None) -> Dict:
    return {"estado": estado, "tipo": tipo, "esperado": esperado, "encontrado": encontrado, "elementos": elementos,
            "evidencia": evidencia, "observacion": observacion.strip(), "advertencias": list(advertencias or [])}


def _evidencia_fila(r: Dict) -> List[str]:
    return [e["comando_id"] for e in r.get("evidencia") or [] if e.get("comando_id")]


def _usuarios(r: Dict, d: Dict) -> Dict:
    usuarios = sorted(d.get("usuarios") or [])
    uni = (r.get("mecanismo") or {}).get("universo_exclusion")
    tec = r.get("resultado_cis_tecnico") or r.get("resultado_cis")
    esperado = "Usuarios exceptuados de la política CIS: " + ", ".join(usuarios)
    ev = _evidencia_fila(r)
    if not uni:
        motivo = ("la evidencia del control no está disponible" if tec == "ERROR DE AUDITORÍA"
                  else "el evaluador del control no publica la configuración real por usuario")
        return _res(NO_VERIFICADA, esperado, f"No verificable: {motivo}.",
                    [{"elemento": u, "estado": SIN_EVIDENCIA, "encontrado": "—", "ok": None} for u in usuarios], ev,
                    "Sin configuración real por usuario no se puede confirmar la exclusión.", "USUARIOS")
    esperado += f". Un usuario está exceptuado si {uni['excluido_si']}."
    existentes = set(uni.get("existentes") or []) if uni.get("existentes") is not None else None
    elems, grupos = [], {EXCEPTUADO: [], POLITICA_APLICADA: [], NO_EXISTE: [], FUERA_POBLACION: [], SIN_EVIDENCIA: []}
    for u in usuarios:
        info = (uni.get("usuarios") or {}).get(u)
        if info is None:
            est = NO_EXISTE if existentes is not None and u not in existentes else SIN_EVIDENCIA
            det = ("la cuenta no existe en /etc/passwd del servidor" if est == NO_EXISTE
                   else "sin dato de la configuración real de este usuario")
            ok = None
        else:
            est = {"INCUMPLE": EXCEPTUADO, "CUMPLE": POLITICA_APLICADA}.get(info["estado"], FUERA_POBLACION)
            det = info.get("detalle") or ""
            ok = True if est == EXCEPTUADO else False if est == POLITICA_APLICADA else None
        grupos[est].append(u)
        elems.append({"elemento": u, "estado": est, "encontrado": det, "ok": ok})
    if grupos[POLITICA_APLICADA]:
        estado = NO_APLICADA
    elif grupos[EXCEPTUADO]:
        estado = APLICADA
    else:
        estado = NO_VERIFICADA
    etiqueta = {EXCEPTUADO: "Exceptuados en la configuración real", POLITICA_APLICADA: "Con la política CIS aplicada (no exceptuados)",
                NO_EXISTE: "No existen en el servidor", FUERA_POBLACION: "Fuera de la población del control", SIN_EVIDENCIA: "Sin evidencia"}
    enc = "; ".join(f"{etiqueta[k]}: {', '.join(v)}" for k, v in grupos.items() if v)
    obs = []
    if grupos[POLITICA_APLICADA]:
        obs.append("La configuración real aplica la política CIS a " + ", ".join(grupos[POLITICA_APLICADA]) + ": la exclusión "
                   "declarada no está implementada para esos usuarios (no es un hallazgo CIS; revisar la definición de la exclusión "
                   "o la configuración del hardening).")
    if grupos[NO_EXISTE]:
        obs.append("Usuarios declarados que no existen en este servidor (la exclusión no tiene efecto sobre ellos): "
                   + ", ".join(grupos[NO_EXISTE]) + ".")
    if grupos[FUERA_POBLACION]:
        obs.append("Usuarios fuera de la población evaluada por el CIS (no requieren exclusión): " + ", ".join(grupos[FUERA_POBLACION]) + ".")
    if estado == NO_VERIFICADA and not grupos[SIN_EVIDENCIA]:
        obs.append("Ningún usuario declarado es exceptuable en este servidor: no hay aplicación que confirmar.")
    obs += [f"Limitación: {x}" for x in uni.get("limitaciones") or []]
    return _res(estado, esperado, enc + f" (fuente: {uni.get('fuente')})", elems, ev, " ".join(obs), "USUARIOS")


def _control_no_aplicado(r: Dict, d: Dict) -> Dict:
    tec = r.get("resultado_cis_tecnico") or r.get("resultado_cis")
    esperado = f"Control omitido por decisión de Infraestructura: regla del hardening desactivada (VALOR {d.get('valor')})."
    det = r.get("hallazgo") or r.get("que_falta") if tec in ("NO CUMPLE", "CUMPLE PARCIALMENTE") else r.get("encontrado")
    return _res(OMITIDO, esperado, f"Configuración real (resultado CIS técnico {tec}): {det or '—'}".strip(), [], _evidencia_fila(r),
                "El control no forma parte de la evaluación de hardening por decisión de Infraestructura; el resultado técnico "
                "se conserva como dato y no se clasifica como aplicada ni no aplicada.", "CONTROL_NO_APLICADO")


def _p2(r: Dict) -> Dict:
    ex = r.get("exclusion") or {}
    parciales = ex.get("detalle") or []
    reglas = ex.get("reglas") or []
    elems = []
    for i, p in enumerate(parciales):
        e = _P2.get(p.get("estado"), NO_VERIFICADA)
        elems.append({"elemento": reglas[i] if i < len(reglas) else f"regla {i + 1}", "estado": p.get("estado"),
                      "esperado": p.get("esperado"), "encontrado": p.get("encontrado"), "observacion": p.get("que_falta"),
                      "advertencia": "; ".join(p.get("advertencias") or []) or None,
                      "ok": True if e == APLICADA else False if e == NO_APLICADA else None})
    estado = _P2.get(ex.get("estado"), NO_VERIFICADA)
    obs = ex.get("que_falta") or ""
    if estado == NO_VERIFICADA:
        obs = (f"Estado de la prueba: {ex.get('estado') or 'sin prueba'}. " + obs).strip()
    return _res(estado, ex.get("esperado") or (ex.get("texto") or "")[:300], ex.get("encontrado") or "—", elems,
                list(ex.get("evidencia") or []), obs, ex.get("tipo") or "CONFIGURACION", ex.get("advertencias"))


def validar(r: Dict) -> Optional[Dict]:
    """Validación efectiva de la exclusión de la fila, o None si la fila no tiene una exclusión definida."""
    if not r.get("exclusion_definida"):
        return None
    if r.get("exclusion_p2_usuarios"):
        return _usuarios(r, r["exclusion_p2_usuarios"])
    if r.get("parte_proyecto") == "PARTE_1":
        d = r.get("exclusion_p1") or {}
        return _control_no_aplicado(r, d) if d.get("tipo") == "CONTROL_NO_APLICADO" else _usuarios(r, d)
    return _p2(r)
