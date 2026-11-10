"""Evidencia SINTÉTICA de la Parte 1 para pruebas y demostración (no corresponde a ningún servidor).

Mezcla deliberada: cumplimientos, fallas reales (blacklist ausente, /var/tmp sin partición, cron.d 0755,
remember=12 frente al CIS 24), controles manuales (IPv6, complejidad) y organizacionales (banner).
Sin recolector privilegiado, sshd -T / sudoers / shadow quedan como ERROR (requiere 1.2.0); con
`privilegiado_sintetico` (salida 1.2.0: solo los 5 grupos nuevos) se evalúan.
"""

from typing import Dict, Tuple

_MOD_CONF = ("C|/etc/modprobe.d/cis.conf|1:install cramfs /bin/false\nC|/etc/modprobe.d/cis.conf|2:blacklist cramfs\n"
             "C|/etc/modprobe.d/cis.conf|3:install squashfs /bin/false\nC|/etc/modprobe.d/cis.conf|4:blacklist squashfs\n"
             "C|/etc/modprobe.d/cis.conf|5:install udf /bin/false\n"
             "C|/etc/modprobe.d/cis.conf|6:install usb-storage /bin/false\nC|/etc/modprobe.d/cis.conf|7:blacklist usb-storage\n"
             "C|/etc/modprobe.d/cis.conf|8:install dccp /bin/false\nC|/etc/modprobe.d/cis.conf|9:blacklist dccp\n"
             "C|/etc/modprobe.d/cis.conf|10:install sctp /bin/false\nC|/etc/modprobe.d/cis.conf|11:blacklist sctp\n"
             "C|/etc/modprobe.d/cis.conf|12:install rds /bin/false\nC|/etc/modprobe.d/cis.conf|13:blacklist rds\n"
             "C|/usr/lib/modprobe.d/50-tipc.conf|1:blacklist tipc")

_SYSCTL_OK = {
    "net.ipv4.ip_forward": "0", "net.ipv4.conf.all.forwarding": "0", "net.ipv4.conf.default.forwarding": "0",
    "net.ipv6.conf.all.forwarding": "0", "net.ipv6.conf.default.forwarding": "0",
    "net.ipv4.conf.all.send_redirects": "0", "net.ipv4.conf.default.send_redirects": "0",
    "net.ipv4.icmp_ignore_bogus_error_responses": "1", "net.ipv4.icmp_echo_ignore_broadcasts": "1",
    "net.ipv4.conf.all.accept_redirects": "0", "net.ipv4.conf.default.accept_redirects": "0",
    "net.ipv6.conf.all.accept_redirects": "0", "net.ipv6.conf.default.accept_redirects": "0",
    "net.ipv4.conf.all.secure_redirects": "0", "net.ipv4.conf.default.secure_redirects": "0",
    "net.ipv4.conf.all.rp_filter": "1", "net.ipv4.conf.default.rp_filter": "1",
    "net.ipv4.conf.all.accept_source_route": "0", "net.ipv4.conf.default.accept_source_route": "0",
    "net.ipv6.conf.all.accept_source_route": "0", "net.ipv6.conf.default.accept_source_route": "0",
    "net.ipv4.conf.all.log_martians": "1", "net.ipv4.conf.default.log_martians": "0",
    "net.ipv4.tcp_syncookies": "1", "net.ipv6.conf.all.accept_ra": "0", "net.ipv6.conf.default.accept_ra": "0",
    "net.ipv6.conf.all.disable_ipv6": "0", "net.ipv6.conf.default.disable_ipv6": "0",
}

# Paquetes de la Parte 1 instalados en los hosts sintéticos (el resto: 'package X is not installed').
PKG_P1 = {
    "rhel": {"chrony": "chrony-4.5-1.0.1.el9.x86_64", "cronie": "cronie-1.5.7-10.el9.x86_64", "sudo": "sudo-1.9.5p2-10.el9.x86_64",
             "rpcbind": "rpcbind-1.2.6-7.el9.x86_64", "nfs-utils": "nfs-utils-2.5.4-25.0.1.el9.x86_64", "at": "at-3.1.23-11.el9.x86_64"},
    "sles": {"chrony": "chrony-4.1-150400.21.5.1.x86_64", "cronie": "cronie-1.5.7-150400.84.3.1.x86_64", "sudo": "sudo-1.9.15p5-150600.3.3.2.x86_64",
             "postfix": "postfix-3.8.4-150600.1.1.x86_64"},
}
GRUPOS_P1 = "sugroup:1050:\nssh_keys:997:"


