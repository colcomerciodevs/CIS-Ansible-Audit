"""Presentación común de la evidencia por control (HTML y Excel): una "ficha" ordenada y legible.

Orden de la ficha: conclusión -> qué exige el CIS -> dónde se buscó -> qué se encontró (comprobación por comprobación)
-> exclusión de Infraestructura -> qué falta -> trazabilidad. Los textos largos con varios elementos ("a; b; c") se
separan en viñetas. No cambia ningún resultado: solo ordena lo que ya está en resultados.json.
"""

import re
from typing import Dict, List, Tuple

from ..motor.fuentes import de_fila

# Los reportes no muestran referencias a los documentos de la línea base (archivo, hoja, fila, códigos TIPO/ORIGEN/
# TRATAMIENTO): solo la descripción del control y el contenido de la exclusión (ajuste 2026-10-27). resultados.json
# conserva la trazabilidad completa.
_CLAVES_DOCUMENTO = re.compile(r"^[ \t]*(TIPO|ORIGEN|TRATAMIENTO)[ \t]*:.*(\n|$)", re.M)
_CAMPOS_DOCUMENTO = {"fuente", "origen", "source_file", "source_sheet", "source_row", "hoja_excel", "fila_excel"}
LINEA_BASE = {"PARTE_1": "Línea base Parte 1", "PARTE_2": "Línea base Parte 2"}
TIPO_CUMPLIMIENTO = {
    "CUMPLIMIENTO_CIS": "Cumple CIS", "INCUMPLIMIENTO_CIS": "No cumple CIS", "CUMPLIMIENTO_PARCIAL_CIS": "Cumple CIS parcialmente",
    "EXCLUSION_INFRAESTRUCTURA": "Cumple por exclusión de Infraestructura", "NO_APLICA": "No aplica",
    "NO_APLICA_METODO_LOGGING": "No aplica por el método de logging", "SIN_CONCLUSION": "Sin conclusión",
    "PRERREQUISITO_INTERNO": "Prerrequisito interno", "CONTROL_EXCLUIDO_INFRAESTRUCTURA": "Control omitido por Infraestructura",
    "CONTROL_OMITIDO_INFRAESTRUCTURA": "Control omitido por Infraestructura",
    "CONTROL_EXCLUIDO_PROCESO_INDEPENDIENTE": "Control excluido: proceso independiente",
    "REQUIERE_REMEDIACION_MANUAL": "Incumplimiento técnico que requiere remediación manual",
    "REQUIERE_AJUSTE_MANUAL": "Incumplimiento técnico que requiere ajuste manual",
}


def tipo_cumplimiento(r: Dict) -> str:
    t = r.get("tipo_cumplimiento")
    return TIPO_CUMPLIMIENTO.get(t, t or "")


_CONTROL_COMPLETO = "Control completo omitido por Infraestructura (regla del hardening desactivada)"
TIPO_EXCLUSION = {"USUARIOS": "Usuarios exceptuados", "CONTROL_NO_APLICADO": "Control completo (omitido)",
                  "CONFIGURACION": "Configuración de Infraestructura"}


def texto_exclusion(texto) -> str:
    """Texto de la exclusión sin las claves que solo referencian el documento de origen ni códigos internos de tipo."""
    t = _CLAVES_DOCUMENTO.sub("", str(texto or ""))
    return re.sub(r"\bCONTROL_NO_APLICADO\b(:\s*VALOR\s+\w+)?", _CONTROL_COMPLETO, t).strip()


def sin_referencias(obj):
    """Copia de un detalle técnico sin los campos que referencian el documento de origen."""
    if isinstance(obj, dict):
        return {k: sin_referencias(v) for k, v in obj.items() if k not in _CAMPOS_DOCUMENTO}
    if isinstance(obj, list):
        return [sin_referencias(x) for x in obj]
    return obj


# Estado de cada comprobación (componente) -> (símbolo, texto)
ESTADO_COMP = {"OK": ("✔", "Cumple"), "FALLA": ("✘", "No cumple"), "INFO": ("ℹ", "Informativo (no decide)"),
               "ERROR": ("⚠", "Error de auditoría"), "MANUAL": ("◔", "Requiere validación manual"),
               "ORG": ("◔", "Requiere evidencia organizacional"), "NA": ("—", "No aplica"), "FUERA": ("—", "Fuera de alcance")}

