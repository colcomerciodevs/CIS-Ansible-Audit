"""Parte 2: análisis del Excel P2, catálogo CIS (PDF oficiales), alcance, mapeo y matriz de alcance.

Lo ejecuta `python3 src/auditar.py preparar`. No se conecta a servidores ni ejecuta comandos remotos. No modifica las
fuentes. Genera data/alcance/matriz_alcance.{json,csv,xlsx}.
"""

import argparse
import collections
import csv
import datetime as dt
import difflib
import json
import re
from pathlib import Path
from typing import Dict, List

import yaml

from . import cis_catalog, excel_baseline, exclusiones, mapeo, validacion
from .normalizacion import compactar, normalizar_propuesta

RAIZ = Path(__file__).resolve().parents[2]

NOMBRES_SO = {
    "RHEL8": "Red Hat Enterprise Linux 8",
    "ORACLE8": "Oracle Linux 8",
    "RHEL9": "Red Hat Enterprise Linux 9",
    "ORACLE9": "Oracle Linux 9",
    "SLES15": "SUSE Linux Enterprise Server 15",
}

# Pares de controles CIS mutuamente excluyentes según el propio Benchmark
# (sección "System Logging": journald y rsyslog son alternativas; "only one is implemented").
CONFLICTOS_TITULO = [
    (
        "journald forwardtosyslog is disabled",
        "journald is configured to send logs to rsyslog",
        "El Benchmark trata journald y rsyslog como métodos alternativos ('only one is implemented'). "
        "ForwardToSyslog deshabilitado (vía journald) y envío de journald a rsyslog (vía rsyslog) no "
        "pueden cumplirse simultáneamente.",
    ),
    (
        "systemd-journal-upload is enabled and active",
        "rsyslog is configured to send logs to a remote log host",
        "Ambos controles implementan el envío remoto de logs por vías distintas (journald vs rsyslog). "
        "El Benchmark indica implementar solo un método; se debe definir cuál es el oficial.",
    ),
]


