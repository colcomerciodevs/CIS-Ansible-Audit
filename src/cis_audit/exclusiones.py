"""Clasificación de `EXCLUSIONES (Infraestructura Linux)`.

Tipos:
- NINGUNA: sin exclusión ni condición adicional.
- NINGUNA_CON_NOTA_INFORMATIVA: "Ninguna" + nota que no impone condición verificable.
- TECNICA_VERIFICABLE: condición comprobable desde Linux (valores, particiones,
  alcance de usuarios, aplicabilidad condicionada a otro control).
- ORGANIZACIONAL: depende de procesos/documentos fuera del host.
- MIXTA: parte técnica verificable + parte organizacional.
- PENDIENTE_REVISION: texto no reconocido por las reglas; requiere análisis manual.

La clasificación es determinista por reglas explícitas; cada regla deja
constancia de qué se verificará y qué evidencia externa haría falta.
"""

import re
from typing import Dict, List, Optional

from .normalizacion import compactar, sin_acentos, texto

_RE_KV = re.compile(r"^\s*\(?\s*([A-Za-z_]+)\s*=\s*([^\s)]+)\s*\)?\s*$")


def _parametros(valor: str) -> Dict[str, str]:
    params = {}
    for linea in texto(valor).splitlines():
        m = _RE_KV.match(linea)
        if m:
            params[m.group(1)] = m.group(2)
    return params


# (id_regla, patrón sobre texto sin acentos en minúsculas, tipo, verificación, evidencia organizacional)
REGLAS = [
    (
        "AUDITD_PARTICION_15GB",
        r"/var/log/audit\)?.*15 ?gb|15 ?gb.*/var/log/audit",
        "TECNICA_VERIFICABLE",
        "Verificar que /var/log/audit sea un punto de montaje independiente (findmnt) con "
        "tamaño >= 15 GB (df) y que exista rotación de logs de auditd (max_log_file_action).",
        None,
    ),
    (
        "AUDITD_MAX_LOG_FILE",
        r"max_log_file\s*=\s*\d+",
        "TECNICA_VERIFICABLE",
        "Leer /etc/audit/auditd.conf y comparar max_log_file y num_logs con los valores "
        "recomendados por Infraestructura.",
        None,
    ),
    (
        "AUDITD_ROTATE",
        r"max_log_file_action\s*=\s*rotate",
        "TECNICA_VERIFICABLE",
        "Leer max_log_file_action en /etc/audit/auditd.conf; la exclusión se cumple con 'rotate'.",
        None,
    ),
    (
        "AUDITD_DISK_ACTION_SYSLOG",
        r"disk_full_action\s*=\s*syslog",
        "TECNICA_VERIFICABLE",
        "Leer disk_full_action y disk_error_action en /etc/audit/auditd.conf; la exclusión se "
        "cumple con 'syslog'.",
        None,
    ),
    (
        "JOURNALD_ROTACION",
        r"systemmaxuse\s*=",
        "TECNICA_VERIFICABLE",
        "Obtener la configuración efectiva de journald (journald.conf + journald.conf.d/*.conf, "
        "orden de precedencia) y comparar SystemMaxUse, SystemKeepFree, RuntimeMaxUse, "
        "RuntimeKeepFree y MaxFileSec con lo recomendado.",
        None,
    ),
    (
        "GDM_NO_APLICA_SI_REMOVIDO",
        r"n/a, ya que el anterior control esta solicitando remover gdm",
        "TECNICA_VERIFICABLE",
        "Verificar que GDM no esté instalado (rpm -q gdm). Si no está instalado, el control GDM "
        "no aplica al host; si está instalado, la exclusión no se sostiene.",
        None,
    ),
    (
        "USUARIOS_CON_LOGIN",
        r"unicamente para usuarios que puedan hacer login",
        "TECNICA_VERIFICABLE",
        "Restringir la evaluación a usuarios con shell /bin/bash o /bin/sh según el Excel. "
        "Nota: CIS define usuario interactivo por shells válidos de /etc/shells; registrar "
        "ambos conjuntos y la diferencia.",
        None,
    ),
    (
        "LOGS_ARCHIVOS_AUTORIZADOS",
        r"tipo:\s*archivos_log_autorizados",
        "TECNICA_VERIFICABLE",
        "Comparar cada archivo de /var/log que incumple el script CIS con los patrones ARCHIVOS de la exclusión y "
        "verificar en el host la CONDICION (propietario y modo máximo). Solo cubre archivo por archivo.",
        None,
    ),
    (
        "BOOTLOADER_PASSWORD_BOVEDA",
        r"boveda de contrasenas",
        "ORGANIZACIONAL",
        None,
        "Evidencia de custodia de la contraseña de GRUB en la bóveda corporativa (registro en "
        "la bóveda por host o grupo de hosts, responsable y fecha). No verificable desde el host.",
    ),
    (
        "PARCHADO_PROCESO",
        r"proceso formal de parchado",
        "ORGANIZACIONAL",
        None,
        "Procedimiento formal de gestión de parches/vulnerabilidades aprobado, calendario y "
        "último ciclo aplicado al host (registro de cambio). El estado de paquetes pendientes "
        "sí se obtiene del host para el Resultado CIS.",
    ),
    (
        "SIEM_INTEGRACION",
        r"integracion con algun siem|syslog central",
        "MIXTA",
        "Verificar en el host la configuración de envío remoto (destino, protocolo, puerto) "
        "y que el servicio esté activo.",
        "Confirmación del Área de Seguridad de que el destino configurado es el SIEM/Syslog "
        "central autorizado y que recibe eventos del host.",
    ),
    (
        "AIDE_HORARIO_CONTROLADO",
        r"horario controlado",
        "MIXTA",
        "Leer la programación de AIDE (cron, systemd timer aidecheck.timer) y registrar la "
        "ventana horaria configurada. No ejecutar AIDE.",
        "Definición formal de la ventana horaria aprobada ('horario controlado') para comparar "
        "con la programación encontrada.",
    ),
]