def salidas_p1(familia: str) -> Dict[str, Tuple[str, int, str]]:
    sles = familia == "sles"
    sys_rt = "\n".join(f"{k} = {v}" for k, v in _SYSCTL_OK.items())
    sys_files = "\n".join(["# /usr/lib/sysctl.d/50-default.conf", "net.ipv4.conf.default.rp_filter = 2", "net.ipv4.conf.*.rp_filter = 2",
                           "-net.ipv4.conf.all.rp_filter", "# /etc/sysctl.d/60-cis-red.conf"]
                          + [f"{k} = {v}" for k, v in _SYSCTL_OK.items() if "disable_ipv6" not in k])
    pam_rhel = "\n".join([
        "AUTHSELECT_CONF|custom/cis", "AUTHSELECT|Profile ID: custom/cis", "AUTHSELECT|- with-faillock",
        "F|/etc/pam.d/system-auth",
        "P|/etc/pam.d/system-auth|5:auth        required      pam_faillock.so preauth silent",
        "P|/etc/pam.d/system-auth|6:auth        sufficient    pam_unix.so",
        "P|/etc/pam.d/system-auth|7:auth        required      pam_faillock.so authfail",
        "P|/etc/pam.d/system-auth|10:account     required      pam_faillock.so",
        "P|/etc/pam.d/system-auth|11:account     required      pam_unix.so",
        "P|/etc/pam.d/system-auth|14:password    requisite     pam_pwquality.so local_users_only",
        "P|/etc/pam.d/system-auth|15:password    requisite     pam_pwhistory.so use_authtok remember=12",
        "P|/etc/pam.d/system-auth|16:password    sufficient    pam_unix.so sha512 shadow use_authtok",
        "F|/etc/pam.d/password-auth",
        "P|/etc/pam.d/password-auth|5:auth        required      pam_faillock.so preauth silent",
        "P|/etc/pam.d/password-auth|7:auth        required      pam_faillock.so authfail",
        "P|/etc/pam.d/password-auth|10:account     required      pam_faillock.so",
        "P|/etc/pam.d/password-auth|14:password    requisite     pam_pwquality.so local_users_only",
        "P|/etc/pam.d/password-auth|15:password    requisite     pam_pwhistory.so use_authtok remember=12",
        "P|/etc/pam.d/password-auth|16:password    sufficient    pam_unix.so sha512 shadow use_authtok",
        "F|/etc/authselect/custom/cis/system-auth",
        "T|/etc/authselect/custom/cis/system-auth|5:auth required pam_faillock.so preauth silent",
        "T|/etc/authselect/custom/cis/system-auth|14:password requisite pam_pwquality.so",
        "T|/etc/authselect/custom/cis/system-auth|15:password requisite pam_pwhistory.so",
        "T|/etc/authselect/custom/cis/system-auth|16:password sufficient pam_unix.so sha512",
        "F|/etc/authselect/custom/cis/password-auth",
        "T|/etc/authselect/custom/cis/password-auth|5:auth required pam_faillock.so preauth silent",
        "T|/etc/authselect/custom/cis/password-auth|14:password requisite pam_pwquality.so",
        "T|/etc/authselect/custom/cis/password-auth|15:password requisite pam_pwhistory.so",
        "T|/etc/authselect/custom/cis/password-auth|16:password sufficient pam_unix.so sha512",
    ])
    pam_sles = "\n".join([
        "F|/etc/pam.d/common-auth", "P|/etc/pam.d/common-auth|8:auth required pam_faillock.so preauth",
        "P|/etc/pam.d/common-auth|9:auth required pam_unix.so try_first_pass",
        "P|/etc/pam.d/common-auth|10:auth [default=die] pam_faillock.so authfail",
        "F|/etc/pam.d/common-account", "P|/etc/pam.d/common-account|8:account required pam_unix.so try_first_pass",
        "P|/etc/pam.d/common-account|9:account required pam_faillock.so",
        "F|/etc/pam.d/common-password",
        "P|/etc/pam.d/common-password|8:password requisite pam_pwquality.so",
        "P|/etc/pam.d/common-password|9:password required pam_pwhistory.so use_authtok remember=24",
        "P|/etc/pam.d/common-password|10:password required pam_unix.so use_authtok sha512",
    ])
    seguridad = "\n".join([
        "F|/etc/security/pwquality.conf", "S|/etc/security/pwquality.conf|11:minlen = 14",
        "F|/etc/security/pwquality.conf.d/50-cis.conf", "S|/etc/security/pwquality.conf.d/50-cis.conf|1:minclass = 4",
        "F|/etc/security/faillock.conf", "S|/etc/security/faillock.conf|9:deny = 5", "S|/etc/security/faillock.conf|10:unlock_time = 900",
    ] + ([] if sles else ["F|/etc/security/pwhistory.conf"]))
    unidades = {
        "chronyd.service": ("enabled", "active"), "crond.service": ("enabled", "active") if not sles else ("not-found", "inactive"),
        "cron.service": ("enabled", "active") if sles else ("not-found", "inactive"),
        "rpcbind.socket": ("enabled", "active") if not sles else ("not-found", "inactive"),
        "rpcbind.service": ("enabled", "inactive") if not sles else ("not-found", "inactive"),
        "nfs-server.service": ("disabled", "inactive") if not sles else ("not-found", "inactive"),
        "tmp.mount": ("generated", "active"),
    }
    from .comandos_p1 import UNIDADES_P1
    unid = "\n".join(f"{u}|{unidades.get(u, ('Failed to get unit file state for ' + u + ': No such file or directory', 'inactive'))[0]}|"
                     f"{unidades.get(u, ('', 'inactive'))[1]}" for u in UNIDADES_P1)
    return {
        "p1.kmod": ("K|/lib/modules/5.15.0/kernel/fs/cramfs/cramfs.ko.xz\nK|/lib/modules/5.15.0/kernel/fs/squashfs/squashfs.ko.xz\n"
                    "K|/lib/modules/5.15.0/kernel/fs/udf/udf.ko.xz\nK|/lib/modules/5.15.0/kernel/drivers/usb/storage/usb-storage.ko.xz\n"
                    "K|/lib/modules/5.15.0/kernel/net/sctp/sctp.ko.xz\nK|/lib/modules/5.15.0/kernel/net/rds/rds.ko.xz\n"
                    "K|/lib/modules/5.15.0/kernel/net/tipc/tipc.ko.xz\nL|xfs\nL|nf_tables\nF|/etc/modprobe.d/cis.conf\n"
                    "F|/usr/lib/modprobe.d/50-tipc.conf\n" + _MOD_CONF, 0, ""),
        "p1.montajes": ("\n".join([
            "MNT|/tmp /dev/mapper/vg-tmp xfs rw,nosuid,nodev,noexec,relatime", "MNT|/dev/shm tmpfs tmpfs rw,nosuid,nodev,noexec",
            "MNT|/home /dev/mapper/vg-home xfs rw,nosuid,nodev,relatime", "MNT|/var /dev/mapper/vg-var xfs rw,nosuid,nodev,relatime",
            "MNT|/var/tmp|NO_MONTADO", "MNT|/var/log /dev/mapper/vg-log xfs rw,nosuid,nodev,noexec,relatime",
            "MNT|/var/log/audit /dev/mapper/vg-audit xfs rw,nosuid,nodev,relatime",
            "FSTAB|/tmp|xfs|defaults,nodev,nosuid,noexec", "FSTAB|/home|xfs|defaults,nodev,nosuid", "FSTAB|/var|xfs|defaults,nodev,nosuid",
            "FSTAB|/var/log|xfs|defaults,nodev,nosuid,noexec", "FSTAB|/var/log/audit|xfs|defaults,nodev,nosuid",
            "FSTAB|/dev/shm|tmpfs|defaults,nodev,nosuid,noexec"]), 0, ""),
        "p1.unidades": (unid, 0, ""),
        "p1.escucha": ("tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\ntcp LISTEN 0 100 127.0.0.1:25 0.0.0.0:*\n"
                       + ("POSTCONF|loopback-only" if sles else "POSTCONF|NO_INSTALADO"), 0, ""),
        "p1.banner": ("S|/etc/motd|0644|root|root|regular file\nS|/etc/issue|0644|root|root|regular file\n"
                      "S|/etc/issue.net|0644|root|root|regular file\n"
                      "C|/etc/issue|Acceso restringido a personal autorizado. Toda actividad es monitoreada.\n"
                      "C|/etc/issue.net|Acceso restringido a personal autorizado. Toda actividad es monitoreada.", 0, ""),
        "p1.chrony": ("F|/etc/chrony.conf\nT|/etc/chrony.conf|3:server ntp1.ejemplo.local iburst\nT|/etc/chrony.conf|4:server ntp2.ejemplo.local iburst", 0, ""),
        "p1.ipv6": ("IPV6_KERNEL|si\nADDR|eth0|fe80::5054:ff:fe12:3456/64", 0, ""),
        "p1.wireless": ("W_FIN", 0, ""),
        "p1.sysctl": (sys_rt, 0, ""),
        "p1.sysctl_archivos": (sys_files, 0, ""),
        "p1.cron": ("S|/etc/crontab|0600|root|root|regular file\nS|/etc/cron.hourly|0700|root|root|directory\n"
                    "S|/etc/cron.daily|0700|root|root|directory\nS|/etc/cron.weekly|0700|root|root|directory\n"
                    "S|/etc/cron.monthly|0700|root|root|directory\nS|/etc/cron.d|0755|root|root|directory\n"
                    "S|/etc/cron.allow|0600|root|root|regular file\nS|/etc/cron.deny|NO_EXISTE\n"
                    "S|/etc/at.allow|0640|root|root|regular file\nS|/etc/at.deny|NO_EXISTE", 0, ""),
        "p1.pam_su": ("F|/etc/pam.d/su\nW|/etc/pam.d/su|6:auth required pam_wheel.so use_uid group=sugroup", 0, ""),
        "p1.pam": ((pam_sles if sles else pam_rhel) + "\n" + seguridad, 0, ""),
        "p1.login_defs": ("F|/etc/login.defs\nD|/etc/login.defs|117:UMASK 027\nD|/etc/login.defs|131:PASS_MAX_DAYS 42\n"
                          "D|/etc/login.defs|132:PASS_MIN_DAYS 7\nD|/etc/login.defs|133:PASS_WARN_AGE 7\n"
                          "D|/etc/login.defs|204:ENCRYPT_METHOD SHA512\nF|/etc/default/useradd\nD|/etc/default/useradd|5:INACTIVE=30", 0, ""),
        "p1.perfiles": ("F|/etc/profile\nL|/etc/profile|79:umask 027\nF|/etc/profile.d/cis.sh\n"
                        "L|/etc/profile.d/cis.sh|1:readonly TMOUT=900 ; export TMOUT\nF|/etc/login.defs\nL|/etc/login.defs|117:UMASK 027", 0, ""),
        "p1.ssh_permisos": ("S|/etc/ssh/sshd_config|0600|root|root|regular file\nS|/etc/ssh/sshd_config.d|0700|root|root|directory\n"
                            "#NO_LEGIBLE|/etc/ssh/sshd_config.d\nS|/etc/ssh/ssh_host_ed25519_key|0640|root|ssh_keys|regular file\n"
                            "S|/etc/ssh/ssh_host_ed25519_key.pub|0644|root|root|regular file\nS_FIN", 0, ""),
        "p1.sshd": ("SSHD_T_RC|1\n#NO_LEGIBLE|/etc/ssh/sshd_config", 0, "sshd: no hostkeys available -- exiting."),
    }