# Glosario visible en el reporte (estados de alcance y de mapeo)
_PROPUESTA_NO = ("Omitido por PROPUESTA DE NTTDATA DE IMPLEMENTACIÓN DE CONTROL = NO: el proveedor NTTDATA propuso no "
                 "implementarlo.")
GLOSARIO_ALCANCE = [
    ("FUERA DE ALCANCE", "Fila de la Parte 2 que existe en el CIS suministrado y cuya columna «PROPUESTA DE NTTDATA DE "
                         "IMPLEMENTACIÓN DE CONTROL» es NO: el proveedor NTTDATA propuso no implementarla. Se conserva para "
                         "trazabilidad; no se audita ni afecta los porcentajes."),
    ("RETIRADO EN CIS ACTUAL", "Fila de la Parte 1 o de la Parte 2 cuyo control no existe en el CIS Benchmark suministrado "
                               "(retirado o nunca incluido en esa versión). No hay requisito CIS contra el cual auditarla, "
                               "independientemente de la propuesta del proveedor; el motivo se indica en cada fila."),
    ("NO APLICA (Parte 1)", "Fila de la Parte 1 que no aplica a la plataforma (p. ej. systemd-timesyncd cuando el servicio de "
                            "tiempo es chrony). El motivo se indica en cada fila."),
    ("Parte 1 no confirmada", "Fila de la Parte 1 cuyo sistema no está confirmado por la línea base y no se evalúa. La hoja única de la "
                              "Parte 1 aplica a Oracle Linux 8/9, RHEL 8/9 y SLES 15 (RHEL 9 con las mismas filas que Oracle Linux 9)."),
]
GLOSARIO_MAPEO = [
    ("MAPEADO", "El control del Excel corresponde directamente a un control del CIS suministrado (mismo número y requisito)."),
    ("MAPEADO_TITULO_ACTUALIZADO", "Mismo requisito, pero en el CIS suministrado cambió el número y/o el título respecto al "
                                   "Excel (renumeración; ver «NOVEDADES AJUSTE NUMERACION CIS»)."),
    ("MAPEADO_CONSOLIDADO", "Varias filas del Excel corresponden a un único control del CIS vigente (el CIS unificó requisitos): "
                            "se evalúa una vez y el resultado se informa en cada fila."),
    ("MAPEADO_DIVIDIDO", "Una fila del Excel corresponde a varios controles del CIS vigente: se evalúan todos y la fila "
                         "cumple solo si cumplen todos."),
    ("RETIRADO", "El control del Excel no existe en el CIS suministrado: no hay requisito CIS que evaluar."),
    ("NO_APLICA_PLATAFORMA", "El control no aplica a la plataforma del servidor."),
]
GLOSARIO_RESULTADO = [
    ("CUMPLE", "Requisito CIS verificado satisfactoriamente con evidencia del servidor."),
    ("NO CUMPLE", "Existe un incumplimiento técnico sin una excepción aplicable."),
    ("NO APLICA", "El control no corresponde al escenario evaluado (motivo indicado en cada control)."),
    ("CONTROL EXCLUIDO", "Existe una exclusión formal del control completo (proceso independiente de parchado). Se conservan su "
                         "identificación CIS, su resultado técnico y su justificación; queda fuera del porcentaje final y nunca "
                         "cuenta como CUMPLE."),
    ("OMITIDO", "Exclusión específica de Infraestructura para los controles de los módulos rds y tipc (regla del hardening "
                "desactivada). El resultado técnico se conserva como dato; queda fuera del porcentaje final y nunca cuenta como CUMPLE."),
    ("REQUIERE REMEDIACIÓN MANUAL", "Incumplimiento técnico que requiere intervención técnica especializada (p. ej. servicios SELinux "
                                    "no confinados); no se corrige de forma automática. Cuenta como no cumplido."),
    ("REQUIERE AJUSTE MANUAL", "Ajuste operativo pendiente (p. ej. permisos de archivos ocultos de 7.2.9) que debe validarse con el "
                               "responsable antes de aplicarse. El resultado técnico NO CUMPLE se conserva; cuenta como no cumplido."),
    ("Tratamiento: PENDIENTE INFRAESTRUCTURA", "Advertencia: el incumplimiento depende de un despliegue de Infraestructura (p. ej. "
                                                "servidor RSYSLOG central). El resultado NO CUMPLE se conserva y cuenta como no cumplido."),
    ("Exclusión de Infraestructura: Definida", "Existe una exclusión configurada para el control. Solo indica que está declarada; su "
                                               "aplicación real se informa en la validación."),
    ("APLICADA", "La exclusión está definida y se confirmó en el servidor que la configuración real es la que declara."),
    ("NO APLICADA", "La exclusión está definida, pero la configuración real del servidor no corresponde a lo que declara "
                    "(p. ej. la política CIS sí se aplicó a un usuario exceptuado)."),
    ("NO VERIFICADA", "No hubo evidencia suficiente para confirmar la aplicación de la exclusión."),
    ("⚠ CON ADVERTENCIA", "La exclusión está aplicada, pero un valor recomendado (no obligatorio) no se cumple; p. ej. la partición "
                          "/var/log/audit por debajo de los 15 GB recomendados."),
]
_MAPEO = dict(GLOSARIO_MAPEO)


