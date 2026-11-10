"""Evidencia SINTÉTICA para pruebas y demostración (no corresponde a ningún servidor).

Genera texto con el formato exacto del recolector para un host Oracle Linux 9 y
un SLES 15 con una mezcla deliberada de cumplimientos, fallas, exclusiones,
conflictos de baseline y controles profundos omitidos.
"""

from datetime import date, timedelta
from typing import Dict, Tuple

from .comandos import COMANDOS

HOY = date.today()


def _bloque(cid: str, out: str, rc: int = 0, err: str = "", omitido: str = None) -> str:
    c = COMANDOS[cid]
    s = f"@@CIS_BEGIN|{cid}|sudo={int(c.sudo)}|{HOY.isoformat()}T10:00:00-05:00\n"
    if omitido:
        return s + f"@@CIS_SKIP|{cid}|{omitido}\n@@CIS_END|{cid}|rc=-1|ms=0\n"
    return s + f"@@CIS_CMD|{c.cmd}\n{out}\n\n@@CIS_STDERR|{cid}\n{err}\n@@CIS_END|{cid}|rc={rc}|ms=12\n"


_REGLAS_DISCO = """-D
-b 8192
-f 1
-w /etc/sudoers -p wa -k scope
-w /etc/sudoers.d -p wa -k scope
-a always,exit -F arch=b64 -C euid!=uid -F auid!=unset -S execve -k user_emulation
-a always,exit -F arch=b32 -C euid!=uid -F auid!=unset -S execve -k user_emulation
-w /var/log/sudo.log -p wa -k sudo_log_file
-a always,exit -F arch=b64 -S adjtimex,settimeofday -k time-change
-a always,exit -F arch=b32 -S adjtimex,settimeofday -k time-change
-a always,exit -F arch=b64 -S clock_settime -F a0=0x0 -k time-change
-a always,exit -F arch=b32 -S clock_settime -F a0=0x0 -k time-change
-w /etc/localtime -p wa -k time-change
-a always,exit -F arch=b64 -S sethostname,setdomainname -k system-locale
-a always,exit -F arch=b32 -S sethostname,setdomainname -k system-locale
-w /etc/issue -p wa -k system-locale
-w /etc/issue.net -p wa -k system-locale
-w /etc/hosts -p wa -k system-locale
-w /etc/hostname -p wa -k system-locale
-w /etc/sysconfig/network -p wa -k system-locale
-w /etc/sysconfig/network-scripts -p wa -k system-locale
-w /etc/NetworkManager -p wa -k system-locale
-a always,exit -F arch=b64 -S creat,open,openat,truncate,ftruncate -F exit=-EACCES -F auid>=1000 -F auid!=unset -k access
-a always,exit -F arch=b64 -S creat,open,openat,truncate,ftruncate -F exit=-EPERM -F auid>=1000 -F auid!=unset -k access
-a always,exit -F arch=b32 -S creat,open,openat,truncate,ftruncate -F exit=-EACCES -F auid>=1000 -F auid!=unset -k access
-a always,exit -F arch=b32 -S creat,open,openat,truncate,ftruncate -F exit=-EPERM -F auid>=1000 -F auid!=unset -k access
-w /etc/group -p wa -k identity
-w /etc/passwd -p wa -k identity
-w /etc/gshadow -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/security/opasswd -p wa -k identity
-w /etc/nsswitch.conf -p wa -k identity
-w /etc/pam.conf -p wa -k identity
-w /etc/pam.d -p wa -k identity
-a always,exit -F arch=b64 -S chmod,fchmod,fchmodat,chown,fchown,lchown,fchownat,setxattr,lsetxattr,fsetxattr,removexattr,lremovexattr,fremovexattr -F auid>=1000 -F auid!=unset -k perm_mod
-a always,exit -F arch=b32 -S chmod,fchmod,fchmodat,chown,fchown,lchown,fchownat,setxattr,lsetxattr,fsetxattr,removexattr,lremovexattr,fremovexattr -F auid>=1000 -F auid!=unset -k perm_mod
-a always,exit -F arch=b64 -S mount -F auid>=1000 -F auid!=unset -k mounts
-a always,exit -F arch=b32 -S mount -F auid>=1000 -F auid!=unset -k mounts
-w /var/run/utmp -p wa -k session
-w /var/log/wtmp -p wa -k session
-w /var/log/btmp -p wa -k session
-w /var/log/lastlog -p wa -k logins
-w /var/run/faillock -p wa -k logins
-a always,exit -F arch=b64 -S unlink,unlinkat,rename,renameat -F auid>=1000 -F auid!=unset -k delete
-a always,exit -F arch=b32 -S unlink,unlinkat,rename,renameat -F auid>=1000 -F auid!=unset -k delete
{mac}
-a always,exit -F path=/usr/bin/chcon -F perm=x -F auid>=1000 -F auid!=unset -k perm_chng
-a always,exit -F path=/usr/bin/setfacl -F perm=x -F auid>=1000 -F auid!=unset -k perm_chng
-a always,exit -F path=/usr/sbin/usermod -F perm=x -F auid>=1000 -F auid!=unset -k usermod
-a always,exit -F arch=b64 -S init_module,finit_module,delete_module,create_module,query_module -F auid>=1000 -F auid!=unset -k kernel_modules
-a always,exit -F path=/usr/bin/kmod -F perm=x -F auid>=1000 -F auid!=unset -k kernel_modules
-e 2"""


