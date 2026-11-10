"""Recolector privilegiado controlado (cis-audit-collect).

Genera el script fijo `/usr/local/libexec/cis-audit/cis-audit-collect`. Infraestructura lo instala mediante un cambio
controlado (docs/Instalacion_Collectors.pdf) y lo autoriza en sudoers con digest SHA-256 y sin argumentos. Este proyecto
NUNCA lo instala.

Propiedades del script generado (verificadas por problemas_script antes de escribirlo):
- sin argumentos (aborta si recibe alguno), stdin cerrado, entorno reconstruido con `env -i`,
  PATH/LC_ALL/IFS fijos, umask 0077, `bash -p`;
- no lee /etc/os-release con `source` (solo grep), no invoca sudo, su ni shells de login;
- solo contiene los comandos READ-ONLY que necesitan privilegios (ids de IDS_PRIVILEGIADOS), con
  el mismo texto que el catálogo `comandos.py` sin el prefijo `sudo -n`; los recorridos profundos
  de filesystem NO se incluyen;
- no escribe archivos ni temporales; la salida va solo a stdout, con el mismo formato delimitado
  del recolector normal, y pasa por un filtro de redacción antes de salir del contexto root;
- GRUB: solo emite indicadores normalizados (true/false); el hash nunca sale del proceso root;
- redacción fail-closed: autoprueba del filtro antes de recolectar y comprobación de su código de
  salida (PIPESTATUS) al final; si falla, emite @@CIS_FATAL y termina con rc 3;
- es determinista: el mismo código fuente produce el mismo SHA-256.
"""

import hashlib
import shlex
from typing import Dict, List, Optional

from .comandos import COMANDOS, _SUDO_PROBE
from .recolector import VERSION_FORMATO

RUTA = "/usr/local/libexec/cis-audit/cis-audit-collect"
DIRECTORIOS_PADRE = ("/usr/local/libexec/cis-audit", "/usr/local/libexec", "/usr/local", "/usr", "/")
# Directorios del PATH fijo del script (y /bin/bash): deben ser de root y no escribibles por grupo/otros.
DIRECTORIOS_PATH = ("/usr/sbin", "/usr/bin", "/sbin", "/bin")
VERSION = "1.8.0"                      # única versión vigente (config/audit.yml -> recolector_privilegiado)
# Enlaces simbólicos de sistema aceptados (usr-merge). Se acepta el enlace solo si es de root y su
# destino (readlink -f) es exactamente la ruta indicada; el destino se verifica como cualquier directorio.
# El recolector y sus directorios padre NUNCA pueden ser enlaces.
ENLACES_PERMITIDOS = {"/bin": "/usr/bin", "/sbin": "/usr/sbin"}

# Únicos comandos que se ejecutan como root. Orden fijo: los grupos se fueron añadiendo al final, sin alterar las líneas
# anteriores. Se excluyen los recorridos profundos (fs.world_writable, fs.sin_dueno, fs.suid_sgid, usr.dotfiles_completo):
# los cubre el recolector de filesystem profundo.
IDS_1_1_0 = (
    "aide.cron",
    "grub.archivos", "grub.password", "grub.grubby", "grub.cfg_linux",
    "apparmor.status",
    "sysctl.archivos",
    "auditd.conf", "auditd.reglas_disco", "auditd.audit_rules", "auditd.reglas_cargadas", "auditd.estado",
    "auditd.sudo_logfile",
    "journald.conf", "journald.tmpfiles",
    "logs.permisos",
    "usr.shadow_vacios", "usr.root_path", "usr.homes", "usr.dotfiles_raiz",
)
# p1.ssh_permisos y p1.sysctl_archivos reutilizan el id y el formato de la Parte 1 (la fusión los reemplaza).
IDS_NUEVOS_1_2_0 = ("priv.ssh_efectiva", "p1.ssh_permisos", "priv.sudo_politica", "priv.cuentas", "p1.sysctl_archivos")
IDS_1_2_0 = IDS_1_1_0 + IDS_NUEVOS_1_2_0
IDS_NUEVOS_1_3_0 = ("priv.kmod_config", "priv.motd", "priv.pwquality")
IDS_NUEVOS_1_4_0 = ("priv.rsyslog_validacion", "priv.logrotate", "priv.journal_archivos", "priv.unconfined_detalle",
                    "priv.suid_rpm", "priv.root_path_dirs")
