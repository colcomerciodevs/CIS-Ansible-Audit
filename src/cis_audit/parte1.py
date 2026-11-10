"""Parte 1: análisis offline de la línea base P1 (alcance, mapeo al Benchmark, prerrequisitos y solapamientos).

Lo ejecuta `python3 src/auditar.py preparar`. No se conecta a servidores. Genera:
    data/alcance/matriz_alcance_parte1.json / .xlsx

La Parte 1 llega en una sola hoja ("SLES15-ORA8-9"); cada fila se expande por escenario con los mismos perfiles de la
Parte 2 (RHEL_ORACLE_8, RHEL_ORACLE_9, SLES_15) y se mapea al Benchmark de cada sistema (config/mapeo_parte1.yml).
Los prerrequisitos internos (PRE-*) se definen en config/prerrequisitos_parte1.yml.
"""

import collections
import datetime as dt
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import openpyxl
import yaml

from . import cis_catalog, mapeo
from .excel_baseline import normalizar_criticidad
from .linea_base_p2 import NOMBRES_SO
from .matriz_xlsx import _hoja_tabla
from .motor import partes
from .normalizacion import compactar

RAIZ = Path(__file__).resolve().parents[2]

PERFIL = {"RHEL8": "RHEL_ORACLE_8", "ORACLE8": "RHEL_ORACLE_8", "RHEL9": "RHEL_ORACLE_9",
          "ORACLE9": "RHEL_ORACLE_9", "SLES15": "SLES_15"}
FAMILIA = {"ORACLE9": "v2", "RHEL9": "v2", "ORACLE8": "v4", "RHEL8": "v4", "SLES15": "sles"}
BENCHMARKS = ("ORACLE8", "RHEL8", "ORACLE9", "RHEL9", "SLES15")
# La hoja de la Parte 1 se llama "SLES15-ORA8-9" y es la única del documento. Decisión D-P1-01 (confirmada
# 2026-10-23): la misma hoja aplica también a RHEL 8/9 (RHEL 9 = Oracle Linux 9, RHEL 8 = Oracle Linux 8).
DECLARADOS_EN_HOJA = set(BENCHMARKS)

ESTADOS_MAPEO_P1 = {
    **mapeo.ESTADOS_MAPEO,
    "MAPEADO_DIVIDIDO": "La fila de la línea base corresponde a varios controles CIS actuales (todos se evalúan).",
    "NO_APLICA_PLATAFORMA": "El mecanismo no existe en esa plataforma (p. ej. authselect en SLES, timesyncd en OL/RHEL).",
}
MAPEADOS = {"MAPEADO", "MAPEADO_TITULO_ACTUALIZADO", "MAPEADO_CONSOLIDADO", "MAPEADO_DIVIDIDO"}

# Estado de alcance (texto compatible con la Parte 2) y código normalizado de la Parte 1.
ALCANCE = {
    "EN_ALCANCE": "EN ALCANCE",
    "NO_APLICA": "NO APLICA",
    "REQUIERE_CONFIRMACION": "REQUIERE CONFIRMACIÓN",
    "RETIRADO_EN_CIS_ACTUAL": "RETIRADO EN CIS ACTUAL",
    "REQUISITO_INTERNO": "REQUISITO INTERNO",
}

