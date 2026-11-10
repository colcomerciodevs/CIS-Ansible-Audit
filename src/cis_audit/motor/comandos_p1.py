"""Comandos de recolección de la Parte 1. Todos READ-ONLY y SIN sudo.

Mismas reglas que el resto del catálogo (motor/seguridad.py y tests/test_motor.py): sin escrituras,
sin `2>/dev/null` que oculte errores, sin perfiles de login, sin modprobe/useradd/chage. Un archivo
existente pero no legible se informa como `#NO_LEGIBLE|ruta` (=> ERROR DE AUDITORÍA, nunca NO CUMPLE).

Adaptaciones del procedimiento Audit del CIS (requisito intacto; ver controles_p1.ADAPTACIONES_P1):
- módulos: el CIS usa `modprobe --showconfig`; aquí se leen las mismas fuentes (modprobe.d con su
  precedencia y /proc/cmdline) porque el proyecto prohíbe invocar modprobe.
- INACTIVE: el CIS usa `useradd -D`; aquí se lee /etc/default/useradd (la fuente de `useradd -D`).
- sysctl persistente: `systemd-sysctl --cat-config` sin sudo (los archivos sysctl son 0644); si alguno
  no es legible se informa y el control queda en ERROR DE AUDITORÍA.
"""

from .comandos import AMBAS, SLES, Comando, _legibles, _unidades

# Módulos de kernel de la Parte 1: nombre -> tipo (directorio bajo /lib/modules/*/kernel/).
MODULOS_P1 = {"cramfs": "fs", "squashfs": "fs", "udf": "fs", "usb-storage": "drivers",
              "dccp": "net", "sctp": "net", "rds": "net", "tipc": "net"}

# Parámetros sysctl de red de la Parte 1 (todas las variantes v2/v4/SLES).
SYSCTL_P1 = (
    "net.ipv4.ip_forward", "net.ipv4.conf.all.forwarding", "net.ipv4.conf.default.forwarding",
    "net.ipv6.conf.all.forwarding", "net.ipv6.conf.default.forwarding",
    "net.ipv4.conf.all.send_redirects", "net.ipv4.conf.default.send_redirects",
    "net.ipv4.icmp_ignore_bogus_error_responses", "net.ipv4.icmp_echo_ignore_broadcasts",
    "net.ipv4.conf.all.accept_redirects", "net.ipv4.conf.default.accept_redirects",
    "net.ipv6.conf.all.accept_redirects", "net.ipv6.conf.default.accept_redirects",
    "net.ipv4.conf.all.secure_redirects", "net.ipv4.conf.default.secure_redirects",
    "net.ipv4.conf.all.rp_filter", "net.ipv4.conf.default.rp_filter",
    "net.ipv4.conf.all.accept_source_route", "net.ipv4.conf.default.accept_source_route",
    "net.ipv6.conf.all.accept_source_route", "net.ipv6.conf.default.accept_source_route",
    "net.ipv4.conf.all.log_martians", "net.ipv4.conf.default.log_martians",
    "net.ipv4.tcp_syncookies", "net.ipv6.conf.all.accept_ra", "net.ipv6.conf.default.accept_ra",
    "net.ipv6.conf.all.disable_ipv6", "net.ipv6.conf.default.disable_ipv6",
)
# Incluye asignaciones con comodín (net.ipv4.conf.*.x) para informarlas: el Audit CIS evalúa el nombre exacto.
_RE_SYSCTL = "|".join(k.replace(".", "[./]") for k in SYSCTL_P1) + "|net[./]ipv[46][./]conf[./][*]"

# Puntos de montaje de la Parte 1.
MONTAJES_P1 = ("/tmp", "/dev/shm", "/home", "/var", "/var/tmp", "/var/log", "/var/log/audit")