IDS_NUEVOS_1_5_0 = ("priv.authorized_keys",)
# 1.6.0: rsyslog.conf y p1.kmod reutilizan el id y el formato del recolector sin sudo (la fusión los reemplaza). En SLES
# /etc/rsyslog.d/*.conf y /etc/modprobe.d/*.conf pueden ser solo de root (lectura incompleta sin privilegios).
IDS_NUEVOS_1_6_0 = ("rsyslog.conf", "p1.kmod")
# 1.8.0: consultas de servicios (systemctl is-enabled/is-active) y chrony reutilizan el id y el formato del recolector sin
# sudo. En SLES 15 la cuenta de auditoría recibe "Failed to connect to bus: Operation not permitted" al consultar systemd y
# /etc/chrony.conf es solo de root; como root systemctl usa el socket privado de systemd (sin D-Bus).
IDS_NUEVOS_1_8_0 = ("auditd.servicio", "rsyslog.servicio", "journald.servicio", "journald.remoto", "aide.timer",
                    "coredump.servicio", "p1.unidades", "p1.chrony")
# Evidencia que entrega el recolector vigente (45 grupos). Solo estos ids se toman de raw/privilegiado.out.
IDS_PRIVILEGIADOS = IDS_1_2_0 + IDS_NUEVOS_1_3_0 + IDS_NUEVOS_1_4_0 + IDS_NUEVOS_1_5_0 + IDS_NUEVOS_1_6_0 + IDS_NUEVOS_1_8_0
# Versión mínima del recolector que entrega cada grupo: si la evidencia procede de un recolector anterior, el dato
# ausente se informa como REQUIERE_EXTENSION_RECOLECTOR (nunca como incumplimiento).
VERSION_MINIMA = {**{c: "1.1.0" for c in IDS_1_1_0}, **{c: "1.2.0" for c in IDS_NUEVOS_1_2_0}, **{c: "1.3.0" for c in IDS_NUEVOS_1_3_0},
                  **{c: "1.4.0" for c in IDS_NUEVOS_1_4_0}, **{c: "1.5.0" for c in IDS_NUEVOS_1_5_0},
                  **{c: "1.6.0" for c in IDS_NUEVOS_1_6_0}, **{c: "1.8.0" for c in IDS_NUEVOS_1_8_0}}


def version_tupla(v: Optional[str]):
    try:
        return tuple(int(x) for x in (v or "").split("."))
    except ValueError:
        return ()
PROHIBIDOS_EN_SCRIPT = ("fs.world_writable", "fs.sin_dueno", "fs.suid_sgid", "usr.dotfiles_completo")

# Para el comando de sudoers solo se emite la directiva logfile (no la línea Defaults completa).
_SUSTITUCIONES = {
    "auditd.sudo_logfile": ("grep -rhE '^[[:space:]]*Defaults.*logfile' /etc/sudoers /etc/sudoers.d/ | "
                            "grep -oE 'logfile[[:space:]]*=[[:space:]]*(\"[^\"]*\"|[^,[:space:]]+)'"),
    "auditd.reglas_disco": "grep -H '' /etc/audit/rules.d/*.rules",
}

# Redacción dentro del script. Segunda barrera: los comandos ya emiten solo datos mínimos (GRUB:
# indicadores; shadow: nombres). El controlador vuelve a redactar al persistir (tercera barrera).
_REDACCION_SED = [
    r"s#-----BEGIN [A-Z ]*PRIVATE KEY-----.*#<REDACTADO>#",
    r"s#(grub\.pbkdf2\.[a-z0-9]+)\.[0-9]+\.[0-9A-Fa-f.]+#\1.<REDACTADO>#g",
    r"s#(password_pbkdf2[[:space:]]+[^[:space:]]+[[:space:]]+)[^<[:space:]][^[:space:]]*#\1<REDACTADO>#g",
    r"s#\$(1|2[abxy]?|5|6|y|gy|7)\$[^[:space:]:]{8,}#<REDACTADO>#g",
    r"s#([A-Za-z][A-Za-z0-9+.-]*://)[^/[:space:]:@]+:[^/[:space:]@]+@#\1<REDACTADO>@#g",
    r"s#(\b(password|passwd|pwd|secret|token|apikey|api_key)[[:space:]]*[=:][[:space:]]*\"?)[^[:space:]\"',)]+#\1<REDACTADO>#Ig",
]