# ------------------------------------------------------------------ capacidad de evaluación por control CIS
# (patrón del título CIS vigente, dominio, evidencias, requiere_sudo, profunda, nota)
# Evidencias: id existente en motor/comandos.py (reutilizable) o "nuevo:<id>" (comando a construir).
CAPACIDADES: List[Tuple[str, str, Tuple[str, ...], bool, bool, Optional[str]]] = [
    (r"world writable", "Permisos de filesystem", ("fs.world_writable",), True, True,
     "Recorrido completo de filesystem: requiere la auditoría de filesystem profundo."),
    (r"kernel module", "Módulos de kernel", ("nuevo:kmod.modulos",), False, False,
     "modprobe -n -v / --showconfig, lsmod y /etc/modprobe.d (solo lectura; nunca modprobe sin -n)."),
    (r"/tmp is|/dev/shm is|separate partition|option set on", "Particiones y opciones de montaje",
     ("nuevo:fs.montajes", "sys.var_log_audit"), False, False, "findmnt -kn y /etc/fstab; systemctl is-enabled tmp.mount."),
    (r"client is not installed|sudo is installed", "Paquetes", ("pkg.rpm",), False, False, "rpm -q (ampliar la lista de paquetes)."),
    (r"mail transfer agents", "Servicios de red", ("nuevo:red.escucha",), False, False,
     "ss -lntu sin procesos: basta la dirección de escucha de los puertos 25/465/587."),
    (r"autofs|services are not in use|xinetd|X window server", "Servicios", ("pkg.rpm", "nuevo:svc.unidades"), False, False,
     "rpm -q + systemctl is-enabled/is-active (sin start/stop)."),
    (r"access to /etc/(motd|issue)", "Banners (permisos)", ("nuevo:perm.banner",), False, False, None),
    (r"/etc/motd is|/etc/issue(\.net)? is|message of the day|warning banner is configured properly", "Banners (contenido)",
     ("nuevo:banner.contenido",), False, False, "Contenido de motd/issue/issue.net (no sensible) y escapes \\m \\r \\s \\v."),
    (r"time synchronization|chrony|timesyncd", "Sincronización de tiempo", ("pkg.rpm", "nuevo:tiempo.sync"), False, False, None),
    (r"IPv6 status", "Red (IPv6)", ("nuevo:red.ipv6", "sysctl.runtime"), False, False, "Manual en CIS: evidencia + política del sitio."),
    (r"wireless", "Red (wireless)", ("nuevo:red.wireless",), False, False, None),
    (r"net\.ipv|forwarding is disabled|packet redirect|icmp|reverse path|source routed|suspicious packets|syn cookies|router advertisements",
     "Parámetros de red (sysctl)", ("sysctl.runtime", "sysctl.archivos"), True, False,
     "Runtime sin sudo (ampliar claves de sysctl.runtime); persistencia vía sysctl.archivos, que hoy solo filtra "
     "randomize_va_space/suid_dumpable dentro del recolector privilegiado 1.1.0."),
    (r"cron|access to at|at is restricted", "Cron / at", ("nuevo:cron.estado",), False, False, "stat de archivos y directorios; systemctl."),
    (r"sudo log file exists", "sudo", ("auditd.sudo_logfile",), True, False, "Ya capturado por el recolector privilegiado 1.1.0 (solo logfile=)."),
    (r"use pty|password for escalation|re-authentication|timestamp_timeout|sudo authentication timeout", "sudo",
     ("nuevo:sudo.defaults",), True, False, "sudoers es 0440 root; requiere extraer solo Defaults use_pty/timestamp_timeout y flags NOPASSWD/!authenticate."),
    (r"su command", "PAM (su)", ("nuevo:pam.su", "usr.group"), False, False, None),
    (r"authselect|pam_|pam modules|password (failed|unlock|history|length|complexity|number|same|maximum|dictionary|quality)",
     "PAM", ("nuevo:pam.config",), False, False, "authselect current, /etc/pam.d y /etc/security/*.conf (0644)."),
    (r"sshd_config|host key files", "SSH (permisos)", ("nuevo:ssh.permisos",), False, False, "stat (sin leer llaves)."),
    (r"^Ensure sshd ", "SSH (configuración efectiva)", ("nuevo:ssh.efectiva",), True, False,
     "sshd -T necesita root (carga llaves de host); incluir Match y archivos Include."),
    (r"strong password hashing algorithm", "Contraseñas (login.defs/PAM)", ("nuevo:login.defs", "nuevo:pam.config"), False, False, None),
    (r"password expiration|minimum password days|warning days|inactive password lock|last password change", "Contraseñas (envejecimiento)",
     ("nuevo:login.defs", "nuevo:shadow.envejecimiento"), True, False,
     "login.defs sin sudo; campos 3-7 de /etc/shadow por cuenta requieren root (nunca el hash)."),
    (r"are locked", "Cuentas", ("usr.passwd", "nuevo:shadow.estado_bloqueo"), True, False, "Estado de bloqueo (! / *) desde /etc/shadow: requiere root."),
    (r"GID 0|UID 0|valid login shell", "Cuentas", ("usr.passwd", "usr.group", "sys.shells", "sys.uid_min"), False, False, None),
    (r"shell timeout|default user umask", "Entorno de shell", ("nuevo:shell.perfiles",), False, False,
     "Lectura estática de /etc/profile*, /etc/bashrc y login.defs (nunca ejecutar perfiles, igual que R6)."),
]

# Encabezados EXACTOS de la línea base Parte 1. La exclusión de Infraestructura sale SOLO de su columna; la columna
# "Observacion" (condiciones "Aplicar si / Omitir si", comentarios técnicos) es una observación y NUNCA una exclusión.
_CAB_P1 = {"Sección": "seccion", "Punto a tratar": "punto", "Tipo ejecución": "tipo", "Información": "info", "Playbook": "playbook",
           "Análisis de Impacto - Aplicabilidad": "impacto", "Nivel de Riesgo (Prod)": "riesgo", "Observacion": "observacion",
           "Exclusiones de Infraestructura": "exclusion"}


