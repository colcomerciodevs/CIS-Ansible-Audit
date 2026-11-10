"""Funciones de normalización usadas solo para clasificación interna.

Nunca se modifica el texto fuente: los valores originales se conservan en
la matriz junto al valor normalizado.
"""

import re
import unicodedata
from typing import Optional

_ACENTOS = str.maketrans("ÁÉÍÓÚÜÑáéíóúüñ", "AEIOUUNaeiouun")

# Texto de relleno que el Excel usa cuando el control fue removido o absorbido.
PLACEHOLDER_CONSULTAR = "consultar, en esta fila"

_RE_CONTROL_ID = re.compile(r"^\d+(?:\.\d+)*$")
_RE_ASSESSMENT = re.compile(r"\((automated|manual|automatico|automático)\)?", re.I)


def texto(valor) -> str:
    """Convierte un valor de celda en texto; None -> ''."""
    if valor is None:
        return ""
    s = str(valor)
    # openpyxl expone retornos de carro Excel como _x000D_
    s = s.replace("_x000D_", "")
    return s


def compactar(valor) -> str:
    """Texto en una sola línea con espacios colapsados."""
    return re.sub(r"\s+", " ", texto(valor)).strip()


def sin_acentos(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    return s.translate(_ACENTOS)


def clave(valor) -> str:
    """Clave de comparación: mayúsculas, sin acentos, sin espacios extremos."""
    return sin_acentos(compactar(valor)).upper()


def normalizar_propuesta(valor, positivos, negativos) -> str:
    """Devuelve 'AFIRMATIVA', 'NEGATIVA' o 'INDETERMINADA'."""
    if any(isinstance(v, bool) for v in list(positivos) + list(negativos)):
        raise ValueError("Valores de alcance leídos como booleanos: entrecomillarlos en config/audit.yml")
    k = clave(valor).rstrip(".")
    if k in {clave(p) for p in positivos}:
        return "AFIRMATIVA"
    if k in {clave(n) for n in negativos}:
        return "NEGATIVA"
    return "INDETERMINADA"


def es_placeholder(valor) -> bool:
    return PLACEHOLDER_CONSULTAR in compactar(valor).lower()


def normalizar_id_control(valor) -> Optional[str]:
    """'  6.3.3.3 ' -> '6.3.3.3'. Devuelve None si no es un identificador."""
    s = compactar(valor).strip(" .")
    if _RE_CONTROL_ID.match(s):
        return s
    return None


def normalizar_titulo(valor) -> str:
    """Normaliza un título CIS para comparar (no para mostrar)."""
    s = compactar(valor).lower()
    s = sin_acentos(s)
    s = s.replace("/etc./", "/etc/").replace("’", "'").replace("‘", "'")
    s = _RE_ASSESSMENT.sub("", s)
    s = re.sub(r"\(automated", "", s)
    s = re.sub(r"[^a-z0-9/_\-\.' ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s