def explicar_mapeo(estado: str) -> str:
    return _MAPEO.get(estado or "", estado or "—")


def estado_fuera(f: Dict) -> str:
    """Estado visible de una fila no auditada: un control inexistente en el CIS es RETIRADO aunque la propuesta sea NO."""
    return "RETIRADO EN CIS ACTUAL" if f.get("estado_mapeo") == "RETIRADO" else (f.get("resultado_cis") or "")


def motivo_fuera_partes(f: Dict) -> Tuple[str, str]:
    """(mapeo, motivo) de una fila fuera de alcance / retirada / no aplica. El mapeo va primero en el reporte."""
    est, m = f.get("resultado_cis") or "", f.get("estado_mapeo")
    if m == "RETIRADO":
        motivo = f.get("motivo_fuera") or ""
    elif est == "FUERA DE ALCANCE" and str(f.get("propuesta_proveedor") or "").strip().upper() == "NO":
        motivo = _PROPUESTA_NO
    else:
        motivo = f.get("motivo_fuera") or est
    return (f"Mapeo CIS: {m}" if m else ""), " ".join(x for x in ((explicar_mapeo(m) if m else ""), motivo) if x)


def motivo_fuera(f: Dict) -> str:
    """Motivo legible en una línea: 'Mapeo CIS: X — explicación. motivo'."""
    cab, txt = motivo_fuera_partes(f)
    return f"{cab} — {txt}" if cab else txt


# ------------------------------------------------------------------ separación de elementos

def _partir(t: str, sep: str) -> List[str]:
    """Divide por `sep` fuera de paréntesis/corchetes/comillas."""
    res, buf, prof, comilla = [], "", 0, False
    i = 0
    while i < len(t):
        ch = t[i]
        if ch == '"':
            comilla = not comilla
        elif not comilla and ch in "([{":
            prof += 1
        elif not comilla and ch in ")]}":
            prof = max(0, prof - 1)
        if not comilla and prof == 0 and t.startswith(sep, i):
            res.append(buf); buf = ""; i += len(sep); continue
        buf += ch
        i += 1
    res.append(buf)
    return [x.strip() for x in res if x.strip()]


def _forma(x: str) -> str:
    """Forma del inicio de un elemento: ruta, clave=valor, «nombre:», '[ref]' o texto."""
    if x.startswith("/"):
        return "ruta"
    if x.startswith("["):
        return "ref"
    if re.match(r"^[\w.$-]+=", x):
        return "kv"
    if re.match(r"^[\w.@-]+:\s", x):
        return "etiqueta"
    return "texto"


def _unir_fragmentos(it: List[str], sep: str) -> List[str]:
    """Si los elementos tienen una forma común (rutas, clave=valor...), un fragmento de otra forma es la continuación del
    elemento anterior (p. ej. '…; grupo de sistema lp aceptado') y se vuelve a unir."""
    formas = [_forma(x) for x in it]
    comun = max(set(formas), key=formas.count)
    if comun == "texto" or formas.count(comun) < 2:
        return it
    res: List[str] = []
    for x, f in zip(it, formas):
        if res and f != comun:
            res[-1] = f"{res[-1]}{sep.rstrip() if sep.strip() else sep}{' ' if sep.strip() else ''}{x}"
        else:
            res.append(x)
    return res