def _cargadas(disco: str, quitar: Tuple[str, ...]) -> str:
    out = []
    for l in disco.splitlines():
        if l.startswith(("-D", "-b", "-f", "-e")) or any(q in l for q in quitar):
            continue
        l = l.replace("auid!=unset", "auid!=-1").replace("/var/run/", "/run/")
        if " -k " in l and l.startswith("-a"):
            l = l.replace(" -k ", " -F key=")
        if l.startswith("-a") and "path=" in l:
            l = l.replace("-a always,exit", "-a always,exit -S all")
        out.append(l)
    return "\n".join(out)


def salidas(familia: str) -> Dict[str, Tuple[str, int, str]]:
    sles = familia == "sles"
    mac = ("-w /etc/apparmor -p wa -k MAC-policy\n-w /usr/share/apparmor -p wa -k MAC-policy" if sles
           else "-w /etc/selinux -p wa -k MAC-policy\n-w /usr/share/selinux -p wa -k MAC-policy")
    disco = _REGLAS_DISCO.format(mac=mac)
    cache = (HOY - timedelta(days=2)).isoformat()
    s: Dict[str, Tuple[str, int, str]] = {
        "sys.hostname": ("demo-sles15-01.ejemplo.local" if sles else "demo-ol9-01.ejemplo.local", 0, ""),
        "sys.os_release": (('NAME="SLES"\nVERSION="15-SP6"\nVERSION_ID="15.6"\nPRETTY_NAME="SUSE Linux Enterprise Server 15 SP6"\nID="sles"\nID_LIKE="suse"'
                            if sles else 'NAME="Oracle Linux Server"\nVERSION="9.4"\nID="ol"\nID_LIKE="fedora"\nVERSION_ID="9.4"\nPRETTY_NAME="Oracle Linux Server 9.4"'), 0, ""),
        "sys.kernel": ("kernel=6.4.0-150600.23-default\narch=x86_64" if sles else "kernel=5.15.0-208.159.3.el9uek.x86_64\narch=x86_64", 0, ""),
        "sys.fecha": (f"{HOY.isoformat()}T10:00:00-05:00", 0, ""),
        "sys.usuario": ("user=auditor_cis uid=1500", 0, ""),
        "sys.sudo": ("SUDO_OK", 0, ""),
        "sys.shells": ("/bin/sh\n/bin/bash\n/usr/bin/sh\n/usr/bin/bash", 0, ""),
        "sys.uid_min": ("1000", 0, ""),
        "sys.fs_nodev": ("sysfs\ntmpfs\nproc\ncgroup2", 0, ""),
        "sys.var_log_audit": ("/var/log/audit /dev/mapper/vg-audit xfs 16106127360" if not sles else "/ /dev/sda2 btrfs 42949672960", 0, ""),
        "pkg.rpm": ("\n".join([
            "aide-0.16-100.el9.x86_64" if not sles else "aide-0.18.6-150600.1.3.x86_64",
            "audit-3.1.2-2.el9.x86_64" if not sles else "audit-3.0.6-150400.4.16.1.x86_64",
            "audit-libs-3.1.2-2.el9.x86_64" if not sles else "package audit-libs is not installed",
            "rsyslog-8.2310.0-4.el9.x86_64" if not sles else "rsyslog-8.2406.0-150600.12.3.1.x86_64",
            "package systemd-journal-remote is not installed", "package setroubleshoot is not installed",
            "package mcstrans is not installed", "package gdm is not installed", "package gdm3 is not installed",
            "package apparmor-parser is not installed" if not sles else "apparmor-parser-3.1.7-150600.5.3.1.x86_64",
            "package apparmor-profiles is not installed" if not sles else "apparmor-profiles-3.1.7-150600.5.3.1.noarch",
            "package apparmor-utils is not installed" if not sles else "apparmor-utils-3.1.7-150600.5.3.1.noarch",
            "package libapparmor1 is not installed" if not sles else "libapparmor1-3.1.7-150600.5.3.1.x86_64",
            "crypto-policies-20240201-1.git9f501f3.el9.noarch" if not sles else "crypto-policies-20230920.570ea89-150600.1.6.noarch"]), 1, ""),
        "repo.gpg_keys": (("gpg-pubkey-39db7c82-5f68629b|SuSE Package Signing Key <build@suse.de>|gpg(SuSE Package Signing Key)" if sles
                           else "gpg-pubkey-8d8b756f-629e59ec|Oracle OSS group <build@oss.oracle.com>|gpg(Oracle OSS group)"), 0, ""),
        "repo.pki_rpm_gpg": ("-rw-r--r--. 1 root root 1786 RPM-GPG-KEY-oracle", 0, ""),
        "aide.cron": ("#SUDO=sudo -n\n/etc/cron.d/aide:0 3 * * * root /usr/sbin/aide --check", 0, ""),
        "aide.timer": ("aidecheck.service|Failed to get unit file state for aidecheck.service: No such file or directory|inactive\naidecheck.timer|disabled|inactive", 0, ""),
        "grub.password": (("GRUB|/boot/grub2/grub.cfg|superusuario_grub_configurado=true|password_pbkdf2_configurada=true" if sles
                           else "GRUB|/boot/grub2/user.cfg|password_grub_configurada=true|formato_pbkdf2_sha512=true\n"
                                "GRUB|/boot/grub2/grub.cfg|superusuario_grub_configurado=true|password_pbkdf2_configurada=true"), 0, ""),
        "grub.archivos": ("/boot/grub2/grub.cfg|0600|root|root\n/boot/grub2/grubenv|0644|root|root\n/boot/efi/EFI/redhat/grub.cfg|0700|root|root", 0, ""),
        "grub.cmdline": ("BOOT_IMAGE=(hd0,gpt2)/vmlinuz ro audit=1 audit_backlog_limit=8192", 0, ""),
        "grub.default": ('GRUB_CMDLINE_LINUX="crashkernel=auto audit=1 audit_backlog_limit=8192' + (' apparmor=1 security=apparmor"' if sles else '"'), 0, ""),
        "sysctl.runtime": ("kernel.randomize_va_space = 2\nfs.suid_dumpable = 0", 0, ""),
        "sysctl.archivos": ("# /usr/lib/sysctl.d/50-default.conf\nkernel.randomize_va_space = 2\nfs.suid_dumpable = 0\n# /etc/sysctl.d/60-cis.conf\nkernel.randomize_va_space = 2", 0, ""),
        "crypto.politica": ("DEFAULT\nDEFAULT", 0, ""),
        "gdm.dconf": ("", 0, ""),
        "auditd.servicio": ("auditd|enabled|active", 0, ""),
        "auditd.conf": ("log_file = /var/log/audit/audit.log\nmax_log_file = 200\nnum_logs = 50\nmax_log_file_action = ROTATE\n"
                        "space_left_action = email\ndisk_full_action = SYSLOG\ndisk_error_action = SYSLOG", 0, ""),
        "auditd.reglas_disco": ("\n".join(f"/etc/audit/rules.d/50-cis.rules:{l}" for l in disco.splitlines()), 0, ""),
        "auditd.audit_rules": (disco, 0, ""),
        "auditd.reglas_cargadas": (_cargadas(disco, ("setfacl",)), 0, ""),
        "auditd.estado": ("enabled 2\nfailure 1\npid 812\nbacklog_limit 8192", 0, ""),
        "auditd.sudo_logfile": ('Defaults logfile="/var/log/sudo.log"', 0, ""),
        "auditd.kmod_symlinks": ("\n".join(f"/usr/sbin/{x}|/usr/bin/kmod" for x in ("lsmod", "rmmod", "insmod", "modinfo", "modprobe", "depmod")) + "\nkmod|/usr/bin/kmod", 0, ""),
        "journald.servicio": ("systemd-journald.service|static|active", 0, ""),
        "journald.conf": ("# /etc/systemd/journald.conf\n[Journal]\n#Storage=auto\nCompress=yes\nForwardToSyslog=yes\nStorage=persistent\n"
                          "SystemMaxUse=4G\nSystemKeepFree=1G\nRuntimeMaxUse=200M\nRuntimeKeepFree=50M\nMaxFileSec=1month", 0, ""),
        "journald.tmpfiles": ("/usr/lib/tmpfiles.d/systemd.conf:z /var/log/journal 2755 root systemd-journal - -\n"
                              "/usr/lib/tmpfiles.d/systemd.conf:a+ /var/log/journal - - - - d:group:adm:r-x", 0, ""),
        "journald.remoto": ("systemd-journal-upload.service|Failed to get unit file state for systemd-journal-upload.service: No such file or directory|inactive\n"
                            "systemd-journal-remote.socket|Failed to get unit file state for systemd-journal-remote.socket: No such file or directory|inactive\n"
                            "systemd-journal-remote.service|Failed to get unit file state for systemd-journal-remote.service: No such file or directory|inactive", 0, ""),
        "journald.upload_conf": ("", 2, ""),
        "rsyslog.servicio": ("rsyslog|enabled|active", 0, ""),
        "rsyslog.conf": ("/etc/rsyslog.conf:$FileCreateMode 0640\n/etc/rsyslog.conf:*.info;mail.none;authpriv.none;cron.none /var/log/messages\n"
                         "/etc/rsyslog.conf:authpriv.* /var/log/secure\n"
                         '/etc/rsyslog.d/90-siem.conf:*.* action(type="omfwd" target="siem.ejemplo.local" port="514" protocol="tcp")', 0, ""),
        "rsyslog.var_log": ("-rw-------. 1 root root 1048576 messages\n-rw-------. 1 root root 20480 secure", 0, ""),
        "logrotate.conf": ("/etc/logrotate.conf:weekly\n/etc/logrotate.conf:rotate 4\n/etc/logrotate.conf:include /etc/logrotate.d\nrsyslog\nsssd", 0, ""),
        "logs.permisos": ("/var/log/messages^0644^root^0^root^0\n/var/log/wtmp^0664^root^0^utmp^22", 0, ""),
        "perm.cuentas": ("\n".join([
            "/etc/passwd|0644|root|0|root|0", "/etc/passwd-|0644|root|0|root|0", "/etc/group|0644|root|0|root|0",
            "/etc/group-|0644|root|0|root|0",
            ("/etc/shadow|0640|root|0|shadow|15" if sles else "/etc/shadow|0|root|0|root|0"),
            ("/etc/shadow-|0640|root|0|shadow|15" if sles else "/etc/shadow-|0|root|0|root|0"),
            ("/etc/gshadow|0640|root|0|shadow|15" if sles else "/etc/gshadow|0|root|0|root|0"),
            ("/etc/gshadow-|0644|root|0|root|0" if sles else "/etc/gshadow-|0400|root|0|root|0")]), 0, ""),
        "usr.passwd": ("root:x:0:0:/root:/bin/bash\nbin:x:1:1:/bin:/sbin/nologin\nsshd:x:74:74:/usr/share/empty.sshd:/sbin/nologin\n"
                       "operador:x:1001:1001:/home/operador:/bin/bash\napp:x:1002:1002:/opt/app:/bin/sh", 0, ""),
        "usr.group": ("root:0:\nbin:1:\nsshd:74:\noperador:1001:\napp:1002:\nutmp:22:\nshadow:15:", 0, ""),
        "usr.shadow_vacios": ("", 0, ""),
        "usr.root_path": ("S|root|/root|/bin/bash\nF|/etc/environment|root|root|0644\nF|/etc/profile|root|root|0644\n"
                          "F|/etc/profile.d/*.sh|NO_EXISTE\nF|/root/.bash_profile|root|root|0600\n"
                          "L|/root/.bash_profile|4:PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:$HOME/bin\n"
                          "L|/root/.bash_profile|5:export PATH\n"
                          "D|/usr/local/sbin|0755|root|directory\nD|/usr/local/bin|0755|root|directory\nD|/usr/sbin|0555|root|directory\n"
                          "D|/usr/bin|0555|root|directory\nD|/root/bin|NO_EXISTE", 0, ""),
        "usr.homes": ("#SUDO=sudo -n\nroot|/root|/bin/bash|root|0550\noperador|/home/operador|/bin/bash|operador|0700\napp|/opt/app|/bin/sh|root|0755", 0, ""),
        "usr.dotfiles_raiz": ("#SUDO=sudo -n\noperador|operador|644|operador|operador|/home/operador/.bashrc\n"
                              "operador|operador|600|operador|operador|/home/operador/.bash_history\n"
                              "operador|operador|640|operador|operador|/home/operador/.netrc\nroot|root|644|root|root|/root/.bashrc", 0, ""),
    }
    if sles:
        s.update({
            "repo.zypp_conf": ("", 1, ""),
            "repo.zypp_repos": ("/etc/zypp/repos.d/SLES15-SP6-Pool.repo:[SLES15-SP6-Pool]\n/etc/zypp/repos.d/SLES15-SP6-Pool.repo:enabled=1\n"
                                "/etc/zypp/repos.d/SLES15-SP6-Pool.repo:gpgcheck=1\n"
                                "/etc/zypp/repos.d/SLES15-SP6-Pool.repo:baseurl=https://updates.suse.com/SUSE/Products/SLE-Product-SLES/15-SP6/x86_64/product?credentials=SCCcredentials", 0, ""),
            "repo.zypper_lr": ("# | Alias | Name | Enabled | GPG Check | Refresh\n1 | SLES15-SP6-Pool | SLES15-SP6-Pool | Yes | (r ) Yes | No", 0, ""),
            "upd.zypper_updates": ("Loading repository data...\nNo updates found.", 0, ""),
            "upd.zypper_patches": ("Loading repository data...\nRepository | Name | Category | Severity | Interactive | Status | Summary\n"
                                   "-----------+------+----------+----------+-------------+--------+--------\n"
                                   "SLES15-SP6-Updates | SUSE-SLE-Module-Basesystem-15-SP6-2024-1234 | security | important | --- | needed | Security update for openssl", 0, ""),
            "upd.zypp_cache_edad": (f"{cache} /var/cache/zypp/raw/SLES15-SP6-Pool", 0, ""),
            "grub.cfg_linux": ("linux /boot/vmlinuz-6.4.0-150600.23-default root=UUID=x ro apparmor=1 security=apparmor audit=1\n"
                               "linux /boot/vmlinuz-6.4.0-150600.21-default root=UUID=x ro audit=1", 0, ""),
            "coredump.limits": ("/etc/security/limits.d/99-cis.conf:* hard core 0", 0, ""),
            "coredump.servicio": ("systemd-coredump.socket|static|inactive\ncoredump.service|Failed to get unit file state for coredump.service: No such file or directory|inactive", 0, ""),
            "apparmor.status": ("apparmor module is loaded.\n42 profiles are loaded.\n40 profiles are in enforce mode.\n2 profiles are in complain mode.\n"
                                "3 processes have profiles defined.\n3 processes are in enforce mode.\n0 processes are in complain mode.\n"
                                "0 processes are unconfined but have a profile defined.", 0, ""),
        })
    else:
        s.update({
            "sys.selinux": ("Enforcing", 0, ""),
            "repo.dnf_conf": ("/etc/dnf/dnf.conf:gpgcheck=1", 0, ""),
            "repo.yum_repos": ("/etc/yum.repos.d/oracle-linux-ol9.repo:[ol9_baseos_latest]\n/etc/yum.repos.d/oracle-linux-ol9.repo:name=Oracle Linux 9 BaseOS Latest\n"
                               "/etc/yum.repos.d/oracle-linux-ol9.repo:baseurl=https://yum.oracle.com/repo/OracleLinux/OL9/baseos/latest/$basearch/\n"
                               "/etc/yum.repos.d/oracle-linux-ol9.repo:gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-oracle\n"
                               "/etc/yum.repos.d/oracle-linux-ol9.repo:gpgcheck=1\n/etc/yum.repos.d/oracle-linux-ol9.repo:enabled=1\n"
                               "/etc/yum.repos.d/interno.repo:[interno_app]\n/etc/yum.repos.d/interno.repo:baseurl=https://usuario:Secreto123@repo.interno.ejemplo/app/\n"
                               "/etc/yum.repos.d/interno.repo:gpgcheck=0\n/etc/yum.repos.d/interno.repo:enabled=1", 0, ""),
            "repo.dnf_repolist": ("ol9_baseos_latest  Oracle Linux 9 BaseOS Latest\ninterno_app  App interna", 0, ""),
            "upd.dnf_check": ("openssl.x86_64  1:3.0.7-27.0.1.el9  ol9_baseos_latest\nkernel-uek.x86_64  5.15.0-209.161.7.el9uek  ol9_UEKR7", 100, ""),
            "upd.dnf_cache_edad": (f"{cache} /var/cache/dnf/ol9_baseos_latest.solv", 0, ""),
            "upd.needs_restarting": ("No core libraries or services have been updated since boot-up.\nReboot should not be necessary.", 0, ""),
            "grub.grubby": ('kernel="/boot/vmlinuz-5.15.0-208.159.3.el9uek.x86_64"\nargs="ro crashkernel=auto audit=1 audit_backlog_limit=8192"\n'
                            'kernel="/boot/vmlinuz-5.14.0-427.13.1.el9_4.x86_64"\nargs="ro crashkernel=auto audit=1"', 0, ""),
            "coredump.conf": ("# /etc/systemd/coredump.conf\n[Coredump]\n#Storage=external\n#ProcessSizeMax=2G\n"
                              "# /etc/systemd/coredump.conf.d/60-cis.conf\n[Coredump]\nStorage=none", 0, ""),
            "selinux.unconfined": ("", 1, ""),
            "gdm.grupos_dnf": ("Installed Environment Groups:\n   Server\nInstalled Groups:", 0, ""),
        })
    return s