def leer_parte1(ruta: Path) -> Dict:
    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    if len(wb.sheetnames) != 1:
        raise ValueError(f"Se esperaba una hoja en {ruta.name}: {wb.sheetnames}")
    ws = wb[wb.sheetnames[0]]
    filas, seccion = [], None
    cab = None
    idx: Dict[str, int] = {}
    for i, r in enumerate(ws.iter_rows(values_only=True), 1):
        if i == 1:
            cab = [compactar(c) for c in r]
            faltan = [h for h in _CAB_P1 if h not in cab]
            if faltan:
                raise ValueError(f"Encabezados inesperados en {ruta.name}: faltan {faltan} (encontrados {cab})")
            idx = {campo: cab.index(h) for h, campo in _CAB_P1.items()}
            continue
        if not any(r):
            continue
        r = list(r) + [None] * len(cab)
        v = {campo: r[j] for campo, j in idx.items()}
        seccion = compactar(v["seccion"]) or seccion
        punto = v["punto"]
        # Un valor no textual (p. ej. el número 0 de la fila 6) no es un título: se conserva el original.
        punto_txt = compactar(punto) if isinstance(punto, str) else None
        exc, obs = v["exclusion"], v["observacion"]
        filas.append({"fila_excel": i, "seccion_excel": seccion, "punto_excel": punto_txt,
                      "punto_excel_original": None if isinstance(punto, str) or punto is None else repr(punto),
                      "tipo_ejecucion": compactar(v["tipo"]), "informacion": compactar(v["info"]), "playbook": compactar(v["playbook"]) or None,
                      "analisis_impacto": compactar(v["impacto"]), "nivel_riesgo_prod": compactar(v["riesgo"]) or None,
                      "observacion": (str(obs).strip() if obs and str(obs).strip() else None),
                      "exclusion_infraestructura": (str(exc).strip() if exc and str(exc).strip() else None)})
    return {"archivo": ruta.name, "hoja": ws.title, "encabezados": cab, "filas": filas}


def clasificar_exclusion_p1(texto: Optional[str]) -> Dict:
    """Columna "Exclusiones de Infraestructura" del Excel P1 (fuente canónica). Formato estructurado obligatorio
    (cis_audit.excepciones_p1.parsear_celda): una celda no vacía que no lo cumpla aborta la construcción (fail-closed).
    La columna "Observacion" (Aplicar si / Omitir si) nunca se interpreta aquí."""
    if not texto:
        return {"tipo_exclusion": "NINGUNA", "verificacion_exclusion_propuesta": None, "evidencia_organizacional_requerida": None}
    from .excepciones_p1 import parsear_celda
    from .excepciones_p1 import resumen as resumen_excl
    e = parsear_celda(texto)
    return {"tipo_exclusion": "EXCEPCION_INFRAESTRUCTURA", "excepcion_estructurada": e, "exclusion_infraestructura": resumen_excl(e),
            "verificacion_exclusion_propuesta": "Capa de resultado final: solo cubre los elementos documentados (fail-closed).",
            "evidencia_organizacional_requerida": None}


def capacidad(titulo: str) -> Dict:
    for patron, dominio, evid, sudo, prof, nota in CAPACIDADES:
        if re.search(patron, titulo, re.I):
            return {"dominio": dominio, "evidencias": list(evid), "requiere_sudo": sudo, "profunda": prof, "nota": nota}
    raise ValueError(f"Control CIS sin regla de capacidad (añadir a CAPACIDADES): {titulo}")


def _capacidad_fila(recs: List[Dict]) -> Dict:
    """Capacidad real según el registro de controles implementado."""
    from .motor import controles_p1 as cp1
    from .motor.comandos import COMANDOS
    from .motor.controles import POR_TITULO
    caps = [capacidad(r["titulo"]) for r in recs]
    ctls = [POR_TITULO.get(r["titulo"]) for r in recs]
    cmds = list(dict.fromkeys(c for ct in ctls if ct for c in ct.comandos))
    ids = {ct.id for ct in ctls if ct}
    ext = bool(ids & cp1.EXTENSION_P1)
    profunda = any(COMANDOS[c].profunda for c in cmds)
    cubierto = bool(ids & cp1.CUBIERTO_1_1_0)
    return {
        "dominio_validacion": " / ".join(dict.fromkeys(c["dominio"] for c in caps)),
        "motor_evalua_hoy": all(ct is not None for ct in ctls),
        "controles_motor": sorted(ids),
        "reutiliza_evaluador_parte2": sorted(i for i in ids if not i.startswith("p1_")),
        "evidencias_reutilizables": [c for c in cmds if not c.startswith("p1.")],
        "evidencias_nuevas": [c for c in cmds if c.startswith("p1.")],
        "requiere_sudo": ext or cubierto or profunda,
        "cubierto_recolector_1_1_0": cubierto,
        "requiere_extension_recolector": ext,
        "extension_condicional": bool(ids & cp1.EXTENSION_CONDICIONAL_P1),
        "requiere_filesystem_profundo": profunda,
        "manual_cis": any(r["assessment"] == "Manual" for r in recs) or bool(ids & cp1.MANUAL_P1),
        "notas_capacidad": [c["nota"] for c in caps if c["nota"]],
    }


