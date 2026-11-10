"""Informe HTML autocontenido (sin recursos externos): Auditoría Completa de Hardening CIS Linux (Parte 1 + Parte 2).

Funciona también con resultados históricos de la Parte 2 sin campos de parte (se interpretan como PARTE_2/CIS
en memoria; el JSON no se modifica).
"""

import html
import json
import re
from pathlib import Path
from typing import Dict, List

from ..motor import partes
from ..motor.porcentaje import CATEGORIAS
from .excel import indicadores_de, unicos_de
from . import presentacion as pres

E = lambda v: html.escape("" if v is None else str(v))  # noqa: E731

CLASE = {
    "CUMPLE": "ok", "NO CUMPLE": "ko", "CUMPLE PARCIALMENTE": "parcial", "REQUIERE VALIDACIÓN MANUAL": "pend",
    "REQUIERE EVIDENCIA ORGANIZACIONAL": "pend", "NO APLICA": "na", "FUERA DE ALCANCE": "na", "RETIRADO EN CIS ACTUAL": "na",
    "REQUIERE CONFIRMACIÓN": "pend", "PENDIENTE DE CONFIRMACIÓN": "pend", "PENDIENTE DE DEFINICIÓN": "pend",
    "ERROR DE AUDITORÍA": "err", "CONFLICTO DE BASELINE": "conf", "NINGUNA": "na", "PARCIAL": "parcial",
    "NO VERIFICABLE TÉCNICAMENTE": "pend", "CONTROL EXCLUIDO": "omit", "PENDIENTE": "pend",
    "APLICADA": "ok", "NO APLICADA": "ko", "NO VERIFICADA": "na", "REQUIERE REMEDIACIÓN MANUAL": "parcial",
    "REQUIERE AJUSTE MANUAL": "parcial", "OMITIDO": "omit",
}

CSS = """
:root{--bg:#f6f7f9;--fg:#1d232b;--muted:#5b6672;--card:#fff;--line:#d9dee4;--ok:#1e7b34;--okb:#e3f4e7;--ko:#b3261e;--kob:#fbe6e4;
--par:#8a5a00;--parb:#fff2cc;--na:#555;--nab:#ececec;--err:#5b2a86;--errb:#efe5f8;--conf:#0b5c7a;--confb:#e0f1f7;--acc:#1f3864;
--p1:#0b5c7a;--p1b:#e0f1f7;--p2:#5b2a86;--p2b:#efe5f8;--pre:#8a5a00;--preb:#fff2cc;--adv:#b45309;--advb:#ffedd5}
@media (prefers-color-scheme:dark){:root{--bg:#14181d;--fg:#e6e9ed;--muted:#9aa5b1;--card:#1c2229;--line:#303842;--okb:#16351f;--ok:#7fd99a;
--kob:#3d1a18;--ko:#ff9c94;--parb:#3a2f12;--par:#ffd479;--nab:#2a2f35;--na:#c7ccd1;--errb:#2e2140;--err:#d1b3f2;--confb:#12313d;--conf:#8fd3ee;--acc:#9db8ff;
--p1:#8fd3ee;--p1b:#12313d;--p2:#d1b3f2;--p2b:#2e2140;--pre:#ffd479;--preb:#3a2f12;--adv:#fdba74;--advb:#3b2410}}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg)}
header{background:var(--acc);color:#fff;padding:24px 32px}header h1{margin:0 0 4px;font-size:22px}header p{margin:0;opacity:.9}
main{max-width:1320px;margin:0 auto;padding:16px}section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px 20px;margin:16px 0}
h2{font-size:18px;margin:0 0 12px}h3{font-size:15px;margin:16px 0 8px}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{background:var(--bg);position:sticky;top:0}
.bloques{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:12px}
.bloque{border:1px solid var(--line);border-radius:8px;padding:12px 14px}.bloque h3{margin:0 0 6px}
.bloque .pct{display:flex;gap:16px}.bloque .pct div b{display:block;font-size:24px}.bloque .pct span{color:var(--muted);font-size:12px}
.bloque table td{padding:2px 6px;border:0}.bloque table td:last-child{text-align:right;font-weight:600}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-weight:600;font-size:12px;white-space:nowrap;border:1px solid currentColor}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-weight:700;font-size:11px;font-family:ui-monospace,monospace;border:1px solid currentColor;margin-right:2px}
.tp1{color:var(--p1);background:var(--p1b)}.tp2{color:var(--p2);background:var(--p2b)}.tpre{color:var(--pre);background:var(--preb)}.tcis{color:var(--muted);background:var(--bg)}
.ok{color:var(--ok);background:var(--okb)}.ko{color:var(--ko);background:var(--kob)}.parcial,.pend{color:var(--par);background:var(--parb)}
.na{color:var(--na);background:var(--nab)}.omit{color:var(--na);background:var(--nab);border-style:dashed}.err{color:var(--err);background:var(--errb)}.conf{color:var(--conf);background:var(--confb)}
.bar{display:flex;height:16px;border-radius:4px;overflow:hidden;border:1px solid var(--line);margin:6px 0}.bar div{height:100%}
.formula{background:var(--bg);border-left:4px solid var(--acc);padding:8px 12px;font-family:ui-monospace,monospace;font-size:12px}
details{border:1px solid var(--line);border-radius:6px;margin:6px 0;padding:6px 10px}summary{cursor:pointer}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);padding:8px;border-radius:4px;font-size:12px;max-height:340px;overflow:auto}
.muted{color:var(--muted)}.filtros{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;margin-bottom:8px}
input,select{padding:6px;border:1px solid var(--line);border-radius:4px;background:var(--card);color:var(--fg);width:100%}
.aviso{border:2px solid var(--ko);color:var(--ko);padding:10px;border-radius:6px;font-weight:600}
.nota{border-left:4px solid var(--par);padding:6px 10px;background:var(--parb);color:var(--fg)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}.kpi{border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.kpi b{display:block;font-size:22px}.kpi span{color:var(--muted);font-size:12px}td.ev{max-width:520px}td.pre{white-space:pre-line}.prebloque{grid-column:1/-1}.bloque.prebloque table.pretabla td{border:0;border-bottom:1px solid var(--line);padding:6px 8px;vertical-align:top;text-align:left;font-weight:normal}.bloque.prebloque table.pretabla tbody tr:last-child td{border-bottom:0}td small{color:var(--muted)}
tr.det>td{background:var(--bg);padding:10px 18px 16px;border-bottom:2px solid var(--acc)}.ficha{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 16px 12px;max-width:1240px}
.plegable{max-height:11em;overflow:hidden;position:relative}.plegable.abierto{max-height:none}
.plegable:not(.abierto)::after{content:"";position:absolute;left:0;right:0;bottom:0;height:2.5em;background:linear-gradient(to bottom,transparent,var(--card))}
@media print{.plegable{max-height:none}.plegable::after{display:none}button.vermas,button.plegar{display:none}li.mas{display:list-item!important}}
@page{size:A4 landscape;margin:10mm}
@media print{body{font-size:10px;background:#fff}main{max-width:none;padding:0}header{padding:8px 12px}
section{border:0;padding:4px 0;margin:8px 0}div[style*="overflow-x"]{overflow:visible!important}
table{font-size:9px;width:100%}th{position:static}th,td{padding:3px 4px;overflow-wrap:anywhere;word-break:break-word}
td.ev{max-width:none}tr{break-inside:avoid}.filtros,button.ver{display:none}[hidden],tr.det[hidden]{display:none!important}
.badge{white-space:normal}.tag{white-space:nowrap;font-size:8px}
table.tctl,table.texcl{table-layout:fixed}
table.tctl col.c1{width:6%}table.tctl col.c2{width:7%}table.tctl col.c3{width:13%}table.tctl col.c4{width:6%}table.tctl col.c5{width:11%}
table.tctl col.c6{width:36%}table.tctl col.c7{width:7%}table.tctl col.c8{width:11%}table.tctl col.c9{width:3%}
table.texcl col.e1{width:5%}table.texcl col.e2{width:9%}table.texcl col.e3{width:14%}table.texcl col.e4{width:19%}table.texcl col.e5{width:27%}
table.texcl col.e6{width:9%}table.texcl col.e7{width:17%}}
a.excl-def{color:var(--acc);font-weight:700}a.volver{font-size:12px}.velem{margin:4px 0 0 0;padding:0;list-style:none}
.velem li{margin:1px 0}.velem .si{color:var(--ok);font-weight:700}.velem .no{color:var(--ko);font-weight:700}.velem .nd{color:var(--muted);font-weight:700}
.adv{color:var(--adv);font-weight:600}.badge.adv{background:var(--advb);border-color:var(--adv)}.velem li.adv{background:var(--advb);border-left:3px solid var(--adv);padding-left:4px}
tr.excl:target td{outline:2px solid var(--acc)}
button.vermas,button.plegar{padding:0;border:0;background:none;color:var(--acc);cursor:pointer;font-weight:600;text-decoration:underline}
button.ver{padding:4px 10px;border:1px solid var(--acc);border-radius:4px;background:var(--card);color:var(--acc);cursor:pointer;font-weight:600;white-space:nowrap}
.ficha h4{margin:14px 0 6px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--acc);border-bottom:1px solid var(--line);padding-bottom:2px}
.ficha ul{margin:4px 0 4px 18px;padding:0}.ficha li{margin:1px 0}.ficha .resul{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.ficha table.comp td{vertical-align:top}.ficha table.comp td.sim{font-size:15px;text-align:center;width:28px}
.sim.OK{color:var(--ok)}.sim.FALLA{color:var(--ko)}.sim.ERROR{color:var(--err)}.sim.MANUAL,.sim.ORG{color:var(--par)}.sim.INFO,.sim.NA,.sim.FUERA{color:var(--muted)}
.ficha .lbl{font-weight:600}.ficha .tipo{display:inline-block;min-width:74px;font-weight:600;color:var(--muted)}
.ficha pre.excl{max-height:200px}td.ev ul{margin:2px 0 2px 16px;padding:0}.leidos{margin-top:6px;padding-top:4px;border-top:1px dashed var(--line)}td.ev .cab{font-weight:600}
dl.glos{display:grid;grid-template-columns:minmax(180px,max-content) 1fr;gap:4px 14px;margin:6px 0}dl.glos dt{font-weight:700;font-family:ui-monospace,monospace;font-size:12px}dl.glos dd{margin:0}
@media (max-width:700px){dl.glos{grid-template-columns:1fr}main{padding:8px}section{padding:12px}th,td{padding:4px}}
"""