# ----------------------------------------------------------------------------- recolector privilegiado 1.2.0
_SSH_K = ("loglevel|VERBOSE", "disableforwarding|yes", "maxauthtries|4", "ignorerhosts|yes", "hostbasedauthentication|no",
          "permitrootlogin|no", "permitemptypasswords|no", "permituserenvironment|no", "clientaliveinterval|15",
          "clientalivecountmax|3", "logingracetime|60", "banner|/etc/issue.net", "usepam|yes", "maxstartups|10:30:60", "maxsessions|10")


def salidas_privilegiadas(familia: str, sudoers: str, ol8: bool = False) -> Dict[str, Tuple[str, int, str]]:
    """Salida sintética de los 5 grupos de 1.2.0 (formato normalizado: sin hash, sin líneas de sudoers).

    sudoers: texto del /etc/sudoers.d/cis_audit "instalado" (sus SHA físico y semántico van en la línea INSTR)."""
    from .privilegiado import sha_instrumentacion, sha_sudoers_archivo
    sles = familia == "sles"
    ssh = ["CONFIG|/etc/ssh/sshd_config", "SSHD_SINTAXIS|0"] + (["OPCIONES_OL8|ok|n=0"] if ol8 else []) + ["SSHD_T_RC|0"]
    ssh += [f"K|{k}" for k in _SSH_K]
    ssh += ["ACCESO|allowusers|0", "ACCESO|allowgroups|2", "ACCESO|denyusers|0", "ACCESO|denygroups|0"]
    if sles:
        ssh += ["INCLUDE|0", "MATCH|0"]
    else:  # un Match que repite PermitRootLogin (no altera) y redefine MaxSessions (altera -> MANUAL)
        ssh += ["INCLUDE|1", "MATCH|1", "MATCH_BLOQUE|1|/etc/ssh/sshd_config.d/50-sftp.conf|Group|permitrootlogin=no;maxsessions=20"]
    sudo = ["ARCHIVO|/etc/sudoers|cargado=1"] + ([] if sles else ["TIMEOUT|15|global|/etc/sudoers|12|cargado=1"])
    sudo += ["USE_PTY|/etc/sudoers|presente=1|negado=0|cargado=1"]
    if sles:  # otra regla NOPASSWD además de la instrumentación -> la exclusión NO se aplica
        sudo += ["ARCHIVO|/etc/sudoers.d/admins|cargado=1", "NOPASSWD|/etc/sudoers.d/admins|3|%wheel|cargado=1",
                 "USE_PTY|/etc/sudoers.d/admins|presente=0|negado=0|cargado=1"]
    sudo += ["ARCHIVO|/etc/sudoers.d/cis_audit|cargado=1", "NOPASSWD|/etc/sudoers.d/cis_audit|37|srvinnops|cargado=1",
             "USE_PTY|/etc/sudoers.d/cis_audit|presente=0|negado=0|cargado=1",
             f"INSTR|existe=1|440|0|0|tipo=regular_file|sha={sha_instrumentacion(sudoers)}|sha_archivo={sha_sudoers_archivo(sudoers)}",
             "TIMEOUT_DEFAULT|5.0 minutes", "VISUDO|0"]
    import datetime as _dt
    from .simulacion import HOY
    h = (HOY - _dt.date(1970, 1, 1)).days
    ctas = [f"U|root|hash=1|bloqueada=0|vacia=0|{h - 70}|1|365|7|30|", f"U|bin|hash=0|bloqueada=1|vacia=0|{h - 900}|0|99999|7||",
            f"U|sshd|hash=0|bloqueada=1|vacia=0|{h - 900}|||||", f"U|operador|hash=1|bloqueada=0|vacia=0|{h - 20}|1|90|7|30|",
            f"U|app|hash=1|bloqueada=0|vacia=0|{h + 3}|0|99999|7||", "INACTIVE_DEFAULT|30|/etc/default/useradd", f"HOY|{h}"]
    perm = ("S|/etc/ssh/sshd_config|0600|root|root|regular file|acl_escritura=0\n"
            "S|/etc/ssh/sshd_config.d|0700|root|root|directory|acl_escritura=0\n"
            + ("" if sles else "S|/etc/ssh/sshd_config.d/50-sftp.conf|0600|root|root|regular file|acl_escritura=0\n")
            + "S|/etc/ssh/ssh_host_ed25519_key|0640|root|ssh_keys|regular file|acl_escritura=0\n"
            "S|/etc/ssh/ssh_host_ed25519_key.pub|0644|root|root|regular file|acl_escritura=0\nS_FIN")
    res = {"priv.ssh_efectiva": ("\n".join(ssh), 0, ""), "p1.ssh_permisos": (perm, 0, ""),
           "priv.sudo_politica": ("\n".join(sudo), 0, ""), "priv.cuentas": ("\n".join(ctas), 0, ""),
           "p1.sysctl_archivos": salidas_p1(familia)["p1.sysctl_archivos"]}
    res.update(salidas_privilegiadas_1_3(familia))
    res.update(salidas_privilegiadas_1_4(familia))
    res.update(salidas_privilegiadas_1_5())
    return res


