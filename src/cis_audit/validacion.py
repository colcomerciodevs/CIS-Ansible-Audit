"""Predicción del dominio y del tipo de validación por control CIS.

Tipos de validación:
- AUTOMATIZABLE: comprobación read-only directa y de bajo impacto.
- AUTOMATIZABLE_CON_PRECAUCION: read-only pero con costo (recorridos de
  filesystem), elevación para leer archivos sensibles o salida que debe
  redactarse antes de persistir.
- SEMIAUTOMATICA: se recopila evidencia técnica, pero la conclusión depende de
  una política del sitio o de una lista aprobada (CIS "site policy").
- REQUIERE_VALIDACION_MANUAL: la conclusión no puede derivarse solo del host.

La predicción se basa en el título oficial del control mapeado y en su tipo
CIS (Automated/Manual). La evaluación definitiva la decide el evaluador de cada control.
"""

import re
from typing import Dict, Optional

# (patrón sobre título CIS en minúsculas, dominio, tipo, nota)
REGLAS = [
    (r"package manager repositories", "Repositorios / gestor de paquetes", "REQUIERE_VALIDACION_MANUAL",
     "CIS exige revisar que los repositorios sean los aprobados por la política del sitio; requiere lista oficial de repositorios."),
    (r"gpg keys", "Repositorios / gestor de paquetes", "SEMIAUTOMATICA",
     "Se listan claves gpg-pubkey y gpgkey= de cada repo; la validez de cada clave contra el proveedor requiere revisión."),
    (r"gpgcheck", "Repositorios / gestor de paquetes", "AUTOMATIZABLE", None),
    (r"updates, patches", "Parches y actualizaciones", "SEMIAUTOMATICA",
     "Consultar actualizaciones pendientes usa metadatos de repositorios (posible tráfico de red). Usar caché (-C) o ventana acordada; la conclusión depende de la política de parches."),
    (r"aide is installed", "AIDE / integridad", "AUTOMATIZABLE", None),
    (r"filesystem integrity is regularly checked", "AIDE / integridad", "AUTOMATIZABLE_CON_PRECAUCION",
     "Solo leer cron/systemd timers. Nunca ejecutar 'aide --check' ni '--init' (alto impacto)."),
    (r"bootloader password", "Boot / GRUB", "AUTOMATIZABLE_CON_PRECAUCION",
     "Requiere leer user.cfg/grub.cfg con sudo; el hash de la contraseña debe redactarse (<REDACTADO>), solo registrar su existencia y formato."),
    (r"bootloader config|apparmor is enabled in the bootloader|selinux is not disabled in bootloader", "Boot / GRUB", "AUTOMATIZABLE", None),
    (r"randomize_va_space|address space layout", "Kernel / sysctl", "AUTOMATIZABLE",
     "Validar runtime (sysctl) y persistencia (sysctl.d, precedencia y duplicados)."),
    (r"coredump|core dump", "Kernel / core dumps", "AUTOMATIZABLE",
     "Validar configuración efectiva de systemd-coredump / limits / sysctl según el Benchmark."),
    (r"unconfined services", "SELinux", "AUTOMATIZABLE",
     "CIS lo clasifica Manual, pero el Audit es 'ps -eZ | grep unconfined_service_t' (read-only)."),
    (r"selinux|mcstrans|setroubleshoot", "SELinux", "AUTOMATIZABLE", None),
    (r"apparmor", "AppArmor", "AUTOMATIZABLE", None),
    (r"gnome display manager", "Paquetes / entorno gráfico", "AUTOMATIZABLE",
     "Preferir 'rpm -q gdm' a 'dnf grouplist' (este último puede refrescar metadatos de repositorios)."),
    (r"gdm|xdmcp", "Entorno gráfico (GDM)", "AUTOMATIZABLE",
     "Aplicabilidad condicionada a que GDM esté instalado."),
    (r"crypto policy", "Política criptográfica", "AUTOMATIZABLE", None),
    (r"firewall|default zone", "Firewall / red", "AUTOMATIZABLE", None),
    (r"auditd packages|auditd is installed|auditd service|prior to auditd|audit_backlog_limit", "auditd", "AUTOMATIZABLE", None),
    (r"audit log storage size", "auditd", "SEMIAUTOMATICA",
     "CIS: 'in compliance with site policy'. El valor de referencia lo define la exclusión de Infraestructura (max_log_file)."),
    (r"audit logs are not automatically deleted|system is disabled when audit logs", "auditd", "AUTOMATIZABLE", None),
    (r"use of privileged commands", "auditd (reglas)", "AUTOMATIZABLE_CON_PRECAUCION",
     "El Audit recorre filesystems locales buscando binarios SUID/SGID; limitar con -xdev y excluir montajes noexec/nosuid según el Benchmark."),
    (r"running and on disk configuration", "auditd (reglas)", "AUTOMATIZABLE",
     "Usar 'augenrules --check' (solo compara). Nunca 'augenrules --load'."),
    (r"collected|recorded|immutable|actions as another user", "auditd (reglas)", "AUTOMATIZABLE",
     "Validar reglas en disco (/etc/audit/rules.d) y cargadas (auditctl -l)."),
    (r"journald log file access", "Logging (journald)", "SEMIAUTOMATICA",
     "CIS Manual: permisos según política del sitio."),
    (r"journald log file rotation", "Logging (journald)", "SEMIAUTOMATICA",
     "CIS Manual: rotación según política del sitio; la referencia la fija la exclusión de Infraestructura."),
    (r"journal-remote authentication|journal-upload authentication", "Logging (journald remoto)", "SEMIAUTOMATICA",
     "Se lee la configuración de certificados; la validez del servidor destino requiere confirmación."),
    (r"journald|journal-remote|journal-upload|only one logging system", "Logging (journald)", "AUTOMATIZABLE", None),
    (r"rsyslog logging is configured|logging is configured", "Logging (rsyslog)", "REQUIERE_VALIDACION_MANUAL",
     "CIS Manual: revisar que las reglas de logging sean las apropiadas para el sitio."),
    (r"send logs to a remote log host", "Logging (rsyslog remoto)", "SEMIAUTOMATICA",
     "Se verifica destino configurado; la autorización del destino requiere confirmación del Área de Seguridad."),
    (r"logrotate", "Logging (logrotate)", "SEMIAUTOMATICA",
     "CIS Manual: configuración según política del sitio."),
    (r"rsyslog", "Logging (rsyslog)", "AUTOMATIZABLE", None),
    (r"access to all logfiles", "Permisos de logs", "AUTOMATIZABLE_CON_PRECAUCION",
     "Recorre /var/log; acotar profundidad y registrar solo desviaciones."),
    (r"world writable|without an owner", "Permisos de archivos", "AUTOMATIZABLE_CON_PRECAUCION",
     "Recorrido de filesystems locales: usar -xdev, excluir /proc /sys y montajes remotos; ejecutar en ventana acordada."),
    (r"suid and sgid", "Permisos de archivos", "REQUIERE_VALIDACION_MANUAL",
     "Recorrido costoso de filesystems y la conclusión requiere una lista aprobada de binarios SUID/SGID."),
    (r"access to /etc/|permissions on /etc/", "Permisos de archivos", "AUTOMATIZABLE", None),
    (r"shadow password fields|use shadowed", "Usuarios / contraseñas", "AUTOMATIZABLE_CON_PRECAUCION",
     "Requiere leer /etc/shadow con sudo; registrar solo nombres de usuario afectados, nunca hashes."),
    (r"dot files", "Usuarios / home", "AUTOMATIZABLE_CON_PRECAUCION",
     "Recorre directorios home; no copiar contenido de .netrc/.rhosts (posibles credenciales)."),
    (r"home directories", "Usuarios / home", "AUTOMATIZABLE", None),
    (r"uid 0|root path|duplicate|groups in /etc/passwd|root account", "Usuarios / grupos", "AUTOMATIZABLE", None),
]


def clasificar(titulo_cis: Optional[str], assessment: Optional[str]) -> Dict:
    if not titulo_cis:
        return {"dominio_validacion": None, "tipo_validacion": None, "nota_validacion": None}
    t = titulo_cis.lower()
    for patron, dominio, tipo, nota in REGLAS:
        if re.search(patron, t):
            return {"dominio_validacion": dominio, "tipo_validacion": tipo, "nota_validacion": nota}
    tipo = "SEMIAUTOMATICA" if assessment == "Manual" else "AUTOMATIZABLE"
    return {
        "dominio_validacion": "Sin clasificar",
        "tipo_validacion": tipo,
        "nota_validacion": "Sin regla específica; clasificado por tipo CIS.",
    }