def _verificar_mapeo(cfg: Dict, catalogos: Dict[str, Dict]) -> None:
    idx = {b: {r["id"]: r for r in c["recomendaciones"]} for b, c in catalogos.items()}
    for fila, m in cfg.items():
        for b in BENCHMARKS:
            fam = FAMILIA[b]
            estado = (m.get("estado_familia") or {}).get(fam, m["estado"])
            ids = [str(i) for i in (m.get("ids") or {}).get(fam, [])]
            if estado in MAPEADOS and not ids:
                raise ValueError(f"F{fila} {b}: estado {estado} sin ids")
            if estado not in MAPEADOS and ids and estado != "NO_APLICA_PLATAFORMA":
                raise ValueError(f"F{fila} {b}: estado {estado} con ids")
            if estado not in MAPEADOS:
                continue
            for i in ids:
                rec = idx[b].get(i)
                if not rec:
                    raise ValueError(f"F{fila} {b}: el control {i} no existe en {catalogos[b]['archivo']}")
                if not re.search(m["patron"], rec["titulo"], re.I):
                    raise ValueError(f"F{fila} {b}: {i} '{rec['titulo']}' no corresponde al patrón '{m['patron']}'")


def construir(fuentes: Dict) -> Dict:
    p1 = leer_parte1(fuentes["parte_1"])
    cfg = yaml.safe_load((RAIZ / "config" / "mapeo_parte1.yml").read_text(encoding="utf-8"))["filas"]
    pre = yaml.safe_load((RAIZ / "config" / "prerrequisitos_parte1.yml").read_text(encoding="utf-8"))["prerrequisitos"]
    catalogos = cis_catalog.cargar_o_construir(RAIZ / "input" / "cis", RAIZ / "data" / "cis_catalog")
    idx = {b: {r["id"]: r for r in c["recomendaciones"]} for b, c in catalogos.items()}
    filas_excel = {f["fila_excel"]: f for f in p1["filas"]}
    if set(cfg) != set(filas_excel):
        raise ValueError(f"config/mapeo_parte1.yml no cubre exactamente las filas del Excel: "
                         f"faltan {sorted(set(filas_excel) - set(cfg))}, sobran {sorted(set(cfg) - set(filas_excel))}")
    _verificar_mapeo(cfg, catalogos)

    matriz: List[Dict] = []
    for nfila, f in sorted(filas_excel.items()):
        m = cfg[nfila]
        control = m["control"]
        titulo = f["punto_excel"] or m.get("titulo_reconstruido")
        if f["punto_excel"] and not f["punto_excel"].startswith(control + " "):
            raise ValueError(f"F{nfila}: 'Punto a tratar' no empieza por {control}: {f['punto_excel'][:40]}")
        excl = clasificar_exclusion_p1(f["exclusion_infraestructura"])
        for b in BENCHMARKS:
            fam = FAMILIA[b]
            estado_mapeo = (m.get("estado_familia") or {}).get(fam, m["estado"])
            ids = [str(i) for i in (m.get("ids") or {}).get(fam, [])] if estado_mapeo in MAPEADOS else []
            recs = [idx[b][i] for i in ids]
            if estado_mapeo == "RETIRADO":
                codigo = "RETIRADO_EN_CIS_ACTUAL"
            elif estado_mapeo == "NO_APLICA_PLATAFORMA":
                codigo = "NO_APLICA"
            elif b not in DECLARADOS_EN_HOJA:
                codigo = "REQUIERE_CONFIRMACION"
            elif not f["playbook"]:
                codigo = "REQUIERE_CONFIRMACION"
            else:
                codigo = "EN_ALCANCE"
            ausencia = None
            if estado_mapeo == "RETIRADO":
                cand = mapeo.mejores_por_titulo(idx[b], titulo, 2)
                ausencia = "; ".join(f"{i} '{t}' (similitud {s:.2f}, no equivalente)" for s, i, t in cand)
            motivos = []
            if codigo == "REQUIERE_CONFIRMACION" and b not in DECLARADOS_EN_HOJA:
                motivos.append("La hoja de la Parte 1 ('SLES15-ORA8-9') no declara RHEL; confirmar si la Parte 1 aplica a RHEL 8/9 (D-P1-01).")
            if codigo == "REQUIERE_CONFIRMACION" and not f["playbook"]:
                motivos.append("La fila no tiene playbook asociado: no consta implementación del proveedor (D-P1-03).")
            fila = {
                "id_matriz": f"P1|{PERFIL[b]}|{b}|F{nfila:03d}",
                "parte_proyecto": partes.PARTE_1, "tipo_control": partes.CIS, "fuente_linea_base": p1["archivo"],
                "perfil_auditoria": PERFIL[b], "benchmark": b, "sistema_operativo": NOMBRES_SO[b], "familia_benchmark": fam,
                "hoja_excel": p1["hoja"], "fila_excel": nfila, "seccion_excel": f["seccion_excel"],
                "control_excel": control, "titulo_excel": titulo, "titulo_excel_vacio": not f["punto_excel"],
                "valor_original_punto_excel": f["punto_excel_original"],
                "tipo_ejecucion": f["tipo_ejecucion"], "playbook": f["playbook"], "informacion_excel": f["informacion"],
                "analisis_impacto": f["analisis_impacto"],
                "nivel_riesgo_prod": f["nivel_riesgo_prod"],
                "riesgo_normalizado": normalizar_criticidad(f["nivel_riesgo_prod"]) if f["nivel_riesgo_prod"] else None,
                "clasificacion_implementacion": ("REQUISITO_DE_INSTALACION" if "instalaci" in (f["tipo_ejecucion"] or "").lower()
                                                 else "POST_INSTALACION_ANSIBLE"),
                "estado_alcance": ALCANCE[codigo], "codigo_alcance_p1": codigo, "motivos_alcance": motivos,
                "estado_mapeo": estado_mapeo, "controles_cis": ids, "control_cis": ids[0] if ids else None,
                "titulos_cis": [r["titulo"] for r in recs], "titulo_cis": recs[0]["titulo"] if recs else None,
                "assessment_cis": sorted({r["assessment"] for r in recs}), "paginas_cis": [r["pagina"] for r in recs],
                "version_cis": catalogos[b]["version"], "fuente_cis": catalogos[b]["archivo"],
                "cambio_numeracion": bool(ids) and ids != [control],
                "nota_mapeo": m.get("nota"), "criterio_excel": m.get("criterio_excel"),
                "verificacion_ausencia": ausencia,
                # Observación (condiciones "Aplicar si / Omitir si", comentarios) y exclusión son conceptos distintos:
                # ninguno puebla al otro. La exclusión P1 solo procede de la columna "Exclusiones de Infraestructura".
                "observacion": f["observacion"],
                **{"exclusion_infraestructura": None, **excl},
                **(_capacidad_fila(recs) if recs else {
                    "dominio_validacion": None, "motor_evalua_hoy": False, "controles_motor": [], "reutiliza_evaluador_parte2": [],
                    "extension_condicional": False,
                    "evidencias_reutilizables": [], "evidencias_nuevas": [], "requiere_sudo": False,
                    "cubierto_recolector_1_1_0": False, "requiere_extension_recolector": False,
                    "requiere_filesystem_profundo": False, "manual_cis": False, "notas_capacidad": []}),
                "resultado_cis": None, "resultado_exclusion": None,
            }
            fila["evidence_ids"] = [partes.evidence_id(b, e) for e in fila["evidencias_reutilizables"] + fila["evidencias_nuevas"]]
            matriz.append(fila)

    prerrequisitos = []
    for pid, p in pre.items():
        for b in BENCHMARKS:
            codigo = "REQUISITO_INTERNO"
            prerrequisitos.append({
                "id_matriz": f"P1|{PERFIL[b]}|{b}|{pid}",
                "parte_proyecto": partes.PARTE_1, "tipo_control": partes.PRERREQUISITO,
                "fuente_requisito": partes.FUENTE_PRE, "origen_requisito": partes.REQUISITO_INTERNO,
                "perfil_auditoria": PERFIL[b], "benchmark": b, "sistema_operativo": NOMBRES_SO[b],
                "control_excel": pid, "titulo_excel": p["descripcion"], "seccion_excel": "Prerrequisitos internos",
                "estado_alcance": ALCANCE[codigo], "codigo_alcance_p1": codigo, "objetivo": p.get("objetivo"),
                "criterio": p["criterio"], "requiere_sudo": p["requiere_sudo"], "requiere_filesystem_profundo": False,
                "sensibilidad_evidencia": p.get("sensibilidad"), "nota": p.get("nota"), "resultado_cis": None,
            })

    p2 = json.loads((RAIZ / "data" / "alcance" / "matriz_alcance.json").read_text(encoding="utf-8"))
    solap = solapamientos(matriz, [partes.normalizar_fila(x) for x in p2["matriz"]])
    return {
        "generado": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "descripcion": "Matriz de alcance de la Parte 1 (análisis, alcance y mapeo; sin evidencia de servidores)",
        "fuentes": {
            "parte_1": {"archivo": p1["archivo"], "hoja": p1["hoja"], "filas": len(p1["filas"]),
                        "sha256": cis_catalog.sha256(fuentes["parte_1"])},
            "parte_2_matriz": {"archivo": "data/alcance/matriz_alcance.json", "generado": p2["generado"],
                               "nota": "No se modifica; se lee solo para solapamientos."},
            "cis": [{k: c[k] for k in ("clave", "archivo", "version", "fecha")} for c in catalogos.values()],
        },
        "estados_mapeo": ESTADOS_MAPEO_P1,
        "estados_alcance": ALCANCE,
        "resumen": resumen(matriz, prerrequisitos, solap),
        "matriz": matriz,
        "prerrequisitos": prerrequisitos,
        "_solapamientos": solap,
    }


