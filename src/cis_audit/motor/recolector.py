"""Generación del script recolector y lectura de su salida.

El script se ejecuta en el host con `ansible.builtin.raw` como
`printf %s <base64> | base64 -d | bash -s`: no se copia ningún archivo al host
ni se requiere Python remoto. Cada comando produce un bloque delimitado:

    @@CIS_BEGIN|<id>|sudo=<0|1>|<fecha>
    @@CIS_CMD|<comando>
    <stdout>
    @@CIS_STDERR|<id>
    <stderr>
    @@CIS_END|<id>|rc=<rc>|ms=<duración>

Los comandos profundos no autorizados producen `@@CIS_SKIP|<id>|<motivo>`.
"""

import base64
import re
import shlex
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .comandos import COMANDOS

VERSION_FORMATO = "1"

_CABECERA = r'''#!/bin/bash
# Recolector de evidencias CIS - SOLO LECTURA. Generado por cis_audit; no editar a mano.
# No escribe archivos, no modifica configuración, no instala ni reinicia nada.
# Elevación únicamente con "sudo -n" (nunca solicita contraseña).
export LC_ALL=C LANG=C PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
umask 077
echo "@@CIS_RECOLECTOR|formato={formato}|familia={familia}|benchmark={benchmark}|profunda={profunda}|sin_sudo={sin_sudo}|matriz={matriz}"
__cis_run() {{
  local id="$1" sudo="$2" t="$3" cmd="$4" err rc t0
  t0=$(date +%s%N)
  printf '@@CIS_BEGIN|%s|sudo=%s|%s\n' "$id" "$sudo" "$(date -Is)"
  printf '@@CIS_CMD|%s\n' "$cmd"
  exec 3>&1
  err=$( {{ timeout "$t" bash -c "$cmd" 1>&3; }} 2>&1 ); rc=$?
  exec 3>&-
  printf '\n@@CIS_STDERR|%s\n%s\n' "$id" "$err"
  printf '@@CIS_END|%s|rc=%s|ms=%s\n' "$id" "$rc" "$(( ($(date +%s%N) - t0) / 1000000 ))"
}}
__cis_skip() {{
  printf '@@CIS_BEGIN|%s|sudo=%s|%s\n@@CIS_SKIP|%s|%s\n@@CIS_END|%s|rc=-1|ms=0\n' "$1" "$2" "$(date -Is)" "$1" "$3" "$1"
}}
. /etc/os-release 2>/dev/null
case "$ID $ID_LIKE" in
  *rhel*|*fedora*|*centos*) __fam=rhel ;;
  *sles*|*suse*) __fam=sles ;;
  *) __fam=desconocida ;;
esac
if [ "$__fam" != "{familia}" ] || [ "$ID" != "{os_id}" ] || [ "${{VERSION_ID%%.*}}" != "{major}" ]; then
  echo "@@CIS_FATAL|sistema_incompatible|esperado={os_id} {major}|detectado=$ID $VERSION_ID"
  exit 0
fi
'''


MOTIVO_SIN_SUDO = "sin_sudo_n: la cuenta no dispone de sudo -n (determinado en el precheck)"

# Invocación REAL de sudo como comando (no como argumento o nombre de paquete): `sudo` / `/usr/bin/sudo` en posición
# de comando, al inicio o tras ; & | ( { $( ` o palabras de control de bash, admitiendo asignaciones previas
# (`LC_ALL=C /usr/bin/sudo -V`). `rpm -q sudo` o `grep … /etc/sudoers` NO son invocaciones.
_RE_INVOCA_SUDO = re.compile(
    r"(?:^|[;&|({`]|\$\(|\b(?:then|do|else|elif)\b)\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:/usr/bin/|/bin/)?sudo(?=\s|$|;)")


def invoca_sudo(cmd: str) -> bool:
    """True si el texto del comando ejecuta sudo (detección tokenizada; solo salvaguarda de coherencia)."""
    return bool(_RE_INVOCA_SUDO.search(cmd))