_CABECERA = r'''#!/bin/bash -p
# =============================================================================
# cis-audit-collect {version} — Recolector PRIVILEGIADO de evidencias CIS (SOLO LECTURA)
# =============================================================================
# Generado por cis_audit (python3 src/auditar.py generar-recolector-privilegiado).
# NO EDITAR: cualquier cambio altera el SHA-256 autorizado en sudoers y el
# precheck dejará de usarlo (RECOLECTOR_PRIVILEGIADO_NO_CONFIABLE).
#
# Instalación (solo Infraestructura, cambio controlado):
#   {ruta}  root:root 0755; directorios padre root y no escribibles por grupo/otros.
#   sudoers: srvinnops ALL=(root) NOPASSWD: sha256:<digest> {ruta} ""
#
# Garantías:
#   - No acepta argumentos ni lee stdin ni variables del invocador.
#   - No escribe archivos, no crea temporales, no cambia configuración, no reinicia
#     servicios, no refresca repositorios, no ejecuta remediaciones.
#   - No ejecuta ni hace "source" de archivos de inicio (PATH de root: análisis estático).
#   - No incluye recorridos completos de filesystem (auditoría profunda: fase aparte).
#   - GRUB y /etc/shadow: solo indicadores o nombres; nunca hashes ni líneas completas.
#   - Filtro de redacción (segunda barrera) en modo fail-closed: si falla, no hay evidencia.
{extra}# =============================================================================
set -u
umask 0077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
LC_ALL=C
LANG=C
IFS=$' \t\n'
export PATH LC_ALL LANG
unset BASH_ENV ENV CDPATH GLOBIGNORE PS4
exec 0</dev/null

if [ "$#" -ne 0 ]; then echo "@@CIS_FATAL|argumentos_no_admitidos"; exit 2; fi
if [ "$(id -u)" != "0" ]; then echo "@@CIS_FATAL|requiere_root"; exit 2; fi

# /etc/os-release se LEE con grep (nunca se ejecuta con "source").
__osv() {{ grep -m1 -E "^$1=" /etc/os-release | cut -d= -f2- | tr -d '"'; }}
__id="$(__osv ID)"; __ver="$(__osv VERSION_ID)"; __major="${{__ver%%.*}}"
case "$__id:$__major" in
  rhel:8) __fam=rhel; __bench=RHEL8 ;;
  rhel:9) __fam=rhel; __bench=RHEL9 ;;
  ol:8)   __fam=rhel; __bench=ORACLE8 ;;
  ol:9)   __fam=rhel; __bench=ORACLE9 ;;
  sles:15) __fam=sles; __bench=SLES15 ;;
  *) echo "@@CIS_FATAL|sistema_no_soportado|detectado=$__id $__ver"; exit 0 ;;
esac

__cis_run() {{
  local id="$1" t="$2" cmd="$3" err rc t0
  t0=$(date +%s%N)
  printf '@@CIS_BEGIN|%s|sudo=1|%s\n' "$id" "$(date -Is)"
  printf '@@CIS_CMD|%s\n' "$cmd"
  exec 3>&1
  err=$( {{ timeout "$t" env -i PATH="$PATH" LC_ALL=C LANG=C /bin/bash -p -c "$cmd" 1>&3; }} 2>&1 ); rc=$?
  exec 3>&-
  printf '\n@@CIS_STDERR|%s\n%s\n' "$id" "$err"
  printf '@@CIS_END|%s|rc=%s|ms=%s\n' "$id" "$rc" "$(( ($(date +%s%N) - t0) / 1000000 ))"
}}

__redactar() {{
  sed -u -E {sed}
}}

# Autoprueba del filtro de redacción (fail-closed): si no redacta, no se recolecta nada.
__canario="$(printf '%s\n' 'h=$6$CANARIOcanario01234' 'GRUB2_PASSWORD=grub.pbkdf2.sha512.10000.AB12.CD34' | __redactar)" || __canario=""
if [ "$__canario" != $'h=<REDACTADO>\nGRUB2_PASSWORD=grub.pbkdf2.sha512.<REDACTADO>' ]; then
  echo "@@CIS_FATAL|redaccion_no_operativa"; exit 3
fi

{{
echo "@@CIS_RECOLECTOR|formato={formato}|familia=$__fam|benchmark=$__bench|profunda=0|sin_sudo=0|privilegiado=1|version={version}"
'''


# Equivalente a pipefail, explícito: el estado del filtro no queda oculto por el "exit" final.
_PIE = r'''__st=("${PIPESTATUS[@]}")
if [ "${__st[1]}" -ne 0 ]; then printf '\n@@CIS_FATAL|redaccion_fallida|rc=%s\n' "${__st[1]}"; exit 3; fi
if [ "${__st[0]}" -ne 0 ]; then printf '\n@@CIS_FATAL|recoleccion_interrumpida|rc=%s\n' "${__st[0]}"; exit 3; fi
exit 0'''