def elementos(texto) -> Tuple[str, List[str]]:
    """(encabezado, elementos). 'N archivos: a; b; c' -> ('N archivos:', [a, b, c]); '1) a · 2) b' -> ('', [a, b]).
    Un texto sin lista devuelve ('', [texto])."""
    t = str(texto or "").strip()
    if not t:
        return "", []
    for sep in (" · ", "; ", " | "):
        partes = _partir(t, sep)
        if sep == " | " and any(re.match(r"^(grep|awk|sed|sort|uniq|wc|cut|head|tail|xargs|tr)\b", x) for x in partes[1:]):
            continue                      # tubería de un comando, no una lista
        if len(partes) >= 2:
            cab = ""
            m = re.match(r"^(.{3,240}?:)\s+(.+)$", partes[0])
            if m and sep == "; " and " " in m.group(1).strip() and not re.search(r"[=/]$", m.group(1)):
                cab, partes[0] = m.group(1), m.group(2)
            if sep == "; " and (not cab or ": " in partes[0]):
                # 'A: B: /ruta1; /ruta2' -> encabezado 'A: B:' si lo que sigue al último ': ' tiene la forma de los demás
                g = re.match(r"^(.{3,600}:)\s+(.+)$", (cab + " " + partes[0]) if cab else partes[0])
                if g and _forma(g.group(2)) == _forma(partes[1]) != _forma(partes[0]):
                    cab, partes[0] = g.group(1), g.group(2)
            partes = [re.sub(r"^\d+\)\s*", "", p) for p in partes]
            return cab, _unir_fragmentos(partes, sep)
    return "", [t]


# ------------------------------------------------------------------ ficha

def comprobaciones(r: Dict) -> List[Dict]:
    res = []
    for c in r.get("componentes") or []:
        sim, txt = ESTADO_COMP.get(c.get("estado"), ("•", c.get("estado") or ""))
        res.append({"simbolo": sim, "estado": txt, "codigo": c.get("estado"), "que": c.get("descripcion") or c.get("id"),
                    "esperado": c.get("esperado") or "", "encontrado": c.get("encontrado") or ""})
    return res


def exige(r: Dict) -> str:
    """Requisito por comprobación decisoria ('descripción: esperado'); sin componentes, el texto del CIS de la fila."""
    it = [f"{c['descripcion']}: {c['esperado']}" for c in r.get("componentes") or []
          if c.get("estado") not in ("INFO", "FUERA") and c.get("esperado")]
    return " · ".join(dict.fromkeys(it)) if len(it) > 1 else (it[0] if it else (r.get("valor_esperado_cis") or ""))


def archivos_leidos(r: Dict) -> str:
    """Archivos de configuración que se leyeron realmente en el servidor (componentes INFO 'archivos_*')."""
    return " · ".join(c["encontrado"] for c in r.get("componentes") or []
                      if str(c.get("id") or "").startswith("archivos_") and c.get("encontrado"))


def donde(r: Dict) -> List[Tuple[str, str]]:
    d = r.get("donde_se_busco")
    res = [(x["tipo"], x["ubicacion"]) for x in d] if d else de_fila(r)
    leidos = archivos_leidos(r)
    return res + ([("Leídos en este servidor", leidos)] if leidos else [])


def ficha(r: Dict) -> Dict:
    """Secciones de la ficha de un control (sin formato)."""
    tec, fin = r.get("resultado_cis"), r.get("resultado_final") or r.get("resultado_cis")
    exc = None
    if r.get("exclusion_definida"):
        exc = {"texto": texto_exclusion((r.get("exclusion") or {}).get("texto")
                                        if r.get("parte_proyecto") == "PARTE_2" and not r.get("exclusion_p2_usuarios")
                                        else r.get("exclusion_infraestructura")),
               "aplicada": bool(r.get("exclusion_aplicada")), "validacion": r.get("validacion_exclusion"),
               "cubiertos": list(r.get("hallazgos_cubiertos") or []), "no_cubiertos": list(r.get("hallazgos_no_cubiertos") or [])}
    falta = [] if fin in ("CUMPLE", "NO APLICA", "CONTROL EXCLUIDO", "OMITIDO") else elementos(r.get("que_falta"))[1]
    return {"tecnico": tec, "final": fin, "tipo": tipo_cumplimiento(r), "conclusion": r.get("evidencia_motivo") or "",
            "tratamiento": r.get("tratamiento"), "tratamiento_motivo": r.get("tratamiento_motivo"),
            "exige": exige(r), "referencia": r.get("referencia_cis") or "",
            "donde": donde(r), "comprobaciones": comprobaciones(r), "exclusion": exc, "falta": falta,
            "traza": [("Control Excel", f"{r.get('control_excel')} — {r.get('titulo_excel') or ''}".strip(" —")),
                      ("Control CIS", f"{r.get('control_cis') or '—'} — {r.get('titulo_cis') or ''}".strip(" —")),
                      ("Mapeo", f"{r.get('estado_mapeo')}: {explicar_mapeo(r.get('estado_mapeo'))}" if r.get("estado_mapeo") else "—"),
                      ("Fuente CIS", f"{r.get('referencia_cis') or '—'}" + (f" (página {r['pagina_cis']})" if r.get("pagina_cis") else "")),
                      ("Propuesta del proveedor", r.get("propuesta_proveedor") or "—")]}


