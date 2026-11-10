"""Selección de lo que se ejecuta en cada benchmark a partir de las filas YES de la matriz.

Solo las filas con propuesta del proveedor YES generan comandos. Una fila NO nunca
añade comandos al recolector (decisión 1 / punto 19).
"""

from typing import Dict, List, Optional

from .comandos import BASE, COMANDOS
from .comandos_priv import SOLO_PRIVILEGIADO
from .controles import Control, control_para_titulo
from .exclusiones_eval import COMANDOS_EXCLUSION, PAQUETES_EXCLUSION

FAMILIA = {"RHEL8": "rhel", "ORACLE8": "rhel", "RHEL9": "rhel", "ORACLE9": "rhel", "SLES15": "sles"}
OS_ID = {"RHEL8": ("rhel", "8"), "ORACLE8": ("ol", "8"), "RHEL9": ("rhel", "9"), "ORACLE9": ("ol", "9"), "SLES15": ("sles", "15")}


def filas_si(matriz: List[Dict], benchmark: str) -> List[Dict]:
    return [f for f in matriz if f["benchmark"] == benchmark and f["estado_alcance"] == "EN ALCANCE"]


# Parte 1: hoja única (SLES15-ORA8-9), aplicable también a RHEL 8/9 (decisión D-P1-01, confirmada 2026-10-23).
OFICIALES_P1 = ("ORACLE8", "RHEL8", "ORACLE9", "RHEL9", "SLES15")


def filas_p1(matriz_p1: Optional[Dict], benchmark: str) -> List[Dict]:
    if not matriz_p1 or benchmark not in OFICIALES_P1:
        return []
    return [f for f in matriz_p1["matriz"] if f["benchmark"] == benchmark and f["estado_alcance"] == "EN ALCANCE"]


def requerimientos(matriz: List[Dict], benchmark: str, matriz_p1: Optional[Dict] = None) -> Dict:
    """Comandos y paquetes exigidos por las filas YES de la Parte 2 y, si se indica, por las filas EN ALCANCE de la Parte 1.

    Una evidencia común a ambas partes se recolecta una sola vez (mismo id de comando)."""
    fam = FAMILIA[benchmark]
    si = filas_si(matriz, benchmark) + [{**f, "titulo_cis": t, "reglas_exclusion": []}
                                        for f in filas_p1(matriz_p1, benchmark) for t in f["titulos_cis"]]
    controles: Dict[str, Control] = {}
    cmds, paquetes, sin_eval = list(BASE), [], []
    for f in si:
        ctl = control_para_titulo(f["titulo_cis"]) if f["titulo_cis"] else None
        if ctl is None:
            sin_eval.append(f["id_matriz"])
            continue
        controles[ctl.id] = ctl
        for c in ctl.comandos_para(fam):
            if c in SOLO_PRIVILEGIADO:  # evidencia exclusiva del recolector privilegiado firmado (1.2.0)
                continue
            if c not in cmds:
                cmds.append(c)
        paquetes += [p for p in ctl.paquetes if p not in paquetes]
        for regla in f.get("reglas_exclusion") or []:
            for c in COMANDOS_EXCLUSION.get(regla, ()):
                if c not in cmds and fam in COMANDOS[c].familias:
                    cmds.append(c)
            paquetes += [p for p in PAQUETES_EXCLUSION.get(regla, ()) if p not in paquetes]
    if benchmark not in ("RHEL8", "ORACLE8") and "gdm.grupos_dnf" in cmds:
        cmds.remove("gdm.grupos_dnf")  # solo el Audit de RHEL/OL 8 consulta grupos de entorno
    orden = list(COMANDOS)
    cmds = sorted(cmds, key=orden.index)
    if not paquetes and "pkg.rpm" in cmds:
        cmds.remove("pkg.rpm")
    return {"benchmark": benchmark, "familia": fam, "filas_si": si, "controles": controles,
            "comandos": cmds, "paquetes": paquetes, "filas_sin_evaluador": sin_eval}