_FILTROS = [("fparte", "parte", "Parte"), ("fres", "estado", "Resultado"), ("fexc", "exclusion", "Exclusión de Infraestructura"),
            ("fhost", "host", "Host"), ("fcrit", "riesgo", "Criticidad")]

JS = """
function filtrar(){const q=document.getElementById('q').value.toLowerCase();
const f={};""" + "".join(f"f['{a}']=document.getElementById('{i}').value;" for i, a, _ in _FILTROS) + """
let n=0;document.querySelectorAll('tr.ctl').forEach(d=>{let ok=!q||d.dataset.texto.includes(q);
for(const k in f){if(f[k]&&d.dataset[k]!==f[k]){ok=false;break}}d.style.display=ok?'':'none';
const det=d.nextElementSibling;if(det&&det.classList.contains('det')&&!ok){det.hidden=true;const b=d.querySelector('button.ver');if(b){b.textContent='Ver evidencia';b.setAttribute('aria-expanded','false')}}if(ok)n++});
document.getElementById('nvis').textContent=n}
function verMas(b){const ul=b.closest('ul');const ab=b.getAttribute('aria-expanded')!=='true';
ul.querySelectorAll('li.mas').forEach(li=>li.hidden=!ab);b.setAttribute('aria-expanded',String(ab));
const pl=b.closest('.plegable');if(pl&&ab&&!pl.classList.contains('abierto')){const pb=pl.nextElementSibling;if(pb&&pb.classList.contains('plegar'))plegar(pb)}
b.textContent=ab?'▴ Ver menos':'▸ Ver los '+b.dataset.n+' restantes'}
function plegar(b){const d=b.previousElementSibling;const ab=!d.classList.contains('abierto');d.classList.toggle('abierto',ab);
b.setAttribute('aria-expanded',String(ab));b.textContent=ab?'▴ Ver menos':'▸ Ver más detalle'}
function verEvidencia(b){const det=b.closest('tr').nextElementSibling;det.hidden=!det.hidden;
b.textContent=det.hidden?'Ver evidencia':'Ocultar evidencia';b.setAttribute('aria-expanded',String(!det.hidden))}
"""

def _instr_host(h) -> str:
    """D-3B-01: la instrumentación temporal de auditoría se muestra siempre que exista (ventana de auditoría)."""
    ins = h.get("instrumentacion_auditoria") or {}
    if not ins.get("archivo_presente"):
        return ""
    if ins.get("instrumentacion_auditoria_detectada"):
        txt = (f"Instrumentación temporal de auditoría: {ins['archivo']} (exacta; "
               f"{'excluida del resultado del proveedor' if ins.get('instrumentacion_excluida_del_resultado_proveedor') else 'NO excluida: ' + str(ins.get('motivo_no_excluida'))}). "
               + (ins.get("observacion") or "") + " No es una excepción CIS permanente; si se mantuviera, debe revisarse la decisión.")
    else:
        txt = f"{ins['archivo']} existe pero NO coincide con la instrumentación aprobada ({ins.get('motivo_no_exacta')}); se evalúa como regla normal."
    rp = h["sistema"].get("recolector_privilegiado") or {}
    hashes = (f"SHA-256 recolector: {rp.get('sha256_encontrado') or '—'} · "
              f"SHA-256 sudoers archivo: encontrado {ins.get('sudoers_sha256_archivo_encontrado') or '—'} / aprobado {ins.get('sudoers_sha256_archivo_aprobado') or '—'} · "
              f"SHA-256 sudoers semántico (adicional): encontrado {ins.get('sudoers_sha256_semantico_encontrado') or '—'}")
    return f"<br><small>{E(txt)}</small><br><small class=\"sha\">{E(hashes)}</small>"


def _instr_fila(r: Dict) -> str:
    """Instrumentación de auditoría relacionada con el control (D-3B-01) y elementos que cubrió en la capa final."""
    ins = r.get("instrumentacion_auditoria") or {}
    cub = ((r.get("detalle_exclusion") or {}).get("elementos_por_instrumentacion")) or []
    if not ins and not cub:
        return "—"
    t = (f"{ins.get('archivo')}: {'exacta' if ins.get('instrumentacion_auditoria_detectada') else 'NO exacta'}"
         f"{' (' + str(ins.get('motivo_no_exacta')) + ')' if ins.get('motivo_no_exacta') else ''}") if ins else ""
    if cub:
        t += "; INSTRUMENTACION_AUDITORIA: " + ", ".join(f"{e.get('usuario')} ({e.get('archivo')}:{e.get('linea')})" for e in cub)
    return t


# Evidencia larga: se muestra recortada (contenedor plegable) con "Ver más detalle" para leerla completa.
PLEGAR_DESDE = 600