# Unidades systemd consultadas por los controles de servicios de la Parte 1 (ambas familias; una unidad
# inexistente se informa como no-existe y no altera el resultado).
UNIDADES_P1 = (
    "autofs.service", "avahi-daemon.socket", "avahi-daemon.service", "dhcpd.service", "dhcpd6.service",
    "named.service", "dnsmasq.service", "vsftpd.service", "dovecot.socket", "dovecot.service", "cyrus-imapd.service",
    "nfs-server.service", "ypserv.service", "cups.socket", "cups.service", "rpcbind.socket", "rpcbind.service",
    "rsyncd.socket", "rsyncd.service", "smb.service", "snmpd.service", "telnet.socket", "tftp.socket", "tftp.service",
    "squid.service", "httpd.socket", "httpd.service", "nginx.service", "apache2.service", "xinetd.service",
    "slapd.service", "crond.service", "cron.service", "chronyd.service", "chrony.service", "systemd-timesyncd.service",
    "tmp.mount",
)

_SSHD_CLAVES = ("loglevel", "disableforwarding", "maxauthtries", "ignorerhosts", "hostbasedauthentication",
                "permitrootlogin", "permitemptypasswords", "permituserenvironment", "clientaliveinterval",
                "clientalivecountmax", "logingracetime", "banner", "usepam", "maxstartups", "maxsessions",
                "allowusers", "allowgroups", "denyusers", "denygroups")

_MODPROBE_D = "/etc/modprobe.d/*.conf /run/modprobe.d/*.conf /usr/local/lib/modprobe.d/*.conf /usr/lib/modprobe.d/*.conf /lib/modprobe.d/*.conf"
_PAM_RHEL = "/etc/pam.d/system-auth /etc/pam.d/password-auth"
_PAM_SLES = "/etc/pam.d/common-auth /etc/pam.d/common-account /etc/pam.d/common-password /etc/pam.d/common-session"
_SEC = "/etc/security/pwquality.conf /etc/security/pwquality.conf.d/*.conf /etc/security/faillock.conf /etc/security/pwhistory.conf"
_PERFILES = ("/etc/profile /etc/profile.d/*.sh /etc/profile.local /etc/bashrc /etc/bash.bashrc /etc/bash.bashrc.local "
             "/etc/login.defs /usr/etc/login.defs /etc/default/login /etc/pam.d/postlogin /etc/pam.d/common-session")


def _por_archivo(rutas: str, filtro: str, etiqueta: str) -> str:
    """Para cada archivo existente: legible -> 'etiqueta|archivo|n:línea' de las líneas que cumplen el filtro
    (sin comentarios); no legible -> '#NO_LEGIBLE|archivo'. grep sin coincidencias no es un error."""
    return ("for f in " + rutas + "; do [ -e \"$f\" ] || continue; if [ ! -r \"$f\" ]; then echo \"#NO_LEGIBLE|$f\"; continue; fi; "
            "echo \"F|$f\"; grep -nE '" + filtro + "' -- \"$f\" | grep -vE '^[0-9]+:[[:space:]]*#' | "
            "while IFS= read -r l; do printf '" + etiqueta + "|%s|%s\\n' \"$f\" \"$l\"; done; done")


def _stat(rutas: str) -> str:
    return ("for f in " + rutas + "; do if [ -e \"$f\" ]; then echo \"S|$(stat -Lc '%n|%#a|%U|%G|%F' -- \"$f\")\"; "
            "else echo \"S|$f|NO_EXISTE\"; fi; done")