_EXTRA_1_3_0 = """# 1.3.0 (añade 3 grupos tras los 25 ids de 1.2.0, que no cambian; solo los 8 ERROR reales de la Parte 1):
#   - priv.kmod_config: install/blacklist de 6 módulos CIS en modprobe.d (5 directorios); destino true|false|OTRO.
#   - priv.motd: indicadores 0/1 de información del sistema en /etc/motd{,.d/*}; nunca el texto.
#   - priv.pwquality: solo minlen, minclass y [dulo]credit de pwquality.conf(.d).
"""
_EXTRA_1_4_0 = """# 1.4.0 (añade 6 grupos tras los 28 ids de 1.3.0, que no cambian; revisión de la guía manual 2026-10-14):
#   - priv.rsyslog_validacion: rsyslogd -N1 (verificación de sintaxis; no arranca el demonio).
#   - priv.logrotate: configuración sin comentarios + logrotate -d (depuración: no rota, no guarda estado, no ejecuta scripts).
#   - priv.journal_archivos: metadatos de /var/log/journal y /run/log/journal (profundidad 2); nunca su contenido.
#   - priv.unconfined_detalle: PID, comm, contexto, ejecutable y paquete RPM de procesos unconfined_service_t.
#   - priv.suid_rpm: SUID/SGID declarados en la base RPM + rpm -V --noscripts --nodeps de sus paquetes.
#   - priv.root_path_dirs: readlink -e / stat -L de directorios candidatos del PATH de root (sin ejecutar perfiles, R6).
"""
_EXTRA_1_5_0 = """# 1.5.0 (añade 1 grupo tras los 34 ids de 1.4.0, que no cambian):
#   - priv.authorized_keys: llaves autorizadas de las cuentas de automatización (PRE-3); por llave solo bits,
#     fingerprint SHA256 y tipo (ssh-keygen -l); nunca la llave, su comentario ni sus opciones.
"""
_EXTRA_1_6_0 = """# 1.6.0 (añade 2 grupos tras los 35 ids de 1.5.0, que no cambian; ejecución de los 4 servidores 2026-10-16):
#   - rsyslog.conf (root): /etc/rsyslog.conf y /etc/rsyslog.d/*.conf sin comentarios (en SLES pueden ser solo de root).
#   - p1.kmod (root): módulos de la Parte 1 (incluye sctp y dccp): disponibles, cargados y directivas install/blacklist
#     de modprobe.d; nunca invoca modprobe.
"""
_EXTRA_1_7_0 = """# 1.7.0 (mismos 37 grupos; ajustes de auditoría 2026-10-26):
#   - usr.root_path: además de las líneas de PATH y de estructura, emite las líneas return/exit/exec/logout de los archivos
#     de inicio de root y la marca FLUJO|1, para demostrar estáticamente que una línea que modifica el PATH se ejecuta
#     (CIS 5.4.2.5). Sigue sin ejecutar ni hacer source del perfil de root (R6).
"""
_EXTRA_1_8_0 = """# 1.8.0 (añade 8 grupos; ajustes de auditoría 2026-10-27):
#   - auditd.servicio, rsyslog.servicio, journald.servicio, journald.remoto, aide.timer, coredump.servicio, p1.unidades:
#     solo 'systemctl is-enabled' y 'systemctl is-active' de unidades fijas (consultas; nunca cambian el estado).
#   - p1.chrony (root): líneas server/pool/sourcedir/confdir/include de /etc/chrony.conf y /etc/chrony.d/*.conf.
#   - usr.homes: añade el grupo del directorio home (stat %G).
#   - auditd.kmod_symlinks no cambia aquí (recolector sin sudo): rutas reales de lsmod/rmmod/insmod/modinfo/modprobe/depmod.
"""
_EXTRA_1_2_0 = """# 1.2.0 (añade 5 grupos tras los 20 comandos de 1.1.0, que no cambian):
#   - priv.ssh_efectiva: sshd -t/-T, solo claves CIS; allow/deny y criterios Match sin valores.
#   - p1.ssh_permisos (root): metadatos y ACL de sshd_config(.d) y llaves de host; nunca su contenido.
#   - priv.sudo_politica: indicadores normalizados; nunca líneas de sudoers. 'sudo -V' solo para el
#     valor por defecto de timestamp_timeout (no ejecuta ningún otro comando).
#   - priv.cuentas: /etc/shadow sin campo de contraseña (indicadores 0/1 y campos de envejecimiento).
#   - p1.sysctl_archivos (root): solo las 28 claves sysctl CIS y los archivos que las contienen.
"""