def _evidencias_p2(fila: Dict) -> List[str]:
    from .motor.controles import control_para_titulo
    c = control_para_titulo(fila.get("titulo_cis") or "")
    return list(c.comandos) if c else []


RELACIONES_MANUALES = [
    # (control Parte 1, control CIS Parte 2 (cualquier benchmark), relación, nota)
    ("1.1.32", ("1.8.4", "1.8.6"), "RELACIONADO",
     "Medios removibles (P1, retirado) vs GDM automount (P2): objetivo similar, controles distintos."),
    ("1.1.33", ("1.8.4", "1.8.6"), "RELACIONADO", "Ídem 1.1.32."),
    ("1.1.34", ("1.8.4", "1.8.6"), "RELACIONADO", "Ídem 1.1.32."),
    ("2.1.5", ("1.8.1", "2.1.21"), "RELACIONADO",
     "X Window server (P1) vs GNOME Display Manager removido (P2): ambos usan rpm -q; criterios distintos."),
    ("5.5.7", ("5.4.2.1",), "PARCIAL",
     "GID 0 (P1, 5.4.2.2) vs UID 0 (P2, 5.4.2.1): misma evidencia /etc/passwd, requisito distinto."),
    ("5.2.3", ("6.3.3.3",), "EVIDENCIA_COMPARTIDA",
     "sudo log file exists (P1) vs auditoría de cambios al log de sudo (P2): misma evidencia auditd.sudo_logfile."),
]