def _plegable(html: str, largo: int) -> str:
    """Envuelve el HTML en un contenedor recortado si el texto supera PLEGAR_DESDE caracteres (se despliega con un clic)."""
    if largo <= PLEGAR_DESDE:
        return html
    return (f'<div class="plegable">{html}</div><button type="button" class="plegar" onclick="plegar(this)" '
            f'aria-expanded="false">▸ Ver más detalle</button>')


def _texto_plegable(texto) -> str:
    t = "" if texto is None else str(texto)
    return _plegable(E(t) or "—", len(t))


def _lista(texto, max_items: int = 0) -> str:
    """Texto con varios elementos ('a; b; c', '1) a · 2) b') como encabezado en negrita + viñetas; si no, texto plano.

    Si el contenido visible es largo, queda en un contenedor plegable con "Ver más detalle"."""
    cab, it = pres.elementos(texto)
    if len(it) <= 1:
        return _plegable((f'<span class="cab">{E(cab)}</span> ' if cab else "") + E(it[0] if it else "—"),
                         len(cab or "") + len(it[0] if it else ""))
    extra = ""
    if max_items and len(it) > max_items:
        resto = len(it) - max_items
        extra = ("".join(f"<li class=mas hidden>{E(x)}</li>" for x in it[max_items:])
                 + f'<li><button type="button" class="vermas" onclick="verMas(this)" aria-expanded="false" data-n="{resto}">'
                 f'▸ Ver los {resto} restantes</button></li>')
        it = it[:max_items]
    html = (f'<span class="cab">{E(cab)}</span>' if cab else "") + "<ul>" + "".join(f"<li>{E(x)}</li>" for x in it) + extra + "</ul>"
    return _plegable(html, len(cab or "") + sum(len(x) for x in it))


def _leidos(r: Dict) -> str:
    """Archivos de configuración leídos en el servidor (journald, rsyslog...), visibles junto a la conclusión."""
    t = pres.archivos_leidos(r)
    return f'<div class="leidos"><span class="cab">Archivos revisados en el servidor:</span>{_lista(t) if len(pres.elementos(t)[1]) > 1 else " <code>" + E(t) + "</code>"}</div>' if t else ""


def _motivo_fuera(f: Dict) -> str:
    cab, txt = pres.motivo_fuera_partes(f)
    return (f'<b><u>{E(cab)}</u></b> — ' if cab else "") + E(txt)


def _ficha(r: Dict) -> str:
    """Ficha del control: conclusión, qué exige el CIS, dónde se buscó, qué se encontró, exclusión, qué falta, trazabilidad."""
    f = pres.ficha(r)
    h = [f'<div class="ficha"><h4>1. Resultado y conclusión</h4><div class="resul"><span class="lbl">Técnico CIS</span>{_badge(f["tecnico"])}'
         f'<span>→</span><span class="lbl">Final</span>{_badge(f["final"])}<small class=muted>{E(f["tipo"] or "")}</small></div>'
         f'<div>{_lista(f["conclusion"])}</div>{_leidos(r)}'
         + (f'<div class="nota"><b>Acción requerida ({E(f["tratamiento"])}):</b> {E(f["tratamiento_motivo"])}</div>' if f.get("tratamiento") else "")]
    h.append(f'<h4>2. Qué exige el CIS</h4><div>{_lista(f["exige"])}</div><small class=muted>{E(f["referencia"])}</small>')
    h.append("<h4>3. Dónde se buscó</h4><ul>" + ("".join(f'<li><span class="tipo">{E(t)}</span> '
                                                          + (f'<code>{E(u)}</code>' if len(pres.elementos(u)[1]) <= 1 else _lista(u)) + '</li>'
                                                          for t, u in f["donde"])
                                                  or "<li>—</li>") + "</ul>")
    filas = "".join(f'<tr><td class="sim {E(c["codigo"])}" title="{E(c["estado"])}">{E(c["simbolo"])}</td><td><b>{E(c["que"])}</b>'
                    f'<br><small class=muted>{E(c["estado"])}</small></td><td>{_lista(c["esperado"])}</td><td>{_lista(c["encontrado"])}</td></tr>'
                    for c in f["comprobaciones"])
    h.append('<h4>4. Qué se encontró</h4><table class="comp"><thead><tr><th></th><th>Comprobación</th><th>Esperado</th><th>Encontrado</th>'
             f'</tr></thead><tbody>{filas or "<tr><td colspan=4>—</td></tr>"}</tbody></table>')
    e = f["exclusion"]
    if e:
        v = e.get("validacion") or {}
        h.append(f'<h4>5. Exclusión de Infraestructura: <a class="excl-def" href="#{ancla_exclusion(r)}">Definida</a></h4>'
                 f'<div class="resul"><span class="lbl">Validación en el servidor:</span>{_badge(v.get("estado") or "NO VERIFICADA")}'
                 + _advertencias(v) + '</div>'
                 f'<div><span class="lbl">Esperado:</span> {E(v.get("esperado") or "—")}</div>'
                 f'<div><span class="lbl">Encontrado:</span> {E(v.get("encontrado") or "—")}</div>'
                 + _plegable(f'<pre class="excl">{E(e["texto"])}</pre>', len(e["texto"] or ""))
                 + (f'<div><span class="lbl">Hallazgos cubiertos por la exclusión:</span><ul>{"".join(f"<li>{E(x)}</li>" for x in e["cubiertos"])}</ul></div>' if e["cubiertos"] else "")
                 + (f'<div><span class="lbl">Hallazgos NO cubiertos:</span><ul>{"".join(f"<li>{E(x)}</li>" for x in e["no_cubiertos"])}</ul></div>' if e["no_cubiertos"] else ""))
    else:
        h.append('<h4>5. Exclusión de Infraestructura</h4><div class=muted>Sin exclusión definida en la línea base.</div>')
    if f["falta"]:
        h.append("<h4>6. Qué falta</h4><ul>" + "".join(f"<li>{E(x)}</li>" for x in f["falta"]) + "</ul>")
    h.append('<h4>7. Trazabilidad</h4><table><tbody>' + "".join(f"<tr><th>{E(k)}</th><td>{E(v)}</td></tr>" for k, v in f["traza"]) + "</tbody></table>")
    return "".join(h) + "</div>"


def _badge(estado: str) -> str:
    return f'<span class="badge {CLASE.get(estado, "na")}">{E(estado)}</span>'


def _tags(r: Dict) -> str:
    p = partes.INSIGNIA[r["parte_proyecto"]]
    t = partes.INSIGNIA_TIPO[r["tipo_control"]]
    return (f'<span class="tag {"tp1" if p == "P1" else "tp2"}" title="{E(r["parte_proyecto"])}">[{p}]</span>'
            f'<span class="tag {"tpre" if t == "PRE" else "tcis"}" title="{E(r["tipo_control"])}">[{t}]</span>')


def _pct(v):
    return "n/d" if v is None else f"{v:.1f} %".replace(".", ",")


def _barra(conteo: Dict[str, int]) -> str:
    total = sum(conteo.values()) or 1
    colores = {"CUMPLE": "var(--ok)", "NO CUMPLE": "var(--ko)", "CUMPLE PARCIALMENTE": "var(--par)",
               "REQUIERE VALIDACIÓN MANUAL": "#d4a72c", "REQUIERE EVIDENCIA ORGANIZACIONAL": "#e8c46a",
               "NO APLICA": "#9aa0a6", "ERROR DE AUDITORÍA": "var(--err)", "CONFLICTO DE BASELINE": "var(--conf)",
               "PENDIENTE DE CONFIRMACIÓN": "#e8c46a", "PENDIENTE DE DEFINICIÓN": "#d4a72c", "CONTROL EXCLUIDO": "#c7ccd1",
               "OMITIDO": "#b0b6bd", "REQUIERE REMEDIACIÓN MANUAL": "#c0841a", "REQUIERE AJUSTE MANUAL": "#d99a2b"}
    return '<div class="bar" role="img" aria-label="Distribución de estados">' + "".join(
        f'<div title="{E(k)}: {v}" style="width:{100 * v / total:.2f}%;background:{colores.get(k, "#999")}"></div>'
        for k, v in conteo.items() if v and k != "FUERA DE ALCANCE") + "</div>"