_OS_OL8 = ('NAME="Oracle Linux Server"\nVERSION="8.10"\nID="ol"\nID_LIKE="fedora"\nVERSION_ID="8.10"\n'
           'PRETTY_NAME="Oracle Linux Server 8.10"')


def variante_ol8(datos: dict) -> dict:
    """Escenario sintético Oracle Linux 8 (RHEL_ORACLE_8) derivado del de OL 9: os-release 8.10 y paquetes, kernel,
    repositorios y GRUB de la rama el8. Solo para pruebas/demostración."""
    def _t(v: str) -> str:
        return (v.replace("el9uek", "el8uek").replace("el9_4", "el8_10").replace(".el9", ".el8").replace("ol9", "ol8")
                .replace("OL9", "OL8").replace("Oracle Linux 9", "Oracle Linux 8").replace("demo-ol9-01", "demo-ol8-01"))
    res = {k: (_t(o), rc, _t(e)) for k, (o, rc, e) in datos.items()}
    res["sys.os_release"] = (_OS_OL8, 0, "")
    return res


def recolector_sintetico(benchmark: str, matriz: list, matriz_p1: dict = None) -> str:
    """Salida completa del recolector del benchmark (comandos profundos omitidos, como en la configuración por defecto).

    matriz_p1: incluye la Parte 1 (comandos p1.* y paquetes adicionales). Sin ella la salida es idéntica a la de la Parte 2."""
    from .seleccion import FAMILIA, requerimientos
    familia = FAMILIA[benchmark]
    req = requerimientos(matriz, benchmark, matriz_p1)
    out = [f"@@CIS_RECOLECTOR|formato=1|familia={familia}|benchmark={benchmark}|profunda=0|matriz=demo"]
    datos = salidas(familia)
    if matriz_p1:
        from .simulacion_p1 import GRUPOS_P1, PKG_P1, salidas_p1
        datos.update(salidas_p1(familia))
        base = {}
        for l in datos["pkg.rpm"][0].splitlines():
            if l.startswith("package "):
                base[l.split()[1]] = l
            elif "|" not in l:
                base[l.rsplit("-", 2)[0]] = l
        inst = {**base, **PKG_P1[familia]}
        extra = [l for l in datos["pkg.rpm"][0].splitlines() if "|" in l]
        datos["pkg.rpm"] = ("\n".join([inst.get(p, f"package {p} is not installed") for p in req["paquetes"]] + extra), 0, "")
        datos["usr.group"] = (datos["usr.group"][0] + "\n" + GRUPOS_P1, 0, "")
    if benchmark in ("ORACLE8", "RHEL8"):
        datos = variante_ol8(datos)
    for cid in req["comandos"]:
        c = COMANDOS[cid]
        if c.profunda:
            out.append(_bloque(cid, "", omitido="auditoria_filesystem_profunda=false"))
        elif cid in datos:
            o, rc, e = datos[cid]
            out.append(_bloque(cid, o, rc, e))
        else:
            out.append(_bloque(cid, "", 1, ""))
    out.append("@@CIS_FIN")
    return "\n".join(out) + "\n"
