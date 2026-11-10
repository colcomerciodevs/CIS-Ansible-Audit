"""Exclusiones de Infraestructura de la Parte 1.

Fuente canónica y ÚNICA: columna "Exclusiones de Infraestructura" del Excel
"Linea Base - Analisis de Impacto _Parte1.xlsx".

    Excel P1 -> parser exacto de la celda -> data/exclusiones/excepciones_p1.json -> motor

Formato de la celda (una clave por línea, valores en una sola línea):

    TIPO: USUARIOS | CONTROL_NO_APLICADO
    USUARIOS: a, b                      (TIPO USUARIOS; admite varias líneas 'USUARIOS (<calificador>): …', que se unen)
    VALOR: false                        (TIPO CONTROL_NO_APLICADO)
    MOTIVO: …
    TRATAMIENTO: EXCLUSION_INFRAESTRUCTURA

Las claves documentales ORIGEN y VARIABLE pueden figurar en la celda: se aceptan para no rechazar la fila, pero no se
interpretan ni se conservan. Toda exclusión P1 tiene origen LINEA_BASE_P1. Fail-closed: una celda no vacía que no
cumpla el formato aborta la extracción. La columna "Observacion" nunca es una exclusión. En la celda del Excel srvinnops
no puede ser una excepción (es INSTRUMENTACION_AUDITORIA, D-3B-01).

Decisiones posteriores de Infraestructura (2026-10-27): config/exclusiones_infraestructura.yml, solo TIPO USUARIOS y con
alcance por perfil de auditoría. para_perfil() UNE sus usuarios a los de la celda del mismo control (nunca reemplaza ni
elimina), por host.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional

TIPOS = ("USUARIOS", "CONTROL_NO_APLICADO")
TRATAMIENTOS = ("EXCLUSION_INFRAESTRUCTURA",)
ORIGEN = "LINEA_BASE_P1"
COLUMNA = "Exclusiones de Infraestructura"
_CLAVES = re.compile(r"^(TIPO|ORIGEN|VARIABLE|USUARIOS(?:\s*\(([A-Za-z0-9_ ]+)\))?|VALOR|MOTIVO|TRATAMIENTO):\s*(.*)$")
# Referencias a fuentes documentales retiradas dentro del MOTIVO (identificador en mayúsculas con '_'): se publican sin
# ellas. '(comentario en <ID>: "texto")' -> '(comentario: "texto")'; ' según <ID>' -> ''.
_ID_DOC = r"[A-Z][A-Z0-9]*_[A-Z0-9_]+(?:\.txt)?"
_REF_COMENTARIO = re.compile(r"\(comentario en " + _ID_DOC + r":\s*")
_REF_SEGUN = re.compile(r"\s+según\s+" + _ID_DOC + r"\b")


def _motivo(texto: str) -> str:
    return _REF_SEGUN.sub("", _REF_COMENTARIO.sub("(comentario: ", texto)).strip()


class ErrorExclusion(ValueError):
    pass


def parsear_celda(texto: str) -> Dict:
    campos: Dict[str, str] = {}
    grupos: Dict[str, List[str]] = {}
    for l in [x.strip() for x in str(texto).replace("\r\n", "\n").split("\n") if x.strip()]:
        m = _CLAVES.match(l)
        if not m:
            raise ErrorExclusion(f"línea no reconocida: {l[:80]!r}")
        clave, calif, valor = m.group(1), m.group(2), m.group(3).strip()
        if clave.startswith("USUARIOS"):
            k = (calif or "*").strip()
            if k in grupos:
                raise ErrorExclusion(f"USUARIOS duplicado ({k})")
            grupos[k] = [u.strip() for u in valor.split(",") if u.strip()]
            continue
        if clave in campos:
            raise ErrorExclusion(f"clave duplicada: {clave}")
        campos[clave] = valor
    for k in ("TIPO", "MOTIVO", "TRATAMIENTO"):
        if not campos.get(k):
            raise ErrorExclusion(f"falta {k}")
    if campos["TIPO"] not in TIPOS:
        raise ErrorExclusion(f"TIPO no soportado: {campos['TIPO']}")
    if campos["TRATAMIENTO"] not in TRATAMIENTOS:
        raise ErrorExclusion(f"TRATAMIENTO no soportado: {campos['TRATAMIENTO']}")
    usuarios = [u for k in grupos for u in grupos[k]]
    if campos["TIPO"] == "USUARIOS":
        if not usuarios:
            raise ErrorExclusion("lista de USUARIOS vacía")
        if campos.get("VALOR"):
            raise ErrorExclusion("VALOR no aplica a TIPO USUARIOS")
    elif grupos or not campos.get("VALOR"):
        raise ErrorExclusion("CONTROL_NO_APLICADO exige VALOR y no admite USUARIOS")
    if "srvinnops" in usuarios:
        raise ErrorExclusion("srvinnops no puede ser una excepción: es INSTRUMENTACION_AUDITORIA (D-3B-01)")
    if len(set(usuarios)) != len(usuarios):
        raise ErrorExclusion("usuario repetido en la celda")
    return {"tipo": campos["TIPO"], "origen": ORIGEN, "elementos_excluidos": usuarios, "valor": campos.get("VALOR"),
            "motivo": _motivo(campos["MOTIVO"]), "tratamiento": campos["TRATAMIENTO"]}


def resumen(e: Dict) -> str:
    """Texto funcional de la exclusión para reportes y matrices (nunca la celda cruda)."""
    return (f"{e['tipo']}: " + (", ".join(e["elementos_excluidos"]) if e["tipo"] == "USUARIOS" else f"VALOR {e['valor']}")
            + f" · Motivo: {e['motivo']}")


def extraer(excel_p1: Path, matriz_p1: Dict, benchmark: str = "ORACLE9") -> List[Dict]:
    """Exclusiones normalizadas desde la columna exacta 'Exclusiones de Infraestructura' (una por fila con contenido)."""
    from .parte1 import leer_parte1
    p1 = leer_parte1(excel_p1)
    por_fila = {f["fila_excel"]: f for f in matriz_p1["matriz"] if f["benchmark"] == benchmark}
    res = []
    for f in p1["filas"]:
        if not f["exclusion_infraestructura"]:
            continue
        m = por_fila.get(f["fila_excel"])
        if m is None:
            raise ErrorExclusion(f"fila {f['fila_excel']}: sin correspondencia en la matriz P1")
        try:
            e = parsear_celda(f["exclusion_infraestructura"])
        except ErrorExclusion as x:
            raise ErrorExclusion(f"fila {f['fila_excel']} ({m['control_excel']}): {x}") from None
        res.append({"source_file": p1["archivo"], "source_sheet": p1["hoja"], "source_row": f["fila_excel"],
                    "control_excel": m["control_excel"], "control_cis": m["controles_cis"], **e, "exclusion_definida": True})
    return res


def para_motor(normalizadas: List[Dict]) -> Dict[str, Dict]:
    """{control_excel: {...}}: la exclusión se aplica a la FILA P1 (varias filas P1 pueden mapear al mismo control CIS)."""
    return {e["control_excel"]: {"tipo": e["tipo"], "usuarios": set(e["elementos_excluidos"]), "controles_cis": e["control_cis"],
                                 "origen": e["origen"], "motivo": e["motivo"], "valor": e["valor"],
                                 "fuente": f"{e['source_file']} · {e['source_sheet']} fila {e['source_row']}"}
            for e in normalizadas}


PERFILES = ("RHEL_ORACLE_8", "RHEL_ORACLE_9", "SLES_15")
BENCHMARKS = ("ORACLE8", "ORACLE9", "RHEL8", "RHEL9", "SLES15")
ORIGEN_COMPLEMENTARIA = "DECISION_INFRAESTRUCTURA"


def _entradas(d: Dict, seccion: str, clave: str, nombre: str) -> List[Dict]:
    """Entradas validadas de una sección del YAML (fail-closed: formato inválido aborta)."""
    res = []
    for i, e in enumerate(d.get(seccion) or [], start=1):
        ctl, us, si, mot = e.get(clave), e.get("usuarios"), e.get("sistemas"), (e.get("motivo") or "").strip()
        if not isinstance(ctl, str) or not ctl:
            raise ErrorExclusion(f"{nombre} {seccion} entrada {i}: falta {clave}")
        if not isinstance(us, list) or not us or not all(isinstance(u, str) and re.fullmatch(r"[a-z_][a-z0-9_.-]*\$?", u) for u in us):
            raise ErrorExclusion(f"{nombre} {seccion} entrada {i} ({ctl}): usuarios inválidos")
        if len(set(us)) != len(us):
            raise ErrorExclusion(f"{nombre} {seccion} entrada {i} ({ctl}): usuario repetido")
        if not isinstance(si, list) or not si or any(s not in PERFILES for s in si):
            raise ErrorExclusion(f"{nombre} {seccion} entrada {i} ({ctl}): sistemas debe ser una lista de {PERFILES}")
        if not mot:
            raise ErrorExclusion(f"{nombre} {seccion} entrada {i} ({ctl}): falta motivo")
        bs = e.get("benchmarks")
        if bs is not None and (not isinstance(bs, list) or not bs or any(b not in BENCHMARKS for b in bs)):
            raise ErrorExclusion(f"{nombre} {seccion} entrada {i} ({ctl}): benchmarks debe ser una lista de {BENCHMARKS}")
        res.append({clave: ctl, "usuarios": list(us), "sistemas": list(si), "benchmarks": list(bs) if bs else None,
                    "motivo": mot, "origen": d.get("origen")})
    return res


def _yaml(ruta: Path) -> Dict:
    import yaml
    return (yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}) if ruta.exists() else {}


def cargar_complementarias(ruta: Path) -> List[Dict]:
    """Sección exclusiones_p1 de config/exclusiones_infraestructura.yml (por control Excel P1)."""
    return _entradas(_yaml(ruta), "exclusiones_p1", "control_excel", ruta.name)


def cargar_complementarias_p2(ruta: Path) -> List[Dict]:
    """Sección exclusiones_p2: solo usuarios, por control CIS (filas P2 sin exclusión en el Excel P2)."""
    return _entradas(_yaml(ruta), "exclusiones_p2", "control_cis", ruta.name)


def p2_para_perfil(complementarias: List[Dict], perfil: Optional[str], benchmark: Optional[str] = None) -> Dict[str, Dict]:
    """{control_cis: {"tipo": "USUARIOS", "usuarios", "motivo"}} de las entradas P2 que aplican al host."""
    base = para_perfil({}, [{**c, "control_excel": c["control_cis"]} for c in complementarias or []], perfil, benchmark)
    return {k: {**v, "fuente": "config/exclusiones_infraestructura.yml"} for k, v in base.items()}


def para_perfil(base: Dict[str, Dict], complementarias: List[Dict], perfil: Optional[str],
                benchmark: Optional[str] = None) -> Dict[str, Dict]:
    """Exclusiones P1 efectivas en un host: las de la línea base más las complementarias de su perfil (y, si la entrada lo
    restringe, de su benchmark: p. ej. RHEL8/RHEL9 sin Oracle Linux), por unión de usuarios."""
    res = {k: {**v, "usuarios": set(v.get("usuarios") or ())} for k, v in (base or {}).items()}
    for c in complementarias or []:
        if perfil not in c["sistemas"] or (c.get("benchmarks") and benchmark not in c["benchmarks"]):
            continue
        actual = res.get(c["control_excel"])
        if actual and actual.get("tipo") != "USUARIOS":
            raise ErrorExclusion(f"{c['control_excel']}: la línea base define {actual.get('tipo')}; una decisión complementaria "
                                 "de usuarios no puede combinarse con ella")
        if actual:
            nuevos = [u for u in c["usuarios"] if u not in actual["usuarios"]]
            actual["usuarios"] |= set(c["usuarios"])
            actual["motivo"] = f"{actual.get('motivo')} {c['motivo']}".strip()
            actual.setdefault("complementos", []).append({"usuarios": nuevos, "motivo": c["motivo"]})
        else:
            res[c["control_excel"]] = {"tipo": "USUARIOS", "usuarios": set(c["usuarios"]), "controles_cis": [],
                                       "origen": ORIGEN_COMPLEMENTARIA, "motivo": c["motivo"], "valor": None,
                                       "fuente": "config/exclusiones_infraestructura.yml",
                                       "complementos": [{"usuarios": list(c["usuarios"]), "motivo": c["motivo"]}]}
    return res