def _bloque(titulo: str, v: Dict, nota: str = "") -> str:
    c = v["conteo"]
    filas = [("Controles en alcance (filas)", v["filas_yes_auditadas"]), ("Evaluados", v["evaluables"]), ("CUMPLE", c.get("CUMPLE", 0)),
             ("NO CUMPLE", c.get("NO CUMPLE", 0)), ("CUMPLE PARCIALMENTE", c.get("CUMPLE PARCIALMENTE", 0)),
             ("REQUIERE VALIDACIÓN MANUAL", c.get("REQUIERE VALIDACIÓN MANUAL", 0)), ("NO APLICA", c.get("NO APLICA", 0)),
             ("ERROR DE AUDITORÍA", c.get("ERROR DE AUDITORÍA", 0)),
             ("REQUIERE EVIDENCIA ORGANIZACIONAL", c.get("REQUIERE EVIDENCIA ORGANIZACIONAL", 0)),
             ("CONFLICTO DE BASELINE", c.get("CONFLICTO DE BASELINE", 0)),
             ("de ellos REQUIERE_EXTENSION_RECOLECTOR", (v.get("motivos") or {}).get("REQUIERE_EXTENSION_RECOLECTOR", 0)),
             ("de ellos PERMISOS_INSUFICIENTES", (v.get("motivos") or {}).get("PERMISOS_INSUFICIENTES", 0))]
    return (f'<div class="bloque"><h3>{titulo}</h3><div class="pct"><div><b>{_pct(v["porcentaje_cumplimiento"])}</b>'
            f'<span>Cumplimiento CIS técnico</span></div><div><b>{_pct(v["cobertura_tecnica"])}</b>'
            f'<span>Cobertura técnica CIS</span></div></div>{_barra(c)}<table>'
            + "".join(f"<tr><td>{E(k)}</td><td>{E(n)}</td></tr>" for k, n in filas) + f"</table>{nota}</div>")


def _tabla_resumen(titulo: str, grupos: Dict[str, Dict]) -> str:
    filas = "".join(
        f"<tr><td>{E(k)}</td><td>{v['filas_yes_auditadas']}</td>" + "".join(f"<td>{v['conteo'].get(c, 0)}</td>" for c in CATEGORIAS)
        + f"<td><b>{_pct(v['porcentaje_cumplimiento'])}</b></td><td><b>{_pct(v['cobertura_tecnica'])}</b></td></tr>"
        for k, v in grupos.items())
    cab = "".join(f"<th>{E(c)}</th>" for c in CATEGORIAS)
    return (f"<h3>{E(titulo)}</h3><div style='overflow-x:auto'><table><thead><tr><th>Grupo</th><th>Filas en alcance</th>{cab}"
            f"<th>Cumplimiento CIS técnico</th><th>Cobertura técnica CIS</th></tr></thead><tbody>{filas}</tbody></table></div>")


_PEND = {"EVIDENCIA_ORGANIZACIONAL": "EVIDENCIA ORGANIZACIONAL", "POLITICA_O_REVISION": "EVIDENCIA ORGANIZACIONAL",
         "CONFLICTO_BASELINE": "CONFLICTO DE BASELINE", "ANALISIS_ESTATICO_NO_CONCLUYENTE": "AUTOMATIZACIÓN PENDIENTE",
         "REQUIERE_EXTENSION_RECOLECTOR": "AUTOMATIZACIÓN PENDIENTE", "PENDIENTE_AUDITORIA_PROFUNDA": "AUTOMATIZACIÓN PENDIENTE",
         "EVIDENCIA_HOST_INSUFICIENTE": "AUTOMATIZACIÓN PENDIENTE"}
_SIN_CONCLUSION = ("REQUIERE EVIDENCIA ORGANIZACIONAL", "REQUIERE VALIDACIÓN MANUAL")


def _final(r: Dict) -> str:
    return r.get("resultado_final") or r["resultado_cis"]


def categoria_pendiente(r: Dict) -> str:
    """Clasificación de una fila sin conclusión (Pendientes)."""
    if r.get("conflicto_baseline"):
        return "CONFLICTO DE BASELINE"
    if _final(r) == "REQUIERE EVIDENCIA ORGANIZACIONAL":
        return "EVIDENCIA ORGANIZACIONAL"
    return _PEND.get(r.get("motivo_resultado") or "", "VALIDACIÓN MANUAL")


def pendientes(cis: List[Dict]) -> List[Dict]:
    return [r for r in cis if _final(r) in _SIN_CONCLUSION or r.get("conflicto_baseline")]


def _bucket(r: Dict) -> str:
    f = _final(r)
    return {"CUMPLE PARCIALMENTE": "PARCIAL"}.get(f, "PENDIENTE" if f in _SIN_CONCLUSION or r.get("conflicto_baseline") else f)


def ancla_exclusion(r: Dict) -> str:
    """Id del detalle de la exclusión de la fila (enlace interno del informe y del PDF impreso)."""
    return "excl-" + re.sub(r"[^A-Za-z0-9_-]", "-", str(r.get("id_resultado") or ""))


def ancla_control(r: Dict) -> str:
    return "ctl-" + re.sub(r"[^A-Za-z0-9_-]", "-", str(r.get("id_resultado") or ""))


def _exc(r: Dict) -> str:
    """'Definida' (enlace al detalle) solo si existe una exclusión configurada; no implica que esté aplicada."""
    if not r.get("exclusion_definida"):
        return "—"
    return f'<a class="excl-def" href="#{ancla_exclusion(r)}" title="Ir al detalle y a la validación de la exclusión">Definida</a>'


def _tratamiento(r: Dict) -> str:
    """Tratamiento distinto del resultado final (si coincide, el badge del resultado ya lo muestra: sin duplicar)."""
    adv = r.get("tratamiento") == "PENDIENTE INFRAESTRUCTURA"
    return (f'<br><span class="badge {"adv" if adv else "parcial"}" title="{E(r.get("tratamiento_motivo"))}">'
            f'{"⚠ " if adv else ""}Tratamiento: {E(r["tratamiento"])}</span>'
            if r.get("tratamiento") and r["tratamiento"] != _final(r) else "")


def _descripcion(r: Dict) -> str:
    return r.get("titulo_cis") or r.get("titulo_excel") or ""