def salidas_privilegiadas_1_5() -> Dict[str, Tuple[str, int, str]]:
    """Salida SINTÉTICA del grupo añadido en 1.5.0 (PRE-3): solo fingerprints y metadatos, nunca llaves."""
    ak = ["AKCFG|archivos=.ssh/authorized_keys|comando=0|strictmodes=yes",
          "AKU|iacolcoauto|existe|home=/home/iacolcoauto|0700|iacolcoauto|iacolcoauto",
          "AKA|iacolcoauto|/home/iacolcoauto/.ssh/authorized_keys|enlace=0|regular_file|0600|iacolcoauto|iacolcoauto|"
          "dir=0700|iacolcoauto|iacolcoauto|lineas=1",
          "AKK|iacolcoauto|/home/iacolcoauto/.ssh/authorized_keys|256|SHA256:/DQNJlEXFV0xqVbwj/03ztVShuKy7xy7MyaFeJaW5uY|(ED25519)",
          "AKU|ansible_test|existe|home=/home/ansible_test|0700|ansible_test|ansible_test",
          "AKA|ansible_test|/home/ansible_test/.ssh/authorized_keys|enlace=0|regular_file|0600|ansible_test|ansible_test|"
          "dir=0700|ansible_test|ansible_test|lineas=1",
          "AKK|ansible_test|/home/ansible_test/.ssh/authorized_keys|256|SHA256:ab8PCAkrtbmcjQj0lbF1CRf2bDtqVdbc0XTj/Fm26rE|(ED25519)",
          "AK_FIN"]
    return {"priv.authorized_keys": ("\n".join(ak), 0, "")}


