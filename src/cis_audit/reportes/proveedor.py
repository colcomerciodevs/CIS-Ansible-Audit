"""Informe consolidado de remediación para el proveedor (HTML + PDF), con varios servidores en un solo documento.

Recopila, por control de la línea base, los resultados finales que requieren acción (NO CUMPLE, CUMPLE PARCIALMENTE,
REQUIERE REMEDIACIÓN MANUAL, REQUIERE AJUSTE MANUAL) y, aparte, los que aún no tienen conclusión (validación manual o
evidencia organizacional): qué exige el control, qué se encontró en cada servidor, qué falta y la remediación de
referencia del Benchmark CIS oficial. Solo lee resultados.json y el catálogo CIS: no cambia ningún resultado.

Como el resto de los reportes, no muestra referencias a los documentos de la línea base (archivo, hoja, fila, códigos
internos) ni rutas internas del proyecto; los textos aprobados (banners) se adjuntan en un anexo.
"""

import datetime as dt
import html
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..motor import partes
from ..motor.base import MOTIVOS
from ..motor.resultado_final import PENDIENTE_INFRA
from . import presentacion as pres
from .html import CLASE

E = lambda v: html.escape("" if v is None else str(v))  # noqa: E731

ACCION = ("NO CUMPLE", "CUMPLE PARCIALMENTE", "REQUIERE REMEDIACIÓN MANUAL", "REQUIERE AJUSTE MANUAL")
SIN_CONCLUSION = ("REQUIERE VALIDACIÓN MANUAL", "REQUIERE EVIDENCIA ORGANIZACIONAL", "ERROR DE AUDITORÍA")
_GRAVEDAD = {"ALTO": 0, "MEDIO": 1, "BAJO": 2}
_CORTO = {"CUMPLE": "Cumple", "NO CUMPLE": "No cumple", "CUMPLE PARCIALMENTE": "Parcial", "NO APLICA": "No aplica",
          "REQUIERE REMEDIACIÓN MANUAL": "Remediación manual", "REQUIERE AJUSTE MANUAL": "Ajuste manual",
          "REQUIERE VALIDACIÓN MANUAL": "Validación manual", "REQUIERE EVIDENCIA ORGANIZACIONAL": "Evidencia org.",
          "CONTROL EXCLUIDO": "Excluido", "OMITIDO": "Omitido", "ERROR DE AUDITORÍA": "Error"}
_RE_ARCHIVO_APROBADO = re.compile(r"input/baseline/files/[^\s/]+/([^\s/():,]+)")
_RE_CODIGO = re.compile(r"\s*\[([A-Z_]+)\]")
_COMP_ACCION = ("FALLA", "ERROR", "MANUAL", "ORG")


def limpio(texto) -> str:
    """Texto apto para el proveedor: sin rutas internas del proyecto ni códigos internos de motivo."""
    t = pres.texto_exclusion(texto)
    t = _RE_ARCHIVO_APROBADO.sub(lambda m: f"texto aprobado «{m.group(1)}» (ver anexo)", t)
    t = t.replace("SIMILITUD_TEXTUAL", "similitud textual")
    return _RE_CODIGO.sub(lambda m: "" if m.group(1) in MOTIVOS else m.group(0), t).strip()


def _archivos_aprobados(texto) -> List[str]:
    return _RE_ARCHIVO_APROBADO.findall(str(texto or ""))


def _final(r: Dict) -> str:
    return r.get("resultado_final") or r.get("resultado_cis") or ""


def _badge(estado: str, corto: bool = False) -> str:
    return f'<span class="badge {"adv" if estado in ("CON ADVERTENCIA", PENDIENTE_INFRA) else CLASE.get(estado, "na")}">{E(_CORTO.get(estado, estado) if corto else estado)}</span>'


def _lista(texto) -> str:
    cab, it = pres.elementos(limpio(texto))
    if len(it) <= 1:
        return E((cab + " " if cab else "") + (it[0] if it else "")) or "—"
    return (f'<span class="cab">{E(cab)}</span>' if cab else "") + "<ul>" + "".join(f"<li>{E(x)}</li>" for x in it) + "</ul>"


def cargar_catalogos(dir_catalogo: Path) -> Dict[str, Dict]:
    """benchmark -> {titulo, version, recomendaciones: {id: recomendación}} del catálogo CIS extraído de los PDF oficiales."""
    res = {}
    for f in sorted(Path(dir_catalogo).glob("*.json")):
        c = json.loads(f.read_text(encoding="utf-8"))
        res[c.get("clave") or f.stem] = {"titulo": c.get("titulo"), "version": c.get("version"),
                                         "recomendaciones": {x["id"]: x for x in c.get("recomendaciones") or []}}
    return res


def _controles_cis(r: Dict) -> List[str]:
    """Controles CIS de la fila que no cumplen (con varios controles por fila, solo los que fallan)."""
    por = r.get("resultados_por_control_cis") or {}
    malos = [k for k, v in por.items() if (v or {}).get("resultado") not in ("CUMPLE", "NO APLICA")]
    todos = r.get("controles_cis") or [x.strip() for x in str(r.get("control_cis") or "").split(",") if x.strip()]
    return sorted(malos or todos, key=partes.clave_control)