def _secundario(r: Dict) -> str:
    evid = "".join(f"<li><code>{E(e['comando_id'])}</code> rc={E(e.get('rc'))} sudo={'sí' if e.get('sudo') else 'no'}"
                   f" · evidence_id {E(partes.evidence_id(r['benchmark'], e['comando_id'], r['host']))}"
                   f"{' — omitido: ' + E(e['omitido']) if e.get('omitido') else ''}<pre>{E(e.get('comando'))}</pre></li>"
                   for e in r.get("evidencia") or [])
    filas = [("Resultado CIS técnico", _badge(r["resultado_cis"]) + (f" {E(r.get('tipo_parcial'))}" if r.get("tipo_parcial") else "")),
             ("Resultado final", _badge(_final(r))),
             ("Tipo de cumplimiento", E(pres.tipo_cumplimiento(r) or "—")),
             ("Exclusión / omisión", E(_omision(r))),
             ("Instrumentación", E(_instr_fila(r))),
             ("Hallazgos cubiertos", "<br>".join(E(x) for x in r.get("hallazgos_cubiertos") or []) or "Ninguno"),
             ("Hallazgos no cubiertos", "<br>".join(E(x) for x in r.get("hallazgos_no_cubiertos") or []) or "Ninguno"),
             ("Fuente de política", E(fuente_politica(r) or "—")),
             ("Comparación", E(json.dumps({k: v for k, v in ((r.get("mecanismo") or {}).get("comparacion_banner") or {}).items()
                                            if k not in ("mensaje",)}, ensure_ascii=False)) if (r.get("mecanismo") or {}).get("comparacion_banner") else "—"),
             ("Observación (línea base)", E(r.get("observacion") or "—")),
             ("Qué pide CIS", E(r.get("valor_esperado_cis") or "—") + f"<br><small>{E(r.get('referencia_cis'))}</small>"),
             ("Qué falta", E(r.get("que_falta") or "—")),
             ("Exclusión de Infraestructura", E(pres.texto_exclusion(r.get("exclusion_infraestructura"))) or "—"),
             ("Detalle técnico de la exclusión", f"<pre>{E(json.dumps(pres.sin_referencias(r['detalle_exclusion']), ensure_ascii=False, indent=1))}</pre>"
              if r.get("detalle_exclusion") else "—"),
             ("Observaciones del auditor", "<br>".join(E(o) for o in r.get("observaciones") or []) or "—")]
    return (_ficha(r)
            + f"<details><summary>8. Comandos ejecutados y evidencia técnica</summary><p class=muted>Ruta: {E(r.get('ruta_evidencia'))}</p>"
              f"<ul>{evid or '<li>—</li>'}</ul></details>"
            + "<details><summary>9. Datos técnicos completos</summary><table><tbody>" + "".join(f"<tr><th>{t}</th><td>{v}</td></tr>" for t, v in filas)
            + "</tbody></table></details>")


_ESTADO_COMP = {"FUERA": "FUERA DE ALCANCE", "INFO": "INFORMATIVO (no decide)", "NA": "NO APLICA"}


def fuente_politica(r: Dict) -> str:
    """Fuente de la política del sitio usada por el control (banner, listas aprobadas, decisión de Infraestructura)."""
    cb = (r.get("mecanismo") or {}).get("comparacion_banner") or {}
    return r.get("fuente_politica") or (r.get("mecanismo") or {}).get("fuente_politica") or cb.get("fuente_politica") or ""


def _omision(r: Dict) -> str:
    t = r.get("tipo_cumplimiento") or ""
    if t == "CONTROL_EXCLUIDO_PROCESO_INDEPENDIENTE":
        return "CONTROL EXCLUIDO — " + str((r.get("detalle_exclusion") or {}).get("fuente") or "proceso independiente")
    if t in ("CONTROL_EXCLUIDO_INFRAESTRUCTURA", "CONTROL_OMITIDO_INFRAESTRUCTURA"):
        return "OMITIDO — decisión de Infraestructura (regla del hardening desactivada)"
    if t == "NO_APLICA_METODO_LOGGING":
        return "NO APLICA por método de logging — " + str((r.get("mecanismo") or {}).get("fuente_aplicabilidad") or "")
    if r.get("exclusion_aplicada"):
        return "La exclusión cubre los hallazgos — " + (r.get("detalle_exclusion_texto") or "")
    return "Ninguna"


def _banner_extra(r: Dict) -> str:
    cb = (r.get("mecanismo") or {}).get("comparacion_banner")
    return (f"<br><small>Fuente de política: {E(cb.get('fuente_politica'))} · Comparación: {E(cb.get('comparacion'))}</small>" if cb else "")


def _fila(r: Dict) -> str:
    a = {"parte": partes.INSIGNIA[r["parte_proyecto"]], "estado": _bucket(r), "exclusion": "CON" if r.get("exclusion_definida") else "SIN",
         "host": r["host"], "riesgo": r.get("criticidad") or ""}
    texto = " ".join(str(x) for x in (r["host"], r.get("control_cis"), _descripcion(r), r["control_excel"], _final(r),
                                      r.get("evidencia_motivo") or "", r.get("detalle_exclusion_texto") or "")).lower()
    data = " ".join(f'data-{k}="{E(v)}"' for k, v in a.items())
    return (f'<tr class="ctl" id="{ancla_control(r)}" {data} data-texto="{E(texto)}"><td>{_tags(r)}</td>'
            f'<td><b>{E(r["control_excel"])}</b><br><small>CIS {E(r.get("control_cis"))}</small></td><td>{E(_descripcion(r))}</td>'
            f'<td>{E(r.get("criticidad"))}</td><td>{_badge(_final(r))}{_tratamiento(r)}</td><td class="ev">{_lista(r.get("evidencia_motivo"), 8)}{_leidos(r)}'
            f'{_banner_extra(r)}</td>'
            f'<td>{_exc(r)}</td><td>{E(r.get("detalle_exclusion_texto") or "—")}'
            f'{("<br><small>Instrumentación: " + E(r["instrumentacion_texto"]) + "</small>") if r.get("instrumentacion_texto") else ""}</td>'
            f'<td><button type="button" class="ver" onclick="verEvidencia(this)" aria-expanded="false">Ver evidencia</button></td></tr>'
            f'<tr class="det" hidden><td colspan="9">{_secundario(r)}</td></tr>')


def filas_exclusion(cis: List[Dict]) -> List[Dict]:
    """Solo exclusiones REALES: P1 desde la columna "Exclusiones de Infraestructura" del Excel P1 (fuente canónica);
    P2 desde la columna EXCLUSIONES (Infraestructura Linux)."""
    res = []
    for r in cis:
        if not r.get("exclusion_definida"):
            continue
        det = r.get("detalle_exclusion") or {}
        ex = r.get("exclusion") or {}
        if r["parte_proyecto"] == partes.PARTE_1 or r.get("exclusion_p2_usuarios"):
            d = r.get("exclusion_p1") or r.get("exclusion_p2_usuarios") or {}
            cub = [f"{e['usuario']}" + (f" ({e['archivo']}:{e['linea']})" if e.get("archivo") else f"={e.get('valor')}" if e.get("valor") else "")
                   for e in det.get("elementos_por_excepcion") or []] + list(det.get("componentes_por_exclusion") or [])
            hall = [c["encontrado"] for c in r.get("componentes") or [] if c["estado"] == "FALLA"]
            fila = {"_tipo": pres.TIPO_EXCLUSION.get(d.get("tipo"), d.get("tipo")), "_autorizados": ", ".join(d.get("usuarios") or []) or f"control completo (VALOR {d.get('valor')})",
                    "_hallazgos": "; ".join(hall) or "ninguno (el control cumple CIS)", "_cubiertos": "; ".join(cub) or "—",
                    "_no_cubiertos": "; ".join(r.get("hallazgos_no_cubiertos") or []) or "ninguno", "_motivo_exc": d.get("motivo")}
            # Compatibilidad con columnas comunes:
            fila.update({"_definido": fila["_autorizados"], "_encontrado": fila["_hallazgos"]})
        else:
            definido = "; ".join(f"{k} = {v}" for k, v in (r.get("exclusion_parametros") or {}).items()) or pres.texto_exclusion(ex.get("texto"))[:240]
            motivo = (r.get("evidencia_motivo") if r.get("exclusion_aplicada") else
                      f"No aplicada: {('no es necesaria (resultado CIS técnico ' + r['resultado_cis'] + ')') if r['resultado_cis'] in ('CUMPLE', 'NO APLICA') else 'no cubre el hallazgo actual'}. "
                      f"Estado de la condición de Infraestructura: {ex.get('estado')}" + (f" ({ex.get('que_falta')})" if ex.get("que_falta") else "") + ".")
            fila = {"_tipo": pres.TIPO_EXCLUSION["CONFIGURACION"], "_definido": definido, "_autorizados": definido, "_encontrado": ex.get("encontrado") or "—",
                    "_hallazgos": "; ".join(c["encontrado"] for c in r.get("componentes") or [] if c["estado"] == "FALLA") or "ninguno",
                    "_cubiertos": "; ".join(det.get("componentes_por_exclusion") or []) or "—",
                    "_no_cubiertos": "; ".join(r.get("hallazgos_no_cubiertos") or []) or "ninguno", "_motivo_exc": motivo}
        v = r.get("validacion_exclusion") or {}
        fila.update({"_validacion": v.get("estado") or "NO VERIFICADA", "_esperado_val": v.get("esperado") or fila["_definido"],
                     "_encontrado_val": v.get("encontrado") or "—", "_observacion_val": v.get("observacion") or "",
                     "_evidencia_val": ", ".join(v.get("evidencia") or []) or "—",
                     "_elementos_val": "; ".join(f"{x['elemento']}: {x['estado']}" + (f" — {x['encontrado']}" if x.get("encontrado") else "")
                                                 for x in v.get("elementos") or []) or "—",
                     "_advertencias_val": "; ".join(v.get("advertencias") or []),
                     "_elementos": list(v.get("elementos") or [])})
        res.append({**r, **fila})
    return res


