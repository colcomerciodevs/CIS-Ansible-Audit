"""Dónde busca cada comando del recolector: archivos, carpetas, comandos y estado del kernel/servicios consultados.

Catálogo curado (no se deduce del texto del comando) para que cada control del reporte indique con exactitud en qué
ubicaciones se buscó la evidencia. Clave: id de comando (comandos.py / comandos_priv.py). Cada entrada es una lista de
(tipo, detalle) con tipo en: Archivo, Archivos, Carpeta, Comando, Kernel, Servicio, Base RPM.
"""

from typing import Dict, List, Tuple

Fuente = Tuple[str, str]

_MODPROBE = "/etc/modprobe.d, /run/modprobe.d, /usr/local/lib/modprobe.d, /usr/lib/modprobe.d, /lib/modprobe.d (*.conf)"
_SYSCTL = ("/etc/sysctl.conf, /etc/sysctl.d/*.conf, /run/sysctl.d/*.conf, /usr/local/lib/sysctl.d/*.conf, "
           "/usr/lib/sysctl.d/*.conf, /lib/sysctl.d/*.conf (orden de carga de systemd-sysctl)")
_INICIO_ROOT = ("~root/.bash_profile, ~root/.bash_login, ~root/.profile, ~root/.bashrc, /etc/environment, /etc/login.defs, "
                "/etc/profile, /etc/profile.d/*.sh, /etc/bashrc, /etc/bash.bashrc (líneas PATH; lectura estática, nunca se ejecutan)")