def _cargar_config() -> Dict:
    with open(RAIZ / "config" / "audit.yml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _cargar_revisiones() -> Dict:
    ruta = RAIZ / "config" / "revisiones_mapeo.yml"
    if not ruta.exists():
        return {}
    with open(ruta, encoding="utf-8") as f:
        datos = yaml.safe_load(f) or {}
    idx = {}
    for r in datos.get("revisiones", []):
        clave = (r["hoja"], int(r["fila"]))
        if clave in idx:
            raise ValueError(f"Revisión duplicada para {clave}")
        idx[clave] = r
    return idx


def construir(args) -> Dict:
    cfg = _cargar_config()
    rutas = cfg["paths"]
    positivos = cfg["scope"]["provider_positive_values"]
    negativos = cfg["scope"]["provider_negative_values"]

    catalogos = cis_catalog.cargar_o_construir(
        RAIZ / rutas["cis"], RAIZ / "data" / "cis_catalog", forzar=args.reconstruir_catalogo
    )
    indices = {k: {r["id"]: r for r in c["recomendaciones"]} for k, c in catalogos.items()}
    excel_path = excel_baseline.localizar_excel(
        RAIZ / rutas["baseline"], ((cfg.get("partes_proyecto") or {}).get("PARTE_2") or {}).get("linea_base"))
    excel = excel_baseline.leer_linea_base(excel_path)
    revisiones = _cargar_revisiones()
    usadas = set()

    filas_matriz: List[Dict] = []
    for hoja in excel["hojas"]:
        for f in hoja["filas"]:
            prop = normalizar_propuesta(f["propuesta_proveedor"], positivos, negativos)
            estado_alcance = {
                "AFIRMATIVA": "EN ALCANCE",
                "NEGATIVA": "FUERA DE ALCANCE",
            }.get(prop, "INDETERMINADO")
            excl = exclusiones.clasificar(f["exclusion_infraestructura"])
            for bench in hoja["benchmarks"]:
                if bench not in catalogos:
                    raise FileNotFoundError(f"Falta el Benchmark {bench} en input/cis")
                cat = catalogos[bench]
                idx = indices[bench]
                res = mapeo.mapear_automatico(f, idx)
                rev = revisiones.get((hoja["hoja"], f["fila_excel"]))
                if rev and rev.get("benchmarks") and bench not in rev["benchmarks"]:
                    rev = None
                if rev:
                    usadas.add((hoja["hoja"], f["fila_excel"]))
                res = mapeo.aplicar_revision(res, rev, idx, bench, cat["version"])
                rec = idx.get(res["control_cis"]) if res["control_cis"] else None
                val = validacion.clasificar(rec["titulo"] if rec else None, rec["assessment"] if rec else None)
                if estado_alcance != "EN ALCANCE":
                    val["tipo_validacion"] = "NO APLICA (fuera de alcance)" if rec else None

                control_excel = compactar(f["control_excel"])
                fila = {
                    "id_matriz": f"{hoja['perfil']}|{bench}|F{f['fila_excel']:03d}",
                    # Identificación de la parte (la Parte 2 conserva su id_matriz).
                    "parte_proyecto": "PARTE_2",
                    "tipo_control": "CIS",
                    "fuente_linea_base": excel["archivo"],
                    "perfil_auditoria": hoja["perfil"],
                    "benchmark": bench,
                    "sistema_operativo": NOMBRES_SO[bench],
                    "hoja_excel": hoja["hoja"],
                    "fila_excel": f["fila_excel"],
                    "orden_excel": compactar(f.get("orden")),
                    "control_excel": control_excel,
                    "titulo_excel": compactar(f["titulo_excel"]),
                    "cis_id_excel": compactar(f["cis_id_excel"]),
                    "cis_titulo_excel": compactar(f["cis_titulo_excel"]),
                    "criticidad_excel": compactar(f["criticidad"]),
                    "criticidad": excel_baseline.normalizar_criticidad(f["criticidad"]),
                    "propuesta_proveedor": compactar(f["propuesta_proveedor"]),
                    "propuesta_normalizada": prop,
                    "estado_alcance": estado_alcance,
                    "control_cis": res["control_cis"],
                    "titulo_cis": rec["titulo"] if rec else None,
                    "assessment_cis": rec["assessment"] if rec else None,
                    "perfiles_cis": rec["perfiles"] if rec else [],
                    "pagina_cis": rec["pagina"] if rec else None,
                    "benchmark_titulo": cat["titulo"],
                    "version_cis": cat["version"],
                    "fecha_benchmark": cat["fecha"],
                    "fuente_cis": cat["archivo"],
                    "estado_mapeo": res["estado_mapeo"],
                    "estado_mapeo_automatico": res.get("estado_mapeo_automatico", res["estado_mapeo"]),
                    "origen_mapeo": res["origen_mapeo"],
                    "requiere_confirmacion_mapeo": bool(rev and rev.get("requiere_confirmacion")),
                    "justificacion_mapeo": res.get("justificacion_revision"),
                    "evidencia_mapeo": res.get("evidencia_mapeo"),
                    "aprobacion_mapeo": res.get("aprobacion_mapeo"),
                    "cambio_numeracion": bool(res["control_cis"]) and res["control_cis"] != control_excel,
                    "indicacion_excel_novedades": res["indicacion_excel"],
                    "ids_citados_en_novedades": res["ids_citados_en_novedades"],
                    "similitud_titulo": res["similitud_titulo"],
                    "candidatos_por_titulo": [
                        {"similitud": round(s, 3), "id": i, "titulo": t}
                        for s, i, t in res.get("candidatos_por_titulo", [])
                    ],
                    "verificacion_ausencia": res.get("verificacion_ausencia"),
                    "novedades_numeracion": compactar(f["novedades_numeracion"]) or None,
                    **excl,
                    **val,
                    "validacion_excel": (f.get("validacion_excel") or "").strip() or None,
                    "observaciones_seguridad_excel": compactar(f.get("observaciones_seguridad")) or None,
                    "resultado_cis": "FUERA DE ALCANCE" if estado_alcance == "FUERA DE ALCANCE" else None,
                    "resultado_exclusion": None,
                    "alertas": list(res["observaciones_mapeo"]),
                    "observaciones": list(res["notas_mapeo"]),
                    "observaciones_mapeo_automatico": res.get("observaciones_mapeo_automatico", []),
                }
                if bench.startswith("ORACLE") and rec and re.search(
                        r"gpg keys|repositories|updates, patches", rec["titulo"], re.I):
                    fila["observaciones"].append(
                        "Oracle Linux: los valores esperados (claves GPG, repositorios ULN/yum.oracle.com, "
                        "erratas) son propios de Oracle y difieren de RHEL; evaluar con el Benchmark de Oracle.")
                filas_matriz.append(fila)

    no_usadas = set(revisiones) - usadas
    if no_usadas:
        raise ValueError(f"Revisiones que no corresponden a ninguna fila: {sorted(no_usadas)}")

    _comprobaciones_cruzadas(filas_matriz, indices)
    resumen = _resumen(filas_matriz, catalogos, excel)
    return {
        "generado": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "descripcion": "Matriz de alcance de la Parte 2 (análisis, alcance y mapeo; sin evidencia de servidores)",
        "fuentes": {
            "excel": {"archivo": excel["archivo"], "sha256": cis_catalog.sha256(excel_path), "hojas": excel["todas_las_hojas"],
                      "hojas_analizadas": [
                          {"hoja": h["hoja"], "perfil": h["perfil"], "benchmarks": h["benchmarks"],
                           "columnas": h["columnas"], "filas": len(h["filas"])}
                          for h in excel["hojas"]]},
            "cis": [
                {k: c[k] for k in ("clave", "archivo", "sha256", "titulo", "familia", "major",
                                   "version", "fecha", "total_recomendaciones")}
                for c in catalogos.values()
            ],
        },
        "estados_mapeo": mapeo.ESTADOS_MAPEO,
        "resumen": resumen,
        "matriz": filas_matriz,
    }


_RE_PRODUCTO = re.compile(r"Red Hat Enterprise Linux|Oracle Linux|Red Hat|Oracle|RHEL|\bOL\b", re.I)


def _sin_producto(t: str) -> str:
    """Neutraliza nombres de producto para comparar procedimientos RHEL vs Oracle."""
    return re.sub(r"\s+", " ", _RE_PRODUCTO.sub("<SO>", t))


def _comprobaciones_cruzadas(filas: List[Dict], indices: Dict) -> None:
    por_bench = collections.defaultdict(list)
    for f in filas:
        por_bench[f["benchmark"]].append(f)

    for bench, fs in por_bench.items():
        # 1. Control Excel duplicado en la misma hoja
        cont = collections.Counter(f["control_excel"] for f in fs)
        for f in fs:
            if cont[f["control_excel"]] > 1:
                otras = [str(o["fila_excel"]) for o in fs
                         if o["control_excel"] == f["control_excel"] and o is not f]
                f["alertas"].append(
                    f"Control Excel {f['control_excel']} duplicado en la hoja (filas {', '.join(otras)})."
                )
        # 2. Mismo control CIS con propuestas contradictorias
        por_cis = collections.defaultdict(list)
        for f in fs:
            if f["control_cis"]:
                por_cis[f["control_cis"]].append(f)
        for cid, grupo in por_cis.items():
            props = {g["estado_alcance"] for g in grupo}
            if len(grupo) > 1:
                for g in grupo:
                    otras = [f"{o['control_excel']} (fila {o['fila_excel']}, {o['propuesta_proveedor']})"
                             for o in grupo if o is not g]
                    msg = f"Control CIS {cid} también mapeado desde: {'; '.join(otras)}."
                    if len(props) > 1:
                        msg += " Propuestas YES/NO contradictorias; el control CIS se audita por estar al menos una fila en alcance."
                        g["alertas"].append(msg)
                    else:
                        g["observaciones"].append(msg)
        # 3. Conflictos entre controles en alcance
        en_alcance = [f for f in fs if f["estado_alcance"] == "EN ALCANCE" and f["titulo_cis"]]
        for a, b, motivo in CONFLICTOS_TITULO:
            fa = [f for f in en_alcance if a in f["titulo_cis"].lower()]
            fb = [f for f in en_alcance if b in f["titulo_cis"].lower()]
            if fa and fb:
                for f in fa + fb:
                    otro = fb if f in fa else fa
                    f["alertas"].append(
                        f"CONFLICTO con {otro[0]['control_cis']} '{otro[0]['titulo_cis']}': {motivo}"
                    )
                    f["conflicto_logging"] = True
        # 4. Exclusión condicionada a remoción de GDM cuando la remoción está fuera de alcance
        gdm_removido = [f for f in fs if f["titulo_cis"] and "gnome display manager is removed" in f["titulo_cis"].lower()]
        gdm_en_alcance = any(f["estado_alcance"] == "EN ALCANCE" for f in gdm_removido)
        for f in fs:
            if "GDM_NO_APLICA_SI_REMOVIDO" in f["reglas_exclusion"] and not gdm_en_alcance:
                f["alertas"].append(
                    "La exclusión depende de que GDM sea removido, pero el control de remoción de GDM "
                    "está FUERA DE ALCANCE en esta hoja."
                )
        # 5. Desviación CIS declarada por la exclusión
        for f in fs:
            if f["exclusion_declara_cis_parcial"]:
                f["alertas"].append(
                    "La exclusión declara cumplimiento CIS parcial: el valor recomendado por "
                    "Infraestructura difiere del requisito CIS. Se espera Resultado CIS distinto de "
                    "CUMPLE aunque la exclusión se cumpla."
                )

    # 6. Diferencias RHEL vs Oracle Linux en el procedimiento de Audit
    for f in filas:
        if f["benchmark"].startswith("ORACLE") and f["control_cis"]:
            par = "RHEL" + f["benchmark"][-1]
            r_ol = indices[f["benchmark"]].get(f["control_cis"])
            r_rh = indices[par].get(f["control_cis"])
            if r_rh is None:
                f["alertas"].append(f"{f['control_cis']} no existe en el Benchmark {par}.")
                continue
            if r_rh["titulo_normalizado"] != r_ol["titulo_normalizado"]:
                f["alertas"].append(f"Título distinto en {par}: '{r_rh['titulo']}'.")
            s = difflib.SequenceMatcher(None, _sin_producto(r_rh["audit"]), _sin_producto(r_ol["audit"])).ratio()
            f["similitud_audit_rhel_oracle"] = round(s, 3)
            if s < 0.98:
                f["alertas"].append(
                    f"El procedimiento Audit de Oracle Linux difiere del de {par} (similitud {s:.2f}); "
                    "usar el Benchmark de Oracle Linux para hosts Oracle."
                )


def _resumen(filas: List[Dict], catalogos: Dict, excel: Dict) -> Dict:
    res = {"por_hoja": {}, "por_benchmark": {}}
    for hoja in excel["hojas"]:
        bench0 = hoja["benchmarks"][0]
        fs = [f for f in filas if f["hoja_excel"] == hoja["hoja"] and f["benchmark"] == bench0]
        alc = collections.Counter(f["estado_alcance"] for f in fs)
        en = [f for f in fs if f["estado_alcance"] == "EN ALCANCE"]
        res["por_hoja"][hoja["hoja"]] = {
            "perfil": hoja["perfil"],
            "benchmarks": hoja["benchmarks"],
            "controles_excel": len(fs),
            "en_alcance": alc.get("EN ALCANCE", 0),
            "fuera_de_alcance": alc.get("FUERA DE ALCANCE", 0),
            "indeterminado": alc.get("INDETERMINADO", 0),
            "exclusiones_en_alcance": dict(collections.Counter(f["tipo_exclusion"] for f in en)),
            "criticidad_en_alcance": dict(collections.Counter(f["criticidad"] for f in en)),
        }
    for bench in sorted({f["benchmark"] for f in filas}):
        fs = [f for f in filas if f["benchmark"] == bench]
        en = [f for f in fs if f["estado_alcance"] == "EN ALCANCE"]
        cis_unicos = sorted({f["control_cis"] for f in en if f["control_cis"]},
                            key=lambda s: [int(x) for x in s.split(".")])
        tipos_val = collections.Counter()
        for cid in cis_unicos:
            f0 = next(f for f in en if f["control_cis"] == cid)
            tipos_val[f0["tipo_validacion"]] += 1
        res["por_benchmark"][bench] = {
            "sistema_operativo": NOMBRES_SO[bench],
            "benchmark": f"{catalogos[bench]['titulo']} {catalogos[bench]['version']}",
            "estado_mapeo_todas": dict(collections.Counter(f["estado_mapeo"] for f in fs)),
            "estado_mapeo_en_alcance": dict(collections.Counter(f["estado_mapeo"] for f in en)),
            "cambio_numeracion_en_alcance": sum(1 for f in en if f["cambio_numeracion"]),
            "cambio_numeracion_todas": sum(1 for f in fs if f["cambio_numeracion"]),
            "controles_cis_unicos_a_auditar": len(cis_unicos),
            "lista_controles_cis_a_auditar": cis_unicos,
            "tipo_validacion_controles_unicos": dict(tipos_val),
            "en_alcance_sin_control_cis": [f["id_matriz"] for f in en if not f["control_cis"]],
            "requiere_confirmacion": [f["id_matriz"] for f in fs if f["requiere_confirmacion_mapeo"]],
            "filas_con_alertas_en_alcance": sum(1 for f in en if f["alertas"]),
        }
    return res


# ----------------------------------------------------------------- escritura

COLUMNAS_TABLA = [
    ("id_matriz", "ID matriz"),
    ("sistema_operativo", "Sistema operativo"),
    ("perfil_auditoria", "Perfil auditoría"),
    ("hoja_excel", "Hoja Excel"),
    ("fila_excel", "Fila Excel"),
    ("control_excel", "Control Excel"),
    ("titulo_excel", "Título Excel"),
    ("criticidad", "Criticidad"),
    ("propuesta_proveedor", "Propuesta proveedor"),
    ("estado_alcance", "Estado alcance"),
    ("cis_id_excel", "Id CIS indicado en Excel"),
    ("control_cis", "Control CIS mapeado"),
    ("titulo_cis", "Título CIS"),
    ("assessment_cis", "Tipo CIS"),
    ("version_cis", "Versión CIS"),
    ("fecha_benchmark", "Fecha Benchmark"),
    ("pagina_cis", "Página CIS"),
    ("estado_mapeo", "Estado mapeo"),
    ("origen_mapeo", "Origen mapeo"),
    ("requiere_confirmacion_mapeo", "Requiere confirmación"),
    ("cambio_numeracion", "Cambio numeración"),
    ("novedades_numeracion", "Novedades numeración (Excel)"),
    ("justificacion_mapeo", "Justificación mapeo"),
    ("exclusion_infraestructura", "Exclusión Infraestructura"),
    ("tipo_exclusion", "Tipo exclusión"),
    ("verificacion_exclusion_propuesta", "Verificación técnica de la exclusión"),
    ("evidencia_organizacional_requerida", "Evidencia organizacional requerida"),
    ("dominio_validacion", "Dominio"),
    ("tipo_validacion", "Tipo validación"),
    ("nota_validacion", "Nota validación"),
    ("resultado_cis", "Resultado CIS"),
    ("resultado_exclusion", "Resultado exclusión"),
    ("alertas", "Alertas"),
    ("observaciones", "Observaciones"),
]


def _valor_plano(v):
    if isinstance(v, list):
        return " || ".join(str(x) for x in v)
    if isinstance(v, bool):
        return "SÍ" if v else "NO"
    return v


def escribir_json(datos: Dict, ruta: Path) -> None:
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)


def escribir_csv(filas: List[Dict], ruta: Path) -> None:
    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([t for _, t in COLUMNAS_TABLA])
        for fila in filas:
            w.writerow([_valor_plano(fila.get(k)) for k, _ in COLUMNAS_TABLA])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reconstruir-catalogo", action="store_true",
                   help="Volver a extraer los PDF CIS aunque el hash no haya cambiado")
    args = p.parse_args(argv)
    datos = construir(args)
    salida = RAIZ / "data" / "alcance"
    salida.mkdir(parents=True, exist_ok=True)
    escribir_json(datos, salida / "matriz_alcance.json")
    escribir_csv(datos["matriz"], salida / "matriz_alcance.csv")
    from .matriz_xlsx import escribir_xlsx
    escribir_xlsx(datos, salida / "matriz_alcance.xlsx", COLUMNAS_TABLA, _valor_plano)
    print(json.dumps(datos["resumen"], ensure_ascii=False, indent=1)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
