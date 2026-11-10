"""Catálogo de recomendaciones a partir de los PDF CIS suministrados.

Extrae texto con `pdftotext -layout` (poppler-utils) e identifica:
- producto, versión y fecha del Benchmark (leídos del contenido, no del nombre);
- cada recomendación: id, título, tipo (Automated/Manual), perfiles, página;
- secciones Description, Rationale, Audit, Remediation y Default Value.

El texto de `Remediation` se conserva únicamente como referencia documental.
En este proyecto nunca se ejecuta.
"""

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .normalizacion import normalizar_titulo

_RE_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)+)\s+(\S.*?)\s*$")
_RE_PAGE = re.compile(r"^\s*Page\s+(\d+)\s*$")
_RE_TOC = re.compile(r"^\s*(\d+(?:\.\d+)*)\s+(.+?)\s*\.{3,}\s*(\d+)\s*$")
_RE_BENCH_VERSION = re.compile(r"^\s*v(\d+\.\d+\.\d+)\s*-\s*(\d{2}-\d{2}-\d{4})\s*$")

_SECCIONES = [
    "Description:",
    "Rationale:",
    "Impact:",
    "Audit:",
    "Remediation:",
    "Default Value:",
    "References:",
    "Additional Information:",
    "CIS Controls:",
    "MITRE ATT&CK Mappings:",
]
_RUIDO = {"Internal Only - General"}