FUENTES: Dict[str, List[Fuente]] = {
    # sistema
    "sys.hostname": [("Comando", "hostname -f")],
    "sys.os_release": [("Archivo", "/etc/os-release")],
    "sys.kernel": [("Comando", "uname -r / uname -m")],
    "sys.fecha": [("Comando", "date -Is")],
    "sys.usuario": [("Comando", "id (cuenta de conexión)")],
    "sys.sudo": [("Comando", "sudo -n true (capacidad de sudo no interactivo)")],
    "sys.shells": [("Archivo", "/etc/shells")],
    "sys.uid_min": [("Archivo", "/etc/login.defs (UID_MIN)")],
    "sys.fs_nodev": [("Kernel", "/proc/filesystems (filesystems nodev)")],
    "sys.var_log_audit": [("Comando", "findmnt -T /var/log/audit (punto de montaje, dispositivo, filesystem y tamaño)")],
    "sys.selinux": [("Comando", "getenforce")],
    "sys.cmdline": [("Kernel", "/proc/cmdline (parámetros de arranque en ejecución)")],
    # paquetes y repositorios
    "pkg.rpm": [("Base RPM", "rpm -q <paquetes de los controles en alcance>")],
    "repo.dnf_conf": [("Archivo", "/etc/dnf/dnf.conf (gpgcheck)")],
    "repo.yum_repos": [("Carpeta", "/etc/yum.repos.d/*.repo (gpgcheck, enabled, baseurl; sin credenciales)")],
    "repo.dnf_repolist": [("Comando", "dnf -C repolist --enabled (solo caché local)")],
    "repo.zypp_conf": [("Archivo", "/etc/zypp/zypp.conf (gpgcheck)")],
    "repo.zypp_repos": [("Carpeta", "/etc/zypp/repos.d/*.repo")],
    "repo.zypper_lr": [("Comando", "zypper --no-refresh lr")],
    "repo.gpg_keys": [("Base RPM", "rpm -q gpg-pubkey (llaves GPG importadas)")],
    "repo.pki_rpm_gpg": [("Carpeta", "/etc/pki/rpm-gpg y /usr/lib/rpm/gnupg/keys (llaves GPG locales)")],
    "upd.dnf_check": [("Comando", "dnf -C check-update (caché local, sin red)")],
    "upd.dnf_cache_edad": [("Carpeta", "/var/cache/dnf (antigüedad de los metadatos)")],
    "upd.needs_restarting": [("Comando", "dnf -C needs-restarting -r")],
    "upd.zypper_updates": [("Comando", "zypper --no-refresh list-updates")],
    "upd.zypper_patches": [("Comando", "zypper --no-refresh list-patches")],
    "upd.zypp_cache_edad": [("Carpeta", "/var/cache/zypp/raw (antigüedad de los metadatos)")],
    # AIDE
    "aide.cron": [("Archivos", "/etc/crontab, /etc/cron.d/*, /etc/cron.{hourly,daily,weekly,monthly}/*"),
                  ("Carpeta", "/var/spool/cron/ (crontabs de usuario)")],
    "aide.timer": [("Servicio", "aidecheck.service y aidecheck.timer (systemctl is-enabled / is-active)")],
    # arranque
    "grub.archivos": [("Carpeta", "/boot (grub*.cfg, grubenv, user.cfg: modo y propietario)")],
    "grub.password": [("Archivos", "/boot/**/user.cfg (GRUB2_PASSWORD) y /boot/**/grub.cfg (superusers/password_pbkdf2); "
                                   "solo indicadores, nunca el hash")],
    "grub.cmdline": [("Kernel", "/proc/cmdline")],
    "grub.default": [("Archivo", "/etc/default/grub (GRUB_CMDLINE_LINUX)")],
    "grub.grubby": [("Comando", "grubby --info=ALL (argumentos de cada entrada de arranque)")],
    "grub.cfg_linux": [("Archivo", "/boot/grub2/grub.cfg (líneas linux)")],
    # sysctl / coredump
    "sysctl.runtime": [("Kernel", "sysctl kernel.randomize_va_space fs.suid_dumpable (valor en ejecución)")],
    "sysctl.archivos": [("Archivos", _SYSCTL)],
    "coredump.conf": [("Archivos", "/etc/systemd/coredump.conf y coredump.conf.d/*.conf (systemd-analyze cat-config)")],
    "coredump.limits": [("Archivos", "/etc/security/limits.conf y /etc/security/limits.d/*")],
    "coredump.servicio": [("Servicio", "systemd-coredump.socket / coredump.service")],
    # MAC / escritorio / crypto
    "selinux.unconfined": [("Comando", "ps -eZ (procesos con contexto unconfined_service_t)")],
    "apparmor.status": [("Comando", "apparmor_status")],
    "gdm.dconf": [("Carpeta", "/etc/dconf/profile/* y /etc/dconf/db/ (configuración dconf de GDM)")],
    "gdm.grupos_dnf": [("Comando", "dnf -C group list --installed")],
    "crypto.politica": [("Archivo", "/etc/crypto-policies/config")],
    # auditd
    "auditd.servicio": [("Servicio", "auditd (is-enabled / is-active)")],
    "auditd.conf": [("Archivo", "/etc/audit/auditd.conf")],
    "auditd.reglas_disco": [("Carpeta", "/etc/audit/rules.d/*.rules (reglas persistentes en disco)")],
    "auditd.audit_rules": [("Archivo", "/etc/audit/audit.rules (reglas fusionadas)")],
    "auditd.reglas_cargadas": [("Kernel", "auditctl -l (reglas cargadas en el kernel)")],
    "auditd.estado": [("Kernel", "auditctl -s (estado del subsistema de auditoría)")],
    "auditd.sudo_logfile": [("Archivos", "/etc/sudoers y /etc/sudoers.d/* (Defaults logfile)")],
    "auditd.kmod_symlinks": [("Archivos", "/usr/sbin/{lsmod,rmmod,insmod,modinfo,modprobe,depmod} (enlaces a kmod)")],
    # journald / rsyslog / logrotate / logs
    "journald.servicio": [("Servicio", "systemd-journald.service")],
    "journald.conf": [("Archivos", "/etc/systemd/journald.conf, /etc/systemd/journald.conf.d/*.conf, /run/systemd/journald.conf.d/*.conf, "
                                   "/usr/lib/systemd/journald.conf.d/*.conf (configuración efectiva: systemd-analyze cat-config "
                                   "systemd/journald.conf, mismo orden de precedencia que journald)")],
    "journald.tmpfiles": [("Archivos", "/etc/tmpfiles.d/systemd.conf y /usr/lib/tmpfiles.d/systemd.conf")],
    "journald.remoto": [("Servicio", "systemd-journal-upload.service, systemd-journal-remote.socket/.service")],
    "journald.upload_conf": [("Archivos", "/etc/systemd/journal-upload.conf y journal-upload.conf.d/*.conf")],
    "rsyslog.servicio": [("Servicio", "rsyslog (is-enabled / is-active)")],
    "rsyslog.conf": [("Archivos", "/etc/rsyslog.conf y /etc/rsyslog.d/*.conf (líneas sin comentarios, con nombre de archivo)")],
    "rsyslog.var_log": [("Carpeta", "/var/log/ (listado de primer nivel)")],
    "logrotate.conf": [("Archivos", "/etc/logrotate.conf y /etc/logrotate.d/*")],
    "logs.permisos": [("Carpeta", "/var/log (find -L /var/log -type f con el filtro del script CIS: modo /0137, propietario o "
                                  "grupo distinto de root)")],
    # cuentas
    "perm.cuentas": [("Archivos", "/etc/passwd, /etc/group, /etc/shadow, /etc/gshadow y sus copias '-' (modo y propietario)")],
    "usr.passwd": [("Archivo", "/etc/passwd")],
    "usr.group": [("Archivo", "/etc/group")],
    "usr.shadow_vacios": [("Archivo", "/etc/shadow (solo nombres de cuentas con contraseña vacía)")],
    "usr.root_path": [("Archivos", _INICIO_ROOT)],
    "usr.homes": [("Archivos", "/etc/passwd y /etc/shells (usuarios interactivos)"), ("Carpeta", "home de cada usuario interactivo (propietario y modo)")],
    "usr.dotfiles_raiz": [("Carpeta", "raíz del home de cada usuario interactivo (archivos ocultos: modo y propietario, sin contenido)")],
    "usr.dotfiles_completo": [("Carpeta", "todo el árbol del home de cada usuario interactivo (filesystem profundo)")],
    # filesystem profundo
    "fs.world_writable": [("Carpeta", "todos los montajes locales (find -xdev; excluye /proc, /sys, /run/user, montajes remotos)")],
    "fs.sin_dueno": [("Carpeta", "todos los montajes locales (find -xdev -nouser/-nogroup)")],
    "fs.suid_sgid": [("Carpeta", "montajes locales sin noexec/nosuid (find -xdev -perm -4000/-2000)")],
    # Parte 1
    "p1.kmod": [("Carpeta", "/lib/modules/$(uname -r) (módulos disponibles)"), ("Kernel", "/proc/modules (módulos cargados)"),
                ("Archivos", _MODPROBE)],
    "p1.montajes": [("Comando", "findmnt --mountpoint de /tmp, /dev/shm, /home, /var, /var/tmp, /var/log, /var/log/audit (opciones de montaje)"),
                    ("Archivo", "/etc/fstab")],
    "p1.unidades": [("Servicio", "unidades systemd de los servicios de la Parte 1 (is-enabled / is-active)")],
    "p1.escucha": [("Comando", "ss -lntu (puertos en escucha)"), ("Comando", "postconf -h inet_interfaces")],
    "p1.banner": [("Archivos", "/etc/motd, /etc/motd.d/*, /etc/issue, /etc/issue.net (contenido y permisos)")],
    "p1.chrony": [("Archivos", "/etc/chrony.conf, /etc/chrony.d/*.conf, /etc/chrony/chrony.conf (server/pool/sourcedir/include)")],
    "p1.ipv6": [("Kernel", "/proc/sys/net/ipv6 y /proc/cmdline (ipv6.disable)"), ("Comando", "ip -6 addr")],
    "p1.wireless": [("Kernel", "/sys/class/net/* (interfaces inalámbricas y su módulo)")],
    "p1.sysctl": [("Kernel", "/proc/sys/net/* (valores de red en ejecución)")],
    "p1.sysctl_archivos": [("Archivos", _SYSCTL)],
    "p1.cron": [("Archivos", "/etc/crontab, /etc/cron.{hourly,daily,weekly,monthly,d}, /etc/cron.allow/deny, /etc/at.allow/deny")],
    "p1.pam_su": [("Archivo", "/etc/pam.d/su (pam_wheel)")],
    "p1.pam": [("Archivos", "/etc/authselect/authselect.conf, /etc/pam.d/system-auth, password-auth (RHEL/OL) o common-* (SLES), "
                            "includes/substack, /etc/security/faillock.conf, pwhistory.conf"),
               ("Comando", "authselect current")],
    "p1.login_defs": [("Archivos", "/etc/login.defs (o /usr/etc/login.defs) y /etc/default/useradd")],
    "p1.perfiles": [("Archivos", "/etc/profile, /etc/profile.d/*.sh, /etc/bashrc, /etc/bash.bashrc, /etc/login.defs, "
                                 "/etc/default/login, /etc/pam.d/postlogin (TMOUT y umask; lectura estática)")],
    "p1.ssh_permisos": [("Archivos", "/etc/ssh/sshd_config, /etc/ssh/sshd_config.d/*.conf, /etc/ssh/ssh_host_*_key(.pub) "
                                     "(modo, propietario y ACL; nunca su contenido)")],
    "p1.sshd": [("Comando", "sshd -T (configuración efectiva)"), ("Archivos", "/etc/ssh/sshd_config y /etc/ssh/sshd_config.d/*.conf")],
    # recolector privilegiado
    "priv.ssh_efectiva": [("Comando", "sshd -t y sshd -T (configuración efectiva, lista blanca de claves CIS)"),
                          ("Archivos", "/etc/ssh/sshd_config y sus Include (bloques Match)")],
    "priv.sudo_politica": [("Archivos", "/etc/sudoers, /etc/sudoers.d/* (archivos cargados) y /etc/sudoers.* "
                                        "(use_pty, timestamp_timeout, NOPASSWD, !authenticate; nunca se transcriben las líneas)"),
                           ("Comando", "sudo -V (valor por defecto de timestamp_timeout) y visudo -c")],
    "priv.cuentas": [("Archivo", "/etc/shadow (campos 3 a 8 por cuenta; el campo de contraseña solo se clasifica)"),
                     ("Archivo", "/etc/default/useradd (INACTIVE)")],
    "priv.kmod_config": [("Archivos", _MODPROBE + "; directivas install/blacklist de cramfs, squashfs, udf, usb-storage, tipc, rds")],
    "priv.motd": [("Archivos", "/etc/motd y /etc/motd.d/* (indicadores de información del sistema; nunca el texto)")],
    "priv.pwquality": [("Archivos", "/etc/security/pwquality.conf y /etc/security/pwquality.conf.d/*.conf")],
    "priv.rsyslog_validacion": [("Comando", "rsyslogd -N1 (verificación de sintaxis de /etc/rsyslog.conf y sus include)")],
    "priv.logrotate": [("Archivos", "/etc/logrotate.conf y /etc/logrotate.d/*"), ("Comando", "logrotate -d /etc/logrotate.conf (modo depuración)")],
    "priv.journal_archivos": [("Carpeta", "/var/log/journal y /run/log/journal (profundidad 2: modo y propietario)")],
    "priv.unconfined_detalle": [("Comando", "ps (procesos unconfined_service_t), /proc/<PID>/exe y rpm -qf")],
    "priv.suid_rpm": [("Base RPM", "rpm -qa (archivos SUID/SGID declarados) y rpm -V --noscripts de esos paquetes")],
    "priv.root_path_dirs": [("Carpeta", "/root/.local/bin, /root/bin, /usr/local/sbin, /usr/local/bin, /usr/sbin, /usr/bin, /sbin, /bin "
                                        "(destino, modo y propietario)")],
    "priv.authorized_keys": [("Comando", "sshd -T (AuthorizedKeysFile, AuthorizedKeysCommand, StrictModes)"),
                             ("Archivos", "AuthorizedKeysFile de iacolcoauto y ansible_test (p. ej. ~/.ssh/authorized_keys): "
                                          "fingerprint por llave con ssh-keygen -l")],
}


def de_comandos(ids) -> List[Fuente]:
    """Ubicaciones (sin duplicados, en orden) de una lista de ids de comando; un id sin catálogo se informa por su id."""
    res: List[Fuente] = []
    for cid in ids or []:
        for f in FUENTES.get(cid, [("Comando", f"evidencia {cid}")]):
            if f not in res:
                res.append(f)
    return res


def de_fila(r: Dict) -> List[Fuente]:
    """Dónde se buscó la evidencia de una fila de resultado: comandos de sus componentes y de su evidencia, en orden."""
    ids = [i for c in r.get("componentes") or [] for i in c.get("evidencia") or []]
    ids += [e.get("comando_id") for e in r.get("evidencia") or [] if e.get("comando_id")]
    ids += list((r.get("exclusion") or {}).get("evidencia") or [])
    return de_comandos(list(dict.fromkeys(ids)))