def excepcion_pendiente(r: Dict) -> bool:
    """Exclusión de Infraestructura definida que el servidor no cumple: validación NO APLICADA / NO VERIFICADA, o con advertencia."""
    v = r.get("validacion_exclusion") or {}
    return bool(r.get("exclusion_definida")) and v.get("estado") != "OMITIDO" and (
        v.get("estado") != "APLICADA" or bool(v.get("advertencias")))


_ORDEN = lambda g: (g["parte"], partes.clave_control(g["control"]))  # noqa: E731


def _grupos(datos_hosts: List[Dict]) -> List[Dict]:
    grupos: Dict[Tuple, Dict] = {}
    for d in datos_hosts:
        for r in d["resultados"]:
            if r.get("tipo_control", partes.CIS) != partes.CIS:
                continue
            k = (r.get("parte_proyecto") or "", r.get("control_excel") or r.get("control_cis") or "")
            g = grupos.setdefault(k, {"parte": k[0], "control": k[1], "titulo": r.get("titulo_excel") or r.get("titulo_cis"),
                                      "por_host": {}})
            g["por_host"].setdefault(r["host"], []).append(r)
    return list(grupos.values())


def agrupar(datos_hosts: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Grupos por control de la línea base (parte + control), con la fila de cada servidor: (requieren acción, sin conclusión)."""
    accion, pendientes = [], []
    for g in _grupos(datos_hosts):
        estados = [_final(r) for rs in g["por_host"].values() for r in rs]
        filas = [r for rs in g["por_host"].values() for r in rs]
        g["criticidad"] = min((str(r.get("criticidad") or "").upper() for r in filas if _final(r) in ACCION + SIN_CONCLUSION),
                              key=lambda c: _GRAVEDAD.get(c, 9), default="")
        if any(e in ACCION for e in estados):
            accion.append(g)
        elif any(e in SIN_CONCLUSION for e in estados):
            pendientes.append(g)
    return sorted(accion, key=_ORDEN), sorted(pendientes, key=_ORDEN)


def agrupar_excepciones(datos_hosts: List[Dict]) -> List[Dict]:
    """Grupos con alguna exclusión de Infraestructura que el servidor no cumple (independiente del resultado CIS)."""
    res = []
    for g in _grupos(datos_hosts):
        filas = [r for rs in g["por_host"].values() for r in rs if excepcion_pendiente(r)]
        if filas:
            g["criticidad"] = min((str(r.get("criticidad") or "").upper() for r in filas), key=lambda c: _GRAVEDAD.get(c, 9), default="")
            res.append(g)
    return sorted(res, key=_ORDEN)


def _titulo(g: Dict) -> str:
    """Descripción del control sin el número que la antecede."""
    return re.sub(r"^[0-9.]+\s+", "", g["titulo"] or "")


def _celda(rs: Optional[List[Dict]]) -> str:
    if not rs:
        return '<span class="muted">—</span>'
    estados = list(dict.fromkeys(_final(r) for r in rs))
    adv = any(r.get("tratamiento") == PENDIENTE_INFRA for r in rs)
    return "<br>".join(_badge(e, corto=True) for e in estados) + ('<br><span class="badge adv">⚠ Pendiente Infra</span>' if adv else "")


def _remediacion(g: Dict, hosts: List[Dict], catalogos: Dict[str, Dict]) -> str:
    """Remediación de referencia del Benchmark CIS por control CIS y benchmark (sin repetir textos idénticos)."""
    vistos, h = {}, []
    for hd in hosts:
        for r in g["por_host"].get(hd["host"]) or []:
            if _final(r) not in ACCION + SIN_CONCLUSION:
                continue
            cat = catalogos.get(r.get("benchmark") or hd.get("benchmark")) or {}
            for cid in _controles_cis(r):
                rec = (cat.get("recomendaciones") or {}).get(cid)
                if not rec:
                    continue
                texto = (rec.get("remediation_referencia_no_ejecutar") or "").strip()
                clave = (cid, rec.get("titulo"), texto)
                ref = f"{cat.get('titulo')} {cat.get('version')} · {cid}" + (f" (pág. {rec['pagina']})" if rec.get("pagina") else "")
                if clave in vistos:
                    vistos[clave]["refs"].append(ref)
                    continue
                vistos[clave] = {"refs": [ref], "titulo": rec.get("titulo_completo") or rec.get("titulo"), "texto": texto,
                                 "desc": (rec.get("description") or "").strip()}
    for v in vistos.values():
        h.append(f'<div class="rem"><div class="lbl">{E(v["titulo"])}</div><small class="muted">{E(" · ".join(dict.fromkeys(v["refs"])))}</small>'
                 + (f'<p class="desc">{E(v["desc"])}</p>' if v["desc"] else "")
                 + (f'<pre>{E(v["texto"])}</pre>' if v["texto"] else '<p class="muted">El Benchmark no incluye texto de remediación.</p>')
                 + "</div>")
    return "".join(h)


def _detalle_host(r: Dict, hd: Dict) -> str:
    fin = _final(r)
    comps = [c for c in r.get("componentes") or [] if c.get("estado") in _COMP_ACCION]
    info = [c for c in r.get("componentes") or [] if c.get("estado") == "INFO" and not str(c.get("id") or "").startswith("archivos_")]
    cis = ", ".join(_controles_cis(r))
    h = [f'<div class="srv"><div class="srvcab"><b>{E(hd["host"])}</b> <small class="muted">{E(hd["so"])} · CIS {E(cis)}'
         f' ({E(r.get("titulo_cis") or "")})</small> {_badge(fin)}</div>']
    pendiente_infra = r.get("tratamiento") == PENDIENTE_INFRA
    if r.get("tratamiento_motivo"):
        h.append(f'<div class="nota"><b>{"⚠ " if pendiente_infra else ""}{E(r.get("tratamiento") or fin)}:</b> '
                 f'{E(limpio(r["tratamiento_motivo"]))}</div>')
    # La conclusión resume el hallazgo; si es larga, repite lo que ya muestran las comprobaciones.
    if r.get("evidencia_motivo") and (not r.get("tratamiento_motivo") or pendiente_infra) and (not comps or len(str(r["evidencia_motivo"])) <= 400):
        h.append(f'<div class="concl">{_lista(r["evidencia_motivo"])}</div>')
    if comps:
        h.append('<table class="comp"><thead><tr><th>Comprobación</th><th>Esperado</th><th>Encontrado en el servidor</th></tr></thead><tbody>'
                 + "".join(f'<tr><td><b>{E(limpio(c.get("descripcion") or c.get("id")))}</b><br>'
                           f'<small class="muted">{E(pres.ESTADO_COMP.get(c.get("estado"), ("", c.get("estado")))[1])}</small></td>'
                           f'<td>{_lista(c.get("esperado"))}</td><td>{_lista(c.get("encontrado"))}</td></tr>' for c in comps)
                 + "</tbody></table>")
    if info and fin in ("REQUIERE REMEDIACIÓN MANUAL", "REQUIERE AJUSTE MANUAL"):
        h.append('<div class="lbl">Elementos identificados</div><ul>'
                 + "".join(f'<li><b>{E(limpio(c.get("descripcion")))}</b>: {E(limpio(c.get("encontrado")))}</li>' for c in info) + "</ul>")
    falta = pres.elementos(limpio(r.get("que_falta")))[1] if fin in ACCION else []
    if falta:
        h.append('<div class="lbl">Qué falta para cumplir</div><ul>' + "".join(f"<li>{E(x)}</li>" for x in falta) + "</ul>")
    h.append(_bloque_exclusion(r))
    return "".join(h) + "</div>"


def _estado_exclusion(r: Dict) -> str:
    v = r.get("validacion_exclusion") or {}
    return "CON ADVERTENCIA" if v.get("estado") == "APLICADA" and v.get("advertencias") else (v.get("estado") or "NO VERIFICADA")


def _bloque_exclusion(r: Dict) -> str:
    """Exclusión de Infraestructura de la fila: qué establece, si el servidor la cumple y, elemento por elemento, qué se encontró."""
    if not r.get("exclusion_definida") or (r.get("validacion_exclusion") or {}).get("estado") == "OMITIDO":
        return ""
    e = pres.ficha(r)["exclusion"] or {}
    v = r.get("validacion_exclusion") or {}
    est = _estado_exclusion(r)
    clase = {"APLICADA": "excl", "CON ADVERTENCIA": "excl advb"}.get(est, "excl kob")
    h = [f'<div class="{clase}"><div class="srvcab"><b>Exclusión de Infraestructura</b> {_badge(est)}</div>']
    if e.get("texto"):
        h.append(f'<div class="lbl">Qué establece Infraestructura</div><pre class="exctxt">{E(limpio(e["texto"]))}</pre>')
    h.append(f'<div><span class="lbl">Esperado:</span> {E(limpio(v.get("esperado")) or "—")}</div>'
             f'<div><span class="lbl">Encontrado en el servidor:</span> {E(limpio(v.get("encontrado")) or "—")}</div>')
    els = v.get("elementos") or []
    if els:
        h.append('<ul class="velem">' + "".join(
            f'<li><b class="{"si" if x.get("ok") else "no" if x.get("ok") is False else "nd"}">'
            f'{"✓" if x.get("ok") else "✗" if x.get("ok") is False else "·"}</b> '
            # Solo los usuarios se nombran: en las exclusiones de configuración el elemento es un código interno.
            + (f'<b>{E(x.get("elemento"))}</b>: ' if v.get("tipo") == "USUARIOS" else "") + f'{E(x.get("estado"))}'
            + (f' — {E(limpio(x.get("encontrado")))}' if x.get("encontrado") and x.get("encontrado") != v.get("encontrado") else "")
            + (f' <span class="adv">⚠ {E(x["advertencia"])}</span>' if x.get("advertencia") else "") + "</li>" for x in els) + "</ul>")
    if v.get("observacion"):
        h.append(f'<div><span class="lbl">Observación:</span> {E(limpio(v["observacion"]))}</div>')
    if est == "APLICADA" and r.get("hallazgos_cubiertos"):
        h.append('<div>Los elementos exceptuados no requieren corrección: ' + E("; ".join(limpio(x) for x in r["hallazgos_cubiertos"])) + ".</div>")
    if r.get("hallazgos_no_cubiertos"):
        h.append('<div><span class="lbl">Hallazgos NO cubiertos por la exclusión:</span><ul>'
                 + "".join(f"<li>{E(limpio(x))}</li>" for x in r["hallazgos_no_cubiertos"]) + "</ul></div>")
    return "".join(h) + "</div>"


def _que_hacer_exclusion(r: Dict) -> str:
    v = r.get("validacion_exclusion") or {}
    if _estado_exclusion(r) == "CON ADVERTENCIA":
        return "La exclusión se cumple, pero con una advertencia que debe atenderse."
    if v.get("tipo") == "USUARIOS":
        return ("La excepción de usuarios declarada no está implementada: los usuarios tienen la política CIS aplicada. No es un "
                "hallazgo CIS; confirmar con Infraestructura si la excepción debe implementarse o retirarse de la línea base.")
    if v.get("estado") == "NO VERIFICADA":
        return "La auditoría no pudo verificar la exclusión en el servidor; requiere revisión manual."
    return "Aplicar en el servidor la configuración que establece Infraestructura (valor esperado)."


def _ficha_excepcion(g: Dict, hosts: List[Dict], n: int, abierto: bool) -> str:
    parte = partes.INSIGNIA.get(g["parte"], g["parte"])
    afectados = [hd for hd in hosts if any(excepcion_pendiente(r) for r in g["por_host"].get(hd["host"]) or [])]
    estados = "".join(f'<span class="est"><small>{E(hd["corto"])}</small> '
                      + "".join(_badge(_estado_exclusion(r)) for r in g["por_host"][hd["host"]] if excepcion_pendiente(r)) + "</span>"
                      for hd in afectados)
    h = [f'<details class="ctl" id="c{n}"{" open" if abierto else ""}><summary><h3><span class="tag t{parte.lower()}">{E(parte)}</span> '
         f'{E(g["control"])} — {E(_titulo(g))}</h3><div class="estados">{estados}</div></summary>'
         f'<div class="meta">Criticidad: <b>{E(g["criticidad"] or "—")}</b> · Servidores afectados: <b>{len(afectados)}</b> de {len(hosts)}</div>']
    for hd in afectados:
        for r in g["por_host"][hd["host"]]:
            if excepcion_pendiente(r):
                h.append(f'<div class="srv"><div class="srvcab"><b>{E(hd["host"])}</b> <small class="muted">{E(hd["so"])} · CIS '
                         f'{E(r.get("control_cis") or "")}</small> <span class="muted">Resultado CIS técnico:</span> {_badge(r.get("resultado_cis") or "")}'
                         f' <span class="muted">final:</span> {_badge(_final(r))}</div>'
                         f'<div class="nota"><b>Qué hacer:</b> {E(_que_hacer_exclusion(r))}</div>{_bloque_exclusion(r)}</div>')
    return "".join(h) + '<a class="volver" href="#excepciones">↑ Volver a la matriz de exclusiones</a></details>'


def _matriz_excepciones(grupos: List[Dict], hosts: List[Dict], ini: int) -> str:
    cab = "".join(f"<th>{E(hd['corto'])}</th>" for hd in hosts)
    filas = ""
    for i, g in enumerate(grupos, ini):
        parte = partes.INSIGNIA.get(g["parte"], g["parte"])
        celdas = ""
        for hd in hosts:
            rs = [r for r in g["por_host"].get(hd["host"]) or [] if r.get("exclusion_definida")]
            celdas += "<td>" + ("<br>".join(_badge(_estado_exclusion(r)) for r in rs) or '<span class="muted">—</span>') + "</td>"
        filas += (f'<tr><td><span class="tag t{parte.lower()}">{E(parte)}</span></td><td><a href="#c{i}">{E(g["control"])}</a></td>'
                  f'<td>{E(_titulo(g))}</td><td>{E(g["criticidad"] or "—")}</td>{celdas}</tr>')
    return (f'<table class="matriz"><thead><tr><th>Parte</th><th>Control</th><th>Descripción</th><th>Criticidad</th>{cab}</tr></thead>'
            f"<tbody>{filas}</tbody></table>")


def _exige(g: Dict, hosts: List[Dict]) -> str:
    """Qué exige el control: requisito por comprobación de cada servidor, sin repetir los idénticos."""
    vistos = {}
    for hd in hosts:
        for r in g["por_host"].get(hd["host"]) or []:
            if _final(r) in ACCION + SIN_CONCLUSION:
                vistos.setdefault(limpio(pres.exige(r)), []).append(hd["corto"])
    if len(vistos) == 1:
        return _lista(next(iter(vistos)))
    return "".join(f'<div class="exige"><small class="muted">{E(", ".join(dict.fromkeys(hs)))}</small>{_lista(t)}</div>'
                   for t, hs in vistos.items())


def _ficha(g: Dict, hosts: List[Dict], catalogos: Dict[str, Dict], n: int, abierto: bool) -> str:
    parte = partes.INSIGNIA.get(g["parte"], g["parte"])
    propuestas = sorted({str(r.get("propuesta_proveedor")) for rs in g["por_host"].values() for r in rs
                         if str(r.get("propuesta_proveedor") or "").upper() not in ("", "YES", "NO", "NONE")
                         and _final(r) in ACCION + SIN_CONCLUSION})
    afectados = [hd for hd in hosts if any(_final(r) in ACCION + SIN_CONCLUSION for r in g["por_host"].get(hd["host"]) or [])]
    # Cada control es plegable (cerrado por defecto en el HTML): el resumen muestra el estado por servidor afectado.
    estados = "".join(f'<span class="est"><small>{E(hd["corto"])}</small> {_celda([r for r in g["por_host"][hd["host"]] if _final(r) in ACCION + SIN_CONCLUSION])}</span>'
                      for hd in afectados)
    h = [f'<details class="ctl" id="c{n}"{" open" if abierto else ""}><summary><h3><span class="tag t{parte.lower()}">{E(parte)}</span> '
         f'{E(g["control"])} — {E(_titulo(g))}</h3><div class="estados">{estados}</div></summary>'
         f'<div class="meta">Criticidad: <b>{E(g["criticidad"] or "—")}</b> · Servidores afectados: <b>{len(afectados)}</b> de {len(hosts)}'
         + (f' · Propuesta del proveedor: <code>{E(", ".join(propuestas))}</code>' if propuestas else "") + "</div>",
         f'<h4>Qué se espera del control</h4>{_exige(g, hosts)}',
         "<h4>Qué se encontró en cada servidor</h4>"]
    for hd in hosts:
        for r in g["por_host"].get(hd["host"]) or []:
            if _final(r) in ACCION + SIN_CONCLUSION:
                h.append(_detalle_host(r, hd))
    otros = [f'{hd["corto"]} ({", ".join(dict.fromkeys(_final(r) for r in g["por_host"][hd["host"]]))})' for hd in hosts
             if g["por_host"].get(hd["host"]) and all(_final(r) not in ACCION + SIN_CONCLUSION for r in g["por_host"][hd["host"]])]
    if otros:
        h.append(f'<p class="muted">Sin acción pendiente en: {E("; ".join(otros))}.</p>')
    rem = _remediacion(g, hosts, catalogos)
    if rem:
        h.append(f'<details{" open" if abierto else ""}><summary>Remediación de referencia del Benchmark CIS</summary>'
                 '<p class="muted">Texto oficial del Benchmark, como referencia. Debe adaptarse a la línea base aprobada y '
                 'validarse en un ambiente de pruebas antes de aplicarse.</p>' + rem + "</details>")
    return "".join(h) + '<a class="volver" href="#matriz">↑ Volver a la matriz</a></details>'


def _matriz(grupos: List[Dict], hosts: List[Dict], ini: int) -> str:
    cab = "".join(f"<th>{E(hd['corto'])}</th>" for hd in hosts)
    filas = ""
    for i, g in enumerate(grupos, ini):
        parte = partes.INSIGNIA.get(g["parte"], g["parte"])
        filas += (f'<tr><td><span class="tag t{parte.lower()}">{E(parte)}</span></td><td><a href="#c{i}">{E(g["control"])}</a></td>'
                  f'<td>{E(_titulo(g))}</td><td>{E(g["criticidad"] or "—")}</td>'
                  + "".join(f"<td>{_celda(g['por_host'].get(hd['host']))}</td>" for hd in hosts) + "</tr>")
    return (f'<table class="matriz"><thead><tr><th>Parte</th><th>Control</th><th>Descripción</th><th>Criticidad</th>{cab}</tr></thead>'
            f"<tbody>{filas}</tbody></table>")


def _resumen(datos_hosts: List[Dict], hosts: List[Dict]) -> str:
    filas = ""
    for d, hd in zip(datos_hosts, hosts):
        fg = ((d.get("resumen") or {}).get("final") or {}).get("global") or {}
        c = fg.get("conteo") or {}
        n = lambda *es: sum(1 for r in d["resultados"] if r.get("tipo_control", partes.CIS) == partes.CIS and _final(r) in es)  # noqa: E731
        pct = fg.get("cumplimiento_final_linea_base")
        filas += (f'<tr><td><b>{E(hd["host"])}</b></td><td>{E(hd["so"])}</td><td>{E(hd["benchmark"])}</td>'
                  f'<td>{E(hd["fecha"])}</td><td><b>{E(f"{pct:.1f} %".replace(".", ",") if pct is not None else "—")}</b>'
                  f'<br><small class="muted">{E(c.get("CUMPLE", 0))}/{E(fg.get("evaluables_final", "—"))}</small></td>'
                  f'<td>{n("NO CUMPLE")}</td><td>{n("CUMPLE PARCIALMENTE")}</td>'
                  f'<td>{n("REQUIERE REMEDIACIÓN MANUAL", "REQUIERE AJUSTE MANUAL")}</td><td>{n(*SIN_CONCLUSION)}</td>'
                  f'<td>{sum(1 for r in d["resultados"] if excepcion_pendiente(r))}</td></tr>')
    return ('<table><thead><tr><th>Servidor</th><th>Sistema operativo</th><th>Benchmark CIS</th><th>Fecha de la evidencia</th>'
            '<th>Cumplimiento final</th><th>No cumple</th><th>Parcial</th><th>Acción manual</th><th>Sin conclusión</th><th>Exclusiones no cumplidas</th></tr></thead>'
            f"<tbody>{filas}</tbody></table>")


def _anexo(grupos: List[Dict], dir_aprobados: Optional[Path]) -> str:
    nombres = sorted({a for g in grupos for rs in g["por_host"].values() for r in rs if _final(r) in ACCION + SIN_CONCLUSION
                      for a in _archivos_aprobados(json.dumps([r.get("valor_esperado_cis"), r.get("que_falta"),
                                                               [c.get("esperado") for c in r.get("componentes") or []]],
                                                              ensure_ascii=False))})
    if not nombres or not dir_aprobados:
        return ""
    h = []
    for nombre in nombres:
        ruta = next((p for p in Path(dir_aprobados).glob(f"*/{nombre}") if p.is_file()), None)
        if ruta:
            h.append(f'<h3>«{E(nombre)}»</h3><pre class="aprobado">{E(ruta.read_text(encoding="utf-8", errors="replace"))}</pre>')
    if not h:
        return ""
    return ('<section id="anexo"><h2>Anexo — Textos aprobados por la organización</h2><p>Contenido exacto que deben tener los '
            "archivos de banner. La auditoría lo compara por similitud textual (mínimo 95 %).</p>" + "".join(h) + "</section>")


CSS = """
:root{--bg:#f6f7f9;--fg:#1d232b;--muted:#5b6672;--card:#fff;--line:#d9dee4;--ok:#1e7b34;--okb:#e3f4e7;--ko:#b3261e;--kob:#fbe6e4;
--par:#8a5a00;--parb:#fff2cc;--na:#555;--nab:#ececec;--err:#5b2a86;--errb:#efe5f8;--acc:#1f3864;--p1:#0b5c7a;--p1b:#e0f1f7;--p2:#5b2a86;--p2b:#efe5f8;--adv:#b45309;--advb:#ffedd5}
@media (prefers-color-scheme:dark){:root{--bg:#14181d;--fg:#e6e9ed;--muted:#9aa5b1;--card:#1c2229;--line:#303842;--okb:#16351f;--ok:#7fd99a;
--kob:#3d1a18;--ko:#ff9c94;--parb:#3a2f12;--par:#ffd479;--nab:#2a2f35;--na:#c7ccd1;--errb:#2e2140;--err:#d1b3f2;--acc:#9db8ff;
--p1:#8fd3ee;--p1b:#12313d;--p2:#d1b3f2;--p2b:#2e2140;--adv:#fdba74;--advb:#3b2410}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg)}
header{background:#1f3864;color:#fff;padding:24px 32px}header h1{margin:0 0 4px;font-size:22px}header p{margin:0;opacity:.9}
main{max-width:1280px;margin:0 auto;padding:16px}section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px 20px;margin:16px 0}
h2{font-size:18px;margin:0 0 12px}h3{font-size:15px;margin:0 0 6px}h4{margin:14px 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--acc)}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{background:var(--bg)}ul{margin:4px 0 4px 18px;padding:0}li{margin:1px 0}code{font-family:ui-monospace,monospace;font-size:12px}
.badge{display:inline-block;padding:1px 8px;border-radius:12px;font-weight:600;font-size:12px;white-space:nowrap;border:1px solid currentColor}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-weight:700;font-size:11px;font-family:ui-monospace,monospace;border:1px solid currentColor}
.tp1{color:var(--p1);background:var(--p1b)}.tp2{color:var(--p2);background:var(--p2b)}
.ok{color:var(--ok);background:var(--okb)}.ko{color:var(--ko);background:var(--kob)}.parcial,.pend{color:var(--par);background:var(--parb)}
.na,.omit{color:var(--na);background:var(--nab)}.err{color:var(--err);background:var(--errb)}
.muted{color:var(--muted)}.lbl{font-weight:600;margin-top:6px}.cab{font-weight:600}
details.ctl{border:1px solid var(--line);border-left:4px solid var(--acc);border-radius:6px;padding:8px 12px;margin:8px 0}details.ctl>summary{list-style:none;font-weight:normal}details.ctl>summary::-webkit-details-marker{display:none}details.ctl>summary h3{display:inline;margin:0}details.ctl>summary h3::before{content:'▸ ';color:var(--acc)}details.ctl[open]>summary h3::before{content:'▾ '}details.ctl[open]>summary{border-bottom:1px solid var(--line);padding-bottom:6px;margin-bottom:6px}.estados{display:flex;flex-wrap:wrap;gap:4px 12px;margin:4px 0 0 18px}.est small{color:var(--muted);font-weight:600}.botones{display:flex;gap:8px;margin:0 0 8px}.botones button{padding:4px 10px;border:1px solid var(--acc);border-radius:4px;background:var(--card);color:var(--acc);cursor:pointer;font-weight:600}.meta{color:var(--muted);font-size:13px}
.srv{border:1px solid var(--line);border-radius:6px;padding:8px 12px;margin:8px 0}.srvcab{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:4px}
.nota{border-left:4px solid var(--par);padding:6px 10px;background:var(--parb);margin:4px 0}
.excl{border-left:4px solid var(--ok);padding:6px 10px;background:var(--okb);margin:6px 0}.excl.kob{border-left-color:var(--ko);background:var(--kob)}
.excl.advb{border-left-color:var(--adv);background:var(--advb)}.adv{color:var(--adv);font-weight:600}.badge.adv{background:var(--advb)}
pre.exctxt{background:var(--card);margin:2px 0 6px}.velem{list-style:none;margin:4px 0;padding:0}.velem .si{color:var(--ok)}.velem .no{color:var(--ko)}.velem .nd{color:var(--muted)}
.concl{margin:4px 0}.exige{margin:4px 0}.exige>small{display:block;font-weight:600}.rem{margin:8px 0}.desc{margin:4px 0;font-style:italic}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);padding:8px;border-radius:4px;font-size:12px}
details{border:1px solid var(--line);border-radius:6px;margin:8px 0;padding:6px 10px}summary{cursor:pointer;font-weight:600}
dl.glos{display:grid;grid-template-columns:minmax(200px,max-content) 1fr;gap:4px 14px}dl.glos dt{font-weight:700}dl.glos dd{margin:0}
a.volver{font-size:12px}.tabla{overflow-x:auto}.tag,table.matriz th{white-space:nowrap}
table.matriz td:nth-child(2){white-space:nowrap;font-weight:600}
@media (max-width:700px){main{padding:8px}section{padding:12px}dl.glos{grid-template-columns:1fr}header{padding:16px}}
@page{size:A4 landscape;margin:10mm}
@media print{body{font-size:10px;background:#fff}main{max-width:none;padding:0}header{padding:8px 12px}section{border:0;padding:4px 0;margin:8px 0}
table{font-size:9px}th,td{padding:3px 4px;overflow-wrap:anywhere}tr,.srvcab{break-inside:avoid}h3,h4,.srvcab{break-after:avoid}
.badge{white-space:normal}a.volver,.botones,.solo-web{display:none}details.ctl{break-inside:auto}pre{font-size:8.5px}}
"""


# Abre el control al llegar desde un enlace de la matriz y antes de imprimir; botones para desplegar o plegar todos.
JS = """
function plegarTodo(abrir){document.querySelectorAll('details.ctl').forEach(d=>d.open=abrir)}
function abrirDestino(){const id=location.hash.slice(1);const d=id&&document.getElementById(id);
if(d&&d.tagName==='DETAILS'){d.open=true;d.scrollIntoView()}}
window.addEventListener('hashchange',abrirDestino);window.addEventListener('DOMContentLoaded',abrirDestino);
window.addEventListener('beforeprint',()=>document.querySelectorAll('details').forEach(d=>d.open=true));
"""


def generar(datos_hosts: List[Dict], ruta_html: Path, catalogos: Dict[str, Dict], dir_aprobados: Optional[Path] = None,
            pdf: bool = True) -> List[Path]:
    """Genera el informe consolidado (.html y, si hay WeasyPrint, .pdf). Devuelve los archivos generados."""
    datos_hosts = sorted(datos_hosts, key=lambda d: (d["hosts"][0]["host"] if d.get("hosts") else ""))
    hosts = []
    for d in datos_hosts:
        h = (d.get("hosts") or [{}])[0]
        r0 = next((r for r in d["resultados"] if r.get("fecha_evidencia")), {})
        hosts.append({"host": h.get("host") or r0.get("host"), "benchmark": h.get("benchmark") or r0.get("benchmark"),
                      "so": (h.get("sistema") or {}).get("nombre_completo") or f'{r0.get("sistema_operativo", "")} {r0.get("version", "")}'.strip(),
                      "fecha": str(r0.get("fecha_evidencia") or d.get("generado") or "")[:10]})
    prefijo = re.match(r"^(.*-)", hosts[0]["host"]).group(1) if hosts and "-" in hosts[0]["host"] else ""
    for hd in hosts:
        hd["corto"] = hd["host"][len(prefijo):] if prefijo and all(x["host"].startswith(prefijo) for x in hosts) else hd["host"]
    accion, pendientes = agrupar(datos_hosts)
    excepciones = agrupar_excepciones(datos_hosts)
    hoy = dt.date.today().isoformat()
    glosario = "".join(f"<dt>{_badge(k)}</dt><dd>{E(v)}</dd>" for k, v in (
        ("NO CUMPLE", "El servidor no cumple el control. Requiere remediación."),
        ("CUMPLE PARCIALMENTE", "Cumple una parte de las comprobaciones del control; deben corregirse las que fallan."),
        ("REQUIERE REMEDIACIÓN MANUAL", "Incumplimiento que no debe corregirse de forma automática: requiere análisis del impacto y coordinación."),
        ("REQUIERE AJUSTE MANUAL", "Incumplimiento puntual (p. ej. permisos de archivos de usuarios) que se corrige elemento por elemento."),
        ("REQUIERE VALIDACIÓN MANUAL", "La auditoría no pudo concluir de forma automática; requiere revisión manual en el servidor."),
        ("REQUIERE EVIDENCIA ORGANIZACIONAL", "Depende de una lista o política aprobada que debe aportar la organización."),
        ("PENDIENTE INFRAESTRUCTURA", "Advertencia: el incumplimiento depende de un despliegue de Infraestructura; se mantiene como No cumple."),
        ("NO APLICADA", "Exclusión de Infraestructura: la configuración que establece Infraestructura no está implementada en el servidor."),
        ("CON ADVERTENCIA", "Exclusión de Infraestructura cumplida, con una advertencia (p. ej. tamaño de partición inferior al recomendado)."),
        ("NO VERIFICADA", "Exclusión de Infraestructura que la auditoría no pudo verificar en el servidor.")))

    def documento(abierto: bool) -> str:
        fichas = "".join(_ficha(g, hosts, catalogos, i, abierto) for i, g in enumerate(accion, 1))
        ini_exc = len(accion) + 1
        fichas_exc = "".join(_ficha_excepcion(g, hosts, i, abierto) for i, g in enumerate(excepciones, ini_exc))
        ini_pend = ini_exc + len(excepciones)
        fichas_pend = "".join(_ficha(g, hosts, catalogos, i, abierto) for i, g in enumerate(pendientes, ini_pend))
        botones = ('<p class="muted solo-web">Cada control está plegado: haga clic en su título (o en el número de la matriz) para ver el detalle.</p>'
                   '<div class="botones"><button type="button" onclick="plegarTodo(true)">Desplegar todos</button>'
                   '<button type="button" onclick="plegarTodo(false)">Plegar todos</button></div>')
        return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Informe de remediación CIS</title><style>{CSS}</style></head><body>
<header><h1>Informe de remediación CIS — hallazgos por servidor</h1>
<p>Auditoría de hardening CIS Linux (solo lectura) · {len(hosts)} servidores · generado el {E(hoy)}</p></header><main>
<section><h2>1. Resumen por servidor</h2><div class="tabla">{_resumen(datos_hosts, hosts)}</div>
<p class="muted">El cumplimiento final ya considera las exclusiones de Infraestructura validadas en cada servidor. Los controles que
cumplen por una exclusión aplicada, los omitidos y los excluidos no aparecen como hallazgos; las exclusiones que el servidor no
cumple se detallan en la sección 5.</p></section>
<section><h2>2. Cómo leer este informe</h2>
<p>Cada control incluye <b>qué se espera</b> (el requisito que verificó la auditoría), <b>qué se encontró en cada servidor</b>
(comprobación, valor esperado y valor real) y <b>qué falta para cumplir</b>. Al final de cada control se adjunta la
<b>remediación de referencia</b> del Benchmark CIS oficial del sistema operativo correspondiente.</p>
<p>La auditoría fue exclusivamente de lectura: no se modificó ningún servidor. La remediación debe planificarse, probarse y
aplicarse por el proveedor conforme a la línea base aprobada; después se ejecutará una nueva auditoría para verificarla.</p>
<dl class="glos">{glosario}</dl></section>
<section id="matriz"><h2>3. Matriz consolidada de hallazgos ({len(accion)} controles)</h2>
<div class="tabla">{_matriz(accion, hosts, 1)}</div></section>
<section><h2>4. Detalle de los controles que requieren acción</h2>
{botones}{fichas or "<p>No hay controles que requieran acción.</p>"}</section>
{f'''<section id="excepciones"><h2>5. Exclusiones de Infraestructura no cumplidas en el servidor ({len(excepciones)} controles)</h2>
<p>Configuraciones que Infraestructura estableció para estos controles en lugar del valor CIS (o como condición adicional) y que
el servidor no tiene implementadas, o que se cumplen con una advertencia. Cuando existe una exclusión, el valor a configurar es
el que establece Infraestructura, aunque el control CIS cumpla o no cumpla por sí mismo.</p>
<div class="tabla">{_matriz_excepciones(excepciones, hosts, ini_exc)}</div>{botones}{fichas_exc}</section>''' if excepciones else ""}
{f'''<section id="pendientes"><h2>{6 if excepciones else 5}. Controles sin conclusión automática ({len(pendientes)})</h2>
<p>No son incumplimientos confirmados: requieren revisión manual o evidencia de la organización para concluir.</p>
<div class="tabla">{_matriz(pendientes, hosts, ini_pend)}</div>{fichas_pend}</section>''' if pendientes else ""}
{_anexo(accion + pendientes, dir_aprobados)}
</main><script>{JS}</script></body></html>"""

    ruta_html = Path(ruta_html)
    ruta_html.parent.mkdir(parents=True, exist_ok=True)
    ruta_html.write_text(documento(False), encoding="utf-8")
    salida = [ruta_html]
    if pdf:
        try:
            from weasyprint import HTML  # opcional: solo en la estación que genera el informe
        except ImportError:
            return salida
        ruta_pdf = ruta_html.with_suffix(".pdf")
        HTML(string=documento(True), base_url=str(ruta_html.parent)).write_pdf(str(ruta_pdf))
        salida.append(ruta_pdf)
    return salida
