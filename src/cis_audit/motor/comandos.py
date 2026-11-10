"""Registro de comandos de recolección (todos de solo lectura).

Cada comando declara: familias de SO, si usa `sudo -n`, impacto estimado, si
recorre el filesystem, si es auditoría profunda (requiere
`auditoria_filesystem_profunda: true`), códigos de retorno válidos, información
sensible posible y su tratamiento.

Reglas de diseño:
- Ningún comando escribe en el host: sin redirecciones a archivos, sin `-i`
  en sed, sin gestores de paquetes en modo escritura, sin systemctl de cambio.
  `tests/test_motor.py` verifica estas reglas sobre todo el registro.
- La elevación es siempre `sudo -n` explícito dentro del comando; nunca se
  usa `become` de Ansible ni se pide contraseña.
- Los secretos se eliminan en el propio host antes de imprimir (awk/printf),
  y la salida se vuelve a redactar en el controlador antes de persistir.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

RHEL = "rhel"
SLES = "sles"
AMBAS = (RHEL, SLES)

# Usuarios con shell válida (/etc/shells sin nologin) o shell bash/sh (exclusión Infra).
_USUARIOS = (
    "awk -F: 'NR==FNR{if($0 ~ /^\\// && $0 !~ /nologin$/) s[$0]=1; next} "
    "(($7 in s) || $7 ~ /\\/(ba)?sh$/){print $1\":\"$6\":\"$7}' /etc/shells /etc/passwd"
)
# Silenciamientos de stderr permitidos (únicos; ver tests/test_motor.py::test_2devnull_solo_justificados):
#  - _SUDO_PROBE: el fallo de "sudo -n true" ES la respuesta buscada (sin sudo); queda registrado en "#SUDO=no".
#  - sys.hostname: "hostname -f" falla si no hay FQDN resoluble; el fallback "hostname" devuelve el nombre corto.
SILENCIOS_JUSTIFICADOS = {"sudo -n true 2>/dev/null", "hostname -f 2>/dev/null"}
_SUDO_PROBE = 'S=""; sudo -n true 2>/dev/null && S="sudo -n"; echo "#SUDO=${S:-no}"; '
_EXCL_PATHS = (
    '! -path "/run/user/*" -a ! -path "/proc/*" -a ! -path "*/containerd/*" '
    '-a ! -path "*/kubelet/pods/*" -a ! -path "*/kubelet/plugins/*" -a ! -path "/sys/*" '
    '-a ! -path "/snap/*" -a ! -path "/var/*/private/*"'
)
_FS_NOREMOTOS = "nfs|nfs4|cifs|smb|smbfs|fuse|fuse\\..*|sshfs|proc|vfat|iso9660|efivarfs|selinuxfs"
_NICE = "nice -n 19 ionice -c3"


def _legibles(*rutas: str) -> str:
    """Prefijo que informa archivos/directorios existentes pero no legibles (#NO_LEGIBLE|ruta).

    Evita que una lectura sin permisos se interprete como "no configurado" (falso NO CUMPLE)."""
    return ("for __f in " + " ".join(rutas) + "; do [ -e \"$__f\" ] || continue; "
            "[ -r \"$__f\" ] || echo \"#NO_LEGIBLE|$__f\"; done; ")


def _unidades(*unidades: str) -> str:
    """Estado etiquetado de unidades systemd: unidad|is-enabled|is-active (no depende del orden de líneas)."""
    return ("for __u in " + " ".join(unidades) + "; do echo \"$__u|$(systemctl is-enabled \"$__u\" 2>&1)|"
            "$(systemctl is-active \"$__u\" 2>&1)\"; done")


# Archivos de inicio que pueden construir el PATH de una sesión de login de root (bash/sh).
ARCHIVOS_INICIO_ROOT = (
    "/etc/environment", "/etc/login.defs", "/usr/etc/login.defs", "/etc/profile", "/etc/profile.d/*.sh",
    "/etc/profile.local", "/etc/bashrc", "/etc/bash.bashrc", "/etc/bash.bashrc.local",
    "{H}/.bash_profile", "{H}/.bash_login", "{H}/.profile", "{H}/.bashrc",
)
# Líneas emitidas: las que mencionan PATH y las de estructura (para saber si una asignación es condicional).
# 1.7.0 (2026-10-26): también return/exit/exec/logout (construcciones que pueden cortar la carga de un archivo de inicio
# antes de una línea posterior), y la marca FLUJO|1 que indica que la salida incluye ese flujo completo (root_path.MARCA_FLUJO).
_RE_LINEAS_PATH = (r"PATH|pathmunge|(^|[[:space:];&|(])(if|then|elif|else|fi|case|esac|for|while|until|do|done|"
                   r"function|select|source|return|exit|exec|logout)([[:space:];)]|$)|(^|[[:space:];&|])\.[[:space:]]|[{}]|\(\)|;;")
_ROOT_PATH_ESTATICO = (
    "echo \"FLUJO|1\"; "
    "R=\"$(awk -F: '$1==\"root\"{print $6\"|\"$7}' /etc/passwd)\"; H=\"${R%%|*}\"; echo \"S|root|$R\"; "
    "for f in " + " ".join(a.replace("{H}", "$H") for a in ARCHIVOS_INICIO_ROOT) + "; do "
    "if [ ! -e \"$f\" ]; then echo \"F|$f|NO_EXISTE\"; continue; fi; "
    "if [ ! -r \"$f\" ]; then echo \"#NO_LEGIBLE|$f\"; continue; fi; "
    "echo \"F|$f|$(stat -Lc '%U|%G|%#a' -- \"$f\")\"; "
    "grep -nE '" + _RE_LINEAS_PATH + "' -- \"$f\" | grep -vE '^[0-9]+:[[:space:]]*#' | while IFS= read -r l; do printf 'L|%s|%s\\n' \"$f\" \"$l\"; done; "
    "done; "
    # Directorios candidatos: rutas absolutas (o $HOME/~) que aparecen en líneas con PATH. Solo metadatos.
    "for f in " + " ".join(a.replace("{H}", "$H") for a in ARCHIVOS_INICIO_ROOT) + "; do [ -r \"$f\" ] && "
    "grep -hE 'PATH|pathmunge' -- \"$f\"; done | grep -vE '^[[:space:]]*#' | grep -oE '(\\$\\{?HOME\\}?|~)?/[^:\"'\"'\"'[:space:];$`)]*' | "
    "while IFS= read -r t; do case \"$t\" in '${HOME}'*) t=\"$H${t#\\$\\{HOME\\}}\";; '$HOME'*) t=\"$H${t#\\$HOME}\";; "
    "'~'*) t=\"$H${t#\\~}\";; esac; echo \"$t\"; done | sort -u | while IFS= read -r d; do "
    "if [ -e \"$d\" ]; then echo \"D|$(stat -Lc '%n|%#a|%U|%F' -- \"$d\")\"; else echo \"D|$d|NO_EXISTE\"; fi; done"
)


@dataclass(frozen=True)
class Comando:
    id: str
    descripcion: str
    cmd: str
    familias: Tuple[str, ...] = AMBAS
    sudo: bool = False
    impacto: str = "bajo"  # bajo | medio | alto
    recorre_fs: bool = False
    profunda: bool = False
    rc_validos: Optional[Tuple[int, ...]] = (0,)  # None = cualquier rc es evidencia
    sensible: Optional[str] = None
    redaccion: str = "Redacción genérica del controlador (patrones de secretos)."
    timeout: Optional[int] = None  # None = timeout por defecto de config


C: List[Comando] = [
    # ------------------------------------------------------------- sistema
    Comando("sys.hostname", "Nombre del host", "hostname -f 2>/dev/null || hostname"),
    Comando("sys.os_release", "Distribución y versión", "cat /etc/os-release"),
    Comando("sys.kernel", "Kernel y arquitectura", 'echo "kernel=$(uname -r)"; echo "arch=$(uname -m)"'),
    Comando("sys.fecha", "Fecha/hora del host", "date -Is"),
    Comando("sys.usuario", "Usuario de conexión", 'echo "user=$(id -un) uid=$(id -u)"'),
    Comando("sys.sudo", "Capacidad de sudo no interactivo", "sudo -n true && echo SUDO_OK",
            sudo=True, rc_validos=None),
    Comando("sys.shells", "Shells válidas", "cat /etc/shells"),
    Comando("sys.uid_min", "UID_MIN de login.defs", "awk '/^\\s*UID_MIN/{print $2}' /etc/login.defs"),
    Comando("sys.fs_nodev", "Tipos de filesystem nodev", "awk '/nodev/{print $2}' /proc/filesystems"),
    Comando("sys.var_log_audit", "Montaje que contiene /var/log/audit: destino, dispositivo, filesystem, tamaño en bytes",
            "findmnt -bn -T /var/log/audit -o TARGET,SOURCE,FSTYPE,SIZE"),
    Comando("sys.selinux", "Estado SELinux", "getenforce", familias=(RHEL,), rc_validos=None),
    # ------------------------------------------------------------- paquetes
    # La lista de paquetes se completa por benchmark con los paquetes que exigen las filas YES.
    Comando("pkg.rpm", "Paquetes requeridos por los controles en alcance", "rpm -q {paquetes}", rc_validos=None),
    # ------------------------------------------------------------- repositorios / GPG
    Comando("repo.dnf_conf", "gpgcheck global en dnf.conf",
            "grep -HiE '^\\s*(gpgcheck|repo_gpgcheck|localpkg_gpgcheck)\\s*=' /etc/dnf/dnf.conf",
            familias=(RHEL,), rc_validos=(0, 1)),
    Comando("repo.yum_repos", "Repositorios definidos (claves de interés, sin credenciales)",
            "grep -HiPr '^\\h*(\\[|name|enabled|gpgcheck|repo_gpgcheck|gpgkey|baseurl|mirrorlist|metalink)\\b' "
            "/etc/yum.repos.d/",
            familias=(RHEL,), rc_validos=(0, 1),
            sensible="baseurl/mirrorlist pueden incluir usuario:contraseña o tokens en la URL",
            redaccion="Se excluyen claves username/password/proxy_password; userinfo y parámetros token/credentials de URL -> <REDACTADO>."),
    Comando("repo.dnf_repolist", "Repositorios habilitados (solo caché local)",
            "dnf -C -q repolist --enabled", familias=(RHEL,), rc_validos=None),
    Comando("repo.zypp_conf", "gpgcheck global en zypp.conf",
            "grep -HiE '^\\s*(gpgcheck|repo_gpgcheck|pkg_gpgcheck)\\s*=' /etc/zypp/zypp.conf",
            familias=(SLES,), rc_validos=(0, 1)),
    Comando("repo.zypp_repos", "Repositorios definidos (claves de interés, sin credenciales)",
            "grep -HiPr '^\\h*(\\[|name|enabled|autorefresh|gpgcheck|repo_gpgcheck|pkg_gpgcheck|gpgkey|baseurl)\\b' "
            "/etc/zypp/repos.d/",
            familias=(SLES,), rc_validos=(0, 1),
            sensible="baseurl puede incluir ?credentials= o userinfo",
            redaccion="userinfo y parámetros credentials/token de URL -> <REDACTADO>."),
    Comando("repo.zypper_lr", "Repositorios (zypper, sin refrescar)",
            "zypper --non-interactive --no-refresh lr -d", familias=(SLES,), rc_validos=None,
            sensible="URI de repositorios", redaccion="userinfo/credentials de URL -> <REDACTADO>."),
    Comando("repo.gpg_keys", "Claves GPG importadas en RPM",
            "rpm -q gpg-pubkey --qf '%{NAME}-%{VERSION}-%{RELEASE}|%{PACKAGER}|%{SUMMARY}\\n'",
            rc_validos=None),
    Comando("repo.pki_rpm_gpg", "Claves GPG locales", "for __d in /etc/pki/rpm-gpg /usr/lib/rpm/gnupg/keys; do [ -d \"$__d\" ] && ls -l \"$__d\"; done",
            rc_validos=None),
    # ------------------------------------------------------------- actualizaciones
    Comando("upd.dnf_check", "Actualizaciones pendientes (caché local, sin red)",
            "dnf -C -q check-update", familias=(RHEL,), impacto="medio", rc_validos=(0, 100, 1)),
    Comando("upd.dnf_cache_edad", "Antigüedad de la caché de metadatos dnf",
            "find /var/cache/dnf -maxdepth 1 -name '*.solv' -printf '%TY-%Tm-%Td %p\\n'",
            familias=(RHEL,), rc_validos=None),
    Comando("upd.needs_restarting", "Reinicio requerido tras actualizaciones",
            "dnf -C -q needs-restarting -r", familias=(RHEL,), rc_validos=(0, 1)),
    Comando("upd.zypper_updates", "Actualizaciones pendientes (sin refrescar)",
            "zypper --non-interactive --no-refresh list-updates", familias=(SLES,), impacto="medio",
            rc_validos=None),
    Comando("upd.zypper_patches", "Parches pendientes (sin refrescar)",
            "zypper --non-interactive --no-refresh list-patches", familias=(SLES,), impacto="medio",
            rc_validos=None),
    Comando("upd.zypp_cache_edad", "Antigüedad de la caché zypp",
            "find /var/cache/zypp/raw -maxdepth 1 -mindepth 1 -printf '%TY-%Tm-%Td %p\\n'",
            familias=(SLES,), rc_validos=None),
    # ------------------------------------------------------------- AIDE
    Comando("aide.cron", "Tareas cron que ejecutan aide (cron del sistema y spool)",
            _SUDO_PROBE + "$S grep -Er '^([^#]+\\s+)?(/usr/s?bin/|^\\s*)aide(\\.wrapper)?\\s(--?\\S+\\s)*(--(check|update)|\\$AIDEARGS)\\b' "
            "/etc/cron.d /etc/cron.daily /etc/cron.weekly /etc/cron.monthly /etc/cron.hourly /etc/crontab /var/spool/cron/",
            sudo=True, rc_validos=(0, 1, 2)),
    Comando("aide.timer", "aidecheck.service/timer",
            _unidades("aidecheck.service", "aidecheck.timer"),
            rc_validos=None),
    # ------------------------------------------------------------- GRUB
    Comando("grub.archivos", "Permisos de archivos GRUB en /boot",
            "sudo -n find /boot -type f \\( -name 'grub*' -o -name 'user.cfg' \\) -exec stat -Lc '%n|%#a|%U|%G' {} +",
            sudo=True),
    Comando("grub.password", "Indicadores normalizados de contraseña GRUB (el hash nunca se emite)",
            "sudo -n find /boot -type f -name user.cfg -exec awk '/^[[:space:]]*GRUB2_PASSWORD=/{d=1} "
            "/^[[:space:]]*GRUB2_PASSWORD=grub[.]pbkdf2[.]sha512[.][0-9]+[.][0-9A-Fa-f]+[.][0-9A-Fa-f]+[[:space:]]*$/{f=1} "
            "END{print \"GRUB|\" FILENAME \"|password_grub_configurada=\" (d?\"true\":\"false\") \"|formato_pbkdf2_sha512=\" (f?\"true\":\"false\")}' {} \\; ; "
            "sudo -n find /boot -type f -name grub.cfg -exec awk '/^[[:space:]]*set[[:space:]]+superusers=/{s=1} "
            "/^[[:space:]]*password_pbkdf2[[:space:]]+[^[:space:]]+[[:space:]]+[^[:space:]]+/{p=1} "
            "END{print \"GRUB|\" FILENAME \"|superusuario_grub_configurado=\" (s?\"true\":\"false\") \"|password_pbkdf2_configurada=\" (p?\"true\":\"false\")}' {} \\;",
            sudo=True, rc_validos=None,
            sensible="Hash PBKDF2 de la contraseña de GRUB",
            redaccion="awk solo imprime indicadores true/false por archivo; el contenido de user.cfg/grub.cfg nunca se emite."),
    Comando("grub.cmdline", "Parámetros del kernel en ejecución", "cat /proc/cmdline"),
    # 2026-10-14: parámetros de auditoría de la línea de arranque EN EJECUCIÓN (6.3.1.2/6.3.1.3, informativo). Solo
    # se emiten los tokens audit=, audit_backlog_limit= e ipv6.disable= (la línea completa podría contener secretos).
    Comando("sys.cmdline", "Parámetros audit/audit_backlog_limit/ipv6.disable del kernel en ejecución (/proc/cmdline)",
            "grep -oE '(^| )(audit|audit_backlog_limit|ipv6[.]disable)=[^ ]*' /proc/cmdline | sed 's/^ //'; echo \"#KERNEL|$(uname -r)\"",
            rc_validos=None),
    Comando("grub.default", "GRUB_CMDLINE_LINUX en /etc/default/grub",
            "grep -E '^\\s*GRUB_CMDLINE_LINUX(_DEFAULT)?=' /etc/default/grub", rc_validos=(0, 1)),
    Comando("grub.grubby", "Argumentos de las entradas de arranque",
            "sudo -n grubby --info=ALL | grep -E '^(kernel|args)='", familias=(RHEL,), sudo=True,
            rc_validos=(0, 1)),
    Comando("grub.cfg_linux", "Líneas linux de grub.cfg",
            "sudo -n grep -E '^\\s*linux' /boot/grub2/grub.cfg", familias=(SLES,), sudo=True,
            rc_validos=(0, 1)),
    # ------------------------------------------------------------- kernel / core dumps
    Comando("sysctl.runtime", "Valores sysctl en ejecución",
            "sysctl kernel.randomize_va_space fs.suid_dumpable"),
    Comando("sysctl.archivos", "sysctl persistente (orden de carga de systemd-sysctl)",
            "C=/usr/lib/systemd/systemd-sysctl; [ -x \"$C\" ] || C=/lib/systemd/systemd-sysctl; "
            "\"$C\" --cat-config | grep -E '^\\s*#\\s*/|randomize_va_space|suid_dumpable'",
            rc_validos=(0, 1)),
    Comando("coredump.conf", "Configuración efectiva de systemd-coredump",
            "systemd-analyze cat-config systemd/coredump.conf | grep -E '^\\s*#\\s*/|^\\s*\\[|^\\s*#?\\s*(Storage|ProcessSizeMax)\\s*='",
            rc_validos=(0, 1)),
    Comando("coredump.limits", "Límite de core en limits.conf",
            _legibles("/etc/security/limits.conf", "/etc/security/limits.d", "/etc/security/limits.d/*")
            + "grep -HE '^\\s*\\*\\s+hard\\s+core' /etc/security/limits.conf /etc/security/limits.d/*",
            familias=(SLES,), rc_validos=(0, 1, 2)),
    Comando("coredump.servicio", "systemd-coredump instalado",
            _unidades("systemd-coredump.socket", "coredump.service"), familias=(SLES,),
            rc_validos=None),
    # ------------------------------------------------------------- MAC
    Comando("selinux.unconfined", "Procesos unconfined_service_t",
            "ps -eZ | grep unconfined_service_t", familias=(RHEL,), rc_validos=(0, 1)),
    Comando("apparmor.status", "Estado de AppArmor", "sudo -n apparmor_status", familias=(SLES,),
            sudo=True, rc_validos=None),
    # ------------------------------------------------------------- GDM / crypto
    Comando("gdm.dconf", "Configuración dconf de GDM",
            "[ -d /etc/dconf ] || { echo '#NO_EXISTE|/etc/dconf'; exit 0; }; "
            "grep -RHiE '^\\s*(disable-user-list|banner-message-enable|banner-message-text)\\s*=' /etc/dconf/db/; "
            "grep -H 'system-db' /etc/dconf/profile/*; ls -1 /etc/dconf/db/",
            rc_validos=None),
    Comando("gdm.grupos_dnf", "Grupos de entorno instalados (caché local)",
            "dnf -C -q group list --installed", familias=(RHEL,), rc_validos=None),
    Comando("crypto.politica", "Política criptográfica del sistema",
            "cat /etc/crypto-policies/config; update-crypto-policies --show", rc_validos=None),
    # ------------------------------------------------------------- auditd
    Comando("auditd.servicio", "auditd habilitado/activo",
            _unidades("auditd"), rc_validos=None),
    Comando("auditd.conf", "Parámetros de auditd.conf",
            "sudo -n grep -E '^\\s*[A-Za-z_]+\\s*=' /etc/audit/auditd.conf", sudo=True),
    Comando("auditd.reglas_disco", "Reglas en /etc/audit/rules.d (con nombre de archivo)",
            "sudo -n sh -c 'grep -H \"\" /etc/audit/rules.d/*.rules'", sudo=True, rc_validos=(0, 1, 2)),
    Comando("auditd.audit_rules", "Reglas fusionadas en /etc/audit/audit.rules",
            "sudo -n grep -vE '^\\s*(#|$)' /etc/audit/audit.rules", sudo=True, rc_validos=(0, 1)),
    Comando("auditd.reglas_cargadas", "Reglas cargadas en el kernel", "sudo -n auditctl -l", sudo=True),
    Comando("auditd.estado", "Estado del subsistema de auditoría (enabled=2 inmutable)",
            "sudo -n auditctl -s", sudo=True),
    Comando("auditd.sudo_logfile", "Archivo de log de sudo (Defaults logfile)",
            "sudo -n sh -c 'grep -rhE \"^[[:space:]]*Defaults.*logfile\" /etc/sudoers /etc/sudoers.d/'",
            sudo=True, rc_validos=(0, 1)),
    Comando("auditd.kmod_symlinks", "Enlaces de herramientas de módulos a kmod",
            # Rutas reales de cada herramienta en /sbin, /usr/sbin, /bin y /usr/bin (SLES 15 no tiene /usr fusionado) y su
            # destino final (readlink -f sigue toda la cadena de enlaces); K| = binarios kmod existentes y su destino.
            "for t in lsmod rmmod insmod modinfo modprobe depmod; do for d in /sbin /usr/sbin /bin /usr/bin; do "
            "f=\"$d/$t\"; if [ -e \"$f\" ] || [ -L \"$f\" ]; then echo \"T|$t|$f|$(readlink -f \"$f\")\"; fi; done; done; "
            "for k in /bin/kmod /usr/bin/kmod /sbin/kmod /usr/sbin/kmod; do [ -e \"$k\" ] && echo \"K|$k|$(readlink -f \"$k\")\"; done; true",
            rc_validos=None),
    # ------------------------------------------------------------- journald
    Comando("journald.servicio", "systemd-journald habilitado/activo",
            _unidades("systemd-journald.service"),
            rc_validos=None),
    Comando("journald.conf", "Configuración efectiva de journald (con orden de archivos)",
            "systemd-analyze cat-config systemd/journald.conf | grep -E '^\\s*#\\s*/|^\\s*\\[|^\\s*[A-Za-z]+\\s*='",
            rc_validos=(0, 1)),
    Comando("journald.tmpfiles", "Permisos definidos en tmpfiles de systemd (logs journald)",
            "for f in /etc/tmpfiles.d/systemd.conf /usr/lib/tmpfiles.d/systemd.conf; do [ -f \"$f\" ] && "
            "grep -HvE '^\\s*(#|$)' \"$f\"; done", rc_validos=None),
    Comando("journald.remoto", "Servicios journal-upload / journal-remote",
            "for u in systemd-journal-upload.service systemd-journal-remote.socket systemd-journal-remote.service; "
            "do echo \"$u|$(systemctl is-enabled \"$u\" 2>&1)|$(systemctl is-active \"$u\" 2>&1)\"; done",
            rc_validos=None),
    Comando("journald.upload_conf", "Autenticación de journal-upload",
            "[ -e /etc/systemd/journal-upload.conf ] || echo '#NO_EXISTE|/etc/systemd/journal-upload.conf'; "
            + _legibles("/etc/systemd/journal-upload.conf", "/etc/systemd/journal-upload.conf.d", "/etc/systemd/journal-upload.conf.d/*.conf")
            + "grep -HE '^\\s*(URL|ServerKeyFile|ServerCertificateFile|TrustedCertificateFile)\\s*=' "
            "/etc/systemd/journal-upload.conf /etc/systemd/journal-upload.conf.d/*.conf",
            rc_validos=(0, 1, 2), sensible="URL del servidor de logs (podría incluir credenciales)",
            redaccion="userinfo de URL -> <REDACTADO>; solo rutas de certificados, nunca su contenido."),
    # ------------------------------------------------------------- rsyslog / logrotate
    Comando("rsyslog.servicio", "rsyslog habilitado/activo",
            _unidades("rsyslog"), rc_validos=None),
    Comando("rsyslog.conf", "Configuración rsyslog sin comentarios",
            _legibles("/etc/rsyslog.conf", "/etc/rsyslog.d", "/etc/rsyslog.d/*.conf")
            + "grep -HvE '^\\s*(#|$)' /etc/rsyslog.conf /etc/rsyslog.d/*.conf", rc_validos=(0, 1, 2),
            sensible="Parámetros de TLS/autenticación de destinos remotos",
            redaccion="Valores de password/pwd/secret/token -> <REDACTADO>."),
    Comando("rsyslog.var_log", "Archivos en /var/log (listado de primer nivel)", "ls -l /var/log/",
            rc_validos=None),
    Comando("logrotate.conf", "Configuración de logrotate",
            _legibles("/etc/logrotate.conf", "/etc/logrotate.d") + "grep -HvE '^\\s*(#|$)' /etc/logrotate.conf; ls -1 /etc/logrotate.d/",
            rc_validos=None),
    Comando("logs.permisos", "Archivos de /var/log con permisos/propietario a revisar (criterio CIS)",
            "sudo -n find -L /var/log -type f \\( -perm /0137 -o ! -user root -o ! -group root \\) "
            "-exec stat -Lc '%n^%#a^%U^%u^%G^%g' {} +",
            sudo=True, impacto="medio", recorre_fs=True, rc_validos=(0, 1)),
    # ------------------------------------------------------------- archivos de cuentas
    Comando("perm.cuentas", "Permisos de archivos de cuentas",
            "stat -Lc '%n|%#a|%U|%u|%G|%g' /etc/passwd /etc/passwd- /etc/group /etc/group- /etc/shadow /etc/shadow- "
            "/etc/gshadow /etc/gshadow-", rc_validos=(0, 1)),
    Comando("usr.passwd", "Cuentas (sin GECOS; campo de contraseña clasificado, nunca el hash)",
            "awk -F: '{p=($2==\"x\")?\"x\":(($2==\"\")?\"<VACIO>\":\"<REDACTADO>\"); "
            "print $1\":\"p\":\"$3\":\"$4\":\"$6\":\"$7}' /etc/passwd",
            sensible="Hash si alguna cuenta no usa shadow; datos personales en GECOS",
            redaccion="GECOS se omite; un hash en el campo 2 se imprime como <REDACTADO> en el host."),
    Comando("usr.group", "Grupos (sin campo de contraseña)", "awk -F: '{print $1\":\"$3\":\"$4}' /etc/group"),
    Comando("usr.shadow_vacios", "Cuentas con contraseña vacía en /etc/shadow (solo nombres)",
            "sudo -n awk -F: '($2 == \"\") {print $1}' /etc/shadow", sudo=True,
            sensible="Hashes de /etc/shadow",
            redaccion="Solo se imprime el nombre de usuario con campo vacío; los hashes nunca salen del host."),
    # R6 (decisión del usuario 2026-10-08): análisis ESTÁTICO. Nunca se ejecuta ni se hace source del
    # perfil de root (sin sudo -i, su -, bash -l). Solo se emiten metadatos de los archivos de inicio y las
    # líneas que afectan a PATH o a la estructura (condicionales, funciones, inclusiones); nunca el archivo completo.
    Comando("usr.root_path", "Archivos de inicio de root: líneas PATH/estructura (análisis estático, sin ejecutar)",
            _ROOT_PATH_ESTATICO, sudo=True, rc_validos=None,
            sensible="Los archivos de inicio de root podrían contener variables con secretos",
            redaccion="Solo líneas con PATH o palabras de estructura shell; redacción de asignaciones sensibles."),
    Comando("usr.homes", "Directorios home de usuarios interactivos",
            _SUDO_PROBE + _USUARIOS + " | while IFS=: read -r u h sh; do "
            "if $S test -d \"$h\"; then echo \"$u|$h|$sh|$($S stat -Lc '%U|%#a|%G' -- \"$h\")\"; "
            "elif $S stat -- \"$h\" 2>&1 | grep -q 'Permission denied'; then echo \"$u|$h|$sh|SIN_PERMISO|\"; "
            "else echo \"$u|$h|$sh|NO_EXISTE|\"; fi; done",
            sudo=True, rc_validos=None),
    Comando("usr.dotfiles_raiz", "Archivos ocultos en la raíz de cada home (sin contenido)",
            _SUDO_PROBE + _USUARIOS + " | while IFS=: read -r u h sh; do g=\"$(id -gn \"$u\")\"; "
            "$S test -d \"$h\" || continue; $S find \"$h\" -xdev -maxdepth 1 -type f -name '.*' "
            "-printf \"$u|$g|%m|%u|%g|%p\\n\"; done",
            sudo=True, recorre_fs=True, rc_validos=None,
            sensible=".netrc/.rhosts pueden contener credenciales", redaccion="Solo nombre, modo y propietario; nunca el contenido."),
    Comando("usr.dotfiles_completo", "Archivos ocultos en todo el árbol de cada home (CIS 7.2.9)",
            _SUDO_PROBE + _USUARIOS + " | while IFS=: read -r u h sh; do g=\"$(id -gn \"$u\")\"; "
            "$S test -d \"$h\" || continue; $S " + _NICE + " find \"$h\" -xdev -type f -name '.*' "
            "-printf \"$u|$g|%m|%u|%g|%p\\n\"; done",
            sudo=True, impacto="alto", recorre_fs=True, profunda=True, rc_validos=None,
            sensible=".netrc/.rhosts pueden contener credenciales", redaccion="Solo nombre, modo y propietario; nunca el contenido."),
    # ------------------------------------------------------------- filesystem profundo
    Comando("fs.world_writable", "Archivos world-writable y directorios sin sticky bit (CIS 7.1.11)",
            _SUDO_PROBE + "findmnt -nlko FSTYPE,TARGET | awk '($1 !~ /^(" + _FS_NOREMOTOS + ")$/ && "
            "$2 !~ /^(\\/run\\/user\\/|\\/tmp|\\/var\\/tmp)/){print $2}' | while IFS= read -r m; do "
            "$S " + _NICE + " find \"$m\" -xdev \\( " + _EXCL_PATHS + " \\) \\( -type f -o -type d \\) -perm -0002 "
            "-printf '%y|%m|%p\\n'; done",
            sudo=True, impacto="alto", recorre_fs=True, profunda=True, rc_validos=None),
    Comando("fs.sin_dueno", "Archivos/directorios sin usuario o grupo (CIS 7.1.12)",
            _SUDO_PROBE + "findmnt -nlko FSTYPE,TARGET | awk '($1 !~ /^(" + _FS_NOREMOTOS + ")$/ && "
            "$2 !~ /^\\/run\\/user\\//){print $2}' | while IFS= read -r m; do "
            "$S " + _NICE + " find \"$m\" -xdev \\( " + _EXCL_PATHS + " \\) \\( -type f -o -type d \\) "
            "\\( \\( -nouser -printf 'NOUSER|%p\\n' \\) , \\( -nogroup -printf 'NOGROUP|%p\\n' \\) \\); done",
            sudo=True, impacto="alto", recorre_fs=True, profunda=True, rc_validos=None),
    Comando("fs.suid_sgid", "Ejecutables SUID/SGID en montajes sin noexec/nosuid (CIS 7.1.13 y 6.3.3.6)",
            _SUDO_PROBE + "findmnt -nlko FSTYPE,TARGET,OPTIONS | awk '($1 !~ /^(" + _FS_NOREMOTOS + ")$/ && "
            "$2 !~ /^\\/run\\/user\\// && $3 !~ /noexec/ && $3 !~ /nosuid/){print $1\" \"$2}' | "
            "while read -r t m; do echo \"#MOUNT|$t|$m\"; $S " + _NICE + " find \"$m\" -xdev -type f \\( -perm -2000 -o -perm -4000 \\) "
            "-printf '%m|%u|%g|%p\\n'; done",
            sudo=True, impacto="alto", recorre_fs=True, profunda=True, rc_validos=None),
]

# Parte 1: comandos sin sudo en módulo propio, mismo catálogo y mismas reglas de seguridad.
from .comandos_p1 import COMANDOS_P1  # noqa: E402
from .comandos_priv import COMANDOS_PRIV  # noqa: E402  (solo recolector privilegiado 1.2.0)

C += COMANDOS_P1 + COMANDOS_PRIV

COMANDOS: Dict[str, Comando] = {c.id: c for c in C}
if len(COMANDOS) != len(C):
    raise RuntimeError("Ids de comando duplicados")


# Comandos de identificación del host: se ejecutan siempre.
BASE = ("sys.hostname", "sys.os_release", "sys.kernel", "sys.fecha", "sys.usuario", "sys.sudo")