def comando_root(cid: str) -> str:
    """Texto del comando para ejecutarse como root dentro del script (sin sudo)."""
    from . import comandos_priv as cpv
    nuevos = {"priv.ssh_efectiva": cpv.SSH_EFECTIVA, "priv.sudo_politica": cpv.SUDO_POLITICA, "priv.cuentas": cpv.CUENTAS,
              "p1.ssh_permisos": cpv.SSH_PERMISOS_ROOT, "p1.sysctl_archivos": cpv.SYSCTL_ARCHIVOS_ROOT,
              "priv.kmod_config": cpv.KMOD_CONFIG, "priv.motd": cpv.MOTD, "priv.pwquality": cpv.PWQUALITY,
              "priv.rsyslog_validacion": cpv.RSYSLOG_VALIDACION, "priv.logrotate": cpv.LOGROTATE,
              "priv.journal_archivos": cpv.JOURNAL_ARCHIVOS, "priv.unconfined_detalle": cpv.UNCONFINED_DETALLE,
              "priv.suid_rpm": cpv.SUID_RPM, "priv.root_path_dirs": cpv.ROOT_PATH_DIRS,
              "priv.authorized_keys": cpv.AUTHORIZED_KEYS}
    if cid in nuevos:
        return nuevos[cid]
    if cid in _SUSTITUCIONES:
        return _SUSTITUCIONES[cid]
    cmd = COMANDOS[cid].cmd
    if _SUDO_PROBE in cmd:
        cmd = 'echo "#SUDO=root"; ' + cmd.replace(_SUDO_PROBE, "").replace("$S ", "")
    cmd = cmd.replace("sudo -n ", "")
    return cmd


def generar(timeout: int = 120) -> str:
    """Script del recolector vigente (determinista: el mismo código fuente produce el mismo SHA-256)."""
    from . import comandos_priv as cpv
    sed = " ".join(f"-e {shlex.quote(e)}" for e in _REDACCION_SED)
    extra = _EXTRA_1_2_0 + _EXTRA_1_3_0 + _EXTRA_1_4_0 + _EXTRA_1_5_0 + _EXTRA_1_6_0 + _EXTRA_1_7_0 + _EXTRA_1_8_0
    partes = [_CABECERA.format(version=VERSION, ruta=RUTA, formato=VERSION_FORMATO, sed=sed, extra=extra)]
    for cid in IDS_PRIVILEGIADOS:
        c = COMANDOS[cid]
        fams = set(c.familias)
        linea = f"__cis_run {cid} {c.timeout or timeout} {shlex.quote(comando_root(cid))}"
        if cid == "priv.ssh_efectiva":  # Audit v4 (OL/RHEL 8): sshd -T $OPTIONS $CRYPTO_POLICY (lectura estática)
            partes.append(f'if [ "$__fam" = rhel ] && [ "$__major" = 8 ]; then '
                          f'__cis_run {cid} {c.timeout or timeout} {shlex.quote(cpv.SSH_EFECTIVA_OL8)}; else {linea}; fi')
            continue
        if fams == {"rhel"}:
            partes.append(f'if [ "$__fam" = rhel ]; then {linea}; fi')
        elif fams == {"sles"}:
            partes.append(f'if [ "$__fam" = sles ]; then {linea}; fi')
        else:
            partes.append(linea)
    partes.append('echo "@@CIS_FIN"')
    partes.append("} 2>&1 | __redactar")
    partes.append(_PIE)
    return "\n".join(partes) + "\n"


def sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def normalizar_sudoers(texto: str) -> str:
    """Contenido efectivo normalizado (igual que el awk de priv.sudo_politica): sin comentarios ni vacías, espacios colapsados."""
    import re
    lineas = []
    for l in texto.splitlines():
        l = l.strip()
        if not l or l.startswith("#"):
            continue
        lineas.append(re.sub(r"[ \t]+", " ", l))
    return "".join(x + "\n" for x in lineas)


def sudoers(digest: str) -> str:
    """Archivo /etc/sudoers.d/cis_audit propuesto para un digest (plantilla templates/sudoers_cis_audit.plantilla)."""
    from pathlib import Path
    plantilla = (Path(__file__).resolve().parents[3] / "templates" / "sudoers_cis_audit.plantilla").read_text(encoding="utf-8")
    return plantilla.replace("<SHA256_DEL_SCRIPT>", digest)