def salidas_privilegiadas_1_3(familia: str) -> Dict[str, Tuple[str, int, str]]:
    """Salida SINTÉTICA de los 3 grupos de 1.3.0 (formato cerrado; nunca texto de banner ni comandos install)."""
    kmod = ["KF|/etc/modprobe.d/CIS.conf|regular"]
    kmod += [f"KC|/etc/modprobe.d/CIS.conf|{2 * i + 1}|install|{m}|false" for i, m in enumerate(("cramfs", "squashfs", "udf", "usb_storage", "tipc", "rds"))]
    kmod += [f"KC|/etc/modprobe.d/CIS.conf|{2 * i + 2}|blacklist|{m}|" for i, m in enumerate(("cramfs", "squashfs", "udf", "usb_storage", "tipc", "rds"))]
    kmod += ["KF|/usr/lib/modprobe.d/dist-blacklist.conf|regular", "K_FIN"]
    motd = ["MO|/etc/motd|tipo=regular_file|legible=1|bytes=120|esc_v=0|esc_r=0|esc_m=0|esc_s=0|id_so=0",
            "MO|/etc/motd.d/cockpit|tipo=symbolic_link:regular_file|legible=1|bytes=69|esc_v=0|esc_r=0|esc_m=0|esc_s=0|id_so=0", "MO_FIN"]
    pwq = ["PF|/etc/security/pwquality.conf", "PF|/etc/security/pwquality.conf.d/50-pwcomplexity.conf",
           "PQ|/etc/security/pwquality.conf.d/50-pwcomplexity.conf|1|minclass|4",
           "PF|/etc/security/pwquality.conf.d/50-pwlength.conf", "PQ|/etc/security/pwquality.conf.d/50-pwlength.conf|1|minlen|14", "PQ_FIN"]
    return {"priv.kmod_config": ("\n".join(kmod), 0, ""), "priv.motd": ("\n".join(motd), 0, ""), "priv.pwquality": ("\n".join(pwq), 0, "")}