# Comandos genéricos (una sola ejecución alimenta muchos controles de ambas partes): se reutilizan,
# pero no se listan como pares de solapamiento para no ocultar las relaciones relevantes.
GENERICOS = {"pkg.rpm", "sys.shells", "sys.uid_min", "usr.group"}


def solapamientos(p1: List[Dict], p2: List[Dict]) -> Dict:
    por_bench = collections.defaultdict(list)
    for f in p2:
        por_bench[f["benchmark"]].append(f)
    pares = []
    for f in p1:
        for g in por_bench[f["benchmark"]]:
            rel, nota = None, None
            if g["control_cis"] and g["control_cis"] in f["controles_cis"]:
                rel = "MISMO_CONTROL_CIS"
            else:
                for c1, c2s, r, n in RELACIONES_MANUALES:
                    if f["control_excel"] == c1 and g["control_cis"] in c2s:
                        rel, nota = r, n
                if not rel and f["control_excel"] in ("1.1.25", "1.1.26", "1.1.27", "1.1.28") and \
                        "AUDITD_PARTICION_15GB" in (g.get("reglas_exclusion") or []):
                    rel, nota = "EVIDENCIA_COMPARTIDA", ("Partición /var/log/audit (P1) y exclusión de Infraestructura "
                                                          "'partición /var/log/audit >= 15 GB' (P2): misma evidencia findmnt (sys.var_log_audit).")
                if not rel and (set(f["evidencias_reutilizables"]) & set(_evidencias_p2(g))) - GENERICOS:
                    comunes = sorted((set(f["evidencias_reutilizables"]) & set(_evidencias_p2(g))) - GENERICOS)
                    rel, nota = "EVIDENCIA_COMPARTIDA", "Comandos comunes: " + ", ".join(comunes)
            if not rel:
                continue
            excl_dist = (compactar(f.get("exclusion_infraestructura")) or "") != (compactar(g.get("exclusion_infraestructura")) or "")
            pares.append({
                "relacion": rel, "benchmark": f["benchmark"], "perfil_auditoria": f["perfil_auditoria"],
                "parte_1": {"id_matriz": f["id_matriz"], "control_excel": f["control_excel"], "controles_cis": f["controles_cis"],
                            "estado_alcance": f["estado_alcance"], "exclusion": f.get("exclusion_infraestructura")},
                "parte_2": {"id_matriz": g["id_matriz"], "control_excel": g["control_excel"], "control_cis": g["control_cis"],
                            "estado_alcance": g["estado_alcance"], "exclusion": g.get("exclusion_infraestructura")},
                "evidencia_compartida": (sorted((set(f["evidencias_reutilizables"]) & set(_evidencias_p2(g))) - GENERICOS) or
                                         (["fs.world_writable"] if rel == "MISMO_CONTROL_CIS" and "7.1.11" in f["controles_cis"] else []) or
                                         (["sys.var_log_audit"] if "sys.var_log_audit" in (nota or "") else [])),
                "exclusiones_distintas": excl_dist,
                "criterios_diferentes": bool(f.get("criterio_excel")),
                "nota": nota,
            })
    internos = []
    p1_por_clave = collections.defaultdict(list)
    for f in p1:
        for c in f["controles_cis"]:
            p1_por_clave[(f["benchmark"], c)].append(f["id_matriz"])
    for (b, c), ids in sorted(p1_por_clave.items()):
        if len(ids) > 1:
            internos.append({"benchmark": b, "control_cis": c, "filas_parte_1": ids,
                             "nota": "Varias filas de la Parte 1 mapean al mismo control CIS: se conservan todas y comparten evidencia."})
    genericos = {}
    for e in sorted(GENERICOS):
        genericos[e] = {"filas_parte_1": len([f for f in p1 if e in f["evidencias_reutilizables"]]),
                        "filas_parte_2": len([g for g in p2 if e in _evidencias_p2(g)])}
    return {"pares_parte1_parte2": pares, "duplicados_internos_parte1": internos,
            "comandos_genericos_compartidos": genericos}