def sha_sudoers_archivo(sudoers: str) -> str:
    """SHA-256 de los BYTES EXACTOS del archivo sudoers (identidad principal de la instrumentación, = sha256sum)."""
    return sha256(sudoers)


def sha_instrumentacion(sudoers: str) -> str:
    """SHA-256 SEMÁNTICO (contenido efectivo normalizado) de /etc/sudoers.d/cis_audit: solo comprobación adicional."""
    return sha256(normalizar_sudoers(sudoers))


def problemas_script(texto: str) -> List[str]:
    """Verificaciones estáticas del script generado (las ejecutan las pruebas y el generador)."""
    from .seguridad import problemas_comando
    p = []
    cuerpo = "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("#"))
    import re
    if re.search(r"(^|[\s;&|(])sudo\s", cuerpo):
        p.append("contiene 'sudo': invoca sudo")
    # Única invocación de sudo admitida (1.2.0): '/usr/bin/sudo -V' para leer el timestamp_timeout por defecto.
    resto = cuerpo.replace("|sudo=1|", "").replace("LC_ALL=C /usr/bin/sudo -V |", "").replace("[ -x /usr/bin/sudo ]", "")
    if re.search(r"\bsudo\b", resto):
        p.append("invocación de sudo distinta de '/usr/bin/sudo -V'")
    if "__cis_run priv." in texto:
        from . import comandos_priv as cpv
        if re.search(r"print[^;}]*\$2|print[^;}]*\bp\b", cpv.CUENTAS):
            p.append("priv.cuentas imprime el campo de contraseña")
        # Se excluyen solo: la asignación interna l=$0 y la normalización de la instrumentación, cuya salida va a sha256sum.
        sud = cpv.SUDO_POLITICA.replace("l=$0;", "").replace(cpv._AWK_NORMALIZA + "' \"$f\" | sha256sum", "")
        if re.search(r'print\s*(\$0|l\b|[;}])|"\s+l\s*[;})]|\$0\s*[;}]', sud):
            p.append("priv.sudo_politica imprime líneas de sudoers")
        for g in IDS_NUEVOS_1_2_0:
            if f"__cis_run {g} " not in texto:
                p.append(f"falta el grupo {g}")
    if "__cis_run priv.kmod_config " in texto:
        from . import comandos_priv as cpv
        for g in IDS_NUEVOS_1_3_0:
            if f"__cis_run {g} " not in texto:
                p.append(f"falta el grupo {g}")
        # Salida cerrada: kmod nunca imprime a[3] (texto del install); MOTD solo usa el contenido para grep;
        # pwquality solo imprime clave y valor.
        if re.search(r"print[^}]*a\[[3-9]\]", cpv.KMOD_CONFIG):
            p.append("priv.kmod_config imprime el texto de un install")
        if re.sub(r"printf '%s' \"\$c\" \| grep", "", cpv.MOTD).count("$c") != 0:   # $c solo como entrada de grep
            p.append("priv.motd usa el contenido del banner fuera de grep")
        if re.search(r"\b(cat|tee|cp|dd)\b", cpv.MOTD + cpv.KMOD_CONFIG + cpv.PWQUALITY):
            p.append("grupos 1.3.0: binario no permitido (cat/tee/cp/dd)")
        if re.search(r"(^|[\s;&|(])(modprobe|insmod|rmmod)\s", cpv.KMOD_CONFIG):
            p.append("priv.kmod_config invoca modprobe")
    for patron, motivo in ((" su ", "invoca su"), ("bash -l", "shell de login"),
                           ("-Hiu", "sesión de login de root"), ("source ", "source"), (". /etc/os-release", "source de os-release"),
                           ("mktemp", "archivo temporal"), ("/tmp/", "ruta temporal"), ("eval ", "eval"),
                           ("augenrules", "augenrules"), ("aide --", "ejecución de aide")):
        if patron in cuerpo:
            p.append(f"contiene '{patron.strip()}': {motivo}")
    for cid in PROHIBIDOS_EN_SCRIPT:
        if f"__cis_run {cid} " in texto:
            p.append(f"incluye el recorrido profundo {cid}")
    if "__cis_run priv.rsyslog_validacion " in texto:
        from . import comandos_priv as cpv
        for g in IDS_NUEVOS_1_4_0:
            if f"__cis_run {g} " not in texto:
                p.append(f"falta el grupo {g}")
        g14 = cpv.RSYSLOG_VALIDACION + cpv.LOGROTATE + cpv.JOURNAL_ARCHIVOS + cpv.UNCONFINED_DETALLE + cpv.SUID_RPM + cpv.ROOT_PATH_DIRS
        if re.search(r"\b(cat|tee|cp|dd|mv|rm|chmod|chown|systemctl|kill)\b", g14):
            p.append("grupos 1.4.0: binario no permitido")
        if re.search(r"rpm\s+-V(?![^;|]*--noscripts)", cpv.SUID_RPM):
            p.append("priv.suid_rpm: rpm -V sin --noscripts (ejecutaría %verifyscript)")
        if not re.search(r"logrotate\\?\"?\s+-d\b|\$L\\?\"?\s+-d\b", cpv.LOGROTATE):
            p.append("priv.logrotate: logrotate sin -d (modo depuración)")
        if not re.search(r"-N1\b", cpv.RSYSLOG_VALIDACION):
            p.append("priv.rsyslog_validacion: rsyslogd sin -N1")
    if "__cis_run priv.authorized_keys " in texto:
        from . import comandos_priv as cpv
        ak = cpv.AUTHORIZED_KEYS
        if re.search(r"\b(cat|tee|cp|dd|mv|rm|chmod|chown|head|tail|sed)\b", ak):
            p.append("priv.authorized_keys: binario no permitido (el contenido de authorized_keys nunca se transcribe)")
        if not all(re.match(r"ssh-keygen -l\b", x) for x in re.findall(r"ssh-keygen[^;|]*", ak)):
            p.append("priv.authorized_keys: ssh-keygen distinto de 'ssh-keygen -l' (solo fingerprint)")
        if re.search(r'\{print\b[^}]*\$(?!1\b|2\b|NF\b)[0-9A-Za-z]', ak.split("ssh-keygen", 1)[1]):
            p.append("priv.authorized_keys: imprime campos de ssh-keygen distintos de bits, fingerprint y tipo")
    if "__cis_run rsyslog.conf " in texto:
        g16 = comando_root("rsyslog.conf") + comando_root("p1.kmod")
        for g in IDS_NUEVOS_1_6_0:
            if f"__cis_run {g} " not in texto:
                p.append(f"falta el grupo {g}")
        if re.search(r"\b(cat|tee|cp|dd|mv|rm|chmod|chown|systemctl|kill)\b", g16):
            p.append("grupos 1.6.0: binario no permitido")
        if re.search(r"(^|[\s;&|(])(modprobe|insmod|rmmod|rsyslogd)\s", g16):
            p.append("grupos 1.6.0: invoca modprobe/rsyslogd")
    if "__cis_run p1.unidades " in texto:
        for g in IDS_NUEVOS_1_8_0:
            if f"__cis_run {g} " not in texto:
                p.append(f"falta el grupo {g}")
        for g in IDS_NUEVOS_1_8_0[:-1]:
            usos = re.findall(r"systemctl\s+(\S+)", comando_root(g))
            if not usos or any(u not in ("is-enabled", "is-active") for u in usos):
                p.append(f"{g}: systemctl distinto de is-enabled/is-active")
    for cid in IDS_PRIVILEGIADOS:
        for prob in problemas_comando(comando_root(cid)):
            p.append(f"{cid}: {prob}")
    if '[ "$#" -ne 0 ]' not in texto:
        p.append("no rechaza argumentos")
    if "exec 0</dev/null" not in texto:
        p.append("no cierra stdin")
    if "umask 0077" not in texto:
        p.append("sin umask 0077")
    if "redaccion_no_operativa" not in texto or 'PIPESTATUS' not in texto:
        p.append("redacción sin modo fail-closed (autoprueba y PIPESTATUS)")
    if not texto.rstrip().endswith(_PIE):
        p.append("el final del script no comprueba el estado del filtro de redacción antes de 'exit 0'")
    if re.search(r"print[^}\n]*\$[0-9]", comando_root("grub.password")):
        p.append("grub.password imprime campos de user.cfg/grub.cfg (solo se admiten indicadores)")
    return p