# Identificación del producto a partir del texto de portada.
_PRODUCTOS = [
    ("Red Hat Enterprise Linux 8", "RHEL", "8"),
    ("Red Hat Enterprise Linux 9", "RHEL", "9"),
    ("Oracle Linux 8", "ORACLE", "8"),
    ("Oracle Linux 9", "ORACLE", "9"),
    ("SUSE Linux Enterprise 15", "SLES", "15"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def extraer_texto(pdf: Path) -> List[str]:
    salida = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.decode("utf-8", errors="replace")
    return salida.splitlines()


def identificar_benchmark(lineas: List[str]) -> Dict[str, Optional[str]]:
    portada = " ".join(l.strip() for l in lineas[:20] if l.strip())
    portada = re.sub(r"\s+", " ", portada)
    info = {"titulo": None, "familia": None, "major": None, "version": None, "fecha": None}
    for nombre, familia, major in _PRODUCTOS:
        if nombre in portada:
            info.update(familia=familia, major=major, titulo=f"CIS {nombre} Benchmark")
            break
    for l in lineas[:20]:
        m = _RE_BENCH_VERSION.match(l)
        if m:
            info["version"] = "v" + m.group(1)
            mm, dd, yyyy = m.group(2).split("-")
            info["fecha"] = f"{yyyy}-{mm}-{dd}"
            break
    return info


def _limpiar(bloque: List[str]) -> str:
    out = []
    for l in bloque:
        if _RE_PAGE.match(l) or l.strip() in _RUIDO:
            continue
        out.append(l.rstrip())
    # dedent común
    no_vacias = [l for l in out if l.strip()]
    ind = min((len(l) - len(l.lstrip()) for l in no_vacias), default=0)
    texto = "\n".join(l[ind:] if len(l) >= ind else l for l in out)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def parsear(lineas: List[str]) -> Dict:
    # Índice de páginas por línea
    pagina_de_linea = []
    pagina = 1
    for l in lineas:
        m = _RE_PAGE.match(l)
        pagina_de_linea.append(pagina)
        if m:
            pagina = int(m.group(1)) + 1

    # TOC: secciones y recomendaciones (para títulos de sección)
    toc = {}
    for l in lineas[:2500]:
        m = _RE_TOC.match(l)
        if m:
            toc[m.group(1)] = re.sub(r"\s+", " ", m.group(2)).strip()

    # Fin del cuerpo: inicio de los apéndices
    fin = len(lineas)
    for i, l in enumerate(lineas):
        if i > 2500 and l.strip().startswith("Appendix: Summary Table"):
            fin = i
            break

    # Localizar encabezados de recomendación: id + título, seguido de
    # "Profile Applicability:" en las siguientes líneas.
    inicios = []
    i = 0
    while i < fin:
        m = _RE_HEADING.match(lineas[i])
        if m:
            j = i + 1
            titulo = [m.group(2)]
            while j < min(i + 5, fin):
                s = lineas[j].strip()
                if s.startswith("Profile Applicability"):
                    inicios.append((i, j, m.group(1), " ".join(titulo)))
                    break
                if s and not _RE_PAGE.match(lineas[j]) and s not in _RUIDO:
                    titulo.append(s)
                j += 1
        i += 1

    recomendaciones = []
    for k, (ini, prof, cid, titulo) in enumerate(inicios):
        fin_bloque = inicios[k + 1][0] if k + 1 < len(inicios) else fin
        bloque = lineas[prof:fin_bloque]
        titulo = re.sub(r"\s+", " ", titulo).strip()
        m = re.search(r"\((Automated|Manual)\)\s*$", titulo)
        assessment = m.group(1) if m else None
        titulo_sin = re.sub(r"\s*\((Automated|Manual)\)\s*$", "", titulo)

        secciones: Dict[str, List[str]] = {"Profile Applicability:": []}
        actual = "Profile Applicability:"
        for l in bloque[1:]:
            s = l.strip()
            if s in _SECCIONES:
                actual = s
                secciones.setdefault(actual, [])
                continue
            secciones.setdefault(actual, []).append(l)

        perfiles = [
            re.sub(r"^[•\-\s]+", "", p).strip()
            for p in secciones.get("Profile Applicability:", [])
            if p.strip().startswith("•")
        ]
        rec = {
            "id": cid,
            "titulo": titulo_sin,
            "titulo_completo": titulo,
            "titulo_normalizado": normalizar_titulo(titulo_sin),
            "assessment": assessment,
            "perfiles": perfiles,
            "pagina": pagina_de_linea[ini],
            "seccion_padre": toc.get(cid.rsplit(".", 1)[0]),
            "description": _limpiar(secciones.get("Description:", [])),
            "rationale": _limpiar(secciones.get("Rationale:", [])),
            "impact": _limpiar(secciones.get("Impact:", [])),
            "audit": _limpiar(secciones.get("Audit:", [])),
            "remediation_referencia_no_ejecutar": _limpiar(secciones.get("Remediation:", [])),
            "default_value": _limpiar(secciones.get("Default Value:", [])),
        }
        recomendaciones.append(rec)

    ids_rec = {r["id"] for r in recomendaciones}
    secciones_toc = {k: v for k, v in toc.items() if k not in ids_rec}
    return {"recomendaciones": recomendaciones, "secciones": secciones_toc}


def construir_catalogo(pdf: Path) -> Dict:
    lineas = extraer_texto(pdf)
    info = identificar_benchmark(lineas)
    datos = parsear(lineas)
    clave = f"{info['familia']}{info['major']}" if info["familia"] else pdf.stem
    return {
        "clave": clave,
        "archivo": pdf.name,
        "sha256": sha256(pdf),
        **info,
        "total_recomendaciones": len(datos["recomendaciones"]),
        "secciones": datos["secciones"],
        "recomendaciones": datos["recomendaciones"],
    }


def cargar_o_construir(dir_cis: Path, dir_salida: Path, forzar: bool = False) -> Dict[str, Dict]:
    """Construye (o reutiliza si el hash coincide) el catálogo de cada PDF."""
    dir_salida.mkdir(parents=True, exist_ok=True)
    catalogos = {}
    for pdf in sorted(dir_cis.glob("*.pdf")):
        digest = sha256(pdf)
        cache = None
        for existente in dir_salida.glob("*.json"):
            with open(existente, encoding="utf-8") as f:
                c = json.load(f)
            if c.get("sha256") == digest:
                cache = c
                break
        if cache is None or forzar:
            cache = construir_catalogo(pdf)
            with open(dir_salida / f"{cache['clave']}.json", "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=1)
        catalogos[cache["clave"]] = cache
    return catalogos