def resumen(matriz: List[Dict], pre: List[Dict], solap: Dict) -> Dict:
    c = collections.Counter
    por_fila = {}
    for f in matriz:
        por_fila.setdefault(f["fila_excel"], []).append(f)
    declarados = [f for f in matriz if f["benchmark"] in DECLARADOS_EN_HOJA]
    en = [f for f in matriz if f["estado_alcance"] == "EN ALCANCE"]
    unicos = {}
    for b in BENCHMARKS:
        unicos[b] = sorted({x for f in en if f["benchmark"] == b for x in f["controles_cis"]})
    return {
        "filas_excel_parte1": len(por_fila),
        "secciones": list(dict.fromkeys(f["seccion_excel"] for f in matriz)),
        "filas_matriz": len(matriz),
        "prerrequisitos": sorted({p["control_excel"] for p in pre}),
        "alcance_por_benchmark": {b: dict(c(f["codigo_alcance_p1"] for f in matriz if f["benchmark"] == b)) for b in BENCHMARKS},
        "mapeo_por_benchmark": {b: dict(c(f["estado_mapeo"] for f in matriz if f["benchmark"] == b)) for b in BENCHMARKS},
        "controles_cis_unicos_en_alcance": {b: len(v) for b, v in unicos.items()},
        "en_alcance_capacidad": {
            b: {
                "filas": len([f for f in en if f["benchmark"] == b]),
                "motor_evalua_hoy": len([f for f in en if f["benchmark"] == b and f["motor_evalua_hoy"]]),
                "sin_sudo": len([f for f in en if f["benchmark"] == b and not f["requiere_sudo"]]),
                "sudo_cubierto_1_1_0": len([f for f in en if f["benchmark"] == b and f["cubierto_recolector_1_1_0"]]),
                "requiere_extension_recolector": len([f for f in en if f["benchmark"] == b and f["requiere_extension_recolector"]]),
                "filesystem_profundo": len([f for f in en if f["benchmark"] == b and f["requiere_filesystem_profundo"]]),
                "manual_cis": len([f for f in en if f["benchmark"] == b and f["manual_cis"]]),
            } for b in BENCHMARKS},
        "solapamientos": dict(c(p["relacion"] for p in solap["pares_parte1_parte2"])),
        "duplicados_internos_parte1": len(solap["duplicados_internos_parte1"]),
        "filas_declaradas_en_hoja": len(declarados),
    }