def problemas_acl(ruta: str, acl: Optional[Dict], aclchar: str) -> List[str]:
    """ACL reales (getfacl -cpn). Escritura efectiva para un usuario/grupo distinto de root (o para
    otros) = no confiable. Sin getfacl solo se acepta si ls no muestra '+'. Las ACL no se modifican."""
    if not acl:
        return [f"{ruta}: sin resultado de ACL"]
    if acl.get("rc") == "NO_DISPONIBLE":
        return [f"{ruta}: tiene ACL extendida ('+') y getfacl no está disponible para verificarla"] if aclchar == "+" else []
    if acl.get("rc") != "0":
        return [f"{ruta}: getfacl falló (rc={acl.get('rc')}): {acl.get('entradas', '')[:160]}"]
    entradas = [e.split("#", 1)[0].strip() for e in (acl.get("entradas") or "").split(",")]
    entradas = [e for e in entradas if e and not e.startswith("default:")]
    mascara = next((e.split(":")[2] for e in entradas if e.startswith("mask:")), None)
    p = []
    for e in entradas:
        c = e.split(":")
        if len(c) != 3 or len(c[2]) != 3:
            p.append(f"{ruta}: entrada ACL no interpretable ({e})"); continue
        tipo, quien, perm = c
        if "w" not in perm or tipo == "mask":
            continue
        if tipo == "other":
            p.append(f"{ruta}: ACL other::{perm} concede escritura")
        elif tipo in ("user", "group") and quien and quien != "0" and (mascara is None or "w" in mascara):
            p.append(f"{ruta}: ACL {tipo}:{quien}:{perm} concede escritura a un {'usuario' if tipo == 'user' else 'grupo'} distinto de root")
        elif tipo == "group" and not quien and mascara is not None and "w" in mascara:
            pass  # grupo propietario: se valida por gid 0
    return p