_NOTAS_INFORMATIVAS = [
    r"importante para (deteccion|prevencion|registro|centralizacion|control|evitar|no saturar)",
]


def archivos_log_autorizados(valor) -> Optional[Dict]:
    """Exclusión P2 TIPO: ARCHIVOS_LOG_AUTORIZADOS -> {patrones, texto_patrones, propietario, modo_max} o None si el texto no
    define con exactitud ARCHIVOS y CONDICION (fail-closed: sin definición exacta, la exclusión no cubre nada).
    Sintaxis de ARCHIVOS: rutas absolutas separadas por coma; '<usuario>' = un nombre sin '/', '[.N]' = sufijo de
    rotación numérico opcional. CONDICION: 'propietario <usuario>' y 'modo <octal> o más restrictivo'."""
    t = texto(valor)
    if not re.search(r"(?im)^\s*TIPO:\s*ARCHIVOS_LOG_AUTORIZADOS\s*$", t):
        return None
    a = re.search(r"(?im)^\s*ARCHIVOS:\s*(.+)$", t)
    c = re.search(r"(?im)^\s*CONDICION:\s*(.+)$", t)
    if not a or not c:
        return None
    prop = re.search(r"propietario\s+([a-z_][a-z0-9_-]*)", c.group(1), re.I)
    modo = re.search(r"modo\s+(0?[0-7]{3,4})\s+o\s+m[aá]s\s+restrictivo", c.group(1), re.I)
    rutas = [x.strip() for x in a.group(1).split(",") if x.strip()]
    if not prop or not modo or not rutas or not all(r.startswith("/var/log/") for r in rutas):
        return None
    pats = []
    for r in rutas:
        p = re.escape(r).replace(re.escape("<usuario>"), r"[^/]+?").replace(re.escape("[.N]"), r"(\.[0-9]+)?")
        pats.append("^" + p + "$")
    return {"patrones": pats, "texto_patrones": rutas, "propietario": prop.group(1), "modo_max": int(modo.group(1), 8),
            "modo_max_texto": modo.group(1).zfill(4)}


def _es_ninguna(t: str) -> bool:
    return bool(re.match(r"^\s*ningun[ao]\b", t))


def clasificar(valor) -> Dict:
    original = texto(valor)
    t = sin_acentos(compactar(valor)).lower()
    reglas: List[str] = []
    tipos: List[str] = []
    verificacion: List[str] = []
    evidencia_org: List[str] = []

    for rid, patron, tipo, verif, org in REGLAS:
        if re.search(patron, t):
            reglas.append(rid)
            tipos.append(tipo)
            if verif:
                verificacion.append(verif)
            if org:
                evidencia_org.append(org)

    nota_informativa = any(re.search(p, t) for p in _NOTAS_INFORMATIVAS)
    cis_parcial = "cumplimiento del control cis parcial" in t

    if not t:
        tipo = "NINGUNA"
    elif tipos:
        if "MIXTA" in tipos or ("ORGANIZACIONAL" in tipos and "TECNICA_VERIFICABLE" in tipos):
            tipo = "MIXTA"
        elif "ORGANIZACIONAL" in tipos:
            tipo = "ORGANIZACIONAL"
        else:
            tipo = "TECNICA_VERIFICABLE"
    elif _es_ninguna(t):
        tipo = "NINGUNA_CON_NOTA_INFORMATIVA" if len(t) > len("ninguna") + 2 else "NINGUNA"
    else:
        tipo = "PENDIENTE_REVISION"

    return {
        "exclusion_infraestructura": original.strip() or None,
        "tipo_exclusion": tipo,
        "reglas_exclusion": reglas,
        "parametros_exclusion": _parametros(original),
        "exclusion_declara_cis_parcial": cis_parcial,
        "nota_informativa": nota_informativa,
        "verificacion_exclusion_propuesta": " | ".join(verificacion) or None,
        "evidencia_organizacional_requerida": " | ".join(evidencia_org) or None,
    }