COLUMNAS = [
    ("id_matriz", "Id matriz"), ("parte_proyecto", "Parte Proyecto"), ("tipo_control", "Tipo control"),
    ("perfil_auditoria", "Perfil"), ("benchmark", "Benchmark"), ("fila_excel", "Fila Excel"),
    ("seccion_excel", "Sección"), ("control_excel", "Control Excel"), ("titulo_excel", "Título Excel"),
    ("tipo_ejecucion", "Tipo ejecución"), ("playbook", "Playbook"), ("nivel_riesgo_prod", "Riesgo (Prod)"),
    ("clasificacion_implementacion", "Implementación"), ("estado_alcance", "Estado alcance"),
    ("motivos_alcance", "Motivos alcance"), ("estado_mapeo", "Estado mapeo"), ("controles_cis", "Controles CIS"),
    ("titulos_cis", "Título CIS"), ("assessment_cis", "Assessment"), ("version_cis", "Versión CIS"),
    ("nota_mapeo", "Justificación mapeo"), ("criterio_excel", "Criterio Excel"),
    ("exclusion_infraestructura", "Exclusión Infraestructura"), ("tipo_exclusion", "Tipo exclusión"),
    ("dominio_validacion", "Dominio"), ("motor_evalua_hoy", "Motor evalúa hoy"),
    ("evidencias_reutilizables", "Evidencia reutilizable"), ("evidencias_nuevas", "Evidencia nueva"),
    ("requiere_sudo", "Requiere sudo"), ("cubierto_recolector_1_1_0", "Cubierto recolector 1.1.0"),
    ("requiere_extension_recolector", "REQUIERE_EXTENSION_RECOLECTOR"), ("requiere_filesystem_profundo", "FS profundo"),
    ("manual_cis", "Manual CIS"), ("verificacion_ausencia", "Verificación ausencia"),
]
COLUMNAS_PRE = [
    ("id_matriz", "Id matriz"), ("parte_proyecto", "Parte Proyecto"), ("tipo_control", "Tipo control"),
    ("benchmark", "Benchmark"), ("control_excel", "Id"), ("titulo_excel", "Descripción"),
    ("origen_requisito", "Origen"), ("fuente_requisito", "Fuente"), ("estado_alcance", "Estado alcance"),
    ("objetivo", "Objetivo"), ("criterio", "Criterio"), ("requiere_sudo", "Requiere sudo"),
    ("sensibilidad_evidencia", "Sensibilidad"), ("nota", "Nota"),
]


def _plano(v):
    if isinstance(v, list):
        return " | ".join(str(x) for x in v)
    if isinstance(v, bool):
        return "SÍ" if v else "NO"
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return v


def escribir_xlsx(datos: Dict, ruta: Path) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    r = datos["resumen"]
    ws.append(["Matriz de alcance — Parte 1 (PARTE_1)", datos["generado"]])
    ws.append(["Fuente", datos["fuentes"]["parte_1"]["archivo"]])
    ws.append(["Filas Excel Parte 1", r["filas_excel_parte1"]])
    ws.append(["Prerrequisitos", ", ".join(r["prerrequisitos"])])
    ws.append([])
    ws.append(["Benchmark"] + list(ALCANCE) + ["Controles CIS únicos en alcance"])
    for b in BENCHMARKS:
        ws.append([b] + [r["alcance_por_benchmark"][b].get(k, 0) for k in ALCANCE] + [r["controles_cis_unicos_en_alcance"][b]])
    ws.column_dimensions["A"].width = 30
    for perfil in ("RHEL_ORACLE_8", "RHEL_ORACLE_9", "SLES_15"):
        _hoja_tabla(wb, f"Parte1_{perfil}", COLUMNAS, [f for f in datos["matriz"] if f["perfil_auditoria"] == perfil], _plano)
    _hoja_tabla(wb, "Parte1_Prerequisitos", COLUMNAS_PRE, datos["prerrequisitos"], _plano)
    sol = [{"relacion": p["relacion"], "benchmark": p["benchmark"], "p1": p["parte_1"]["id_matriz"],
            "p1_control": p["parte_1"]["control_excel"], "p2": p["parte_2"]["id_matriz"], "p2_control": p["parte_2"]["control_excel"],
            "cis": p["parte_2"]["control_cis"], "evid": p["evidencia_compartida"], "excl": p["exclusiones_distintas"], "nota": p["nota"]}
           for p in datos["_solapamientos"]["pares_parte1_parte2"]]
    _hoja_tabla(wb, "Solapamientos_P1_P2", [("relacion", "Relación"), ("benchmark", "Benchmark"), ("p1", "Id P1"),
                                             ("p1_control", "Control P1"), ("p2", "Id P2"), ("p2_control", "Control P2"),
                                             ("cis", "Control CIS P2"), ("evid", "Evidencia compartida"),
                                             ("excl", "Exclusiones distintas"), ("nota", "Nota")], sol, _plano)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    wb.save(ruta)


def main(argv=None) -> int:
    cfg = yaml.safe_load((RAIZ / "config" / "audit.yml").read_text(encoding="utf-8"))
    pp = cfg["partes_proyecto"]
    fuentes = {"parte_1": RAIZ / pp["PARTE_1"]["linea_base"]}
    datos = construir(fuentes)
    salida = {k: v for k, v in datos.items() if k != "_solapamientos"}
    (RAIZ / "data" / "alcance").mkdir(parents=True, exist_ok=True)
    (RAIZ / "data" / "alcance" / "matriz_alcance_parte1.json").write_text(
        json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    escribir_xlsx(datos, RAIZ / "data" / "alcance" / "matriz_alcance_parte1.xlsx")
    print(json.dumps(datos["resumen"], ensure_ascii=False, indent=1))
    return 0