def verificar_instalacion(objetos: Dict[str, Dict], acls: Dict[str, Dict], sha_encontrado: str,
                          sha_esperado: str, ruta: str = RUTA) -> List[str]:
    """Condiciones de confianza (precheck). Vacío = confiable.

    objetos: {ruta: {uid_l, gid_l, tipo_l (sin seguir enlaces), destino (readlink -f), uid, gid, modo,
    tipo, acl ('+' de ls)}}; acls: {ruta: {rc, entradas}}."""
    p = []
    if not sha_esperado:
        p.append("SHA-256 esperado no configurado en config/audit.yml (recolector_privilegiado.sha256)")
    elif sha_encontrado != sha_esperado:
        p.append(f"SHA-256 no coincide: encontrado {sha_encontrado or 'n/d'}, esperado {sha_esperado}")
    principal = ruta
    padres = tuple(dict.fromkeys((principal.rsplit('/', 1)[0],) + DIRECTORIOS_PADRE))
    for ruta in (principal,) + padres + DIRECTORIOS_PATH:
        o = objetos.get(ruta)
        if not o:
            p.append(f"{ruta}: sin metadatos"); continue
        destino = o.get("destino")
        if o.get("tipo_l") == "symbolic link":
            if ruta == principal:
                p.append(f"{ruta}: es un enlace simbólico (se exige un archivo regular)"); continue
            if ENLACES_PERMITIDOS.get(ruta) != destino:
                p.append(f"{ruta}: enlace simbólico no permitido (destino {destino})"); continue
            if o.get("uid_l") != "0" or o.get("gid_l") != "0":
                p.append(f"{ruta}: enlace simbólico no pertenece a root:root ({o.get('uid_l')}:{o.get('gid_l')})")
        elif destino != ruta:
            p.append(f"{ruta}: resuelve a {destino} (algún componente de la ruta es un enlace)"); continue
        if o.get("uid") != "0":
            p.append(f"{destino}: propietario uid {o.get('uid')} (se exige root)")
        if o.get("gid") != "0":
            p.append(f"{destino}: grupo gid {o.get('gid')} (se exige root)")
        try:
            modo = int(o.get("modo", ""), 8)
        except ValueError:
            p.append(f"{destino}: modo no interpretable ({o.get('modo')})"); continue
        if modo & 0o022:
            p.append(f"{destino}: modo {o.get('modo')} escribible por grupo/otros")
        if ruta == principal:
            if not (o.get("tipo_l") or "").startswith("regular") or not (o.get("tipo") or "").startswith("regular"):
                p.append(f"{ruta}: no es un archivo regular ({o.get('tipo_l')})")
            if modo & 0o7000:
                p.append(f"{ruta}: modo {o.get('modo')} con bits setuid/setgid/sticky")
        elif o.get("tipo") != "directory":
            p.append(f"{destino}: no es un directorio ({o.get('tipo')})")
        p.extend(problemas_acl(ruta, acls.get(ruta), o.get("acl", "")))
    return p