def _advertencias(v: Dict) -> str:
    """Advertencias de la validación (naranja): recomendaciones no cumplidas que no impiden que la exclusión esté APLICADA."""
    return "".join(f'<div class="adv">⚠ {E(a)}</div>' for a in v.get("advertencias") or [])


def _marca(ok, advertencia=None) -> str:
    if advertencia:
        return '<span class="adv">⚠</span>'
    return '<span class="si">✓</span>' if ok else ('<span class="no">✗</span>' if ok is False else '<span class="nd">·</span>')


def _fila_exclusion(r: Dict) -> str:
    """Detalle de una exclusión definida: esperado frente a lo encontrado, validación en el servidor y evidencia técnica."""
    elems = "".join(f"<li{' class=adv' if x.get('advertencia') else ''}>{_marca(x.get('ok'), x.get('advertencia'))} <b>{E(x['elemento'])}</b>: {E(x['estado'])}"
                    + (f" — {E(x['encontrado'])}" if x.get("encontrado") else "")
                    + (f" <span class=adv>{E(x['advertencia'])}</span>" if x.get("advertencia") else "")
                    + (f" <small>(esperado: {E(x['esperado'])})</small>" if x.get("esperado") else "")
                    + (f" <small>{E(x['observacion'])}</small>" if x.get("observacion") else "") + "</li>" for x in r["_elementos"])
    elems_html = _plegable(f'<ul class="velem">{elems}</ul>', sum(len(str(x)) for x in r["_elementos"]) // 2) if elems else ""
    return (f'<tr class="excl" id="{ancla_exclusion(r)}"><td>{_tags(r)}</td>'
            f'<td><b>{E(r["control_excel"])}</b><br><small>CIS {E(r.get("control_cis"))}</small><br>'
            f'<a class="volver" href="#{ancla_control(r)}">↩ ir al control</a></td>'
            f'<td>{E(_descripcion(r))}</td>'
            f'<td class="ev">{_lista(r["_esperado_val"])}<br><small>Motivo: {E(r.get("_motivo_exc") or "—")}</small></td>'
            f'<td class="ev">{_lista(r["_encontrado_val"])}{elems_html}</td>'
            f'<td>{_badge(r["_validacion"])}'
            + ('<br><span class="badge adv">⚠ CON ADVERTENCIA</span>' if r["_advertencias_val"] else "") + '</td>'
            f'<td class="ev">{_advertencias(r.get("validacion_exclusion") or {})}{_texto_plegable(r["_observacion_val"] or "—")}</td></tr>')


def _opciones(valores) -> str:
    return "".join(f"<option>{E(v)}</option>" for v in sorted({v for v in valores if v}))


def generar(datos: Dict, ruta: Path, demo: bool = False) -> None:
    ind = indicadores_de(datos)
    uni = unicos_de(datos)
    res = sorted((partes.normalizar_fila(r) for r in datos["resultados"]), key=partes.orden_fila)
    g = ind["cis_global"]
    fin = (datos["resumen"].get("final") or {})
    fg = fin.get("global")
    hosts = datos["hosts"]
    cis = [r for r in res if r["tipo_control"] == partes.CIS]
    pre = [r for r in res if r["tipo_control"] == partes.PRERREQUISITO]
    pend = pendientes(cis)
    errores = [r for r in cis if _final(r) == "ERROR DE AUDITORÍA"]
    excl = filas_exclusion(cis)
    aviso = '<p class="aviso">DEMOSTRACIÓN — DATOS SINTÉTICOS. Este informe no corresponde a ningún servidor real.</p>' if demo else ""
    hay_p1 = any(r["parte_proyecto"] == partes.PARTE_1 for r in res)
    nota_p1 = "" if hay_p1 else '<p class="nota">Esta ejecución no incluye filas de la Parte 1 en alcance (la Parte 1 cubre Oracle Linux 8/9, RHEL 8/9 y SLES 15).</p>'
    fecha = next((r.get("fecha_evidencia") for r in cis if r.get("fecha_evidencia")), None) or datos.get("generado")
    resumen_host = ""
    for h in hosts:
        hc = [r for r in cis if r["host"] == h["host"]]
        kp = [("Host", f"{h['host']} ({h['sistema'].get('hostname') or '—'})"), ("Dirección IP", h["sistema"].get("ip") or "—"), ("Sistema operativo", h["sistema"].get("nombre_completo")),
              ("Benchmark", h.get("benchmark")), ("Fecha de la evidencia", fecha),
              ("Controles P1", sum(1 for r in hc if r["parte_proyecto"] == partes.PARTE_1)),
              ("Controles P2", sum(1 for r in hc if r["parte_proyecto"] == partes.PARTE_2)), ("Total CIS en alcance", len(hc))]
        resumen_host += "<table><tbody>" + "".join(f"<tr><th>{E(k)}</th><td>{E(v)}</td></tr>" for k, v in kp) + "</tbody></table>"
    kpis = [(_pct(g["porcentaje_cumplimiento"]), f"Cumplimiento CIS técnico ({g['conteo'].get('CUMPLE', 0)}/{g['evaluables']})"),
            (_pct(g["cobertura_tecnica"]), f"Cobertura técnica CIS ({g['evaluables']}/{g['filas_yes_auditadas']})")]
    if fg:
        kpis += [(_pct(fg["cumplimiento_final_linea_base"]), f"Cumplimiento final de línea base ({fg['conteo'].get('CUMPLE', 0)}/{fg['evaluables_final']})"),
                 (_pct(fg["cobertura_validacion"]), f"Cobertura de validación ({fg['concluidas']}/{fg['filas_yes']})")]
    kpis += [(sum(1 for r in cis if _final(r) == "NO CUMPLE"), "NO CUMPLE (resultado final)"), (len(pend), "Pendientes"),
             (sum(1 for r in cis if _final(r) in ("REQUIERE REMEDIACIÓN MANUAL", "REQUIERE AJUSTE MANUAL")), "Requieren acción manual"),
             (sum(1 for r in cis if _final(r) == "CONTROL EXCLUIDO"), "CONTROL EXCLUIDO"),
             (sum(1 for r in cis if _final(r) == "OMITIDO"), "OMITIDO"), (len(errores), "Errores de auditoría")]
    kpi_html = '<div class="kpis">' + "".join(f'<div class="kpi"><b>{E(v)}</b><span>{E(t)}</span></div>' for v, t in kpis) + "</div>"
    filtros = "".join(f'<label class=muted>{E(t)}<select id="{i}" onchange="filtrar()" aria-label="{E(t)}"><option value="">Todos</option>'
                      + (_opciones(["P1", "P2"]) if a == "parte" else
                         "".join(f"<option>{x}</option>" for x in ("CUMPLE", "NO CUMPLE", "PARCIAL", "REQUIERE REMEDIACIÓN MANUAL", "REQUIERE AJUSTE MANUAL", "NO APLICA",
                                                    "CONTROL EXCLUIDO", "OMITIDO", "PENDIENTE", "ERROR DE AUDITORÍA"))
                         if a == "estado" else '<option value="CON">EXCLUSIÓN DEFINIDA</option><option value="SIN">SIN EXCLUSIÓN</option>' if a == "exclusion"
                         else _opciones(r["host"] for r in cis) if a == "host" else _opciones(r.get("criticidad") for r in cis))
                      + '</select></label>' for i, a, t in _FILTROS)
    cab_ctl = ("<thead><tr><th>Parte</th><th>Control</th><th>Descripción</th><th>Criticidad</th><th>Resultado</th><th>Evidencia / Motivo</th>"
               "<th>Exclusión de Infraestructura</th><th>Detalle de exclusión</th><th>Más</th></tr></thead>")
    ex_rows = "".join(_fila_exclusion(r) for r in excl)
    from ..motor.validacion_exclusiones import ESTADOS as _EV
    ex_cuenta = {k: sum(1 for r in excl if r["_validacion"] == k) for k in _EV}
    ex_resumen = (f'<p><b>Exclusiones definidas: {len(excl)}</b> · ' + " · ".join(f"{_badge(k)} {n}" for k, n in ex_cuenta.items()) + "</p>")
    pend_rows = "".join(f"<tr><td>{_tags(r)}</td><td>{E(categoria_pendiente(r))}</td><td><b>{E(r['control_excel'])}</b><br><small>CIS {E(r.get('control_cis'))}</small></td>"
                        f"<td>{E(_descripcion(r))}</td><td>{_badge(_final(r))}</td><td class=\"ev\">{_lista(r.get('evidencia_motivo'), 8)}</td></tr>"
                        for r in sorted(pend, key=lambda r: (categoria_pendiente(r), partes.orden_fila(r))))
    err_rows = "".join(f"<tr><td>{_tags(r)}</td><td>{E(r['host'])}</td><td>{E(r['control_excel'])} / {E(r.get('control_cis'))}</td>"
                       f"<td>{E(_descripcion(r))}</td><td class=\"ev\">{_lista(r.get('evidencia_motivo'), 8)}</td></tr>" for r in errores)
    pre_rows = "".join(f"<tr><td>{_tags(r)}</td><td><b>{E(r['control_excel'])}</b></td><td>{E(r['titulo_excel'])}</td><td>{_badge(_final(r))}</td>"
                       f"<td class=\"pre\">{_texto_plegable(r.get('evidencia_motivo'))}</td><td>{E(r.get('fuente_evidencia') or '—')}</td></tr>" for r in pre)
    fuentes = "".join(f"<li>{E(c.get('titulo'))} {E(c.get('version'))} ({E(c.get('fecha'))}) — {E(c.get('archivo'))}</li>" for c in datos["fuentes_cis"])
    fuera = sorted((partes.normalizar_fila(f) for f in datos["fuera_de_alcance"]), key=partes.orden_fila)
    no_conf = datos.get("parte1_no_confirmada") or []
    tabla_hosts = "".join(
        f"<tr><td>{E(h['host'])}</td><td>{E(h['sistema'].get('hostname'))}</td><td>{E(h['sistema'].get('ip') or '—')}</td><td>{E(h['sistema'].get('nombre_completo'))}</td>"
        f"<td>{E(h['benchmark'])}</td><td>{E(((h['sistema'].get('recolector_privilegiado') or {}).get('estado')))}"
        f"{(' ' + E((h['sistema'].get('recolector_privilegiado') or {}).get('version'))) if (h['sistema'].get('recolector_privilegiado') or {}).get('version') else ''}"
        f"{_instr_host(h)}</td><td>{E('; '.join(h['errores']) or '—')}</td></tr>" for h in hosts)
    bloques_fin = ""
    if fin:
        def _bf(t, v):
            return (f'<div class="bloque"><h3>{t}</h3><div class="pct"><div><b>{_pct(v["cumplimiento_final_linea_base"])}</b>'
                    f'<span>Cumplimiento final de línea base ({v["conteo"].get("CUMPLE", 0)}/{v["evaluables_final"]})</span></div>'
                    f'<div><b>{_pct(v["cobertura_validacion"])}</b><span>Cobertura de validación ({v["concluidas"]}/{v["filas_yes"]})</span></div></div>'
                    f'{_barra(v["conteo"])}<table>' + "".join(f"<tr><td>{E(k)}</td><td>{E(n)}</td></tr>" for k, n in v["conteo"].items()) + "</table></div>")
        bloques_fin = ('<h3>Resultado final de auditoría (no es el porcentaje CIS técnico)</h3><div class="bloques">'
                       + _bf("FINAL — P1 + P2", fin["global"]) + _bf('<span class="tag tp1">[P1]</span> FINAL PARTE 1', fin["parte_1"])
                       + _bf('<span class="tag tp2">[P2]</span> FINAL PARTE 2', fin["parte_2"]) + f'</div><p class="formula">{E(fin["global"]["formula"])}</p>')
    unico = (f'<div class="bloque"><h3>Controles CIS únicos</h3><div class="pct"><div><b>{_pct(uni["porcentaje_cumplimiento"])}</b>'
             f'<span>Cumplimiento CIS técnico</span></div><div><b>{_pct(uni["cobertura_tecnica"])}</b><span>Cobertura técnica CIS</span></div></div>'
             f'<p class=muted>En alcance {uni.get("unicos_en_alcance", uni["total"])} · evaluables {uni.get("unicos_evaluables", uni["total"])} · '
             f'conflictos de baseline {uni.get("conflictos_baseline", 0)} (por host). Denominador propio.</p></div>')
    go = ind["global_operativo"]
    operativo = (f'<div class="bloque"><h3>Cumplimiento Global Operativo</h3><div class="pct"><div><b>{_pct(go["porcentaje_cumplimiento"])}</b>'
                 f'<span>CIS + prerrequisitos internos</span></div></div><p class="nota">Incluye prerrequisitos internos. <b>NO es el porcentaje CIS.</b></p></div>')
    doc = f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Auditoría Completa de Hardening CIS Linux</title><style>{CSS}</style></head><body>
<header><h1>Auditoría Completa de Hardening CIS Linux</h1><p>Parte 1 + Parte 2 · Ejecución {E(datos['run_id'])} · generado {E(datos['generado'])} · auditoría de solo lectura</p></header>
<main>{aviso}{nota_p1}
<section><h2>Resumen ejecutivo</h2>{resumen_host}{kpi_html}
<p class=muted>Cumplimiento CIS técnico y cobertura técnica CIS usan solo el resultado CIS técnico. El cumplimiento final de línea base incluye las
exclusiones de Infraestructura aprobadas; los CONTROL EXCLUIDO y OMITIDO quedan fuera de su denominador y nunca cuentan como CUMPLE; REQUIERE
REMEDIACIÓN MANUAL y REQUIERE AJUSTE MANUAL son incumplimientos con acción pendiente y cuentan como no cumplidos. Nunca se mezclan numeradores ni denominadores.
Los prerrequisitos internos de la Parte 1 se detallan en su propia sección.</p></section>
<section><h2>Controles</h2><div class="filtros"><label class=muted>Buscar<input id="q" placeholder="control, descripción, evidencia…" oninput="filtrar()" aria-label="Buscar"></label>{filtros}</div>
<p class=muted>Mostrando <span id="nvis">{len(cis)}</span> de {len(cis)} controles CIS.</p>
<div style="overflow-x:auto"><table class="tctl">{"".join(f'<col class="c{i}">' for i in range(1, 10))}{cab_ctl}<tbody>{''.join(_fila(r) for r in cis)}</tbody></table></div></section>
<section id="exclusiones"><h2>Exclusiones de Infraestructura</h2><p class=muted>Solo exclusiones realmente definidas. P1: columna "Exclusiones de Infraestructura" de la línea base Parte 1 (la columna Observacion no es una exclusión). P2: únicamente la columna EXCLUSIONES (Infraestructura Linux) de la línea base Parte 2.
Cada exclusión se valida contra la configuración real del servidor; que esté <b>Definida</b> no significa que esté aplicada. Las exclusiones de usuarios se comprueban usuario por usuario.</p>
{ex_resumen}<dl class="glos"><dt>APLICADA</dt><dd>Definida y confirmada técnicamente en el servidor.</dd><dt>NO APLICADA</dt><dd>Definida, pero la configuración real no corresponde a lo que declara.</dd><dt>NO VERIFICADA</dt><dd>Sin evidencia suficiente para confirmar su aplicación.</dd><dt>OMITIDO</dt><dd>Control omitido por decisión de Infraestructura (rds y tipc): no se clasifica como aplicada ni no aplicada.</dd><dt class="adv">⚠ CON ADVERTENCIA</dt><dd>Aplicada, pero un valor recomendado (no obligatorio) no se cumple; por ejemplo, la partición /var/log/audit por debajo de 15 GB.</dd></dl>
<div style="overflow-x:auto"><table class="texcl">{"".join(f'<col class="e{i}">' for i in range(1, 8))}<thead><tr><th>Parte</th><th>Control CIS relacionado</th><th>Descripción</th><th>Exclusión esperada</th><th>Configuración real encontrada</th><th>Resultado de validación</th><th>Observación</th></tr></thead><tbody>{ex_rows or '<tr><td colspan=7>Ninguna</td></tr>'}</tbody></table></div></section>
<section><h2>Pendientes</h2><p class=muted>Solo controles CIS sin conclusión.</p>
<div style="overflow-x:auto"><table><thead><tr><th>Parte</th><th>Clasificación</th><th>Control</th><th>Descripción</th><th>Resultado</th><th>Evidencia / Motivo</th></tr></thead><tbody>{pend_rows or '<tr><td colspan=6>Ninguno</td></tr>'}</tbody></table></div></section>
<section><h2><span class="tag tp1">[P1]</span><span class="tag tpre">[PRE]</span> Prerrequisitos Parte 1</h2><p class=muted>PRERREQUISITO INTERNO (no CIS). Criterio: config/prerrequisitos_parte1.yml. Fuera de los porcentajes CIS y del cumplimiento final de línea base.</p>
<div style="overflow-x:auto"><table><thead><tr><th>Parte</th><th>ID</th><th>Descripción</th><th>Resultado</th><th>Evidencia / Motivo</th><th>Fuente de evidencia</th></tr></thead><tbody>{pre_rows or '<tr><td colspan=6>Sin prerrequisitos en esta ejecución</td></tr>'}</tbody></table></div></section>
<section><h2>Errores de auditoría</h2><p class=muted>Problemas del proceso (permisos, timeouts, salida no interpretable). No son incumplimientos.</p>
<ul>{''.join(f"<li><b>{E(h)}</b>: {E('; '.join(v))}</li>" for h, v in datos['resumen']['errores_hosts'].items())}</ul>
<div style="overflow-x:auto"><table><thead><tr><th>Parte</th><th>Host</th><th>Control</th><th>Descripción</th><th>Motivo</th></tr></thead><tbody>{err_rows or '<tr><td colspan=5>Ninguno</td></tr>'}</tbody></table></div></section>
<section><h2>Indicadores detallados</h2><h3>CIS técnico (resultado_cis)</h3><div class="bloques">
{_bloque('GLOBAL CIS — P1 + P2', g, '<p class=muted>Solo controles CIS. No incluye prerrequisitos.</p>')}
{_bloque('<span class="tag tp1">[P1]</span> PARTE 1', ind['parte_1'], '<p class=muted>Fuente: línea base Parte 1.</p>')}
{_bloque('<span class="tag tp2">[P2]</span> PARTE 2', ind['parte_2'], '<p class=muted>Fuente: línea base Parte 2.</p>')}
{unico}{operativo}</div>{bloques_fin}
<p><b>Controles no evaluados por falta de privilegios: {g['no_evaluados_por_falta_de_privilegios']}</b> · Pendientes de auditoría profunda: {g['pendientes_auditoria_profunda']}</p>
<h3>Fórmulas</h3><p class="formula">{E(g['formula'])}</p><p class="formula">{E(uni['formula'])}</p>
{_tabla_resumen('Por Benchmark (P1 + P2, solo CIS técnico)', datos['resumen']['por_benchmark'])}{_tabla_resumen('Por servidor (P1 + P2, solo CIS técnico)', datos['resumen']['por_host'])}</section>
<section><h2>Alcance, fuentes y servidores</h2><ul><li>Parte 1: línea base Parte 1 (aplica a Oracle Linux 8/9, RHEL 8/9 y SLES 15).</li><li>Parte 2: línea base Parte 2.</li></ul><ul>{fuentes}</ul>
<div style="overflow-x:auto"><table><thead><tr><th>Host</th><th>Hostname</th><th>Dirección IP</th><th>Sistema</th><th>Benchmark</th><th>Recolector privilegiado</th><th>Errores</th></tr></thead><tbody>{tabla_hosts}</tbody></table></div>
<details><summary>Fuera de alcance, retirados y no confirmados: {len(fuera)} filas (trazabilidad; no afectan los porcentajes) · Parte 1 no confirmada: {len(no_conf)}</summary><table><thead><tr><th>Parte</th><th>Host</th><th>Control Excel</th><th>Título</th><th>CIS</th><th>Estado</th><th>Motivo</th></tr></thead><tbody>
{''.join(f"<tr><td>{_tags(f)}</td><td>{E(f['host'])}</td><td>{E(f['control_excel'])}</td><td>{E(f['titulo_excel'])}</td><td>{E(f.get('control_cis'))}</td><td>{_badge(pres.estado_fuera(f))}</td><td>{_motivo_fuera(f)}</td></tr>" for f in fuera)}</tbody></table></details>
<h3>Glosario: resultado final, exclusiones y tratamiento</h3><dl class="glos">{''.join(f"<dt>{E(k)}</dt><dd>{E(v)}</dd>" for k, v in pres.GLOSARIO_RESULTADO)}</dl>
<h3>Glosario: estados de alcance</h3><dl class="glos">{''.join(f"<dt>{E(k)}</dt><dd>{E(v)}</dd>" for k, v in pres.GLOSARIO_ALCANCE)}</dl>
<h3>Glosario: estados de mapeo Excel → CIS</h3><dl class="glos">{''.join(f"<dt>{E(k)}</dt><dd>{E(v)}</dd>" for k, v in pres.GLOSARIO_MAPEO)}</dl></section>
</main><script>{JS}</script></body></html>"""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(doc, encoding="utf-8")