COMANDOS_P1 = [
    Comando("p1.kmod", "Módulos de kernel de la Parte 1: disponibles, cargados y configuración modprobe.d (sin invocar modprobe)",
            "find /lib/modules/ -type f \\( " + " -o ".join(f"-name '{m}.ko*'" for m in MODULOS_P1) + " \\) -printf 'K|%p\\n'; "
            "awk '{print \"L|\"$1}' /proc/modules; "
            + _por_archivo(_MODPROBE_D, "^[[:space:]]*(install|blacklist)[[:space:]]", "C")
            + "; grep -o 'modprobe.blacklist=[^ ]*' /proc/cmdline | sed 's/^/B|/'",
            rc_validos=None, impacto="bajo"),
    Comando("p1.montajes", "Montajes, fstab y tmp.mount de los puntos de montaje de la Parte 1",
            "for t in " + " ".join(MONTAJES_P1) + "; do o=\"$(findmnt -kn -r -o TARGET,SOURCE,FSTYPE,OPTIONS --mountpoint \"$t\")\"; "
            "if [ -n \"$o\" ]; then echo \"MNT|$o\"; else echo \"MNT|$t|NO_MONTADO\"; fi; done; "
            + _legibles("/etc/fstab")
            + "awk '$0 !~ /^[[:space:]]*#/ && NF>=4 {print \"FSTAB|\"$2\"|\"$3\"|\"$4}' /etc/fstab",
            rc_validos=None),
    Comando("p1.unidades", "Estado de unidades systemd de servicios de la Parte 1 (is-enabled/is-active, sin cambios)",
            _unidades(*UNIDADES_P1), rc_validos=None),
    Comando("p1.escucha", "Puertos en escucha (sin procesos) y postfix inet_interfaces",
            "ss -lntuH; if command -v postconf >/dev/null; then echo \"POSTCONF|$(postconf -h inet_interfaces)\"; else echo 'POSTCONF|NO_INSTALADO'; fi",
            rc_validos=None),
    Comando("p1.banner", "Contenido y permisos de /etc/motd, /etc/motd.d, /etc/issue, /etc/issue.net (no sensible)",
            _stat("/etc/motd /etc/issue /etc/issue.net") + "; for f in /etc/motd /etc/motd.d/* /etc/issue /etc/issue.net; do "
            "[ -f \"$f\" ] || continue; if [ ! -r \"$f\" ]; then echo \"#NO_LEGIBLE|$f\"; continue; fi; "
            "head -n 60 -- \"$f\" | while IFS= read -r l; do printf 'C|%s|%s\\n' \"$f\" \"$l\"; done; done",
            rc_validos=None),
    Comando("p1.chrony", "Fuentes de tiempo configuradas en chrony (server/pool/sourcedir/include)",
            _por_archivo("/etc/chrony.conf /etc/chrony.d/*.conf /etc/chrony/chrony.conf",
                         "^[[:space:]]*(server|pool|sourcedir|confdir|include)[[:space:]]", "T"),
            rc_validos=None),
    Comando("p1.ipv6", "Estado de IPv6 (kernel, sysctl, direcciones y parámetro de arranque)",
            "if [ -d /proc/sys/net/ipv6 ]; then echo 'IPV6_KERNEL|si'; else echo 'IPV6_KERNEL|no'; fi; "
            "grep -o 'ipv6.disable=[01]' /proc/cmdline | sed 's/^/CMDLINE|/'; "
            "ip -6 -o addr show | awk '{print \"ADDR|\"$2\"|\"$4}'",
            rc_validos=None),
    Comando("p1.wireless", "Interfaces inalámbricas y su módulo de controlador",
            "for d in /sys/class/net/*; do if [ -d \"$d/wireless\" ] || [ -L \"$d/phy80211\" ]; then "
            "echo \"W|${d##*/}|$(basename \"$(readlink -f \"$d/device/driver/module\")\")\"; fi; done; echo 'W_FIN'",
            rc_validos=None),
    Comando("p1.sysctl", "Parámetros sysctl de red en ejecución (Parte 1)",
            "for k in " + " ".join(SYSCTL_P1) + "; do p=\"/proc/sys/$(echo \"$k\" | tr . /)\"; "
            "if [ -r \"$p\" ]; then echo \"$k = $(cat -- \"$p\")\"; elif [ -e \"$p\" ]; then echo \"#NO_LEGIBLE|$p\"; "
            "else echo \"$k = NO_EXISTE\"; fi; done",
            rc_validos=None),
    Comando("p1.sysctl_archivos", "sysctl persistente de red: orden de carga de systemd-sysctl (sin sudo)",
            _legibles("/etc/sysctl.conf /etc/sysctl.d/* /run/sysctl.d/* /usr/local/lib/sysctl.d/* /usr/lib/sysctl.d/* /lib/sysctl.d/*")
            + "C=/usr/lib/systemd/systemd-sysctl; [ -x \"$C\" ] || C=/lib/systemd/systemd-sysctl; "
            "\"$C\" --cat-config | grep -E '^[[:space:]]*#[[:space:]]*/|" + _RE_SYSCTL + "'; "
            "if [ -f /etc/sysctl.conf ] && [ \"$(readlink -f /etc/sysctl.d/99-sysctl.conf)\" != /etc/sysctl.conf ]; then "
            "echo '# /etc/sysctl.conf'; grep -E '" + _RE_SYSCTL + "' /etc/sysctl.conf; fi",
            rc_validos=None),
    Comando("p1.cron", "Permisos de crontab, cron.*, cron.allow/deny y at.allow/deny",
            _stat("/etc/crontab /etc/cron.hourly /etc/cron.daily /etc/cron.weekly /etc/cron.monthly /etc/cron.d "
                  "/etc/cron.allow /etc/cron.deny /etc/at.allow /etc/at.deny"),
            rc_validos=None),
    Comando("p1.pam_su", "Restricción de su (pam_wheel) en /etc/pam.d/su",
            _por_archivo("/etc/pam.d/su", "pam_wheel", "W"), rc_validos=None),
    Comando("p1.pam", "Pila PAM efectiva (authselect/pam-config), includes/substack y /etc/security/*.conf",
            "if command -v authselect >/dev/null; then echo \"AUTHSELECT_CONF|$(head -n 1 /etc/authselect/authselect.conf)\"; "
            "authselect current | sed 's/^/AUTHSELECT|/'; fi; "
            + _por_archivo(_PAM_RHEL + " " + _PAM_SLES, "^[[:space:]]*-?(auth|account|password|session)[[:space:]]", "P") + "; "
            "p=''; [ -r /etc/authselect/authselect.conf ] && p=\"$(head -n 1 /etc/authselect/authselect.conf | grep -E '^custom/')\"; if [ -n \"$p\" ]; then "
            + _por_archivo("/etc/authselect/$p/system-auth /etc/authselect/$p/password-auth",
                           "pam_(pwquality|pwhistory|faillock|unix)[.]so", "T") + "; fi; "
            + _por_archivo(_SEC, "^[[:space:]]*[a-z_]+", "S"),
            familias=AMBAS, rc_validos=None),
    Comando("p1.login_defs", "login.defs (envejecimiento, hash, UMASK) y /etc/default/useradd (INACTIVE)",
            _por_archivo("/etc/login.defs /usr/etc/login.defs /etc/default/useradd",
                         "^[[:space:]]*(PASS_MAX_DAYS|PASS_MIN_DAYS|PASS_WARN_AGE|ENCRYPT_METHOD|UMASK|USERGROUPS_ENAB|INACTIVE)\\b", "D"),
            rc_validos=None),
    Comando("p1.perfiles", "TMOUT/umask en archivos de inicio (lectura estática; nunca se ejecutan)",
            _por_archivo(_PERFILES, "TMOUT|umask|UMASK|pam_umask|(^|[[:space:];&|(])(if|then|elif|else|fi|case|esac|for|while|until|do|done|function)([[:space:];]|$)|[{}]", "L"),
            rc_validos=None),
    Comando("p1.ssh_permisos", "Permisos de sshd_config, sshd_config.d y llaves de host (stat; no se leen llaves)",
            _stat("/etc/ssh/sshd_config /etc/ssh/sshd_config.d") + "; if [ -d /etc/ssh/sshd_config.d ] && [ ! -r /etc/ssh/sshd_config.d ]; then "
            "echo '#NO_LEGIBLE|/etc/ssh/sshd_config.d'; fi; for f in /etc/ssh/sshd_config.d/*.conf /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub; do "
            "[ -e \"$f\" ] && echo \"S|$(stat -Lc '%n|%#a|%U|%G|%F' -- \"$f\")\"; done; echo 'S_FIN'",
            rc_validos=None),
    Comando("p1.sshd", "Configuración efectiva de sshd (sshd -T, sin privilegios) y presencia de Match/Include",
            "S=\"$(command -v sshd || echo /usr/sbin/sshd)\"; o=\"$(\"$S\" -T)\"; rc=$?; echo \"SSHD_T_RC|$rc\"; "
            "printf '%s\\n' \"$o\" | grep -iE '^(" + "|".join(_SSHD_CLAVES) + ")[[:space:]]'; "
            + _por_archivo("/etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf", "^[[:space:]]*(Match|Include)[[:space:]]", "M"),
            rc_validos=None,
            sensible="sshd -T podría listar rutas de llaves de host", redaccion="Solo se emiten las claves CIS evaluadas (sin HostKey)."),
]