def generar_script(benchmark: str, comandos: List[str], paquetes: List[str], profunda: bool, timeout: int,
                   timeout_fs: int, matriz_sha: str = "", sin_sudo: bool = False) -> str:
    """Script del benchmark: solo los comandos que exigen las filas YES (ver motor/seleccion.py).

    sin_sudo=True (precheck sin `sudo -n`): no se invoca sudo en absoluto. Los comandos que lo
    exigen se omiten (-> ERROR DE AUDITORÍA / PERMISOS_INSUFICIENTES) y los que solo lo usan si
    está disponible se ejecutan sin privilegios. Así no se generan intentos fallidos de sudo.

    Criterio: la decisión usa SOLO la metadata explícita `Comando.sudo`. Nunca una
    búsqueda de la cadena "sudo" en el texto (que descartaba `rpm -q … sudo …`). Como salvaguarda fail-closed,
    un comando con `sudo=False` cuyo texto invoque sudo (detección tokenizada) aborta la generación, y en modo
    sin_sudo ninguna línea `__cis_run` puede contener una invocación de sudo. Los grupos `priv.*` (solo del
    recolector privilegiado firmado) nunca entran en este script.
    """
    from .comandos_priv import SOLO_PRIVILEGIADO
    from .comandos import _SUDO_PROBE
    from .seleccion import FAMILIA, OS_ID
    familia = FAMILIA[benchmark]
    os_id, major = OS_ID[benchmark]
    lineas = [_CABECERA.format(formato=VERSION_FORMATO, familia=familia, benchmark=benchmark, os_id=os_id, major=major,
                               profunda=int(profunda), sin_sudo=int(sin_sudo), matriz=matriz_sha[:16])]
    for cid in comandos:
        if cid in SOLO_PRIVILEGIADO:
            raise ValueError(f"{cid}: comando exclusivo del recolector privilegiado; no puede ir en el recolector normal")
        c = COMANDOS[cid]
        cmd = c.cmd.format(paquetes=" ".join(paquetes)) if cid == "pkg.rpm" else c.cmd
        if not c.sudo and invoca_sudo(cmd):
            raise ValueError(f"{cid}: invoca sudo pero su definición declara sudo=False (metadata incoherente)")
        if c.profunda and not profunda:
            lineas.append(f"__cis_skip {c.id} {int(c.sudo)} 'auditoria_filesystem_profunda=false'")
            continue
        if sin_sudo and c.sudo:
            if _SUDO_PROBE in cmd:
                cmd = cmd.replace(_SUDO_PROBE, 'S=""; echo "#SUDO=no"; ')
            else:
                lineas.append(f"__cis_skip {c.id} {int(c.sudo)} {shlex.quote(MOTIVO_SIN_SUDO)}")
                continue
        if sin_sudo and invoca_sudo(cmd):
            raise ValueError(f"{cid}: el recolector sin sudo contendría una invocación de sudo")
        t = c.timeout or (timeout_fs if c.recorre_fs else timeout)
        lineas.append(f"__cis_run {c.id} {int(c.sudo)} {t} {shlex.quote(cmd)}")
    lineas.append('echo "@@CIS_FIN"')
    return "\n".join(lineas) + "\n"


def comando_raw(script: str) -> str:
    """Comando de una línea para ansible.builtin.raw (sin archivos en el host)."""
    b64 = base64.b64encode(script.encode()).decode()
    return f"printf %s {b64} | base64 -d | bash -s"


# ----------------------------------------------------------------- lectura

@dataclass
class Registro:
    id: str
    comando: str = ""
    stdout: str = ""
    stderr: str = ""
    rc: Optional[int] = None
    sudo: bool = False
    fecha: str = ""
    ms: int = 0
    omitido: Optional[str] = None  # motivo si no se ejecutó

    @property
    def lineas(self) -> List[str]:
        return [l for l in self.stdout.splitlines() if l.strip()]


@dataclass
class Recoleccion:
    familia: Optional[str] = None
    benchmark: Optional[str] = None
    profunda: bool = False
    sin_sudo: bool = False
    fatal: Optional[str] = None
    completo: bool = False
    registros: Dict[str, Registro] = field(default_factory=dict)
    version: Optional[str] = None  # solo en la cabecera del recolector privilegiado


_RE_BEGIN = re.compile(r"^@@CIS_BEGIN\|([^|]+)\|sudo=(\d)\|(.*)$")
_RE_END = re.compile(r"^@@CIS_END\|([^|]+)\|rc=(-?\d+)\|ms=(\d+)$")


def parsear(salida: str) -> Recoleccion:
    rec = Recoleccion()
    actual: Optional[Registro] = None
    zona = None  # "out" | "err"
    buf_out: List[str] = []
    buf_err: List[str] = []
    for linea in salida.replace("\r\n", "\n").split("\n"):
        if linea.startswith("@@CIS_RECOLECTOR|"):
            campos = dict(p.split("=", 1) for p in linea.split("|")[1:] if "=" in p)
            rec.familia = campos.get("familia")
            rec.benchmark = campos.get("benchmark")
            rec.profunda = campos.get("profunda") == "1"
            rec.sin_sudo = campos.get("sin_sudo") == "1"
            rec.version = campos.get("version")
            continue
        if linea.startswith("@@CIS_FATAL|"):
            rec.fatal = linea[len("@@CIS_FATAL|"):]
            continue
        if linea == "@@CIS_FIN":
            rec.completo = True
            continue
        m = _RE_BEGIN.match(linea)
        if m:
            actual = Registro(id=m.group(1), sudo=m.group(2) == "1", fecha=m.group(3))
            zona, buf_out, buf_err = "out", [], []
            continue
        if actual is None:
            continue
        if linea.startswith("@@CIS_CMD|"):
            actual.comando = linea[len("@@CIS_CMD|"):]
            continue
        if linea.startswith("@@CIS_SKIP|"):
            actual.omitido = linea.split("|", 2)[2]
            continue
        if linea.startswith("@@CIS_STDERR|"):
            zona = "err"
            continue
        m = _RE_END.match(linea)
        if m and m.group(1) == actual.id:
            if buf_out and buf_out[-1] == "":
                buf_out.pop()  # salto de línea añadido por el recolector
            actual.stdout = "\n".join(buf_out)
            actual.stderr = "\n".join(buf_err).strip()
            actual.rc = int(m.group(2))
            actual.ms = int(m.group(3))
            rec.registros[actual.id] = actual
            actual, zona = None, None
            continue
        (buf_out if zona == "out" else buf_err).append(linea)
    return rec