def salidas_privilegiadas_1_4(familia: str) -> Dict[str, Tuple[str, int, str]]:
    """Salida SINTÉTICA de los 6 grupos añadidos en 1.4.0 (formato cerrado del recolector)."""
    rn = ["RN_OUT|rsyslogd: version 8.2310.0, config validation run (level 1), master config /etc/rsyslog.conf",
          "RN_OUT|rsyslogd: End of config validation run. Bye.", "RN_RC|0"]
    lr = ["LC|/etc/logrotate.conf|1:weekly", "LC|/etc/logrotate.conf|2:rotate 4", "LC|/etc/logrotate.conf|3:create",
          "LC|/etc/logrotate.conf|4:include /etc/logrotate.d",
          "LC|/etc/logrotate.d/rsyslog|1:/var/log/messages", "LC|/etc/logrotate.d/rsyslog|2:/var/log/secure", "LC|/etc/logrotate.d/rsyslog|3:{",
          "LC|/etc/logrotate.d/rsyslog|4:    missingok", "LC|/etc/logrotate.d/rsyslog|5:    sharedscripts",
          "LC|/etc/logrotate.d/rsyslog|6:    postrotate", "LC|/etc/logrotate.d/rsyslog|7:        /usr/bin/systemctl -s HUP kill rsyslog.service >/dev/null 2>&1 || true",
          "LC|/etc/logrotate.d/rsyslog|8:    endscript", "LC|/etc/logrotate.d/rsyslog|9:}",
          "LD|reading config file /etc/logrotate.conf", "LD|rotating pattern: /var/log/messages", "LD_RC|0", "L_FIN"]
    jr = ["J|d|2755|root|systemd-journal|/var/log/journal", "J|d|2755|root|systemd-journal|/var/log/journal/0123456789abcdef",
          "J|f|640|root|systemd-journal|/var/log/journal/0123456789abcdef/system.journal", "JD|/run/log/journal|NO_EXISTE", "J_FIN"]
    rp = [f"PD|{d}|NO_EXISTE" for d in ("/root/.local/bin", "/root/bin")]
    rp += [f"PD|{d}|enlace=0|destino={d}|0755|root|root|directory" for d in ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin")]
    rp += ["PD|/sbin|enlace=1|destino=/usr/sbin|0755|root|root|directory", "PD|/bin|enlace=1|destino=/usr/bin|0755|root|root|directory", "PD_FIN"]
    res = {"priv.rsyslog_validacion": ("\n".join(rn), 0, ""), "priv.logrotate": ("\n".join(lr), 0, ""),
           "priv.journal_archivos": ("\n".join(jr), 0, ""), "priv.root_path_dirs": ("\n".join(rp), 0, "")}
    if familia != "sles":
        res["priv.unconfined_detalle"] = ("UP_FIN", 0, "")
        res["priv.suid_rpm"] = ("RS|4755|/usr/bin/passwd|passwd\nRS|4755|/usr/bin/sudo|sudo\nRS_FIN", 0, "")
    return res


def privilegiado_sintetico(benchmark: str, sudoers: str, version: str = None) -> str:
    """raw/privilegiado.out sintético del recolector principal (por defecto la versión vigente) con los grupos
    privilegiados de la Parte 1 y los añadidos posteriormente (los grupos compartidos con la Parte 2 no se incluyen, de
    modo que la evidencia de la Parte 2 es la misma que sin recolector privilegiado). Solo para demo y pruebas."""
    from .comandos import COMANDOS
    from .privilegiado import VERSION, VERSION_MINIMA, version_tupla
    from .seleccion import FAMILIA
    from .simulacion import HOY
    version = version or VERSION
    familia = FAMILIA[benchmark]
    out = [f"@@CIS_RECOLECTOR|formato=1|familia={familia}|benchmark={benchmark}|profunda=0|sin_sudo=0|privilegiado=1|version={version}"]
    for cid, (o, rc, e) in salidas_privilegiadas(familia, sudoers, benchmark in ("ORACLE8", "RHEL8")).items():
        if version_tupla(VERSION_MINIMA.get(cid, VERSION)) > version_tupla(version):
            continue
        out.append(f"@@CIS_BEGIN|{cid}|sudo=1|{HOY.isoformat()}T10:05:00-05:00\n@@CIS_CMD|{COMANDOS[cid].cmd}\n{o}\n\n"
                   f"@@CIS_STDERR|{cid}\n{e}\n@@CIS_END|{cid}|rc={rc}|ms=15")
    out.append("@@CIS_FIN")
    return "\n".join(out) + "\n"