def texto_lista(texto, viñeta: str = "  • ") -> str:
    cab, it = elementos(texto)
    if len(it) <= 1:
        return (cab + " " if cab else "") + (it[0] if it else "")
    return (cab + "\n" if cab else "") + "\n".join(viñeta + x for x in it)


def ficha_texto(r: Dict) -> str:
    """Ficha en texto plano estructurado (celdas del Excel)."""
    f = ficha(r)
    s = [f"■ RESULTADO: técnico {f['tecnico']} → final {f['final']}" + (f" ({f['tipo']})" if f["tipo"] else "")]
    if f["tratamiento"]:
        s += [f"■ TRATAMIENTO: {f['tratamiento']} — {f['tratamiento_motivo']}"]
    s += ["", "■ CONCLUSIÓN", texto_lista(f["conclusion"])]
    if f["exige"]:
        s += ["", "■ QUÉ EXIGE EL CIS", texto_lista(f["exige"])]
    if f["donde"]:
        s += ["", "■ DÓNDE SE BUSCÓ"] + [f"  • {t}: {texto_lista(u, chr(10) + '      - ').lstrip(chr(10))}" for t, u in f["donde"]]
    if f["comprobaciones"]:
        s += ["", "■ QUÉ SE ENCONTRÓ"]
        for c in f["comprobaciones"]:
            s.append(f"  {c['simbolo']} {c['que']} — {c['estado']}")
            if c["esperado"]:
                s.append(f"      Esperado: {c['esperado']}")
            cab, it = elementos(c["encontrado"])
            if len(it) <= 1:
                s.append(f"      Encontrado: {(cab + ' ') if cab else ''}{it[0] if it else '—'}")
            else:
                s.append(f"      Encontrado: {cab}")
                s += [f"        - {x}" for x in it]
    if f["exclusion"]:
        e = f["exclusion"]
        v = e.get("validacion") or {}
        s += ["", "■ EXCLUSIÓN DE INFRAESTRUCTURA: Definida", f"  Validación en el servidor: {v.get('estado') or 'NO VERIFICADA'}",
              f"  Esperado: {v.get('esperado') or '—'}", f"  Encontrado: {v.get('encontrado') or '—'}"]
        s += [f"    {'✓' if x.get('ok') else '✗' if x.get('ok') is False else '·'} {x['elemento']}: {x['estado']} — {x.get('encontrado') or ''}"
              + (f" ⚠ {x['advertencia']}" if x.get("advertencia") else "") for x in v.get("elementos") or []]
        s += [f"  ⚠ {a}" for a in v.get("advertencias") or []]
        if v.get("observacion"):
            s.append(f"  Observación: {v['observacion']}")
        if e["cubiertos"]:
            s += ["  Hallazgos cubiertos:"] + [f"    - {x}" for x in e["cubiertos"]]
        if e["no_cubiertos"]:
            s += ["  Hallazgos NO cubiertos:"] + [f"    - {x}" for x in e["no_cubiertos"]]
    if f["falta"]:
        s += ["", "■ QUÉ FALTA"] + [f"  • {x}" for x in f["falta"]]
    return "\n".join(s)[:32000]


def donde_texto(r: Dict) -> str:
    return "\n".join(f"• {t}: {texto_lista(u, chr(10) + '    - ').lstrip(chr(10))}" for t, u in donde(r))
