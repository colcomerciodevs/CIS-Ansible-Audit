"""Tipos comunes de evaluación y reglas de agregación.

Un control se evalúa como lista de componentes (requisitos atómicos del CIS).
Estados de componente:
  OK, FALLA, ERROR, MANUAL, ORG (evidencia organizacional), NA, FUERA (sub-requisito fuera de alcance),
  INFO (evidencia informativa que se muestra pero no decide el resultado; p. ej. el FileCreateMode de imjournal).

Agregación (en este orden):
  1. todos NA                      -> NO APLICA
  2. alguna FALLA y algún OK       -> CUMPLE PARCIALMENTE (desviación parcial) solo si el control
                                      declara requisitos independientes (permite_parcial); si no, NO CUMPLE
     alguna FALLA sin OK           -> NO CUMPLE   (una falla con evidencia es concluyente aunque otro componente falle por error)
  3. algún ERROR                   -> ERROR DE AUDITORÍA
  4. algún MANUAL                  -> REQUIERE VALIDACIÓN MANUAL
  5. algún ORG                     -> REQUIERE EVIDENCIA ORGANIZACIONAL
  6. resto OK                      -> CUMPLE
Componentes FUERA (partes del control CIS cuya fila del Excel es NO o no existe) no
participan y no afectan el resultado: si la parte en alcance cumple, el resultado es
CUMPLE con una observación que indica qué quedó fuera de alcance.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .recolector import Registro

OK, FALLA, ERROR, MANUAL, ORG, NA, FUERA = "OK", "FALLA", "ERROR", "MANUAL", "ORG", "NA", "FUERA"
INFO = "INFO"

CUMPLE = "CUMPLE"
NO_CUMPLE = "NO CUMPLE"
PARCIAL = "CUMPLE PARCIALMENTE"
MANUAL_R = "REQUIERE VALIDACIÓN MANUAL"
ORG_R = "REQUIERE EVIDENCIA ORGANIZACIONAL"
NO_APLICA = "NO APLICA"
FUERA_R = "FUERA DE ALCANCE"
ERROR_R = "ERROR DE AUDITORÍA"

MOTIVOS = {
    "PERMISOS_INSUFICIENTES": "La cuenta de auditoría no tiene sudo -n para el comando requerido, o el dato exige el recolector privilegiado (no instalado o no ejecutado)",
    "TIMEOUT": "El comando superó el tiempo máximo",
    "COMANDO_INEXISTENTE": "Comando no disponible en el host",
    "RC_INESPERADO": "Código de retorno no esperado",
    "NO_RECOLECTADO": "Comando no recolectado",
    "SALIDA_NO_INTERPRETABLE": "La salida no pudo interpretarse",
    "HOST_SIN_EVIDENCIA": "El host no produjo evidencia (inalcanzable, no soportado o recolección fallida)",
    "PENDIENTE_AUDITORIA_PROFUNDA": "Requiere recorrido intensivo de filesystem no autorizado en esta ejecución",
    "EVIDENCIA_ORGANIZACIONAL": "Depende de una lista o política aprobada externa al host",
    "CONFLICTO_BASELINE": "Requisitos incompatibles en la línea base",
    "POLITICA_O_REVISION": "Requiere revisión humana según el propio Benchmark",
    "ANALISIS_ESTATICO_NO_CONCLUYENTE": "El valor efectivo no es determinable sin ejecutar código del host (prohibido); requiere validación manual",
    "RECOLECTOR_PRIVILEGIADO_NO_CONFIABLE": "El recolector privilegiado instalado no supera la verificación de integridad (archivo regular, owner/grupo/modo, ACL, enlaces, directorios padre, SHA-256); no se ejecutó",
    "REQUIERE_EXTENSION_RECOLECTOR": "El dato no lo entrega el recolector privilegiado usado en la ejecución (versión anterior a la vigente); no es incumplimiento",
    "EVIDENCIA_HOST_INSUFICIENTE": "Política aprobada disponible (archivo + SHA-256), pero la evidencia del host no permite la comparación exacta",
    "RECOLECCION_PRIVILEGIADA_FALLIDA": "El recolector privilegiado no terminó correctamente (p. ej. fallo del filtro de redacción); su salida se descartó sin almacenarse",
    "SYSTEMD_NO_CONSULTABLE": "systemctl no pudo consultar a systemd con la cuenta de auditoría (p. ej. 'Failed to connect to bus: Operation not permitted'); no significa que el servicio esté detenido ni activo",
}
_RE_MOTIVO = re.compile(r"\[([A-Z_]+)\]")


def motivo_de(texto: str) -> Optional[str]:
    m = _RE_MOTIVO.search(texto or "")
    return m.group(1) if m and m.group(1) in MOTIVOS else None


# Respuesta de systemctl que NO es un estado de la unidad: sin acceso al bus de systemd / D-Bus. Nunca equivale a
# "disabled"/"inactive" ni a "enabled"/"active" (ajuste 2026-10-27).
RE_SYSTEMD_SIN_ACCESO = re.compile(r"Failed to (connect to|get D-Bus connection)|Operation not permitted|Access denied|Permission denied|"
                                   r"Connection refused|Transport endpoint is not connected|System has not been booted with systemd", re.I)


def es_consulta_systemctl(cid: str) -> bool:
    """True si el comando del catálogo consulta estados con 'systemctl is-enabled/is-active' (formato unidad|enabled|active)."""
    from .comandos import COMANDOS
    c = COMANDOS.get(cid)
    return bool(c and "systemctl is-enabled" in c.cmd)


_RE_SUDO = re.compile(r"sudo: (a password is required|a terminal is required|.*not in the sudoers|.*may not run sudo|.*no tty present)", re.I)


@dataclass
class Componente:
    id: str
    descripcion: str
    estado: str
    esperado: str = ""
    encontrado: str = ""
    evidencia: List[str] = field(default_factory=list)  # ids de comando
    subcondicion: Optional[str] = None
    # Elementos identificados de una FALLA (p. ej. {"usuario", "archivo", "linea"} o {"usuario", "valor"}). Los usa la
    # capa de resultado final para aplicar excepciones por usuario; sin elementos, una excepción no puede cubrirla.
    elementos: List[Dict] = field(default_factory=list)


@dataclass
class Evaluacion:
    componentes: List[Componente] = field(default_factory=list)
    observaciones: List[str] = field(default_factory=list)
    mecanismo: Dict = field(default_factory=dict)

    def add(self, *a, **k) -> Componente:
        c = Componente(*a, **k)
        self.componentes.append(c)
        return c


class Ctx:
    """Contexto de evaluación de un host."""

    def __init__(self, registros: Dict[str, Registro], benchmark: str, familia: str, config: Dict,
                 exclusion: Optional[Dict] = None, listas: Optional[Dict] = None, profunda: bool = False):
        self.r = registros
        self.benchmark = benchmark
        self.familia = familia
        self.config = config or {}
        self.exclusion = exclusion or {}
        self.listas = listas or {}
        self.profunda = profunda

    def reg(self, cid: str) -> Optional[Registro]:
        return self.r.get(cid)

    def problema(self, cid: str, rc_validos=None) -> Optional[str]:
        """Describe por qué un comando no es evidencia utilizable, o None si lo es.

        El texto empieza con un código entre corchetes (ver MOTIVOS)."""
        from .comandos import COMANDOS
        r = self.r.get(cid)
        if r is None:
            return f"[NO_RECOLECTADO] {cid}: no recolectado"
        no_conf = (self.config or {}).get("recolector_privilegiado_no_confiable")
        fallida = (self.config or {}).get("recoleccion_privilegiada_fallida")
        from .privilegiado import IDS_PRIVILEGIADOS
        if fallida and cid in IDS_PRIVILEGIADOS and not r.sudo:
            return f"[RECOLECCION_PRIVILEGIADA_FALLIDA] {cid}: {fallida}"
        if r.omitido and r.omitido.startswith("sin_sudo"):
            if no_conf:
                return f"[RECOLECTOR_PRIVILEGIADO_NO_CONFIABLE] {cid}: no ejecutado con privilegios; {no_conf}"
            return f"[PERMISOS_INSUFICIENTES] {cid}: no ejecutado; {r.omitido}"
        if r.omitido:
            return f"[PENDIENTE_AUDITORIA_PROFUNDA] {cid}: omitido ({r.omitido})"
        if r.rc == 124:
            return f"[TIMEOUT] {cid}: superó el tiempo máximo"
        if _RE_SUDO.search(r.stderr or ""):
            return f"[PERMISOS_INSUFICIENTES] {cid}: sudo -n no disponible para la cuenta de auditoría"
        no_legibles = [l.split("|", 1)[1] for l in (r.stdout or "").splitlines() if l.startswith("#NO_LEGIBLE|")]
        if no_legibles or "Permission denied" in (r.stderr or ""):
            det = ", ".join(no_legibles) or (r.stderr or "").strip()[:200]
            if no_conf and cid in IDS_PRIVILEGIADOS and not r.sudo:
                return f"[RECOLECTOR_PRIVILEGIADO_NO_CONFIABLE] {cid}: lectura incompleta sin privilegios ({det}); {no_conf}"
            return f"[PERMISOS_INSUFICIENTES] {cid}: lectura incompleta sin privilegios ({det})"
        if r.rc == 127 or "command not found" in (r.stderr or ""):
            return f"[COMANDO_INEXISTENTE] {cid}: comando inexistente en el host"
        validos = rc_validos if rc_validos is not None else COMANDOS[cid].rc_validos if cid in COMANDOS else (0,)
        if validos is not None and r.rc not in validos:
            return f"[RC_INESPERADO] {cid}: código de retorno {r.rc} ({(r.stderr or '').strip()[:160]})"
        if es_consulta_systemctl(cid):
            sin = [l.split("|", 1)[0] for l in self.lineas(cid) if l.count("|") == 2 and RE_SYSTEMD_SIN_ACCESO.search(l.split("|", 1)[1])]
            if sin:
                msg = next(m.group(0) for l in self.lineas(cid) for m in [RE_SYSTEMD_SIN_ACCESO.search(l.split("|", 1)[-1])] if m)
                origen = "como root (recolector privilegiado)" if r.sudo else "con la cuenta de auditoría sin privilegios"
                return (f"[SYSTEMD_NO_CONSULTABLE] {cid}: systemctl no pudo consultar a systemd {origen} ('{msg}') para "
                        f"{len(sin)} unidad(es) ({', '.join(sin[:6])}{'…' if len(sin) > 6 else ''}). No significa que el servicio esté "
                        "detenido ni activo: el estado no se puede determinar con esta evidencia"
                        + ("" if r.sudo else "; lo resuelve el recolector principal 1.8.0, que consulta systemctl como root") + ".")
        return None

    def omitido(self, cid: str) -> bool:
        """True solo si el comando se omitió por auditoría profunda no autorizada."""
        r = self.r.get(cid)
        return bool(r and r.omitido and r.omitido.startswith("auditoria_filesystem_profunda"))

    def sin_sudo(self, cid: str) -> bool:
        r = self.r.get(cid)
        return bool(r and "#SUDO=no" in (r.stdout or ""))

    def lineas(self, cid: str) -> List[str]:
        r = self.r.get(cid)
        return [] if r is None else [l for l in r.stdout.splitlines()
                                     if l.strip() and not l.startswith(("#SUDO=", "#NO_LEGIBLE|", "#NO_EXISTE|"))]

    @property
    def uid_min(self) -> int:
        l = self.lineas("sys.uid_min")
        try:
            return int(l[0].strip())
        except (IndexError, ValueError):
            return 1000


def agregar(componentes: List[Componente], permite_parcial: bool = True):
    """Devuelve (estado, tipo_parcial).

    permite_parcial=False para controles cuyos componentes son atributos de un único
    requisito (p. ej. modo/propietario/grupo de un archivo): una falla es NO CUMPLE.
    """
    activos = [c for c in componentes if c.estado not in (FUERA, INFO)]
    est = {c.estado for c in activos}
    if not activos:
        return MANUAL_R, None
    if est == {NA}:
        return NO_APLICA, None
    activos = [c for c in activos if c.estado != NA]
    est = {c.estado for c in activos}
    if FALLA in est:
        return (PARCIAL, "DESVIACION_PARCIAL") if (OK in est and permite_parcial) else (NO_CUMPLE, None)
    if ERROR in est:
        return ERROR_R, None
    if MANUAL in est:
        return MANUAL_R, None
    if ORG in est:
        return ORG_R, None
    return CUMPLE, None


def modo_ok(modo: str, mascara: int) -> bool:
    return (int(modo, 8) & mascara) == 0


def max_modo(mascara: int) -> str:
    return "%04o" % (0o777 & ~mascara)


def archivos_catconfig(lineas: List[str]) -> List[str]:
    """Archivos leídos por `systemd-analyze cat-config`, en su orden de precedencia (encabezados '# /ruta.conf')."""
    res = []
    for l in lineas:
        m = re.match(r"^#\s*(/\S+\.conf)\s*$", l.strip())
        if m and m.group(1) not in res:
            res.append(m.group(1))
    return res


def lista_archivos(archivos: List[str]) -> str:
    """'1) /a · 2) /b' (o 'ninguno')."""
    return " · ".join(f"{i}) {a}" for i, a in enumerate(archivos, 1)) or "ninguno"


def config_efectiva(lineas: List[str], seccion: Optional[str] = None, aceptar_comentado: bool = False):
    """Interpreta salida de `systemd-analyze cat-config` filtrada.

    Devuelve {clave: (valor, archivo, comentado)} con la última asignación efectiva.
    Si aceptar_comentado, una clave solo presente comentada se devuelve como valor por defecto.
    """
    archivo, sec = None, None
    efectivos, comentados = {}, {}
    for l in lineas:
        s = l.strip()
        m = re.match(r"^#\s*(/\S+\.conf)\s*$", s)
        if m:
            archivo, sec = m.group(1), None
            continue
        m = re.match(r"^\[(.+)\]$", s)
        if m:
            sec = m.group(1)
            continue
        if seccion and sec not in (None, seccion):
            continue
        m = re.match(r"^(#?)\s*([A-Za-z]+)\s*=\s*(.*?)\s*$", s)
        if m:
            (comentados if m.group(1) else efectivos)[m.group(2)] = (m.group(3), archivo, bool(m.group(1)))
    if aceptar_comentado:
        for k, v in comentados.items():
            efectivos.setdefault(k, v)
    return efectivos
