"""Validaciones estáticas de seguridad de los comandos y del mecanismo de ejecución.

Las usan las pruebas unitarias y `auditar.py plan` antes de cualquier ejecución.
"""

import re
from typing import List

PROHIBIDOS = {
    "modifica archivos": [r"\brm\s", r"\bmv\s", r"(^|[;|&]\s*)cp\s", r"sed\s+-i", r">>", r"\bchmod\b", r"\bchown\b",
                          r"\bsetfacl\s+-", r"\btee\b", r"\btouch\b", r"\bmkdir\b", r"\bln\s+-", r"\btruncate\s+-"],
    # (?<![/\w.-]): una ruta como /etc/default/useradd no es una invocación del comando.
    "modifica usuarios": [r"(?<![/\w.-])(useradd|userdel|usermod\s+-|groupadd|groupmod|chpasswd|chage)\b", r"(^|[;&|]\s*|sudo -n\s+)passwd\s"],
    "modifica kernel/sysctl": [r"sysctl\s+-w", r"sysctl\s+-p", r"(^|[;&|]\s*|sudo -n\s+)(modprobe|insmod|rmmod)\s", r"\bsetenforce\b", r"\bsetsebool\b"],
    "instala o remueve paquetes": [r"\b(dnf|yum)\s+(-\S+\s+)*(install|remove|erase|upgrade|update|reinstall|downgrade|autoremove)\b",
                                   r"\bzypper\s+(\S+\s+)*(in|install|rm|remove|up|update|dup|patch)\b", r"\brpm\s+-(i|U|e|F)"],
    "refresca repositorios": [r"\b(dnf|yum)\s+(-\S+\s+)*(makecache|clean)\b", r"\bzypper\s+(\S+\s+)*(ref|refresh|clean)\b"],
    "reinicia o cambia servicios": [r"systemctl\s+(enable|disable|start|stop|restart|reload|try-restart|mask|unmask|daemon-reload|edit|set-|isolate|kill)",
                                    r"\bservice\s+\S+\s+(start|stop|restart|reload)"],
    "reinicia servidores": [r"\b(reboot|shutdown|poweroff|halt)\b", r"\binit\s+[0-6]\b", r"systemctl\s+(reboot|poweroff|halt|kexec)"],
    "modifica auditoría/arranque/crypto": [r"\baide\s+--(init|check|update)", r"\baugenrules\b", r"\bauditctl\s+-(e|R|D|a|w|b|f)\b",
                                           r"grub2-mkconfig", r"grub2-setpassword", r"grubby\s+--(update|args|remove|add|set)",
                                           r"update-crypto-policies\s+--set", r"authselect\s+(select|apply)", r"pam-config\s+-[ad]",
                                           r"journalctl\s+--(vacuum|rotate|flush)", r"logrotate\s+-f"],
    "modifica firewall": [r"\bfirewall-cmd\b", r"\biptables\b", r"\bnft\s", r"\bkill(all)?\b"],
}


def problemas_comando(cmd: str) -> List[str]:
    res = []
    for cat, pats in PROHIBIDOS.items():
        for p in pats:
            if re.search(p, cmd):
                res.append(f"{cat}: {p}")
    limpio = re.sub(r"[12]?>/dev/null|[12]>&[12]|<[A-Z]+>", "", cmd)
    if re.search(r"(?<![-=<!])>(?![=&])", limpio):
        res.append("redirección de salida a archivo")
    for m in re.finditer(r"\bsudo\b", cmd):
        if cmd[max(0, m.start() - 9):m.end() + 3] == "/usr/bin/sudo -V":
            continue  # solo consulta de valores por defecto (priv.sudo_politica, recolector 1.2.0)
        if cmd[max(0, m.start() - 9):m.end() + 2] == "/usr/bin/sudo ]":
            continue
        if not (cmd[m.end():m.end() + 3] == " -n" or cmd[max(0, m.start() - 3):m.start()] == 'S="'):
            res.append("sudo sin -n (podría solicitar contraseña)")
    for m in re.finditer(r"(?<![/\w.-])(dnf|yum)\s", cmd):
        if not cmd[m.end():].startswith("-C"):
            res.append("dnf/yum sin -C (podría refrescar metadatos)")
    for m in re.finditer(r"(?<![/\w.-])zypper\s", cmd):
        if "--no-refresh" not in cmd[m.end():m.end() + 40]:
            res.append("zypper sin --no-refresh")
    return res
